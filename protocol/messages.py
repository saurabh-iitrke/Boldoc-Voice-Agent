"""Talker <-> control plane <-> thinker contract.

Commands flow talker -> control plane (each is a talker tool call). Every command carries
  * `epoch`    — session fencing token; commands from a superseded talker are rejected,
  * `idem_key` — hash(session, turn, tool, args); replays/duplicates never repeat side effects.
Events flow control plane -> talker/website through a per-session ordered stream (`seq`).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field
from typing_extensions import Annotated

Format = Literal["docx", "pdf", "xlsx", "pptx", "csv", "md", "txt", "html"]
Channel = Literal["sms", "web", "email", "whatsapp"]
Tier = Literal["T0", "T1", "T2", "T3"]  # T3: a long document, written by Hermes (docs/design/hermes.md)


def new_id(prefix: str) -> str:
    """Time-sortable unique id (ms timestamp + 64 random bits), e.g. 'evt_018f3c2a9b1c4d7e…'."""
    return f"{prefix}_{int(time.time() * 1000):012x}{os.urandom(8).hex()}"


def make_idem_key(session_id: str, turn_id: str, tool: str, args: Dict[str, Any]) -> str:
    canonical = json.dumps({"s": session_id, "t": turn_id, "tool": tool, "a": args}, sort_keys=True,
                           ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


# ----------------------------------------------------------------------------------------- commands
class _Command(BaseModel):
    session_id: str
    epoch: int
    turn_id: str
    idem_key: str = ""

    def with_idem_key(self):
        # what control adds on its side (the brief) never changes the key: a retried command stays a duplicate
        args = self.model_dump(exclude={"session_id", "epoch", "turn_id", "idem_key", "brief", "callback_ok",
                                        "callback_words", "research_ok"})
        return self.model_copy(update={"idem_key": make_idem_key(self.session_id, self.turn_id,
                                                                  self.cmd, args)})  # type: ignore[attr-defined]


class CreateDoc(_Command):
    cmd: Literal["create_doc"] = "create_doc"
    formats: List[Format]
    doc_lang: str = "en-IN"
    intent: str                              # what the caller wants (talker's summary = intent only)
    utterance_ids: List[str] = Field(default_factory=list)  # verbatim transcript = content source of truth
    transcript: str = ""                     # verbatim conversation so far ("Caller: …\nBolDoc: …")
    brief: Dict[str, Any] = Field(default_factory=dict)  # control adds it: a long document's scope (outline, facts)
    callback_ok: bool = False  # the caller said yes to being called back with the writer's questions (long documents)
    callback_words: Optional[str] = None  # their words when they said it (kept with the consent)
    research_ok: Optional[bool] = None  # the caller asked for web research (True) or refused it (False), unasked;
    # None: they said nothing, and it goes by the document's type (long documents)


class EditDoc(_Command):
    cmd: Literal["edit_doc"] = "edit_doc"
    doc_id: str
    base_version: int
    instruction: str
    target_block_ids: List[str] = Field(default_factory=list)
    utterance_ids: List[str] = Field(default_factory=list)
    tier_hint: Optional[Tier] = None
    transcript: str = ""
    # the caller wants the document in another language ("English mein likho"): it is rewritten whole in it, and
    # stays in it for later changes
    doc_lang: Optional[str] = None


class Answer(_Command):
    cmd: Literal["answer"] = "answer"
    question_id: str
    value: str


class Cancel(_Command):
    cmd: Literal["cancel"] = "cancel"
    task_id: str


class Undo(_Command):
    cmd: Literal["undo"] = "undo"
    doc_id: str


class Deliver(_Command):
    cmd: Literal["deliver"] = "deliver"
    doc_id: str
    channel: Channel
    recipient: str            # E.164 / email / web-session ref — must already be verified
    purpose: Literal["initial", "updated", "resend"] = "initial"


Command = Annotated[Union[CreateDoc, EditDoc, Answer, Cancel, Undo, Deliver], Field(discriminator="cmd")]


# ------------------------------------------------------------------------------------------- events
EventType = Literal[
    "task.accepted", "task.progress", "task.question", "task.completed", "task.failed", "task.cancelled",
    "delivery.submitted", "delivery.delivered", "delivery.failed", "doc.preview", "command.rejected",
    "callback.done",  # a callback has nothing left to ask: the talker thanks them and hangs up
    # talker-local notices, never stored: the call's time limit, a quiet caller once the work is done, and a
    # reminder of the writer's question
    "call.time_warning", "call.time_up", "call.idle_check", "call.idle_goodbye", "call.question_nudge", "call.reask",
]
PRIORITY: Dict[str, int] = {  # arbiter order: questions > failures > completions > progress
    "task.question": 0, "task.failed": 1, "delivery.failed": 1, "command.rejected": 1, "task.completed": 2,
    "task.cancelled": 2, "delivery.delivered": 3, "delivery.submitted": 4, "task.accepted": 4,
    "task.progress": 5, "doc.preview": 6, "call.time_up": 0, "call.time_warning": 1, "call.question_nudge": 1,
    "call.idle_goodbye": 2, "call.idle_check": 3, "call.reask": 3,
}


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: new_id("evt"))
    session_id: str
    epoch: int
    seq: int                       # strictly increasing per session (assigned by the store)
    type: EventType
    task_id: Optional[str] = None
    doc_id: Optional[str] = None
    base_version: Optional[int] = None
    head_version: Optional[int] = None
    ts: float = Field(default_factory=time.time)
    expires_at: Optional[float] = None
    payload: Dict[str, Any] = Field(default_factory=dict)

    @property
    def priority(self) -> int:
        return PRIORITY[self.type]
