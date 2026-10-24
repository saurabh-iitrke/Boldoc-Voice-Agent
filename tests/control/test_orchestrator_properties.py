"""Randomised interleavings of commands, duplicates, stale talkers, worker crashes and timeouts.

M3 exit criterion: ≥1,000 interleavings with no lost edits, monotonic contiguous versions,
single active task per document, exactly-once deliveries, ordered event streams.
"""
from collections import Counter

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from apps.control.core.orchestrator import ACTIVE, Orchestrator
from docengine.model import Document, Paragraph, Run
from protocol.fsm import TERMINAL, TaskState
from protocol.messages import Answer, Cancel, CreateDoc, Deliver, EditDoc, Undo

PHONE = "+919812345678"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class OrchestratorMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.clock = Clock()
        counter = iter(range(1, 10**9))
        self.orch = Orchestrator(clock=self.clock, coalesce_window_s=2.5, lease_s=30, max_attempts=3,
                                 id_factory=lambda prefix: f"{prefix}_{next(counter):06d}")
        self.orch.open_session("s", "pstn", PHONE)
        self.orch.verify_recipient("s", PHONE)
        self.turn = 0
        self.sent = []            # commands accepted at least once (for replays)
        self.instructions = []    # every instruction we saw accepted (must never vanish)
        self.terminal_seen = {}   # task_id -> terminal state (must never change)

    # ---------------------------------------------------------------- helpers
    def _epoch(self):
        return self.orch.s.sessions["s"].epoch

    def _next_turn(self):
        self.turn += 1
        return f"t{self.turn}"

    def _docs(self):
        return list(self.orch.s.docs.values())

    def _send(self, cmd):
        res = self.orch.handle(cmd)
        if res.status == "accepted":
            self.sent.append(cmd)
            if isinstance(cmd, (CreateDoc, EditDoc)):
                self.instructions.append(cmd.intent if isinstance(cmd, CreateDoc) else cmd.instruction)
        return res

    # ---------------------------------------------------------------- talker-side rules
    @rule(n=st.integers(0, 10_000))
    def create(self, n):
        self._send(CreateDoc(session_id="s", epoch=self._epoch(), turn_id=self._next_turn(), formats=["pdf"],
                             intent=f"draft-{n}-{self.turn}"))

    @precondition(lambda self: self._docs())
    @rule(data=st.data(), delta=st.integers(-1, 1))
    def edit(self, data, delta):
        d = data.draw(st.sampled_from(self._docs()))
        self._send(EditDoc(session_id="s", epoch=self._epoch(), turn_id=self._next_turn(), doc_id=d.id,
                           base_version=max(0, d.head_version + delta), instruction=f"edit-{self.turn}"))

    @precondition(lambda self: self.sent)
    @rule(data=st.data())
    def replay(self, data):
        cmd = data.draw(st.sampled_from(self.sent))
        before = len(self.orch.s.tasks), len(self.orch.s.deliveries)
        res = self.orch.handle(cmd.model_copy(update={"epoch": self._epoch()}))  # re-dispatched talker replays
        assert res.status == "duplicate"
        assert (len(self.orch.s.tasks), len(self.orch.s.deliveries)) == before

    @rule()
    def takeover(self):
        old = self._epoch()
        self.orch.open_session("s", "pstn")  # reconnect / re-dispatch bumps the epoch
        assert self._epoch() == old + 1
        before = len(self.orch.s.tasks)
        res = self.orch.handle(CreateDoc(session_id="s", epoch=old, turn_id=self._next_turn(), formats=["pdf"],
                                         intent="from-stale-talker"))
        assert res.status == "rejected" and len(self.orch.s.tasks) == before

    @precondition(lambda self: self.orch.s.tasks)
    @rule(data=st.data())
    def cancel(self, data):
        t = data.draw(st.sampled_from(sorted(self.orch.s.tasks.values(), key=lambda x: x.id)))
        self._send(Cancel(session_id="s", epoch=self._epoch(), turn_id=self._next_turn(), task_id=t.id))

    @precondition(lambda self: self._docs())
    @rule(data=st.data())
    def undo(self, data):
        d = data.draw(st.sampled_from(self._docs()))
        self._send(Undo(session_id="s", epoch=self._epoch(), turn_id=self._next_turn(), doc_id=d.id))

    @precondition(lambda self: self._docs())
    @rule(data=st.data(), purpose=st.sampled_from(["initial", "updated", "resend"]))
    def deliver(self, data, purpose):
        d = data.draw(st.sampled_from(self._docs()))
        self._send(Deliver(session_id="s", epoch=self._epoch(), turn_id=self._next_turn(), doc_id=d.id,
                           channel="sms", recipient=PHONE, purpose=purpose))

    @precondition(lambda self: self.orch.s.questions)
    @rule(data=st.data())
    def answer(self, data):
        q = data.draw(st.sampled_from(sorted(self.orch.s.questions.values(), key=lambda x: x.id)))
        self._send(Answer(session_id="s", epoch=self._epoch(), turn_id=self._next_turn(), question_id=q.id,
                          value="yes"))

    # ---------------------------------------------------------------- thinker-side rules
    @rule(worker=st.sampled_from(["w1", "w2", "w3"]))
    def claim(self, worker):
        self.orch.claim_next(worker)

    def _running(self):
        return sorted((t for t in self.orch.s.tasks.values() if t.state == TaskState.RUNNING), key=lambda t: t.id)

    @precondition(lambda self: self._running())
    @rule(data=st.data(), consume_first=st.booleans())
    def commit(self, data, consume_first):
        t = data.draw(st.sampled_from(self._running()))
        if consume_first:
            self.orch.consume_inbox(t.id, t.worker_id)
        body = Document(id="x", title="t", blocks=[Paragraph(runs=[Run(text=t.id)])])
        self.orch.commit(t.id, t.worker_id, body, ["changed"])

    @precondition(lambda self: self._running())
    @rule(data=st.data(), retryable=st.booleans())
    def fail(self, data, retryable):
        t = data.draw(st.sampled_from(self._running()))
        self.orch.fail(t.id, t.worker_id, "boom", retryable)

    @precondition(lambda self: self._running())
    @rule(data=st.data())
    def ask(self, data):
        t = data.draw(st.sampled_from(self._running()))
        self.orch.ask(t.id, t.worker_id, "q?", default="no", ttl_s=20)

    @precondition(lambda self: self.orch.s.tasks)
    @rule(data=st.data())
    def zombie_commit(self, data):
        t = data.draw(st.sampled_from(sorted(self.orch.s.tasks.values(), key=lambda x: x.id)))
        before = {d.id: d.head_version for d in self._docs()}
        res = self.orch.commit(t.id, "zombie", Document(id="x", title="z"), ["z"])
        assert res.status == "rejected"
        assert before == {d.id: d.head_version for d in self._docs()}

    @rule(seconds=st.sampled_from([0.5, 3, 25, 40]))
    def tick(self, seconds):
        self.clock.now += seconds
        self.orch.expire()

    @rule()
    def hang_up_and_finalize(self):
        self.orch.go_offline("s")
        self.orch.finalize_updates("s")

    # ---------------------------------------------------------------- invariants
    @invariant()
    def versions_contiguous_and_stored(self):
        for d in self._docs():
            assert all((d.id, v) in self.orch.s.versions for v in range(1, d.head_version + 1))
            assert (d.id, d.head_version + 1) not in self.orch.s.versions

    @invariant()
    def single_active_task_per_doc(self):
        active = Counter(t.doc_id for t in self.orch.s.tasks.values() if t.state in ACTIVE)
        assert all(n <= 1 for n in active.values())

    @invariant()
    def no_lost_instructions(self):
        holders = Counter()
        for t in self.orch.s.tasks.values():
            for ins in t.instructions + t.inbox:
                holders[ins] += 1
        for ins in self.instructions:
            assert holders[ins] == 1, f"instruction {ins!r} held by {holders[ins]} tasks"

    @invariant()
    def failed_or_cancelled_work_is_announced(self):
        announced = {e.task_id for e in self.orch.s.events.get("s", []) if e.type in ("task.failed", "task.cancelled")}
        for t in self.orch.s.tasks.values():
            if t.state in (TaskState.FAILED, TaskState.CANCELLED):
                assert t.id in announced

    @invariant()
    def terminal_states_never_change(self):
        for t in self.orch.s.tasks.values():
            if t.id in self.terminal_seen:
                assert t.state == self.terminal_seen[t.id]
            elif t.state in TERMINAL:
                self.terminal_seen[t.id] = t.state

    @invariant()
    def deliveries_exactly_once(self):
        keys = list(self.orch.s.deliveries)
        assert len(keys) == len(set(keys))
        initial = Counter(k[:2] for k in keys if k[2] == "initial")
        assert all(n == 1 for n in initial.values())
        updated = Counter((k[0], k[1], k[3]) for k in keys if k[2] == "updated")
        assert all(n == 1 for n in updated.values())

    @invariant()
    def event_stream_ordered(self):
        seqs = [e.seq for e in self.orch.s.events.get("s", [])]
        assert seqs == list(range(1, len(seqs) + 1))


TestOrchestratorInterleavings = OrchestratorMachine.TestCase
TestOrchestratorInterleavings.settings = settings(max_examples=1000, stateful_step_count=40, deadline=None)
