"""Pipecat processors in a real (test) pipeline: the claim guard, the playback gate, Plivo checkpoints."""
import asyncio
import json

import pytest

pytest.importorskip("pipecat")

from loguru import logger
from pipecat.frames.frames import (
    InputDTMFFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputTransportMessageFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.serializers.plivo import PlivoFrameSerializer
from pipecat.tests.utils import SleepFrame, run_test

from apps.talker.logic.claims import PROGRESS_LINE, PROGRESS_LINES
from apps.talker.logic.playout import Playout
from apps.talker.logic.turns import reply_gap_s
from apps.talker.processors import ClaimGuard, PlaybackGate, PlayoutMarks, PlivoPlayoutSerializer
from apps.talker.trace import CallTrace


class FakeSession:
    def __init__(self, busy=False, silent_for=10.0, speech_s=0.5, verdict="complete"):
        from apps.talker.logic.language import LanguageTracker
        from apps.talker.trace import CallTrace
        self.busy, self.silent_for, self.speech_s, self.verdict = busy, silent_for, speech_s, verdict
        self.log = logger
        self.trace = CallTrace("test", content=True)
        self.lang = LanguageTracker()
        self.tool_calls = 0

    def on_tool_call(self):
        self.tool_calls += 1

    captions = ()
    ending = False          # the call is ending: a dropped goodbye gets the fixed one
    goodbyes = 0
    guarded = False         # a test sets it: this reply is checked against the tools before it's heard
    asks_change = False     # ...the caller's words ask for a change
    claims_change = False   # ...and the reply claims it

    def caption_said(self, sentence):
        self.captions = (*self.captions, sentence)

    def guard_reply(self):
        return self.guarded

    async def change_requested(self, wait=0.6):
        return self.asks_change

    async def reply_to_change(self, reply):
        return "claims" if self.claims_change else "asks"

    def required_gap(self):
        return max(reply_gap_s(self.speech_s), 2.0) if self.verdict == "incomplete" else reply_gap_s(self.speech_s)

    ack_pcm = None  # no हम्म unless a test gives the session one
    ack_playing = False
    last_ack_at = -1e9
    acks = 0
    user_speaking = bot_speaking = False

    def wants_ack(self, silent_s):
        return self.verdict == "incomplete" and self.ack_pcm is not None and silent_s >= 1.2 and not self.acks

    def ack_started(self, silent_s):
        import time
        self.acks += 1
        self.ack_playing, self.last_ack_at = True, time.monotonic()

    def ack_done(self):
        self.ack_playing = False

    def work_in_progress(self):
        return self.busy

    def caller_silent_for(self):
        return self.silent_for

    asked_again = 0
    said = None
    notes = None

    retries_left = 1  # once per caller turn, as in the session
    said_again = 0

    def ask_again_after_malformed_reply(self):
        return self.ask_again("not speech")

    def ask_again(self, note):
        self.asked_again += 1
        self.notes = (self.notes or []) + [note]
        if self.retries_left <= 0:
            return False
        self.retries_left -= 1
        return True

    async def goodbye_fallback(self):
        self.goodbyes += 1

    async def nothing_said(self):
        self.said_again += 1

    approved_unmade = False  # a test sets it: the caller just said yes to a change no tool has made
    changes_made = 0
    said_changing = 0
    unheard_announcements = 0

    async def approved_change_unmade(self):
        return self.approved_unmade

    async def make_approved_change(self):
        self.changes_made += 1
        self.approved_unmade = False
        return True

    async def changing_now(self):
        self.said_changing += 1

    def announcement_unheard(self, reply_said_nothing=False):
        self.unheard_announcements += 1

    said_new = False  # the caller's last turn had words of their own that no tool has acted on

    def said_something_new(self):
        return self.said_new

    def remember_said(self, text):
        from apps.talker.logic.claims import normalized
        self.said = (self.said or []) + [normalized(text)]

    def said_before(self, sentence):
        from apps.talker.logic.claims import is_self_intro, repeats
        return is_self_intro(sentence) or repeats(sentence, self.said or [])

    reask = 0  # how many times the waiting change's question may still be asked again (session.may_ask_again)

    def may_ask_again(self, sentence):
        from apps.talker.logic.turns import asked_to_change
        if self.reask and asked_to_change(sentence, also_write=False):
            self.reask -= 1
            return True
        return False

    instructions = ()

    def echoes_instruction(self, sentence):
        from apps.talker.logic.claims import echoes
        return echoes(sentence, list(self.instructions))

    heard = forgotten = 0

    def reply_started(self):
        return None

    def reply_heard(self, reply=None):
        self.heard += 1

    def forget_unheard(self, reply=None):
        self.forgotten += 1
        self.forgot_replies = [*getattr(self, "forgot_replies", []), reply]
        return ["ji samajh gayi"]

    unacted = False
    acted_since_turn = True  # as if the reply follows a tool call; tests of unacted claims set it False

    def unacted_change(self):
        return self.unacted

    no_docs = False

    def no_document_yet(self):
        return self.no_docs


def texts(frames):
    return [f.text for f in frames if isinstance(f, LLMTextFrame)]


def audio(n=3):
    return [TTSAudioRawFrame(audio=b"\x00" * 320, sample_rate=8000, num_channels=1) for _ in range(n)]


def test_claim_guard_replaces_premature_completion_only_while_work_is_running():
    reply = [LLMFullResponseStartFrame(), LLMTextFrame("26 से 27 October कर दिया"), LLMTextFrame(" है। Link SMS से"),
             LLMTextFrame(" आ जाएगा।"), LLMFullResponseEndFrame()]
    session = FakeSession(busy=True)
    session.lang.family = "hinglish"
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=reply))
    assert texts(down) == [PROGRESS_LINE + " ", "Link SMS से आ जाएगा। "]
    english = FakeSession(busy=True)  # an English call gets its line in English
    down, _ = asyncio.run(run_test(ClaimGuard(english), frames_to_send=list(reply)))
    assert texts(down)[0] == PROGRESS_LINES["en-IN"] + " "
    assert [e["e"] for e in session.trace.events if e["e"] != "said"] == ["claim_guard"]
    down, _ = asyncio.run(run_test(ClaimGuard(FakeSession(busy=False)), frames_to_send=list(reply)))
    assert "".join(texts(down)).strip() == "26 से 27 October कर दिया है। Link SMS से आ जाएगा।"


def test_claim_guard_drops_a_promise_of_email_delivery():
    reply = [LLMFullResponseStartFrame(), LLMTextFrame("Main likh rahi hoon. Aapko email par link"),
             LLMTextFrame(" mil jayega. Kuch aur?"), LLMFullResponseEndFrame()]
    session = FakeSession(busy=True)
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=reply))
    assert "".join(texts(down)).strip() == "Main likh rahi hoon. Kuch aur?"
    assert [e.get("why") for e in session.trace.events if e["e"] == "claim_guard"] == ["channel"]
    alone = FakeSession(busy=True)  # nothing else in the reply: BolDoc is asked to say it right
    down, _ = asyncio.run(run_test(ClaimGuard(alone), frames_to_send=[
        LLMFullResponseStartFrame(), LLMTextFrame("Link WhatsApp par bhej dungi."), LLMFullResponseEndFrame()]))
    assert texts(down) == [] and alone.notes and "SMS" in alone.notes[-1]


def test_playback_gate_releases_in_order_once_the_caller_is_quiet():
    gate = PlaybackGate(FakeSession(silent_for=5.0))
    frames = [LLMFullResponseStartFrame(), LLMTextFrame("Ji. "), *audio(), LLMFullResponseEndFrame(), SleepFrame(0.2)]
    down, _ = asyncio.run(run_test(gate, frames_to_send=frames))
    kinds = [type(f).__name__ for f in down]
    assert kinds == ["LLMFullResponseStartFrame", "LLMTextFrame", "TTSAudioRawFrame", "TTSAudioRawFrame",
                     "TTSAudioRawFrame", "LLMFullResponseEndFrame"]


def test_playback_gate_drops_an_unheard_reply_when_the_caller_keeps_talking():
    session = FakeSession(silent_for=0.0, speech_s=9.0)  # caller still talking (long dictation)
    frames = [LLMFullResponseStartFrame(), LLMTextFrame("Ji, samajh gayi. "), *audio(), SleepFrame(0.2),
              InterruptionFrame(), SleepFrame(0.1)]
    down, _ = asyncio.run(run_test(PlaybackGate(session), frames_to_send=frames))
    assert not [f for f in down if isinstance(f, (TTSAudioRawFrame, LLMTextFrame))]
    assert [f for f in down if isinstance(f, InterruptionFrame)]
    # never heard, so never said (it may be said later) and traced as such
    assert (session.forgotten, session.heard) == (1, 0)
    assert [e for e in session.trace.events if e["e"] == "unsaid"]


def test_playback_gate_keeps_a_reply_held_when_a_tool_call_passes_while_the_caller_is_still_talking():
    """The tool call passes; the reply waits for the caller's pause like any other."""
    from pipecat.frames.frames import FunctionCallInProgressFrame
    talking = FakeSession(silent_for=0.0, speech_s=2.0, verdict="incomplete")
    call = FunctionCallInProgressFrame(function_name="create_document", tool_call_id="c1", arguments={})
    frames = [LLMFullResponseStartFrame(), LLMTextFrame("Likh doon? "), *audio(), LLMFullResponseEndFrame(), call,
              SleepFrame(0.2)]
    down, _ = asyncio.run(run_test(PlaybackGate(talking), frames_to_send=frames))
    assert [type(f).__name__ for f in down] == ["FunctionCallInProgressFrame"]
    assert [e["why"] for e in talking.trace.events if e["e"] == "gate_kept"] == ["tool call"]
    assert talking.heard == 0


def test_playback_gate_holds_a_released_reply_when_the_caller_goes_on_before_its_first_audio():
    """Its first audio now waits for the quiet again, and a turn the caller starts drops it unheard."""
    import time

    class Resumes(FakeSession):  # quiet, then talking again from 0.15 s, quiet again from 0.6 s
        def __init__(self):
            super().__init__(silent_for=5.0)
            self.t0 = time.monotonic()

        def caller_silent_for(self):
            t = time.monotonic() - self.t0
            return 0.0 if 0.15 <= t < 0.6 else 5.0

    session = Resumes()
    frames = [LLMFullResponseStartFrame(), LLMTextFrame("Ji. "), SleepFrame(0.3), *audio(), SleepFrame(0.6),
              LLMFullResponseEndFrame(), SleepFrame(0.1)]
    down, _ = asyncio.run(run_test(PlaybackGate(session), frames_to_send=frames))
    assert [e["e"] for e in session.trace.events].count("gate_rehold") == 1
    assert [type(f).__name__ for f in down] == ["LLMFullResponseStartFrame", "LLMTextFrame", "TTSAudioRawFrame",
                                                "TTSAudioRawFrame", "TTSAudioRawFrame", "LLMFullResponseEndFrame"]

    session = Resumes()                      # ...and their words start a turn: the reply is never heard
    frames = [LLMFullResponseStartFrame(), LLMTextFrame("Ji. "), SleepFrame(0.3), *audio(), SleepFrame(0.1),
              InterruptionFrame(), SleepFrame(0.1)]
    down, _ = asyncio.run(run_test(PlaybackGate(session), frames_to_send=frames))
    assert not [f for f in down if isinstance(f, TTSAudioRawFrame)]

    quiet = FakeSession(silent_for=5.0)      # still quiet when the audio comes: it plays at once, as before
    frames = [LLMFullResponseStartFrame(), LLMTextFrame("Ji. "), SleepFrame(0.2), *audio(), SleepFrame(0.1)]
    down, _ = asyncio.run(run_test(PlaybackGate(quiet), frames_to_send=frames))
    assert len([f for f in down if isinstance(f, TTSAudioRawFrame)]) == 3
    assert "gate_rehold" not in [e["e"] for e in quiet.trace.events]


def test_reply_gap_grows_with_how_long_the_caller_spoke():
    assert reply_gap_s(0.6) <= reply_gap_s(1.2) <= reply_gap_s(2.5) < reply_gap_s(9.0)
    assert reply_gap_s(2.5) > 0.56  # longer than the pauses measured between spoken sentences


def test_a_callers_answer_that_reuses_boldocs_words_reaches_the_model():
    """Transcripts are never dropped as echo now (phones and browsers cancel it)."""
    from pipecat.frames.frames import TranscriptionFrame

    from apps.talker.processors import CallerScript

    frames = [TranscriptionFrame("Yes, new document.", "u", "t"),
              TranscriptionFrame("leave application तैयार है", "u", "t")]
    down, _ = asyncio.run(run_test(CallerScript(FakeSession()), frames_to_send=frames))
    assert [f.text for f in down if isinstance(f, TranscriptionFrame)] == ["Yes, new document.",
                                                                           "leave application तैयार है"]


def test_playback_gate_waits_longer_when_the_caller_stopped_mid_thought():
    session = FakeSession(silent_for=1.0, speech_s=3.0, verdict="incomplete")  # 1 s of silence < 1.8 s needed
    frames = [LLMFullResponseStartFrame(), LLMTextFrame("Ji. "), *audio(), SleepFrame(0.3), InterruptionFrame(),
              SleepFrame(0.1)]
    down, _ = asyncio.run(run_test(PlaybackGate(session), frames_to_send=frames))
    assert not [f for f in down if isinstance(f, TTSAudioRawFrame)]  # caller resumed: never heard


def test_playback_gate_says_hmm_in_a_long_pause_and_holds_the_reply():
    """The caller stopped mid-sentence: after 1.2 s of silence the gate plays BolDoc's हम्म straight to the line
    (once), keeps the reply held, and drops it when the caller goes on."""
    from pipecat.frames.frames import OutputAudioRawFrame

    session = FakeSession(silent_for=1.5, speech_s=3.0, verdict="incomplete")
    session.ack_pcm = b"\x10\x00" * 2400
    frames = [LLMFullResponseStartFrame(), LLMTextFrame("Ji. "), *audio(), SleepFrame(0.3), InterruptionFrame(),
              SleepFrame(0.1)]
    down, _ = asyncio.run(run_test(PlaybackGate(session), frames_to_send=frames))
    hmm = [f for f in down if type(f) is OutputAudioRawFrame]
    assert len(hmm) == 1 and hmm[0].audio == session.ack_pcm and hmm[0].sample_rate == 8000
    assert not [f for f in down if isinstance(f, (TTSAudioRawFrame, LLMTextFrame))]  # the reply was never heard
    assert session.acks == 1


def test_playback_gate_never_says_hmm_after_a_finished_sentence():
    from pipecat.frames.frames import OutputAudioRawFrame

    session = FakeSession(silent_for=5.0, verdict="complete")
    session.ack_pcm = b"\x10\x00" * 2400
    frames = [LLMFullResponseStartFrame(), LLMTextFrame("Ji. "), *audio(), LLMFullResponseEndFrame(), SleepFrame(0.2)]
    down, _ = asyncio.run(run_test(PlaybackGate(session), frames_to_send=frames))
    assert not [f for f in down if type(f) is OutputAudioRawFrame]
    assert len([f for f in down if isinstance(f, TTSAudioRawFrame)]) == 3


def test_language_router_switches_voice_once_per_change():
    from pipecat.frames.frames import TTSUpdateSettingsFrame

    from apps.talker.processors import LanguageRouter
    session = FakeSession()
    made = []

    def update(family):
        made.append(family)
        return TTSUpdateSettingsFrame(settings={"voice": family})
    router = LanguageRouter(session, {"hinglish", "ta-IN"}, update)
    session.lang.observe("எனக்கு ஒரு விடுப்பு கடிதம் வேண்டும்")
    reply = [LLMFullResponseStartFrame(), LLMTextFrame("சரி. "), LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(router, frames_to_send=reply + list(reply)))
    kinds = [type(f).__name__ for f in down]
    assert made == ["ta-IN"] and kinds[0] == "TTSUpdateSettingsFrame" and kinds.count("TTSUpdateSettingsFrame") == 1


def test_caller_turn_starts_on_real_words_and_never_discards_them():
    from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
    from pipecat.turns.types import ProcessFrameResult

    from apps.talker.processors import CallerTurnStart

    async def go():
        s = CallerTurnStart()
        started, resets = [], []

        async def start():
            started.append(1)

        async def reset():
            resets.append(1)
        s.trigger_user_turn_started, s.trigger_reset_aggregation = start, reset
        s._bot_speaking = True
        ack = await s._handle_transcription(InterimTranscriptionFrame("haan theek hai", "u", "t"))
        real = await s._handle_transcription(TranscriptionFrame("Excel में बना दीजिए", "u", "t"))
        return ack, real, started, resets
    ack, real, started, resets = asyncio.run(go())
    assert ack == ProcessFrameResult.CONTINUE and real == ProcessFrameResult.STOP
    assert started == [1] and resets == []  # one real word is enough; nothing is ever wiped


def test_when_boldoc_and_the_caller_start_together_she_yields_at_once(monkeypatch):
    """Starting together, she yields as soon as it's a voice; later in her reply, interrupting still goes through the
    usual 0.3 s check (a "haan" doesn't stop her)."""
    import time

    from pipecat.frames.frames import BotStartedSpeakingFrame, VADUserStartedSpeakingFrame

    from apps.talker import processors
    from apps.talker.processors import CallerTurnStart
    monkeypatch.setattr(processors, "BARGE_IN_S", 0)  # the usual check is a task: off here, so nothing starts at once

    async def go(bot_for):
        s = CallerTurnStart()
        started = []

        async def start():
            started.append(1)
        s.trigger_user_turn_started = start
        await s.process_frame(BotStartedSpeakingFrame())
        s._bot_speaking = True
        s._bot_started_at = time.monotonic() - bot_for
        await s.process_frame(VADUserStartedSpeakingFrame())
        return started
    assert asyncio.run(go(0.1)) == [1]   # together: she stops now
    assert asyncio.run(go(2.0)) == []    # mid-reply: not at once


def _turn_controller(verdict, short_answer=None, asked=None):
    """Pipecat's real turn controller with our strategies and a stand-in for Smart Turn (fixed verdict)."""
    from pipecat.audio.turn.base_turn_analyzer import BaseTurnAnalyzer, BaseTurnParams, EndOfTurnState
    from pipecat.turns.user_turn_controller import UserTurnController
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    from apps.talker.processors import CallerTurnStart, CallerTurnStop

    class SmartTurn(BaseTurnAnalyzer):
        silence_timeout = False  # what Smart Turn's own "not finished" fallback returns once it runs out

        @property
        def speech_triggered(self):
            return False

        @property
        def params(self):
            return BaseTurnParams()

        def append_audio(self, buffer, is_speech):
            return EndOfTurnState.COMPLETE if self.silence_timeout else EndOfTurnState.INCOMPLETE

        async def analyze_end_of_turn(self):
            return (EndOfTurnState.COMPLETE if verdict == "complete" else EndOfTurnState.INCOMPLETE), None

        def clear(self):
            pass

    from apps.talker.processors import LateAnswer
    analyzer = SmartTurn()
    late = LateAnswer()
    c = UserTurnController(user_turn_strategies=UserTurnStrategies(
        start=[CallerTurnStart(asked=asked, late=late)],
        stop=[CallerTurnStop(turn_analyzer=analyzer, short_answer=short_answer, late=late)]))
    events = []

    @c.event_handler("on_user_turn_started")
    async def started(controller, strategy, params):
        events.append("start")

    @c.event_handler("on_user_turn_stopped")
    async def stopped(controller, strategy, params):
        events.append("stop")
    return c, analyzer, events


async def _setup(c):
    from pipecat.clocks.system_clock import SystemClock
    from pipecat.frames.frames import STTMetadataFrame
    from pipecat.processors.frame_processor import FrameProcessorSetup
    from pipecat.utils.asyncio.task_manager import TaskManager

    await c.setup(FrameProcessorSetup(clock=SystemClock(), task_manager=TaskManager(), pipeline_worker=None))
    await c.process_frame(STTMetadataFrame(service_name="sarvam", ttfs_p99_latency=1.0))  # what Saaras reports


def test_a_bare_haan_while_boldoc_is_quiet_is_the_callers_turn():
    """Once she is quiet, any word is the caller's turn."""
    from pipecat.frames.frames import TranscriptionFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame

    async def go():
        c, _, events = _turn_controller("complete")
        await _setup(c)
        for f in (VADUserStartedSpeakingFrame(start_secs=0.2), VADUserStoppedSpeakingFrame(stop_secs=0.2),
                  TranscriptionFrame("हाँ", "u", "t", finalized=True)):
            await c.process_frame(f)
        await asyncio.sleep(0.1)
        return events
    assert asyncio.run(go()) == ["start", "stop"]


def _over_her_end(asked, then=()):
    """BolDoc speaking; the caller's "Yes" over the end of her line (too short to cut her off); then she stops."""
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        TranscriptionFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
    )

    async def go():
        c, _, events = _turn_controller("complete", asked=lambda: asked)
        await _setup(c)
        await c.process_frame(BotStartedSpeakingFrame())
        await asyncio.sleep(0.6)            # well into her line (not the two of them starting together)
        for f in (VADUserStartedSpeakingFrame(start_secs=0.2),
                  VADUserStoppedSpeakingFrame(stop_secs=0.2), TranscriptionFrame("Yes", "u", "t", finalized=True)):
            await c.process_frame(f)
        await asyncio.sleep(0.1)
        over = list(events)                 # nothing while she speaks: an acknowledgement never cuts her off
        await c.process_frame(BotStoppedSpeakingFrame())
        for f in then:
            await asyncio.sleep(0.1)
            await c.process_frame(f)
        await asyncio.sleep(0.6)
        return over, events
    return asyncio.run(go())


def test_a_yes_said_over_the_end_of_her_question_is_answered_when_she_stops():
    over, events = _over_her_end(asked=True)
    assert over == [] and events == ["start", "stop"]


def test_a_yes_said_over_a_statement_stays_an_acknowledgement():
    over, events = _over_her_end(asked=False)
    assert over == [] and events == []


def test_a_caller_who_goes_on_after_she_stops_makes_one_turn_with_it():
    from pipecat.frames.frames import TranscriptionFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
    over, events = _over_her_end(asked=True, then=(
        VADUserStartedSpeakingFrame(start_secs=0.2), VADUserStoppedSpeakingFrame(stop_secs=0.2),
        TranscriptionFrame("likh dijiye.", "u", "t", finalized=True)))
    assert over == [] and events == ["start", "stop"]


def test_a_partial_transcript_after_the_caller_stopped_never_starts_a_turn():
    """a stale partial 50 ms after a turn ended started an empty turn that cut off the reply."""
    from pipecat.frames.frames import (
        InterimTranscriptionFrame,
        TranscriptionFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
    )

    async def go():
        c, _, events = _turn_controller("complete")
        await _setup(c)
        for f in (VADUserStartedSpeakingFrame(start_secs=0.2), InterimTranscriptionFrame("मुझे अपने HR", "u", "t"),
                  VADUserStoppedSpeakingFrame(stop_secs=0.2),
                  TranscriptionFrame("मुझे अपने HR manager के लिए leave application लिखनी है।", "u", "t",
                                     finalized=True),
                  InterimTranscriptionFrame("लिखनी है", "u", "t")):  # Saaras, for audio it already finalized
            await c.process_frame(f)
        await asyncio.sleep(0.1)
        await c.cleanup()
        return events
    assert asyncio.run(go()) == ["start", "stop"]  # the partial while speaking started it; the stale one didn't


def test_words_that_arrive_after_the_caller_stopped_end_the_turn_without_waiting():
    """'दोनों चाहिए' was finalized after VAD's stop; the turn then waited 0.8 s more (1.2 s total)."""
    from pipecat.frames.frames import (
        InputAudioRawFrame,
        TranscriptionFrame,
        VADUserStartedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
    )

    async def go(verdict):
        c, analyzer, events = _turn_controller(verdict)
        await _setup(c)
        await c.process_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
        await c.process_frame(VADUserStoppedSpeakingFrame(stop_secs=0.2))  # Smart Turn judges this stop
        await asyncio.sleep(0.05)
        await c.process_frame(TranscriptionFrame("दोनों चाहिए", "u", "t", finalized=True))  # the turn starts now
        await asyncio.sleep(0.3)
        at_once = list(events)
        analyzer.silence_timeout = True  # Smart Turn's silence fallback runs out
        await c.process_frame(InputAudioRawFrame(audio=b"\x00" * 320, sample_rate=8000, num_channels=1))
        await asyncio.sleep(0.05)
        await c.cleanup()
        return at_once, events
    assert asyncio.run(go("complete")) == (["start", "stop"], ["start", "stop"])  # ends on the final itself
    at_once, finally_ = asyncio.run(go("incomplete"))
    assert at_once == ["start"] and finally_ == ["start", "stop"]  # "not finished" still waits for the fallback


def test_a_short_whole_answer_ends_the_turn_without_waiting_out_smart_turn():
    """The talker's own rules call them whole answers."""
    from pipecat.frames.frames import TranscriptionFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame

    async def go(text, goes_on=False):
        c, _, events = _turn_controller("incomplete", short_answer=lambda t: t in ("FBI", "Okay No"))
        await _setup(c)
        await c.process_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
        await c.process_frame(VADUserStoppedSpeakingFrame(stop_secs=0.2))
        await asyncio.sleep(0.05)
        await c.process_frame(TranscriptionFrame(text, "u", "t", finalized=True))
        await asyncio.sleep(0.2)
        early = list(events)
        if goes_on:  # "haan... sab sahi hai": they speak again before the 0.5 s are up
            await c.process_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
        await asyncio.sleep(0.5)
        await c.cleanup()
        return early, events
    # a whole short answer: the turn ends 0.5 s after the caller went quiet, not after Smart Turn's 1.0 s fallback
    assert asyncio.run(go("Okay No")) == (["start"], ["start", "stop"])
    assert asyncio.run(go("Okay No", goes_on=True))[1] == ["start"]   # they went on: the same turn continues
    assert asyncio.run(go("mera naam"))[1] == ["start"]               # not a whole answer: Smart Turn's wait stands


def test_short_answers_are_whole_answers_by_the_talkers_rules():
    from apps.talker.session import CallSession
    s = CallSession("s", None, "pstn", None)
    s.last_bot_line = "Bank ka naam kya hai jahan se loan lena hai?"
    s.speech_s = 0.6
    assert s.short_answer_done("FBI") and s.short_answer_done("Okay No")
    assert not s.short_answer_done("haan") and not s.short_answer_done("ਹਾਂ ji")  # often "haan, sab sahi hai"
    s.speech_s = 2.9
    assert not s.short_answer_done("नहीं Central Bank नहीं SBM")  # longer: the classifier and Smart Turn decide


def test_a_tool_call_that_arrives_after_the_reply_ended_is_counted():
    """Pipecat 1.13 runs a reply's function calls after LLMFullResponseEndFrame: the monitor logged every pure tool-call
    reply as "no speech and no tool call". Only a reply that stays empty warns."""
    from types import SimpleNamespace

    from pipecat.frames.frames import FunctionCallInProgressFrame, LLMFullResponseEndFrame, LLMFullResponseStartFrame
    from pipecat.observers.base_observer import FramePushed
    from pipecat.processors.frame_processor import FrameDirection

    from apps.talker.processors import SignalObserver

    class TalkerLLM:
        BOLDOC_LLM = True
    warnings, traced = [], []
    tools = []
    s = SimpleNamespace(on_llm_start=lambda: None, on_llm_end=lambda: None, announcing=None, log_content=False,
                        on_tool_call=lambda: tools.append(1),
                        trace=SimpleNamespace(add=lambda e, **k: traced.append(e)),
                        log=SimpleNamespace(info=lambda m: None, warning=lambda m: warnings.append(m)),
                        live=SimpleNamespace(publish=lambda *a, **k: None, set_state=lambda *a: None))
    obs = SignalObserver(s)
    llm = TalkerLLM()

    def pushed(frame):
        return FramePushed(source=llm, destination=None, frame=frame, direction=FrameDirection.DOWNSTREAM,
                           timestamp=0)

    async def go():
        await obs.on_push_frame(pushed(LLMFullResponseStartFrame()))
        await obs.on_push_frame(pushed(LLMFullResponseEndFrame()))
        await obs.on_push_frame(pushed(FunctionCallInProgressFrame(function_name="edit_document", tool_call_id="c1",
                                                                   arguments={})))
        await asyncio.sleep(0.6)
        await obs.on_push_frame(pushed(LLMFullResponseStartFrame()))   # a reply that really says and does nothing
        await obs.on_push_frame(pushed(LLMFullResponseEndFrame()))
        await asyncio.sleep(0.6)
    asyncio.run(go())
    assert traced.count("tool_call") == 1 and warnings == ["llm: reply had no speech and no tool call"]
    assert tools == [1]  # the session hears of it too: a goodbye that came with a tool call is the tool's to handle


def test_text_that_is_not_speech_is_never_spoken_and_the_reply_is_asked_for_again():
    """Qwen3 on Bedrock once streamed a tool call as text ('create游戏副本\\n{'): text-to-speech must never get it."""
    from apps.talker.logic.claims import is_not_speech
    assert is_not_speech("create游戏副本\n{") and is_not_speech('create_document {"formats"')
    assert is_not_speech("[STATE — facts") and is_not_speech("}")
    assert not is_not_speech("ठीक है, PDF और Word दोनों बना देती हूँ।") and not is_not_speech("சரி, எழுதுகிறேன்.")
    assert not is_not_speech("Your invoice for ₹12,000 + GST is being written.")

    session = FakeSession()
    garbled = [LLMFullResponseStartFrame(), LLMTextFrame("create游戏副本"), LLMTextFrame("\n{"),
               LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=garbled))
    assert texts(down) == [] and session.asked_again == 1
    assert [e["e"] for e in session.trace.events] == ["not_speech"]
    # speech followed by a stray brace: the speech is kept, nothing is asked again
    session = FakeSession()
    mixed = [LLMFullResponseStartFrame(), LLMTextFrame("Ji, likh deti hoon. "), LLMTextFrame("{"),
             LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=mixed))
    assert texts(down) == ["Ji, likh deti hoon. "] and session.asked_again == 0


def test_boldocs_own_instructions_are_never_read_out_and_the_reply_is_asked_for_again():
    """end_call's answer read out word for word, then twice more from its history, and the
    change being confirmed was lost."""
    from apps.talker.logic.claims import instruction_sentences
    from apps.talker.processors import INSTRUCTION_NOTE
    told = ("The caller still wants something (or it isn't clear): answer what they just said. End the call only "
            "when they want nothing more.")
    session = FakeSession()
    session.instructions = instruction_sentences(told)
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=[
        LLMFullResponseStartFrame(), LLMTextFrame(told), LLMFullResponseEndFrame()]))
    assert texts(down) == [] and session.notes == [INSTRUCTION_NOTE]
    assert [e["e"] for e in session.trace.events] == ["instruction_echo", "instruction_echo"]
    # what an instruction quotes for BolDoc to say is spoken, and nothing is asked again
    session = FakeSession()
    session.instructions = instruction_sentences(told) + instruction_sentences(
        'Ask once more, briefly ("Maaf kijiye, theek se sunai nahi diya. Badal doon, haan ya nahi?"); don\'t read '
        'it all back again.')
    asked = "Maaf kijiye, theek se sunai nahi diya. Badal doon, haan ya nahi?"
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=[
        LLMFullResponseStartFrame(), LLMTextFrame(asked), LLMFullResponseEndFrame()]))
    assert " ".join("".join(texts(down)).split()) == asked and session.asked_again == 0


def test_a_final_that_came_just_before_its_turn_is_counted_in_it():
    """Saaras finalizes after VAD's stop, so the final can be what starts the turn: it is part of that turn."""
    from types import SimpleNamespace

    from pipecat.frames.frames import TranscriptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame
    from pipecat.observers.base_observer import FramePushed
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.services.stt_service import STTService

    from apps.talker.processors import SignalObserver

    traced = []
    s = SimpleNamespace(turn_open=False, bot_speaking=False, speech_s=1.0, last_user_speech_end=0.0, log_content=True,
                        on_turn_end=lambda: None, on_transcript=lambda t: None,
                        trace=SimpleNamespace(add=lambda e, **k: traced.append((e, k))),
                        log=SimpleNamespace(info=lambda m: None, warning=lambda m: None),
                        live=SimpleNamespace(publish=lambda *a, **k: None, set_state=lambda *a: None))
    obs = SignalObserver(s)

    class Saaras(STTService):
        async def run_stt(self, audio):
            yield None
    stt = Saaras.__new__(Saaras)  # only its type matters: the observer counts transcripts from speech recognition

    def pushed(frame, source=None):
        return FramePushed(source=source or object(), destination=None, frame=frame,
                           direction=FrameDirection.DOWNSTREAM, timestamp=0)

    async def go():
        await obs.on_push_frame(pushed(TranscriptionFrame("दोनों चाहिए", "u", "t"), stt))  # before the turn opens
        await obs.on_push_frame(pushed(UserStartedSpeakingFrame()))
        await obs.on_push_frame(pushed(UserStoppedSpeakingFrame()))
    asyncio.run(go())
    end = next(k for e, k in traced if e == "turn_end")
    assert (end["words"], end["text"]) == (2, "दोनों चाहिए")


def test_boldoc_never_says_the_same_thing_twice_nor_introduces_itself_again():
    """Said "namaste" and heard BolDoc's introduction and opening question again."""
    from apps.talker.logic.claims import is_self_intro, repeats, signature

    greeting = "namaste main boldoc hoon ek ai assistant"
    assert is_self_intro("Namaste! Main BolDoc hoon.") and is_self_intro("Hi, I'm BolDoc.")
    assert not is_self_intro("BolDoc par aapka swagat hai, boliye.")
    said = [greeting, "aapka account number kya hai"]
    assert repeats("Aapka account number kya hai?", said)                # word for word
    assert repeats("Aapka account number kya hai bataiye?", said)        # nearly
    assert not repeats("Ji, boliye.", said) and not repeats("Aapka naam kya hai?", said)
    assert signature("Aapka account number kya hai?") == signature("aapka  account number, kya hai")

    session = FakeSession()
    session.remember_said("Namaste, main BolDoc hoon, ek AI assistant.")
    session.remember_said("Boliye, aaj kya likhna hai?")
    reply = [LLMFullResponseStartFrame(), LLMTextFrame("Namaste! Main BolDoc hoon. "),
             LLMTextFrame("Aapko kis tarah ka document chahiye? "), LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=reply))
    # a short "Namaste!" back is fine; the introduction is not said again
    assert texts(down) == ["Namaste! ", "Aapko kis tarah ka document chahiye? "] and session.asked_again == 0
    # the same question again on the next turn: dropped, and the model is asked again (once) with a note
    again = [LLMFullResponseStartFrame(), LLMTextFrame("Aapko kis tarah ka document chahiye?"),
             LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=again))
    assert texts(down) == [] and session.asked_again == 1 and "repeat" in session.notes[-1]
    assert [e["e"] for e in session.trace.events].count("repeat_drop") == 2
    # its retry repeats too: no third try, and not silence : "say that again"
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=again))
    assert texts(down) == [] and session.said_again == 1


def test_the_waiting_changes_question_is_asked_again_when_the_answer_was_unclear():
    """Asked again it is, word for word, while the session allows (twice); anything else BolDoc said before is still
    dropped."""
    session = FakeSession()
    session.remember_said("सौरभ ji, 25 की जगह 26 October कर दूं?")
    session.remember_said("आपकी leave application अभी लिख रही हूँ, बस एक मिनट।")
    session.reask = 1
    ask = [LLMFullResponseStartFrame(), LLMTextFrame("सौरभ ji, 25 की जगह 26 October कर दूं?"), LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=ask))
    assert texts(down) == ["सौरभ ji, 25 की जगह 26 October कर दूं? "]
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=ask))
    assert texts(down) == []  # no more re-asks allowed: a repeat again
    session.reask = 1
    other = [LLMFullResponseStartFrame(), LLMTextFrame("आपकी leave application अभी लिख रही हूँ, बस एक मिनट।"),
             LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=other))
    assert texts(down) == [] and session.reask == 1  # only the change's question may come again


def test_debug_capture_matches_callers_in_either_format(monkeypatch):
    from apps.talker.plivo_stream import debug_caller
    monkeypatch.setenv("DEBUG_CALLERS", "919988776655, +91 90000 00000")
    assert debug_caller("+919988776655") and debug_caller("919988776655") and debug_caller("+919000000000")
    assert not debug_caller("+919999900001") and not debug_caller(None) and not debug_caller("")


def test_one_question_at_a_time_reaches_the_caller():
    """The model joins two asks about one reply in three ("bank kaunsa hai aur account number kya hai?")."""
    session = FakeSession()
    reply = [LLMFullResponseStartFrame(), LLMTextFrame("Achha, account band karna. "),
             LLMTextFrame("Aapka bank kaunsa hai aur aapka account number kya hai? "), LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=reply))
    assert texts(down) == ["Achha, account band karna. ", "Aapka bank kaunsa hai? "]
    chained = [LLMFullResponseStartFrame(), LLMTextFrame("Achha, letter. Kis baare mein hai? "),
               LLMTextFrame("Aur kiske naam se likhna hai? "), LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(ClaimGuard(FakeSession()), frames_to_send=chained))
    assert "".join(texts(down)) == "Achha, letter. Kis baare mein hai? "
    examples = [LLMFullResponseStartFrame(), LLMTextFrame("Namaste! Bataiye, aaj main kya likh doon? "),
                LLMTextFrame("Koi application, letter, ya bill? "), LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(ClaimGuard(FakeSession()), frames_to_send=examples))
    # the examples that follow a question are kept
    assert "".join(texts(down)) == "Namaste! Bataiye, aaj main kya likh doon? Koi application, letter, ya bill? "


def test_a_sentence_speech_recognition_ended_ends_the_turn_though_smart_turn_heard_more_coming():
    """"account bond करवाना है।" waited Smart Turn's 1.0 s fallback (1.39 s end of turn)."""
    from pipecat.frames.frames import TranscriptionFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame

    async def go(text):
        c, _, events = _turn_controller("incomplete")
        await _setup(c)
        await c.process_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
        await c.process_frame(VADUserStoppedSpeakingFrame(stop_secs=0.2))               # Smart Turn: not finished
        await c.process_frame(TranscriptionFrame(text, "u", "t", finalized=True))
        await asyncio.sleep(0.2)
        await c.cleanup()
        return events
    assert asyncio.run(go("account band करवाना है।")) == ["start", "stop"]     # punctuated: the sentence ended
    assert asyncio.run(go("account band karwana")) == ["start"]              # bare: wait for Smart Turn


def test_talking_over_bolDoc_interrupts_her_before_words_arrive_but_a_short_haan_does_not():
    """Real call: interruptions took 0.62-0.79 s waiting for the first recognised word."""
    from pipecat.frames.frames import BotStartedSpeakingFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame

    async def go(speech_s):
        c, _, events = _turn_controller("complete")
        await _setup(c)
        await c.process_frame(BotStartedSpeakingFrame())
        await asyncio.sleep(0.6)                       # BolDoc is into her reply (at its very start they'd be together)
        await c.process_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
        await asyncio.sleep(speech_s)
        await c.process_frame(VADUserStoppedSpeakingFrame(stop_secs=0.2))
        await asyncio.sleep(0.4)
        await c.cleanup()
        return events
    assert asyncio.run(go(0.5)) == ["start"]           # kept talking: interrupted at ~0.3 s (words follow later)
    assert asyncio.run(go(0.1)) == []                  # a quick "haan": BolDoc carries on


def test_a_change_claimed_done_without_the_tool_is_never_heard_and_the_tool_is_called():
    """"छुट्टी 26 से 27 October तक कर दीजिए" -> "kar di hai" with no edit_document: nothing changed."""
    from apps.talker.logic.claims import is_change_request
    assert is_change_request("छुट्टी 26 से 27 October तक कर दीजिए।") and is_change_request("date badal do")
    assert not is_change_request("हाँ सब सही है") and not is_change_request("ho gaya kya?")
    session = FakeSession()
    session.unacted = True
    reply = [LLMFullResponseStartFrame(), LLMTextFrame("Saurabh ji, 26 se 27 October ki chhutti kar di hai. "),
             LLMTextFrame("Abhi update kar deti hoon. "), LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=reply))
    assert texts(down) == ["Abhi update kar deti hoon. "]                 # the false "done" is gone
    assert session.asked_again == 1 and "edit_document" in session.notes[-1]
    acted = FakeSession()                                                # the tool was called: nothing to correct
    down, _ = asyncio.run(run_test(ClaimGuard(acted), frames_to_send=list(reply)))
    assert len(texts(down)) == 2 and acted.asked_again == 0


def test_bolDoc_starting_while_the_caller_talks_is_interrupted_too():
    """a reply released just before the caller went on played over them for 1.3 s."""
    from pipecat.frames.frames import BotStartedSpeakingFrame, VADUserStartedSpeakingFrame

    async def go():
        c, _, events = _turn_controller("complete")
        await _setup(c)
        await c.process_frame(VADUserStartedSpeakingFrame(start_secs=0.2))   # the caller is talking...
        await c.process_frame(BotStartedSpeakingFrame())                     # ...and BolDoc starts anyway
        await asyncio.sleep(0.45)
        await c.cleanup()
        return events
    assert asyncio.run(go()) == ["start"]


def test_writing_it_now_with_nothing_being_written_is_never_heard_and_the_tool_is_called():
    """create_document was put off while the caller talked; BolDoc then said "likh rahi hoon" for
    the rest of the call and no document was written."""
    session = FakeSession()
    session.acted_since_turn = False
    reply = [LLMFullResponseStartFrame(), LLMTextFrame("Saurabh ji, aapki leave application likh rahi hoon. "),
             LLMTextFrame("Kuch aur add karna hai? "), LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=reply))
    assert texts(down) == ["Kuch aur add karna hai? "] and "create_document" in session.notes[-1]
    writing = FakeSession(busy=True)                                     # a document really is being written
    writing.acted_since_turn = False
    down, _ = asyncio.run(run_test(ClaimGuard(writing), frames_to_send=list(reply)))
    assert len(texts(down)) == 2 and writing.asked_again == 0


def test_a_document_claimed_ready_when_none_was_written_is_never_heard():
    """the idle goodbye said "aapka document ready hai" after nothing had been written."""
    session = FakeSession()
    session.no_docs = True
    reply = [LLMFullResponseStartFrame(), LLMTextFrame("Namaste Saurabh ji, aapka document ready hai. "),
             LLMTextFrame("Koi aur kaam ho toh bataiye. "), LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=reply))
    assert texts(down) == ["Koi aur kaam ho toh bataiye. "]


def test_a_hindi_conversation_gets_gurmukhi_back_in_devanagari_both_ways():
    """Speech-to-text wrote a Hindi speaker in Gurmukhi and BolDoc answered in Punjabi."""
    from pipecat.frames.frames import TranscriptionFrame

    from apps.talker.logic.scripts import gurmukhi_to_devanagari
    from apps.talker.processors import CallerScript

    assert gurmukhi_to_devanagari("ਮੈਂ ਹਿੰਦੀ ਸਪੀਕਰ ਹਾਂ") == "मैं हिंदी सपीकर हां"
    assert gurmukhi_to_devanagari("ਗੱਲ ਕਰ ਸਕਦੇ ਹਾਂ, PDF ੨੬") == "गल्ल कर सकदे हां, PDF २६"
    frames = [TranscriptionFrame("ਮੈਂ ਹਿੰਦੀ ਸਪੀਕਰ ਹਾਂ", "u", "t"), TranscriptionFrame("हाँ ठीक है", "u", "t")]
    down, _ = asyncio.run(run_test(CallerScript(FakeSession()), frames_to_send=frames))
    assert [f.text for f in down if isinstance(f, TranscriptionFrame)] == ["मैं हिंदी सपीकर हां", "हाँ ठीक है"]
    tamil = FakeSession()
    tamil.lang.observe("எனக்கு ஒரு லீவ் லெட்டர் வேண்டும் நாளைக்கு")  # another language's call: Gurmukhi is still Hindi
    down, _ = asyncio.run(run_test(CallerScript(tamil), frames_to_send=[
        TranscriptionFrame("ਹਾਂ ਜੀ ਠੀਕ ਹੈ", "u", "t")]))
    assert [f.text for f in down if isinstance(f, TranscriptionFrame)] == ["हां जी ठीक है"]
    reply = [LLMFullResponseStartFrame(), LLMTextFrame("ਹਾਂ ਜੀ, ਮੈਂ ਸਮਝ ਗਈ। "), LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(ClaimGuard(FakeSession()), frames_to_send=reply))
    assert "".join(texts(down)).strip() == "हां जी, मैं समझ गई।"


def test_a_done_claim_about_the_callers_new_words_while_writing_is_asked_for_again():
    """"गाय का नाम सुखवंती है" while the letter was drafted; BolDoc said "naam add kar diya hai" with no tool called,
    the claim became "I'm working on it" and the name never reached the document."""
    session = FakeSession(busy=True)
    session.said_new = True
    reply = [LLMFullResponseStartFrame(), LLMTextFrame("Sukhwanti gaay ka naam bhi add kar diya hai. "),
             LLMFullResponseEndFrame()]
    down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=reply))
    assert texts(down) == [] and session.asked_again == 1 and "edit_document" in session.notes[-1]
    calm = FakeSession(busy=True)  # nothing new from the caller ("haan"): the progress line, as before
    calm.lang.family = "hinglish"
    down, _ = asyncio.run(run_test(ClaimGuard(calm), frames_to_send=list(reply)))
    assert texts(down) == [PROGRESS_LINE + " "] and calm.asked_again == 0


def test_a_checkpoint_follows_each_reply_that_played_out_and_never_one_cut_off():
    """Plivo answers it with playedStream once the caller has heard the reply to its end."""
    po = Playout(CallTrace("t"))
    marks = lambda down: [f.message for f in down if isinstance(f, OutputTransportMessageFrame)]  # noqa: E731
    reply = [TTSStartedFrame(), *audio(), TTSStoppedFrame()]
    down, _ = asyncio.run(run_test(PlayoutMarks(po, "stream-1"), frames_to_send=reply))
    assert marks(down) == [{"event": "checkpoint", "streamId": "stream-1", "name": "r1"}]
    assert isinstance(down[-1], OutputTransportMessageFrame) and isinstance(down[-2], TTSStoppedFrame)  # behind it
    # a TTS run with no audio needs none; a reply cut off gets none (the clear takes the queue)
    down, _ = asyncio.run(run_test(PlayoutMarks(po, "stream-1"), frames_to_send=[TTSStartedFrame(), TTSStoppedFrame()]))
    assert marks(down) == []
    down, _ = asyncio.run(run_test(PlayoutMarks(po, "stream-1"), frames_to_send=[
        TTSStartedFrame(), *audio(), SleepFrame(0.05), InterruptionFrame(), TTSStoppedFrame()]))
    assert marks(down) == []


def test_plivos_playback_answers_reach_the_call_and_everything_else_is_pipecats():
    """Pipecat's own serializer drops playedStream and clearedAudio; the call's Playout gets them, with the moments
    its checkpoints and clears actually went out."""
    trace = CallTrace("t")
    po = Playout(trace)
    ser = PlivoPlayoutSerializer(stream_id="stream-1", params=PlivoFrameSerializer.InputParams(auto_hang_up=False),
                                 playout=po)

    async def go():
        sent = await ser.serialize(OutputTransportMessageFrame(message={
            "event": "checkpoint", "streamId": "stream-1", "name": po.name()}))
        assert json.loads(sent) == {"event": "checkpoint", "streamId": "stream-1", "name": "r1"}
        assert await ser.deserialize(json.dumps({"event": "playedStream", "sequenceNumber": 3, "streamId": "stream-1",
                                                 "name": "r1"})) is None
        assert json.loads(await ser.serialize(InterruptionFrame()))["event"] == "clearAudio"
        assert await ser.deserialize('{"event": "clearedAudio", "sequenceNumber": 4, "streamId": "stream-1"}') is None
        dtmf = await ser.deserialize(json.dumps({"event": "dtmf", "dtmf": {"digit": "5"}}))
        assert isinstance(dtmf, InputDTMFFrame)  # the rest is Pipecat's, as before
    asyncio.run(go())
    assert [e["e"] for e in trace.events] == ["played", "audio_cleared"] and po.confirmed == 1


def test_a_reply_after_a_change_request_is_checked_against_the_tools_before_it_is_heard():
    """Held until the reply ends: a tool call after it, or no change asked, or a reply that only asks, plays; a claim of
    the unmade change is dropped and asked for again."""
    from pipecat.frames.frames import FunctionCallInProgressFrame

    claim = [LLMFullResponseStartFrame(), LLMTextFrame("English mein likh deti hoon. "),
             LLMTextFrame("Screen par aa gaya hoga. "), LLMFullResponseEndFrame(), SleepFrame(0.5)]

    def run(session, frames):
        down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=frames))
        return texts(down)

    s = FakeSession()
    s.guarded, s.asks_change, s.claims_change = True, True, True
    assert run(s, list(claim)) == [] and s.asked_again == 1 and "edit_document" in s.notes[-1]
    # its sentences were traced as said when they passed; dropped whole, they are unsaid
    assert [e for e in s.trace.events if e["e"] == "unsaid"]
    s = FakeSession()                                    # the tool came right after it: it plays
    s.guarded, s.asks_change, s.claims_change = True, True, True
    tool = FunctionCallInProgressFrame(function_name="edit_document", tool_call_id="c1", arguments={})
    frames = [*claim[:4], tool, SleepFrame(0.5)]
    assert run(s, frames) == ["English mein likh deti hoon. ", "Screen par aa gaya hoga. "] and s.asked_again == 0
    s = FakeSession()                                    # the caller didn't ask for a change after all: it plays
    s.guarded, s.asks_change, s.claims_change = True, False, True
    assert len(run(s, list(claim))) == 2 and s.asked_again == 0
    s = FakeSession()                                    # it only says the change back and asks: it plays
    s.guarded, s.asks_change, s.claims_change = True, True, False
    asks = [LLMFullResponseStartFrame(), LLMTextFrame("Poori application English mein likh doon? "),
            LLMFullResponseEndFrame(), SleepFrame(0.5)]
    assert run(s, asks) == ["Poori application English mein likh doon? "] and s.asked_again == 0


def test_the_talker_fails_over_on_any_error_once_and_stays():
    """Pipecat's own failover moves only when a service can no longer be used; a rate-limited Sarvam stays "usable"
    while the caller hears nothing. Any error from the active LLM moves the call to the fallback, once."""
    from pipecat.frames.frames import ErrorFrame
    from pipecat.processors.frame_processor import FrameProcessor

    from apps.talker.processors import FailoverOnError

    async def go():
        primary, backup, other = FrameProcessor(name="sarvam"), FrameProcessor(name="qwen"), FrameProcessor()
        strategy = FailoverOnError([primary, backup])
        switched = []

        @strategy.event_handler("on_service_switched")
        async def on_switched(_s, service):
            switched.append(service.name)
        assert await strategy.handle_error(ErrorFrame(error="TTS hiccup", processor=other)) is None  # not the LLM's
        assert await strategy.handle_error(ErrorFrame(error="429 Too Many Requests", processor=primary)) is backup
        await asyncio.sleep(0.05)  # event handlers run as tasks
        assert strategy.active_service is backup and switched == ["qwen"]
        assert await strategy.handle_error(ErrorFrame(error="timeout", processor=backup)) is None  # nowhere left
        assert strategy.active_service is backup
    asyncio.run(go())


def test_the_change_a_caller_approved_is_made_by_the_session_when_the_model_doesnt_call_the_tool():
    """Now the session makes it and the reply is asked for again with that in [STATE]; with no retry left this turn, a
    fixed "I'm making the change now" instead of silence; and a tool call from the model leaves it to the model."""
    from pipecat.frames.frames import FunctionCallInProgressFrame

    from apps.talker.processors import MADE_NOTE

    claim = [LLMFullResponseStartFrame(), LLMTextFrame("The document is updated now, Saurabh. "),
             LLMTextFrame("You can see the new subject on your screen. "), LLMFullResponseEndFrame(), SleepFrame(0.5)]

    def run(session, frames):
        down, _ = asyncio.run(run_test(ClaimGuard(session), frames_to_send=frames))
        return texts(down)

    s = FakeSession()
    s.guarded, s.asks_change, s.claims_change, s.approved_unmade = True, True, True, True
    assert run(s, list(claim)) == [] and s.changes_made == 1 and s.notes == [MADE_NOTE] and s.said_changing == 0
    s = FakeSession()
    s.guarded, s.asks_change, s.claims_change, s.approved_unmade, s.retries_left = True, True, True, True, 0
    assert run(s, list(claim)) == [] and s.changes_made == 1 and s.said_changing == 1 and s.said_again == 0
    s = FakeSession()
    s.guarded, s.asks_change, s.claims_change, s.approved_unmade = True, True, True, True
    tool = FunctionCallInProgressFrame(function_name="edit_document", tool_call_id="c1", arguments={})
    assert len(run(s, [*claim[:4], tool, SleepFrame(0.5)])) == 2 and s.changes_made == 0


def test_a_drop_at_the_gate_is_about_the_replies_it_held_not_one_made_since():
    """Each reply's frames carry its number (ClaimGuard stamps it)."""
    from pipecat.frames.frames import InterruptionFrame

    def start(n):
        f = LLMFullResponseStartFrame()
        f.metadata["boldoc_reply"] = n
        return f

    s = FakeSession(silent_for=0.0)  # the caller is talking.
    asyncio.run(run_test(PlaybackGate(s), frames_to_send=[start(2), *audio(), InterruptionFrame()]))
    assert s.forgot_replies == [2]
    s = FakeSession(silent_for=0.0)  # two replies held when the drop comes: both, and only them
    asyncio.run(run_test(PlaybackGate(s), frames_to_send=[start(4), *audio(), start(5), *audio(), InterruptionFrame()]))
    assert s.forgot_replies == [4, 5]
    s = FakeSession()
    down, _ = asyncio.run(run_test(ClaimGuard(s), frames_to_send=[LLMFullResponseStartFrame(), LLMTextFrame("Ji. "),
                                                                  LLMFullResponseEndFrame()]))
    assert any(isinstance(f, LLMFullResponseStartFrame) and "boldoc_reply" in f.metadata for f in down)


def test_a_goodbye_waiting_to_play_is_not_dropped_by_the_callers_last_words():
    """While it's on its way (playing or held), the caller's words start no turn; otherwise they do, as always."""
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.turns.types import ProcessFrameResult

    from apps.talker.processors import CallerTurnStart

    async def go(on_its_way):
        s = CallerTurnStart(hold=lambda: on_its_way)
        started = []

        async def start():
            started.append(1)
        s.trigger_user_turn_started = start
        s._bot_speaking = False  # held at the gate: BolDoc isn't speaking yet
        r = await s._handle_transcription(TranscriptionFrame("बस इतना ही", "u", "t"))
        return r, started
    assert asyncio.run(go(True)) == (ProcessFrameResult.CONTINUE, [])
    assert asyncio.run(go(False)) == (ProcessFrameResult.STOP, [1])
