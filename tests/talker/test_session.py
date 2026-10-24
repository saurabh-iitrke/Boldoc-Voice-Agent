"""Talker <-> control plane glue: tool payloads, guards, the [STATE] note, and announcing finished work."""
import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("pipecat")  # the talker's session runs inside its Pipecat pipeline

from apps.talker.logic.claims import split_sentences
from apps.talker.session import CallSession, normalize_formats, transcript_of

SNAP = {"session_id": "s", "epoch": 1, "state": "converse", "verified_recipients": ["+919988776655"], "docs": [],
        "tasks": [], "question": None, "last_seq": 0}


class FakeControl:
    def __init__(self, fail=False, admission=None):
        self.fail, self.commands, self.ended, self.calls_off = fail, [], False, []
        self.snapshot = dict(SNAP)
        self.queue = asyncio.Queue()
        self.admission = admission or {"max_call_s": 1800, "first_call": False, "test": False,
                                       "caller": "+919988776655"}
        self.opened = None
        self.files, self.files_used = [], []  # files waiting for the caller's next document; the ones a create took

    async def open(self, session_id, channel, test=False, late_admit=None):
        if self.fail:
            raise ConnectionError("down")
        self.opened = {"session_id": session_id, "test": test, "late_admit": late_admit}
        return {"epoch": 1, "snapshot": self.snapshot, "admission": self.admission, "files": self.files}

    async def command(self, payload):
        self.commands.append(payload)
        if payload["cmd"] == "answer":  # the writer takes the reply: no open question while it reads it
            self.snapshot = dict(self.snapshot, question=None)
        if payload["cmd"] == "create_doc":
            self.snapshot = dict(self.snapshot, docs=[{"id": "doc_1", "title": "", "head_version": 0,
                                                       "formats": payload["formats"], "deliveries": []}],
                                 tasks=[{"id": "task_1", "kind": "create", "doc_id": "doc_1", "state": "queued",
                                         "created_at": 0}])
        return {"status": "accepted", "eta_s": 15, "snapshot": self.snapshot,
                **({"files_used": self.files_used} if payload["cmd"] == "create_doc" else {})}

    async def events(self, session_id, after, wait=20.0):
        evs, snap = await self.queue.get()
        return {"events": evs, "snapshot": snap}

    async def callbacks_off(self, session_id, words):
        self.calls_off.append(words)

    async def end(self, session_id, lang=None):
        self.ended, self.ended_lang = True, lang

    async def trace(self, session_id, payload):
        self.traced = payload

    async def aclose(self):
        pass


def params(args, messages=(), tool_call_id="call_1"):
    out = {}

    async def cb(result, **kw):
        out["result"], out["properties"] = result, kw.get("properties")
    ctx = SimpleNamespace(get_messages=lambda: list(messages))
    return SimpleNamespace(arguments=args, tool_call_id=tool_call_id, context=ctx, result_callback=cb), out


MESSAGES = [{"role": "assistant", "content": "Namaste!"}, {"role": "user", "content": "leave application chahiye"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "x"}]}, {"role": "tool", "content": "{}"},
            {"role": "user", "content": [{"type": "text", "text": "PDF aur Word"}]}]


def test_helpers():
    assert transcript_of(MESSAGES) == "BolDoc: Namaste!\nCaller: leave application chahiye\nCaller: PDF aur Word"
    assert normalize_formats(["PDF", "word", "Excel", "pdf", "fax"]) == ["pdf", "docx", "xlsx"]
    assert normalize_formats("pptx") == ["pptx"] and normalize_formats(None) == []


def test_create_then_guard_then_edit_payloads():
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        s.last_bot_line, s.last_caller_text = "Toh: 25 se 27 October ki chhutti. Likh doon?", "haan likh dijiye"
        s.answered_line = s.last_bot_line  # the line their turn answers
        p, out = params({"summary": "Leave 25-27 Oct", "formats": ["PDF", "Word"], "language": "hi-IN"}, MESSAGES)
        await s.create_document(p)
        cmd = ctl.commands[0]
        assert cmd["formats"] == ["pdf", "docx"] and cmd["doc_lang"] == "hi-IN" and cmd["epoch"] == 1
        assert cmd["turn_id"] == "call_1" and "Caller: PDF aur Word" in cmd["transcript"]
        assert out["result"]["status"] == "started" and out["result"]["done"] is False
        assert "ending 6655" in out["result"]["sms"]
        # what the answer tells BolDoc to do is never read out; the facts it gives (the SMS note) may be said
        assert s.echoes_instruction("Tell the caller you are writing it now and about how long it takes.")
        assert not any(s.echoes_instruction(x) for x in split_sentences(out["result"]["sms"] + " ")[0])
        # the LLM calls create again while the first is still being written: no second document
        p2, out2 = params({"summary": "again", "formats": ["pdf"], "language": "en-IN"}, tool_call_id="call_2")
        await s.create_document(p2)
        assert len(ctl.commands) == 1 and out2["result"]["status"] == "already_writing"
        # an edit during drafting goes to that document, based on what exists
        s.last_bot_line, s.last_caller_text = "Toh 26 se 27 October kar doon?", "haan"
        s.answered_line = s.last_bot_line  # the line their turn answers
        p3, out3 = params({"instruction": "26 se 27 kar do"}, MESSAGES, tool_call_id="call_3")
        await s.edit_document(p3)
        assert ctl.commands[1]["doc_id"] == "doc_1" and ctl.commands[1]["base_version"] == 0
        assert out3["result"]["status"] == "will_be_included_in_the_draft"
        state = s.state_text()
        assert "work in progress" in state and "still being written" in state
        await s.end()
        assert ctl.ended
        kinds = [e["e"] for e in ctl.traced["events"]]
        tools = [e.get("status") for e in ctl.traced["events"] if e["e"] == "tool"]
        assert kinds[0] == "session" and "text" not in str(ctl.traced["events"])
        assert tools == ["accepted", "already_writing", "accepted"]  # the guard's answer is traced too
    asyncio.run(go())


def test_a_reply_dropped_before_it_played_was_never_said():
    """a held reply dropped because the caller kept talking stayed "said" (the next question was
    then dropped as a repeat: 38 s of silence) and stayed BolDoc's last line (a yes to it wrote the letter)."""
    s = CallSession("s", None, "pstn", FakeControl())
    heard, dropped = "Toh 25 ki jagah 26 October kar doon?", "26 October se 27 October tak kar doon?"
    s.reply_started()
    s.remember_said(heard)
    s.on_bot_line(heard)
    s.reply_heard()
    s.reply_started()
    s.remember_said(dropped)
    s.on_bot_line(dropped)
    again = "Toh 26 October se 27 October tak ki chhutti kar doon?"
    assert s.said_before(again) and s.last_bot_line == dropped
    assert s.forget_unheard() == ["26 october se 27 october tak kar doon"]
    assert not s.said_before(again) and s.said_before(heard) and s.last_bot_line == heard
    s.on_bot_line(dropped)  # the dropped reply's end, arriving after the drop, changes nothing
    assert s.last_bot_line == heard


def test_the_line_peak_silero_hands_back_as_numpy_never_breaks_boldoc_stopping(monkeypatch):
    """Silero's confidence is a numpy array, round() of it raised in on_bot_stopped
    before BolDoc was marked quiet, so every later caller turn was taken as talking over her: no turn all call."""
    import numpy as np
    from pipecat.audio.vad.silero import SileroVADAnalyzer

    from apps.talker.bot import OnsetVAD
    monkeypatch.setattr(SileroVADAnalyzer, "voice_confidence", lambda self, buf: np.array([0.83], dtype=np.float32))
    vad = object.__new__(OnsetVAD)  # the peak bookkeeping only: no model
    vad._prev_volume = np.float64(0.41)
    vad.voice_confidence(b"")
    conf, vol = vad.take_peak()
    assert type(conf) is float and type(vol) is float and round(conf, 2) == 0.83 and vol == 0.41
    assert vad.take_peak() == (0.0, 0.0)
    s = CallSession("s", None, "pstn", FakeControl())
    s.vad_peak = lambda: (np.float32(0.5), np.float64(0.2))  # numpy scalars are rounded as they are
    s.on_bot_started()
    s.on_bot_stopped()
    assert not s.bot_speaking
    assert [(e["conf"], e["vol"]) for e in s.trace.events if e["e"] == "caller_peak"] == [(0.5, 0.2)]


def test_an_unclear_answer_is_asked_again_and_an_offer_is_no_read_back():
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        ctl.snapshot = s.snapshot = dict(s.snapshot, docs=[{"id": "doc_1", "title": "Leave Application",
                                                            "head_version": 1, "formats": ["pdf"], "deliveries": []}])
        # a phone line's "haan, kar dijiye" : not a yes, so asked again in other words
        s.last_bot_line, s.last_caller_text = "Toh 25 ki jagah 26 October kar doon?", "How Give me the car."
        s.answered_line = s.last_bot_line  # the line their turn answers
        p, out = params({"instruction": "26 se 27 October"}, MESSAGES)
        await s.edit_document(p)
        assert out["result"]["status"] == "confirm_first" and "in other words" in out["result"]["say"]
        assert not ctl.commands
        s.last_caller_text = "हाँ जी, कर दीजिए।"
        await s.edit_document(params({"instruction": "26 se 27 October"}, MESSAGES, tool_call_id="call_2")[0])
        assert [c["cmd"] for c in ctl.commands] == ["edit_doc"]
        # this call's document named in "…ready hai. Koi aur document chahiye?" is no read-back of a change
        s.last_bot_line = "Saurabh ji, leave application ready hai. Koi aur document chahiye?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.last_caller_text, s.acted_since_turn = "छुट्टी 26 से 27 October तक कर दीजिए।", False
        p, out = params({"instruction": "26 se 27 October"}, MESSAGES, tool_call_id="call_2b")
        await s.edit_document(p)
        assert out["result"]["status"] == "confirm_first" and [c["cmd"] for c in ctl.commands] == ["edit_doc"]
        # "…koi aur document likh doon?" offers a new document: its yes writes nothing
        s.last_bot_line = "Leave Application ready hai. Kuch aur change karna hai ya koi aur document likh doon?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.last_caller_text, s.acted_since_turn = "Hello, लिख दीजिए।", False
        p, out = params({"summary": "Leave 25-27 Oct", "formats": ["pdf"]}, MESSAGES, tool_call_id="call_3")
        await s.create_document(p)
        assert out["result"]["status"] == "confirm_first" and "in other words" not in out["result"]["say"]
        assert [c["cmd"] for c in ctl.commands] == ["edit_doc"]
        await s.end()
    asyncio.run(go())


def test_a_waiting_change_outlives_the_line_that_asked_it_until_made_declined_or_left():
    """The change waits until it's made, the caller says no, or three of their turns pass; until then no claim of it
    passes and [STATE] says so."""
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        ctl.snapshot = s.snapshot = dict(s.snapshot, docs=[{"id": "doc_1", "title": "Leave Application",
                                                            "head_version": 1, "formats": ["pdf"], "deliveries": []}])
        s.last_bot_line, s.last_caller_text = "Leave Application ready hai.", "26 se 27 October tak kar dijiye"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.acted_since_turn = False
        await s.edit_document(params({"instruction": "dates 26 to 27 October"}, MESSAGES)[0])
        assert ctl.commands == [] and s.pending_change == "dates 26 to 27 October"
        # the question, asked again after an unclear answer: twice, word for word
        s.on_bot_line("सौरभ ji, 25 की जगह 26 October कर दूं?")
        s.last_caller_text = "How"
        assert s.may_ask_again("सौरभ ji, 25 की जगह 26 October कर दूं?")
        assert s.may_ask_again("सौरभ ji, 25 की जगह 26 October कर दूं?")
        assert not s.may_ask_again("सौरभ ji, 25 की जगह 26 October कर दूं?")
        # another line takes its place: the change still waits, a claim of it is unacted, and [STATE] says so
        s.on_bot_line("सौरभ ji, आपकी Leave Application ready है। कुछ और चाहिए या call end कर दूँ?")
        s.utterance.clear()
        s.on_transcript("Yes, please do it.")
        s.on_turn_end()
        s.acted_since_turn = False
        assert s.change_waiting() and s.unacted_change() and "still waits for their yes" in s.state_text()
        # made: nothing waits any more
        s.on_bot_line("Toh 25 ki jagah 26 October kar doon?")
        s.utterance.clear()
        s.on_transcript("हाँ जी, कर दीजिए।")
        s.on_turn_end()
        await s.edit_document(params({"instruction": "dates 26 to 27 October"}, MESSAGES)[0])
        assert [c["cmd"] for c in ctl.commands] == ["edit_doc"] and s.pending_change is None
        # a "no" settles it, and so do three turns on something else
        s._change_said_back("rent 5000")
        s.utterance.clear()
        s.on_transcript("nahi, rehne dijiye")
        s.on_turn_end()
        assert s.pending_change is None
        s._change_said_back("rent 5000")
        for said in ("ek aur document chahiye", "invoice banana hai", "Sharma Traders ke liye"):
            s.utterance.clear()
            s.on_transcript(said)
            s.on_turn_end()
        assert s.pending_change == "rent 5000"
        s.utterance.clear()
        s.on_transcript("das hazaar ka")
        s.on_turn_end()
        assert s.pending_change is None
        await s.end()
    asyncio.run(go())


def test_a_change_said_back_waits_for_a_yes_and_never_counts_as_made():
    """Never from a "Likh doon?" read-back: that dropped a true "ready hai" (reverted, 6124e64)."""
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        ctl.snapshot = s.snapshot = dict(s.snapshot, docs=[{"id": "doc_1", "title": "Leave Application",
                                                            "head_version": 1, "formats": ["pdf"], "deliveries": []}])
        s.last_bot_line = "Toh: 25 se 27 October ki chhutti, aapke naam se. Likh doon?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.last_caller_text, s.acted_since_turn = "How", False
        assert not s.change_waiting() and not s.unacted_change()  # "Leave Application ready hai" stays sayable
        # the model asked on its own (no tool call), and the reply was garbled: nothing is changed yet
        s.last_bot_line, s.last_caller_text = "सौरभ ji, 26 से 27 October कर दूँ?", "Give me the car."
        s.answered_line = s.last_bot_line  # the line their turn answers
        assert s.change_waiting() and s.unacted_change() and "nothing has changed" in s.state_text()
        # their "no": BolDoc's next line isn't the change question any more, so nothing lingers
        s.last_bot_line, s.last_caller_text = "Theek hai, waisa hi rehne deti hoon. Aur kuch?", "nahi"
        s.answered_line = s.last_bot_line  # the line their turn answers
        assert not s.change_waiting() and not s.unacted_change()
        # an offer of more is no change question either
        s.last_bot_line, s.last_caller_text = "Ho gaya. Kuch aur badal doon?", "nahi"
        s.answered_line = s.last_bot_line  # the line their turn answers
        assert not s.change_waiting()
        # asked again, and a clear yes: the change is sent; acted on, it's no longer waiting
        s.last_bot_line, s.last_caller_text = "Toh 25 ki jagah 26 October kar doon?", "हाँ जी, कर दीजिए।"
        s.answered_line = s.last_bot_line  # the line their turn answers
        await s.edit_document(params({"instruction": "26 se 27 October"}, MESSAGES)[0])
        assert [c["cmd"] for c in ctl.commands] == ["edit_doc"] and not s.unacted_change()
        await s.end()
    asyncio.run(go())


def test_a_reply_dropped_twice_is_followed_by_say_that_again_not_silence():
    async def go():
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        said = []

        async def speak(text, **kw):
            said.append(text)
        s.speak = speak
        s.llm_busy_since = 1.0  # another reply is on its way: it may well answer them
        await s.nothing_said()
        assert said == []
        s.llm_busy_since = None
        await s.nothing_said()
        await s.nothing_said()  # once per caller turn
        assert said == ["Sorry, could you say that again?"]
        s.on_turn_end()
        s.announcing = SimpleNamespace(type="call.idle_check", event_id="e1")  # an idle check: nobody to ask
        await s.nothing_said()
        assert len(said) == 1
    asyncio.run(go())


def test_nothing_is_written_until_the_request_makes_sense_and_every_needed_fact_is_given():
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        s._prepare_intake = lambda: None
        readback = "Toh: gaay ko shikayat application, kal ek litre kam doodh mila tha. Likh doon?"
        # 1. the request doesn't make sense as said: asked first, once
        s.sense = {"verdict": "check", "to": "the cow", "ask": "Gaay ko application? Ya doodh wale ko shikayat?"}
        s.intake = {"document": "complaint application", "allowed": True, "facts": []}
        assert "Gaay ko application? Ya doodh wale ko shikayat?" in s.state_text()
        s.last_bot_line, s.last_caller_text, s.acted_since_turn = readback, "haan likh dijiye", False
        s.answered_line = s.last_bot_line  # the line their turn answers
        p, out = params({"summary": "complaint", "formats": ["pdf"]}, MESSAGES)
        await s.create_document(p)
        assert out["result"]["status"] == "check_first" and not ctl.commands
        s.turns_heard += 1  # the caller answered the question: their answer stands, it isn't asked again
        assert s._sense_pending() is None and "Gaay ko application" not in s.state_text()
        # 2. a needed fact still missing: asked for, not written
        s.intake = {"document": "complaint to the milkman", "allowed": True, "facts": [
            {"label": "your name", "question": "Aapka naam kya likhoon?", "value": "", "required": True}]}
        p, out = params({"summary": "complaint", "formats": ["pdf"]}, MESSAGES, tool_call_id="call_2")
        await s.create_document(p)
        assert out["result"]["status"] == "ask_first" and "Aapka naam kya likhoon?" in out["result"]["say"]
        assert not ctl.commands
        # 3. a checklist still on its way is waited for: here it brings the name, and the yes writes it
        async def newer():
            await asyncio.sleep(0.05)
            s.intake = {"document": "complaint to the milkman", "allowed": True, "facts": [
                {"label": "your name", "question": "Aapka naam?", "value": "Piyush Yadav", "required": True}]}
        s._intake_task = asyncio.get_running_loop().create_task(newer())
        p, out = params({"summary": "complaint", "formats": ["pdf"]}, MESSAGES, tool_call_id="call_3")
        await s.create_document(p)
        assert out["result"]["status"] == "started" and [c["cmd"] for c in ctl.commands] == ["create_doc"]
        await s.end()
    asyncio.run(go())


def test_a_yes_to_a_complete_read_back_writes_without_waiting_for_the_next_checklist():
    """Gates on 3146bc9: every create turn waited 1.7-2.4 s for the checklist of the caller's "haan, likh dijiye"."""
    async def go():
        leave = {"document": "a leave application", "allowed": True, "facts": [
            {"label": "leave dates", "question": "Which dates?", "value": "25-27 October", "required": True}]}
        readback = "Toh: HR manager ko leave application, 25 se 27 October. Likh doon?"

        async def finished():  # the caller has finished (the turn judge isn't under test here)
            return True
        for said, waits in (("Haan, likh dijiye.", False), ("Haan, par PDF bhi chahiye?", True)):
            ctl = FakeControl()
            s = CallSession("s", "919988776655", "pstn", ctl)
            await s.start()
            s._prepare_intake, s.wait_caller_done = (lambda: None), finished
            s.intake = dict(leave)
            newer = asyncio.get_running_loop().create_task(asyncio.sleep(0.3))  # the next checklist, on its way
            s._intake_task = newer
            s.last_bot_line, s.last_caller_text, s.acted_since_turn = readback, said, False
            s.answered_line = s.last_bot_line  # the line their turn answers
            p, out = params({"summary": "leave", "formats": ["pdf"]}, MESSAGES)
            await s.create_document(p)
            assert newer.done() is waits
            assert out["result"]["status"] == ("confirm_first" if waits else "started")
            await s.end()
    asyncio.run(go())


def test_a_hindi_call_hears_the_writing_line_in_devanagari_and_the_model_is_told_not_to_repeat_it():
    """The voice reads Hindi in Latin letters wrong and the model writes nothing else, so in a Hindi/Hinglish call
    BolDoc says the writing line itself, in Devanagari; an English call keeps the model's own words."""
    async def go():
        leave = {"document": "a leave application", "allowed": True, "facts": [
            {"label": "leave dates", "question": "Which dates?", "value": "25-27 October", "required": True}]}

        async def finished():
            return True
        for family, screen in (("hinglish", False), ("hinglish", True), ("en-IN", False)):
            ctl = FakeControl()
            if screen:
                ctl.snapshot = dict(ctl.snapshot, on_screen=True)
            s = CallSession("s", "919988776655", "pstn", ctl)
            await s.start()
            spoken, runs = [], []

            async def speak(text, to_context=True, spoken=spoken):
                spoken.append((text, to_context))

            async def run_llm(runs=runs):
                runs.append(1)
            s.speak, s.run_llm = speak, run_llm
            s.lang.family = family
            s._prepare_intake, s.wait_caller_done = (lambda: None), finished
            s.intake = dict(leave)
            s.last_bot_line = s.answered_line = "Toh: HR ko leave application, 25 se 27 October. Likh doon?"
            s.last_caller_text, s.acted_since_turn = "Haan, likh dijiye.", False
            p, out = params({"summary": "leave", "formats": ["pdf"]}, MESSAGES)
            ctx = []  # the model's context: Pipecat has recorded the call (its result settles in place)
            p.context = SimpleNamespace(get_messages=lambda ctx=ctx: ctx, add_message=ctx.append)
            ctx += [{"role": "assistant", "tool_calls": [{"id": "call_1"}]},
                    {"role": "tool", "content": "IN_PROGRESS", "tool_call_id": "call_1"}]
            await s.create_document(p)
            res = out["result"]
            assert res["status"] == "started"
            if family == "en-IN":
                assert spoken == [] and "said" not in res and res["say"].startswith("Tell the caller")
                assert len(ctx) == 2  # the model says it in English, as before
            else:
                where = "screen पर दिखेगा" if screen else "link SMS से आ जाएगा"
                (line, to_context), = spoken
                assert line.startswith("जी, मैं अभी लिख रही हूँ। लगभग 15 second लगेंगे") and where in line
                assert res["said"] == line and "don't say any of that again" in res["say"]
                assert s.said_before(line) and s.convo[-1] == f"BolDoc: {line}"
                # in the context once, as BolDoc's own words right after the call; the model runs on the result
                assert line.endswith("तब तक कुछ और जोड़ना हो, तो बताइए।")  # the invitation, in her own words too
                assert not to_context and ctx[2:] == [{"role": "assistant", "content": line}]
                assert out["properties"].run_llm is False and runs == []  # the model isn't run on it
            await s.end()
    asyncio.run(go())


def test_the_writing_line_waits_for_the_call_to_be_in_the_context_and_never_lands_ahead_of_it():
    async def go():
        s = CallSession("s", None, "pstn", FakeControl())
        ctx = [{"role": "user", "content": "haan likh dijiye"}]
        context = SimpleNamespace(get_messages=lambda: ctx, add_message=ctx.append)
        p = SimpleNamespace(tool_call_id="c9", context=context)
        await s._in_context_after_call(p, "जी, मैं अभी लिख रही हूँ।", wait_s=0.1)
        assert len(ctx) == 1  # the call never showed up: the line is left out rather than put ahead of it

        async def recorded_later():
            await asyncio.sleep(0.05)
            ctx.extend([{"role": "assistant", "tool_calls": [{"id": "c9"}]},
                        {"role": "tool", "content": "IN_PROGRESS", "tool_call_id": "c9"}])
        later = asyncio.get_running_loop().create_task(recorded_later())
        await s._in_context_after_call(p, "जी, मैं अभी लिख रही हूँ।", wait_s=1.0)
        await later
        assert [m.get("role") for m in ctx] == ["user", "assistant", "tool", "assistant"]
    asyncio.run(go())


def test_the_writing_line_rounds_the_time_and_names_where_the_link_goes():
    s = CallSession("s", None, "pstn", FakeControl())
    s.lang.family = "hinglish"
    s.snapshot = dict(SNAP, verified_recipients=[])
    assert s.writing_line(13) == ("जी, मैं अभी लिख रही हूँ। लगभग 15 second लगेंगे। तब तक कुछ और जोड़ना हो, तो "
                                  "बताइए।")  # no SMS possible: none promised
    s.snapshot = dict(SNAP)
    assert s.writing_line(22).startswith("जी, मैं अभी लिख रही हूँ। लगभग 20 second लगेंगे, और link SMS से आ जाएगा।")
    assert s.writing_line(1).startswith("जी, मैं अभी लिख रही हूँ। लगभग 5 second")
    s.lang.family = "en-IN"
    assert s.writing_line(15) is None


def test_a_create_cancelled_while_its_checklist_is_awaited_is_put_off():
    """Gate on 1deae7e (doc2): the caller's next words cancelled create_document while it waited for the checklist; it
    left no trace and BolDoc said "likh rahi hoon" for 40 s with nothing written."""
    async def go():
        async def finished():
            return True
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        s._prepare_intake, s.wait_caller_done = (lambda: None), finished
        s.intake = {"document": "a leave application", "allowed": True, "facts": []}
        s.last_bot_line = "Toh: 25 se 27 October ki chhutti. Aur koi detail?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.last_caller_text = "English mein likhiye"
        loop = asyncio.get_running_loop()
        s._intake_task = loop.create_task(asyncio.sleep(5))  # not a yes to a read-back: the checklist is awaited
        p, out = params({"summary": "leave", "formats": ["pdf"]}, MESSAGES)
        call = loop.create_task(s.create_document(p))
        await asyncio.sleep(0.1)
        call.cancel()  # the caller's next words: Pipecat cancels the tool call
        with pytest.raises(asyncio.CancelledError):
            await call
        assert s.deferred_tool == "create_document" and not ctl.commands and "result" not in out
        # the checklist waited for is replaced by a newer one while the call goes on: it carries on with what there is
        s.deferred_tool, older = None, loop.create_task(asyncio.sleep(5))
        s._intake_task = older
        p, out = params({"summary": "leave", "formats": ["pdf"]}, MESSAGES, tool_call_id="call_2")
        call = loop.create_task(s.create_document(p))
        await asyncio.sleep(0.1)
        older.cancel()
        await call
        assert out["result"]["status"] == "confirm_first" and s.deferred_tool is None
        s._intake_task.cancel()
        await s.end()
    asyncio.run(go())


def test_new_words_from_the_caller_are_told_from_a_plain_yes():
    s = CallSession("s", None, "pstn", FakeControl())
    s.last_caller_text, s.acted_since_turn = "गाय का नाम सुखवंती है", False
    assert s.said_something_new()
    s.last_caller_text = "haan theek hai"
    assert not s.said_something_new()
    s.last_caller_text, s.acted_since_turn = "naam Sukhwanti hai", True  # a tool already acted on it
    assert not s.said_something_new()


def test_end_call_asked_again_ends_with_a_goodbye_not_silence():
    """told to say goodbye, the model called end_call 8 more times; 8 s of silence, then hang-up."""
    async def go():
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        await s.start()
        said = []

        async def speak(text, **kw):
            said.append(text)
        s.speak, s.goodbye_note = speak, "the caller wants nothing more: say a short goodbye"
        p, out = params({})
        await s.end_call_tool(p)
        assert out["result"]["status"] == "ending" and out["properties"] is None  # the model says the goodbye
        for i in range(3):  # ...but asks to end the call again, three times
            p, out = params({}, tool_call_id=f"again_{i}")
            await s.end_call_tool(p)
            assert out["result"]["status"] == "ending" and out["properties"].run_llm is False
        assert said == ["Thank you for calling BolDoc. Goodbye!"]  # once, in the call's language (English here)
        await s.end()
    asyncio.run(go())


def test_a_callback_opens_with_why_asks_and_thanks_then_hangs_up_when_nothing_is_left(monkeypatch):
    """BolDoc rang them about their document's questions. It says why, the questions come as the
    writer's questions do, and once control says nothing is left (callback.done) it thanks them and hangs up."""
    from apps.talker import session as session_mod
    from apps.talker.prompts import callback_opener
    monkeypatch.setattr(session_mod, "END_GRACE_S", 0.05)
    assert callback_opener("Saurabh Kumar", "project report", 2) == (
        "Namaste Saurabh ji, BolDoc bol rahi hoon. Aapke project report ke liye 2 baatein poochni thi. Abhi baat ho "
        "sakti hai?")
    assert callback_opener(None, "project report", 1, english=True) == (
        "Hello, this is BolDoc calling about your project report. I have 1 quick question for it. Is this a good "
        "time?")

    async def go():
        ctl = FakeControl(admission={"max_call_s": 300, "first_call": False, "test": False, "caller": None,
                                     "plan": "credits", "via": "callback",
                                     "callback": {"title": "project report", "n": 2}})
        s = CallSession("s", None, "pstn", ctl)
        spoken, ended = [], []

        async def speak(text, **kw):
            spoken.append(text)

        async def end_call():
            ended.append(1)
        s.speak, s.end_call = speak, end_call
        s.lang.family = "hinglish"
        await s.start()
        assert s.callback == {"title": "project report", "n": 2} and "This is a callback" in s.state_text()
        ev = {"session_id": "s", "epoch": 1, "seq": 4, "type": "callback.done", "doc_id": "doc_1", "payload": {}}
        await ctl.queue.put(([ev], dict(SNAP, last_seq=4)))
        for _ in range(40):
            await asyncio.sleep(0.05)
            if ended:
                break
        assert spoken == ["Shukriya! Aapka project report kuch minute mein SMS pe aa jayega. Namaste!"] and ended
        assert s.ending and any(e["e"] == "callback_done" for e in s.trace.events)
        for _ in range(2):  # "mujhe call mat karna": recorded once, however often they say it
            s.utterance, s.speech_s = ["Mujhe dobara call mat karna"], 1.5
            s.on_turn_end()
        await asyncio.sleep(0.05)
        assert ctl.calls_off == ["Mujhe dobara call mat karna"]
        await s.end()
    asyncio.run(go())


def test_finished_work_is_announced_in_a_quiet_moment_and_counted_only_once_heard():
    async def go():
        ctl = FakeControl()
        s = CallSession("s", None, "pstn", ctl)
        runs = []

        async def run_llm():
            runs.append(1)
        s.run_llm = run_llm
        s.arbiter.min_user_silence_s = 0.0
        await s.start()
        done_snap = dict(SNAP, docs=[{"id": "doc_1", "title": "Leave Application", "head_version": 1,
                                      "formats": ["pdf"], "deliveries": []}], last_seq=3)
        ev = {"session_id": "s", "epoch": 1, "seq": 3, "type": "task.completed", "doc_id": "doc_1",
              "head_version": 1, "payload": {"diff_summary": ["wrote the first draft"]}}
        await ctl.queue.put(([ev], done_snap))
        for _ in range(40):
            await asyncio.sleep(0.05)
            if runs:
                break
        assert runs and s.announcing is not None
        assert '"Leave Application" is ready' in s.state_text() and "say this now" in s.state_text()
        # caller barges in before hearing it -> not counted; it becomes a must-mention for the next reply
        s.on_bot_started()
        s.on_interruption()
        assert s.announcing is None and "must mention" in s.state_text()
        s.on_bot_started()
        s.on_bot_stopped()  # next reply played fully
        assert "must mention" not in s.state_text()
        await s.end()
    asyncio.run(go())


def test_side_effects_wait_until_the_caller_has_finished():
    async def go():
        ctl = FakeControl()
        s = CallSession("s", None, "pstn", ctl)
        await s.start()
        s.last_bot_line = "Toh: Acme ko leave application, 25 se 27 October. Likh doon?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.on_user_vad(True)
        s.on_user_vad(False)          # a pause...
        s.on_transcript("haan sahi hai English mein likhiye")

        async def keeps_talking():
            await asyncio.sleep(0.1)
            s.on_user_vad(True)       # ...and the caller carries on
        talker = asyncio.create_task(keeps_talking())
        p, out = params({"summary": "x", "formats": ["pdf"], "language": "en-IN"})
        await s.create_document(p)
        await talker
        assert out["result"]["status"] == "not_started" and ctl.commands == []
        assert out["properties"].run_llm is False   # no model run just to stay quiet; the caller's turn runs it
        assert "NOT STARTED" in s.state_text() and "call create_document now" in s.state_text()
        s.on_user_vad(False)
        s.last_user_speech_end -= 2.0  # now they have really finished
        s.last_caller_text = "haan sahi hai English mein likhiye"
        s._yes = (s.last_caller_text, "yes")  # a yes with more in it: the yes-check's verdict, by meaning
        p, out = params({"summary": "x", "formats": ["pdf"], "language": "en-IN"}, tool_call_id="c2")
        await s.create_document(p)
        assert out["result"]["status"] == "started" and len(ctl.commands) == 1
        assert "NOT STARTED" not in s.state_text()
        # a voice onset the VAD hasn't confirmed yet already counts as "not silent"
        import time as _t
        s.voice_at = _t.monotonic
        assert s.caller_silent_for() < 0.05
        await s.end()
    asyncio.run(go())


def test_control_plane_down_means_honest_conversation_only():
    async def go():
        s = CallSession("s", None, "pstn", FakeControl(fail=True))
        await s.start()
        assert s.epoch is None and "unavailable" in s.state_text()
        p, out = params({"summary": "x", "formats": ["pdf"], "language": "en-IN"})
        await s.create_document(p)
        assert out["result"]["status"] == "unavailable"
        await s.end()
    asyncio.run(go())


def test_plivo_extra_headers_real_format_and_admission_handoff():
    from apps.talker.plivo_stream import parse_extra_headers, ph_header, remember_admission, take_admission
    assert parse_extra_headers("{X-PH-caller: 919988776655, X-PH-resumed: 0}") == {"caller": "919988776655",
                                                                                  "resumed": "0"}
    assert parse_extra_headers("caller=919988776655,resumed=0")["caller"] == "919988776655"
    assert parse_extra_headers('{"caller": "919988776655"}') == {"caller": "919988776655"}
    assert parse_extra_headers(None) == {} and parse_extra_headers("") == {}
    remember_admission("uuid-1", {"decision": "admit", "first_call": True})
    assert take_admission("uuid-1")["admission"]["first_call"] is True
    assert take_admission("uuid-1") is None  # one stream start consumes it
    assert ph_header({"X-PH-Session": "tok"}, "session") == "tok"
    assert ph_header({"x-ph-session": "tok2"}, "Session") == "tok2"
    assert ph_header({"From": "x"}, "session") is None


def test_session_takes_identity_from_control_and_ends_when_refused():
    import httpx

    class Refusing(FakeControl):
        async def open(self, session_id, channel, test=False, late_admit=None):
            req = httpx.Request("POST", "http://control/internal/sessions")
            raise httpx.HTTPStatusError("403", request=req, response=httpx.Response(403, request=req))

    async def go():
        s = CallSession("s", None, "pstn", FakeControl())
        s.late_admit = {"call_uuid": "s", "from": "919988776655"}
        await s.start()
        assert s.caller == "+919988776655" and s.max_call_s == 1800
        assert s.client.opened["late_admit"]["from"] == "919988776655"
        await s.end()

        ended = []
        r = CallSession("r", None, "pstn", Refusing())

        async def end_call():
            ended.append(True)
        r.end_call = end_call
        await r.start()
        assert r.refused and ended == [True] and r.epoch is None
        await r.end()
    asyncio.run(go())


def test_time_limit_warns_then_says_goodbye_then_hangs_up(monkeypatch):
    from apps.talker import session as session_mod
    monkeypatch.setattr(session_mod, "TIME_WARNING_S", 0.2)
    monkeypatch.setattr(session_mod, "GOODBYE_GRACE_S", 0.3)

    async def go():
        s = CallSession("s", None, "pstn", FakeControl(admission={"max_call_s": 0.4, "caller": None}))
        ended = []

        async def end_call():
            ended.append(time_now())
        s.end_call = end_call
        t0 = time_now()
        await s.start()
        await asyncio.sleep(1.0)
        offered = [p.event.type for p in s.arbiter._pending.values()]
        stages = [e["stage"] for e in s.trace.events if e["e"] == "time_limit"]
        assert stages == ["call.time_warning", "call.time_up"], stages
        assert ended and 0.6 <= ended[0] - t0 <= 0.85  # limit (0.4 s) + grace (0.3 s): nobody said goodbye
        await s.end()
        return offered
    import time as _t
    time_now = _t.monotonic
    asyncio.run(go())


def test_turn_verdicts_rules_then_classifier(monkeypatch):
    from apps.talker import session as session_mod

    async def go():
        s = CallSession("s", None, "pstn", FakeControl())
        s.api_key = "k"
        s.speech_s = 3.0
        s.utterance = ["मेरा नाम सौरभ है और मैं"]
        s.on_turn_end()
        assert s.verdict == "incomplete" and s.required_gap() == 3.5   # word rule, instant: room to think
        s.utterance, s.speech_s = ["चार"], 0.5
        s.on_turn_end()
        assert s.verdict == "complete" and s.required_gap() <= 0.5     # short answers are answers

        async def fake_classify(client, key, last, text):
            await asyncio.sleep(0.01)
            return "incomplete"
        s.utterance, s.speech_s = ["गोवा trip के खर्चे की Excel sheet बनानी है।"], 3.0
        s.on_turn_end()
        assert s.verdict == "complete"                                 # a punctuated sentence is finished
        monkeypatch.setattr(session_mod, "classify", fake_classify)
        s.utterance, s.speech_s = ["गोवा trip के expense Excel sheet में flight"], 3.8
        s.on_turn_end()
        assert s.verdict == "incomplete" and s.required_gap() == 3.5   # the amount is still to come: words decide
        s.utterance, s.speech_s = ["हम चार लोग Goa जा रहे हैं फिर"], 3.8
        s.on_turn_end()
        assert s.verdict is None                                       # still judging: playback waits a moment
        await s._judge_task
        assert s.verdict == "incomplete" and s.required_gap() == 2.0   # the classifier alone: the old 2.0 s
        assert [e["verdict"] for e in s.trace.events if e["e"] == "turn_verdict"] == [
            "incomplete", "complete", "complete", "incomplete", "incomplete"]
        s.on_bot_started()
        assert s.verdict is None and s.utterance == []
        await s.end()
    asyncio.run(go())


def test_the_end_of_turn_model_judges_when_the_words_dont_decide(monkeypatch):
    """LiveKit's model (eou.py) decides what the word rules leave open, before the classifier; an unfinished
    sentence it finds holds BolDoc's reply 3.5 s, and a finished one doesn't wait."""
    from apps.talker import eou
    from apps.talker import session as session_mod

    calls = []

    async def fake_unfinished(family, bot_line, text):
        calls.append((family, text))
        return ("फिर" in text, 0.01 if "फिर" in text else 0.6)

    async def never(*a, **k):
        raise AssertionError("the classifier isn't asked when the model answers")
    monkeypatch.setattr(eou, "unfinished", fake_unfinished)
    monkeypatch.setattr(eou, "ready", lambda: True)
    monkeypatch.setattr(session_mod, "classify", never)

    async def go():
        s = CallSession("s", None, "pstn", FakeControl())
        s.api_key = ""  # overflow calls have no classifier: the model still judges
        s.lang.family = "hinglish"
        s.utterance, s.speech_s = ["हम चार लोग Goa जा रहे हैं फिर"], 2.4
        s.on_turn_end()
        await s._judge_task
        assert (s.verdict, s.verdict_source, s.required_gap()) == ("incomplete", "eou", 3.5)
        s.utterance, s.speech_s = ["मुझे loan लेना है"], 1.6
        s.on_turn_end()
        await s._judge_task
        assert s.verdict == "complete" and s.required_gap() <= 0.6
        assert [e.get("p") for e in s.trace.events if e["e"] == "turn_verdict"] == [0.01, 0.6]
        assert calls == [("hinglish", "हम चार लोग Goa जा रहे हैं फिर"), ("hinglish", "मुझे loan लेना है")]
        await s.end()
    asyncio.run(go())


def test_a_tool_called_again_instead_of_asking_makes_boldoc_ask_itself():
    """The second identical "not yet" stops the model and BolDoc asks."""
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        spoken = []

        async def speak(text, **kw):
            spoken.append(text)
        s.speak = speak
        s.lang.family = "hinglish"
        s.last_bot_line = "तो: गौशाला के manager को application, गाय के इलाज के लिए। लिख दूँ?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.last_caller_text = "Hello"
        outs = []
        for n in range(4):
            p, out = params({"summary": "application", "formats": ["pdf"]}, MESSAGES, tool_call_id=f"c{n}")
            await s.create_document(p)
            outs.append(out)
        assert [o["result"]["status"] for o in outs] == ["confirm_first"] * 4 and not ctl.commands
        assert outs[0]["properties"] is None                               # the first: the model asks
        assert all(o["properties"].run_llm is False for o in outs[1:])     # then it isn't run again
        assert spoken == ["Maaf kijiye, theek se sunai nahi diya. Kya main ise likh doon, haan ya nahi?"]  # asked once
        assert sum(1 for e in s.trace.events if e["e"] == "tool_loop_stopped") == 3
        s.last_caller_text = "हाँ जी, लिख दीजिए।"                           # their answer: no loop any more
        p, out = params({"summary": "application", "formats": ["pdf"]}, MESSAGES, tool_call_id="c9")
        await s.create_document(p)
        assert out["result"]["status"] != "confirm_first"
        await s.end()
    asyncio.run(go())


def test_holds_are_long_only_where_the_words_are_clearly_unfinished():
    """A short phrase keeps the old 2.0 s; the rules and longer utterances get room to think; a lone greeting waits 1.0
    s for the request that follows it."""
    async def go():
        s = CallSession("s", None, "pstn", FakeControl())
        s.speech_s = 1.5
        cases = [("incomplete", "rules", "मेरा account number है", 3.5),
                 ("incomplete", "eou", "बैंक मैनेजर के लिए", 2.0),
                 ("incomplete", "eou", "मुझे अपने HR manager के लिए एक", 3.5),
                 ("incomplete", "classifier", "मुझे अपने HR manager के लिए एक", 2.0),
                 ("complete", "rules", "नमस्ते", 1.0),
                 ("complete", "rules", "मुझे loan लेना है", 0.6)]
        for verdict, source, text, gap in cases:
            s.verdict, s.verdict_source, s.last_caller_text = verdict, source, text
            assert s.required_gap() == gap, (source, text)
        await s.end()
    asyncio.run(go())


def test_hmm_once_per_pause_in_an_unfinished_sentence():
    """BolDoc says हम्म after 1.2 s of silence, once, and never over anyone."""
    async def go():
        s = CallSession("s", None, "pstn", FakeControl())
        s.ack_pcm = b"\x00\x01" * 800
        s.verdict, s.verdict_source = "incomplete", "eou"
        assert not s.wants_ack(1.0)                 # too soon: people pause that long mid-sentence
        assert s.wants_ack(1.3)
        s.ack_started(1.3)
        assert s.ack_playing and not s.wants_ack(2.0)  # once
        s.ack_done()
        assert not s.wants_ack(2.5)                 # and not again in the same few seconds
        s.last_ack_at -= 10
        s.verdict = "complete"
        assert not s.wants_ack(2.0)                 # a finished sentence gets an answer, not a हम्म
        s.verdict, s.ack_pcm = "incomplete", None
        assert not s.wants_ack(2.0)                 # no clip for this voice: the hold stays silent
        assert [e["e"] for e in s.trace.events].count("ack") == 1
        await s.end()
    asyncio.run(go())


def test_caller_language_reaches_the_state_note():
    async def go():
        s = CallSession("s", None, "pstn", FakeControl())
        await s.start()
        s.on_transcript("எனக்கு ஒரு விடுப்பு கடிதம் வேண்டும்")
        assert s.lang.family == "ta-IN" and "Reply in Tamil" in s.state_text()
        assert any(e["e"] == "lang_switch" for e in s.trace.events)
        await s.end()
    asyncio.run(go())


def test_earlier_documents_are_offered_and_matched_by_title():
    from apps.talker.session import match_document

    docs = [{"id": "d1", "title": "Leave Application", "head_version": 2},
            {"id": "d2", "title": "GST Invoice for Sharma Traders", "head_version": 1}]
    assert match_document("leave application", docs)["id"] == "d1"
    assert match_document("Sharma Traders invoice", docs)["id"] == "d2"
    assert match_document("rent agreement", docs) is None

    async def go():
        s = CallSession("s", None, "pstn", FakeControl())
        await s.start()
        s.recent_docs = [*docs, {"id": "d0", "title": "Leave Application", "head_version": 1}]
        note = s.state_text()
        assert note.count('"Leave Application"') == 1 and '"GST Invoice for Sharma Traders"' in note  # each title once
        assert "NEW document: create_document" in note and "Never read" in note
        args = {"instruction": "date 28 kar do", "document": "leave application"}
        s.last_bot_line = "Pichhli Leave Application mein date 28 kar doon?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.last_caller_text = "haan, pichhli wali mein"  # the caller means the earlier one, and said yes
        s._yes = (s.last_caller_text, "yes")  # a yes with more in it: the yes-check's verdict, by meaning
        sent = {}

        async def cb(r):
            sent.update(r)
        params = SimpleNamespace(arguments=args, tool_call_id="t1", result_callback=cb,
                                 context=SimpleNamespace(get_messages=lambda: []))
        s.wait_caller_done = lambda *a, **k: asyncio.sleep(0, result=True)
        await s.edit_document(params)
        cmd = s.client.commands[-1]
        assert (cmd["doc_id"], cmd["base_version"]) == ("d1", 2)
        # two earlier documents and no title: ask which one
        sent.clear()
        params.arguments = {"instruction": "change the date"}
        await s.edit_document(params)
        assert sent["status"] == "which_document" and len(sent["earlier_documents"]) == 2
        # even with ONE earlier document, an unnamed change is never guessed onto it (the gate's wrong edit)
        sent.clear()
        s.recent_docs = docs[:1]
        await s.edit_document(params)
        assert sent["status"] == "which_document" and "create_document" in sent["say"]
        assert len(s.client.commands) == 1
        await s.end()
    asyncio.run(go())


def test_the_state_says_when_nothing_is_being_written():
    from apps.talker.logic.state_block import build_state_block

    empty = {"docs": [], "tasks": [], "verified_recipients": ["+919988776655"]}
    assert "none yet. Nothing is being written until you call create_document" in build_state_block(empty, "converse")
    one = {**empty, "docs": [{"id": "doc_1", "title": "Leave Application", "head_version": 1, "formats": ["pdf"]}]}
    assert "none yet" not in build_state_block(one, "converse")


def test_a_short_answer_ending_on_the_questions_own_word_is_complete():
    """'Bank Manager के लिए' after 'यह letter किसके लिए है?' was held 1.6 s as if the caller had stopped mid-phrase."""
    from apps.talker.turn_judge import by_rules
    asked = "ठीक है, मैं letter लिख देती हूँ। यह letter किसके लिए है?"
    assert by_rules("Bank Manager के लिए", 1.0, asked) == "complete"
    assert by_rules("Bank Manager के लिए", 1.4, "") is None                    # no question: the classifier decides
    assert by_rules("kal se", 0.8, "Theek hai. Chhutti kab se chahiye?") == "complete"
    assert by_rules("mera naam Saurabh aur", 2.0, "Aapka naam kya hai?") == "incomplete"
    # the question in Latin script, the answer in Devanagari
    assert by_rules("Bank Manager के लिए", 1.0, "Ji, main likh deti hoon. Yeh letter kis liye hai?") == "complete"
    assert by_rules("SBI में", 0.7, "Account kis bank mein hai?") == "complete"


def test_the_welcome_fits_the_caller():
    from apps.talker.prompts import welcome
    first = welcome(True, "Saurabh", "Leave Application")  # calls start in English
    assert "AI assistant" in first and "thirty days" in first and "Saurabh" not in first   # notice + disclosure
    assert welcome(False).startswith("Hello! I'm BolDoc") and "welcome back" in welcome(False).lower()
    back = welcome(False, "Saurabh", "Leave Application")
    assert back.startswith("Hello Saurabh!") and "change your Leave Application" in back
    hindi = welcome(True, english=False)  # Hindi picked on the website
    assert "AI assistant" in hindi and "tees din" in hindi
    back_hi = welcome(False, "Saurabh", "Leave Application", english=False)
    assert back_hi.startswith("Namaste Saurabh ji!") and "Pichhli baar ki Leave Application" in back_hi

    async def go():
        from datetime import datetime, timedelta, timezone
        s = CallSession("s", None, "pstn", FakeControl())
        await s.start()
        now = datetime.now(timezone.utc)
        s.recent_docs = [{"id": "d1", "title": "Rent Receipt", "updated_at": (now - timedelta(days=3)).isoformat()}]
        assert s.last_document_title() == "Rent Receipt"
        s.recent_docs[0]["updated_at"] = (now - timedelta(days=45)).isoformat()
        assert s.last_document_title() is None          # too old to offer in the welcome
        await s.end()
    asyncio.run(go())


def test_a_short_phrase_ending_on_a_connector_goes_to_the_classifier():
    from apps.talker.turn_judge import by_rules
    assert by_rules("Bank Manager के लिए", 1.0, "Aapko kis baare mein letter likhna hai?") is None   # it decides
    assert by_rules("main kal subah das baje se", 2.2, "Kab se chahiye?") == "complete"           # answers "se"
    assert by_rules("mera naam Saurabh Kumar hai aur", 2.4, "Aapka naam?") == "incomplete"      # long, open


def test_the_call_ends_after_a_goodbye_and_a_quiet_caller_is_checked_on_first(monkeypatch):
    """A real call stayed open after the caller said "nothing is needed"."""
    from apps.talker import session as sm
    from apps.talker.session import IDLE_BYE_S, IDLE_CHECK_S

    async def done(*a):
        return True
    monkeypatch.setattr(sm, "caller_is_done", done)

    async def go():
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        s.api_key = "k"
        await s.start()
        hung = []

        async def hang_up():
            hung.append(1)
        s.end_call = hang_up
        p, out = params({})
        s.last_caller_text = "nahi, bas, thank you"  # the caller said they are done
        s.snapshot = {**s.snapshot, "tasks": [{"id": "t1", "kind": "create", "state": "running"}]}
        await s.end_call_tool(p)
        assert out["result"]["status"] == "not_now" and not s.ending      # never while a document is written
        s.snapshot = {**s.snapshot, "tasks": []}
        p, out = params({})
        await s.end_call_tool(p)
        assert out["result"]["status"] == "ending" and s.ending
        s.on_bot_started()
        s.on_bot_stopped()                                                 # the goodbye has been heard
        await asyncio.sleep(0.8)
        assert hung == [1]
        # idle: quiet after the work, asked once, then a goodbye
        t = CallSession("t", "919988776655", "pstn", FakeControl())
        await t.start()
        t.turns_heard, t.last_user_speech_end, t.last_bot_stop = 1, 100.0, 100.0
        assert t._idle_notice(100.0 + IDLE_CHECK_S - 1) is None
        assert t._idle_notice(100.0 + IDLE_CHECK_S + 1) == "call.idle_check"
        t._idle_stage = 1
        assert t._idle_notice(100.0 + IDLE_BYE_S + 1) == "call.idle_goodbye"
        t.on_turn_end()                                                    # the caller spoke: start over
        assert t._idle_stage == 0
        await s.end()
        await t.end()
    asyncio.run(go())


def test_a_question_nobody_answers_is_asked_again_after_a_few_seconds():
    """After "Application mein aapka naam kya likhun?" the line was silent for 12 s, then BolDoc asked "koi aur cheez
    chahiye?" as if the caller were done."""
    from apps.talker.logic.state_block import describe_event
    from apps.talker.session import IDLE_CHECK_S, REASK_S
    from protocol.messages import Event
    s = CallSession("s", "919988776655", "pstn", FakeControl())
    s.turns_heard, s.last_user_speech_end, s.last_bot_stop = 1, 100.0, 100.0
    s.last_bot_line = "Samajh gayi, SBM Bank se paanch crore ka loan. Application mein aapka naam kya likhun?"
    s.answered_line = s.last_bot_line  # the line their turn answers
    assert s._idle_notice(100.0 + REASK_S - 0.5) is None
    assert s._idle_notice(100.0 + REASK_S + 0.5) == "call.reask"                 # sooner, and the same question
    s.last_bot_line = "Theek hai, main likh deti hoon."
    s.answered_line = s.last_bot_line  # the line their turn answers
    assert s._idle_notice(100.0 + REASK_S + 0.5) is None                         # no question pending: wait longer
    assert s._idle_notice(100.0 + IDLE_CHECK_S + 0.5) == "call.idle_check"
    ev = lambda kind: Event(session_id="s", epoch=1, seq=0, type=kind)  # noqa: E731
    assert "still there" in describe_event(ev("call.idle_check"), {"docs": []})  # nothing written yet
    assert "anything else" in describe_event(ev("call.idle_check"), {"docs": [{"id": "d"}]})
    assert "again" in describe_event(ev("call.reask"), {})


def test_the_name_said_on_a_call_is_used_and_saved():
    async def go():
        ctl = FakeControl()
        saved = []

        async def save_name(session_id, name):
            saved.append(name)
        ctl.save_name = save_name
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        s.on_transcript("मेरा नाम है Saurabh")
        await asyncio.sleep(0.05)
        assert s.caller_name == "Saurabh" and saved == ["Saurabh"]
        assert "Saurabh ji" in s.state_text()
        await s.end()
    asyncio.run(go())



def test_the_reply_to_the_writers_question_goes_to_the_writer_as_said():
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        s.arbiter.min_user_silence_s = 0.0
        runs = []

        async def run_llm():
            runs.append(1)
        s.run_llm = run_llm
        await s.start()
        q = {"id": "q_1", "text": "What is your employee ID?", "default": "no answer", "task_id": "task_1"}
        writing = [{"id": "task_1", "kind": "create", "doc_id": "doc_1", "state": "awaiting_input", "created_at": 0}]
        doc = [{"id": "doc_1", "title": "", "head_version": 0, "formats": ["pdf"], "deliveries": []}]
        asking = dict(SNAP, docs=doc, tasks=writing, question=q, last_seq=4)
        ev = {"session_id": "s", "epoch": 1, "seq": 4, "type": "task.question", "task_id": "task_1",
              "doc_id": "doc_1", "payload": {"question_id": "q_1", "text": q["text"], "default": "no answer"}}
        ctl.snapshot = asking
        await ctl.queue.put(([ev], asking))
        for _ in range(40):
            await asyncio.sleep(0.05)
            if runs:
                break
        assert 'the document still needs a fact; ask the caller (unless you just did): "What is your employee ' \
               'ID?"' in s.state_text() and "say this now" in s.state_text()

        def caller_says(text):
            s.on_transcript(text)
            s.speech_s = 1.5
            s.on_turn_end()

        def boldoc_replies():
            s.on_bot_started()
            s.on_bot_stopped()
        caller_says("E 4521")  # before BolDoc asked it: not taken as its answer
        await asyncio.sleep(0.05)
        assert not ctl.commands
        boldoc_replies()  # the announcement (its [STATE] showed the question) plays in full: it has been asked
        caller_says("haan")  # a backchannel is not a reply
        await asyncio.sleep(0.05)
        assert not ctl.commands
        boldoc_replies()
        caller_says("ek minute")  # "wait for me": nothing goes, not even when BolDoc says "ji, zaroor"
        boldoc_replies()
        await asyncio.sleep(0.05)
        assert not ctl.commands
        caller_says("kitna time lagega?")
        await asyncio.sleep(0.05)
        assert ctl.commands == [{"cmd": "answer", "session_id": "s", "epoch": 1, "turn_id": "reply-q_1-1",
                                 "question_id": "q_1", "value": "kitna time lagega?"}]
        assert s.acted_since_turn  # nothing to call a tool for: the claim guard won't demand one
        caller_says("E 4521")  # the writer is reading the last reply: no open question just now
        await asyncio.sleep(0.05)
        assert len(ctl.commands) == 1
        s.snapshot = ctl.snapshot = asking  # that didn't answer it: the same question is open again (not re-asked)
        boldoc_replies()  # ...and what the caller said meanwhile goes now, when BolDoc answers it
        await asyncio.sleep(0.05)
        assert ctl.commands[-1]["value"] == "E 4521" and len(ctl.commands) == 2
        # a turn the judge held as unfinished still goes once BolDoc replies to it
        import apps.talker.session as session_module
        s.snapshot = ctl.snapshot = asking
        judge, session_module.by_rules = session_module.by_rules, lambda *a: "incomplete"
        try:
            caller_says("haan likh lijiye E 4522")
        finally:
            session_module.by_rules = judge
        await asyncio.sleep(0.05)
        assert len(ctl.commands) == 2
        boldoc_replies()
        await asyncio.sleep(0.05)
        assert ctl.commands[-1]["value"] == "haan likh lijiye E 4522" and len(ctl.commands) == 3
        await s.end()
    asyncio.run(go())


def test_every_talker_notice_can_be_offered_and_said():
    """call.idle_check was offered as an Event its type list didn't allow: the first "anything else?" on a call
    would have raised inside the arbiter loop and silenced every later announcement."""
    from apps.talker.logic.state_block import describe_event
    from protocol.messages import PRIORITY, Event
    s = CallSession("s", None, "pstn", FakeControl())
    for kind in ("call.time_warning", "call.time_up", "call.idle_check", "call.idle_goodbye", "call.question_nudge",
                 "call.reask"):
        s._offer_local(kind)
        ev = Event(session_id="s", epoch=0, seq=0, type=kind)
        assert ev.priority == PRIORITY[kind] and describe_event(ev, {}) != kind.replace(".", " ")


def test_one_bad_notice_never_stops_the_announcements():
    async def go():
        s = CallSession("s", None, "pstn", FakeControl())

        async def run_llm():
            pass
        s.run_llm = run_llm
        await s.start()
        tries = []

        def broken(now):
            tries.append(now)
            raise ValueError("a bad notice")
        s._idle_notice = broken
        await asyncio.sleep(0.7)
        assert len(tries) >= 2  # the loop went on after the error
        await s.end()
    asyncio.run(go())


def test_a_quiet_caller_is_reminded_of_the_writers_question_once():
    import time as clock

    from apps.talker.session import QUESTION_NUDGE_S
    s = CallSession("s", None, "pstn", FakeControl())
    s.snapshot = dict(SNAP, question={"id": "q_1", "text": "What is your employee ID?", "default": "", "task_id": "t"})
    now = clock.monotonic()
    s.last_bot_stop, s.last_user_speech_end = now - QUESTION_NUDGE_S - 1, 0.0
    assert not s._question_nudge(now)  # not asked yet: nothing to remind of
    s.heard_questions.add("q_1")
    assert s._question_nudge(now) and not s._question_nudge(now)  # once per question


def test_the_call_never_ends_in_the_same_reply_that_starts_a_document():
    """the model called create_document and end_call together; end_call ran before the task
    existed and hung up, so the caller's next change was never heard."""
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        s.end_call = lambda: asyncio.sleep(0)
        await s.start()
        s.last_bot_line = "Toh: 25 se 27 October ki chhutti. Likh doon?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.on_transcript("haan, dono chahiye")
        s.speech_s = 1.0
        s.on_turn_end()
        s._yes = ("haan, dono chahiye", "yes")  # a yes with more in it: the yes-check's verdict, by meaning
        command = ctl.command

        async def slow(payload):  # control takes a moment to answer
            await asyncio.sleep(0.05)
            return await command(payload)
        ctl.command = slow
        create, created = params({"summary": "Leave 25-27 Oct", "formats": ["pdf", "docx"], "language": "en-IN"},
                                 MESSAGES)
        end, ended = params({}, tool_call_id="call_2")
        await asyncio.gather(s.create_document(create), s.end_call_tool(end))
        assert created["result"]["status"] == "started" and ended["result"]["status"] == "not_now"
        assert not s.ending
        await s.end()
    asyncio.run(go())


def test_the_call_ends_when_the_work_is_done_and_the_caller_wants_nothing_more(monkeypatch):
    """Ending is decided by the call's state and by meaning (the judge), never by a list of words."""
    from apps.talker import session as sm
    asked = []

    async def judge(client, key, bot_line, caller_text, ready):
        asked.append((caller_text, ready))
        await asyncio.sleep(0.05)
        return caller_text in ("Nahi karna", "Night")
    monkeypatch.setattr(sm, "caller_is_done", judge)

    async def go():
        hung = []

        async def hang_up():
            hung.append(1)
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        s.api_key, s.end_call = "k", hang_up
        await s.start()
        s.on_transcript("Isme kya likha hai?")
        s.on_turn_end()
        assert s._done_task is None  # nothing written yet: no ending decision at all
        s.snapshot = dict(SNAP, docs=[{"id": "doc_1", "title": "Loan Application", "head_version": 1,
                                       "formats": ["pdf"], "deliveries": []}], tasks=[])
        assert s.job_done() and "ended for you" in s.state_text()
        s.last_bot_line = "Letter ready hai. Koi aur letter likhna hai?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.utterance = ["link kab aayega?"]
        s.on_turn_end()
        await s.before_reply()  # the reply waits for the decision
        assert not s.ending and "goodbye" not in s.state_text()  # a question: they still want something
        s.on_bot_started()
        s.utterance = ["Nahi karna"]
        s.on_turn_end()
        await s.before_reply()
        assert s.ending and "short, warm goodbye" in s.state_text()  # this reply is the goodbye
        s.on_bot_started()
        s.on_bot_stopped()  # it played: the call ends
        await asyncio.sleep(0.8)
        assert hung and asked == [("link kab aayega?", 1), ("Nahi karna", 1)]
        await s.end()
    asyncio.run(go())


def test_no_goodbye_by_code_while_a_document_is_being_written():
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        s.snapshot = dict(SNAP, docs=[{"id": "doc_1", "title": "", "head_version": 0, "formats": ["pdf"],
                                       "deliveries": []}],
                          tasks=[{"id": "t1", "kind": "create", "doc_id": "doc_1", "state": "running",
                                  "created_at": 0}])
        s.last_bot_line = "Main likh rahi hoon. Isme kuch aur add karna hai?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.on_transcript("nahi")
        s.on_turn_end()
        assert not s.ending
        await s.end()
    asyncio.run(go())


def test_the_writers_checklist_is_kept_for_each_request_and_a_refused_request_is_not_sent():
    async def go():
        ctl = FakeControl()
        asked, told = [], []

        async def intake(session_id, transcript, written=None, earlier=None):
            asked.append(transcript)
            told.append(written)
            return {"intake": {"document": "medical certificate", "allowed": False,
                               "refusal": "it would pretend to come from a doctor; a leave application instead"},
                    "ms": 900}
        ctl.intake = intake
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        s.on_bot_line("Namaste! Bataiye, aaj kya likhna hai?")
        s.on_transcript("doctor ka medical certificate chahiye")
        s.on_turn_end()
        await asyncio.sleep(0.05)
        assert asked == ["BolDoc: Namaste! Bataiye, aaj kya likhna hai?\nCaller: doctor ka medical certificate chahiye"]
        assert "NOT ALLOWED" not in s.state_text() and "medical certificate" not in s.state_text()  # not spoken
        p, out = params({"summary": "medical certificate", "formats": ["pdf"], "language": "en-IN"}, MESSAGES)
        await s.create_document(p)
        assert out["result"]["status"] == "not_allowed" and not ctl.commands       # never sent
        assert len(asked) == 2  # one refusing check is confirmed by a fresh one before it is refused
        # with a document on the call, the next request is checked too, and the check knows what is already written
        s.snapshot = dict(SNAP, docs=[{"id": "d", "title": "Leave", "head_version": 1, "formats": ["pdf"],
                                       "deliveries": []}])
        s.on_bot_started()
        s.on_transcript("theek hai")
        s.on_turn_end()
        await asyncio.sleep(0.05)
        assert len(asked) == 3 and told[-1] == ["Leave"]
        await s.end()
    asyncio.run(go())


def test_a_create_cancelled_by_the_callers_next_words_is_made_again():
    """create_document on "English mein likhiye" was cancelled 80 ms later when "PDF" started a
    new turn (Pipecat cancels tool calls on interruption), and nothing was ever written."""
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        s.verdict = None  # the caller has just paused; whether they are done isn't decided yet: the handler waits
        p, _ = params({"summary": "Leave 25-27 Oct", "formats": ["pdf"], "language": "en-IN"}, MESSAGES)
        call = asyncio.create_task(s.create_document(p))
        await asyncio.sleep(0.1)
        call.cancel()  # what Pipecat does when the next turn starts
        with pytest.raises(asyncio.CancelledError):
            await call
        assert not ctl.commands and s.deferred_tool == "create_document"
        assert "NOT STARTED: your create_document call was put off" in s.state_text()
        await s.end()
    asyncio.run(go())


def test_an_earlier_calls_document_is_changed_only_when_the_caller_means_it():
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        s.recent_docs = [{"id": "old_1", "title": "Leave Application", "head_version": 2,
                          "updated_at": "2026-10-21T10:00:00+00:00"}]
        s.last_caller_text = "छुट्टी 26 से 27 October तक कर दीजिए।"  # nothing about an earlier document
        p, out = params({"instruction": "dates 26-27", "document": "Leave Application"}, MESSAGES)
        await s.edit_document(p)
        assert out["result"]["status"] == "confirm_first" and not ctl.commands
        s.last_bot_line = "Kya aap pichhli Leave Application ki baat kar rahe hain?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.last_caller_text = "haan wahi"  # yes to BolDoc's question
        p, out = params({"instruction": "dates 26-27", "document": "Leave Application"}, MESSAGES, "call_2")
        await s.edit_document(p)
        assert ctl.commands[-1]["doc_id"] == "old_1"
        s.last_bot_line, s.last_caller_text = "", "पिछली बार वाली leave application में 26 कर दीजिए"
        s.answered_line = s.last_bot_line  # the line their turn answers
        p, out = params({"instruction": "26", "document": "Leave Application"}, MESSAGES, "call_3")
        await s.edit_document(p)
        assert out["result"]["status"] == "confirm_first" and len(ctl.commands) == 1  # said back first
        s.last_bot_line, s.last_caller_text = "Pichhli Leave Application mein 26 kar doon?", "haan"
        s.answered_line = s.last_bot_line  # the line their turn answers
        p, out = params({"instruction": "26", "document": "Leave Application"}, MESSAGES, "call_4")
        await s.edit_document(p)
        assert len(ctl.commands) == 2
        await s.end()
    asyncio.run(go())


def test_a_fact_asked_twice_and_not_given_no_longer_holds_up_the_document():
    """the writer's checklist called the HR manager's name required; BolDoc asked for it five
    times and wrote nothing."""
    async def go():
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        await s.start()
        s.intake = {"document": "a leave application", "allowed": True, "facts": [
            {"label": "leave dates", "question": "Which dates?", "value": "25-27 October", "required": True},
            {"label": "HR manager's name", "question": "What is the HR manager's name?", "value": "",
             "required": True}]}
        assert 'Ask next: "What is the HR manager\'s name?"' in s.state_text()
        s._prepare_intake = lambda: None  # (no background calls in this test)
        for said in ("ਹਾਂ सब सही है। English में लिखिए", "No"):  # asked, and not given, twice
            s.on_bot_started()
            s.on_transcript(said)
            s.on_turn_end()
        assert "Every needed fact is given" in s.state_text() and "HR manager's name" not in s.state_text()
        await s.end()
    asyncio.run(go())


def test_a_failed_intake_leaves_no_stale_checklist_but_a_refusal_stands():
    async def go():
        ctl = FakeControl()
        replies = []

        async def intake(session_id, transcript, written=None, earlier=None):
            return replies.pop(0)
        ctl.intake = intake
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        loan = {"document": "a loan application", "allowed": True, "facts": [
            {"label": "applicant name", "question": "Your name?", "value": "", "required": True}]}
        replies += [{"intake": loan}, {"intake": None, "error": "provider_unavailable"}]
        await s._run_intake("Caller: loan application")
        assert "applicant name" in s.state_text()
        await s._run_intake("Caller: loan application\nCaller: mera naam Saurabh Kumar hai")  # that one timed out
        assert s.intake is None and "applicant name" not in s.state_text()            # stale: not shown
        refused = {"document": "medical certificate", "allowed": False, "refusal": "from a doctor"}
        replies += [{"intake": refused}, {"intake": None, "error": "provider_unavailable"}]
        await s._run_intake("Caller: doctor ka certificate")
        await s._run_intake("Caller: doctor ka certificate\nCaller: please")
        assert s.intake["allowed"] is False                     # a refusal stands (applied when BolDoc writes)
        await s.end()
    asyncio.run(go())



def test_the_call_ends_only_when_the_caller_says_they_are_done_or_boldoc_says_goodbye():
    """the talker model called end_call on "chhutti 26 se 27 kar dijiye" and on a "Likh doon?"."""
    from apps.talker.session import IDLE_CHECK_S, REASK_S
    from protocol.messages import Event

    async def go():
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        s.end_call = lambda: asyncio.sleep(0)
        await s.start()
        s.last_caller_text = "छुट्टी 26 से 27 October तक कर दीजिए।"
        p, out = params({})
        await s.end_call_tool(p)
        assert out["result"]["status"] == "not_now" and not s.ending          # a change request is not goodbye
        s.announcing = Event(session_id="s", epoch=1, seq=0, type="call.idle_goodbye")
        p, out = params({}, tool_call_id="call_2")
        await s.end_call_tool(p)
        assert out["result"]["status"] == "ending"                           # BolDoc's own idle goodbye
        await s.end()
    asyncio.run(go())
    # after the work, "anything else?" gets the full wait, not the quick re-ask
    t = CallSession("t", "919988776655", "pstn", FakeControl())
    t.turns_heard, t.last_user_speech_end, t.last_bot_stop = 1, 100.0, 100.0
    t.last_bot_line = "Leave application ready hai. Kuch aur badalna hai ismein?"
    t.answered_line = t.last_bot_line  # the line their turn answers
    assert t._idle_notice(100.0 + REASK_S + 1) is None
    assert t._idle_notice(100.0 + IDLE_CHECK_S + 1) == "call.idle_check"


def test_a_browser_call_is_told_the_document_is_on_screen_not_coming_by_sms():
    from apps.talker.logic.state_block import build_state_block

    phone = {"docs": [], "tasks": [], "verified_recipients": ["+919988776655"]}
    assert "verified mobile for SMS" in build_state_block(phone, "converse")
    browser = build_state_block({**phone, "on_screen": True}, "converse")
    assert "ON SCREEN" in browser and "Send to my phone" in browser and "verified mobile for SMS" not in browser
    s = CallSession("s", None, "sip", FakeControl())
    s.snapshot = {"on_screen": True, "verified_recipients": ["+919988776655"]}
    assert "screen" in s._sms_note() and "don't promise" in s._sms_note()
    assert s._where_it_is() == "on their screen and in their BolDoc account"
    s.snapshot = {"verified_recipients": ["+919988776655"]}
    assert "SMS to the number ending 6655" in s._sms_note() and s._where_it_is() == "on the SMS link"


def test_the_call_hangs_up_once_plivo_says_the_goodbye_has_played():
    """On a line that confirms its checkpoints, not on the estimate (0.6 s after the goodbye's last audio went out):
    Plivo's playedStream for the checkpoint behind the goodbye."""
    async def go():
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        await s.start()
        hung = []

        async def hang_up():
            hung.append(1)
        s.end_call = hang_up
        greeting = s.playout.name()                       # the greeting played, and Plivo said so
        s.playout.sent(greeting)
        s.playout.on_event({"event": "playedStream", "streamId": "x", "name": greeting})
        s.ending = True
        s.on_bot_started()                                # the goodbye
        s.on_bot_stopped()                                # its last audio went out, the checkpoint behind it
        bye = s.playout.name()
        s.playout.sent(bye)
        await asyncio.sleep(0.8)
        assert hung == []                                 # still playing: past the estimate, not hung up
        s.playout.on_event({"event": "playedStream", "streamId": "x", "name": bye})
        await asyncio.sleep(0.1)
        assert hung == [1] and [e["how"] for e in s.trace.events if e["e"] == "goodbye_played"] == ["played"]
        await s.end()
    asyncio.run(go())


def _scope_of_project_report() -> dict:
    return {"size": "long", "kind": "project_report", "name": "project report (DPR) for a loan", "size_n": 12,
            "unit": "pages", "minutes": 10, "price": 60, "signup_needed": False,
            "outline": [{"id": "s1", "title": "Executive summary"}, {"id": "s2", "title": "Repayment"}],
            "asks": [{"what": "bank and loan", "question": "Kis bank ke liye?"}]}


def test_a_big_document_is_scoped_and_the_callback_asked_before_it_is_written():
    """A long document is scoped on the call (outline, time, price, language) and BolDoc asks, on its own,
    whether it may call back with the writer's questions; the caller's yes goes with the create. Web research isn't
    asked about: it goes by the document's type, and a caller who says they want it or not is heard."""
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        s._prepare_intake = lambda: None
        s.scope = {"size": "long", "kind": "project_report", "name": "project report (DPR) for a loan",
                   "size_n": 12, "unit": "pages", "minutes": 10, "price": 60, "signup_needed": False,
                   "outline": [{"id": "s1", "title": "Executive summary"}, {"id": "s2", "title": "Repayment"}],
                   "asks": [{"what": "bank and loan", "question": "Kis bank ke liye?"}]}
        english = s._scope_line()  # calls start in English: every question in it is English
        assert "may I call you back on this number?" in english and "Should I write it in English or in Hindi?" \
            in english and "Shall I write it now?" in english and "likh doon" not in english.lower()
        s.lang.current, s.lang.family = "hi-IN", "hinglish"  # a Hinglish call: the Hinglish words
        line = s._scope_line()
        assert "Executive summary; Repayment" in line and "60 credits" in line and "call kar sakti hoon" in line
        assert "English mein likhun ya Hindi mein?" in line  # the language, unless they said it
        s.scope["research"] = True  # web research goes by the type, unasked
        assert "internet" not in s._scope_line() and s.research_ok is None
        # a caller who says so themselves is heard: no, then yes
        s.last_bot_line = "Aur kuch?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.on_transcript("internet se kuch mat daalna")
        s.on_turn_end()
        assert s.research_ok is False
        s.utterance.clear()  # a new turn (the pipeline starts one when the caller speaks)
        s.on_transcript("nahi, internet se jaankari jod dijiye")  # "nahi" to the last question, yes to research
        s.on_turn_end()
        assert s.research_ok is True
        s.last_bot_line = "Likhte waqt kuch poochna pada to isi number se call kar sakti hoon?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.utterance.clear()
        s.on_transcript("haan bilkul")
        s.on_turn_end()
        assert s.callback_ok is True and "call kar sakti hoon" not in s._scope_line()  # asked once
        s.last_bot_line = "Toh: project report, 2 hisse. Likh doon?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.last_caller_text, s.acted_since_turn = "haan likh dijiye", False

        async def finished():
            return True
        s.wait_caller_done = finished
        p, _out = params({"summary": "project report", "formats": ["pdf"]}, MESSAGES)
        await s.create_document(p)
        assert ctl.commands[-1]["callback_ok"] is True and ctl.commands[-1]["research_ok"] is True
        assert s.scope is None and s.callback_ok is None  # the next request is worked out afresh
        s.scope = dict(_scope_of_project_report(), signup_needed=True)  # a trial caller: sign up, or a short one now
        assert "sign-up" in s._scope_line() and "short version" in s._scope_line()
        await s.end()
    asyncio.run(go())


def test_a_long_document_in_progress_is_shown_in_minutes_and_the_caller_may_hang_up():
    from apps.talker.logic.state_block import build_state_block
    snap = dict(SNAP, docs=[{"id": "d1", "title": "Project report", "head_version": 0, "formats": ["pdf"],
                             "deliveries": []}],
                tasks=[{"id": "t1", "kind": "create", "doc_id": "d1", "state": "running", "tier": "T3",
                        "created_at": 0}])
    text = build_state_block(snap, mode="converse", eta_s={"t1": 540})
    assert "about 9 min left" in text and "may hang up" in text


def test_files_added_on_the_website_are_mentioned_once_and_go_to_the_big_document():
    """The caller added files on their home page for their next document: BolDoc says it has them, once,
    and the big document they ask for takes them."""
    async def go():
        ctl = FakeControl()
        ctl.files = ["Cafe quotation.docx", "Sales April.xlsx"]
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        s._prepare_intake = lambda: None
        assert "Aapki 2 files mil gayi hain" in s.state_text() and "Cafe quotation.docx, Sales April.xlsx" in \
            s.state_text()
        s.on_bot_line("Namaste Saurabh ji! Aapki 2 files mil gayi hain. Bataiye, kya likhna hai?")
        assert "you've told them so" in s.state_text() and "Aapki 2 files" not in s.state_text()
        ctl.files_used = list(ctl.files)
        s.last_bot_line = "Toh: project report, 9 hisse. Likh doon?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.last_caller_text, s.acted_since_turn = "haan likh dijiye", False

        async def finished():
            return True
        s.wait_caller_done = finished
        p, _out = params({"summary": "project report", "formats": ["pdf"], "language": "en-IN"}, MESSAGES)
        await s.create_document(p)
        assert s.files == [] and s._files_line() is None  # they went to this document
        await s.end()
    asyncio.run(go())


def test_a_refused_request_is_said_first_and_stays_refused():
    """(What is refused is the writer's check's call: forgeries, fake receipts, threats; the mechanics here are the
    talker's.)"""
    async def go():
        ctl = FakeControl()
        replies = []

        async def intake(session_id, transcript, written=None, earlier=None):
            return replies.pop(0)
        ctl.intake = intake
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        cert = {"document": "medical certificate from a doctor", "allowed": False,
                "refusal": "BolDoc can't write a certificate that has to come from a doctor; it can write a leave "
                           "application instead."}
        s.scope = {"size": "long", "name": "report", "outline": [{"title": "Summary"}], "size_n": 15,
                   "unit": "pages", "minutes": 5, "minutes_lo": 3, "price": 60, "asks": []}
        replies += [{"intake": dict(cert)}]
        await s._run_intake("Caller: doctor ka medical certificate bana do")
        text = s.state_text()
        assert "NOT ALLOWED" not in text  # one check refused: not said yet ...
        assert "60 credits" not in text and "Likh doon" not in text  # ...but no scoping, time or price meanwhile
        replies += [{"intake": dict(cert, document="doctor's medical certificate")}]
        await s._run_intake("Caller: doctor ka medical certificate bana do\nCaller: 3 din ka")
        text = s.state_text()
        assert "NOT ALLOWED" in text and "come from a doctor" in text and "Say so now" in text
        s.on_bot_line("Maaf kijiye, main doctor ka certificate nahi bana sakti. Leave application likh sakti hoon.")
        assert s.refusal["told"] and "you have told the caller" in s.state_text()
        # the check wavers ("it's only for practice"): the refusal stands, the go-ahead isn't given, create refuses
        replies += [{"intake": {"document": "practice medical certificate", "allowed": True, "facts": []}}]
        await s._run_intake("Caller: bas practice ke liye hai, bana do")
        assert s.refusal_stands() is not None and "you have told the caller" in s.state_text()
        replies += [{"intake": {"document": "certificate", "allowed": True, "facts": []}}]
        await s._run_intake("Caller: bas practice ke liye hai, bana do\nCaller: bana do na")
        assert s.refusal_stands() is not None  # two wavers about the same document lift nothing
        s.last_bot_line, s.last_caller_text = "Theek hai, certificate bana doon?", "haan bana do"
        s.answered_line = s.last_bot_line  # the line their turn answers
        p, out = params({"summary": "medical certificate", "formats": ["pdf"], "language": "en-IN"}, MESSAGES)
        await s.create_document(p)
        assert out["result"]["status"] == "not_allowed" and not ctl.commands
        # the caller moves on to something BolDoc can write: two checks in a row allow it
        letter = {"document": "leave application to the office", "allowed": True, "facts": []}
        replies += [{"intake": dict(letter)}, {"intake": dict(letter)}]
        await s._run_intake("Caller: theek hai, office ko leave application likh do")
        assert s.refusal is not None
        await s._run_intake("Caller: theek hai, office ko leave application likh do\nCaller: 3 din ki")
        assert s.refusal is None and "NOT ALLOWED" not in s.state_text()
        await s.end()
    asyncio.run(go())


def test_the_writers_check_never_reads_boldocs_own_refusals():
    """The check judges the caller's words, not BolDoc's."""
    from apps.talker.session import without_refusals
    convo = ["Caller: AI par research likhna hai", "BolDoc: Maaf kijiye, main personal research nahi likh sakti. "
             "Main ek report likh sakti hoon. Kis topic par?", "BolDoc: Maaf kijiye, main yeh nahi likh sakti.",
             "Caller: Yes, write it down."]
    assert without_refusals(convo) == ["Caller: AI par research likhna hai",
                                       "BolDoc: Main ek report likh sakti hoon. Kis topic par?",
                                       "Caller: Yes, write it down."]


def test_a_language_question_is_not_a_yes_to_write():
    """A real call: "English mein likhun ya Hindi mein?" answered "English mein likho aap" started a 12-slide deck
    with no outline, time or price said."""
    from apps.talker.logic.turns import approved, asked_to_write
    assert not asked_to_write("Achha, renewable energy pe presentation. English mein likhun ya Hindi mein?")
    assert approved("English mein likhun ya Hindi mein?", "English mein likho aap") is None
    assert not asked_to_write("Ya fir sirf \"HR Manager\" likh doon?")      # a choice of wording, not a go-ahead
    assert asked_to_write("Maaf kijiye, theek se sunai nahi diya. Likh doon, haan ya nahi?")  # still asks whether
    assert asked_to_write("Leave application, 26 se 27 October, aapke naam se. Likh doon?")
    assert not asked_to_write("Maine kiya. Likh doon")                         # no question mark: no ask
    assert asked_to_write("मैंने किया था. लिख दूँ?")                            # "किया" isn't "या"


def test_the_same_request_under_another_name_is_recognised():
    from apps.talker.logic.turns import same_request
    refused = "4th year college project report"
    for again in ("college project report", "demo project report", "academic project", "project report",
                  "final year project on machine learning"):
        assert same_request(again, refused), again
    for other in ("leave application to the college", "project report for a bank loan", "presentation on solar"):
        assert not same_request(other, refused), other


def _t3_session(ctl=None, on_screen=False):
    ctl = ctl or FakeControl()
    if on_screen:
        ctl.snapshot = dict(ctl.snapshot, on_screen=True)
    s = CallSession("s", "919988776655", "pstn", ctl)
    return s, ctl


def test_a_long_document_says_its_measured_time_and_offers_to_hang_up():
    """A real call: "das minute lagenge" for a deck ready in 77 s, and the caller kept on the line for it."""
    async def go():
        s, ctl = _t3_session()

        async def command(payload):
            ctl.commands.append(payload)
            ctl.snapshot = dict(ctl.snapshot, docs=[{"id": "doc_1", "title": "", "head_version": 0,
                                                     "formats": ["pptx"], "deliveries": []}],
                                tasks=[{"id": "task_1", "kind": "create", "doc_id": "doc_1", "state": "queued",
                                        "tier": "T3", "created_at": 0}])
            return {"status": "accepted", "task_id": "task_1", "tier": "T3", "eta_s": 120, "eta_lo_s": 60,
                    "snapshot": ctl.snapshot}
        ctl.command = command
        await s.start()
        s.intake = {"document": "presentation on renewable energy", "allowed": True, "facts": []}
        s.last_bot_line = "Renewable energy pe 12 slides ka deck, English mein, 1-2 minute, 60 credits. Likh doon?"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.last_caller_text = "haan likh do"
        p, out = params({"summary": "renewable energy deck", "formats": ["pptx"], "language": "en-IN"}, MESSAGES)
        await s.create_document(p)
        r = out["result"]
        assert r["status"] == "started" and r["takes"] == "about 1 to 2 minutes"
        assert "end the call now?" in r["say"] and "SMS as soon as it's ready" in r["say"]  # a yes or no to it
        assert "stay on the line" in r["say"]
        assert "task_1" in s.task_eta and s._etas()["task_1"] <= 120
        assert "min left" in s.state_text() and "don't ask whether they need anything else" in s.state_text()
        await s.end()
    asyncio.run(go())


def test_the_call_may_end_while_a_long_document_is_written_but_not_a_short_one(monkeypatch):
    """A long document's link comes by SMS when it's ready; a letter is seconds away."""
    from apps.talker import session as session_mod
    monkeypatch.setattr(session_mod, "END_GRACE_S", 30)

    async def done(*a, **k):
        return True
    monkeypatch.setattr(session_mod, "caller_is_done", done)

    async def go():
        for tier, ends in (("T3", True), ("T2", False)):
            s, _ctl = _t3_session()
            await s.start()
            s.api_key = "k"
            s.snapshot = dict(SNAP, docs=[{"id": "d1", "title": "", "head_version": 0, "formats": ["pdf"],
                                           "deliveries": []}],
                              tasks=[{"id": "t1", "kind": "create", "doc_id": "d1", "state": "running", "tier": tier,
                                      "created_at": 0}])
            s.last_bot_line, s.last_caller_text = "Call rakhna chahenge ya line par rahenge?", "rakh deta hoon"
            s.answered_line = s.last_bot_line  # the line their turn answers
            p, out = params({})
            await s.end_call_tool(p)
            assert (out["result"]["status"] == "ending") is ends, tier
            if ends:
                assert "still being written" in out["result"]["say"] and "SMS" in out["result"]["say"]
                assert not s.job_done()  # no automatic goodbye on a "theek hai" while they wait for it
            s.ending = True  # don't wait for the backstop hang-up in this test
            await s.end()
    asyncio.run(go())


def test_a_long_documents_progress_is_announced_and_a_new_document_is_checked():
    from apps.talker.logic.state_block import describe_event
    from protocol.messages import Event

    async def go():
        s, _ctl = _t3_session()
        await s.start()
        s.snapshot = dict(SNAP, docs=[{"id": "d1", "title": "", "head_version": 0, "formats": ["pptx"],
                                       "deliveries": []}],
                          tasks=[{"id": "t1", "kind": "create", "doc_id": "d1", "state": "running", "tier": "T3",
                                  "created_at": 0}])
        ev = Event(session_id="s", epoch=1, seq=3, type="task.progress", task_id="t1", doc_id="d1",
                   payload={"stage": "section 4 of 9"})
        assert s._worth_saying(ev)
        assert not s._worth_saying(Event(session_id="s", epoch=1, seq=4, type="task.progress", task_id="t1",
                                         doc_id="d1", payload={"stage": "started"}))
        line = describe_event(ev, s.snapshot)
        assert "4 of its 9 sections are written" in line and "stay on the line" in line and "ask nothing" in line
        done = Event(session_id="s", epoch=1, seq=5, type="task.completed", task_id="t1", doc_id="d1",
                     head_version=1)
        snap = dict(s.snapshot, docs=[{"id": "d1", "title": "Renewable Energy", "head_version": 1,
                                       "formats": ["pptx"], "deliveries": []}])
        line = describe_event(done, snap)
        assert '"Renewable Energy" is ready' in line and "take a look" in line and "something should change" in line
        assert "on their screen" in describe_event(done, dict(snap, on_screen=True))
        await s.end()
    asyncio.run(go())


def test_the_last_goodbye_plays_to_the_end():
    """A real call: the goodbye was cut 0.3 s in by the caller's own "bye", and the line dropped 2 s later unheard."""
    async def go():
        s, _ctl = _t3_session()
        await s.start()
        s.bot_speaking = True
        assert not s.goodbye_playing()      # an ordinary reply: the caller can cut in
        s.ending = True
        assert s.goodbye_playing()          # the goodbye: nothing cuts it off
        s.bot_speaking = False
        assert not s.goodbye_playing()
        await s.end()
    asyncio.run(go())


def test_the_wait_tone_plays_only_while_a_caller_waits_quietly_for_a_long_document(monkeypatch):
    from apps.talker import wait_tone
    monkeypatch.setenv("WAIT_TONE", "1")

    async def go():
        s, _ctl = _t3_session()
        await s.start()
        now = 1000.0
        s.last_bot_stop, s.last_user_speech_end = now - 10, now - 12
        assert not s._wants_wait_tone(now)  # nothing long being written
        s.snapshot = dict(SNAP, tasks=[{"id": "t1", "kind": "create", "doc_id": "d1", "state": "running",
                                        "tier": "T3", "created_at": 0}])
        assert s._wants_wait_tone(now)
        s._tone_at = now - 1
        assert not s._wants_wait_tone(now)  # one motif every few seconds
        s._tone_at = 0.0
        s.last_bot_stop = now - 2
        assert not s._wants_wait_tone(now)  # BolDoc has just spoken
        s.last_bot_stop, s.ending = now - 10, True
        assert not s._wants_wait_tone(now)  # saying goodbye
        s.ending = False
        monkeypatch.setenv("WAIT_TONE", "0")
        assert not s._wants_wait_tone(now)
        await s.end()
    asyncio.run(go())
    clip = wait_tone.CLIP
    assert 0.4 <= len(clip) / 2 / wait_tone.RATE <= 0.6   # half a second of 16-bit audio


def test_a_second_document_on_the_call_is_checked_on_its_own_words():
    """After a complaint letter, "AI par 8-9 page ka research" was never checked or scoped, went to the short writer,
    and no format was asked."""
    async def go():
        ctl = FakeControl()
        seen = []

        async def intake(session_id, transcript, written=None, earlier=None):
            seen.append((transcript, written))
            return {"intake": {"document": "research on AI", "allowed": True, "facts": []}}
        ctl.intake = intake
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        s.on_bot_line("Bataiye, kya likhna hai?")
        s.on_transcript("Shubham ko complaint letter")
        s.on_turn_end()
        await asyncio.sleep(0.05)
        s.scope = {"size": "long", "name": "x"}
        s.last_bot_line, s.last_caller_text = "Shubham ko complaint, aapke naam se. Likh doon?", "haan likh do"
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.intake = {"document": "complaint letter", "allowed": True, "facts": []}
        p, out = params({"summary": "complaint", "formats": ["pdf"], "language": "en-IN"}, MESSAGES)
        await s.create_document(p)
        assert out["result"]["status"] == "started"
        assert s.intake is None and s.scope is None   # the next request starts afresh
        s.on_bot_started()
        s.on_bot_line("Likh rahi hoon.")
        s.on_transcript("ab AI par 8-9 page ka research")
        s.on_turn_end()
        await asyncio.sleep(0.05)
        transcript, written = seen[-1]
        assert "complaint" not in transcript and "8-9 page" in transcript   # only what was said since
        assert written == ["the document being written"]
        await s.end()
    asyncio.run(go())


def test_a_yes_to_add_to_a_document_goes_to_edit_and_a_long_wait_has_no_countdown():
    from apps.talker.logic.state_block import approval_line, build_state_block
    assert "call create_document now" in approval_line("write")
    line = approval_line("write", has_docs=True)
    assert "edit_document" in line and "create_document" in line
    snap = dict(SNAP, docs=[{"id": "d1", "title": "", "head_version": 0, "formats": ["pdf"], "deliveries": []}],
                tasks=[{"id": "t1", "kind": "create", "doc_id": "d1", "state": "running", "tier": "T2",
                        "created_at": 0}])
    text = build_state_block(snap, mode="converse", eta_s={"t1": -12})
    assert "a little longer than usual" in text and "s left" not in text
    assert "don't ask whether they need anything else" in text
    assert "about 9 s left" in build_state_block(snap, mode="converse", eta_s={"t1": 9.4})


def test_the_callers_own_name_from_the_writers_check_is_used_and_saved():
    """The caller's own name, however given ("Aapka naam?" "Saurabh"), comes from the writer's check (by meaning)."""
    async def go():
        ctl = FakeControl()
        saved = []

        async def save_name(session_id, name):
            saved.append(name)

        async def intake(session_id, transcript, written=None, earlier=None):
            return {"intake": {"document": "leave application", "allowed": True, "facts": [],
                               "caller_name": "Saurabh"}}
        ctl.save_name, ctl.intake = save_name, intake
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        await s._run_intake("BolDoc: Aapka naam?\nCaller: Saurabh")
        await asyncio.sleep(0.05)
        assert s.caller_name == "Saurabh" and saved == ["Saurabh"]
        await s.end()
    asyncio.run(go())


def test_a_goodbye_the_model_says_without_end_call_still_ends_the_call(monkeypatch):
    """The call now ends once such a goodbye has played, when the goodbye check says so by meaning, nothing is pending,
    and the model called no tool with that reply."""
    from apps.talker import session as sm
    asked = []

    async def closes(client, key, before, caller_text, reply):
        asked.append((before, caller_text, reply))
        return "great day" in reply
    monkeypatch.setattr(sm, "reply_closes_call", closes)
    monkeypatch.setattr(sm, "END_GRACE_S", 30)
    question = "What would you like to write today? Maybe a letter, application, or something else?"
    goodbye = "No problem at all. Just reach out whenever you need a document drafted. Have a great day!"

    async def call():
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        s.api_key = "k"
        await s.start()
        hung = []

        async def hang_up():
            hung.append(1)
        s.end_call = hang_up
        return s, hung

    async def reply(s, before, caller_text, text, tool=False):
        s.last_bot_line, s.last_caller_text = before, caller_text
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.on_llm_start()
        s.on_bot_line(text)
        if tool:
            s.on_tool_call()
        s.on_bot_started()
        await asyncio.sleep(0.8)  # the check runs just after the reply (a tool call arrives in that time)

    async def go():
        s, hung = await call()
        await reply(s, question, "Nahi", goodbye)
        assert s.ending and asked == [(question, "Nahi", goodbye)]
        s.on_bot_stopped()                                       # the goodbye has played: the call ends
        await asyncio.sleep(0.8)
        assert hung == [1]
        await s.end()

        s, hung = await call()                                   # the goodbye hadn't started playing yet:
        s.last_bot_line, s.last_caller_text = question, "Nahi"     # it ends the call when it has played, not before
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.on_llm_start()
        s.on_bot_line(goodbye)
        await asyncio.sleep(0.8)
        assert s.ending and hung == []
        s.on_bot_started()
        s.on_bot_stopped()
        await asyncio.sleep(0.8)
        assert hung == [1]
        await s.end()

        s, hung = await call()                                   # the judge says it isn't over: nothing happens
        await reply(s, question, "Nahi", "Theek hai. Agar kuch aur chahiye ho to bataiye, main yahin hoon.")
        assert not s.ending and len(asked) == 3
        await reply(s, question, "Nahi", goodbye, tool=True)    # end_call (or any tool) came with it: its own path
        await reply(s, question, "Nahi", "Theek hai, aur kya likhna hai?")  # a question: not a goodbye
        await reply(s, "Aapka naam?", "Saurabh", "Dhanyavaad Saurabh ji.")  # asked, by meaning: it isn't over
        assert not s.ending and len(asked) == 4
        s.snapshot = {**s.snapshot, "tasks": [{"id": "t1", "kind": "create", "state": "running"}]}
        await reply(s, question, "Nahi", goodbye)               # a document is being written: never here
        assert not s.ending and len(asked) == 4 and hung == []
        await s.end()
    asyncio.run(go())


def test_set_language_switches_the_voice_and_the_call_reports_its_language_at_the_end():
    """Asked in words the rules don't catch, the talker model calls set_language; the language the call ends in is
    sent to control, which starts the caller's next call in it (phone, "Call me" or the browser)."""
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        assert s.lang.family == "en-IN"
        p, out = params({"language": "hi-IN"})
        await s.set_language_tool(p)
        assert out["result"]["status"] == "ok" and s.lang.family == "hinglish" and s.lang.chosen == "hi-IN"
        assert "Hindi" in s.state_text()
        p, out = params({"language": "fr-FR"})
        await s.set_language_tool(p)
        assert out["result"]["status"] == "unknown_language" and s.lang.family == "hinglish"
        await s.end()
        assert ctl.ended and ctl.ended_lang == "hi-IN"

        quiet = FakeControl()                                    # a call they said nothing in teaches nothing
        t = CallSession("t", "919988776655", "pstn", quiet)
        await t.start()
        await t.end()
        assert quiet.ended and quiet.ended_lang is None
    asyncio.run(go())


def test_an_english_call_is_asked_in_english_and_never_hears_hinglish_words():
    """English calls now ask in English, which the yes-checks know, and Hinglish calls keep their words."""
    from apps.talker.logic.state_block import approval_line, describe_event
    from apps.talker.logic.turns import asked_to_change, asked_to_write
    from apps.talker.prompts import SYSTEM_PROMPT
    from protocol.messages import Event
    assert asked_to_write("So: a letter to your mother, from you. Shall I write it?")
    assert asked_to_change("So, the hyphens removed. Shall I make this change?")
    waiting = approval_line(None, change_waiting=True, family="en-IN")
    assert "Shall I make this change?" in waiting and "doon" not in waiting
    assert "kar doon?" in approval_line(None, change_waiting=True, family="hinglish").lower()
    assert "Shall I write it now?" in SYSTEM_PROMPT and "Kya main ise likh doon?" in SYSTEM_PROMPT  # each language
    ev = Event(session_id="s", epoch=1, seq=0, type="call.reask", payload={"question": "...Shall I make this change?"})
    assert "Shall I make this change?" in describe_event(ev, {})  # the question itself is asked again

    async def go():
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        await s.start()
        s.caller_name = "Saurav"
        assert '("Saurav")' in s.state_text() and "Saurav ji" not in s.state_text()
        s.last_bot_line, s.last_caller_text = "Who is the letter to?", "my mother"
        s.answered_line = s.last_bot_line  # the line their turn answers
        p, out = params({"summary": "letter to mother", "formats": ["pdf"], "language": "en-IN"})
        await s.create_document(p)
        assert out["result"]["status"] == "confirm_first" and "Shall I write it now?" in out["result"]["say"]
        assert "Likh doon" not in out["result"]["say"]
        s.lang.current, s.lang.family = "hi-IN", "hinglish"
        assert "Saurav ji" in s.state_text()
        s._offer_local("call.reask")  # the notice carries BolDoc's question, to be asked again
        await s.end()
    asyncio.run(go())


def test_a_change_the_rules_dont_know_is_found_by_meaning_and_its_reply_is_checked():
    """After a document exists, other caller words go to the change check, by meaning; until it answers, the reply is
    checked."""
    from apps.talker import session as sm

    async def judge(client, key, line, text):
        return text == "English mein likho"

    async def go(monkeypatch_judge):
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        s.api_key = "k"
        await s.start()
        s._prepare_intake = lambda: None
        s.snapshot = {**s.snapshot, "docs": [{"id": "doc_1", "title": "Leave", "head_version": 1}]}
        s.last_bot_line, s.utterance = "Aapka letter ready hai. Kuch badalna hai?", ["English mein likho"]
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.on_turn_end()
        assert s.change_suspected() and s.guard_reply()           # asked, not answered yet: checked
        assert await s.change_requested() and s.unacted_change()
        s.utterance = ["theek hai, shukriya"]
        s.on_turn_end()
        assert not await s.change_requested() and not s.guard_reply()
        await s.end()
    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(sm, "asks_for_change", judge)
    try:
        asyncio.run(go(judge))
    finally:
        mp.undo()


def test_an_answer_the_yes_rules_cant_place_goes_to_the_yes_check_and_only_its_yes_counts(monkeypatch):
    """The go-ahead: word rules decide at once; "Sounds good" or "Yup" after "Shall I write it?" goes to a small
    model, and only its "yes" writes the document. A clear yes or no never waits for it."""
    from apps.talker import session as sm
    asked = []

    async def judge(client, key, line, text):
        asked.append(text)
        return {"Sounds good": "yes", "Next time you buy": "no"}.get(text, "unclear")
    monkeypatch.setattr(sm, "is_yes", judge)
    read_back = "So: a letter to your mother, from you. Shall I write it?"

    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        s.api_key = "k"
        await s.start()
        s._prepare_intake = lambda: None
        s._clear_to_write = lambda: True
        for said, writes in [("Next time you buy", False), ("Sounds good", True)]:
            s.last_bot_line, s.utterance = read_back, [said]
            s.answered_line = s.last_bot_line  # the line their turn answers
            s.on_turn_end()
            await s.before_reply()                     # [STATE] is built after the check
            assert ("call create_document now" in s.state_text()) is writes, said
            p, out = params({"summary": "letter to mother", "formats": ["pdf"], "language": "en-IN"})
            s.acted_since_turn = False
            await s.create_document(p)
            assert (out["result"]["status"] != "confirm_first") is writes, (said, out["result"])
        s.last_bot_line, s.utterance = read_back, ["Yes"]   # the rules know a plain yes: no check
        s.answered_line = s.last_bot_line  # the line their turn answers
        s.on_turn_end()
        assert asked == ["Next time you buy", "Sounds good"]
        await s.end()
    asyncio.run(go())


def test_a_failover_moves_the_checks_and_answers_the_turn_that_failed():
    async def go():
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        await s.start()
        runs = []

        async def run_llm():
            runs.append(1)
        s.run_llm, s.api_key = run_llm, "sarvam-key"
        await s.on_llm_failover("bedrock:qwen.qwen3-235b-a22b-2507", judge="qwen-judge")
        assert runs == [1] and s.api_key == "qwen-judge"
        assert any(e["e"] == "llm_failover" for e in s.trace.events)
        await s.end()
    asyncio.run(go())


def _turn(s, words):
    """The caller says `words` and their turn ends (what the turn detector does on a real call)."""
    s.utterance.clear()
    s.on_transcript(words)
    s.on_turn_end()


LEAVE = [{"id": "doc_1", "title": "Leave Application", "head_version": 1, "formats": ["pdf"], "deliveries": []}]


def test_a_yes_to_a_change_said_back_is_made_by_the_session_when_the_model_only_claims_it():
    """The say-back counts as a waiting change (the caller asked for one a turn before), the reply is held, and the
    session makes the change itself."""
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        ctl.snapshot = s.snapshot = dict(s.snapshot, docs=LEAVE)
        _turn(s, "Leave application is wrong.")
        s._note_change_asked(s.last_caller_text)  # what the change check (by meaning) says of it
        s.on_bot_line("What is wrong with the Leave Application, Saurabh?")
        _turn(s, "Subject is leave application for personal walk.")
        said_back = "I'll update the subject to \"Leave application for personal walk\" right now. Shall I change it?"
        s.on_bot_line(said_back)
        assert s.pending_change == said_back
        _turn(s, "Yes change")
        s._yes = ("Yes change", "yes")  # the yes-check's verdict on these words
        assert s.guard_reply()
        s.on_bot_line("The document is updated now, Saurabh.")  # the model's reply: held, not heard
        assert await s.approved_change_unmade() and await s.make_approved_change()
        edit = ctl.commands[-1]
        assert edit["cmd"] == "edit_doc" and edit["doc_id"] == "doc_1" and said_back in edit["instruction"]
        assert "Caller: Leave application is wrong." in edit["transcript"]
        assert s.pending_change is None and s.acted_since_turn
        p, out = params({"instruction": "subject: personal work"}, MESSAGES)  # the model's own call, too late
        await s.edit_document(p)
        assert [c["cmd"] for c in ctl.commands] == ["edit_doc"] and out["result"]["status"] == "started"
        # a plain "yes" holds the reply too (no word of change in it), and a "no" makes nothing
        s.on_bot_line("Toh 25 ki jagah 26 October kar doon?")
        _turn(s, "yes")
        assert s.guard_reply() and await s.approved_change_unmade()
        s.on_bot_line("Toh 25 ki jagah 26 October kar doon?")
        _turn(s, "nahi, rehne dijiye")
        assert not await s.approved_change_unmade()
    asyncio.run(go())


def test_asking_again_after_a_silence_is_never_dropped_as_a_repeat_or_carried_into_the_answer():
    """A re-ask's question is asked again word for word; an announcement whose reply said nothing ends; a silence nudge
    the caller talks over is dropped, while news is carried by the next reply."""
    import time

    from protocol.messages import Event

    async def go():
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        await s.start()
        q = "Shall I change the subject, yes or no?"
        s.remember_said(q)
        assert s.said_before(q) and not s.may_ask_again(q)
        reask = Event(session_id="s", epoch=1, seq=0, type="call.reask", payload={"question": q})
        s.arbiter.offer(reask, time.monotonic())
        s.announcing = reask
        s.on_llm_start()
        assert s.may_ask_again(q)
        s.announcement_unheard(reply_said_nothing=True)
        assert s.announcing is None and s.arbiter.must_mention() == []
        news = Event(session_id="s", epoch=1, seq=3, type="task.completed", task_id="t", doc_id="doc_1",
                     head_version=1)
        s.arbiter.offer(news, time.monotonic())
        s.announcing = news
        s.on_llm_start()
        s.on_interruption()
        assert s.announcing is None and [e.type for e in s.arbiter.must_mention()] == ["task.completed"]
        s.on_llm_end()
        s.on_llm_start()  # a reply to the caller that says nothing isn't an announcement's
        s.announcing = reask
        s.announcement_unheard(reply_said_nothing=True)
        assert s.announcing is reask
    asyncio.run(go())


def test_a_yes_is_judged_against_the_line_the_caller_answered_not_the_reply_being_made():
    """A reply's words become BolDoc's last line as soon as the model ends it, before its tool calls run: "Theek hai,
    badal rahi hoon." then edit_document made the tool see no "kar doon?" at all; and a read-back said in the same
    reply as create_document would count as asked and answered. The yes is judged on the line their turn answered."""
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        ctl.snapshot = s.snapshot = dict(s.snapshot, docs=LEAVE)
        s.on_bot_line("Toh 25 ki jagah 26 October kar doon?")
        _turn(s, "haan")
        s.on_bot_line("Theek hai, badal rahi hoon.")
        p, out = params({"instruction": "dates 26 to 27 October"}, MESSAGES)
        await s.edit_document(p)
        assert [c["cmd"] for c in ctl.commands] == ["edit_doc"]
        ctl2 = FakeControl()
        t = CallSession("t", "919988776655", "pstn", ctl2)
        await t.start()
        t.on_bot_line("Kis tareekh se kis tareekh tak chhutti chahiye?")
        _turn(t, "haan, 25 se 27 October")
        t.on_bot_line("Toh: 25 se 27 October ki chhutti, aapke naam se. Likh doon?")
        p, out = params({"summary": "leave 25-27 October", "formats": ["pdf"], "language": "hi-IN"}, MESSAGES)
        await t.create_document(p)
        assert ctl2.commands == [] and out["result"]["status"] == "confirm_first"
    asyncio.run(go())


def test_dropping_an_older_reply_leaves_the_one_being_made_alone():
    """The drop took the new reply's place as BolDoc's last line, and "Yes, please do it." got confirm_first. A drop is
    about its own reply: its sentences are unsaid, and the newer reply's line stands."""
    async def go():
        ctl = FakeControl()
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        ctl.snapshot = s.snapshot = dict(s.snapshot, docs=LEAVE)
        heard = "Saurabh ji, samajh gayi. 25th ki jagah 26th October karna hai, ya kuch aur?"
        held = "Sorry Saurabh ji, mujhe samajh nahi aaya. Aap 26 se 27 October karna chahte hain, ya kuch aur?"
        new = "Toh 25 October ki jagah 26 se 27 October ki leave kar doon? Likh doon?"
        one = s.reply_started()
        s.remember_said(heard)
        s.on_bot_line(heard)
        s.reply_heard(one)
        two = s.reply_started()
        s.remember_said(held)
        s.on_bot_line(held)               # held at the gate: the caller is talking
        _turn(s, "Give me the car.")
        three = s.reply_started()         # the reply to it starts before the gate drops the held one
        assert s.forget_unheard(two) and not s.said_before(held)
        s.remember_said(new)
        s.on_bot_line(new)
        s.reply_heard(three)
        assert s.last_bot_line == new and s.said_before(new)
        _turn(s, "Yes, please do it.")
        p, _out = params({"instruction": "dates 26 to 27 October"}, MESSAGES)
        await s.edit_document(p)
        assert [c["cmd"] for c in ctl.commands] == ["edit_doc"]
    asyncio.run(go())


def test_a_goodbye_is_on_its_way_from_the_moment_it_is_said_until_it_has_played():
    s = CallSession("s", "919988776655", "pstn", FakeControl())
    s.reply_started()
    s.remember_said("Ji, aur kuch?")
    assert not s.goodbye_on_its_way()  # not ending: the caller's words start turns as usual
    s.ending = True
    assert s.goodbye_on_its_way()      # said, held at the gate for the caller's pause
    s.reply_heard()
    assert not s.goodbye_on_its_way()  # released: once it plays, goodbye_playing holds it
    s.bot_speaking = True
    assert s.goodbye_on_its_way()


def test_with_nothing_left_a_yes_to_ending_the_call_ends_it_after_the_goodbye(monkeypatch):
    """With only long documents being written (their links come by SMS), BolDoc asks "Call end kar doon?"; a yes ends
    the call by the session, a no keeps them on the line, and a short document still being written never ends it."""
    from apps.talker import session as session_mod
    monkeypatch.setattr(session_mod, "END_GRACE_S", 30)

    async def not_done(*a, **k):
        return False
    monkeypatch.setattr(session_mod, "caller_is_done", not_done)
    asked = "Saurabh ji, main likh rahi hoon, paanch minute lagenge. Call end kar doon? Link SMS pe aa jayega."

    async def go():
        for tier, said, ends in (("T3", "haan", True), ("T3", "nahi, main line pe hoon", False), ("T2", "haan", False)):
            s, _ctl = _t3_session()
            await s.start()
            s.api_key = "k"
            s.snapshot = dict(SNAP, docs=[{"id": "d1", "title": "", "head_version": 0, "formats": ["pdf"],
                                           "deliveries": []}],
                              tasks=[{"id": "t1", "kind": "create", "doc_id": "d1", "state": "running", "tier": tier,
                                      "created_at": 0}])
            s.on_bot_line(asked)
            _turn(s, said)
            if s._done_task is not None:
                await s._done_task
            assert s.ending is ends, (tier, said)
            if ends:
                assert "goodbye" in s.goodbye_note and "still being written" in s.goodbye_note
            s.ending = True  # don't wait for the backstop hang-up in this test
            await s.end()
    asyncio.run(go())


def test_one_refusing_check_is_confirmed_by_a_fresh_one_before_a_create_is_refused():
    """A single refusal that isn't confirmed gets a fresh check; two in a row refuse."""
    async def go(second_allows):
        ctl = FakeControl()
        verdicts = [False, second_allows]

        async def intake(session_id, transcript, written=None, earlier=None):
            allowed = verdicts.pop(0) if verdicts else second_allows
            return {"intake": {"document": "application to the gaushala manager", "allowed": allowed,
                               "refusal": "" if allowed else "not allowed: something else instead", "facts": []},
                    "ms": 50}
        ctl.intake = intake
        s = CallSession("s", "919988776655", "pstn", ctl)
        await s.start()
        s.on_bot_line("Toh: gaushala ke manager ko, Sukhwanti ke ilaaj ke liye madad. Likh doon?")
        _turn(s, "haan likh dijiye")
        await asyncio.sleep(0.05)  # the turn's check (refuses once)
        assert (s.intake or {}).get("allowed") is False and s.refusal_stands() is None
        p, out = params({"summary": "application to the gaushala manager", "formats": ["pdf"], "language": "hi-IN"},
                        MESSAGES)
        await s.create_document(p)
        return [c["cmd"] for c in ctl.commands], out["result"]["status"]
    assert asyncio.run(go(True)) == (["create_doc"], "started")
    assert asyncio.run(go(False))[0] == []


def test_the_goodbye_to_a_quiet_caller_is_a_fixed_line_never_the_models_question():
    """The goodbye is said as is, in the call's language, with where the document is."""
    from protocol.messages import Event

    async def go():
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        await s.start()
        said, ran = [], []

        async def speak(text, **kw):
            said.append(text)

        async def run_llm():
            ran.append(1)
        s.speak, s.run_llm = speak, run_llm
        s.arbiter.decide = lambda signals: Event(session_id="s", epoch=1, seq=0, type="call.idle_goodbye")
        await s._arbitrate_once()
        assert ran == [] and said == ["It seems you're busy right now. Call again any time. Goodbye!"]
        assert not said[0].rstrip().endswith("?")
        s.on_bot_started()
        assert s.goodbye_playing()                       # the caller's words over it start no turn
        s.on_bot_stopped()
        assert s.ending                                  # and the call ends once it has played
        await s.end()
    asyncio.run(go())
    t = CallSession("t", "919988776655", "pstn", FakeControl())
    t.lang.family = "hinglish"
    t.snapshot = {"docs": [{"id": "d1", "title": "Leave Application", "head_version": 1}], "tasks": []}
    assert t.idle_goodbye_line() == ("Lagta hai aap abhi vyast hain. Aapka document SMS link par hai. Jab chahein, "
                                     "phir se call kijiye. Namaste!")


def test_a_reply_cut_off_counts_only_what_had_played_as_said():
    """Only what surely finished playing is said; BolDoc's last line is what they heard."""
    import time as _time

    async def go():
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        await s.start()
        s.last_bot_line = s._heard_line = "Kuch aur add karna hai?"
        s.reply_started()
        line = ("Aapki Leave Application ready hai, aur link SMS se aa raha hai. Ek baar dekh lijiye, sab sahi hai "
                "ya kuch change karna hai?")
        s.remember_said(line)
        s.last_bot_line = line
        s.reply_heard()
        s.on_bot_started()
        s._spoke_from = _time.monotonic() - 1.9          # cut off 1.9 s in: the first sentence (60 chars) wasn't over
        s.on_interruption()
        assert not s.said_before("Aapki Leave Application ready hai, aur link SMS se aa raha hai.")
        assert not s.said_before("Ek baar dekh lijiye, sab sahi hai ya kuch change karna hai?")
        assert s.last_bot_line == "Kuch aur add karna hai?"    # what they last heard in full
        # played well past the first sentence: it was heard, the question wasn't
        s.reply_started()
        s.remember_said(line)
        s.last_bot_line = line
        s.reply_heard()
        s.on_bot_started()
        s._spoke_from = _time.monotonic() - 6.0
        s.on_interruption()
        assert s.said_before("Aapki Leave Application ready hai, aur link SMS se aa raha hai.")
        assert not s.said_before("Ek baar dekh lijiye, sab sahi hai ya kuch change karna hai?")
        assert s.last_bot_line == "Aapki Leave Application ready hai, aur link SMS se aa raha hai."
        await s.end()
    asyncio.run(go())


def test_a_goodbye_dropped_whole_is_replaced_by_the_fixed_one():
    """the goodbye's reply was dropped (a repeat, a claim) and nothing was said: 8.8 s of
    silence on one, a caller hanging up on a silent line on another."""
    async def go():
        s = CallSession("s", "919988776655", "pstn", FakeControl())
        await s.start()
        said = []

        async def speak(text, **kw):
            said.append(text)
        s.speak = speak
        s.bot_speaking = False
        s._say_goodbye_and_end()
        await s.nothing_said()
        await s.nothing_said()                              # once
        assert said == ["Thank you for calling BolDoc. Goodbye!"]
        await s.end()
    asyncio.run(go())
