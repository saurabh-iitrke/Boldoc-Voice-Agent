"""Orchestrator core: the single source of truth for sessions, documents, tasks and deliveries.

Pure domain logic over a Store (in-memory for tests; Postgres in production, one transaction per call).
Guarantees (property-tested in tests/control/test_orchestrator_properties.py):
  * fencing   — commands from a superseded talker epoch never mutate state
  * idempotent — a replayed command (same idem_key) returns the original result, no new side effects
  * no lost edits — every accepted instruction lives in exactly one task (queue, inbox or committed work)
  * single writer — at most one active task per document; commits are compare-and-swap on head_version
  * crash-safe — expired worker leases requeue the task; stale workers cannot commit
  * exactly-once delivery per (doc, recipient, purpose[, version])
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from docengine.model import Document
from protocol.fsm import ConvState, TaskState, check_task_transition
from protocol.messages import Answer, Cancel, CreateDoc, Deliver, EditDoc, Event, Tier, Undo, new_id

ACTIVE = frozenset({TaskState.RUNNING, TaskState.AWAITING_INPUT, TaskState.COMMITTING})
MAX_RESENDS = 3


@dataclass
class Session:
    id: str
    channel: str
    caller: Optional[str]
    epoch: int = 1
    state: ConvState = ConvState.DISCLOSE
    verified_recipients: List[str] = field(default_factory=list)
    user_id: Optional[int] = None     # the caller's account (from the call record)
    plan: Optional[str] = None        # trial | credits | anonymous | test: what this call may do
    attached: List[str] = field(default_factory=list)  # the caller's documents from earlier calls, opened on this one


@dataclass
class DocRecord:
    id: str
    session_id: str                   # the call that created it
    head_version: int = 0
    last_delivered_version: Dict[str, int] = field(default_factory=dict)  # recipient -> version
    formats: List[str] = field(default_factory=lambda: ["pdf"])
    doc_lang: str = "en-IN"
    title: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    owner_user_id: Optional[int] = None  # whose document it is: any later call of theirs may change it
    long: bool = False                   # written by Hermes (T3): never redrafted whole for a small change


@dataclass
class Question:
    id: str
    task_id: str
    text: str
    default: str
    expires_at: float
    answer: Optional[str] = None
    session_id: Optional[str] = None  # the call it is asked on, when not the task's own (a callback, a later call)

    def asked_on(self, task: Task) -> str:
        return self.session_id or task.session_id


@dataclass
class Task:
    id: str
    session_id: str
    doc_id: str
    kind: str                      # create | edit | undo
    tier: Tier
    base_version: int
    created_at: float
    instructions: List[str] = field(default_factory=list)   # accepted work (incl. consumed inbox)
    utterance_ids: List[str] = field(default_factory=list)
    inbox: List[str] = field(default_factory=list)          # arrived while running, not yet consumed
    state: TaskState = TaskState.QUEUED
    attempts: int = 0
    worker_id: Optional[str] = None
    lease_until: Optional[float] = None
    question_id: Optional[str] = None
    committed_version: Optional[int] = None
    error: Optional[str] = None
    transcript: str = ""           # latest verbatim conversation the thinker should read
    big: Dict[str, Any] = field(default_factory=dict)  # T3: brief, Hermes run, questions asked and answers


@dataclass
class Delivery:
    id: str
    key: Tuple
    doc_id: str
    version: int
    channel: str
    recipient: str
    purpose: str
    status: str = "pending"       # pending -> sending -> submitted -> delivered | failed
    provider_ref: Optional[str] = None
    error: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0
    session_id: Optional[str] = None  # the call it belongs to (hears about it); default: the doc's own call


DELIVERY_RANK = {"pending": 0, "sending": 1, "submitted": 2, "delivered": 3, "failed": 3}


@dataclass
class CommandResult:
    status: str                    # accepted | duplicate | rejected
    task_id: Optional[str] = None
    doc_id: Optional[str] = None
    delivery_id: Optional[str] = None
    merged_into: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class CommitResult:
    status: str                    # committed | conflict | rejected
    version: Optional[int] = None
    follow_up_task_id: Optional[str] = None
    reason: Optional[str] = None


class MemoryStore:
    """Dict-backed store. The Postgres store implements the same attributes/operations transactionally."""

    def __init__(self) -> None:
        self.sessions: Dict[str, Session] = {}
        self.docs: Dict[str, DocRecord] = {}
        self.versions: Dict[Tuple[str, int], Document] = {}
        self.version_meta: Dict[Tuple[str, int], dict] = {}   # {"at": ts, "changes": [...], "task_id": ...}
        self.tasks: Dict[str, Task] = {}
        self.questions: Dict[str, Question] = {}
        self.idem: Dict[str, CommandResult] = {}
        self.deliveries: Dict[Tuple, Delivery] = {}
        self.events: Dict[str, List[Event]] = {}

    def append_event(self, session_id: str, epoch: int, type_: str, **kw) -> Event:
        stream = self.events.setdefault(session_id, [])
        ev = Event(session_id=session_id, epoch=epoch, seq=len(stream) + 1, type=type_, **kw)
        stream.append(ev)
        return ev


class Orchestrator:
    def __init__(self, store: Optional[MemoryStore] = None, clock: Callable[[], float] = time.monotonic,
                 coalesce_window_s: float = 2.5, lease_s: float = 30.0, max_attempts: int = 3,
                 id_factory: Callable[[str], str] = new_id):
        self.s = store or MemoryStore()
        self.clock = clock
        self.new_id = id_factory  # injectable for deterministic tests
        self.coalesce_window_s = coalesce_window_s
        self.lease_s = lease_s
        self.max_attempts = max_attempts
        self.big_docs = False  # Hermes is configured (HERMES_URL): long documents go to T3

    # ------------------------------------------------------------------ sessions & fencing
    def open_session(self, session_id: str, channel: str, caller: Optional[str] = None,
                     user_id: Optional[int] = None, plan: Optional[str] = None) -> Session:
        """Start, or take over (reconnect / re-dispatched talker) a session. Takeover bumps the epoch."""
        sess = self.s.sessions.get(session_id)
        if sess is None:
            sess = Session(id=session_id, channel=channel, caller=caller, user_id=user_id, plan=plan)
            self.s.sessions[session_id] = sess
        else:
            sess.epoch += 1
            if sess.state in (ConvState.OFFLINE, ConvState.RECONNECTING):
                sess.state = ConvState.CONVERSE
        return sess

    def go_offline(self, session_id: str) -> None:
        """Caller hung up / dropped: tasks keep running; delivery continues asynchronously."""
        self.s.sessions[session_id].state = ConvState.OFFLINE

    def verify_recipient(self, session_id: str, recipient: str) -> None:
        """Called after OTP (or caller-ID confirmation) succeeds."""
        sess = self.s.sessions[session_id]
        if recipient not in sess.verified_recipients:
            sess.verified_recipients.append(recipient)

    def _emit(self, session_id: str, type_: str, **kw) -> Event:
        return self.s.append_event(session_id, self.s.sessions[session_id].epoch, type_, **kw)

    def notify(self, session_id: str, type_: str, **kw) -> Optional[Event]:
        """An event for one call from outside a command or task step (callback.done: a callback has nothing left
        to ask)."""
        return self._emit(session_id, type_, **kw) if session_id in self.s.sessions else None

    def usable(self, doc: DocRecord, session_id: str) -> bool:
        """May this call act on this document? Its own documents, and the caller's earlier ones it has opened."""
        if doc.session_id == session_id:
            return True
        sess = self.s.sessions.get(session_id)
        return sess is not None and doc.id in sess.attached

    def session_docs(self, session_id: str) -> List[DocRecord]:
        return sorted((d for d in self.s.docs.values() if self.usable(d, session_id)), key=lambda d: d.created_at)

    def attach(self, session_id: str, doc_id: str) -> bool:
        """Open the caller's earlier document on this call (ownership is checked by the caller of this method)."""
        sess, doc = self.s.sessions.get(session_id), self.s.docs.get(doc_id)
        if sess is None or doc is None:
            return False
        if doc.id not in sess.attached and doc.session_id != session_id:
            sess.attached.append(doc.id)
        return True

    # ------------------------------------------------------------------ commands (talker tools)
    def handle(self, cmd) -> CommandResult:
        sess = self.s.sessions.get(cmd.session_id)
        if sess is None:
            return CommandResult("rejected", reason="unknown_session")
        if cmd.epoch != sess.epoch:
            return CommandResult("rejected", reason="stale_epoch")  # superseded talker: never mutate
        if not cmd.idem_key:
            cmd = cmd.with_idem_key()
        prior = self.s.idem.get(cmd.idem_key)
        if prior is not None:
            return CommandResult("duplicate", prior.task_id, prior.doc_id, prior.delivery_id, prior.merged_into,
                                 prior.reason)
        handler = {CreateDoc: self._create, EditDoc: self._edit, Answer: self._answer, Cancel: self._cancel,
                   Undo: self._undo, Deliver: self._deliver}[type(cmd)]
        result = handler(cmd)
        # a late answer is not news for the caller: the talker turns it into a change itself
        if result.status == "rejected" and not isinstance(cmd, Answer):
            self._emit(cmd.session_id, "command.rejected", payload={"cmd": cmd.cmd, "reason": result.reason})
        self.s.idem[cmd.idem_key] = result  # rejections are idempotent too (same answer on replay)
        return result

    def seen(self, cmd) -> bool:
        """Has this exact command (same idempotency key) been answered before?"""
        return (cmd.idem_key or cmd.with_idem_key().idem_key) in self.s.idem

    def reject(self, cmd, reason: str) -> CommandResult:
        """Refuse a command before any handler runs (a limit), with the same event and idempotent answer a
        handler's rejection gets, so the talker and the call report see it like any other refusal."""
        sess = self.s.sessions.get(cmd.session_id)
        if sess is None:
            return CommandResult("rejected", reason="unknown_session")
        if cmd.epoch != sess.epoch:
            return CommandResult("rejected", reason="stale_epoch")
        if not cmd.idem_key:
            cmd = cmd.with_idem_key()
        prior = self.s.idem.get(cmd.idem_key)
        if prior is not None:
            return CommandResult("duplicate", prior.task_id, prior.doc_id, prior.delivery_id, prior.merged_into,
                                 prior.reason)
        result = CommandResult("rejected", reason=reason)
        self._emit(cmd.session_id, "command.rejected", payload={"cmd": cmd.cmd, "reason": reason})
        self.s.idem[cmd.idem_key] = result
        return result

    def _new_task(self, cmd, doc_id: str, kind: str, tier: Tier, base_version: int, instruction: str) -> Task:
        t = Task(id=self.new_id("task"), session_id=cmd.session_id, doc_id=doc_id, kind=kind, tier=tier,
                 base_version=base_version, created_at=self.clock(), instructions=[instruction],
                 utterance_ids=list(getattr(cmd, "utterance_ids", [])), transcript=getattr(cmd, "transcript", ""))
        self.s.tasks[t.id] = t
        return t

    def _doc_tasks(self, doc_id: str) -> List[Task]:
        return sorted((t for t in self.s.tasks.values() if t.doc_id == doc_id), key=lambda t: t.created_at)

    def _create(self, cmd: CreateDoc) -> CommandResult:
        doc = DocRecord(id=self.new_id("doc"), session_id=cmd.session_id, formats=list(cmd.formats) or ["pdf"],
                        doc_lang=cmd.doc_lang, created_at=self.clock(), updated_at=self.clock(),
                        owner_user_id=self.s.sessions[cmd.session_id].user_id)
        self.s.docs[doc.id] = doc
        tier: Tier = "T3" if self.big_docs and cmd.brief.get("size") == "long" else "T2"
        t = self._new_task(cmd, doc.id, "create", tier, 0, cmd.intent)
        if tier == "T3":  # the scope agreed on the call outlives it: the writer works after the caller hangs up
            doc.long, t.big = True, {"brief": dict(cmd.brief)}
        payload = {"tier": tier, "formats": list(cmd.formats)}
        if tier == "T3" and cmd.brief.get("unit"):  # slides or pages: how long the next ones take (apps/control/eta.py)
            payload["unit"] = cmd.brief["unit"]
        self._emit(cmd.session_id, "task.accepted", task_id=t.id, doc_id=doc.id, base_version=0, payload=payload)
        return CommandResult("accepted", task_id=t.id, doc_id=doc.id)

    def _edit(self, cmd: EditDoc) -> CommandResult:
        doc = self.s.docs.get(cmd.doc_id)
        if doc is None or not self.usable(doc, cmd.session_id):
            return CommandResult("rejected", reason="unknown_doc")
        if cmd.base_version > doc.head_version or cmd.base_version < 0:
            return CommandResult("rejected", doc_id=doc.id, reason="unknown_version")
        if cmd.doc_lang and cmd.doc_lang != doc.doc_lang:
            # Another language: the whole document is rewritten in it (the editor asks for a redraft, which writes in
            # the document's language).
            doc.doc_lang = cmd.doc_lang
            cmd = cmd.model_copy(update={"tier_hint": "T2"})
        tasks = self._doc_tasks(doc.id)
        now = self.clock()
        # 1) a task is mid-flight: hand the edit to its inbox (thinker reads it at the next tool boundary)
        active = next((t for t in tasks if t.state in (TaskState.RUNNING, TaskState.AWAITING_INPUT)), None)
        if active is not None:
            active.inbox.append(cmd.instruction)
            active.utterance_ids.extend(cmd.utterance_ids)
            active.transcript = cmd.transcript or active.transcript
            self._emit(cmd.session_id, "task.accepted", task_id=active.id, doc_id=doc.id,
                       payload={"tier": active.tier, "via": "inbox"})
            return CommandResult("accepted", task_id=active.id, doc_id=doc.id, merged_into=active.id)
        # 2) coalesce with a queued edit that arrived moments ago (one task, one version, one SMS)
        queued = [t for t in tasks if t.state == TaskState.QUEUED and t.kind == "edit"]
        if queued and now - queued[-1].created_at <= self.coalesce_window_s:
            q = queued[-1]
            q.instructions.append(cmd.instruction)
            q.utterance_ids.extend(cmd.utterance_ids)
            q.transcript = cmd.transcript or q.transcript
            q.tier = "T2" if "T2" in (q.tier, cmd.tier_hint) else q.tier
            self._emit(cmd.session_id, "task.accepted", task_id=q.id, doc_id=doc.id,
                       payload={"tier": q.tier, "via": "coalesced"})
            return CommandResult("accepted", task_id=q.id, doc_id=doc.id, merged_into=q.id)
        # 3) new queued task, based on the version the caller actually heard
        t = self._new_task(cmd, doc.id, "edit", cmd.tier_hint or "T1", cmd.base_version, cmd.instruction)
        self._emit(cmd.session_id, "task.accepted", task_id=t.id, doc_id=doc.id, base_version=cmd.base_version,
                   payload={"tier": t.tier, "via": "queued"})
        return CommandResult("accepted", task_id=t.id, doc_id=doc.id)

    def _answer(self, cmd: Answer) -> CommandResult:
        q = self.s.questions.get(cmd.question_id)
        if q is None or q.answer is not None:
            return CommandResult("rejected", reason="unknown_or_answered_question")
        task = self.s.tasks[q.task_id]
        if q.asked_on(task) != cmd.session_id or task.state != TaskState.AWAITING_INPUT:
            return CommandResult("rejected", task_id=task.id, reason="task_not_waiting")
        q.answer = cmd.value
        self._set_state(task, TaskState.RUNNING)
        task.lease_until = self.clock() + self.lease_s
        return CommandResult("accepted", task_id=task.id, doc_id=task.doc_id)

    def _cancel(self, cmd: Cancel) -> CommandResult:
        task = self.s.tasks.get(cmd.task_id)
        if task is None or task.session_id != cmd.session_id:
            return CommandResult("rejected", reason="unknown_task")
        if task.state in (TaskState.QUEUED, TaskState.RUNNING, TaskState.AWAITING_INPUT):
            self._set_state(task, TaskState.CANCELLED)
            self._emit(task.session_id, "task.cancelled", task_id=task.id, doc_id=task.doc_id,
                       payload={"instructions": task.instructions + task.inbox})
            return CommandResult("accepted", task_id=task.id, doc_id=task.doc_id)
        if task.state == TaskState.COMPLETED:  # after commit, cancel means undo
            return self._undo(Undo(session_id=cmd.session_id, epoch=cmd.epoch, turn_id=cmd.turn_id,
                                   idem_key=cmd.idem_key + ":undo", doc_id=task.doc_id))
        return CommandResult("rejected", task_id=task.id, reason=f"task_{task.state.value}")

    def _undo(self, cmd: Undo) -> CommandResult:
        doc = self.s.docs.get(cmd.doc_id)
        if doc is None or not self.usable(doc, cmd.session_id):
            return CommandResult("rejected", reason="unknown_doc")
        if doc.head_version < 2:
            return CommandResult("rejected", doc_id=doc.id, reason="nothing_to_undo")
        t = self._new_task(cmd, doc.id, "undo", "T0", doc.head_version, f"restore v{doc.head_version - 1}")
        self._emit(cmd.session_id, "task.accepted", task_id=t.id, doc_id=doc.id, payload={"tier": "T0"})
        return CommandResult("accepted", task_id=t.id, doc_id=doc.id)

    def _deliver(self, cmd: Deliver) -> CommandResult:
        doc = self.s.docs.get(cmd.doc_id)
        sess = self.s.sessions[cmd.session_id]
        if doc is None or not self.usable(doc, cmd.session_id):
            return CommandResult("rejected", reason="unknown_doc")
        if doc.head_version == 0:
            return CommandResult("rejected", doc_id=doc.id, reason="not_ready")
        if cmd.recipient not in sess.verified_recipients:
            return CommandResult("rejected", doc_id=doc.id, reason="unverified_recipient")
        return self._create_delivery(doc, cmd.channel, cmd.recipient, cmd.purpose, cmd.session_id)

    def _create_delivery(self, doc: DocRecord, channel: str, recipient: str, purpose: str,
                         session_id: Optional[str] = None) -> CommandResult:
        if purpose == "initial":
            key: Tuple = (doc.id, recipient, "initial")
        elif purpose == "updated":
            key = (doc.id, recipient, "updated", doc.head_version)
        else:
            n = sum(1 for k in self.s.deliveries if k[:3] == (doc.id, recipient, "resend"))
            if n >= MAX_RESENDS:
                return CommandResult("rejected", doc_id=doc.id, reason="resend_limit")
            key = (doc.id, recipient, "resend", n + 1)
        existing = self.s.deliveries.get(key)
        if existing is not None:
            return CommandResult("duplicate", doc_id=doc.id, delivery_id=existing.id)
        d = Delivery(id=self.new_id("dlv"), key=key, doc_id=doc.id, version=doc.head_version, channel=channel,
                     recipient=recipient, purpose=purpose, created_at=self.clock(), updated_at=self.clock(),
                     session_id=session_id or doc.session_id)
        self.s.deliveries[key] = d
        doc.last_delivered_version[recipient] = doc.head_version
        self._emit(d.session_id, "delivery.submitted", doc_id=doc.id, head_version=doc.head_version,
                   payload={"delivery_id": d.id, "channel": channel, "purpose": purpose})
        return CommandResult("accepted", doc_id=doc.id, delivery_id=d.id)

    def finalize_updates(self, session_id: str) -> List[str]:
        """Call-end / debounce timer: one 'updated' message per recipient if the doc moved past what they have.
        Not after a browser call: SMS there only when the caller asks."""
        out: List[str] = []
        sess = self.s.sessions.get(session_id)
        if sess is not None and sess.channel == "sip":
            return out
        for doc in self.session_docs(session_id):
            for recipient, delivered in list(doc.last_delivered_version.items()):
                if doc.head_version > delivered:
                    channel = next((x.channel for x in self.s.deliveries.values()
                                    if x.doc_id == doc.id and x.recipient == recipient), "sms")
                    res = self._create_delivery(doc, channel, recipient, "updated", session_id)
                    if res.status == "accepted":
                        out.append(res.delivery_id)
        return out

    def auto_deliver(self, doc_id: str, channel: str = "sms", session_id: Optional[str] = None) -> Optional[str]:
        """After a commit: send it to the (working) call's first verified recipient, exactly once per recipient.
        Not during a browser call: the caller watches it on screen, and an SMS goes only when they ask
        (send_on_request) or once they have hung up."""
        doc = self.s.docs[doc_id]
        sid = session_id or doc.session_id
        sess = self.s.sessions[sid]
        if doc.head_version == 0 or not sess.verified_recipients:
            return None
        # a browser call's caller who has hung up before it was ready isn't watching the screen: the link by SMS, as
        # BolDoc told them
        if sess.channel == "sip" and sess.state.value != "offline":
            return None
        res = self._create_delivery(doc, channel, sess.verified_recipients[0], "initial", sid)
        return res.delivery_id if res.status == "accepted" else None

    def send_on_request(self, doc_id: str, recipient: str) -> CommandResult:
        """"Send to my phone" (a browser call's document, or any of the owner's from the console): the first SMS to
        this number, then resends, at most MAX_RESENDS."""
        doc = self.s.docs.get(doc_id)
        if doc is None or doc.head_version == 0:
            return CommandResult("rejected", doc_id=doc_id, reason="not_ready")
        res = self._create_delivery(doc, "sms", recipient, "initial", doc.session_id)
        if res.status == "duplicate":
            res = self._create_delivery(doc, "sms", recipient, "resend", doc.session_id)
        return res

    def delivery(self, delivery_id: str) -> Optional[Delivery]:
        return next((d for d in self.s.deliveries.values() if d.id == delivery_id), None)

    def mark_delivery(self, delivery_id: str, status: str, provider_ref: Optional[str] = None,
                      error: Optional[str] = None) -> bool:
        """Provider feedback (send result, delivery receipt). Status only moves forward; late/duplicate receipts
        are ignored. Returns True if the status changed."""
        d = self.delivery(delivery_id)
        if d is None or DELIVERY_RANK[status] <= DELIVERY_RANK[d.status] or d.status in ("delivered", "failed"):
            return False
        d.status, d.updated_at = status, self.clock()
        d.provider_ref = provider_ref or d.provider_ref
        d.error = error or d.error
        session_id = d.session_id or self.s.docs[d.doc_id].session_id
        if status in ("delivered", "failed"):
            self._emit(session_id, f"delivery.{status}", doc_id=d.doc_id, head_version=d.version,
                       payload={"delivery_id": d.id, "channel": d.channel, "purpose": d.purpose,
                                **({"code": error} if error else {})})
        return True

    def snapshot(self, session_id: str) -> dict:
        """Everything the talker may say about this session's documents (JSON-safe). Rendered into the state
        block before every LLM call; the talker never states a document fact that isn't in here."""
        sess = self.s.sessions[session_id]
        docs = []
        for d in self.session_docs(session_id):
            dels = sorted((x for x in self.s.deliveries.values() if x.doc_id == d.id), key=lambda x: x.created_at)
            last_changes = self.s.version_meta.get((d.id, d.head_version), {}).get("changes", [])
            docs.append({"id": d.id, "title": d.title, "head_version": d.head_version, "formats": d.formats,
                         "doc_lang": d.doc_lang, "last_changes": last_changes[:5],
                         "deliveries": [{"id": x.id, "channel": x.channel, "recipient": x.recipient,
                                         "status": x.status, "version": x.version, "purpose": x.purpose}
                                        for x in dels]})
        tasks = [{"id": t.id, "kind": t.kind, "doc_id": t.doc_id, "state": t.state.value, "tier": t.tier,
                  "queued_requests": len(t.inbox), "created_at": t.created_at, "instructions": t.instructions[-3:]}
                 for t in sorted(self.s.tasks.values(), key=lambda t: t.created_at)
                 if t.session_id == session_id and (t.state == TaskState.QUEUED or t.state in ACTIVE)]
        question = next(({"id": q.id, "text": q.text, "default": q.default, "task_id": q.task_id}
                         for q in self.s.questions.values()
                         if q.answer is None and q.asked_on(self.s.tasks[q.task_id]) == session_id
                         and self.s.tasks[q.task_id].state == TaskState.AWAITING_INPUT), None)
        events = self.s.events.get(session_id, [])
        return {"session_id": session_id, "epoch": sess.epoch, "state": sess.state.value,
                "verified_recipients": list(sess.verified_recipients), "docs": docs, "tasks": tasks,
                "question": question, "last_seq": events[-1].seq if events else 0,
                "on_screen": sess.channel == "sip"}  # a browser call: the caller sees the document being written

    # ------------------------------------------------------------------ thinker-facing (workers)
    def _set_state(self, task: Task, new: TaskState) -> None:
        check_task_transition(task.state, new)
        task.state = new

    def claim(self, task_id: str, worker_id: str) -> Tuple[Optional[Task], str]:
        """Claim one specific task (a durable workflow owns it). Returns (task, "claimed") or (None, reason):
        "busy" (another task is working on the same document: retry soon), "not_queued" or "unknown"."""
        t = self.s.tasks.get(task_id)
        if t is None:
            return None, "unknown"
        if t.state != TaskState.QUEUED:
            return None, "not_queued"
        if any(o.doc_id == t.doc_id and o.state in ACTIVE for o in self.s.tasks.values() if o.id != t.id):
            return None, "busy"
        # oldest queued task on a document goes first (edits apply in the order they were asked)
        older = [o for o in self.s.tasks.values() if o.doc_id == t.doc_id and o.state == TaskState.QUEUED
                 and o.created_at < t.created_at]
        if older:
            return None, "busy"
        self._set_state(t, TaskState.RUNNING)
        t.worker_id, t.lease_until = worker_id, self.clock() + self.lease_s
        t.attempts += 1
        self._emit(t.session_id, "task.progress", task_id=t.id, doc_id=t.doc_id,
                   payload={"stage": "started", "attempt": t.attempts})
        return t, "claimed"

    def claim_next(self, worker_id: str) -> Optional[Task]:
        busy_docs = {t.doc_id for t in self.s.tasks.values() if t.state in ACTIVE}
        for t in sorted(self.s.tasks.values(), key=lambda t: t.created_at):
            if t.state == TaskState.QUEUED and t.doc_id not in busy_docs:
                self._set_state(t, TaskState.RUNNING)
                t.worker_id, t.lease_until = worker_id, self.clock() + self.lease_s
                t.attempts += 1
                self._emit(t.session_id, "task.progress", task_id=t.id, doc_id=t.doc_id,
                           payload={"stage": "started", "attempt": t.attempts})
                return t
        return None

    def progress(self, task_id: str, stage: str) -> None:
        """A long task's milestone ("section 3 of 7"): the website shows it; the talker mentions it now and then."""
        t = self.s.tasks.get(task_id)
        if t is not None and t.state in ACTIVE:
            self._emit(t.session_id, "task.progress", task_id=t.id, doc_id=t.doc_id, payload={"stage": stage})

    def _owned(self, task_id: str, worker_id: str) -> Optional[Task]:
        t = self.s.tasks.get(task_id)
        return t if t is not None and t.worker_id == worker_id and t.state in ACTIVE else None

    def heartbeat(self, task_id: str, worker_id: str) -> bool:
        t = self._owned(task_id, worker_id)
        if t is None:
            return False  # lease lost: the worker must stop (and must not commit)
        t.lease_until = self.clock() + self.lease_s
        return True

    def rebase(self, task_id: str, worker_id: str) -> Optional[int]:
        """Edits are semantic ("make the date 26th"): apply them to the latest version, not a stale base."""
        t = self._owned(task_id, worker_id)
        if t is None:
            return None
        t.base_version = self.s.docs[t.doc_id].head_version
        return t.base_version

    def recover_after_restart(self) -> List[str]:
        """Workers live in the control-plane process: after a restart nothing that was running still runs."""
        requeued = []
        for t in self.s.tasks.values():
            if t.state in ACTIVE:
                self._retry_or_fail(t, "restart", retryable=True)
                requeued.append(t.id)
        for d in self.s.deliveries.values():
            if d.status == "sending":  # at-most-once: it may or may not have gone out, so never send it twice
                self.mark_delivery(d.id, "failed", error="unknown_after_restart")
        return requeued

    def consume_inbox(self, task_id: str, worker_id: str) -> List[str]:
        """Thinker pulls mid-run edits at a tool boundary; they become part of this task's work."""
        t = self._owned(task_id, worker_id)
        if t is None:
            return []
        items, t.inbox = t.inbox, []
        t.instructions.extend(items)
        return items

    def ask(self, task_id: str, worker_id: str, text: str, default: str, ttl_s: float = 60.0,
            session_id: Optional[str] = None) -> Optional[str]:
        """Put a question to the caller on the task's own call, or on `session_id`: another of their calls that can
        use the document (a callback, or a call they made later). Only that call may answer it."""
        t = self._owned(task_id, worker_id)
        if t is None or t.state != TaskState.RUNNING:
            return None
        target = session_id or t.session_id
        if target != t.session_id and not self.usable(self.s.docs[t.doc_id], target):
            return None
        q = Question(id=self.new_id("q"), task_id=t.id, text=text, default=default, expires_at=self.clock() + ttl_s,
                     session_id=target if target != t.session_id else None)
        self.s.questions[q.id] = q
        t.question_id = q.id
        self._set_state(t, TaskState.AWAITING_INPUT)
        t.lease_until = q.expires_at + self.lease_s
        self._emit(target, "task.question", task_id=t.id, doc_id=t.doc_id, expires_at=q.expires_at,
                   payload={"question_id": q.id, "text": text, "default": default})
        return q.id

    def add_instruction(self, task_id: str, worker_id: str, instruction: str) -> None:
        """The worker queues a change for its own next edit (a field the caller asked to leave out)."""
        t = self._owned(task_id, worker_id)
        if t is not None:
            t.inbox.append(instruction)

    def reopen_question(self, task_id: str, worker_id: str) -> bool:
        """The caller's words didn't answer it: the same question waits for their next words (not asked again,
        so no new event; it shows in the snapshot again)."""
        t = self._owned(task_id, worker_id)
        q = self.s.questions.get(t.question_id or "") if t is not None else None
        if t is None or q is None or t.state != TaskState.RUNNING:
            return False
        q.answer = None
        self._set_state(t, TaskState.AWAITING_INPUT)
        return True

    def withdraw_question(self, task_id: str, worker_id: str) -> None:
        """The worker stops waiting (the caller hung up, or asked for a change instead): the question takes its
        default and the task runs on."""
        t = self._owned(task_id, worker_id)
        if t is None or t.state != TaskState.AWAITING_INPUT:
            return
        q = self.s.questions.get(t.question_id or "")
        if q is not None and q.answer is None:
            q.answer = q.default
        self._set_state(t, TaskState.RUNNING)
        t.lease_until = self.clock() + self.lease_s

    def commit(self, task_id: str, worker_id: str, document: Document, changes: List[str]) -> CommitResult:
        t = self._owned(task_id, worker_id)
        if t is None or t.state != TaskState.RUNNING:
            return CommitResult("rejected", reason="not_owner_or_not_running")
        doc = self.s.docs[t.doc_id]
        self._set_state(t, TaskState.COMMITTING)
        if t.base_version != doc.head_version:  # someone else committed first: rebase and retry
            t.base_version = doc.head_version
            self._set_state(t, TaskState.QUEUED)
            t.worker_id = t.lease_until = None
            return CommitResult("conflict", reason="head_moved")
        version = doc.head_version + 1
        self.s.versions[(doc.id, version)] = document.model_copy(update={"id": doc.id, "version": version})
        self.s.version_meta[(doc.id, version)] = {"at": self.clock(), "changes": list(changes), "task_id": t.id}
        doc.head_version = version
        doc.title = document.title
        doc.updated_at = self.clock()
        t.committed_version = version
        self._set_state(t, TaskState.COMPLETED)
        self._emit(t.session_id, "task.completed", task_id=t.id, doc_id=doc.id, head_version=version,
                   payload={"diff_summary": changes[:5], "instructions": len(t.instructions)})
        follow_up = None
        if t.inbox:  # edits that arrived after the last tool boundary: never dropped
            f = Task(id=self.new_id("task"), session_id=t.session_id, doc_id=doc.id, kind="edit", tier="T1",
                     base_version=version, created_at=self.clock(), instructions=list(t.inbox))
            t.inbox = []
            self.s.tasks[f.id] = f
            follow_up = f.id
            self._emit(t.session_id, "task.accepted", task_id=f.id, doc_id=doc.id, base_version=version,
                       payload={"tier": "T1", "via": "follow_up"})
        return CommitResult("committed", version=version, follow_up_task_id=follow_up)

    def fail(self, task_id: str, worker_id: str, code: str, retryable: bool) -> None:
        t = self._owned(task_id, worker_id)
        if t is None:
            return
        self._retry_or_fail(t, code, retryable)

    def _retry_or_fail(self, t: Task, code: str, retryable: bool) -> None:
        t.worker_id = t.lease_until = None
        if retryable and t.attempts < self.max_attempts:
            self._set_state(t, TaskState.QUEUED)
            return
        self._set_state(t, TaskState.FAILED)
        t.error = code
        self._emit(t.session_id, "task.failed", task_id=t.id, doc_id=t.doc_id,
                   payload={"code": code, "instructions": t.instructions + t.inbox})

    def expire(self) -> None:
        """Periodic sweep: answer expired questions with their default; requeue tasks whose worker died."""
        now = self.clock()
        for q in self.s.questions.values():
            t = self.s.tasks[q.task_id]
            if q.answer is None and now >= q.expires_at and t.state == TaskState.AWAITING_INPUT:
                q.answer = q.default
                self._set_state(t, TaskState.RUNNING)
                t.lease_until = now + self.lease_s
        for t in self.s.tasks.values():
            if t.state in (TaskState.RUNNING, TaskState.AWAITING_INPUT) and t.lease_until is not None \
                    and now > t.lease_until:
                self._retry_or_fail(t, "lease_expired", retryable=True)
