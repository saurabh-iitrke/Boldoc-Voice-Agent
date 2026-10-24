"""The scripted caller: a fake Plivo caller that talks to the talker over the real stream protocol.

Synthesises the caller's lines with a Sarvam voice (cached on disk, so a repeat costs nothing), streams them in real
time as 20 ms mu-law frames, records BolDoc's audio, measures voice-to-voice latency, and transcribes what BolDoc
said. A scripted call is anonymous (no number, no SMS) unless control admitted it first with a caller number; the
e2e gate (scripts/e2e_gate.sh) and the admin console's test call admit it with the DID as caller.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import io
import json
import os
import re
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

import httpx

API = "https://api.sarvam.ai"
FRAME = 160  # 20 ms of 8 kHz mu-law
CACHE = Path(os.environ.get("TESTCALL_CACHE", "/tmp/boldoc-caller-audio"))

# Each step: ("say", text) waits for BolDoc's reply; ("listen", seconds) stays silent and records anything BolDoc says
# on its own (the thinker finishing while the caller waits); ("confirm", (yes, yes_again[, topic[, otherwise]])) says
# yes, and says yes_again if BolDoc asks again, as a person would when a phone line garbles their "haan"; or, if BolDoc
# asks something the script has no line for, says `otherwise`.
DONT_KNOW = "Woh nahi pata, aise hi likh dijiye."
SCENARIOS: Dict[str, List[Tuple[str, Any]]] = {
    "basic": [
        ("say", "Namaste. Mujhe apne manager ke liye ek leave application likhni hai."),
        ("say", "Pachchees se sattaais October tak, meri behen ki shaadi hai Jaipur mein."),
        ("say", "Haan, HR manager ko address kar dijiye. PDF chahiye."),
    ],
    # the caller pauses mid-sentence to recall a number
    "pause": [
        ("say", "Namaste. Mujhe Goa trip ke kharche ki Excel sheet banani hai."),
        ("say_parts", ["Goa trip ke kharche mein flight", 1.4, "atharah hazaar paanch sau rupaye."]),
        ("say", "Hotel teen raat ka, chaar hazaar do sau per night. Aur chaar log ja rahe hain."),
        ("say", "Bas itna hi. Excel mein bana dijiye."),
        ("listen", 25),
    ],
    # a slow speaker who stops to think in the middle of sentences : BolDoc says हम्म and waits, never answering half a
    # sentence
    "slow": [
        ("say_parts", ["Namaste. Mujhe apne HR manager ke liye", 2.8, "ek leave application likhni hai."]),
        ("say_parts", ["Pachchees se sattaais October tak chhutti chahiye kyunki", 3.0,
                       "meri behen ki shaadi hai Jaipur mein."]),
        ("say_parts", ["Mera naam Saurabh Kumar hai aur main", 2.5, "Acme Technologies mein Senior Engineer hoon."]),
        ("say", "Haan, sab sahi hai. English mein likhiye, PDF chahiye."),
        ("confirm", ("Haan, likh dijiye.", "हाँ जी, लिख दीजिए।", ("application", "एप्लीकेशन", "छुट्टी", "chhutti"),
                     DONT_KNOW)),
        ("listen", 20),
        ("say", "Nahi, bas. Thank you."),
        ("listen", 12),
    ],
    # a caller who switches to Tamil: BolDoc should answer in Tamil with a Tamil voice, and write a Tamil document
    "tamil": [
        ("say", "Namaste. Mujhe ek chhutti ki application chahiye."),
        ("say", "வணக்கம். நான் தமிழில் பேசலாமா? எனக்கு ஒரு விடுப்பு கடிதம் வேண்டும்."),
        ("say", "நவம்பர் மூன்று முதல் ஐந்து வரை, என் தம்பியின் திருமணம். என் பெயர் சௌரப் குமார்."),
        ("say", "ஆம், சரி. தமிழில் PDF ஆக வேண்டும்."),
        ("listen", 30),
    ],
    "interrupt": [
        ("say", "Namaste. Mujhe ek letter likhna hai."),
        ("barge", (2.5, "Bank manager ke liye.")),
        ("say", "Account band karwana hai."),
        ("say", "Mera naam Saurabh Kumar hai. Account SBI mein hai."),
        ("listen", 20),
    ],
    "doc": [
        ("say", "Namaste. Mujhe apne HR manager ke liye ek leave application likhni hai."),
        ("say", "Pachchees se sattaais October tak chhutti chahiye, meri behen ki shaadi hai Jaipur mein. Mera naam "
                "Saurabh Kumar hai, main Acme Technologies mein Senior Engineer hoon."),
        ("say", "Haan, sab sahi hai. English mein likhiye, PDF aur Word dono chahiye."),
        # nothing is written or changed before a yes to what BolDoc read back; a yes said when it's already writing does
        # no harm. This speech-to-text heard "…PDF aur Word dono mein bana doon?" as "…बना दो।", so a re-ask is known by
        # its topic too the yes in Devanagari: this voice's romanised "Haan, likh dijiye" came out as "Hello लिख दीजिए"
        # and "Hello ਲਿਖ ଜିଏ", an unclear yes, and the script ran out of lines before a document
        ("confirm", ("हाँ जी, लिख दीजिए।", "हाँ जी, लिख दीजिए।", ("application", "एप्लीकेशन", "छुट्टी", "chhutti"),
                     DONT_KNOW)),
        # the change comes once the document is ready (~6-10 s) and before BolDoc's "anything else?" / goodbye
        # (12 s + 10 s of silence after it says so): a caller silent for 35 s is said goodbye to, as designed
        ("listen", 20),
        ("say", "Ek change kar dijiye. Chhutti chhabbees se sattaais October tak kar dijiye."),
        # this voice's romanised "Haan, kar dijiye" comes out of speech-to-text as "हाव काट दीजिए" / "How… Give me the
        # car"; its Devanagari line comes out right
        ("confirm", ("हाँ जी, कर दीजिए।", "हाँ जी, कर दीजिए।", ("26", "छब्बीस", "chhabbees"))),
        ("listen", 12),
        ("say", "Nahi, bas. Thank you."),  # after BolDoc's "anything else?": it says goodbye and ends the call
        ("listen", 12),
    ],
    # a request nobody would send as said : BolDoc asks what's meant, then writes what the caller really needs
    "sense": [
        ("say", "Namaste. Mujhe gaay ko ek application likhni hai."),
        ("say", "Arre nahi, gaushala ke manager ko. Hamari gaay Sukhwanti bimaar hai, uske ilaaj ke liye madad "
                "chahiye."),
        ("say", "Mera naam Saurabh Kumar hai, Shyam Nagar, Jaipur se. Hindi mein PDF chahiye."),
        ("confirm", ("Haan, likh dijiye.", "हाँ जी, लिख दीजिए।", ("application", "एप्लीकेशन", "गौशाला", "gaushala"),
                     DONT_KNOW)),
        ("listen", 20),
        ("say", "Nahi, bas. Thank you."),
        ("listen", 12),
    ],
    # a caller who asks for Hindi without a talking word, then wants nothing : BolDoc switches to its Hindi voice, says
    # goodbye, and the call ends
    "lang": [
        ("say", "Hello. Hindi please."),
        ("say", "Abhi kuch nahi likhna hai. Bas itna hi, shukriya."),
        ("listen", 15),
    ],
    # a long document: scoped on the call, the callback question, the outline read back, then Hermes writes
    # it after the call (it lands minutes later: check the report again then)
    "bigdoc": [
        ("say", "Namaste. Mujhe MUDRA loan ke liye project report chahiye. Meri chai ki dukaan hai, use cafe banana "
                "hai."),
        ("say", "Dukaan ka naam Kumar Tea and Snacks hai, Shyam Nagar, Jaipur mein. Mera naam Saurabh Kumar hai. Abhi "
                "mahine ki bikri do lakh chaalis hazaar hai."),
        ("say", "Kharcha: coffee machine ek lakh bees hazaar, fridge paintalis hazaar, furniture ek lakh das hazaar, "
                "kitchen pachaasi hazaar, aur interiors ek lakh chaalis hazaar. Main khud ek lakh lagaunga, baaki loan "
                "chahiye, Punjab National Bank se."),
        ("confirm", ("Haan, call kar lijiye.", "हाँ जी, कॉल कर लीजिए।", ("call", "कॉल"), DONT_KNOW)),
        ("confirm", ("Haan, likh dijiye.", "हाँ जी, लिख दीजिए।", ("report", "रिपोर्ट", "hisse", "section"), DONT_KNOW)),
        ("listen", 15),
        ("say", "Theek hai, shukriya. Bas itna hi."),
        ("listen", 10),
    ],
}
# What a scenario's call must produce (the call report's --expect): the doc scenario writes and changes a document;
# the sense scenario asks what was meant before it writes.
EXPECT: Dict[str, Tuple[str, ...]] = {"doc": ("create", "edit"), "sense": ("sense", "create"), "bigdoc": ("create",),
                                      "slow": ("create",), "lang": ("hindi", "ends")}

_DONE_OR_DOING = ("कर रही", "कर दी है", "कर दिया", "हो गया", "हो गई", "बदल दी", "बदल दिया", "बदल रही", "तैयार",
                  "kar rahi", "kar di hai", "kar diya", "ho gaya", "ho gayi", "badal rahi", "badal di", "ready",
                  "update", "लिख रही", "लिख दी है", "बना रही", "बना दी है", "likh rahi", "likh di hai", "bana rahi",
                  "bana di hai")
# BolDoc asking the caller to say it again: "Maaf kijiye, ek baar phir boliye?", "theek se sunai nahi diya"
_SAY_AGAIN = ("फिर बोलिए", "फिर से बोलिए", "दोबारा", "सुनाई नहीं", "phir boliye", "dobara", "sunai nahi",
              "say that again")


def asked_again(heard: str, topic: tuple = ()) -> bool:
    """BolDoc's reply (as transcribed) asks for the yes once more: a sentence with "…kar doon?" / "likh doon?" that
    isn't an offer of more ("kuch aur … doon?"), also when the reply goes on to something else; or, with a topic, a
    sentence about it that says nothing is done or being done (this speech-to-text heard "…kar doon?" as "…कर दो।").
    """
    for s in re.split(r"[.?!।]", heard.lower()):
        if any(w in s for w in _SAY_AGAIN):
            return True  # a person says it again
        if any(w in s for w in ("कुछ और", "कोई और", "kuch aur", "koi aur")):
            continue
        if any(w in s for w in ("दूँ", "दूं", "doon", "dun")):
            return True
        # a sentence about the topic that asks for something else is a new question, not the yes again
        if topic and any(t in s for t in topic) and not any(w in s for w in _DONE_OR_DOING) and not _asks_wh(heard):
            return True
    return False


_ASKING = ("?", "क्या", "कौन", "कितन", "कब", "कहाँ", "कहां", "कैसे", "किस", "kya", "kaun", "kitna", "kitne", "kitni",
           "kis ", "kab ", "kahan", "kaise", "what", "which", "how ", "when", "where", "who")


def _asks_wh(heard: str) -> bool:
    """Asks for information (kya, kitna, kaise, what ...), not for a yes."""
    low = heard.lower()
    return any(w in low for w in _ASKING if w != "?")


def asks_other(heard: str) -> bool:
    """BolDoc's reply (as transcribed) asks the caller something, not a yes again, and says nothing is done or being
    done: "नमस्ते। गाय को क्या problem है?"."""
    low = heard.lower()
    return any(w in low for w in _ASKING) and not any(w in low for w in _DONE_OR_DOING)


def still_open(heard: str) -> bool:
    """BolDoc said more than an acknowledgement after the caller's yes, and it neither writes nor finishes: a question
    the transcript lost. "Theek hai" alone is not."""
    low = heard.lower().strip()
    return len(low.split()) >= 5 and not any(w in low for w in _DONE_OR_DOING)


def mulaw_silence(n_frames: int) -> List[bytes]:
    return [b"\xff" * FRAME] * n_frames


CALLER_VOICES = {"hi-IN": "rahul_hi_conversational", "en-IN": "dev_en_conversational", "ta-IN": "aravind_ta_ads",
                 "te-IN": "tarun_te_narration"}


def lang_of(text: str) -> str:
    if any("஀" <= ch <= "௿" for ch in text):
        return "ta-IN"
    if any("ఀ" <= ch <= "౿" for ch in text):
        return "te-IN"
    return "hi-IN"


def _ulaw_byte(u: int) -> int:
    """G.711 mu-law to a 16-bit sample (what audioop.ulaw2lin did; audioop is gone in Python 3.13)."""
    u = ~u & 0xFF
    sample = (((u & 0x0F) << 3) + 0x84) << ((u & 0x70) >> 4)
    return (0x84 - sample) if u & 0x80 else (sample - 0x84)


_ULAW = [_ulaw_byte(i).to_bytes(2, "little", signed=True) for i in range(256)]


def ulaw_to_pcm16(mulaw: bytes) -> bytes:
    return b"".join(_ULAW[b] for b in mulaw)


def lines_of(steps: List[Tuple[str, Any]]) -> List[str]:
    """Every line a scenario says (to synthesise up front)."""
    out: List[str] = []
    for kind, arg in steps:
        if kind == "say":
            out.append(arg)
        elif kind == "confirm":
            out += list(arg[:2]) + ([arg[3]] if len(arg) > 3 else [])
        elif kind == "barge":
            out.append(arg[1])
        elif kind == "say_parts":
            out += [x for x in arg if isinstance(x, str)]
    return out


async def synth(key: str, text: str, voice: str = "", cache: Path = CACHE) -> bytes:
    cache.mkdir(parents=True, exist_ok=True)
    lang = lang_of(text)
    voice = voice or CALLER_VOICES[lang]
    f = cache / (hashlib.sha1(f"{voice}:{text}".encode()).hexdigest() + ".ulaw")
    if f.exists():
        return f.read_bytes()
    body = {"text": text, "target_language_code": lang, "model": "bulbul:v4-flash", "speaker": voice,
            "speech_sample_rate": 8000, "output_audio_codec": "mulaw"}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{API}/text-to-speech/stream", json=body, headers={"api-subscription-key": key})
        r.raise_for_status()
        f.write_bytes(r.content)
    return f.read_bytes()


async def transcribe(key: str, mulaw: bytes) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(ulaw_to_pcm16(mulaw))
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{API}/speech-to-text", headers={"api-subscription-key": key},
                         files={"file": ("bot.wav", buf.getvalue(), "audio/wav")},
                         data={"model": "saaras:v3", "mode": "codemix"})
    if r.status_code != 200:
        return f"<stt HTTP {r.status_code}>"
    return r.json().get("transcript", "")


@dataclass
class StepResult:
    label: str
    latency: Optional[float]   # BolDoc's first audio after the caller stopped (s), None: no reply to time
    audio: bytes               # what BolDoc said during the step (mu-law)


@dataclass
class CallResult:
    call_id: str
    greeting: bytes
    steps: List[StepResult] = field(default_factory=list)
    ended_by_bolDoc: bool = False  # BolDoc said goodbye and hung up (end_call or the idle goodbye)
    clears: int = 0                # barge-in clears BolDoc sent


Event = Callable[[str, Dict[str, Any]], Union[Awaitable[None], None]]


class ScriptedCall:
    """One scripted call over a talker stream URL: `await ScriptedCall(...).run`.

    `on_event(kind, data)` (sync or async) follows the call as it happens: "connected"; "greeting" {seconds, audio};
    "step" {i, total, kind, text} before each step; "replied" {i, label, latency, audio} after it, with BolDoc's audio
    from that step (also when BolDoc hangs up mid-step: its goodbye); "asked_again" {i, heard, again, other} with what
    BolDoc asked and the yes said once more (other: it asked something else and got the step's `otherwise`); "ended"
    {by_bolDoc}. `out_dir` keeps each step's audio as step<i>.ulaw (and step<i>a.ulaw for a yes said twice)."""

    QUIET_S = 1.4          # BolDoc's reply is over after this much silence
    CONFIRM_QUIET_S = 2.6  # a re-ask can be two sentences with a gap between them ("Maaf kijiye, theek se sunai nahi

    def __init__(self, key: str, ws_url: str, scenario: str, call_id: str, *, on_event: Optional[Event] = None,
                 out_dir: Optional[Path] = None, connect: Optional[Callable[..., Any]] = None):
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}")
        self.key, self.ws_url, self.scenario, self.call_id = key, ws_url, scenario, call_id
        self.steps = SCENARIOS[scenario]
        self.on_event, self.out_dir = on_event, out_dir
        self._connect = connect  # tests pass a fake; the default is websockets.connect
        self.audio_for: Dict[str, bytes] = {}
        self.bot_audio: List[Tuple[float, bytes]] = []
        self.clears: List[float] = []
        self.played: List[str] = []  # checkpoints answered with playedStream, as Plivo does
        self._played_to = 0.0        # when BolDoc's audio sent so far will have played (like Plivo's queue)
        self._marks: List[asyncio.Task] = []
        self._chunk = 0
        self._ws = None
        self._at: Optional[Tuple[int, int]] = None  # the step being said, and how much BolDoc had said before it

    async def _emit(self, event: str, **data: Any) -> None:
        if self.on_event is None:
            return
        out = self.on_event(event, data)
        if inspect.isawaitable(out):
            await out

    async def prepare(self) -> None:
        """Synthesise every line before connecting (cached: a repeat costs nothing)."""
        for text in lines_of(self.steps):
            if text not in self.audio_for:
                self.audio_for[text] = await synth(self.key, text)

    async def _send_frames(self, frames: List[bytes]) -> None:
        t0 = time.monotonic()
        for i, fr in enumerate(frames):
            self._chunk += 1
            await self._ws.send(json.dumps({"event": "media", "streamId": "test-stream", "media": {
                "track": "inbound", "chunk": self._chunk, "timestamp": str(self._chunk * 20),
                "payload": base64.b64encode(fr).decode()}}))
            await asyncio.sleep(max(0.0, t0 + (i + 1) * 0.02 - time.monotonic()))

    async def _wait_bot_quiet(self, after: int, quiet: float = QUIET_S, timeout: float = 25.0) -> None:
        """Stream silence until NEW bot audio (index > `after`) has started and then stopped for `quiet` s."""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            await self._send_frames(mulaw_silence(10))
            if len(self.bot_audio) > after and time.monotonic() - self.bot_audio[-1][0] > quiet:
                return

    def _frames(self, text: str) -> List[bytes]:
        utt = self.audio_for[text]
        return [utt[j:j + FRAME].ljust(FRAME, b"\xff") for j in range(0, len(utt), FRAME)]

    async def _speak(self, parts: list, n_before: int, quiet: float = QUIET_S) -> Tuple[str, Optional[float]]:
        """Say `parts` (text, or seconds of a thinking pause), wait for BolDoc's reply: (label, latency)."""
        frames: List[bytes] = []
        for part in parts:
            frames += self._frames(part) if isinstance(part, str) else mulaw_silence(int(part * 50))
        await self._send_frames(frames)
        end_of_speech = time.monotonic()
        talked_over = len(self.bot_audio) > n_before  # BolDoc audio arrived while the caller was still talking
        await self._wait_bot_quiet(after=n_before, quiet=quiet)
        after = [t for t, _ in self.bot_audio[n_before:] if t >= end_of_speech]
        label = ("[TALKED OVER] " if talked_over else "") + " … ".join(p for p in parts if isinstance(p, str))
        return label, (after[0] - end_of_speech) if after else None

    def _keep(self, name: str, audio: bytes) -> None:
        if self.out_dir is not None:
            (self.out_dir / name).write_bytes(audio)

    async def run(self) -> CallResult:
        import websockets

        await self.prepare()
        connect = self._connect or (lambda url: websockets.connect(url, max_size=None))
        result = CallResult(self.call_id, b"")
        async with connect(self.ws_url) as ws:
            self._ws = ws
            await ws.send(json.dumps({"event": "start", "streamId": "test-stream", "start": {
                "callId": self.call_id, "streamId": "test-stream", "accountId": "test",
                "tracks": ["inbound"], "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000}},
                "extra_headers": "resumed=0"}))
            await self._emit("connected")

            async def reader() -> None:
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("event") == "playAudio":
                        now, audio = time.monotonic(), base64.b64decode(m["media"]["payload"])
                        self.bot_audio.append((now, audio))
                        self._played_to = max(self._played_to, now) + len(audio) / 8000  # a byte a sample, 8 kHz
                    elif m.get("event") == "checkpoint":  # answered once the audio before it has played
                        self._marks = [t for t in self._marks if not t.done()]
                        self._marks.append(asyncio.create_task(self._played(ws, m.get("name"), self._played_to)))
                    elif m.get("event") == "clearAudio":
                        self.clears.append(time.monotonic())
                        self._played_to = time.monotonic()
                        for t in self._marks:  # Plivo's docs don't say; here a cleared checkpoint is never answered
                            t.cancel()
                        await ws.send(json.dumps({"event": "clearedAudio", "streamId": "test-stream"}))

            rtask = asyncio.create_task(reader())
            try:
                await self._wait_bot_quiet(after=0)  # greeting
                result.greeting = b"".join(b for _, b in self.bot_audio)
                await self._emit("greeting", seconds=round(len(result.greeting) / 8000, 1), audio=result.greeting)
                try:
                    await self._steps(result)
                except websockets.ConnectionClosed:
                    result.ended_by_bolDoc = True
                    if self._at is not None:  # it hung up mid-step, after its goodbye: what it said, as a reply
                        await asyncio.wait({rtask}, timeout=2)  # the last frames it sent
                        i, n_before = self._at
                        await self._emit("replied", i=i, label="(BolDoc hung up)", latency=None,
                                         audio=b"".join(b for _, b in self.bot_audio[n_before:]))
            finally:
                rtask.cancel()
                for t in self._marks:
                    t.cancel()
        result.clears = len(self.clears)
        await self._emit("ended", by_bolDoc=result.ended_by_bolDoc)
        return result

    async def _played(self, ws, name: Any, at: float) -> None:
        import websockets

        await asyncio.sleep(max(0.0, at - time.monotonic()))
        try:
            await ws.send(json.dumps({"event": "playedStream", "streamId": "test-stream", "name": name}))
        except websockets.ConnectionClosed:  # the call ended first
            return
        self.played.append(str(name))

    async def _steps(self, result: CallResult) -> None:
        total = len(self.steps)
        for i, (kind, arg) in enumerate(self.steps):
            n_before = len(self.bot_audio)
            self._at = (i + 1, n_before)
            text = (arg[1] if kind == "barge" else arg[0] if kind == "confirm" else
                    " … ".join(p for p in arg if isinstance(p, str)) if kind == "say_parts" else
                    f"The caller stays quiet for {arg:g} s" if kind == "listen" else arg)
            await self._emit("step", i=i + 1, total=total, kind=kind, text=text)
            if kind == "barge":  # talk over BolDoc: wait until its reply has played for `after_s`, then speak
                after_s, line = arg
                start = time.monotonic()
                while len(self.bot_audio) <= n_before and time.monotonic() - start < 15:
                    await self._send_frames(mulaw_silence(5))
                await self._send_frames(mulaw_silence(int(after_s * 50)))
                await self._send_frames(self._frames(line))
                end_of_speech = time.monotonic()
                await self._wait_bot_quiet(after=len(self.bot_audio))
                after = [t for t, _ in self.bot_audio[n_before:] if t >= end_of_speech]
                latency = (after[0] - end_of_speech) if after else None
                label = f"[BARGE after {after_s}s] {line}"
            elif kind == "listen":  # caller stays quiet; anything BolDoc says now is unprompted
                await self._send_frames(mulaw_silence(int(arg * 50)))
                latency = None
                if len(self.bot_audio) > n_before:
                    await self._wait_bot_quiet(after=n_before, timeout=10)
                label = f"(silent {arg}s)"
            elif kind == "confirm":
                yes, yes_again, *rest = arg
                topic, otherwise = (tuple(rest[0]) if rest else ()), (rest[1] if len(rest) > 1 else None)
                label, latency = await self._speak([yes], n_before, quiet=self.CONFIRM_QUIET_S)
                # a person answers each question in turn: the sense gate answered the writer's other question with
                # `otherwise`, then had no yes for the read-back that followed, and nothing was written
                for extra in "abc":
                    audio = b"".join(b for _, b in self.bot_audio[n_before:])
                    heard = asyncio.create_task(transcribe(self.key, audio)) if audio else None
                    while heard is not None and not heard.done():  # the line stays open while it's transcribed
                        await self._send_frames(mulaw_silence(5))
                    said = heard.result() if heard is not None else ""
                    # anything else she says that isn't writing or done is answered: speech-to-text of her reply dropped
                    # its question once, the script stayed quiet and the call idled out
                    answer = yes_again if heard is not None and asked_again(said, topic) else \
                        otherwise if otherwise and (asks_other(said) or still_open(said)) else None
                    if answer is None:
                        break
                    self._keep(f"step{i + 1}{extra}.ulaw", audio)
                    result.steps.append(StepResult(label, latency, audio))
                    await self._emit("asked_again", i=i + 1, heard=said, again=answer, other=answer != yes_again)
                    n_before = len(self.bot_audio)
                    self._at = (i + 1, n_before)
                    label, latency = await self._speak([answer], n_before)
                    label = ("[ASKED AGAIN] " if answer == yes_again else "[ASKED SOMETHING ELSE] ") + label
            else:
                label, latency = await self._speak(arg if kind == "say_parts" else [arg], n_before)
            audio = b"".join(b for _, b in self.bot_audio[n_before:])
            self._keep(f"step{i + 1}.ulaw", audio)
            result.steps.append(StepResult(label, latency, audio))
            self._at = None
            await self._emit("replied", i=i + 1, label=label, latency=latency, audio=audio)
