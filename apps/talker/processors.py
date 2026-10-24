"""Pipeline processors that keep BolDoc polite and honest on a live call.

SignalObserver  feeds the session/arbiter: caller speaking, turn open, BolDoc generating or speaking, words said.
ClaimGuard      (LLM -> TTS) replaces a sentence that claims work is done while the thinker is still on it.
PlaybackGate    (TTS -> output) holds each new reply's audio until the caller has really finished: the LLM and
                TTS start at the first pause (low latency), but playback waits for a silence that grows with how
                long the caller has been talking. If they resume first, the interruption drops the held reply,
                unheard and never added to the conversation.
PlayoutMarks    (-> output) a Plivo checkpoint right behind each reply's last audio; PlivoPlayoutSerializer hands
                Plivo's answers (playedStream, clearedAudio) to the call's Playout (logic/playout.py).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Callable, List, Optional, Tuple

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    FunctionCallInProgressFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    SystemFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData, TTSUsageMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyManual
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.plivo import PlivoFrameSerializer
from pipecat.services.stt_service import STTService
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.min_words_user_turn_start_strategy import MinWordsUserTurnStartStrategy
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import TurnAnalyzerUserTurnStopStrategy

from .logic.claims import (
    chained_question,
    is_completion_claim,
    is_not_speech,
    is_progress_claim,
    is_wrong_channel,
    one_question,
    progress_line,
    signature,
    speakable,
    split_sentences,
)
from .logic.language import HINGLISH, UNSPOKEN, asked_for, detect
from .logic.scripts import TO_DEVANAGARI
from .logic.turns import collapse_repeats, real_words

BARGE_IN_S = 0.3  # caller speech over BolDoc (after VAD's own 0.2 s onset) that interrupts her
# BolDoc's reply this young when the caller's voice starts: they began together, and she yields at once.
GLARE_S = 0.5
ACK_MAX_S = 1.5   # a held reply never waits longer than this for BolDoc's own "हम्म" to finish playing
LATE_ANSWER_S = 0.3  # after BolDoc stops: an answer said over her question is taken now unless the caller goes on


class LateAnswer:
    """The caller's answer said over the end of BolDoc's question ("Yes", as she finished asking "...likh doon?"): too
    short to cut her off, it started no turn, and nothing came after it, so it was never answered. When she stops, it
    becomes the caller's turn (CallerTurnStart), and that turn ends on it (CallerTurnStop): both hold this."""

    def __init__(self):
        self.text: Optional[str] = None


def error_stage(processor: str) -> str:
    """Which part of the call failed, from the processor's class name (for errors by provider and stage)."""
    name = processor.lower()
    for stage in ("stt", "llm", "tts"):
        if stage in name:
            return stage
    return "transport" if ("transport" in name or "serializer" in name) else "other"


class SignalObserver(BaseObserver):
    """Feeds the session (arbiter, gate) and writes a per-turn timeline to the log: metadata always,
    words only for test/debug callers. This is what makes a bad real call debuggable afterwards."""

    def __init__(self, session):
        super().__init__()
        self.s = session
        self._turn_words = 0
        self._turn_text: List[str] = []
        self._llm_t0: Optional[float] = None
        self._llm_first: Optional[float] = None
        self._llm_text: List[str] = []
        self._llm_tools: List[str] = []
        self._empty_check: Optional[asyncio.TimerHandle] = None
        self._between_words, self._between_text = 0, []   # finals heard while no turn was open
        self._bot_t0: Optional[float] = None
        self._await_audio = False

    async def on_push_frame(self, data: FramePushed):
        if isinstance(data.frame, ErrorFrame) and data.first_push:  # errors travel upstream: record them either way
            src = type(data.source).__name__
            self.s.trace.add("error", stage=error_stage(src), processor=src,
                             fatal=bool(getattr(data.frame, "fatal", False)) or None)
            self.s.log.warning(f"pipeline error in {src}: {str(data.frame.error)[:200]}")
            return
        # Pipecat broadcasts turn/speaking/interruption frames both ways as two frames: count the downstream one.
        if not data.first_push or data.direction != FrameDirection.DOWNSTREAM:
            return
        f, s, now = data.frame, self.s, time.monotonic()
        src = type(data.source).__name__
        if isinstance(f, MetricsFrame):  # usage, for the call's cost: LLM tokens and TTS characters
            for m in f.data:
                if isinstance(m, LLMUsageMetricsData):
                    s.trace.add("usage", kind="llm", model=m.model, prompt=m.value.prompt_tokens,
                                completion=m.value.completion_tokens)
                elif isinstance(m, TTSUsageMetricsData):
                    s.trace.add("usage", kind="tts", model=m.model, chars=m.value)
            return
        if isinstance(f, VADUserStartedSpeakingFrame):
            s.on_user_vad(True)
        elif isinstance(f, VADUserStoppedSpeakingFrame):
            s.on_user_vad(False)
        elif isinstance(f, UserStartedSpeakingFrame):
            s.turn_open = True
            # A final that came in while no turn was open belongs to this one: often it is what started it (Saaras
            # finalizes after VAD's stop). Pipecat's aggregator adds it to this turn too.
            self._turn_words, self._turn_text = self._between_words, self._between_text
            self._between_words, self._between_text = 0, []
            s.log.info(f"turn: caller started (BolDoc {'speaking' if s.bot_speaking else 'quiet'})")
            s.trace.add("turn_start", bot_speaking=s.bot_speaking)
            s.live.set_state("listening")
        elif isinstance(f, UserStoppedSpeakingFrame):
            s.turn_open = False
            s.on_turn_end()
            decided = now - s.last_user_speech_end if s.last_user_speech_end else 0.0
            s.log.info(f"turn: caller done, {self._turn_words} words, {s.speech_s:.1f}s speech, end decided "
                       f"{decided:.2f}s after they went quiet"
                       + (f": {' '.join(self._turn_text)}" if s.log_content else ""))
            s.trace.add("turn_end", words=self._turn_words, speech_s=round(s.speech_s, 2),
                        decided_after=round(decided, 3), text=" ".join(self._turn_text))
            if self._turn_text:
                s.live.publish("caller", text=" ".join(self._turn_text))
            s.live.set_state("thinking")
            if self._turn_words == 0:
                s.log.warning("turn: no transcript for this turn (speech recognition returned nothing)")
        elif isinstance(f, TranscriptionFrame) and isinstance(data.source, STTService):
            if s.turn_open:
                self._turn_words += len(f.text.split())
                self._turn_text.append(f.text)
            else:
                self._between_words += len(f.text.split())
                self._between_text.append(f.text)
            s.on_transcript(f.text)
            s.trace.add("stt_final", words=len(f.text.split()), lang=detect(f.text))  # the script, never the words
        elif isinstance(f, LLMFullResponseStartFrame) and getattr(data.source, "BOLDOC_LLM", False):
            s.on_llm_start()
            self._llm_t0, self._llm_first, self._llm_text, self._llm_tools = now, None, [], []
            self._await_audio = True
            s.trace.add("llm_start", announce=s.announcing.type if s.announcing else None)
        elif isinstance(f, LLMTextFrame) and getattr(data.source, "BOLDOC_LLM", False):
            if self._llm_first is None:
                self._llm_first = now
                s.trace.add("llm_first_token")
            self._llm_text.append(f.text)
        elif isinstance(f, FunctionCallInProgressFrame):
            # Pipecat 1.13 runs a reply's function calls after its LLMFullResponseEndFrame, so this usually comes
            # after the reply ended: record it whenever it arrives.
            self._llm_tools.append(f.function_name)
            s.on_tool_call()
            if self._empty_check is not None:
                self._empty_check.cancel()
                self._empty_check = None
            s.trace.add("tool_call", tool=f.function_name)
            s.log.info(f"llm: tool call {f.function_name}")
        elif isinstance(f, LLMFullResponseEndFrame) and getattr(data.source, "BOLDOC_LLM", False):
            s.on_llm_end()
            text = "".join(self._llm_text).strip()
            ttft = f"{(self._llm_first - self._llm_t0) * 1000:.0f}ms" if self._llm_first and self._llm_t0 else "-"
            s.log.info(f"llm: first token {ttft}, {len(text)} chars, tools={self._llm_tools or '-'}"
                       + (f": {text}" if s.log_content and text else ""))
            s.trace.add("llm_end", chars=len(text), tools=self._llm_tools or None, text=text)
            if text:
                s.on_bot_line(text)  # its caption goes to the screen as it plays (session.caption_said)
            if not text and not self._llm_tools:  # unless its function call is just behind it
                self._empty_check = asyncio.get_running_loop().call_later(
                    0.5, s.log.warning, "llm: reply had no speech and no tool call")
        elif isinstance(f, TTSAudioRawFrame) and self._await_audio and src.endswith("TTSService"):
            self._await_audio = False
            s.trace.add("tts_first_audio")
        elif isinstance(f, BotStartedSpeakingFrame):
            if self._bot_t0 is None:
                s.trace.add("bot_start")
                s.live.set_state("speaking")
            self._bot_t0 = now
            s.on_bot_started()
        elif isinstance(f, BotStoppedSpeakingFrame):
            if self._bot_t0 is not None:
                s.log.info(f"bot: spoke {now - self._bot_t0:.1f}s")
                s.trace.add("bot_stop", spoke_s=round(now - self._bot_t0, 2))
                s.live.set_state("writing" if s.busy_writing() else "listening")
                self._bot_t0 = None
            s.on_bot_stopped()
        elif isinstance(f, InterruptionFrame):
            if s.bot_speaking and self._bot_t0 is not None:
                s.log.info(f"bot: cut off after {now - self._bot_t0:.1f}s")
                s.trace.add("bot_cut", after_s=round(now - self._bot_t0, 2))
                self._bot_t0 = None
            s.on_interruption()


class CallerScript(FrameProcessor):
    """(STT -> user aggregator) A word speech-to-text wrote over and over on noise is kept once, and a Hindi speaker's
    words that it wrote in Gurmukhi or Bengali reach the model in Devanagari (logic/scripts.py). There is no text
    echo filter: phones and browsers cancel echo themselves (the browser call asks for it, web/src/lib/mic.ts), and
    the one this replaced dropped only real callers' words, 13 of 13 in 210 calls."""

    def __init__(self, session, **kwargs):
        super().__init__(**kwargs)
        self.s = session

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        script = detect(frame.text) if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)) else None
        convert = TO_DEVANAGARI.get(script)
        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            collapsed = collapse_repeats(frame.text)
            if collapsed != frame.text:
                if isinstance(frame, TranscriptionFrame):
                    self.s.trace.add("stt_loop", words=len(frame.text.split()), kept=len(collapsed.split()))
                frame.text = collapsed
        if convert is not None and (script in UNSPOKEN or self.s.lang.family in ("hinglish", "en-IN")
                                    or asked_for(frame.text) in HINGLISH):
            frame.text = convert(frame.text)
            if isinstance(frame, TranscriptionFrame):
                self.s.trace.add("script_fixed", side="caller")
        await self.push_frame(frame, direction)


# What the model is told when the claim guard dropped its whole reply (it is asked once more, once per caller turn).
UNACTED_NOTE = ("You told the caller you are writing or have changed the document, but you did not call "
                "create_document or edit_document, so nothing is being written. If you have every fact it needs, call "
                "the tool now (edit_document for a change to the document, create_document for a new one). If a fact "
                "is missing, ask for it instead. Never say it's being written or done until a [STATE] note says so.")
CHANGE_IGNORED_NOTE = ("The caller asked to change their document (their last words), and your reply didn't take it "
                       "up. Say the change back in one short sentence and ask whether to make it (or ask the one "
                       "detail it needs); after their yes, call edit_document (with language, if they want the "
                       "document in another language).")
WHILE_WRITING_NOTE = ("The document is still being written and the caller just said something for it. If it adds "
                      "or changes anything, call edit_document now (it goes into the draft); for a name, date or "
                      "amount, say it back first and ask \"...kar doon?\". Otherwise answer what they said. Never "
                      "say it is added or done until a [STATE] note says so.")
MADE_NOTE = ("The caller said yes to the change you said back, and it is being made now (there is no need to call "
             "edit_document for it). Tell them in one short sentence that you are making the change. It is NOT done "
             "yet; you will say when it is. Don't ask about it again.")
REPEAT_NOTE = ("You were about to repeat something you have already said; the caller heard it. Answer what they just "
               "said. If it was unclear, ask differently, in a few words. Never introduce yourself again.")
CHANNEL_NOTE = ("The link never goes by email or WhatsApp: it comes by SMS to the number in [STATE] (if there is "
                "one). Say that, in a few words.")
INSTRUCTION_NOTE = ("You read your instructions out to the caller. They are for you, never to be said: do what they "
                    "ask, and answer the caller in your own words, in their language.")


class ClaimGuard(FrameProcessor):
    """(LLM -> TTS) Sentence by sentence, what may be spoken: a premature "it's done" becomes a progress line; text
    that isn't speech (a tool call written out as text), BolDoc's own instructions read out, and anything it has
    already said (or introducing itself again) are dropped; a reply left with nothing to say is asked for again, once
    per caller turn."""

    def __init__(self, session, **kwargs):
        super().__init__(**kwargs)
        self.s = session
        self._buf = ""
        self._replaced = False
        self._spoke = self._dropped = self._asked = False
        self._why = ""
        # A reply after the caller asked for a change, with nothing being written, is held until it ends and checked
        # against what the tools did (session.guard_reply): a tool call follows it, or it only says the change back and
        # asks, and it plays; it claims or promises the change though no tool was called, and it is dropped and asked
        # for again (turn_judge.claims_unmade_change).
        self._held: Optional[List[Tuple[Frame, FrameDirection]]] = None
        self._tool_seen = False
        self._deciding: Optional[asyncio.Task] = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._buf, self._replaced = "", False
            self._spoke = self._dropped = self._asked = False
            self._why = ""
            frame.metadata["boldoc_reply"] = self.s.reply_started()  # the playback gate knows whose frames it holds
            self._held, self._tool_seen = ([] if self.s.guard_reply() else None), False
            await self.push_frame(frame, direction)
        elif isinstance(frame, LLMTextFrame):
            self._buf += frame.text
            sentences, self._buf = split_sentences(self._buf)
            for s in sentences:
                await self._emit(s)
        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._buf.strip():
                await self._emit(self._buf)
            self._buf = ""
            if self._held is not None:  # decided once the tools have had their say (a task: frames keep flowing)
                self._held.append((frame, direction))
                self._deciding = self.create_task(self._decide_held())
                return
            await self.push_frame(frame, direction)
            await self._after_reply()
        elif isinstance(frame, InterruptionFrame):
            self._buf = ""
            if self._deciding is not None:
                await self.cancel_task(self._deciding)
                self._deciding = None
            self._held = None
            await self.push_frame(frame, direction)
        else:
            if "FunctionCall" in type(frame).__name__:
                self._tool_seen = True
            await self.push_frame(frame, direction)

    async def _out(self, frame: Frame) -> None:
        """A sentence for text-to-speech: held while the reply is being checked, else on its way."""
        if self._held is not None:
            self._held.append((frame, FrameDirection.DOWNSTREAM))
        else:
            await self.push_frame(frame)

    async def _decide_held(self) -> None:
        held, self._held = self._held or [], None
        text = " ".join(f.text for f, _ in held if isinstance(f, LLMTextFrame)).strip()
        check = None
        if text:
            check = self.create_task(self.s.reply_to_change(text))
        for _ in range(8):  # a tool call comes just after the reply's end (Pipecat runs them after it)
            if self._tool_seen:
                break
            await asyncio.sleep(0.03)
        if not self._tool_seen and await self.s.approved_change_unmade() and await self.s.make_approved_change():
            # The caller said yes to the change BolDoc said back and the model didn't make it: the session made it,
            # and the reply (a claim it's done, or the question again) is asked for again with that in [STATE].
            if check is not None:
                await self.cancel_task(check)
            if text:
                self.s.trace.add("claim_guard", why="change_made_for_model", text=text[:120])
                self._unsay()
            self._spoke, self._dropped, self._why = False, True, "made"
            for f, d in [(f, d) for f, d in held if not isinstance(f, LLMTextFrame)]:
                await self.push_frame(f, d)
            await self._after_reply()
            return
        verdict = None
        if not self._tool_seen and text and await self.s.change_requested(wait=0.6):
            verdict = await check if check is not None else None
        elif check is not None:
            await self.cancel_task(check)
        if verdict in ("claims", "ignores"):
            self.s.log.warning(f"claim guard: the reply {verdict} a change no tool was asked to make")
            self.s.trace.add("claim_guard", why=f"change_{verdict}", text=text[:120])
            self._unsay()  # never heard: neither said nor shown
            self._spoke, self._dropped = False, True
            self._why = "unacted" if verdict == "claims" else "change_ignored"
            held = [(f, d) for f, d in held if not isinstance(f, LLMTextFrame)]
        for f, d in held:
            await self.push_frame(f, d)
        await self._after_reply()

    def _unsay(self) -> None:
        """The held reply is dropped whole: its sentences, traced as said when they passed, were never heard."""
        for sentence in self.s.forget_unheard():
            self.s.trace.add("unsaid", sig=signature(sentence))

    async def _after_reply(self) -> None:
        """The reply has ended: one that said nothing it should is asked for again (once per caller turn)."""
        if not self._spoke:
            self.s.announcement_unheard(reply_said_nothing=True)  # an announcement whose reply said nothing
        if self._dropped and not self._spoke and self.s.ending and self._why != "made":
            await self.s.goodbye_fallback()  # the goodbye said nothing: a fixed one now, not a retry and silence
            return
        retried = False
        if self._why == "made":
            if not self.s.ask_again(MADE_NOTE):
                await self.s.changing_now()  # never silence while the change is being made
            return
        if self._why == "unacted":
            retried = self.s.ask_again(UNACTED_NOTE)
        elif self._why == "change_ignored":
            retried = self.s.ask_again(CHANGE_IGNORED_NOTE)
        elif self._why == "unacted_while_writing":
            retried = self.s.ask_again(WHILE_WRITING_NOTE)
        elif self._dropped and not self._spoke:
            if self._why == "repeat":
                retried = self.s.ask_again(REPEAT_NOTE)
            elif self._why == "channel":
                retried = self.s.ask_again(CHANNEL_NOTE)
            elif self._why == "instruction":
                retried = self.s.ask_again(INSTRUCTION_NOTE)
            else:
                retried = self.s.ask_again_after_malformed_reply()
        if self._dropped and not self._spoke and not retried:
            await self.s.nothing_said()  # its retry was dropped too: a short "say that again", not silence

    async def _emit(self, sentence: str) -> None:
        convert = TO_DEVANAGARI.get(detect(sentence)) if self.s.lang.family in ("hinglish", "en-IN") else None
        if convert is not None:
            sentence = convert(sentence)  # a Hindi conversation is never answered in Gurmukhi or Bengali
            self.s.trace.add("script_fixed", side="reply")
        if is_not_speech(sentence):
            self.s.log.warning("claim guard: dropped a reply that isn't speech (a tool call written as text?)")
            self.s.trace.add("not_speech", text=sentence[:80])
            self._dropped, self._why = True, "not_speech"
            return
        if self.s.echoes_instruction(sentence):  # a tool's answer or a note read out instead of followed
            self.s.log.warning("claim guard: dropped a sentence that reads out BolDoc's own instructions")
            self.s.trace.add("instruction_echo", text=sentence[:120])
            self._dropped, self._why = True, "instruction"
            return
        if self._asked and chained_question(sentence):
            self.s.trace.add("second_question", text=sentence[:120])
            return  # one thing at a time: the second ask comes in a later turn
        sentence = one_question(sentence)
        # "aapka document ready hai" with no document on this call is never true
        unacted = not self.s.work_in_progress() and (
            (is_completion_claim(sentence) and (self.s.unacted_change() or self.s.no_document_yet())) or
            (is_progress_claim(sentence) and not self.s.acted_since_turn))
        if unacted:  # "kar di hai" / "likh rahi hoon" with no tool called and nothing being written
            self.s.log.warning("claim guard: dropped a claim of work no tool was asked to do")
            self.s.trace.add("claim_guard", why="unacted", text=sentence[:120])
            self._dropped, self._why = True, "unacted"
            return
        if is_wrong_channel(sentence):  # "email par link mil jayega": it only ever comes by SMS
            self.s.log.warning("claim guard: dropped a promise of email or WhatsApp delivery")
            self.s.trace.add("claim_guard", why="channel", text=sentence[:120])
            self._dropped, self._why = True, "channel"
            return
        if self.s.said_before(sentence) and not self.s.may_ask_again(sentence):
            self.s.log.warning("claim guard: dropped a sentence BolDoc has already said")
            self.s.trace.add("repeat_drop", sig=signature(sentence), text=sentence[:120])
            self._dropped, self._why = True, "repeat"
            return
        if self.s.work_in_progress() and is_completion_claim(sentence) and self.s.said_something_new():
            # What the caller adds must reach it.
            self.s.log.warning("claim guard: dropped a claim about the caller's new words while writing")
            self.s.trace.add("claim_guard", why="unacted_while_writing", text=sentence[:120])
            self._dropped, self._why = True, "unacted_while_writing"
            return
        self._spoke = True
        self._asked = self._asked or sentence.rstrip().endswith("?")
        self.s.remember_said(sentence)
        self.s.trace.add("said", sig=signature(sentence), words=len(sentence.split()))
        if self.s.work_in_progress() and is_completion_claim(sentence):
            self.s.log.warning("claim guard: replaced a premature completion claim")
            self.s.trace.add("claim_guard", text=sentence)
            if not self._replaced:
                self._replaced = True
                self.s.caption_said(progress_line(self.s.lang.family))
                await self._out(LLMTextFrame(progress_line(self.s.lang.family) + " "))
            return
        self.s.caption_said(sentence)
        await self._out(LLMTextFrame(speakable(sentence, self.s.lang.family == "hinglish") + " "))


class FailoverOnError(ServiceSwitcherStrategyManual):
    """The talker's LLMs, primary first: any error from the active one on a turn moves the rest of the call to the
    next (a rate limit, a timeout, a 5xx as much as a rejected key). Pipecat's own failover moves only when a service
    can no longer be used, and a rate-limited Sarvam stays "usable" while the caller hears nothing. It never moves
    back: one switch, then the same model to the end of the call."""

    async def handle_error(self, error: ErrorFrame):
        failed = error.processor or self._active_service
        if failed is not self._active_service:
            return None  # not the talker's: another service's error
        idx = self._services.index(self._active_service)
        if idx + 1 >= len(self._services):
            return None  # the fallback itself failed: nowhere left to go
        return await self._set_active_if_available(self._services[idx + 1])


class PlaybackGate(FrameProcessor):
    """(TTS -> output) Holds each reply until the caller has been quiet long enough, saying "हम्म" if they stopped
    mid-sentence, and drops it if they start a new turn first.

    The quiet is judged when the reply starts, often before its first audio exists (the model and the voice take 0.3-0.7
    s). So when that first audio arrives, the caller must still be quiet. Then the reply is held again (and dropped if
    their words start a turn)."""

    RESUMED_S = 0.25  # the caller's voice this recently, when the first audio comes: they have gone on

    def __init__(self, session, max_hold_s: float = 6.0, **kwargs):
        super().__init__(**kwargs)
        self.s = session
        self.max_hold_s = max_hold_s  # a 3.5 s hold for an unfinished sentence, plus a pause broken by "umm"
        self._held: List[Tuple[Frame, FrameDirection]] = []
        self._holding = False
        self._since = 0.0
        self._acked = False
        self._watcher: Optional[asyncio.Task] = None
        self._first_audio = False  # a released reply whose first audio hasn't come yet
        self._reply: Optional[int] = None  # the reply whose frames are arriving (stamped by ClaimGuard)
        self._held_replies: List[Optional[int]] = []  # the replies with frames held: a release or drop is about them

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (InterruptionFrame, EndFrame)):
            await self._drop("interrupted" if isinstance(frame, InterruptionFrame) else "end")
            await self.push_frame(frame, direction)
            return
        if direction != FrameDirection.DOWNSTREAM or isinstance(frame, SystemFrame):
            await self.push_frame(frame, direction)
            return
        if "FunctionCall" in type(frame).__name__:
            # a tool call, not speech: never hold it. It takes a held reply with it (keeping their order) only once the
            # caller has finished; over a caller still talking, the reply stays held
            if self._holding and not self._may_play():
                self.s.trace.add("gate_kept", why="tool call")
                await self.push_frame(frame, direction)
                return
            await self._release("tool call")
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, LLMFullResponseStartFrame):
            self._reply = frame.metadata.get("boldoc_reply", self._reply)
            if not self._holding:
                self._holding, self._since, self._acked, self._first_audio = True, time.monotonic(), False, False
                self._watcher = self.create_task(self._watch())
        if self._holding:
            self._hold(frame, direction)
            return
        if isinstance(frame, TTSAudioRawFrame) and self._first_audio:
            self._first_audio = False
            if self.s.caller_silent_for() < self.RESUMED_S:  # they went on after the reply was released: wait again
                self._holding, self._since, self._acked = True, time.monotonic(), True
                self._hold(frame, direction)
                self.s.trace.add("gate_rehold")
                self.s.log.info("playback gate: the caller went on before the reply's first audio; holding it")
                self._watcher = self.create_task(self._watch())
                return
        await self.push_frame(frame, direction)

    async def _watch(self) -> None:
        while self._holding:
            await asyncio.sleep(0.04)
            waited = time.monotonic() - self._since
            gap = self.s.required_gap()
            silent = self.s.caller_silent_for()
            if not self._acked and self.s.wants_ack(silent):
                # the caller stopped mid-sentence: "हम्म" (they're heard, go on), outside the model and the reply.
                # Plain audio: the transport plays it but reports no "bot speaking" (only TTS or speech-stream audio
                # does), so the caller's turn stays open and their going on is an ordinary turn start.
                self._acked = True
                self.s.ack_started(silent)
                await self.push_frame(OutputAudioRawFrame(audio=self.s.ack_pcm, sample_rate=8000, num_channels=1))
            if self.s.ack_playing and time.monotonic() - self.s.last_ack_at < ACK_MAX_S:
                continue  # never start the reply over BolDoc's own "हम्म"
            if self._may_play() or waited >= self.max_hold_s:
                await self._release(f"silence≥{gap:.2f}s" if waited < self.max_hold_s else "max hold")
                return

    def _may_play(self) -> bool:
        """The caller has been quiet long enough for the held reply to play."""
        if self.s.ack_playing and time.monotonic() - self.s.last_ack_at < ACK_MAX_S:
            return False  # never start the reply over BolDoc's own "हम्म"
        waited = time.monotonic() - self._since
        gap = self.s.required_gap()
        # don't wait long on the classifier; and BolDoc speaking on her own ("it's ready") has no caller turn to judge
        judged = (self.s.verdict is not None or self.s.announcing is not None or waited >= gap + 0.5)
        return judged and self.s.caller_silent_for() >= gap

    async def play_clip(self, pcm: bytes) -> bool:
        """A sound straight to the line (the wait tone), never over a reply held or playing. Plain audio, like the
        "हम्म": the transport doesn't count it as BolDoc speaking."""
        if self._holding or self.s.bot_speaking or not pcm:
            return False
        await self.push_frame(OutputAudioRawFrame(audio=pcm, sample_rate=8000, num_channels=1))
        return True

    def _hold(self, frame: Frame, direction: FrameDirection) -> None:
        self._held.append((frame, direction))
        if self._reply not in self._held_replies:
            self._held_replies.append(self._reply)

    async def _release(self, why: str) -> None:
        if not self._holding:
            return
        held_for = time.monotonic() - self._since
        self._first_audio = not any(isinstance(f, TTSAudioRawFrame) for f, _ in self._held)  # checked when it comes
        while self._held:  # frames arriving meanwhile are appended and drained in order
            f, d = self._held.pop(0)
            await self.push_frame(f, d)
        self._holding = False
        for reply in self._held_replies or [None]:
            self.s.reply_heard(reply)
        self._held_replies = []
        self.s.trace.add("gate_release", held_ms=round(held_for * 1000), why=why)
        if held_for > 0.05:
            self.s.log.info(f"playback gate: released after {held_for * 1000:.0f} ms ({why}, "
                            f"caller spoke {self.s.speech_s:.1f}s)")

    async def _drop(self, why: str) -> None:
        if self._holding and self._held:
            self.s.log.info(f"playback gate: dropped an unheard reply ({why}, {len(self._held)} frames)")
            self.s.trace.add("gate_drop", why=why)
            for reply in self._held_replies or [None]:  # only the replies it held, not one made since
                for sentence in self.s.forget_unheard(reply):  # never heard, so never said: it may be said later
                    self.s.trace.add("unsaid", sig=signature(sentence))
        self._held, self._holding, self._first_audio, self._held_replies = [], False, False, []
        if self._watcher is not None:
            await self.cancel_task(self._watcher)
            self._watcher = None


class LanguageRouter(FrameProcessor):
    """(LLM -> TTS) Before each reply, switch BolDoc's voice and TTS language if the caller changed language.
    Sits right before the TTS so the update is applied before the reply's first word is synthesised."""

    def __init__(self, session, families: set, make_update, initial: str = "hinglish", **kwargs):
        super().__init__(**kwargs)
        self.s = session
        self.families = families
        self.make_update = make_update
        self.applied = initial

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame) and direction == FrameDirection.DOWNSTREAM:
            family = self.s.lang.family
            if family != self.applied and family in self.families:
                await self.push_frame(self.make_update(family))
                self.applied = family
                self.s.log.info(f"voice: switched to {family}")
                self.s.trace.add("voice_switch", family=family)
        await self.push_frame(frame, direction)


class CallerTurnStart(MinWordsUserTurnStartStrategy):
    """Start the caller's turn on their first real word, and never throw their words away.

    Pipecat's MinWords strategy, while the bot is talking, resets the user aggregation on any transcript shorter
    than min_words. On a live call an interim "Excel" (1 word) wiped the final "Excel में बना दीजिए", the turn
    started empty and BolDoc said "awaaz cut gayi thi". Here, while BolDoc talks, only real words count
    (acknowledgements like "haan", "theek hai" never interrupt her), and nothing is ever reset. Once she is quiet,
    anything the caller says is their turn: a bare "haan" answers "Likh doon?" (it used to wait for a next turn that
    never came). An acknowledgement said over the end of her question is that answer: when she stops, it becomes the
    caller's turn (LateAnswer).
    """

    def __init__(self, hold: Optional[Callable[[], bool]] = None, asked: Optional[Callable[[], bool]] = None,
                 late: Optional[LateAnswer] = None, **kwargs):
        super().__init__(min_words=1, **kwargs)
        self._caller_speaking = False
        self._barge: Optional[asyncio.Task] = None
        self._bot_started_at = 0.0
        self._asked = asked or (lambda: False)  # BolDoc's line asks the caller something (it ends in a question)
        self.late = late or LateAnswer()
        self._unclaimed: List[str] = []         # acknowledgements said over her current line
        self._late_task: Optional[asyncio.Task] = None
        # BolDoc's last goodbye is playing or waiting to play: the caller's "ok, bye" over it starts no turn and doesn't
        # cut it off or drop it
        self._hold = hold or (lambda: False)

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        # Talking over BolDoc for BARGE_IN_S stops her before speech recognition has a word: interruptions took
        # 0.62-0.79 s on a real call waiting for the first word. "haan"/"hmm" are shorter than that. Either way round:
        # the caller starts over BolDoc, or BolDoc starts (a reply released a moment before the caller went on) while
        # the caller is talking.
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_started_at = time.monotonic()
            self._unclaimed = []  # an acknowledgement over an earlier line was about that line
            await self._cancel_late()
        elif isinstance(frame, BotStoppedSpeakingFrame) and self._unclaimed and self._asked() and not self._hold():
            await self._cancel_late()
            self._late_task = self.task_manager.create_task(self._take_late_answer(), f"{self}::late_answer")
        starts_over = (isinstance(frame, VADUserStartedSpeakingFrame) and self._bot_speaking) or (
            isinstance(frame, BotStartedSpeakingFrame) and self._caller_speaking)
        together = (isinstance(frame, VADUserStartedSpeakingFrame) and self._bot_speaking
                    and time.monotonic() - self._bot_started_at < GLARE_S and not self._hold())
        if together:
            self._caller_speaking = True
            await self.trigger_user_turn_started()
            return await super().process_frame(frame)
        if starts_over and BARGE_IN_S > 0 and self._barge is None:
            self._barge = self.task_manager.create_task(self._barge_in(), f"{self}::barge_in")
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            await self._cancel_barge()
        # A partial transcript starts a turn only while the caller is speaking. Saaras sends partials for audio it has
        # already finalized: one arriving 50 ms after a turn ended started an empty turn that cut off BolDoc's reply and
        # then waited out a 1 s fallback. After the caller stops, the final decides.
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._caller_speaking = True
            await self._cancel_late()  # they're going on: what they say now makes the turn, the answer joins it
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._caller_speaking = False
        elif isinstance(frame, InterimTranscriptionFrame) and not self._caller_speaking:
            return ProcessFrameResult.CONTINUE
        return await super().process_frame(frame)

    async def _handle_transcription(self, frame):
        if self._hold():
            return ProcessFrameResult.CONTINUE
        if real_words(frame.text) > 0 or not self._bot_speaking:
            await self.trigger_user_turn_started()
            return ProcessFrameResult.STOP
        if isinstance(frame, TranscriptionFrame):  # final, and only an acknowledgement, over BolDoc
            self._unclaimed.append(frame.text)
        return ProcessFrameResult.CONTINUE

    async def _take_late_answer(self) -> None:
        try:
            await asyncio.sleep(LATE_ANSWER_S)
        except asyncio.CancelledError:
            return
        self._late_task = None
        if self._unclaimed and not self._caller_speaking and not self._bot_speaking and not self._hold():
            self.late.text = " ".join(self._unclaimed)
            self._unclaimed = []
            await self.trigger_user_turn_started()

    async def _cancel_late(self) -> None:
        if self._late_task is not None:
            await self.task_manager.cancel_task(self._late_task)
            self._late_task = None

    async def handle_user_turn_started(self):
        await super().handle_user_turn_started()
        await self._cancel_barge()
        self._unclaimed = []
        await self._cancel_late()

    async def _barge_in(self) -> None:
        try:
            await asyncio.sleep(BARGE_IN_S)
        except asyncio.CancelledError:
            return
        self._barge = None
        if self._caller_speaking and self._bot_speaking and not self._hold():
            await self.trigger_user_turn_started()

    async def _cancel_barge(self) -> None:
        if self._barge is not None:
            await self.task_manager.cancel_task(self._barge)
            self._barge = None


class CallerTurnStop(TurnAnalyzerUserTurnStopStrategy):
    """Smart Turn decides when the caller has finished, without a needless wait when the words come late, and without
    waiting out its fallback when speech recognition has already ended the sentence.

    Saaras finalizes after our VAD has seen the caller stop, so a short answer's turn often starts from its final
    transcript, with the caller already silent. Pipecat resets the stop strategy at every turn start, which forgot that
    stop and Smart Turn's verdict on it; the turn then ended only when an STT safety timer ran out (p99 1.0 s minus
    VAD's 0.2 s): 1.2 s end-of-turn instead of ~0.4 s on the gate. A turn that starts after a stop the caller hasn't
    spoken since keeps what that stop established: complete ends the turn on the final transcript, incomplete still
    waits for Smart Turn's silence fallback.
    """

    SHORT_ANSWER_SILENCE_S = 0.5  # a whole short answer ends the turn after this much silence, not Smart Turn's 1.0 s

    def __init__(self, *, short_answer: Optional[Callable[[str], bool]] = None,
                 short_answer_s: Optional[float] = None, late: Optional[LateAnswer] = None, **kwargs):
        super().__init__(**kwargs)
        self._late = late  # an answer said over BolDoc's question: its turn is just that (CallerTurnStart)
        self._late_end: Optional[asyncio.Task] = None
        self._short_answer = short_answer  # the session's "this short reply is a whole answer" (talker's rules)
        if short_answer_s is not None:  # the agent's settings for this call
            self.SHORT_ANSWER_SILENCE_S = short_answer_s
        self._vad_stop_at = 0.0
        self._short_end: Optional[asyncio.Task] = None

    async def _handle_vad_user_started_speaking(self, frame):
        await super()._handle_vad_user_started_speaking(frame)
        if self._short_end is not None:  # they went on: not the end after all
            self._short_end.cancel()
            self._short_end = None

    async def _handle_vad_user_stopped_speaking(self, frame):
        self._vad_stop_at = time.monotonic()
        await super()._handle_vad_user_stopped_speaking(frame)

    async def _end_short_answer(self, delay: float) -> None:
        await asyncio.sleep(delay)
        self._short_end = None
        if self._vad_stopped and not self._vad_user_speaking and not self._turn_complete:
            self._turn_complete = True
            await self._maybe_trigger_user_turn_stopped()

    async def _handle_transcription(self, frame):
        await super()._handle_transcription(frame)
        # Smart Turn heard "not finished", but speech recognition ended the sentence ("account band करवाना है।"): Saaras
        # punctuates a final only when the sentence ended and returns fragments bare, so the turn is over.
        text = str(frame.text)
        if not (getattr(frame, "finalized", False) and self._vad_stopped and not self._vad_user_speaking
                and not self._turn_complete):
            return
        if text.rstrip().endswith(("।", ".", "?", "!", "॥")):
            self._turn_complete = True
            await self._maybe_trigger_user_turn_stopped()
        elif self._short_answer is not None and self._short_answer(text):
            # A whole short answer ("FBI", "Okay No", "loan लेना है"): Smart Turn called them unfinished, and a real
            # call waited out its 1.0 s fallback on 4 of 12 turns (1.24 s end-of-turn). Ending on the final itself split
            # "haan... sab sahi hai" into two turns on the gate (a talk-over, and a create_document made mid-sentence
            # and put off four times): so the turn ends after 0.5 s of silence instead, and speech before then cancels
            # it.
            left = self.SHORT_ANSWER_SILENCE_S - (time.monotonic() - self._vad_stop_at)
            if left <= 0:
                self._turn_complete = True
                await self._maybe_trigger_user_turn_stopped()
            elif self._short_end is None:
                self._short_end = asyncio.get_running_loop().create_task(self._end_short_answer(left))

    async def handle_user_turn_started(self):
        stopped_before = self._vad_stopped and not self._vad_user_speaking
        complete = self._turn_complete
        await super().handle_user_turn_started()
        if stopped_before:
            self._vad_stopped, self._turn_complete = True, complete
        late = self._late.text if self._late is not None else None
        if late and not self._vad_user_speaking:
            # already said and transcribed, the caller quiet since: the turn ends on it as soon as it has begun
            self._late.text = None
            self._text, self._transcript_finalized, self._turn_complete, self._vad_stopped = late, True, True, True
            self._late_end = asyncio.get_running_loop().create_task(self._end_late())

    async def _end_late(self) -> None:
        await asyncio.sleep(0.05)  # after the turn's start has gone through the aggregator
        self._late_end = None
        if self._turn_complete and self._transcript_finalized and not self._vad_user_speaking:
            await self._maybe_trigger_user_turn_stopped()


class PlayoutMarks(FrameProcessor):
    """(-> output) A checkpoint right behind each reply's last audio, so Plivo says when the caller has heard all of
    it. It goes in the transport's queue after the audio (sent as the audio is, at real-time pace), so an
    interruption, which empties that queue, takes it too: a reply cut off never gets one."""

    def __init__(self, playout, stream_id: str, **kwargs):
        super().__init__(**kwargs)
        self.playout, self.stream_id = playout, stream_id
        self._audio = False  # the speech being queued now has audio (a TTS run without any needs no checkpoint)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            return
        if isinstance(frame, TTSAudioRawFrame):
            self._audio = True
        elif isinstance(frame, InterruptionFrame):
            self._audio = False
        elif isinstance(frame, TTSStoppedFrame) and self._audio:  # also what ends "bot speaking" in the transport
            self._audio = False
            await self.push_frame(OutputTransportMessageFrame(message={
                "event": "checkpoint", "streamId": self.stream_id, "name": self.playout.name()}))


class PlivoPlayoutSerializer(PlivoFrameSerializer):
    """Pipecat's Plivo serializer, which drops Plivo's playback answers: they go to the call's Playout, with the
    moments its checkpoints and clears actually went out."""

    def __init__(self, *args, playout, **kwargs):
        super().__init__(*args, **kwargs)
        self.playout = playout

    async def serialize(self, frame: Frame):
        if isinstance(frame, InterruptionFrame):  # Pipecat sends clearAudio for it
            self.playout.cut()
        elif isinstance(frame, OutputTransportMessageFrame) and isinstance(frame.message, dict) and \
                frame.message.get("event") == "checkpoint":
            self.playout.sent(str(frame.message.get("name")))
        return await super().serialize(frame)

    async def deserialize(self, data):
        # media is nearly every message: parsed twice only for the two events the base class ignores
        if isinstance(data, str) and ('"playedStream"' in data or '"clearedAudio"' in data):
            with contextlib.suppress(ValueError):
                msg = json.loads(data)
                if msg.get("event") in ("playedStream", "clearedAudio"):
                    self.playout.on_event(msg)
                    return None
        return await super().deserialize(data)
