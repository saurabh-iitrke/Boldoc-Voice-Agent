"""One call's link to the control plane: session + epoch, the document tools, live state, and when to speak up.

The talker never decides document facts. It sends commands (tools), receives durable events by long-poll, keeps the
latest session snapshot, and renders it into the [STATE] note sent with every LLM request. When the thinker
finishes something while the caller is quiet, the arbiter (code, not the LLM) picks the moment to say so.
"""
from __future__ import annotations

import asyncio
import contextlib
import math
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx
from loguru import logger
from pipecat.frames.frames import FunctionCallResultProperties

from protocol.messages import Event

from . import eou, wait_tone
from .agent_config import CallSettings
from .live import LiveFeed
from .logic.arbiter import Arbiter, Signals
from .logic.claims import (
    echoes,
    instruction_sentences,
    is_change_request,
    is_self_intro,
    normalized,
    repeats,
    signature,
    split_sentences,
)
from .logic.language import NAMES, LanguageTracker
from .logic.names import first_name, spoken_name
from .logic.playout import Playout
from .logic.state_block import (
    address,
    approval_line,
    build_state_block,
    days_ago,
    earlier_documents_line,
    intake_line,
    mask_number,
    phrase,
)
from .logic.turns import (
    approved,
    approves,
    asked_callback,
    asked_to_change,
    asked_to_end,
    asked_to_write,
    asks_for_research,
    declines,
    is_backchannel,
    is_greeting,
    offers_more,
    real_words,
    reply_gap_s,
    same_request,
    wants_no_calls,
)
from .plivo_stream import debug_caller
from .trace import CallTrace
from .turn_judge import (
    asks_for_change,
    by_rules,
    caller_is_done,
    classify,
    is_yes,
    reply_closes_call,
    reply_to_change,
)

# Events worth interrupting a quiet moment for. Everything else only updates the state note.
ANNOUNCE = {"task.completed", "task.failed", "task.question", "task.cancelled", "delivery.failed", "command.rejected"}
# BolDoc saying it can't write something: a refusal the [STATE] asked for has been said
_CANT = r"(?:sakti|pa(?:a|o)?ungi|paoongi)"      # "can't" / "won't be able to"
_CANT_HI = r"(?:सकती|पाऊँगी|पाउंगी)"
REFUSED_WORDS = re.compile(rf"\bnahi?n? (?:likh|bana) {_CANT}|\b(?:likh|bana) nahi?n? {_CANT}|"
                           rf"नहीं (?:लिख|बना) {_CANT_HI}|(?:लिख|बना) नहीं {_CANT_HI}|\bcan'?t (?:write|make|do)\b|"
                           r"\bcannot (?:write|make)\b|\bnot able to (?:write|make)\b|\bunable to write\b|"
                           r"sambhav nahi|संभव नहीं", re.I)
TIME_WARNING_S = 60    # say "about a minute left" this long before the call's limit
GOODBYE_GRACE_S = 12   # at the limit: hang up once the goodbye was heard, or after this long regardless
IDLE_CHECK_S = 12      # work done and the caller quiet this long: BolDoc asks if they need anything else...
IDLE_BYE_S = 10        # ...and still quiet this long after that: a goodbye, then the call ends
QUESTION_NUDGE_S = 8   # the writer's question asked and nobody has spoken since: one gentle reminder
REASK_S = 6            # BolDoc asked the caller something and heard nothing: ask again, briefly
END_GRACE_S = 8        # after end_call: hang up once the goodbye was heard, or after this long regardless
# said by BolDoc itself when the model, told to say goodbye, asks to end the call again instead
GOODBYE_LINE = {"en-IN": "Thank you for calling BolDoc. Goodbye!",
                "hinglish": "BolDoc ko call karne ke liye shukriya. Namaste!"}
# BolDoc's goodbye to a caller who has gone quiet, said as is (the call ends after it). Asked to say a goodbye, the
# model once asked its question again ("Saurabh, shall I write it?") and the call ended on that. {where}: where their
# document is, if one was written.
IDLE_GOODBYE = {"en-IN": ("It seems you're busy right now. {where}Call again any time. Goodbye!",
                          "Your document is {place}. "),
                "hinglish": ("Lagta hai aap abhi vyast hain. {where}Jab chahein, phir se call kijiye. Namaste!",
                             "Aapka document {place} hai. ")}
IDLE_PLACE = {"en-IN": {"screen": "on your screen and in your BolDoc account", "sms": "on the SMS link"},
              "hinglish": {"screen": "screen par aur aapke BolDoc account mein", "sms": "SMS link par"}}
# a callback's last line, once every question it rang about is answered
CALLBACK_THANKS = {"en-IN": "Thank you! Your {title} will come by SMS in a few minutes. Goodbye!",
                   "hinglish": "Shukriya! Aapka {title} kuch minute mein SMS pe aa jayega. Namaste!"}
# said by BolDoc itself the moment a document is accepted for writing, in a Hindi/Hinglish call: in Devanagari, because
# the voice reads Hindi written in Latin letters wrong, and the model writes Latin letters even when told not to (0 of
# 88 Hindi words in Devanagari, measured). The model isn't run after it: told it was said, and with it in context, the
# model still added its own "document abhi ban rahi hai, lagbhag 14 second bache hain" on 3 of 3 gate calls (the [STATE]
# note's time left), romanized again.
WRITING_LINE = {"hinglish": "जी, मैं अभी लिख रही हूँ। लगभग {eta} second लगेंगे{where}। तब तक कुछ और जोड़ना हो, तो बताइए।"}
WRITING_WHERE = {"hinglish": {"sms": ", और link SMS से आ जाएगा", "screen": ", और यह आपकी screen पर दिखेगा"}}
# said by BolDoc itself when a reply and its retry were both dropped (silence is worse than asking again)
SAY_AGAIN = {"en-IN": "Sorry, could you say that again?", "hinglish": "Maaf kijiye, ek baar phir boliye?"}
# said by BolDoc itself when the model calls create/edit again instead of asking for the yes (see _answer)
YES_AGAIN = {("create", "hinglish"): "Maaf kijiye, theek se sunai nahi diya. Kya main ise likh doon, haan ya nahi?",
             ("create", "en-IN"): "Sorry, I didn't catch that. Shall I write it now, yes or no?",
             ("edit", "hinglish"): "Maaf kijiye, theek se sunai nahi diya. Badal doon, haan ya nahi?",
             ("edit", "en-IN"): "Sorry, I didn't catch that. Shall I make the change, yes or no?"}
FORMAT_ALIASES = {"pdf": "pdf", "docx": "docx", "word": "docx", "doc": "docx", "xlsx": "xlsx", "excel": "xlsx",
                  "xls": "xlsx", "pptx": "pptx", "powerpoint": "pptx", "ppt": "pptx", "csv": "csv", "md": "md",
                  "markdown": "md", "txt": "txt", "text": "txt", "html": "html"}
LANGS = {"en-IN", "hi-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN", "mr-IN", "bn-IN", "gu-IN", "pa-IN", "od-IN"}
ETA_S = {"create": 15, "edit": 6, "undo": 2, "create_big": 300}  # create_big: a long document (control measures it)
ACTIVE_TASK = ("queued", "running", "awaiting_input", "committing")
# A caller waiting on the line for a long document hears how far it has got (Hermes reports each section), not "main
# likh rahi hoon... aur kuch chahiye?" over and over : at most this often, and only once BolDoc has been quiet this
# long.
PROGRESS_EVERY_S = 40.0
PROGRESS_QUIET_S = 20.0
CHECK_WAIT_S = 25      # asked to look over the new document: they're reading it, not ignoring the question
VAD_STOP_S = 0.2   # the VAD reports "stopped" this long after the caller's last sound (bot.py VADParams)
VAD_START_S = 0.2  # ...and "started" this long after their first sound (Silero default start_secs)
# A sentence left unfinished (word rules or the end-of-turn model): BolDoc waits this long in silence before answering,
# saying "हम्म" after ACK_AFTER_S. A slow speaker on a real call went on 4.0 s into a pause, after a 2.0 s hold had
# already ended.
UNFINISHED_HOLD_S = 3.5
SHORT_UNFINISHED_WORDS = 4  # the model's "unfinished" on this few words is often an answer ("बैंक मैनेजर के लिए"): 2.0 s
GREETING_GAP_S = 1.0  # "नमस्ते." then the request ~0.56 s later : BolDoc answered the greeting over it
ACK_AFTER_S = 1.2
ACK_COOLDOWN_S = 6.0
ACK_TAIL_S = 0.15
# Cut off mid-reply, a sentence counts as heard only if it had surely finished playing: at a slow speaking rate, plus
# the phone line's lag. Repeating a sentence they may have heard is better than never saying one they didn't.
HEARD_CHARS_S = 13.0
HEARD_MARGIN_S = 0.3


class ControlClient:
    def __init__(self, base_url: str, key: str):
        self.http = httpx.AsyncClient(base_url=base_url, headers={"X-Internal-Key": key},
                                      timeout=httpx.Timeout(10.0, read=30.0))

    async def open(self, session_id: str, channel: str, test: bool = False,
                   late_admit: Optional[dict] = None) -> dict:
        """Identity is not ours to claim: control looks the call up by its id (admitted on the answer webhook)."""
        r = await self.http.post("/internal/sessions", json={"session_id": session_id, "channel": channel,
                                                             "test": test, "late_admit": late_admit})
        r.raise_for_status()
        return r.json()

    async def command(self, payload: dict) -> dict:
        r = await self.http.post("/internal/commands", json=payload)
        r.raise_for_status()
        return r.json()

    async def events(self, session_id: str, after: int, wait: float = 20.0) -> dict:
        r = await self.http.get(f"/internal/sessions/{session_id}/events", params={"after": after, "wait": wait})
        r.raise_for_status()
        return r.json()

    async def end(self, session_id: str, lang: Optional[str] = None) -> None:
        """The call ended; `lang`: the language it ended in, to start the caller's next call in."""
        r = await self.http.post(f"/internal/sessions/{session_id}/end", json={"lang": lang} if lang else None)
        r.raise_for_status()

    async def intake(self, session_id: str, transcript: str, written: Optional[List[str]] = None,
                     earlier: Optional[List[str]] = None) -> dict:
        r = await self.http.post(f"/internal/sessions/{session_id}/intake",
                                 json={"transcript": transcript, "written": list(written or []),
                                       "earlier": list(earlier or [])}, timeout=12.0)
        r.raise_for_status()
        return r.json()

    async def callbacks_off(self, session_id: str, words: str) -> None:
        r = await self.http.post(f"/internal/sessions/{session_id}/callbacks-off", json={"words": words[:300]})
        r.raise_for_status()

    async def save_name(self, session_id: str, name: str) -> None:
        r = await self.http.post(f"/internal/sessions/{session_id}/caller-name", json={"name": name})
        r.raise_for_status()

    async def trace(self, session_id: str, payload: dict) -> None:
        r = await self.http.post(f"/internal/sessions/{session_id}/trace", json=payload)
        r.raise_for_status()

    async def aclose(self) -> None:
        await self.http.aclose()


# The caller means a document from an earlier call ("pichhli wali", "jo pehle banayi thi"), or says yes to BolDoc's "do
# you mean your earlier ...?".
_EARLIER = re.compile(r"pichh?l|pehl[ae]|purani|purane|last time|earlier|previous|old one|पिछ|पहल|पुरान", re.I)
# BolDoc asked, and the answer wasn't a clear yes: asked again in other words. Told to say it back again, the model
# repeated its question word for word, which the claim guard drops as a repeat.
_ASK_AGAIN = ("Not yet: their answer to your question wasn't a clear yes (a phone line can garble it). If they asked "
              "or corrected something, answer that. Otherwise ask once more, briefly and in other words, for a yes or "
              "no (\"{not_heard} {what}, {yes_or_no}?\"); don't read it all back again.")


def ask_again(what: str, family: str) -> str:
    """The "not a clear yes" instruction, with its example in the call's language."""
    return _ASK_AGAIN.format(not_heard=phrase("not_heard", family), what=what.rstrip("?"),
                             yes_or_no=phrase("yes_or_no", family))
_YES = re.compile(r"\b(?:haan|haa|ha|yes|yeah|ji|jee|sahi|wahi|wohi|correct|right|bilkul|haanji)\b|हाँ|हां|जी|सही|वही|"
                  r"बिल्कुल", re.I)


def match_document(wanted: str, docs: List[dict]) -> Optional[dict]:
    """The document whose title best matches what the caller called it ("leave wali application", "invoice")."""
    want = {w for w in wanted.lower().replace("-", " ").split() if len(w) > 2}
    best, best_score = None, 0.0
    for d in docs:
        title = str(d.get("title") or "").lower()
        if wanted.lower() in title or (title and title in wanted.lower()):
            return d
        words = {w for w in title.replace("-", " ").split() if len(w) > 2}
        score = len(want & words) / max(1, len(words)) if words else 0.0
        if score > best_score:
            best, best_score = d, score
    return best if best_score >= 0.5 else None


def transcript_of(messages: List[Any]) -> str:
    """LLM context messages -> 'Caller: …\\nBolDoc: …' (tool calls and system notes left out)."""
    lines = []
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        if isinstance(content, str) and content.strip():
            lines.append(f"{'Caller' if m['role'] == 'user' else 'BolDoc'}: {content.strip()}")
    return "\n".join(lines)


def normalize_formats(raw: Any) -> List[str]:
    items = raw if isinstance(raw, list) else [raw] if raw else []
    return list(dict.fromkeys(FORMAT_ALIASES[str(x).lower().strip(". ")] for x in items
                              if str(x).lower().strip(". ") in FORMAT_ALIASES))


def without_refusals(lines: List[str]) -> List[str]:
    """The conversation as the writer's check reads it, without BolDoc's own "I can't write that" sentences: the check
    took a wrong refusal in the transcript as policy and refused again. A request that really is refused still is:
    the refusal comes from the caller's own words."""
    out = []
    for line in lines:
        if line.startswith("BolDoc:"):
            said = re.split(r"(?<=[.?!।])\s+", line[len("BolDoc:"):].strip())
            kept = [x for x in said if not REFUSED_WORDS.search(x)]
            if not kept:
                continue
            line = "BolDoc: " + " ".join(kept)
        out.append(line)
    return out


class CallSession:
    def __init__(self, session_id: str, caller: Optional[str], channel: str, client: ControlClient):
        self.session_id, self.caller, self.channel, self.client = session_id, caller, channel, client
        self.epoch: Optional[int] = None
        self.snapshot: Dict[str, Any] = {"docs": [], "tasks": [], "verified_recipients": []}
        self.last_seq = 0
        self.ready = asyncio.Event()
        self.cfg = CallSettings()  # the agent's settings for this call (apply_settings: the published ones)
        self.arbiter = Arbiter(progress_cooldown_s=PROGRESS_EVERY_S)
        self.run_llm: Optional[Callable[[], Awaitable[None]]] = None  # set by the bot: queue an LLM turn
        # pipeline signals (monotonic clock)
        self.user_speaking = False
        self.turn_open = False
        self.llm_busy_since: Optional[float] = None
        self.bot_speaking = False
        self.last_user_speech_end = 0.0
        self.turn_words = 0          # words the caller said since BolDoc last started speaking
        self.speech_s = 0.0          # seconds the caller spoke since BolDoc last started speaking (VAD)
        self._speech_started: Optional[float] = None
        self.transcripts_seen = 0
        self.voice_at: Callable[[], float] = lambda: 0.0  # VAD onset probe (set by the bot)
        self.deferred_tool: Optional[str] = None  # a tool call put off because the caller was still talking
        self.lang = LanguageTracker()
        self.utterance: List[str] = []   # what the caller said since BolDoc last started speaking
        self.malformed_retried = False     # a dropped reply was asked for again (once per caller turn)
        self._said_again = False           # ...and when that was dropped too, "say that again" (once per turn)
        self.malformed_note: Optional[str] = None
        self.said: List[str] = []          # what BolDoc has said, normalized, greeting first: never said again
        # Of those, each reply's sentences not heard yet, and each reply's line, by reply number: the playback gate may
        # still hold an older reply when the next one starts, and a drop of it is about that reply only.
        self._unheard: Dict[int, List[str]] = {}
        self._said_text: Dict[int, List[Tuple[str, str]]] = {}  # each reply's sentences: (normalized, as said)
        self._playing: List[Tuple[str, str]] = []  # the released sentences playing now, in order (cut-offs)
        self._line_before = ""                     # BolDoc's last heard line before the one playing
        self._line_of: Dict[int, str] = {}
        self.instructions: List[str] = []  # what BolDoc has been told (tool answers, notes), normalized: never said
        self._heard_line = ""              # BolDoc's last line that played (last_bot_line goes back to it on a drop)
        self._reply_released = self._reply_dropped = False  # the reply being made: let through / dropped unheard
        self.caller_name: Optional[str] = None   # their name: saved ("My details") or said on this call
        self.callback: Optional[Dict[str, Any]] = None  # we rang them about a long document's questions: {title, n}
        self.ending = False                # end_call (or the idle goodbye): hang up once the goodbye is heard
        self.turns_heard = 0
        self.last_bot_stop = 0.0
        self._idle_stage = 0               # 1: asked "anything else?", 2: said goodbye
        self.last_caller_text = ""         # the caller's last turn, for the claim guard
        self.acted_since_turn = False      # create/edit called since the caller's last turn
        self.last_bot_line = ""
        # BolDoc's line the caller's turn answers (set when their turn ends). What they said yes to is judged against
        # it, never against a reply BolDoc is making now: a reply's words become last_bot_line as soon as the model
        # ends it, before the claim check decides and before its tool calls run, so a spoken "Okay, updating it."
        # followed by edit_document made the tool see no "Shall I change it?" at all.
        self.answered_line = ""
        self.verdict: Optional[str] = None  # complete | incomplete | wait | None (still judging)
        self.verdict_source: Optional[str] = None  # rules | eou (LiveKit's model) | classifier
        self._last_refusal: Optional[tuple] = None  # (tool, status, the caller's words) of the last "not yet"
        self._loop_asked = False                     # BolDoc asked itself after the model looped (once a turn)
        self.ack_pcm: Optional[bytes] = None  # BolDoc's "हम्म" in this call's voice (ack.py)
        self.ack_until = 0.0  # it plays until then (the transport reports no "bot speaking" for it)
        self.last_ack_at = -1e9
        self._judge_task: Optional[asyncio.Task] = None
        self._done_task: Optional[asyncio.Task] = None  # "does the caller want anything more?", once the work is done
        self.goodbye_note: Optional[str] = None         # set when they don't: this reply is the goodbye
        self.greet_note: Optional[str] = None           # the greeting, when the model says it (a picked language)
        self._judge_http: Optional[httpx.AsyncClient] = None
        self.api_key = ""                 # Sarvam key for the turn classifier (set by the bot)
        self.log_content = False  # transcripts in logs: test calls only
        # what the reply being generated/spoken is carrying
        self.announcing: Optional[Event] = None
        self.announce_at = 0.0
        self.announce_heard_start = False
        self.mentioned: List[str] = []
        self._loops: List[asyncio.Task] = []
        self.log = logger.bind(call=session_id[-8:])
        self.trace = CallTrace(session_id)
        self.playout = Playout(self.trace)  # Plivo's word on what has played (checkpoints, clears)
        self._spoke_from = 0.0             # when BolDoc's latest reply started playing
        self._reply_t0 = 0.0               # when the model's latest reply started
        self._reply_announce: Optional[str] = None  # the notice it was carrying, if any (not an answer to the caller)
        self._last_tool_at = 0.0           # when the model last called a tool
        self._reply_n = 0                  # the model's replies so far: each reply's caption has its own id
        self._caption: List[str] = []      # what this reply says, sentence by sentence, as it passes the guards
        self._caption_sent = 0             # ...and how many of those the caller's screen has
        self._yes_task: Optional[asyncio.Task] = None  # the small model's word on an answer the yes-rules can't place
        self._yes: Optional[tuple] = None  # (the caller's words, "yes" | "no" | "unclear")
        self._change_task: Optional[asyncio.Task] = None  # the small model's word on whether they asked for a change
        self._change: Optional[tuple] = None  # (the caller's words, True | False | None)
        self.live = LiveFeed("", session_id)  # replaced by the bot with a Redis-backed feed
        self.test = False
        self.late_admit: Optional[dict] = None
        self.refused = False                  # control has no admission for this call: hang up
        self.max_call_s: Optional[int] = None
        self.plan: Optional[str] = None       # trial | credits | anonymous | test (from the call record)
        self.limits: Optional[dict] = None
        self.recent_docs: List[dict] = []     # the caller's documents from earlier calls (titles only)
        self.files: List[str] = []           # files they added on the website for their next document (names)
        self._files_said = False              # BolDoc has told them it has their files
        self.question_id: Optional[str] = None  # the writer's open question ([STATE] "the document still needs")
        # The writer's questions BolDoc has asked (a reply played in full while its [STATE] showed them), the
        # question in the [STATE] of the reply being spoken, and how much of what the caller said since BolDoc last
        # spoke has gone to the writer.
        self.heard_questions: set = set()
        self.state_question: Optional[str] = None
        self.utterance_passed = 0
        self.replies_passed = 0
        self.nudged: set = set()                   # questions already reminded of once
        self.commands_in_flight = 0                # create/edit/answer requests not yet answered by control
        self.convo: List[str] = []                 # the conversation as words ("Caller: ...", "BolDoc: ...")
        self.intake: Optional[dict] = None         # the writer's checklist for the document being asked for
        self.sense: Optional[dict] = None          # does the request make sense as said ({"verdict", "to", "ask"})
        self.scope: Optional[dict] = None          # a long document (Hermes writes it): type, outline, price, time
        self.callback_ok: Optional[bool] = None    # may BolDoc call back with the writer's questions (said on call)
        self.callback_words = ""                   # what they said when they agreed (kept with the consent)
        self.research_ok: Optional[bool] = None    # the caller asked for web research (True) or refused it (False)
        self.pending_change: Optional[str] = None  # a change said back, waiting for the caller's yes (any later turn)
        self._pending_turns = 0                    # caller turns since it was said back: it lapses when they move on
        self._reasked = 0                          # its question asked again after an answer that wasn't clear
        self._change_asked_at: Optional[int] = None  # the caller turn whose words asked for a change (rules or check)
        self._change_words = ""                    # those words
        self._made_change_at: Optional[int] = None  # the caller turn whose approved change the session made itself
        self._calls_off = False                    # they asked not to be called again (recorded once)
        self._sense_shown_at: Optional[int] = None  # the caller turn its question was put to BolDoc (once a call)
        self.intake_asked: Dict[str, int] = {}     # how many turns each still-needed fact has been "ask next"
        # A document BolDoc may not write: {"document", "refusal", "refused", "allowed", "told"} (see _track_refusal)
        self.refusal: Optional[dict] = None
        self._refusal_asked = False                # this reply's [STATE] told BolDoc to say the refusal now
        self._asked_to_check = False               # BolDoc asked them to look over their new document
        self.task_eta: Dict[str, tuple] = {}       # task id -> (accepted at, about how long it takes: lo s, hi s)
        self._request_from = 0                     # where the request being worked out starts in the conversation
        self.intake_on = True                      # off: scripted A/B test calls without the writer's checklist
        self._intake_task: Optional[asyncio.Task] = None
        self.goodbye_heard = False
        self.end_call: Optional[Callable[[], Awaitable[None]]] = None  # set by the bot: hang up gracefully
        self.speak: Optional[Callable[..., Awaitable[None]]] = None  # set by the bot: say a fixed line as is
        self.vad_peak: Optional[Callable[[], Tuple[float, float]]] = None  # set by the bot: the line's loudest voice
        self.play_clip: Optional[Callable[[bytes], Awaitable[bool]]] = None  # set by the bot: a sound to the line
        self._tone_at = 0.0                        # the wait tone's last motif
        self._end_answered = self._spoke_after_end = False  # end_call answered "ending"; BolDoc spoke after that

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        try:
            r = await self.client.open(self.session_id, self.channel, test=self.test, late_admit=self.late_admit)
            self.epoch = r["epoch"]
            self.snapshot = r["snapshot"]
            self.last_seq = r["snapshot"].get("last_seq", 0)  # a resumed call doesn't re-announce old news
            adm = r.get("admission") or {}
            self.caller = adm.get("caller")
            if debug_caller(self.caller):  # the developer's own phone: words in logs and trace while debugging
                self.log_content = self.trace.content = True
            self.max_call_s = adm.get("max_call_s")
            self.plan, self.limits = adm.get("plan"), adm.get("limits")
            self.recent_docs = list(r.get("recent_docs") or [])
            self.files = [str(f) for f in r.get("files") or []][:10]
            self.caller_name = adm.get("name")
            self.callback = adm.get("callback") if adm.get("via") == "callback" else None
            self._loops = [asyncio.create_task(self._poll()), asyncio.create_task(self._arbitrate())]
            if self.max_call_s:
                self._loops.append(asyncio.create_task(self._time_limit(float(self.max_call_s))))
            self.log.info(f"session open epoch={self.epoch} docs={len(self.snapshot['docs'])} "
                          f"sms={'yes' if self.snapshot.get('verified_recipients') else 'no'} max={self.max_call_s}s")
            self.trace.add("session", epoch=self.epoch, sms=bool(self.snapshot.get("verified_recipients")),
                           max_call_s=self.max_call_s)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:  # no admission: a forged or stale stream, never a real caller's call
                self.refused = True
                self.log.warning("call not admitted by the control plane: ending")
                self.trace.add("refused")
                if self.end_call is not None:
                    await self.end_call()
            else:
                self.log.warning(f"control plane error ({e.response.status_code}); conversation only")
        except Exception as e:
            self.log.warning(f"control plane unavailable ({type(e).__name__}); conversation only")
        finally:
            self.ready.set()

    def _offer_local(self, type_: str) -> None:
        """A notice from the talker itself (not the control plane), voiced by the arbiter like any event. Asking
        again carries the question itself, so that is what gets asked again."""
        payload = {"question": self.last_bot_line} if type_ == "call.reask" else {}
        self.arbiter.offer(Event(session_id=self.session_id, epoch=self.epoch or 0, seq=0, type=type_,
                                 payload=payload), time.monotonic())
        self.trace.add("time_limit" if type_.startswith("call.time") else "notice", stage=type_)

    def _question_nudge(self, now: float) -> bool:
        """The writer's question was asked and nobody has spoken since: one gentle reminder, offering to leave it
        out, instead of silence until the writer stops waiting."""
        q = self.snapshot.get("question")
        if (q is None or q["id"] not in self.heard_questions or q["id"] in self.nudged or self.ending
                or self.bot_speaking or self.user_speaking or self.turn_open or self.announcing is not None):
            return False
        if now - max(self.last_user_speech_end or 0.0, self.last_bot_stop) < QUESTION_NUDGE_S:
            return False
        self.nudged.add(q["id"])
        return True

    def _idle_notice(self, now: float) -> Optional[str]:
        """A caller who goes quiet: if BolDoc had just asked them something, it asks again after a few seconds;
        otherwise, once the work is done, it asks whether they need anything else. Still quiet after that: a goodbye.
        """
        if (self.ending or self.bot_speaking or self.user_speaking or self.turn_open or self.turns_heard == 0
                or self.work_in_progress() or self.announcing is not None):
            return None
        quiet = now - max(self.last_user_speech_end or 0.0, self.last_bot_stop)
        # a question the caller owes an answer to is asked again soon; "anything else?" once the work is done is
        # not (someone reading their SMS gets the full wait: a gate call was hung up on 16 s after it)
        asked = (self.last_bot_line.rstrip().endswith("?") and not offers_more(self.last_bot_line)
                 and not self._asked_to_check)
        if self._idle_stage == 0 and quiet >= (self.cfg.reask_after_s if asked else CHECK_WAIT_S
                                               if self._asked_to_check else self.cfg.idle_check_after_s):
            return "call.reask" if asked else "call.idle_check"
        if self._idle_stage == 1 and quiet >= self.cfg.idle_goodbye_after_s:
            return "call.idle_goodbye"
        return None

    async def _hang_up_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        if self.end_call is not None:
            await self.end_call()

    async def _hang_up_when_heard(self, since: float) -> None:
        """Hang up once the goodbye that started playing at `since` has played to its end: Plivo's playedStream for
        the checkpoint behind it (0.6 s after its last audio went out on a line that confirms nothing)."""
        how = await self.playout.heard_since(since)
        self.trace.add("goodbye_played", how=how)
        if self.end_call is not None:
            await self.end_call()

    async def _time_limit(self, max_s: float) -> None:
        """The call's time limit: a heads-up a minute before, then a goodbye, then hang up."""
        elapsed = lambda: time.monotonic() - self.trace.started_mono  # noqa: E731
        await asyncio.sleep(max(0.0, max_s - TIME_WARNING_S - elapsed()))
        if max_s > TIME_WARNING_S:
            self._offer_local("call.time_warning")
            self.log.info("time limit: one minute left")
        await asyncio.sleep(max(0.0, max_s - elapsed()))
        self._offer_local("call.time_up")
        self.log.info("time limit reached: saying goodbye")
        deadline = time.monotonic() + GOODBYE_GRACE_S
        while time.monotonic() < deadline and not self.goodbye_heard:
            await asyncio.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
        if self.goodbye_heard:  # its last audio has gone out: hang up once it has played
            return await self._hang_up_when_heard(self._spoke_from)
        if self.end_call is not None:
            await self.end_call()

    async def end(self) -> None:
        for t in self._loops:
            t.cancel()
        if self._judge_http is not None:
            await self._judge_http.aclose()
        self.live.publish("ended")
        await self.live.close()
        if self.epoch is not None:
            try:
                await asyncio.wait_for(self.client.trace(self.session_id, self.trace.payload()), 5.0)
            except Exception as e:
                self.log.warning(f"call trace not stored: {type(e).__name__}")
            try:
                await asyncio.wait_for(self.client.end(self.session_id, self.lang.call_language()), 5.0)
            except Exception as e:
                self.log.warning(f"session end not recorded: {type(e).__name__}")
        await self.client.aclose()

    async def _poll(self) -> None:
        while True:
            try:
                r = await self.client.events(self.session_id, self.last_seq)
                self.snapshot = r["snapshot"]
                for raw in r["events"]:
                    ev = Event.model_validate(raw)
                    self.last_seq = max(self.last_seq, ev.seq)
                    self.trace.add("event", type=ev.type, seq=ev.seq, version=ev.head_version)
                    if ev.type == "task.progress" and not self.cfg.progress_lines:
                        pass  # progress lines are off (agent settings): [STATE] still shows how far it is
                    elif ev.type in ANNOUNCE or self._worth_saying(ev):
                        self.arbiter.offer(ev, time.monotonic())
                        self.log.info(f"event {ev.type} queued for the caller")
                    elif ev.type == "callback.done":
                        await self._callback_done()
                self._track_question()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log.warning(f"event poll: {type(e).__name__}")
                await asyncio.sleep(1.0)

    def _wants_wait_tone(self, now: float) -> bool:
        """The caller is waiting on the line for a long document and nobody is talking: the soft wait tone."""
        if (not wait_tone.enabled() or not self.cfg.wait_tone or self.ending or self.bot_speaking
                or self.user_speaking or self.turn_open
                or self.announcing is not None or self.llm_busy_since is not None or self.commands_in_flight
                or self.snapshot.get("question") is not None or not self.big_docs_writing()):
            return False
        quiet = now - max(self.last_user_speech_end or 0.0, self.last_bot_stop)
        return quiet >= wait_tone.QUIET_S and now - self._tone_at >= wait_tone.EVERY_S

    def _worth_saying(self, ev: Event) -> bool:
        """A long document's real progress (a section done, research under way), while its caller is on the line."""
        if ev.type != "task.progress" or self.ending:
            return False
        stage = str((ev.payload or {}).get("stage") or "")
        task = next((t for t in self.snapshot.get("tasks", []) if t["id"] == ev.task_id), None)
        return (task is not None and task.get("tier") == "T3"
                and (stage.startswith("section ") or stage == "web research"))

    async def _no_more_calls(self, words: str) -> None:
        try:
            await self.client.callbacks_off(self.session_id, words)
            self.trace.add("callbacks_off")
        except Exception as e:
            self.log.warning(f"callbacks off not recorded: {type(e).__name__}")

    async def _callback_done(self) -> None:
        """We called them about their document's questions and every one is answered: thanks, then hang up."""
        if self.ending or self.callback is None:
            return
        self.ending = True
        title = self.callback.get("title") or "document"
        line = CALLBACK_THANKS.get(self.lang.family, CALLBACK_THANKS["en-IN"]).format(title=title)
        self.trace.add("callback_done")
        self.log.info("callback: every question answered; thanking and hanging up")
        if self.speak is not None:
            self.remember_said(line)
            await self.speak(line)
        asyncio.get_running_loop().create_task(self._hang_up_after(END_GRACE_S))

    def _callback_line(self) -> Optional[str]:
        if self.callback is None:
            return None
        return (f"This is a callback: BolDoc rang the caller because the writer of their {self.callback.get('title')} "
                "needs a few answers. Ask the writer's question in [STATE], one at a time, and say each answer back "
                "in a few words. If they say it isn't a good time, say you'll send a link by SMS to answer instead "
                "and call end_call. If they ask not to be called again, say you won't and call end_call.")

    def _track_question(self) -> None:
        q = self.snapshot.get("question")
        if q is None:
            self.arbiter.drop_questions()  # answered, or the writer stopped waiting: never ask it now
        elif self.question_id is not None and q["id"] != self.question_id:
            self.arbiter.question_resolved()  # the earlier one was answered: the next one may be asked
        self.question_id = q["id"] if q else None

    def _pass_on_reply(self) -> None:
        """While the writer's question is open, each finished caller turn after BolDoc asked it goes to the writer as
        the caller said it; whether it answers the question is decided there (apps/thinker/gaps.py)."""
        q = self.snapshot.get("question")
        text = " ".join(self.utterance[self.utterance_passed:]).strip()  # said since BolDoc spoke, not yet passed on
        if q is None or self.epoch is None or q["id"] not in self.heard_questions or not text or is_backchannel(text):
            return
        self.utterance_passed = len(self.utterance)
        self.replies_passed += 1
        self.acted_since_turn = True  # the writer acts on it, a change asked in the same breath included
        asyncio.get_running_loop().create_task(self._send_reply(q["id"], text, self.replies_passed))

    async def _send_reply(self, question_id: str, text: str, n: int) -> None:
        res = await self._command({"cmd": "answer", "session_id": self.session_id, "epoch": self.epoch,
                                   "turn_id": f"reply-{question_id}-{n}", "question_id": question_id,
                                   "value": text[:400]})
        self.log.info(f"reply to the writer's question passed on: {(res or {}).get('status', 'failed')}")

    # ------------------------------------------------------------------ when to speak up
    @property
    def llm_busy(self) -> bool:
        return self.llm_busy_since is not None and time.monotonic() - self.llm_busy_since < 15.0

    def signals(self) -> Signals:
        return Signals(now=time.monotonic(), bot_speaking=self.bot_speaking or self.llm_busy,
                       user_speaking=self.user_speaking, eou_pending=self.turn_open, mode="converse",
                       last_user_speech_end=self.last_user_speech_end)

    async def _arbitrate(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            try:
                await self._arbitrate_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # never let one bad notice silence the rest of the call
                self.log.exception("arbiter loop error")

    async def _arbitrate_once(self) -> None:
        now = time.monotonic()
        if self.announcing is not None:
            if now - self.announce_at > 20.0:  # never got voiced (LLM error): fold into the next reply
                self.arbiter.mark_interrupted(self.announcing.event_id)
                self.announcing = None
            return
        if self.run_llm is None:
            return
        notice = self._idle_notice(now)
        if notice is not None:
            self._idle_stage += 1
            self._offer_local(notice)
        elif self._question_nudge(now):
            self._offer_local("call.question_nudge")
        ev = self.arbiter.decide(self.signals())
        if ev is not None:
            self.announcing, self.announce_at, self.announce_heard_start = ev, now, False
            self.log.info(f"announcing {ev.type}")
            self.trace.add("announce", type=ev.type, seq=ev.seq)
            if ev.type == "call.idle_goodbye" and self.speak is not None:
                await self.speak(self.idle_goodbye_line())  # a fixed goodbye: never the model's question again
            else:
                await self.run_llm()
        elif self._wants_wait_tone(now) and self.play_clip is not None and await self.play_clip(wait_tone.CLIP):
            if now - self._tone_at > 2 * wait_tone.EVERY_S:
                self.trace.add("wait_tone")  # a stretch of it starts (one event, not one per motif)
            self._tone_at = now

    def ask_again_after_malformed_reply(self) -> bool:
        return self.ask_again("Your last reply was not speech: it looked like a tool call written out as text. Reply "
                              "to the caller in plain words, or call the tool through the tool interface.")

    def ask_again(self, note: str) -> bool:
        """ClaimGuard dropped the whole reply: ask once more, with a [STATE] note, once per caller turn."""
        if self.malformed_retried or self.run_llm is None:
            return False
        self.malformed_retried = True
        self.malformed_note = note
        self.told(note)
        asyncio.get_running_loop().create_task(self.run_llm())
        return True

    async def nothing_said(self) -> None:
        """A reply to the caller was dropped whole and its one retry too: a short fixed line instead of silence. Once
        per caller turn; never for BolDoc's own notices (an idle check has nobody to ask) or once it's ending; and
        not while another reply is being made (it was said on top of the retry's question once) or the caller is
        talking (this line isn't held for a pause like a reply is)."""
        if self.ending:
            await self.goodbye_fallback()
            return
        if self._said_again or self.announcing is not None or self.speak is None:
            return
        if self.llm_busy_since is not None or self.turn_open or self.user_speaking:
            return
        self._said_again = True
        self.trace.add("said_again")
        await self.speak(SAY_AGAIN.get(self.lang.family, SAY_AGAIN["en-IN"]))

    async def changing_now(self) -> None:
        """The session made the change the caller said yes to, and the model can't be asked again this turn: a short
        fixed line, so they hear it's being made rather than nothing (its announcement says when it's done)."""
        if self.ending or self.speak is None or self.turn_open or self.user_speaking:
            return
        self.trace.add("said_changing")
        await self.speak(phrase("changing", self.lang.family))

    def remember_said(self, text: str) -> None:
        raw = split_sentences(text + " ")[0] or [text]
        sentences = [normalized(s) for s in raw]
        self.said += sentences
        self._unheard.setdefault(self._reply_n, []).extend(sentences)
        self._said_text.setdefault(self._reply_n, []).extend(zip(sentences, raw))

    def reply_started(self) -> int:
        """A new reply; its number goes with its frames to the playback gate (ClaimGuard stamps it)."""
        self._reply_released, self._reply_dropped = False, False
        self._reply_n += 1
        self._caption, self._caption_sent = [], 0
        for old in [n for n in self._line_of if n < self._reply_n - 8] + [n for n in self._unheard
                                                                          if n < self._reply_n - 8]:
            self._line_of.pop(old, None)  # long settled
            self._unheard.pop(old, None)
            self._said_text.pop(old, None)
        return self._reply_n

    # The caller's screen shows what BolDoc says, as she says it: only sentences that pass the guards, sent when the
    # reply starts to play and updated in place (same id) if more follow.
    def caption_said(self, sentence: str) -> None:
        self._caption.append(sentence.strip())
        if self.bot_speaking:
            self._publish_caption()

    def _publish_caption(self) -> None:
        if self._caption and len(self._caption) != self._caption_sent:
            self.live.publish("agent", text=" ".join(self._caption), id=self._reply_n)
            self._caption_sent = len(self._caption)

    def reply_heard(self, reply: Optional[int] = None) -> None:
        """The playback gate let a reply through (this one by default): the caller hears it."""
        n = self._reply_n if reply is None else reply
        self._unheard.pop(n, None)
        self._playing += self._said_text.pop(n, [])  # on its way to the caller: a cut-off may take some back
        self._line_before = self._heard_line
        if n == self._reply_n:
            self._reply_released = True
        line = self._line_of.get(n)
        if line is not None:
            self._heard_line = line
        elif n == self._reply_n:
            self._heard_line = self.last_bot_line  # its own line, once the model ends it (on_bot_line)

    def forget_unheard(self, reply: Optional[int] = None) -> List[str]:  # (its caption, never sent, goes with it)
        """The playback gate dropped a reply (this one by default) before any of it played (the caller kept talking): it
        was never said, and BolDoc's last line is still the one before it. A reply older than the one being made only
        takes its own sentences, and its line if that is still the last one."""
        n = self._reply_n if reply is None else reply
        forgotten = self._unheard.pop(n, [])
        self._said_text.pop(n, None)
        for s in forgotten:
            for i in range(len(self.said) - 1, -1, -1):
                if self.said[i] == s:
                    del self.said[i]
                    break
        if n == self._reply_n:
            self._caption, self._caption_sent = [], 0
            self.last_bot_line, self._reply_dropped = self._heard_line, True
        elif self._line_of.get(n) is not None and self.last_bot_line == self._line_of[n]:
            self.last_bot_line = self._heard_line
        return forgotten

    def no_document_yet(self) -> bool:
        return not self.snapshot.get("docs")

    def unacted_change(self) -> bool:
        """The caller asked for a change on their last turn and no tool has been called since."""
        # a change asked for (by the word rules, or the change check by meaning), a yes to the change BolDoc said back
        # ("…kar doon?" "हाँ"), or any reply to it
        return not self.acted_since_turn and (is_change_request(self.last_caller_text) or self._approved() == "change"
                                              or self._change == (self.last_caller_text, True) or self.change_waiting())

    # Is the caller asking to change the document written on this call? The word rules know some ways of saying it;
    # every other caller turn after a document exists goes to a small model, by meaning (turn_judge.asks_for_change:
    # 36/36 on the bench, ~0.2 s, in parallel with the reply).
    def _start_change_check(self) -> None:
        text, line = self.last_caller_text, self.last_bot_line
        if (not text or not self.api_key or not self.snapshot.get("docs") or is_change_request(text)
                or is_backchannel(text)):
            return
        if self._change_task is not None and not self._change_task.done():
            self._change_task.cancel()
        self._change_task = asyncio.get_running_loop().create_task(self._check_change(line, text))

    async def _check_change(self, line: str, text: str) -> None:
        if self._judge_http is None:
            self._judge_http = httpx.AsyncClient()
        t0 = time.monotonic()
        found = await asks_for_change(self._judge_http, self.api_key, line, text)
        self._change = (text, found)
        if found and text == self.last_caller_text:
            self._note_change_asked(text)
        self.trace.add("change_check", change=found, ms=round((time.monotonic() - t0) * 1000))

    def _note_change_asked(self, text: str) -> None:
        """The caller's words ask for a change (the word rules, or the check by meaning). BolDoc's say-back of it may
        come a turn or two later, after a question about what to change."""
        self._change_asked_at, self._change_words = self.turns_heard, text[:200]

    def change_suspected(self) -> bool:
        """The caller's last words ask for a change, or the check by meaning hasn't answered yet."""
        text = self.last_caller_text
        pending = self._change_task is not None and not self._change_task.done()
        return bool(text) and (is_change_request(text) or self._change == (text, True) or pending)

    async def change_requested(self, wait: float = 0.6) -> bool:
        """Whether the caller's last words ask for a change: the rules at once, else the check (waited for)."""
        text = self.last_caller_text
        if is_change_request(text) or self._approved() == "change":
            return True
        task = self._change_task
        if task is not None and not task.done():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(task), wait)
        return self._change == (text, True)

    async def reply_to_change(self, reply: str) -> Optional[str]:
        """How BolDoc's reply, with no tool called, meets the change the caller asked for: claims, asks or ignores."""
        if not self.api_key:
            return None
        if self._judge_http is None:
            self._judge_http = httpx.AsyncClient()
        t0 = time.monotonic()
        verdict = await reply_to_change(self._judge_http, self.api_key, self.last_caller_text, reply)
        self.trace.add("reply_check", verdict=verdict, ms=round((time.monotonic() - t0) * 1000))
        return verdict

    async def on_llm_failover(self, model: str, judge: Any = None) -> None:
        """The talker's LLM failed on a turn and the fallback has taken over for the rest of the call: its checks
        move too, and the turn that failed is answered now (the caller would otherwise hear nothing)."""
        self.trace.add("llm_failover", to=model)
        self.log.warning(f"talker LLM failed: the rest of the call runs on {model}")
        if judge is not None:
            self.api_key = judge
        if not self.turn_open and not self.ending and self.run_llm is not None:
            await self.run_llm()

    def apply_settings(self, cfg: CallSettings) -> None:
        """The agent's settings this call runs with, from the admin console's newest publish (or a draft under
        test): taken once, as the call starts, and kept to its end."""
        self.cfg = cfg
        self.arbiter.progress_cooldown_s = cfg.progress_every_s
        self.trace.add("agent_config", version=cfg.version)

    def guard_reply(self) -> bool:
        """This reply must be checked against what the tools did before it is heard: a document exists, nothing is being
        written, no tool has acted since the caller asked, and their words may ask for a change or answer BolDoc's
        "Shall I change it?"."""
        return (bool(self.snapshot.get("docs")) and not self.work_in_progress() and not self.acted_since_turn
                and not self.commands_in_flight and self.announcing is None
                and (self.change_suspected() or self.change_waiting()))

    async def approved_change_unmade(self) -> bool:
        """The caller has just said yes to a change BolDoc said back, and nothing has acted on it this turn."""
        if self.acted_since_turn or not self.snapshot.get("docs") or self.refusal_stands():
            return False
        await self._said_yes(wait=0.6)
        return self._approved() == "change" and not self.acted_since_turn

    async def make_approved_change(self) -> bool:
        """The change the caller said yes to, sent by the session itself when the model didn't call edit_document for
        it. What a caller approves is carried out by the call's own logic, not left to the model's tool choice. The
        editor gets the change as BolDoc said it back (what the caller approved), their own words, and the
        conversation."""
        docs = self.snapshot.get("docs") or []
        if self.epoch is None or not docs:
            return False
        doc = docs[-1]
        said_back = self.answered_line.strip()
        asked = self.pending_change if self.pending_change and self.pending_change != said_back else ""
        instruction = (f"The caller said yes to this change, as BolDoc said it back: \"{said_back}\""
                       + (f" Their request: \"{asked}\"." if asked else ""))
        payload = {"cmd": "edit_doc", "session_id": self.session_id, "epoch": self.epoch,
                   "turn_id": f"approved-{self.turns_heard}", "doc_id": doc["id"],
                   "base_version": doc["head_version"], "instruction": instruction[:500],
                   "transcript": "\n".join(self.convo)}
        self.acted_since_turn = True
        res = await self._command(payload)
        if res is None or res.get("status") not in ("accepted", "duplicate"):
            self.acted_since_turn = False
            self.log.warning(f"approved change not made: {(res or {}).get('status')} {(res or {}).get('reason') or ''}")
            return False
        self.pending_change, self._made_change_at = None, self.turns_heard
        self.log.info("edit_document -> made by the session after the caller's yes")
        self.trace.add("approved_change", by="session", status=res["status"])
        return True

    def said_something_new(self) -> bool:
        """The caller's last turn had words of their own (not just "haan" or "ok"), and no tool has acted on it."""
        return not self.acted_since_turn and real_words(self.last_caller_text) >= 2

    def _sense_pending(self) -> Optional[dict]:
        """The sense check's question, until the caller has answered it: asked once a call, so a caller who
        insists on what they said is never stuck."""
        sp = self.sense or {}
        if sp.get("verdict") != "check" or not sp.get("ask"):
            return None
        if self._sense_shown_at is not None and self.turns_heard > self._sense_shown_at:
            return None
        return sp

    def _clear_to_write(self) -> bool:
        """The checklist in hand says nothing stands in the way: allowed, nothing to ask about what was meant, every
        needed fact given. No checklist (none yet, or the last one failed) isn't clear."""
        it = self._checklist()
        if not it or it.get("allowed") is False or self._sense_pending() is not None:
            return False
        return not any(f.get("required") and not f.get("value") for f in it.get("facts") or [])

    async def _fresh_intake(self, tool: str, wait_s: float = 2.5) -> None:
        """The checklist and sense check for what the caller has just said, when one is still on its way. A new turn
        that cancels the tool call while it waits puts the call off, as in _caller_finished."""
        task = self._intake_task
        if task is not None and not task.done():
            t0 = time.monotonic()
            try:
                await asyncio.wait_for(asyncio.shield(task), wait_s)
            except asyncio.CancelledError:
                me = asyncio.current_task()
                if me is not None and me.cancelling():
                    self._note_deferred(tool, "a new turn cancelled the call")
                    raise
                # (the checklist waited for was replaced by a newer one: go on with what there is)
            except Exception:
                pass
            self.trace.add("intake_wait", ms=round((time.monotonic() - t0) * 1000))

    def change_waiting(self) -> bool:
        """A change BolDoc said back ("…26 October kar doon?") waits for the caller's yes: asked in the last line the
        caller heard, whether the model asked on its own or after edit_document's confirm_first, or earlier and not
        yet answered (pending_change). A change question only: counting a "Likh doon?" read-back too dropped a true
        "Leave Application ready hai" 13 times (reverted, 6124e64)."""
        return self.pending_change is not None or asked_to_change(self.last_bot_line, also_write=False)

    def _change_said_back(self, change: str) -> None:
        if change != self.pending_change:
            self._reasked = 0
        self.pending_change, self._pending_turns = change[:200] or "the change", 0

    def may_ask_again(self, sentence: str) -> bool:
        """The question about the waiting change may be asked again, word for word, when the caller's answer wasn't a
        clear yes or no ("How", "Give me the car"): twice at most. Asking again after a silence is what the re-ask is
        for (once per silence: _idle_notice), so its question is never a repeat to drop."""
        if self._reply_announce == "call.reask" and sentence.rstrip().endswith("?"):
            return True
        if not (self.pending_change and asked_to_change(sentence, also_write=False)) or self._reasked >= 2:
            return False
        if self.said_yes() or declines(self.last_caller_text):
            return False
        self._reasked += 1
        return True

    def _pending_line(self) -> Optional[str]:
        """A change still waiting for the caller's yes after BolDoc's last line moved on to something else."""
        if not self.pending_change or asked_to_change(self.last_bot_line, also_write=False) or self.acted_since_turn:
            return None
        return (f"a change the caller asked for still waits for their yes: \"{self.pending_change}\". It is NOT made. "
                "If they just said yes to it, call edit_document now; otherwise say it back in one short sentence and "
                f"ask \"{phrase('change', self.lang.family)}\" before anything else. Never say it is done or ready.")

    def said_before(self, sentence: str) -> bool:
        """A sentence that would repeat BolDoc: said already this call, or introducing itself again after the
        greeting."""
        return is_self_intro(sentence) or repeats(sentence, self.said)

    def echoes_instruction(self, sentence: str) -> bool:
        """A sentence that reads out what BolDoc was told to do (a tool's answer, a note) instead of doing it."""
        return echoes(sentence, self.instructions)

    def told(self, text: Optional[str]) -> None:
        """An instruction for the model (never for the caller): its sentences are never spoken."""
        if text:
            self.instructions += [s for s in instruction_sentences(text) if s not in self.instructions]

    async def _result(self, params, result: dict, **kwargs) -> None:
        """A tool's answer to the model. What it tells BolDoc to do ("say", "next") is never read out to the caller;
        the facts it gives (the SMS note, a reason) may be."""
        for key in ("say", "next"):
            self.told(result.get(key))
        await params.result_callback(result, **kwargs)

    def on_bot_started(self) -> None:
        self.greet_note = None  # said
        self._publish_caption()
        self._spoke_after_end = True
        if self.verdict != "wait":  # a turn BolDoc is answering is finished, whatever the turn judge thought
            self._pass_on_reply()
        self.bot_speaking = True
        self._spoke_from = time.monotonic()
        if self.vad_peak is not None:
            self.vad_peak()  # from here: what the caller's line carried while BolDoc spoke
        self.turn_words = 0
        self.speech_s = 0.0
        self.utterance, self.utterance_passed = [], 0
        self.verdict = None
        if self.announcing is not None:
            self.announce_heard_start = True

    def _trace_caller_peak(self) -> None:
        """How voice-like and loud the caller's line got while BolDoc spoke (the VAD's thresholds: 0.7 and its
        minimum volume): a barge-in that never happened can be told from one that was too quiet."""
        if self.vad_peak is not None and self.bot_speaking:
            conf, vol = self.vad_peak()
            self.trace.add("caller_peak", conf=round(float(conf), 2), vol=round(float(vol), 2))

    def on_bot_stopped(self) -> None:
        """A reply finished playing without being cut off: what it carried has been heard."""
        self._trace_caller_peak()
        self._playing = []
        self.bot_speaking = False
        self.last_bot_stop = time.monotonic()
        self.arbiter.progress_not_before = self.last_bot_stop + PROGRESS_QUIET_S
        if self.state_question is not None:  # the reply played in full with the writer's question in its [STATE]
            self.heard_questions.add(self.state_question)
        if self.announcing is not None and self.announcing.type == "call.idle_goodbye" and self.announce_heard_start:
            self.ending = True
        if self.ending:  # the goodbye has been sent: hang up once it has played
            asyncio.get_running_loop().create_task(self._hang_up_when_heard(self._spoke_from))
        if self.mentioned:
            self.arbiter.mentioned_in_turn(self.mentioned)
            self.mentioned = []
        if self.announcing is not None and self.announce_heard_start:
            if self.announcing.type == "call.time_up":
                self.goodbye_heard = True
            if self.announcing.type == "task.completed" and (self.announcing.head_version or 0) == 1:
                self._asked_to_check = True  # "ek baar dekh lijiye: sab sahi hai?": they may be reading it
            self.arbiter.mark_spoken(self.announcing.event_id)
            self.announcing = None

    def goodbye_playing(self) -> bool:
        """BolDoc is saying its last goodbye (the call ends after it): it plays to the end."""
        system_goodbye = (self.announcing is not None and self.announce_heard_start
                          and self.announcing.type in ("call.idle_goodbye", "call.time_up"))
        return self.bot_speaking and (self.ending or system_goodbye)

    def goodbye_on_its_way(self) -> bool:
        """BolDoc's last goodbye is playing, or said and waiting at the playback gate for the caller to pause: their
        words over it start no turn, so it is neither cut off nor dropped unplayed."""
        return self.goodbye_playing() or (self.ending and any(self._unheard.values()))

    def on_interruption(self) -> None:
        self._trace_caller_peak()
        if self.bot_speaking and self._playing:
            self._unsay_cut_off(time.monotonic() - self._spoke_from)
        self._playing = []
        self.state_question = None
        self.bot_speaking = False
        self.ack_until = 0.0  # a "हम्म" the caller talked over is gone with the rest of the audio
        self.last_bot_stop = time.monotonic()
        self.mentioned = []
        self.announcement_unheard()

    async def goodbye_fallback(self) -> None:
        """The goodbye's reply was dropped whole (a repeat, a claim): the call is ending, so a fixed goodbye, never
        silence."""
        if self._spoke_after_end or self.speak is None or self.bot_speaking:
            return
        self._spoke_after_end = True
        self.trace.add("goodbye_fallback")
        await self.speak(GOODBYE_LINE.get(self.lang.family, GOODBYE_LINE["en-IN"]))

    def _unsay_cut_off(self, played_s: float) -> None:
        """The caller cut BolDoc off: only what had surely finished playing was said. The rest was never heard, so it
        may be said again, and her last line is what they heard."""
        heard, end_s = [], 0.0
        for norm, raw in self._playing:
            end_s += len(raw) / HEARD_CHARS_S
            if end_s + HEARD_MARGIN_S <= played_s:
                heard.append(raw)
                continue
            for i in range(len(self.said) - 1, -1, -1):
                if self.said[i] == norm:
                    del self.said[i]
                    break
            self.trace.add("unsaid", sig=signature(norm), cut=True)
        if len(heard) < len(self._playing):
            self.last_bot_line = " ".join(heard) if heard else self._line_before
            self._heard_line = self.last_bot_line

    def announcement_unheard(self, reply_said_nothing: bool = False) -> None:
        """The announcement being voiced wasn't heard: the caller talked over it, or its reply said nothing (every
        sentence dropped). News (a document ready, a question from the writer) is carried by the next reply; a nudge
        about a silence (asking again, "still there?") is dropped, since the caller is talking now and the next reply
        answers them. Either way the call stops announcing."""
        if self.announcing is None or (reply_said_nothing and self._reply_announce is None):
            return  # (a reply to the caller that said nothing isn't the announcement's)
        if self.announcing.type in ("call.reask", "call.idle_check"):
            self.arbiter.mark_spoken(self.announcing.event_id)
        else:
            self.arbiter.mark_interrupted(self.announcing.event_id)
        self.announcing = None

    def on_llm_start(self) -> None:
        self.llm_busy_since = self._reply_t0 = time.monotonic()
        self._reply_announce = self.announcing.type if self.announcing is not None else None

    def on_tool_call(self) -> None:
        self._last_tool_at = time.monotonic()

    def on_llm_end(self) -> None:
        self.llm_busy_since = None

    def on_user_vad(self, speaking: bool) -> None:
        now = time.monotonic()
        self.user_speaking = speaking
        if speaking:
            self._speech_started = now - VAD_START_S
            self.trace.add("vad_start", mono=self._speech_started)
        else:
            end = now - VAD_STOP_S  # when the caller actually went quiet
            if self._speech_started is not None:
                self.speech_s += max(0.0, end - self._speech_started)
                self._speech_started = None
            self.last_user_speech_end = end
            self.trace.add("vad_stop", mono=end)

    def on_transcript(self, text: str) -> None:
        self.turn_words += len(text.split())
        self.transcripts_seen += 1
        self.utterance.append(text)
        if self.log_content:
            self.log.info(f"caller: {text}")
        self._remember_name(spoken_name(text))
        switched = self.lang.observe(text, self.last_bot_line)
        if switched:
            self.log.info(f"language: caller now speaking {switched}")
            self.trace.add("lang_switch", family=switched)

    def _remember_name(self, name: Optional[str]) -> None:
        """The caller's own name: used for the rest of the call, and saved for the next call's welcome (a name saved
        in "My details" is never overwritten)."""
        if not name or name == self.caller_name:
            return
        self.caller_name = name
        self.log.info("caller said their name" + (f": {name}" if self.log_content else ""))
        if self.epoch is not None:
            asyncio.get_running_loop().create_task(self._save_name(name))

    async def _save_name(self, name: str) -> None:
        try:
            await self.client.save_name(self.session_id, name)
        except Exception as e:
            self.log.warning(f"caller's name not saved: {type(e).__name__}")

    def on_turn_end(self) -> None:
        """The turn detector thinks the caller is done: decide (or start deciding) whether they really are."""
        self.malformed_retried = self._said_again = self._loop_asked = False
        self.turns_heard += 1
        self._idle_stage = 0
        self.answered_line = self.last_bot_line
        self.last_caller_text = " ".join(self.utterance).strip()
        self.acted_since_turn = False
        self._start_yes_check()
        self._start_change_check()
        if is_change_request(self.last_caller_text):
            self._note_change_asked(self.last_caller_text)
        if asked_callback(self.last_bot_line):  # the caller's answer to "isi number se call kar sakti hoon?"
            if approves(self.last_caller_text):
                self.callback_ok, self.callback_words = True, self.last_caller_text[:200]
            elif declines(self.last_caller_text):
                self.callback_ok = False
        # Web research goes by the document's type, unasked; a caller who says they don't want it (or do) is heard.
        wanted = asks_for_research(self.last_caller_text)
        if wanted is not None:
            self.research_ok = wanted
        if self.pending_change is not None:  # a "no" settles it; three turns on, they've moved on
            self._pending_turns += 1
            if declines(self.last_caller_text) or self._pending_turns > 3:
                self.pending_change = None
        if self.last_caller_text:
            self.convo.append(f"Caller: {self.last_caller_text}")
            self._asked_to_check = False
        if self.callback is not None and not self._calls_off and wants_no_calls(self.last_caller_text):
            self._calls_off = True  # on a callback: "mujhe call mat karna" is recorded, and kept
            asyncio.get_running_loop().create_task(self._no_more_calls(self.last_caller_text))
        self._count_asked()
        self._prepare_intake()
        if (asked_to_end(self.answered_line) and self.nothing_left() and not self.ending and self.last_caller_text
                and self.api_key):
            # BolDoc asked "Call end kar doon?" with nothing left on the call: their answer decides, and the reply
            # waits for it (before_reply), so a yes is answered with the goodbye.
            self._done_task = asyncio.create_task(self._decide_end_offer(self.last_caller_text))
        elif self.job_done() and not self.ending and self.last_caller_text and self.api_key:
            # The call's work is finished: whether the caller wants anything more is decided now, by meaning, and
            # the reply waits for it (before_reply), so a caller who is done hears the goodbye, not another offer.
            self._done_task = asyncio.create_task(self._decide_done(self.last_caller_text))
        text = " ".join(self.utterance).strip()
        verdict = by_rules(text, self.speech_s, self.last_bot_line)
        if self._judge_task is not None:
            self._judge_task.cancel()
            self._judge_task = None
        if verdict is None and self.lang.family not in ("hinglish", "en-IN"):
            # the classifier was tuned on Hindi/English.
            verdict = "complete"
        if verdict is not None or not (self.api_key or eou.ready()):
            self._set_verdict(verdict or "complete", "rules")
            return
        self.verdict = None
        self._judge_task = asyncio.create_task(self._judge(text))

    async def _judge(self, text: str) -> None:
        t0 = time.monotonic()
        try:  # LiveKit's end-of-turn model: on our utterances it held 5% of finished sentences, the classifier 33%
            found = await eou.unfinished(self.lang.family, self.last_bot_line, text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.log.warning(f"end-of-turn model: {type(e).__name__}; asking the classifier")
            found = None
        if found is not None:
            unfinished, p = found
            self._set_verdict("incomplete" if unfinished else "complete", "eou",
                              round((time.monotonic() - t0) * 1000), p=round(p, 4))
            return
        if not self.api_key:
            self._set_verdict("complete", "rules")
            return
        try:
            if self._judge_http is None:
                self._judge_http = httpx.AsyncClient()
            verdict = await classify(self._judge_http, self.api_key, self.last_bot_line, text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.log.warning(f"turn classifier: {type(e).__name__}; treating the turn as complete")
            verdict = "complete"
        self._set_verdict(verdict, "classifier", round((time.monotonic() - t0) * 1000))

    def _checklist(self) -> Optional[dict]:
        """The writer's checklist, minus facts already pressed for twice: a checklist that wrongly calls a fact required
        never stalls the call."""
        if not self.intake or not self.intake.get("facts"):
            return self.intake
        facts = [dict(f, required=False) if self.intake_asked.get(f.get("label", ""), 0) >= 2 else f
                 for f in self.intake["facts"]]
        return dict(self.intake, facts=facts)

    def _count_asked(self) -> None:
        """At each caller turn: the fact BolDoc was told to ask next has been asked once more."""
        it = self._checklist()
        missing = [f for f in (it or {}).get("facts") or [] if f.get("required") and not f.get("value")]
        if missing and it.get("allowed", True):
            label = missing[0].get("label", "")
            self.intake_asked[label] = self.intake_asked.get(label, 0) + 1

    def on_bot_line(self, text: str) -> None:
        if self._reply_dropped:  # this reply ended after the playback gate dropped it: the caller never heard it
            return
        before, self.last_bot_line = self.last_bot_line, text
        self._line_of[self._reply_n] = text
        if "?" not in text and not self.ending and self._reply_announce is None and self.last_caller_text:
            asyncio.get_running_loop().create_task(
                self._end_after_goodbye(text, before, self.last_caller_text, self._reply_t0))
        self._refusal_said(text)
        if self.files and re.search(r"\bfiles?\b|फ़ाइल|फाइल", text, re.I):
            self._files_said = True
        asked_lately = self._change_asked_at is not None and self.turns_heard - self._change_asked_at <= 2
        if asked_to_change(text, also_write=False) and (is_change_request(self.last_caller_text) or
                                                        self.pending_change or asked_lately):
            self._change_said_back(self.pending_change or text)  # asked on its own, after the caller asked for it
        if self._reply_released:
            self._heard_line = text
        self.convo.append(f"BolDoc: {text}")

    def _prepare_intake(self) -> None:
        """The writer's checklist for what the caller is asking for now (in the background; the newest turn wins): for
        every new document of the call, on what was said since the last one was started."""
        if not self.intake_on or self.epoch is None or self.ending or len(self.convo) <= self._request_from:
            return
        if self._intake_task is not None and not self._intake_task.done():
            self._intake_task.cancel()
        self._intake_task = asyncio.get_running_loop().create_task(self._run_intake(
            "\n".join(without_refusals(self.convo[self._request_from:][-40:]))))

    def _written_titles(self) -> List[str]:
        """The call's documents, for the writer's check: a change to one of them isn't a new document."""
        return [str(d.get("title") or "the document being written") for d in self.snapshot.get("docs") or []]

    def _earlier_titles(self) -> List[str]:
        """The caller's documents from earlier calls, each title once: a change to one of them isn't a new document
        (the writer's check)."""
        return list(dict.fromkeys(str(d.get("title") or "") for d in self.recent_docs if d.get("title")))[:8]

    def _new_request(self) -> None:
        """A document was started: what comes next is a new request, worked out afresh (its own checklist, sense
        check, scope, refusal, callback and research answers)."""
        if self._intake_task is not None and not self._intake_task.done():
            self._intake_task.cancel()
        self._request_from = len(self.convo)
        self.intake = self.sense = self.scope = self.refusal = None
        self.intake_asked = {}
        self._sense_shown_at = None
        self.callback_ok, self.callback_words, self.research_ok = None, "", None

    async def _run_intake(self, transcript: str) -> None:
        try:
            r = await self.client.intake(self.session_id, transcript, written=self._written_titles(),
                                         earlier=self._earlier_titles())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.log.warning(f"intake: {type(e).__name__}")
            r = {}
        it = r.get("intake")
        if it is None and (self.intake or {}).get("allowed") is False:
            return  # a refusal stands until a newer intake says otherwise
        self._track_refusal(it)
        self.intake = it  # None when it failed: no checklist is better than a stale one
        self._remember_name((it or {}).get("caller_name"))  # their own name, however they gave it (by meaning)
        self.sense = r.get("sense")
        scope = r.get("scope")
        if scope and (self.scope or {}).get("kind") != scope.get("kind"):
            self.trace.add("scope", kind=scope.get("kind"), signup=bool(scope.get("signup_needed")),
                           sections=len(scope.get("outline") or []))
        self.scope = scope
        if (self.sense or {}).get("verdict") == "check":
            self.trace.add("sense", verdict="check", text=self.sense.get("ask"))
        if it:
            missing = [f for f in it.get("facts") or [] if f.get("required") and not f.get("value")]
            self.trace.add("intake", allowed=it.get("allowed"), facts=len(it.get("facts") or []),
                           missing=len(missing), ms=r.get("ms"))

    def _track_refusal(self, it: Optional[dict]) -> None:
        """A document BolDoc may not write is said so first, once, and stays refused.

        Unclear words once made the check refuse a document nobody asked for, so a refusal is said once two checks in a
        row refuse; until then BolDoc only stops talking about writing it. Once said, it stands until two checks in a
        row allow what the caller asks for instead."""
        if not it:
            return
        r = self.refusal
        if it.get("allowed") is False and it.get("document"):
            if r is None:
                self.refusal = {"document": it["document"], "refusal": it.get("refusal") or "", "refused": 1,
                                "allowed": 0, "told": False, "names": [it["document"]]}
            else:
                r.update(document=it["document"], refusal=it.get("refusal") or r["refusal"], allowed=0,
                         refused=r["refused"] + 1, names=(r["names"] + [it["document"]])[-6:])
        elif it.get("allowed") and r is not None:
            if not r["told"]:
                self.refusal = None  # one check refused, the next didn't: nothing settled, nothing said
            elif any(same_request(it.get("document") or "", n) for n in r["names"]):
                r["allowed"] = 0     # the same document under another name ("it's only a demo"): still refused
            else:
                r.update(allowed=r["allowed"] + 1, refused=0)
                if r["allowed"] >= 2:  # they've moved on to something BolDoc can write
                    self.refusal = None
                    self.trace.add("refusal_cleared")

    def refusal_stands(self) -> Optional[dict]:
        """The refused document, once two checks in a row refused it or BolDoc has said so."""
        r = self.refusal
        return r if r is not None and (r["told"] or r["refused"] >= 2) else None

    def _refusal_line(self) -> Optional[str]:
        r = self.refusal_stands()
        if r is None:
            return None
        if not r["told"]:
            self._refusal_asked = True
            return (f"NOT ALLOWED: the caller is asking for {r['document']}, and BolDoc can't write it: "
                    f"{r['refusal']} Say so now, kindly, in one or two sentences, before anything else, and offer what "
                    "you can write instead. Don't ask about it, read anything back, give a time or a price, or offer "
                    "to write it.")
        return (f"you have told the caller BolDoc can't write {r['document']} ({r['refusal']}). If they ask again or "
                "insist (\"it's only a demo\", \"just write it\"), say kindly that it still isn't possible and offer "
                f"what you can write instead. Never agree to write it, read it back or ask "
                f"\"{phrase('write', self.lang.family)}\" for it.")

    def _refused(self) -> Optional[str]:
        """Why BolDoc may not write what is asked for (the writer's newest check, or a refusal already said), else
        None."""
        if (self.intake or {}).get("allowed") is False:
            return self.intake.get("refusal") or ""
        r = self.refusal_stands()
        return r["refusal"] if r is not None else None

    async def _confirm_refusal(self) -> None:
        """One check refused and it isn't confirmed: a fresh check decides before anything is refused, as a refusal is
        only said when two in a row refuse."""
        if (self.intake or {}).get("allowed") is False and self.refusal_stands() is None:
            self._prepare_intake()
            await self._fresh_intake("create_document", wait_s=4.0)

    def _not_allowed(self) -> dict:
        """create_document's answer for a refused request: the model says so, and from then on it stands."""
        reason = self._refused()
        if self.refusal is not None and not self.refusal["told"]:
            self.refusal["told"] = True
            self.trace.add("refusal_told", document=self.refusal["document"][:60])
        return {"status": "not_allowed", "reason": reason,
                "say": "Tell the caller kindly, in one or two sentences, that BolDoc can't write this, and offer what "
                       "it can write instead (the reason above). Nothing is written."}

    def _refusal_said(self, text: str) -> None:
        """The reply asked to carry the refusal did (or two replies have had the chance): it stands from now on."""
        r = self.refusal
        if self._refusal_asked and r is not None and not r["told"]:
            r["asked"] = r.get("asked", 0) + 1
            if REFUSED_WORDS.search(text) or r["asked"] >= 2:
                r["told"] = True
                self.trace.add("refusal_told", document=r["document"][:60])
        self._refusal_asked = False

    def asks_caller(self) -> bool:
        """BolDoc's line asks the caller something: an acknowledgement said over its end is the answer."""
        return self.last_bot_line.rstrip().endswith("?")

    def short_answer_done(self, text: str) -> bool:
        """A short reply ("FBI", "Okay No", "PDF") that the talker's rules call a whole answer: the turn may end on
        its final transcript instead of waiting out Smart Turn's silence fallback."""
        return (0 < self.speech_s <= 1.2 and not is_backchannel(text)  # "haan" is often "haan, sab sahi hai"
                and by_rules(text, self.speech_s, self.last_bot_line) == "complete")

    async def _decide_done(self, text: str) -> None:
        if self._judge_http is None:
            self._judge_http = httpx.AsyncClient()
        ready = sum(1 for d in self.snapshot.get("docs") or [] if int(d.get("head_version") or 0) > 0)
        t0 = time.monotonic()
        done = await caller_is_done(self._judge_http, self.api_key, self.last_bot_line, text, ready)
        self.trace.add("caller_done_check", done=done, job_done=True, ms=round((time.monotonic() - t0) * 1000))
        if done and not self.ending and self.job_done():
            self._say_goodbye_and_end()

    def nothing_left(self) -> bool:
        """Nothing on this call needs the caller any more: every document is written, or only long ones are being
        written (their links come by SMS), with nothing asked of them and nothing being sent."""
        if self.job_done():
            return True
        active = [t for t in self.snapshot.get("tasks") or [] if t.get("state") in ACTIVE_TASK]
        return (bool(active) and all(t.get("tier") == "T3" for t in active) and not self.commands_in_flight
                and self.snapshot.get("question") is None)

    async def _decide_end_offer(self, text: str) -> None:
        """The caller's answer to "Call end kar doon?" with nothing left on the call. A yes (the word rules, or the
        yes-check by meaning) or a caller who is done (the done check by meaning: "bye", "I'll hang up") ends it: the
        session says the goodbye and hangs up, not the model's end_call. A no keeps them on the line."""
        if declines(text):
            return
        if not approves(text):
            if self._judge_http is None:
                self._judge_http = httpx.AsyncClient()
            yes, done = await asyncio.gather(self._said_yes(wait=0.6), caller_is_done(
                self._judge_http, self.api_key, self.answered_line, text, 0))
            self.trace.add("end_offer_check", yes=yes, done=done)
            if not (yes or done):
                return
        if not self.ending and self.nothing_left():
            self.log.info("caller confirmed the end: saying goodbye")
            self._say_goodbye_and_end()

    def _say_goodbye_and_end(self) -> None:
        self.ending, self._spoke_after_end = True, False
        where = (f"still being written, and {self._big_sms_note()}" if self.big_docs_writing()
                 else self._where_it_is())
        self.goodbye_note = ("the caller wants nothing more: say a short, warm goodbye in one sentence (their "
                             f"document is {where}). Don't ask anything; the call ends after it.")
        self.log.info("caller is done: saying goodbye")
        asyncio.get_running_loop().create_task(self._hang_up_after(END_GRACE_S))  # even if it isn't heard

    async def before_reply(self) -> None:
        """Called before each talker request: waits (briefly) for a pending "is the caller done?" decision, and for
        the yes-check on an answer to "Likh doon?" the word rules couldn't place, so [STATE] says whether it was a
        yes."""
        for task, wait in ((self._done_task, 0.8), (self._yes_task, 0.6)):
            if task is None or task.done():
                continue
            with contextlib.suppress(Exception):  # too slow or failed: the reply goes on as usual
                await asyncio.wait_for(asyncio.shield(task), wait)

    # The caller's go-ahead ("Likh doon?" "haan"): the word rules decide at once; an answer they can't place, after
    # BolDoc asked for the go-ahead ("Sounds good", "Yup", "Kardo", a garbled "Hello, write it down"), goes to a small
    # model (turn_judge.is_yes: never a yes for a correction, question, "wait" or no on the bench, 36/36; ~0.16 s).
    # Only its "yes" counts; "unclear" is asked again, as before.
    def _start_yes_check(self) -> None:
        text, line = self.last_caller_text, self.last_bot_line
        if (not text or not self.api_key or approves(text) or declines(text)
                or not (asked_to_write(line) or asked_to_change(line, also_write=False) or asked_callback(line)
                        or asked_to_end(line))):
            return
        if self._yes_task is not None and not self._yes_task.done():
            self._yes_task.cancel()
        self._yes_task = asyncio.get_running_loop().create_task(self._check_yes(line, text))

    async def _check_yes(self, line: str, text: str) -> None:
        if self._judge_http is None:
            self._judge_http = httpx.AsyncClient()
        t0 = time.monotonic()
        verdict = await is_yes(self._judge_http, self.api_key, line, text)
        self._yes = (text, verdict)
        self.trace.add("yes_check", verdict=verdict, ms=round((time.monotonic() - t0) * 1000))
        if verdict == "yes" and asked_callback(line) and self.callback_ok is None:
            self.callback_ok, self.callback_words = True, text[:200]

    def said_yes(self) -> bool:
        """The caller's last words are a yes to BolDoc's request for the go-ahead: by the word rules, or the
        yes-check's "yes" for exactly these words."""
        return approves(self.last_caller_text) or self._yes == (self.last_caller_text, "yes")

    async def _said_yes(self, wait: float = 1.2) -> bool:
        task = self._yes_task
        if task is not None and not task.done():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(task), wait)
        return self.said_yes()

    def _approved(self) -> Optional[str]:
        """What the caller just said yes to ("change", "write"), by the rules or the yes-check."""
        line = self.answered_line
        kind = approved(line, self.last_caller_text)
        if kind is None and self._yes == (self.last_caller_text, "yes") and (line or "").rstrip().endswith("?"):
            kind = ("change" if asked_to_change(line, also_write=False) else
                    "write" if asked_to_write(line) else None)
        return kind

    def job_done(self) -> bool:
        """The call's work is finished: every document written, nothing being written, asked or sent. A long document
        still being written isn't: a caller who stays on the line for it is never said goodbye to on a "theek hai";
        one who wants to go says so, and end_call lets them (the link comes by SMS)."""
        docs = self.snapshot.get("docs") or []
        return (bool(docs) and all(int(d.get("head_version") or 0) > 0 for d in docs) and not self.work_in_progress()
                and not self.commands_in_flight and self.snapshot.get("question") is None)

    def _set_verdict(self, verdict: str, source: str, ms: Optional[int] = None, p: Optional[float] = None) -> None:
        self.verdict, self.verdict_source = verdict, source
        self.trace.add("turn_verdict", verdict=verdict, source=source, ms=ms, p=p)
        if verdict == "complete":
            self._pass_on_reply()
        if verdict != "complete":
            self.log.info(f"turn: caller {'paused mid-thought' if verdict == 'incomplete' else 'asked to wait'} "
                          f"({source}); holding the reply")

    def required_gap(self) -> float:
        """Silence the caller must keep before BolDoc's reply may play."""
        base = reply_gap_s(self.speech_s)
        if self.verdict == "incomplete":
            # long only where the words are clearly unfinished: the rules, or the model on a longer utterance. The
            # classifier alone called a third of finished sentences unfinished : it keeps 2.0 s
            long = self.verdict_source == "rules" or (
                self.verdict_source == "eou" and len(self.last_caller_text.split()) > SHORT_UNFINISHED_WORDS)
            return max(base, self.cfg.unfinished_hold_s if long else 2.0)
        if self.verdict == "complete" and is_greeting(self.last_caller_text):
            return max(base, GREETING_GAP_S)
        return base  # "wait" ("ek minute rukiye") deserves a quick "ji, zaroor", not silence

    def wants_ack(self, silent_s: float) -> bool:
        """Say "हम्म" now: the reply is held for an unfinished sentence and the caller has been quiet a while."""
        return (self.cfg.listening_sound and self.verdict == "incomplete" and self.ack_pcm is not None
                and silent_s >= self.cfg.listening_sound_after_s
                and not self.user_speaking and not self.bot_speaking and not self.ack_playing
                and time.monotonic() - self.last_ack_at >= ACK_COOLDOWN_S)

    @property
    def ack_playing(self) -> bool:
        return time.monotonic() < self.ack_until

    def ack_started(self, silent_s: float) -> None:
        self.last_ack_at = time.monotonic()
        # 8 kHz 16-bit audio, plus a little for the line, so a reply never starts on top of it
        self.ack_until = self.last_ack_at + len(self.ack_pcm or b"") / 16000 + ACK_TAIL_S
        self.trace.add("ack", silence_ms=round(silent_s * 1000), source=self.verdict_source)
        self.log.info(f"turn: caller paused mid-sentence ({silent_s:.1f}s); said हम्म")

    def ack_done(self) -> None:
        self.ack_until = 0.0

    async def wait_caller_done(self, max_wait_s: float = 2.5) -> bool:
        """Before a side effect: make sure the caller has finished, not just paused mid-thought. False if they kept
        talking (the LLM will be asked again with the whole utterance)."""
        seen, t0 = self.transcripts_seen, time.monotonic()
        while time.monotonic() - t0 < max(max_wait_s, self.required_gap() + 0.5):
            if self.user_speaking or self.transcripts_seen != seen:
                return False
            if self.verdict is not None and self.caller_silent_for() >= self.required_gap():
                return True
            await asyncio.sleep(0.05)
        return True

    def caller_silent_for(self) -> float:
        if self.user_speaking:
            return 0.0
        return time.monotonic() - max(self.last_user_speech_end, self.voice_at())

    def busy_writing(self) -> bool:
        return any(t.get("kind") in ("create", "edit") for t in self.snapshot.get("tasks", []))

    def work_in_progress(self) -> bool:
        return any(t.get("state") in ACTIVE_TASK for t in self.snapshot.get("tasks", []))

    def short_work_in_progress(self) -> bool:
        """Work the caller waits for on the line: anything but a long document. Hermes writes those for minutes, after
        the call if need be, and the link comes by SMS."""
        return any(t.get("state") in ACTIVE_TASK and t.get("tier") != "T3" for t in self.snapshot.get("tasks", []))

    def big_docs_writing(self) -> set:
        return {t["doc_id"] for t in self.snapshot.get("tasks", [])
                if t.get("state") in ACTIVE_TASK and t.get("tier") == "T3"}

    # ------------------------------------------------------------------ the [STATE] note
    def _etas(self) -> Dict[str, float]:
        """Seconds left on each task: from what control estimated when it took it (long documents: measured on the
        last ones written), else the usual time for its kind."""
        now = time.time()
        out: Dict[str, float] = {}
        for t in self.snapshot.get("tasks", []):
            known = self.task_eta.get(t["id"])
            if known is not None:
                out[t["id"]] = known[2] - (now - known[0])
            else:
                out[t["id"]] = (ETA_S.get("create_big" if t.get("tier") == "T3" else t["kind"], 10)
                                - (now - t.get("created_at", now)))
        return out

    def state_text(self) -> str:
        if self.epoch is None:
            return ("[STATE — the document system is unavailable right now. Nothing can be written or sent on this "
                    "call. Say so plainly and suggest calling back in a few minutes.]")
        announce_id = self.announcing.event_id if self.announcing else None
        must = [e for e in self.arbiter.must_mention() if e.event_id != announce_id]
        self.mentioned = [e.event_id for e in must]
        note, self.malformed_note = self.malformed_note, None
        self.state_question = (self.snapshot.get("question") or {}).get("id")
        # while a request is refused (or the writer's check has just refused it), nothing about writing it: no
        # checklist, no scoping, time or price, no go-ahead (see _track_refusal)
        first = self.refusal is None
        intake = intake_line(self._checklist()) if first else None
        sense = self._sense_pending() if first else None
        if sense is not None and self._sense_shown_at is None:
            self._sense_shown_at = self.turns_heard
        sense_line = (f"from the document writer: as said, this isn't something a real person would send (to: "
                      f"{sense.get('to') or 'unclear'}). Before anything else, ask: \"{sense['ask']}\" Write nothing "
                      "until the caller says what they really need.") if sense else None
        go_ahead = None if self.acted_since_turn or self.refusal_stands() else approval_line(
            self._approved(), change_waiting=self.change_waiting(),
            has_docs=bool(self.snapshot.get("docs")), family=self.lang.family)
        scope = self._scope_line() if first else None
        refusal = self._refusal_line()
        extra = [x for x in (self.lang.prompt_line(), self._callback_line(), self._plan_line(), self._earlier_line(),
                             self._name_line(), self._files_line(), refusal, sense_line, scope, intake, note,
                             go_ahead,
                             self._pending_line(), self.goodbye_note, self.greet_note) if x]
        return build_state_block(self.snapshot, mode="converse", must_mention=must, announce=self.announcing,
                                 eta_s=self._etas(), deferred_tool=self.deferred_tool,
                                 extra="\n".join(extra) or None)

    def _scope_line(self) -> Optional[str]:
        """A long document: scope it, ask about a callback, read back the outline, then write it."""
        sc = self.scope
        if not sc or sc.get("size") != "long":
            return None
        if sc.get("signup_needed"):
            return (f"a big document ({sc.get('name')}): those need a free sign-up first. Tell the caller kindly; the "
                    f"sign-up link comes by SMS (sign in with this number: Rs {self._signup_bonus()} free). Offer to "
                    "write a short "
                    "version now instead, and write it only if they want it.")
        titles = "; ".join(str(s.get("title")) for s in sc.get("outline") or [])
        asks = " ".join(f"\"{a['question']}\"" for a in sc.get("asks") or [])
        consent = ("" if self.callback_ok is not None else
                   f" Then ask on its own: {phrase('callback', self.lang.family)}")
        size = f"{sc.get('size_n')} {sc.get('unit', 'pages')}"
        hi = int(sc.get("minutes") or 5)
        lo = min(int(sc.get("minutes_lo") or hi), hi)
        takes = f"{lo} to {hi} minutes" if lo < hi else f"{hi} minutes"
        return (f"a big document: {sc.get('name')}, about {size}, written in about {takes} (the caller needn't "
                f"wait on the line), for {sc.get('price')} credits. Before writing, ask "
                f"what is still missing, one question at a time (useful if not said yet: {asks}). If they haven't "
                f"said which language it should be in, ask: {phrase('which_language', self.lang.family)}. If they "
                f"haven't said the format, ask which they want ({self._format_words(sc.get('formats'))} usually)."
                f"{consent} "
                f"Then read back the section titles ({titles}), say how long it takes and what it costs, and ask "
                f"\"{phrase('write', self.lang.family)}\". The caller may add, drop or rename sections.")

    def _signup_bonus(self) -> int:
        """What a free sign-up gives (control's pricing, sent with the call's admission)."""
        return int((self.limits or {}).get("signup_bonus") or 2000)

    @staticmethod
    def _format_words(formats) -> str:
        words = {"pdf": "PDF", "docx": "Word", "xlsx": "Excel", "pptx": "PowerPoint", "csv": "CSV"}
        return " and ".join(words.get(str(f), str(f).upper()) for f in (formats or ["pdf", "docx"]))

    def _files_line(self) -> Optional[str]:
        """Files they added on the website for their next document: BolDoc says it has them, once."""
        if not self.files:
            return None
        names = ", ".join(self.files[:4]) + (f" and {len(self.files) - 4} more" if len(self.files) > 4 else "")
        n = len(self.files)
        if self._files_said:
            return (f"the caller's {n} file{'s' if n > 1 else ''} from the website ({names}) go into the big "
                    "document they ask for now; you've told them so.")
        return (f"the caller added {n} file{'s' if n > 1 else ''} on the website for their next big document: {names}."
                f" Early on, say once that you have {'them' if n > 1 else 'it'} (\"Aapki {n} file"
                f"{'s' if n > 1 else ''} mil gayi hain: ...\") and that they go into the report, plan or deck they ask "
                "for now. A short letter or application doesn't use them.")

    def _name_line(self) -> Optional[str]:
        if not self.caller_name:
            return None
        first = first_name(self.caller_name)
        return (f"the caller's name: {self.caller_name}. Address them by it now and then "
                f"(\"{address(first, self.lang.family)}\"), not in every reply.")

    async def end_call_tool(self, params) -> None:
        """The caller is done: BolDoc says goodbye (the reply to this result), then the call ends."""
        await self.ready.wait()
        if self._end_answered:
            # Asked again instead of saying goodbye: 8 times on one gate call, 8 s of silence until the backstop hung
            # up. No more model runs; if nothing has played since, BolDoc says a goodbye itself.
            self.trace.add("tool", tool="end_call", status="ending_again", ms=0)
            await self._result(params, {"status": "ending"}, properties=FunctionCallResultProperties(run_llm=False))
            if not self._spoke_after_end and not self.bot_speaking and self.speak is not None:
                self._spoke_after_end = True
                await self.speak(GOODBYE_LINE.get(self.lang.family, GOODBYE_LINE["en-IN"]))
            return
        # Also when a create/edit was asked in this same reply: the model once called create_document and end_call
        # together, end_call ran before the new task existed, and the call ended with the document unwritten and the
        # caller's next change never heard.
        system_goodbye = self.announcing is not None and self.announcing.type in ("call.idle_goodbye",
                                                                                  "call.time_up")
        if self.goodbye_note:  # already decided (the work is done and the caller wants nothing more): just say it
            self.trace.add("tool", tool="end_call", status="ending", ms=0)
            self._end_answered, self._spoke_after_end = True, False
            return await self._result(params, {"status": "ending", "say": "Say the short goodbye now."})
        if not system_goodbye and not self.short_work_in_progress() and not self.acted_since_turn \
                and not self.commands_in_flight:
            # The caller decides, by what they mean, not by a word list : the call ends once the work is done and they
            # want nothing more. Before any document is ready, the same check stops the model ending the call on a
            # request ("chhutti 26 se 27 kar dijiye") or on its own "Likh doon?".
            if self._judge_http is None:
                self._judge_http = httpx.AsyncClient()
            ready = sum(1 for d in self.snapshot.get("docs") or [] if int(d.get("head_version") or 0) > 0)
            done = bool(self.api_key) and await caller_is_done(self._judge_http, self.api_key, self.last_bot_line,
                                                                self.last_caller_text, ready)
            self.trace.add("caller_done_check", done=done, job_done=self.job_done())
            if not done:
                return await self._answer(params, "end_call", {
                    "status": "not_now", "say": "The caller still wants something (or it isn't clear): answer what "
                    "they just said. End the call only when they want nothing more."})
        if self.short_work_in_progress() or self.acted_since_turn or self.commands_in_flight:
            return await self._answer(params, "end_call", {
                "status": "not_now", "say": "A document is still being written. Tell them it will be "
                f"{self._where_it_is()} in a few seconds, and ask if they'd like to stay on until it's ready."})
        self.ending = True
        self.trace.add("tool", tool="end_call", status="ending", ms=0)
        self._end_answered, self._spoke_after_end = True, False
        asyncio.get_running_loop().create_task(self._hang_up_after(END_GRACE_S))  # even if the goodbye isn't heard
        await self._result(params, {"status": "ending", "say": self._goodbye_say()})

    async def _end_after_goodbye(self, reply: str, before: str, caller_text: str, reply_t0: float) -> None:
        """BolDoc said goodbye to a caller who wants nothing more, but the model didn't call end_call : the call ends
        once that goodbye has played, as end_call would have ended it. Decided by meaning, and only when nothing is
        being written, asked or sent."""
        await asyncio.sleep(0.6)  # a tool call the model made with this reply arrives just after it
        if (self._last_tool_at >= reply_t0 or self.ending or self.work_in_progress() or self.commands_in_flight
                or self.acted_since_turn or self.snapshot.get("question") is not None or self.turn_open
                or self.user_speaking or self.last_caller_text != caller_text or not self.api_key):
            return
        if self._judge_http is None:
            self._judge_http = httpx.AsyncClient()
        t0 = time.monotonic()
        closes = await reply_closes_call(self._judge_http, self.api_key, before, caller_text, reply)
        self.trace.add("goodbye_check", ends=closes, ms=round((time.monotonic() - t0) * 1000))
        if not closes or self.ending or self.last_caller_text != caller_text or self.turn_open:
            return
        self.ending = True
        self.log.info("BolDoc said goodbye without ending the call: it ends once the goodbye has played")
        if not self.bot_speaking and self._spoke_from >= reply_t0:  # it has already played (a short "Bye!"); one
            # still to start ends the call when it stops playing (on_bot_stopped, now that `ending` is set)
            asyncio.get_running_loop().create_task(self._hang_up_when_heard(self._spoke_from))
        asyncio.get_running_loop().create_task(self._hang_up_after(END_GRACE_S + len(reply) / 12.0))  # unheard too

    async def set_language_tool(self, params) -> None:
        """The caller asked BolDoc to talk in another language, in words the rules didn't catch ("can we continue
        in Hindi?"): from now on BolDoc talks in it, in that language's voice (switched before the reply plays)."""
        await self.ready.wait()
        code = str((params.arguments or {}).get("language") or "")
        if code not in NAMES:
            return await self._answer(params, "set_language", {
                "status": "unknown_language", "say": "That isn't one of BolDoc's languages: reply in the language "
                "they asked for if you can, else say kindly which languages you speak."})
        switched = self.lang.choose(code)
        self.trace.add("tool", tool="set_language", status="ok", ms=0)
        if switched:
            self.log.info(f"language: the caller asked for {switched}")
            self.trace.add("lang_switch", family=switched, source="model")
        name = "Hindi or Hinglish, the way they mix it" if code == "hi-IN" else NAMES[code]
        await self._result(params, {"status": "ok", "language": NAMES[code],
                                    "say": f"From now on reply in {name}. Answer what they said."})

    def _goodbye_say(self) -> str:
        """What the goodbye carries: where the document is, or that a long one still being written comes by SMS."""
        if self.big_docs_writing():
            return ("Say a short, warm goodbye in one sentence: thank them, and say their document is still being "
                    f"written and {self._big_sms_note()}.")
        return ("Say a short, warm goodbye in one sentence: thank them, and if a document was written, remind them "
                f"it's {self._where_it_is()}.")

    def last_document_title(self, within_days: int = 30) -> Optional[str]:
        """The caller's most recent earlier document, if it's recent enough to offer in the welcome."""
        from datetime import datetime, timedelta, timezone
        for d in self.recent_docs[:1]:
            try:
                fresh = datetime.now(timezone.utc) - datetime.fromisoformat(str(d.get("updated_at")))
            except (TypeError, ValueError):
                return None
            return d.get("title") if fresh <= timedelta(days=within_days) else None
        return None

    def _earlier_line(self) -> Optional[str]:
        return earlier_documents_line(self.recent_docs, {d["id"] for d in self.snapshot.get("docs") or []})

    def _plan_line(self) -> Optional[str]:
        """The trial's limits, so a refusal never surprises the caller (mentioned only when it matters)."""
        if self.plan != "trial" or not self.limits:
            return None
        minutes = round((self.max_call_s or 300) / 60)
        return (f"free trial call: up to {minutes} minutes, {self.limits['docs_per_call']} new documents, "
                f"{self.limits['edits_per_doc']} changes per document. Mention this only if the caller asks for more "
                f"than that.")

    # ------------------------------------------------------------------ tools
    async def _command(self, payload: dict) -> Optional[dict]:
        t0 = time.monotonic()
        self.commands_in_flight += 1
        try:
            # shielded: if the caller barges in mid-request, the command still lands (it is idempotent anyway)
            res = await asyncio.shield(self.client.command(payload))
        except Exception as e:
            self.log.warning(f"command {payload['cmd']} failed: {type(e).__name__}")
            self.trace.add("tool", tool=payload["cmd"], status="error", ms=round((time.monotonic() - t0) * 1000))
            return None
        finally:
            self.commands_in_flight -= 1
        self.trace.add("tool", tool=payload["cmd"], status=res.get("status"), reason=res.get("reason"),
                       task=res.get("task_id"), ms=round((time.monotonic() - t0) * 1000))
        self.snapshot = res.get("snapshot") or self.snapshot
        if res.get("status") in ("accepted", "duplicate"):
            self.deferred_tool = None
        return res

    def idle_goodbye_line(self) -> str:
        """The goodbye to a caller who has gone quiet, in the call's language, with where their document is."""
        family = self.lang.family if self.lang.family in IDLE_GOODBYE else "en-IN"
        line, doc = IDLE_GOODBYE[family]
        written = any(int(d.get("head_version") or 0) > 0 for d in self.snapshot.get("docs") or [])
        place = IDLE_PLACE[family]["screen" if self.snapshot.get("on_screen") else "sms"]
        return line.format(where=doc.format(place=place) if written else "")

    async def _in_context_after_call(self, params, line: str, wait_s: float = 3.0) -> None:
        """BolDoc's own words, into the model's context right after this tool call, so the model run on its result goes
        on from them. Pipecat records the call when its frame reaches the end of the pipeline (in a few ms, or once
        audio ahead of it has played): a line added before that would sit ahead of the call."""
        ctx = params.context
        if not hasattr(ctx, "add_message"):
            return

        def recorded() -> bool:
            return any(isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id") == params.tool_call_id
                       for m in ctx.get_messages()[-3:])
        until = time.monotonic() + wait_s
        while not recorded():
            if time.monotonic() >= until:
                self.log.warning("writing line: the tool call isn't in the context yet; the line is left out of it")
                return
            await asyncio.sleep(0.02)
        ctx.add_message({"role": "assistant", "content": line})

    def writing_line(self, eta_s: float) -> Optional[str]:
        """BolDoc's own words when a document starts being written, where the model's would be misread (a Hindi or
        Hinglish call): how long it takes, to the nearest 5 seconds, and where the link goes."""
        if self.lang.family not in WRITING_LINE:
            return None
        if self.snapshot.get("on_screen"):
            where = WRITING_WHERE[self.lang.family]["screen"]
        else:
            where = WRITING_WHERE[self.lang.family]["sms"] if self.snapshot.get("verified_recipients") else ""
        return WRITING_LINE[self.lang.family].format(eta=max(5, 5 * round(eta_s / 5)), where=where)

    def _where_it_is(self) -> str:
        """Where the caller finds their document: a browser call's caller watches it on screen."""
        if self.snapshot.get("on_screen"):
            return "on their screen and in their BolDoc account"
        return "on the SMS link"

    @staticmethod
    def _minutes(lo_s: float, hi_s: float) -> str:
        """How long a long document takes, as said: "about 3 to 5 minutes"."""
        lo = max(1, round(lo_s / 60))
        hi = max(lo + 1, math.ceil(hi_s / 60))
        return f"about {lo} to {hi} minutes"

    def _big_sms_note(self) -> str:
        """Where a long document goes when it's ready if the caller doesn't wait for it."""
        if self.snapshot.get("verified_recipients"):
            where = ", and it stays in their BolDoc account" if self.snapshot.get("on_screen") else ""
            return f"the link comes by SMS as soon as it's ready{where}"
        return "it will be in their BolDoc account as soon as it's ready"

    def _sms_note(self) -> str:
        if self.snapshot.get("on_screen"):
            return ("it appears on the caller's screen as it is written, with downloads; an SMS goes only if they tap "
                    "\"Send to my phone\" there, so don't promise one")
        nums = self.snapshot.get("verified_recipients") or []
        if nums:
            return f"the link goes by SMS to the number ending {mask_number(nums[0])[-4:]} when it is ready"
        return "no SMS is possible on this call (no mobile number); do not promise one"

    def _note_deferred(self, tool: str, why: str = "the caller kept talking") -> None:
        self.deferred_tool = tool
        self.log.info(f"{tool} deferred: {why}")
        self.trace.add("tool_deferred", tool=tool)

    async def _defer(self, params, tool: str) -> None:
        self._note_deferred(tool)
        # No model run for this result: the caller is still talking, and the end of their turn runs it anyway. A
        # follow-up run here only produced silence and cost an LLM call per deferral.
        await self._result(params, {
            "status": "not_started", "reason": "the caller was still speaking",
            "next": f"Say nothing now. When the caller has finished, call {tool} again with everything they said."},
            properties=FunctionCallResultProperties(run_llm=False))

    async def _answer(self, params, tool: str, result: dict, ask: Optional[str] = None) -> None:
        """A tool call answered here, with no command sent (nothing open to change, already writing, unavailable):
        traced like a command, so the call report shows what the model asked for and why nothing happened.

        `ask`: what to put to the caller for a "not yet" (a yes, a missing fact). The same "not yet" again for the same
        words means the model called the tool again instead of asking. Then BolDoc asks itself with the fixed line, and
        the model isn't run again until the caller answers."""
        self.trace.add("tool", tool=tool, status=result.get("status"), ms=0)
        key = (tool, result.get("status"), self.last_caller_text)
        if ask is not None and key == self._last_refusal:
            self.trace.add("tool_loop_stopped", tool=tool, status=result.get("status"))
            self.log.warning(f"llm: {tool} again without asking ({result.get('status')}); BolDoc asks itself")
            await self._result(params, result, properties=FunctionCallResultProperties(run_llm=False))
            if not self._loop_asked and self.speak is not None:
                self._loop_asked = True
                self.remember_said(ask)
                await self.speak(ask)
            return
        self._last_refusal = key if ask is not None else None
        await self._result(params, result)

    def _yes_again(self, kind: str) -> str:
        return YES_AGAIN.get((kind, self.lang.family), YES_AGAIN[(kind, "en-IN")])

    async def create_document(self, params) -> None:
        await self.ready.wait()
        self.acted_since_turn = True
        args = params.arguments or {}
        if self.epoch is None:
            return await self._answer(params, "create_doc", {"status": "unavailable"})
        await self._confirm_refusal()
        if self._refused() is not None:  # control refuses it too
            return await self._answer(params, "create_doc", self._not_allowed())
        running = [t for t in self.snapshot.get("tasks", []) if t["kind"] == "create"]
        if running:
            return await self._answer(params, "create_doc", {
                "status": "already_writing", "eta_seconds": max(3, int(self._etas().get(running[0]["id"], 10)))})
        if not await self._caller_finished("create_document"):
            return await self._defer(params, "create_document")
        # Don't hurry : the checklist for what was just said, then a request that makes sense, every needed fact, and
        # only then the read-back and its yes. A yes to a read-back of a complete checklist doesn't wait for the next
        # one; anything said along with the yes reaches the writer in the transcript.
        if not (asked_to_write(self.answered_line) and await self._said_yes() and self._clear_to_write()):
            await self._fresh_intake("create_document")
        await self._confirm_refusal()
        if self._refused() is not None:
            self.acted_since_turn = False
            return await self._answer(params, "create_doc", self._not_allowed())
        sense = self._sense_pending()  # the checklist and the sense check are about this request
        if sense is not None:
            if self._sense_shown_at is None:
                self._sense_shown_at = self.turns_heard
            self.acted_since_turn = False
            return await self._answer(params, "create_doc", {
                "status": "check_first",
                "say": f"Not yet: as said, nobody would send this. First ask the caller \"{sense['ask']}\" and write "
                       "once they have said what they really need."}, ask=str(sense["ask"]))
        missing = [f for f in (self._checklist() or {}).get("facts") or [] if f.get("required") and not f.get("value")]
        if missing and (self.intake or {}).get("document"):
            self.acted_since_turn = False
            first = missing[0]
            return await self._answer(params, "create_doc", {
                "status": "ask_first", "missing": [f.get("label") for f in missing],
                "say": f"Not yet: the writer still needs {', '.join(str(f.get('label')) for f in missing)}. Ask now: "
                       f"\"{first.get('question') or first.get('label')}\". Write once it is given, or if the caller "
                       "says to write without it."}, ask=str(first.get("question") or first.get("label")))
        if not (asked_to_write(self.answered_line) and await self._said_yes()):
            self.acted_since_turn = False  # nothing is being written: "likh rahi hoon" stays a false claim
            return await self._answer(params, "create_doc", {
                "status": "confirm_first", "say": ask_again(phrase("write", self.lang.family), self.lang.family)
                if asked_to_write(self.answered_line) else
                "Not yet: nothing is written until the caller says yes to your read-back. If they asked or "
                "corrected something, answer that first. Then say back in one or two sentences what the "
                "document will say (who it is to, what for, the key facts, whose name it is from) and ask "
                f"\"{phrase('write', self.lang.family)}\"."}, ask=self._yes_again("create"))
        lang = args.get("language") if args.get("language") in LANGS else "en-IN"
        payload = {"cmd": "create_doc", "session_id": self.session_id, "epoch": self.epoch,
                   "callback_ok": bool(self.callback_ok),
                   "callback_words": self.callback_words if self.callback_ok else None,
                   "research_ok": self.research_ok,
                   "turn_id": params.tool_call_id or f"t{time.time_ns()}",
                   "formats": normalize_formats(args.get("formats")) or ["pdf", "docx"], "doc_lang": lang,
                   "intent": str(args.get("summary") or "")[:500],
                   "transcript": transcript_of(params.context.get_messages())}
        res = await self._command(payload)
        if res is None:
            return await self._result(params, {"status": "unavailable"})
        self.log.info(f"create_document -> {res['status']} {res.get('reason') or ''}")
        if res["status"] in ("accepted", "duplicate"):
            self._new_request()  # what the caller asks for next is worked out afresh
            used = [str(f) for f in res.get("files_used") or []]
            if used:  # the files they added on the website went to this document
                self.files = []
            hi = float(res.get("eta_s") or 15)
            lo = float(res.get("eta_lo_s") or hi)
            if res.get("task_id"):
                self.task_eta[res["task_id"]] = (time.time(), lo, hi)
            if res.get("tier") == "T3":  # minutes, not seconds: the caller needn't wait on the line
                return await self._result(params, {
                    "status": "started", "done": False, "takes": self._minutes(lo, hi), "sms": self._big_sms_note(),
                    **({"files_used": used} if used else {}),
                    "say": "Tell the caller you are writing it now" + (", using the files they added" if used else "")
                           + f" and that it takes {self._minutes(lo, hi)}. " + (
                               f"Then ask, in these words: \"{phrase('end_call', self.lang.family)}\" "
                               f"({self._big_sms_note()}; they may also stay on the line until it's ready). "
                               if self.cfg.offer_hang_up else f"{self._big_sms_note().capitalize()}. ")
                           + "It is NOT ready yet."})
            line = self.writing_line(hi) if res["status"] == "accepted" and self.speak is not None else None
            if line is not None:
                # Said at once, in the voice's own script, and put in the model's context as its own words right after
                # this call; the model isn't run on the result (it said the line again, see WRITING_LINE). It speaks
                # next for the ready notice or the caller's next words.
                self.remember_said(line)
                self.convo.append(f"BolDoc: {line}")
                await self.speak(line, to_context=False)
                await self._in_context_after_call(params, line)
                where = (" and that it appears on their screen" if self.snapshot.get("on_screen") else
                         " and that the link comes by SMS" if self.snapshot.get("verified_recipients") else "")
                return await self._result(params, {
                    "status": "started", "done": False, "eta_seconds": int(hi), "sms": self._sms_note(),
                    **({"files_used": used} if used else {}), "said": line,
                    "say": f"You have just told the caller you are writing it, how long it takes{where} and that they "
                           "can add anything meanwhile (in the words under \"said\"): don't say any of that again" +
                           (", but do say you are using the files they added" if used else "") +
                           ". It is NOT ready yet."},
                    **({} if used else {"properties": FunctionCallResultProperties(run_llm=False)}))
            return await self._result(params, {
                "status": "started", "done": False, "eta_seconds": int(hi), "sms": self._sms_note(),
                **({"files_used": used} if used else {}),
                "say": "Tell the caller you are writing it now and about how long it takes" +
                       (", using the files they added" if used else "") + ". It is NOT ready yet."})
        say = {"signup_needed": "Tell the caller kindly that a document this big needs a free sign-up: the link has "
                                f"come by SMS (sign in with this number: Rs {self._signup_bonus()} free). Offer to "
                                "write a short "
                                "version now instead. Nothing is written.",
               "low_balance": "Tell the caller this big document costs more than their balance; they can add balance "
                              "on boldoc.in. Offer to write a short version now instead. Nothing is written."}
        out = {"status": "not_started", "reason": res.get("reason")}
        if res.get("reason") in say:
            out["say"] = say[res["reason"]]
        await self._result(params, out)

    async def edit_document(self, params) -> None:
        await self.ready.wait()
        self.acted_since_turn = True
        args = params.arguments or {}
        docs = self.snapshot.get("docs") or []
        if self.epoch is None:
            return await self._answer(params, "edit_doc", {"status": "unavailable"})
        if self._made_change_at == self.turns_heard:  # the session already made the change they said yes to
            return await self._answer(params, "edit_doc", {
                "status": "started", "done": False,
                "say": "The change the caller said yes to is being made now. It is NOT done yet; you will say when it "
                       "is."})
        wanted = str(args.get("document") or "").strip()
        # This call's document by default. An earlier call's only when named: never guessed (the gate's "change the
        # dates" once landed on an older leave application because this call's was never created).
        doc = match_document(wanted, docs + self.recent_docs) if wanted else (docs[-1] if docs else None)
        if doc is not None and not any(d["id"] == doc["id"] for d in docs) and not self._means_earlier(doc):
            title = str(doc.get("title") or "")
            return await self._answer(params, "edit_doc", {
                "status": "confirm_first", "earlier_document": title,
                "say": f"Before changing anything, ask the caller whether they mean their earlier \"{title}\" "
                       f"({days_ago(doc.get('updated_at'))}); change it only after they say yes. If they mean the "
                       "document they are making on this call, it isn't written yet: call create_document first."},
                ask=f"Kya aap apne pehle wale \"{title}\" ki baat kar rahe hain?" if self.lang.family == "hinglish"
                else f"Do you mean your earlier \"{title}\"?")
        if doc is None:
            if self.recent_docs:
                return await self._answer(params, "edit_doc", {
                    "status": "which_document", "open_on_this_call": [d["title"] for d in docs],
                    "earlier_documents": list(dict.fromkeys(d["title"] for d in self.recent_docs)),
                    "say": "Nothing by that name is open on this call. If they mean an earlier document, confirm its "
                           "title and call edit_document with document set to it. If they are describing a new "
                           "document, call create_document."})
            return await self._answer(params, "edit_doc", {"status": "no_document_yet"})
        if not await self._caller_finished("edit_document"):
            return await self._defer(params, "edit_document")
        # "do you mean your earlier Leave Application?": only about an earlier call's document. This call's own title in
        # "Leave Application ready hai… koi aur document chahiye?" let "chhutti 26 se 27 kar dijiye" change it with
        # nothing said back.
        earlier = not any(d["id"] == doc["id"] for d in docs)
        asked_which = earlier and doc["title"].lower() in self.answered_line.lower() and \
            self.answered_line.rstrip().endswith("?")
        if not ((asked_to_change(self.answered_line) or asked_which) and await self._said_yes()):
            # "…kar doon?" answered yes; or "do you mean your earlier Leave Application?" answered yes, which
            # already confirms the change they described
            self.acted_since_turn = False
            self._change_said_back(str(args.get("instruction") or "").strip())
            return await self._answer(params, "edit_doc", {
                "status": "confirm_first", "say": ask_again(phrase("change_again", self.lang.family),
                                                            self.lang.family) if asked_to_change(
                    self.answered_line) else
                "Not yet: say the change back in one short sentence and ask "
                f"\"{phrase('change', self.lang.family)}\" ({phrase('change_back', self.lang.family)}); change it "
                "only after they say yes. If they asked something, answer that first."}, ask=self._yes_again("edit"))
        lang = args.get("language") if args.get("language") in LANGS else None
        await self._send_edit(params, doc, str(args.get("instruction") or ""), lang)

    def _means_earlier(self, doc: dict) -> bool:
        said, title = self.last_caller_text, str(doc.get("title") or "").lower()
        asked = bool(title) and title in self.answered_line.lower() and self.answered_line.rstrip().endswith("?")
        return bool(_EARLIER.search(said)) or (asked and bool(_YES.search(said)))

    async def _caller_finished(self, tool: str) -> bool:
        """wait_caller_done, also when Pipecat cancels the tool call because the caller started a new turn: either way
        the request is put off, and BolDoc is told to make it again."""
        try:
            return await self.wait_caller_done()
        except asyncio.CancelledError:
            self._note_deferred(tool, "a new turn cancelled the call")
            raise

    async def _send_edit(self, params, doc: dict, instruction: str, lang: Optional[str] = None) -> None:
        payload = {"cmd": "edit_doc", "session_id": self.session_id, "epoch": self.epoch,
                   "turn_id": params.tool_call_id or f"t{time.time_ns()}", "doc_id": doc["id"],
                   "base_version": doc["head_version"], "instruction": instruction[:500],
                   "transcript": transcript_of(params.context.get_messages()),
                   **({"doc_lang": lang} if lang else {})}  # the whole document, rewritten in another language
        res = await self._command(payload)
        if res is None:
            return await self._result(params, {"status": "unavailable"})
        self.log.info(f"edit_document -> {res['status']} {res.get('reason') or ''}")
        if res["status"] in ("accepted", "duplicate"):
            self.pending_change = None  # made: nothing waits any more
            still_writing = doc["head_version"] == 0
            return await self._result(params, {
                "status": "will_be_included_in_the_draft" if still_writing else "started", "done": False,
                "eta_seconds": res.get("eta_s") or 6,
                "say": "Tell the caller you are making the change now. It is NOT done yet; you will say when it is."})
        await self._result(params, {"status": "not_done", "reason": res.get("reason")})
