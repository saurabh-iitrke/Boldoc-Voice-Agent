"""The scripted caller's step loop (apps/control/testcall/caller.py, shared by the e2e gate and the admin's test
call), against a fake talker that greets, answers each line, and asks once more after the first yes."""
import asyncio
import base64
import json
import time

import websockets

from apps.control.testcall import caller

SILENCE = b"\xff"


class FakeTalker:
    """A talker on the other end of the stream: greets, then answers whenever the caller stops speaking."""

    def __init__(self, replies, hangs_up=False, checkpoints=False, clear_last=False):
        self.replies, self.inbox, self.sent = list(replies), None, []
        self.speaking, self.heard = False, 0
        self.hangs_up, self.closed = hangs_up, False  # hangs up after its last reply, as after its goodbye
        self.checkpoints, self.clear_last = checkpoints, clear_last  # a checkpoint behind each reply; cut the last
        self.marked_at, self.played, self.cleared = {}, [], 0

    async def __aenter__(self):
        self.inbox = asyncio.Queue()  # made in the running loop (Python 3.9 ties a queue to the loop it's made in)
        return self

    async def __aexit__(self, *exc):
        return False

    def _play(self, n=6):
        for _ in range(n):
            self.inbox.put_nowait(json.dumps({"event": "playAudio", "media": {
                "payload": base64.b64encode(b"\x10" * caller.FRAME).decode()}}))
        if self.checkpoints:
            name = f"r{len(self.marked_at) + 1}"
            self.marked_at[name] = time.monotonic()
            self.inbox.put_nowait(json.dumps({"event": "checkpoint", "streamId": "test-stream", "name": name}))
            if self.clear_last and not self.replies:  # the caller cut in on the last reply
                self.inbox.put_nowait(json.dumps({"event": "clearAudio", "streamId": "test-stream"}))

    async def send(self, raw):
        if self.closed:
            raise websockets.ConnectionClosedOK(None, None)
        m = json.loads(raw)
        self.sent.append(m["event"])
        if m["event"] == "playedStream":
            self.played.append((m["name"], time.monotonic() - self.marked_at[m["name"]]))
        elif m["event"] == "clearedAudio":
            self.cleared += 1
        if m["event"] == "start":
            self._play()  # the greeting
        elif m["event"] == "media":
            audio = base64.b64decode(m["media"]["payload"])
            if audio.strip(SILENCE):
                self.speaking = True
            elif self.speaking:  # the caller stopped: answer
                self.speaking, self.heard = False, self.heard + 1
                if self.replies:
                    self.replies.pop(0)
                    self._play()
                    if self.hangs_up and not self.replies:
                        self.closed = True
                        self.inbox.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        m = await self.inbox.get()
        if m is None:
            raise StopAsyncIteration
        return m


def test_the_step_loop_says_each_line_and_answers_a_re_ask(monkeypatch):
    monkeypatch.setitem(caller.SCENARIOS, "tiny", [
        ("say", "Namaste."),
        ("confirm", ("Haan, kar dijiye.", "हाँ जी, कर दीजिए।", ("26",))),
        ("listen", 0.3),
    ])

    async def synth(key, text, voice="", cache=None):
        return b"\x40" * caller.FRAME * 5  # 0.1 s of "speech"
    heard_texts = iter(["Maaf kijiye, theek se sunai nahi diya. 26 October kar doon?"])

    async def transcribe(key, audio):
        return next(heard_texts, "theek hai")
    monkeypatch.setattr(caller, "synth", synth)
    monkeypatch.setattr(caller, "transcribe", transcribe)
    talker = FakeTalker(replies=["hello", "asks again", "done"])
    events, data_of = [], {}

    def on_event(kind, data):
        events.append(kind)
        data_of.setdefault(kind, []).append(data)
    call = caller.ScriptedCall("k", "ws://talker/plivo/stream", "tiny", "test-1", on_event=on_event,
                               connect=lambda url: talker)
    call.QUIET_S, call.CONFIRM_QUIET_S = 0.25, 0.35

    async def run():
        return await asyncio.wait_for(call.run(), 20)
    result = asyncio.run(run())
    assert result.greeting and not result.ended_by_bolDoc
    labels = [s.label for s in result.steps]
    assert labels == ["Namaste.", "Haan, kar dijiye.", "[ASKED AGAIN] हाँ जी, कर दीजिए।", "(silent 0.3s)"]
    assert all(s.latency is not None for s in result.steps[:3]) and result.steps[3].latency is None
    assert events[:3] == ["connected", "greeting", "step"] and "asked_again" in events and events[-1] == "ended"
    # what the console shows: the greeting's audio to transcribe, each step in words
    assert data_of["greeting"][0]["audio"] == result.greeting
    assert [d["text"] for d in data_of["step"]][-1] == "The caller stays quiet for 0.3 s"
    assert talker.heard == 3 and talker.sent[0] == "start"


def test_a_goodbye_that_hangs_up_is_still_a_reply(monkeypatch):
    monkeypatch.setitem(caller.SCENARIOS, "bye", [("say", "Nahi, bas. Thank you."), ("listen", 0.3)])

    async def synth(key, text, voice="", cache=None):
        return b"\x40" * caller.FRAME * 5
    monkeypatch.setattr(caller, "synth", synth)
    talker = FakeTalker(replies=["goodbye"], hangs_up=True)
    events = []
    call = caller.ScriptedCall("k", "ws://talker/plivo/stream", "bye", "test-2",
                               on_event=lambda kind, data: events.append((kind, data)), connect=lambda url: talker)
    call.QUIET_S = 0.25

    async def run():
        return await asyncio.wait_for(call.run(), 20)
    result = asyncio.run(run())
    assert result.ended_by_bolDoc and result.steps == []  # it hung up during the first step
    replied = [data for kind, data in events if kind == "replied"]
    assert len(replied) == 1 and replied[0]["i"] == 1 and replied[0]["latency"] is None
    assert replied[0]["audio"] == b"\x10" * caller.FRAME * 6  # its goodbye, whole
    assert events[-1] == ("ended", {"by_bolDoc": True})


def test_a_question_the_script_has_no_line_for_gets_its_otherwise(monkeypatch):
    """The writer's checklist asked "गाय को क्या problem है?" after the yes; the script had no answer and nothing was
    written."""
    monkeypatch.setitem(caller.SCENARIOS, "tiny", [
        ("confirm", ("Haan, likh dijiye.", "हाँ जी, लिख दीजिए।", ("application",), caller.DONT_KNOW)),
        ("listen", 0.3),
    ])

    async def synth(key, text, voice="", cache=None):
        return b"\x40" * caller.FRAME * 5
    heard_texts = iter(["नमस्ते। गाय को क्या problem है?"])

    async def transcribe(key, audio):
        return next(heard_texts, "theek hai")
    monkeypatch.setattr(caller, "synth", synth)
    monkeypatch.setattr(caller, "transcribe", transcribe)
    talker = FakeTalker(replies=["hello", "asks", "writes"])
    call = caller.ScriptedCall("k", "ws://talker/plivo/stream", "tiny", "test-1", connect=lambda url: talker)
    call.QUIET_S, call.CONFIRM_QUIET_S = 0.25, 0.35

    async def run():
        return await asyncio.wait_for(call.run(), 20)
    result = asyncio.run(run())
    assert [s.label for s in result.steps] == ["Haan, likh dijiye.", "[ASKED SOMETHING ELSE] " + caller.DONT_KNOW,
                                               "(silent 0.3s)"]
    assert caller.DONT_KNOW in caller.lines_of(caller.SCENARIOS["doc"])  # synthesised up front with the rest
    assert caller.asks_other("नमस्ते। गाय को क्या problem है?")
    assert not caller.asks_other("सौरभ जी लिख रही हूँ लगभग 14 second लगेंगे")  # being written: nothing to answer
    assert not caller.asks_other("Theek hai.")
    assert caller.still_open("सौरभ जी बाकी एक चीज़ और जैसे दवा या डॉक्टर बुलवाना")
    assert not caller.still_open("theek hai")
    assert not caller.still_open("सौरभ जी लिख रही हूँ लगभग 14 second लगेंगे")


def test_a_re_ask_is_heard_in_any_of_its_forms():
    topic = ("26", "छब्बीस", "chhabbees")
    assert caller.asked_again("माफ़ कीजिए ठीक से सुनाई नहीं दिया")       # "didn't hear properly"
    assert caller.asked_again("Maaf kijiye, theek se sunai nahi diya.")
    assert caller.asked_again("Sorry, could you say that again?")
    assert caller.asked_again("सौरभ जी छुट्टी 26 से 27 October कर दो।", topic)  # "kar doon?" heard as "kar do"
    assert not caller.asked_again("कुछ और बदल दूँ?")                         # an offer of more, not a re-ask
    assert not caller.asked_again("सौरभ जी छुट्टी 26 से 27 October कर दी है। कुछ और चाहिए?", topic)
    write = ("application", "छुट्टी")
    assert caller.asked_again("आपके नाम से HR manager को छुट्टी की application 25 से 27 October बहन की शादी के लिए "
                              "PDF और Word दोनों में बना दो।", write)
    assert not caller.asked_again("सौरभ जी मैं आपकी leave application लिख रही हूँ बस पंद्रह सेकंड", write)
    cow = ("application", "आवेदन")
    heard = "Saurabh ji, application likhne ke liye ek baat aur. Aapko kis tarah ki madad chahiye, dawai ya doctor?"
    assert not caller.asked_again(heard, cow) and caller.asks_other(heard)
    assert caller.asked_again("Saurabh ji, gaushala manager ko application likh doon?", cow)  # the yes, asked again


def test_mu_law_decodes_like_g711():
    pcm = caller.ulaw_to_pcm16(bytes([0xFF, 0x7F, 0x00, 0x80]))
    samples = [int.from_bytes(pcm[i:i + 2], "little", signed=True) for i in range(0, 8, 2)]
    assert samples == [0, 0, -32124, 32124]


def test_the_caller_answers_checkpoints_and_clears_like_plivo(monkeypatch):
    """playedStream once the audio before a checkpoint has played (6 frames: 120 ms at real time), clearedAudio for
    a clear, and a checkpoint cleared before it played is never answered."""
    monkeypatch.setitem(caller.SCENARIOS, "marks", [("say", "Namaste."), ("listen", 0.3)])

    async def synth(key, text, voice="", cache=None):
        return b"\x40" * caller.FRAME * 5
    monkeypatch.setattr(caller, "synth", synth)
    talker = FakeTalker(replies=["hello"], checkpoints=True, clear_last=True)
    call = caller.ScriptedCall("k", "ws://talker/plivo/stream", "marks", "test-3", connect=lambda url: talker)
    call.QUIET_S = 0.25

    async def run():
        return await asyncio.wait_for(call.run(), 20)
    asyncio.run(run())
    assert [name for name, _ in talker.played] == ["r1"] and talker.played[0][1] >= 0.1  # the greeting, once heard
    assert talker.cleared == 1 and call.played == ["r1"]                                   # the reply was cut: r2 never
