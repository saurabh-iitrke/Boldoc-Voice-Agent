"""Background work of the control plane: thinker tasks, SMS delivery, the expiry sweep.

Thinker task:  claim (lease) -> draft (T2) / edit (T1, escalates to T2) / undo (T0)
  -> ask the caller for facts the draft still lacks (one question at a time, while they are on the line)
  -> fold in the answers and the edits that arrived meanwhile -> render every requested format (validated)
  -> commit (CAS on head)
  -> in the SAME tick: stage renditions + link, schedule the SMS -> one durable flush -> events become visible.
A heartbeat keeps the lease alive during slow LLM calls; a worker that lost its lease can never commit.

Two ways to drive it (same code path):
  temporal  durable workflows (apps/control/flows.py) run each task and delivery; a reconciler starts a workflow
            for every queued task / pending delivery (outbox -> workflows), so nothing is lost if Temporal was down.
  inline    in-process loops (tests, or when Temporal is unreachable at startup).
"""
from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from loguru import logger

from apps.thinker.drafter import draft
from apps.thinker.editor import edit
from apps.thinker.gaps import Reading, fill, interpret, label_of, open_gaps, question_for, removal, remove_gap
from apps.thinker.llm import ThinkerError
from docengine import RenderedFile, render, validate
from docengine.model import Document
from protocol.fsm import ConvState, TaskState

from . import preview as doc_preview
from .accounts.pricing import PRICING
from .core.orchestrator import Orchestrator, Task
from .sms import PlivoSms, SmsError, compose
from .store_pg import PgStore

# One rendering thread: WeasyPrint/Pango work is CPU-bound and not something to run in parallel on a small box.
_RENDER_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")
ETA_S = {"create": 15, "edit": 6, "undo": 2, "create_big": 600}  # create_big: a long document Hermes writes
# Facts a draft still lacks are asked, never assumed: one question at a time, while the caller is on the line.
MAX_QUESTIONS = 3
QUESTION_WAIT_S = 30.0       # unanswered (the talker reminds once at 8 s): the placeholder stays, flagged
QUESTIONS_BUDGET_S = 100.0   # all of one task's questions (the task's activity has 4 minutes)
NO_ANSWER = "no answer: leave it as a blank to fill in later"


def _no_redraft(doc) -> dict:
    """A long document (Hermes wrote it) is never rewritten whole for a change: one draft call can't hold it."""
    return {"allow_redraft": False} if getattr(doc, "long", False) else {}


def render_all(doc: Document, formats: List[str]) -> Tuple[List[RenderedFile], Dict[str, str]]:
    """Render each format and validate it. A format that fails is reported, never delivered broken."""
    files, failed = [], {}
    for fmt in dict.fromkeys(formats):
        try:
            f = render(doc, fmt)
            problems = validate(doc, f)
            if problems:
                failed[fmt] = "; ".join(problems)[:200]
                continue
            files.append(f)
        except Exception as e:  # renderer bug or missing system dependency: keep the other formats
            failed[fmt] = f"{type(e).__name__}: {e}"[:200]
    return files, failed


class Background:
    def __init__(self, orch: Orchestrator, store: PgStore, llm, public_host: str,
                 sms: Optional[PlivoSms], link_ttl_days: float = 7.0):
        # llm: the thinker's endpoint (protocol.llm.LLMEndpoint), or a Sarvam key string
        self.orch, self.store, self.key, self.host, self.sms = orch, store, llm, public_host, sms
        self.link_ttl_days = link_ttl_days
        self.links: Dict[str, str] = {}  # doc_id -> link token (plaintext lives only in memory; DB keeps hashes)
        self.task_wake = asyncio.Event()
        self.delivery_wake = asyncio.Event()
        self._tasks: List[asyncio.Task] = []
        self.mode = "inline"
        self.flows = None          # TemporalFlows when mode == "temporal"
        self.bus = None            # Redis bus (rate limits, fan-out); optional
        self.big = None            # BigDocs (apps/control/bigdoc.py) when Hermes is configured: long documents
        self.sms_daily_limit = 10  # per recipient per day (Redis)
        self._started: Dict[str, float] = {}  # workflow id -> when we last asked Temporal to start it
        self._warming: set = set()  # previews being made for new versions (kept so the tasks aren't collected)

    def start(self, workers: int, flows=None) -> None:
        if flows is not None:
            self.mode, self.flows = "temporal", flows
            self._tasks = [asyncio.create_task(self._reconcile()), asyncio.create_task(self._sweeper()),
                           asyncio.create_task(self._retention())]
        else:
            self._tasks = [asyncio.create_task(self._worker(f"w{i}")) for i in range(workers)]
            self._tasks += [asyncio.create_task(self._deliveries()), asyncio.create_task(self._sweeper()),
                            asyncio.create_task(self._retention())]

    def wake(self) -> None:
        self.task_wake.set()
        self.delivery_wake.set()

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def link_for(self, doc_id: str) -> str:
        """Stable link per document while this process lives; minted again (new token) after a restart."""
        token = self.links.get(doc_id)
        if token is None:
            token = self.links[doc_id] = secrets.token_urlsafe(16)  # 128 bits, 22 chars
            self.store.stage_link(doc_id, token, self.link_ttl_days)
        return f"https://{self.host}/d/{token}"

    # ------------------------------------------------------------------ thinker
    async def _worker(self, worker_id: str) -> None:
        while True:
            try:
                task = self.orch.claim_next(worker_id)
                if task is None:
                    self.task_wake.clear()
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self.task_wake.wait(), 2.0)
                    continue
                await self.store.flush()
                await self._run(task, worker_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(f"{worker_id}: loop error")
                await asyncio.sleep(1.0)

    async def _heartbeat(self, task_id: str, worker_id: str, beat=None) -> None:
        while True:
            await asyncio.sleep(max(1.0, self.orch.lease_s / 3))
            if not self.orch.heartbeat(task_id, worker_id):
                return
            if beat is not None:
                beat()  # Temporal activity heartbeat: a dead worker is noticed and the activity retried

    async def run_task(self, task_id: str, worker_id: str, beat=None) -> str:
        """One task, as a durable workflow's activity runs it: claimed -> committed / requeued / failed;
        "busy" (the document is being written by another task) and "skipped" (no longer queued) for the workflow."""
        task, why = self.orch.claim(task_id, worker_id)
        if task is None:
            return "busy" if why == "busy" else "skipped"
        await self.store.flush()
        return await self._run(task, worker_id, beat)

    async def _run(self, task: Task, worker_id: str, beat=None) -> str:
        doc = self.orch.s.docs[task.doc_id]
        log = logger.bind(task=task.id[-6:], doc=doc.id[-6:])
        t0 = time.perf_counter()
        hb = asyncio.create_task(self._heartbeat(task.id, worker_id, beat))
        try:
            document, changes, stats = await self._produce(task, worker_id, beat)
            if stats.get("doc_lang") and stats["doc_lang"] != doc.doc_lang:
                doc.doc_lang = stats["doc_lang"]  # rewritten in another language: it stays in it
            if task.kind != "undo" and task.tier != "T3":  # Hermes asks its own questions while it writes
                document, filled = await self._fill_gaps(task, worker_id, doc, document)
                changes += filled
            extra = self.orch.consume_inbox(task.id, worker_id)  # edits that arrived while we were writing
            if extra:
                document, more, _ = await edit(self.key, document, extra, task.transcript, doc.doc_lang, doc.formats,
                                               intent="; ".join(task.instructions), **_no_redraft(doc))
                changes += more
            version = task.base_version + 1
            staged = document.model_copy(update={"id": doc.id, "version": version})
            files, failed = await asyncio.get_running_loop().run_in_executor(_RENDER_POOL, render_all, staged,
                                                                             doc.formats)
            if not files:
                raise ThinkerError("render_failed", "; ".join(f"{k}: {v}" for k, v in failed.items()))
            res = self.orch.commit(task.id, worker_id, staged, changes)
            dlv = None
            if res.status == "committed":
                # same tick as the commit: the flush that makes "completed" visible also stores the files + link
                self.store.stage_renditions(doc.id, res.version, files)
                meta = self.orch.s.version_meta[(doc.id, res.version)]
                if failed:
                    meta["failed_formats"] = failed
                # paid plan: the debit commits in the same transaction as the version it pays for
                # the call that asked for this work pays for it and hears about it (it may not be the call that
                # created the document: callers change their earlier documents on later calls)
                sess = self.orch.s.sessions.get(task.session_id)
                kind = {"create": "doc_create_big" if task.tier == "T3" else "doc_create",
                        "edit": "doc_edit"}.get(task.kind)
                if sess is not None and sess.plan == "credits" and sess.user_id and kind:
                    self.store.stage_debit(sess.user_id, kind, -PRICING.price_for(kind), f"task:{task.id}", doc.id)
                # which model wrote it and what it used: cost per document, model comparisons
                meta["thinker"] = {k: stats[k] for k in ("model", "tier", "attempts", "latency_ms", "usage", "reviewed")
                                   if stats.get(k) not in (None, {}, "")}
                self.link_for(doc.id)
                dlv = self.orch.auto_deliver(doc.id, session_id=task.session_id)
                if self.orch.s.sessions[task.session_id].state.value == "offline":
                    self.orch.finalize_updates(task.session_id)  # caller already hung up: send the update now
            await self.store.flush()
            if res.status == "committed":  # its Word/Excel/PowerPoint previews made ahead (apps/control/preview.py)
                warming = asyncio.create_task(doc_preview.warm(self.store, doc.id, res.version,
                                                               [f.fmt for f in files]))
                self._warming.add(warming)
                warming.add_done_callback(self._warming.discard)
            if dlv or res.follow_up_task_id or res.status == "conflict":
                self.wake()
            if res.status == "committed" and self.flows is not None:
                await self.flows.version_committed(task.session_id)
            log.info(f"{task.kind} -> {res.status} v{res.version} in {time.perf_counter() - t0:.1f}s "
                     f"tier={stats.get('tier', task.tier)} formats={[f.fmt for f in files]} failed={list(failed)}")
            return {"committed": "committed", "conflict": "requeued"}.get(res.status, "failed")
        except ThinkerError as e:
            log.warning(f"{task.kind} failed: {e}")
            self.orch.fail(task.id, worker_id, e.code, retryable=e.code in ("provider_unavailable", "timeout"))
            await self.store.flush()
        except Exception as e:
            log.exception(f"{task.kind} crashed")
            self.orch.fail(task.id, worker_id, type(e).__name__, retryable=True)
            await self.store.flush()
        finally:
            hb.cancel()
        return "requeued" if self.orch.s.tasks[task.id].state.value == "queued" else "failed"

    async def _fill_gaps(self, task: Task, worker_id: str, doc, document: Document) -> Tuple[Document, List[str]]:
        """Each placeholder in the draft becomes a question on the call, one at a time, while the caller is on the
        line. An answer goes in at once, by code; a field the caller doesn't have is taken out by code (or, in
        running text, by the task's last edit). Asking stops at the first question nobody answers. An edit asks
        only about gaps it made: one left open before was already asked (the caller can fill it in by saying so)."""
        changes: List[str] = []
        deadline = time.monotonic() + QUESTIONS_BUDGET_S
        base = self.orch.s.versions.get((doc.id, task.base_version))
        known = set(open_gaps(base)) if base is not None else set()
        for gap in [g for g in open_gaps(document) if g not in known][:MAX_QUESTIONS]:
            left = deadline - time.monotonic()
            if left < 10 or not self._on_the_line(task):
                break
            said = await self._ask(task, worker_id, doc, gap, min(QUESTION_WAIT_S, left))
            if said is None:
                break
            if said.kind == "answer":
                document = fill(document, gap, said.value)
                changes.append(f"added {label_of(gap)}")
            else:
                document, in_text = remove_gap(document, gap)
                if in_text:
                    self.orch.add_instruction(task.id, worker_id, removal(gap))  # with the last edit
                changes.append(f"left out {label_of(gap)}")
        return document, changes

    def on_the_line(self, task: Task) -> bool:
        return self._on_the_line(task)

    def _on_the_line(self, task: Task) -> bool:
        sess = self.orch.s.sessions.get(task.session_id)
        return sess is not None and sess.state not in (ConvState.OFFLINE, ConvState.RECONNECTING)

    async def _ask(self, task: Task, worker_id: str, doc, gap: str, wait_s: float) -> Optional[Reading]:
        """One gap's question, voiced on the call once. The caller's replies are read until one answers it or says
        they don't have it; anything else ("one minute", a question of their own) leaves the question open for
        their next words. None: nobody answered in time, or the caller hung up."""
        question = question_for(gap)
        qid = self.orch.ask(task.id, worker_id, question, NO_ANSWER, ttl_s=wait_s)
        if qid is None:
            raise ThinkerError("lease_lost", "task no longer owned")
        q, until, log = self.orch.s.questions[qid], time.monotonic() + wait_s, logger.bind(task=task.id[-6:])
        while True:
            await self.store.flush()  # the question reaches the call (or is open again)
            while q.answer is None:
                t = self.orch.s.tasks[task.id]
                if t.worker_id != worker_id or t.state != TaskState.AWAITING_INPUT:
                    break  # cancelled, or the lease was lost: checked below
                if not self._on_the_line(task) or time.monotonic() > until:
                    self.orch.withdraw_question(task.id, worker_id)
                    break
                await asyncio.sleep(0.2)
            t = self.orch.s.tasks[task.id]
            if t.worker_id != worker_id or t.state != TaskState.RUNNING:
                raise ThinkerError("lease_lost", "task no longer owned")
            if q.answer in (None, NO_ANSWER):
                await self.store.flush()
                log.info("question not answered")
                return None
            # part of the conversation from now on: a later edit, or a redraft, sees the answer too
            t.transcript = f"{t.transcript}\nBolDoc: {question}\nCaller: {q.answer}".strip()
            try:
                said, _ = await interpret(self.key, gap, question, q.answer, doc.doc_lang)
            except ThinkerError as e:
                log.warning(f"reply not understood ({e.code})")
                said = Reading("other", "", "")
            if said.change:  # asked for along with the reply: the talker may not have passed it on
                self.orch.add_instruction(task.id, worker_id, said.change)
            log.info(f"question: reply read as {said.kind}")
            if said.kind != "other":
                await self.store.flush()
                return said
            if time.monotonic() > until - 3 or not self.orch.reopen_question(task.id, worker_id):
                await self.store.flush()
                return None

    async def _produce(self, task: Task, worker_id: str, beat=None) -> Tuple[Document, List[str], dict]:
        doc = self.orch.s.docs[task.doc_id]
        if task.kind == "create" and task.tier == "T3":
            if self.big is None:  # Hermes not configured (any more): today's writer writes it
                document, changes, stats = await self.draft_t2(task)
                return document, changes, {**stats, "fallback_from": "T3", "fallback_reason": "hermes_off"}
            return await self.big.write(task, worker_id)
        if task.kind == "create":
            return await self.draft_t2(task)
        base_version = self.orch.rebase(task.id, worker_id)
        if base_version is None:
            raise ThinkerError("lease_lost", "task no longer owned")
        base = self.orch.s.versions[(doc.id, base_version)]
        if task.kind == "undo":
            prev = self.orch.s.versions.get((doc.id, base_version - 1))
            if prev is None:
                raise ThinkerError("nothing_to_undo", f"no version before v{base_version}")
            return prev, [f"restored version {base_version - 1}"], {"tier": "T0"}
        create = next((t for t in self.orch._doc_tasks(doc.id) if t.kind == "create"), None)
        intent = "; ".join(create.instructions) if create else ""
        return await edit(self.key, base, task.instructions, task.transcript, doc.doc_lang, doc.formats,
                          intent=intent, **_no_redraft(doc))

    async def draft_t2(self, task: Task, extra: str = "") -> Tuple[Document, List[str], dict]:
        """Today's writer: one draft call and its review (also the fallback for a long document)."""
        doc = self.orch.s.docs[task.doc_id]
        sess = self.orch.s.sessions.get(doc.session_id)
        facts = await self.store.known_facts(sess.user_id) if sess is not None and sess.user_id else {}
        intent = "; ".join(task.instructions) + (f". {extra}" if extra else "")
        document, stats = await draft(self.key, task.transcript, intent, doc.id, doc.formats, doc_lang=doc.doc_lang,
                                      known_facts=facts)
        return document, ["wrote the first draft"], stats

    # ------------------------------------------------------------------ delivery
    async def _deliveries(self) -> None:
        while True:
            try:
                pending = [d for d in self.orch.s.deliveries.values() if d.status == "pending" and d.channel == "sms"]
                for d in pending:
                    await self._send_sms(d.id)
                self.delivery_wake.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.delivery_wake.wait(), 2.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("delivery loop error")
                await asyncio.sleep(2.0)

    async def send_delivery(self, delivery_id: str) -> str:
        """Send one pending delivery, at most once; returns its status (the workflow then awaits the receipt)."""
        d = self.orch.delivery(delivery_id)
        if d is None:
            return "unknown"
        if d.status == "pending":
            await self._send_sms(delivery_id)
        return self.orch.delivery(delivery_id).status

    async def _send_sms(self, delivery_id: str) -> None:
        d = self.orch.delivery(delivery_id)
        doc = self.orch.s.docs[d.doc_id]
        if self.sms is None:
            self.orch.mark_delivery(d.id, "failed", error="sms_disabled")
            await self.store.flush()
            return
        if self.bus is not None and not await self.bus.allow(f"sms:{d.recipient}:{time.strftime('%Y%m%d')}",
                                                             self.sms_daily_limit, 86400):
            logger.warning(f"sms to …{d.recipient[-4:]} not sent: daily limit reached")
            self.orch.mark_delivery(d.id, "failed", error="daily_limit")
            await self.store.flush()
            return
        text = compose(doc.title, self.link_for(doc.id), d.purpose)
        self.orch.mark_delivery(d.id, "sending")
        await self.store.flush()  # durable BEFORE the provider call: a crash here never causes a second SMS
        try:
            ref = await self.sms.send(d.recipient, text)
            self.orch.mark_delivery(d.id, "submitted", provider_ref=ref)
            logger.info(f"sms {d.purpose} for doc …{doc.id[-6:]} to …{d.recipient[-4:]} submitted ({len(text)} chars)")
        except SmsError as e:
            logger.warning(f"sms to …{d.recipient[-4:]} failed: {e}")
            self.orch.mark_delivery(d.id, "failed", error=e.code)
        await self.store.flush()

    def on_receipt(self, provider_ref: str, status: str, error: Optional[str]) -> bool:
        """Plivo delivery receipt -> delivery status (forward-only; duplicates and late receipts are ignored)."""
        d = next((x for x in self.orch.s.deliveries.values() if x.provider_ref == provider_ref), None)
        if d is None:
            return False
        mapped = {"delivered": "delivered", "failed": "failed", "undelivered": "failed", "rejected": "failed"}
        new = mapped.get(status.lower())
        return bool(new) and self.orch.mark_delivery(d.id, new, error=error if new == "failed" else None)

    # ------------------------------------------------------------------ outbox -> durable workflows
    async def _reconcile(self) -> None:
        """Every queued task and pending delivery gets a workflow (ids are deterministic, so starting twice is a
        no-op). If Temporal is briefly down, they are picked up on the next pass: nothing is lost."""
        while True:
            try:
                now = time.monotonic()
                for t in [t for t in self.orch.s.tasks.values() if t.state.value == "queued"]:
                    big = t.tier == "T3"
                    wid = f"big-{t.id}" if big else f"task-{t.id}"
                    if now - self._started.get(wid, -1e9) > 30 and await self.flows.start_task(t.id, wid, big=big):
                        self._started[wid] = now
                for d in [d for d in self.orch.s.deliveries.values() if d.status == "pending" and d.channel == "sms"]:
                    wid = f"dlv-{d.id}"
                    if now - self._started.get(wid, -1e9) > 30 and await self.flows.start_delivery(d.id, wid):
                        self._started[wid] = now
                self.task_wake.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.task_wake.wait(), 2.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("reconciler error")
                await asyncio.sleep(2.0)

    # ------------------------------------------------------------------ sweep
    async def _retention(self, first_s: float = 600.0, every_s: float = 6 * 3600) -> None:
        """What the privacy page promises (apps/control/retention.py), every 6 hours."""
        from .retention import Retention
        await asyncio.sleep(first_s)
        while True:
            try:
                await Retention(self.orch, self.store).run()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"retention run failed: {type(e).__name__}: {e}")
            await asyncio.sleep(every_s)

    async def _sweeper(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                self.orch.expire()
                await self.store.flush()
                if any(t.state.value == "queued" for t in self.orch.s.tasks.values()):
                    self.task_wake.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("sweeper error")
