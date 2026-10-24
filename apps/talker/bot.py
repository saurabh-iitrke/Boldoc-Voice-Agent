"""The talker: one Pipecat pipeline per call, on a Plivo bidirectional audio stream.

caller audio (μ-law 8 kHz) -> PlivoFrameSerializer -> 16 kHz PCM -> Silero VAD + Smart Turn v3 (when the caller
has finished) -> Sarvam realtime STT (codemix captions) -> Sarvam LLM -> Sarvam TTS (8 kHz) -> back to Plivo, a
checkpoint behind each reply (Plivo answers playedStream once the caller has heard it all).

Documents: the LLM's tools send commands to the control plane; every LLM request carries a fresh [STATE] note
(the only document facts the talker may state); finished work is announced when the caller is quiet.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from fastapi import WebSocket
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState
from pipecat.frames.frames import EndFrame, LLMRunFrame, TTSSpeakFrame, TTSUpdateSettingsFrame
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.serializers.plivo import PlivoFrameSerializer
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.sarvam.llm import SarvamLLMService
from pipecat.services.sarvam.stt import SarvamRealtimeSTTService
from pipecat.services.sarvam.tts import SarvamTTSService, SarvamTTSSettings
from pipecat.transcriptions.language import Language
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.turns.empty_user_turn import EmptyUserTurnConfig
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from protocol.llm import LLMEndpoint, overflow_endpoint
from protocol.llm import endpoint as llm_endpoint

from . import ack, agent_config
from .live import LiveFeed
from .logic.claims import speakable
from .logic.language import NAMES, LanguageTracker
from .processors import (
    CallerScript,
    CallerTurnStart,
    CallerTurnStop,
    ClaimGuard,
    FailoverOnError,
    LanguageRouter,
    LateAnswer,
    PlaybackGate,
    PlayoutMarks,
    PlivoPlayoutSerializer,
    SignalObserver,
)
from .prompts import SYSTEM_PROMPT, callback_opener, welcome
from .session import CallSession, ControlClient
from .settings import SETTINGS
from .stt import DOMAIN_PROMPT, PrerollSarvamSTT
from .voices import tts_language

TOOLS = ToolsSchema(standard_tools=[
    FunctionSchema(
        name="create_document",
        description="Start writing a NEW document once the caller has confirmed what it is, who it is for, the key "
                    "facts and the format. Returns at once; the document is written in the background.",
        properties={
            "summary": {"type": "string", "description": "One line: what to write, for whom, key facts"},
            "formats": {"type": "array", "items": {"type": "string",
                                                   "enum": ["pdf", "docx", "xlsx", "pptx", "csv", "md", "txt"]}},
            "language": {"type": "string", "enum": ["en-IN", "hi-IN", "mr-IN", "bn-IN", "gu-IN", "pa-IN", "ta-IN",
                                                    "te-IN", "kn-IN", "ml-IN", "od-IN"],
                         "description": "Language the document is written in"},
        },
        required=["summary", "formats", "language"]),
    FunctionSchema(
        name="edit_document",
        description="Change the caller's document: the current one by default (also while it is still being "
                    "written), or one from their earlier calls when [STATE] lists it and they ask for it. Also to "
                    "have the whole document in another language ('English mein likho', 'make it Hindi'): set "
                    "language.",
        properties={"instruction": {"type": "string", "description": "The change, with the exact new facts"},
                    "document": {"type": "string", "description": "Title of an earlier document to change, only "
                                                                  "when the caller means one listed in [STATE]"},
                    "language": {"type": "string", "enum": ["en-IN", "hi-IN", "mr-IN", "bn-IN", "gu-IN", "pa-IN",
                                                            "ta-IN", "te-IN", "kn-IN", "ml-IN", "od-IN"],
                                 "description": "Only when the caller wants the document in another language: the "
                                                "language to rewrite it in"}},
        required=["instruction"]),
    FunctionSchema(
        name="set_language",
        description="Switch the language you talk in, when the caller asks you to talk in another language or asks "
                    "whether you speak it, in any words ('Hindi please', 'can we continue in Tamil?', 'Hindi aati hai "
                    "aapko?', 'English mein baat kijiye', 'मुझे हिंदी में समझाइए'). Not for the language a document is "
                    "written in (that is create_document's language), and not when they only mention a language. Then "
                    "answer them in that language.",
        properties={"language": {"type": "string", "enum": ["en-IN", "hi-IN", "mr-IN", "bn-IN", "gu-IN", "ta-IN",
                                                            "te-IN", "kn-IN", "ml-IN", "od-IN"],
                                 "description": "The language to talk in (hi-IN: Hindi or Hinglish)"}},
        required=["language"]),
    FunctionSchema(
        name="end_call",
        description="End the call, only when the caller is done (they say 'bas', 'nahi, aur kuch nahi', 'thank you', "
                    "'that's all') and nothing is being written. Then say a short, warm goodbye; the call ends "
                    "after it.",
        properties={}, required=[]),
])


class StateNote:
    """Appends the live [STATE] note as the last message of every LLM request (never stored in the conversation
    history, so it can't go stale there). Mixed into whichever provider's service the talker uses."""

    BOLDOC_LLM = True  # the observer recognises the talker's LLM by this, whatever the provider

    def __init__(self, *, state_provider, before_reply=None, **kwargs):
        super().__init__(**kwargs)
        self._state_provider = state_provider
        self._before_reply = before_reply

    async def get_chat_completions(self, context):
        if self._before_reply is not None:  # e.g. the "is the caller done?" decision, so this reply can be the goodbye
            await self._before_reply()
        return await super().get_chat_completions(context)

    def build_chat_completion_params(self, params_from_context):
        params = super().build_chat_completion_params(params_from_context)
        messages = merge_user_runs(list(params["messages"]))
        state = self._state_provider()
        if state:
            messages.append({"role": "system", "content": state})
        params["messages"] = messages
        return params


class BolDocLLM(StateNote, SarvamLLMService):
    """Sarvam (the default talker)."""


class BolDocOpenAILLM(StateNote, OpenAILLMService):
    """Any OpenAI-compatible provider configured in protocol/llm.py (TALKER_LLM=<provider>:<model>)."""


def _tts_lang(family: str) -> Language:
    """Pipecat's Language for a voice family ("hinglish" speaks hi-IN); English if pipecat doesn't know the code."""
    try:
        return Language(tts_language(family))
    except ValueError:
        return Language.EN_IN


def make_llm(ep: LLMEndpoint, state_provider, before_reply=None):
    """The talker's LLM service for an endpoint. Sarvam: /v1 honours reasoning_effort=null; Pipecat's /v2 default
    ignored it and spent the whole budget on hidden reasoning."""
    common = dict(model=ep.model, system_instruction=SYSTEM_PROMPT, temperature=0.5, max_tokens=300,
                  extra={"extra_body": ep.extra} if ep.extra else {})
    if ep.provider == "sarvam":
        return BolDocLLM(state_provider=state_provider, before_reply=before_reply, api_key=ep.api_key,
                         base_url=ep.base_url,
                         settings=SarvamLLMService.Settings(**common))
    header_auth = ep.auth.startswith("header:")
    return BolDocOpenAILLM(state_provider=state_provider, before_reply=before_reply,
                           api_key="unused" if header_auth else ep.api_key,
                           base_url=ep.base_url, default_headers=ep.headers() if header_auth else None,
                           settings=OpenAILLMService.Settings(**common))


def merge_user_runs(messages: list) -> list:
    """Caller turns with no reply in between (a held reply was dropped because they kept talking) are one turn."""
    out: list = []
    for m in messages:
        prev = out[-1] if out else None
        if (prev is not None and m.get("role") == "user" and prev.get("role") == "user"
                and isinstance(m.get("content"), str) and isinstance(prev.get("content"), str)):
            out[-1] = {**prev, "content": f"{prev['content'].rstrip()} {m['content'].lstrip()}"}
        else:
            out.append(m)
    return out


class OnsetVAD(SileroVADAnalyzer):
    """Silero VAD that also records the last moment it heard voice-like audio, including the unconfirmed
    'starting' state, so the playback gate notices a caller resuming within ~30 ms instead of after start_secs."""

    last_voice_at: float = 0.0
    # The most voice-like and the loudest the line got since the last look (BolDoc starting to speak): whether a caller
    # who says they talked over her was heard below the thresholds or not at all.
    peak_conf: float = 0.0
    peak_vol: float = 0.0

    def voice_confidence(self, buffer: bytes) -> float:
        conf = super().voice_confidence(buffer)
        # Silero returns a one-element numpy array: the peaks are kept as plain floats, which the trace can round
        self.peak_conf = max(self.peak_conf, float(np.max(conf)))
        self.peak_vol = max(self.peak_vol, float(self._prev_volume))
        return conf

    def take_peak(self) -> Tuple[float, float]:
        peak = (self.peak_conf, self.peak_vol)
        self.peak_conf = self.peak_vol = 0.0
        return peak

    async def analyze_audio(self, buffer: bytes) -> VADState:
        state = await super().analyze_audio(buffer)
        if state in (VADState.STARTING, VADState.SPEAKING):
            self.last_voice_at = time.monotonic()
        return state


@dataclass
class CallInfo:
    call_id: str
    stream_id: str
    hang_up_on_end: bool = True
    test: bool = False                  # synthetic test call: transcripts may be logged
    first_call: bool = False            # this number's first call: the greeting says how long transcripts are kept
    late_admit: Optional[dict] = None   # answer parameters when control was unreachable at answer time
    llm: str = "primary"                # admission's choice: primary (Sarvam) or overflow (busy times)
    intake: bool = True                 # the writer's checklist; scripted test calls may turn it off to compare
    lang: Optional[str] = None          # the language picked on the website (browser call, Call me); else English
    remembered_lang: Optional[str] = None  # the language the caller's last call ended in (any way they called)


async def run_bot(websocket: WebSocket, call: CallInfo) -> None:
    log = logger.bind(call=call.call_id[-8:])
    session = CallSession(call.call_id, None, "pstn", ControlClient(SETTINGS.control_url, SETTINGS.internal_key))
    serializer = PlivoPlayoutSerializer(  # Plivo's playedStream / clearedAudio go to session.playout
        stream_id=call.stream_id,
        call_id=call.call_id,
        auth_id=SETTINGS.plivo_auth_id or None,
        auth_token=SETTINGS.plivo_auth_token or None,
        params=PlivoFrameSerializer.InputParams(plivo_sample_rate=8000, auto_hang_up=call.hang_up_on_end),
        playout=session.playout,
    )
    transport = FastAPIWebsocketTransport(websocket, FastAPIWebsocketParams(
        audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False, serializer=serializer,
        session_timeout=SETTINGS.max_call_s,
    ))

    stt = PrerollSarvamSTT(
        api_key=SETTINGS.sarvam_api_key,
        endpointing="manual",  # local VAD + Smart Turn own the turn boundaries (faster, tunable for dictation)
        # language None -> "auto": every Indian language; codemix keeps English words in Latin script
        settings=SarvamRealtimeSTTService.Settings(model=SETTINGS.stt_model, language=None, stream_type="fast",
                                                   mode="codemix", prompt=DOMAIN_PROMPT),
    )
    session.test, session.late_admit = call.test, call.late_admit
    session.log_content = call.test
    session.trace.content = call.test
    backup = overflow_endpoint()                          # OVERFLOW_TALKER_LLM: the fallback model
    overflow = backup if call.llm == "overflow" else None
    judge = llm_endpoint("judge")  # the one-word checks (JUDGE_LLM, default the talker's)
    backup_judge = overflow_endpoint("judge")
    # Overflow calls exist because the primary LLM is at its limit: their checks run on the fallback model too
    # (Qwen3-235B on Bedrock: 58/58 on the judge bench, ~0.25 s), never adding to the primary's load.
    session.api_key = backup_judge if overflow else (judge if judge.api_key else SETTINGS.sarvam_api_key)
    session.live = LiveFeed(SETTINGS.redis_url, call.call_id)  # website call view (captions, agent state)
    presence = asyncio.create_task(session.live.presence())    # this call counts against the capacity cap
    talker_llm = overflow or llm_endpoint("talker", SETTINGS.llm_model)  # TALKER_LLM / OVERFLOW_TALKER_LLM
    llm = make_llm(talker_llm, session.state_text, session.before_reply)
    if overflow is None and backup is not None:
        # Mid-call failover: a turn the primary fails on (rate limit, timeout, outage) is answered at once by the
        # fallback, which keeps the rest of the call (processors.FailoverOnError).
        llm = LLMSwitcher(llms=[llm, make_llm(backup, session.state_text, session.before_reply)],
                          strategy_type=FailoverOnError)

        @llm.strategy.event_handler("on_service_switched")
        async def _failed_over(_strategy, _service):
            await session.on_llm_failover(backup.label, backup_judge)
    session.trace.add("llm", model=talker_llm.label, role=call.llm)
    session.intake_on = call.intake
    # the language picked on the website for this call, else the one their last call ended in, else English
    cfg = await agent_config.for_call()  # the agent's settings this call runs with (admin console), kept to its end
    session.apply_settings(cfg)
    session.lang = LanguageTracker.starting(call.lang, call.remembered_lang, default=cfg.default_language)
    voices = dict(cfg.voices)
    start_family = session.lang.family if session.lang.family in voices else "en-IN"
    tts_model, tts_voice = voices[start_family]
    tts = SarvamTTSService(
        api_key=SETTINGS.sarvam_api_key, model=tts_model, voice_id=tts_voice, sample_rate=8000,
        params=SarvamTTSService.InputParams(language=_tts_lang(start_family), pace=0.9),  # 1.0 felt rushed
    )

    ack_tasks: set = set()

    def load_ack(model: str, voice: str, family: str) -> None:
        """BolDoc's "हम्म" in this voice, for a caller who pauses mid-sentence (fetched once per voice, ack.py)."""
        async def fetch() -> None:
            pcm = await ack.clip(SETTINGS.sarvam_api_key, model, voice, tts_language(family))
            if pcm:
                session.ack_pcm = pcm
        task = asyncio.get_running_loop().create_task(fetch())
        ack_tasks.add(task)
        task.add_done_callback(ack_tasks.discard)

    load_ack(tts_model, tts_voice, start_family)

    def voice_update(family: str) -> TTSUpdateSettingsFrame:
        model, voice = voices[family]
        load_ack(model, voice, family)
        return TTSUpdateSettingsFrame(delta=SarvamTTSSettings(model=model, voice=voice, language=tts_language(family)))

    llm.register_function("create_document", session.create_document)
    llm.register_function("edit_document", session.edit_document)
    llm.register_function("end_call", session.end_call_tool)
    llm.register_function("set_language", session.set_language_tool)

    context = LLMContext(tools=TOOLS)  # persona lives in system_instruction (Pipecat 1.13)
    vad = OnsetVAD(params=VADParams(stop_secs=0.2))
    late = LateAnswer()  # an answer said over the end of BolDoc's question: the start and stop strategies share it
    session.voice_at = lambda: vad.last_voice_at
    session.vad_peak = vad.take_peak
    aggregators = LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(
        vad_analyzer=vad,
        # The caller spoke but speech recognition got nothing (phone line): ask once, never answer with dead air.
        empty_user_turn=EmptyUserTurnConfig(
            idle_prompt="The caller just said something on the phone line but it was not recognised. In one short "
                        "sentence in their language, ask them to say it again.",
            max_consecutive_recoveries=1),
        user_turn_strategies=UserTurnStrategies(
            # over BolDoc, the first real word starts the turn ("haan"/"theek hai" never cut her off, and answer her
            # question once she stops); once she's quiet, any word does; nothing cuts off the last goodbye
            start=[CallerTurnStart(hold=session.goodbye_on_its_way, asked=session.asks_caller, late=late)],
            # Smart Turn's "not finished" fallback waits 3 s by default; our turn judge (word rules + classifier)
            # holds replies for real mid-thought pauses (2.0 s), so 1.0 s is enough here.
            stop=[CallerTurnStop(short_answer=session.short_answer_done, short_answer_s=cfg.short_answer_s, late=late,
                                 turn_analyzer=LocalSmartTurnAnalyzerV3(
                params=SmartTurnParams(stop_secs=1.0)))],
        ),
        # The net under the stop strategy, which ends every normal turn within ~1.2 s (a finished sentence at once, a
        # short answer after 0.5 s, Smart Turn's fallback after 1.0 s). At Pipecat's default 5.0 s, the 2 of 715 gate
        # turns it missed ("…के लिए" then more words) waited 5.35 s for a reply.
        user_turn_stop_timeout=2.0,
    ))

    gate = PlaybackGate(session)
    session.play_clip = gate.play_clip  # the wait tone while a long document is written (wait_tone.py)
    pipeline = Pipeline([transport.input(), stt, CallerScript(session), aggregators.user(), llm,
                         ClaimGuard(session),
                         LanguageRouter(session, set(voices), voice_update, initial=start_family), tts,
                         gate, PlayoutMarks(session.playout, call.stream_id), transport.output(),
                         aggregators.assistant()])
    task = PipelineTask(pipeline, params=PipelineParams(
        audio_in_sample_rate=16000, audio_out_sample_rate=8000, enable_metrics=True, enable_usage_metrics=True,
    ), observers=[SignalObserver(session)])

    async def _run_llm() -> None:
        await task.queue_frames([LLMRunFrame()])

    session.run_llm = _run_llm

    @transport.event_handler("on_client_connected")
    async def _greet(_transport, _client):
        if not call.first_call:
            # a returning caller is welcomed by name and offered their last document, which the session brings
            # (it opens in parallel: ~0.3 s; a first call's welcome needs nothing and never waits)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(session.ready.wait(), timeout=0.8)
        family = session.lang.family
        if family not in ("en-IN", "hinglish"):  # picked on the website: the model greets in that language
            session.greet_note = (
                f"Greet the caller now, in {NAMES.get(session.lang.current, session.lang.current)}, in its own "
                "script, in one or two short sentences: you are BolDoc, an AI assistant that writes documents and "
                "sends them to their phone" + ("; the text of the conversation is kept thirty days" if call.first_call
                                               else "") + "; ask what they would like to write.")
            log.info("call connected; greeting in the picked language")
            return await session.run_llm()
        greeting = callback_opener(session.caller_name, session.callback.get("title") or "document",
                                   session.callback.get("n") or 1, english=family == "en-IN") if session.callback \
            else welcome(call.first_call, session.caller_name, session.last_document_title(), english=family == "en-IN")
        log.info("call connected; greeting")
        context.add_message({"role": "assistant", "content": greeting})
        session.remember_said(greeting)  # its question and introduction are never said again
        session.last_bot_line = greeting  # what the caller's first words answer
        # the name as the greeting's voice says it: calls start in English, in the English voice
        await task.queue_frames([TTSSpeakFrame(speakable(greeting, hindi_voice=family == "hinglish"))])

    ended = []

    async def _end_call() -> None:
        """Hang up after whatever is queued has been spoken (time limit reached, or the call was refused). Once:
        the goodbye, end_call and the backstop timer may all ask for it (5 times on one gate call)."""
        if ended:
            return
        ended.append(1)
        log.info("ending the call")
        await task.queue_frames([EndFrame()])

    session.end_call = _end_call

    async def _speak(text: str, to_context: bool = True) -> None:
        """A fixed line, as is: the goodbye when the model asks to end the call again instead of saying one.
        to_context=False: the session puts the line in the model's context itself, where it belongs."""
        await task.queue_frames([TTSSpeakFrame(speakable(text, hindi_voice=session.lang.family == "hinglish"),
                                               append_to_context=to_context)])

    session.speak = _speak

    @transport.event_handler("on_client_disconnected")
    async def _bye(_transport, _client):
        log.info("caller disconnected")
        await task.cancel()

    opening = asyncio.create_task(session.start())  # in parallel with the greeting: never delays "Namaste"
    try:
        await PipelineRunner(handle_sigint=False).run(task)
    finally:
        await opening
        presence.cancel()
        await session.end()  # work in progress continues; the update SMS goes out from the control plane
        log.info("pipeline finished")
