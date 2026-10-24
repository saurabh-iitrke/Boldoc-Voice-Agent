import pytest

from apps.control.core.orchestrator import Orchestrator
from docengine.model import Document, Paragraph, Run
from protocol.fsm import TaskState
from protocol.messages import Answer, Cancel, CreateDoc, Deliver, EditDoc, Undo


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def env():
    clock = Clock()
    orch = Orchestrator(clock=clock, coalesce_window_s=2.5, lease_s=30, max_attempts=3)
    orch.open_session("s1", "pstn", "+919812345678")
    orch.verify_recipient("s1", "+919812345678")
    return orch, clock


def doc(text="hello", title="Note"):
    return Document(id="x", title=title, blocks=[Paragraph(runs=[Run(text=text)])])


def create(orch, turn="t1", epoch=1):
    return orch.handle(CreateDoc(session_id="s1", epoch=epoch, turn_id=turn, formats=["pdf"], intent="leave letter"))


def edit(orch, doc_id, base, text, turn, epoch=1):
    return orch.handle(EditDoc(session_id="s1", epoch=epoch, turn_id=turn, doc_id=doc_id, base_version=base,
                               instruction=text))


def run_to_commit(orch, worker="w1", text="v"):
    t = orch.claim_next(worker)
    assert t is not None
    return t, orch.commit(t.id, worker, doc(text), [f"changed {text}"])


def test_create_commit_deliver_exactly_once(env):
    orch, _ = env
    r = create(orch)
    assert r.status == "accepted"
    _, c = run_to_commit(orch)
    assert c.status == "committed" and c.version == 1
    d1 = orch.handle(Deliver(session_id="s1", epoch=1, turn_id="t2", doc_id=r.doc_id, channel="sms",
                             recipient="+919812345678"))
    d2 = orch.handle(Deliver(session_id="s1", epoch=1, turn_id="t3", doc_id=r.doc_id, channel="sms",
                             recipient="+919812345678"))
    assert d1.status == "accepted" and d2.status == "duplicate" and d1.delivery_id == d2.delivery_id
    types = [e.type for e in orch.s.events["s1"]]
    assert types.count("delivery.submitted") == 1 and "task.completed" in types


def test_stale_epoch_rejected_and_replay_after_takeover_is_duplicate(env):
    orch, _ = env
    first = create(orch, turn="t1", epoch=1)
    orch.open_session("s1", "pstn")  # talker re-dispatched -> epoch 2
    stale = edit(orch, first.doc_id, 0, "x", "t9", epoch=1)
    assert stale.status == "rejected" and stale.reason == "stale_epoch"
    replay = create(orch, turn="t1", epoch=2)  # new talker replays the same tool call
    assert replay.status == "duplicate" and replay.task_id == first.task_id
    assert len(orch.s.tasks) == 1


def test_edits_during_draft_go_to_inbox_and_none_are_lost(env):
    orch, _ = env
    r = create(orch)
    t = orch.claim_next("w1")
    edit(orch, r.doc_id, 0, "make it formal", "t2")
    assert t.inbox == ["make it formal"]
    assert orch.consume_inbox(t.id, "w1") == ["make it formal"]  # thinker reads at a tool boundary
    edit(orch, r.doc_id, 0, "add my phone number", "t3")       # arrives after the last boundary
    c = orch.commit(t.id, "w1", doc("draft"), ["drafted"])
    assert c.status == "committed" and c.follow_up_task_id is not None
    follow = orch.s.tasks[c.follow_up_task_id]
    assert follow.instructions == ["add my phone number"] and follow.base_version == 1


def test_coalescing_window(env):
    orch, clock = env
    r = create(orch)
    run_to_commit(orch)
    a = edit(orch, r.doc_id, 1, "change date", "t2")
    clock.now += 1.0
    b = edit(orch, r.doc_id, 1, "change subject", "t3")
    assert b.merged_into == a.task_id and orch.s.tasks[a.task_id].instructions == ["change date", "change subject"]
    clock.now += 5.0
    c = edit(orch, r.doc_id, 1, "add a closing line", "t4")
    assert c.task_id != a.task_id and c.merged_into is None


def test_stale_base_version_rebases_instead_of_overwriting(env):
    orch, clock = env
    r = create(orch)
    run_to_commit(orch)                        # v1
    stale = edit(orch, r.doc_id, 1, "edit A", "t2")
    clock.now += 10
    edit(orch, r.doc_id, 1, "edit B", "t3")    # separate task (outside window)
    run_to_commit(orch, text="A")              # v2 from task A
    t = orch.claim_next("w1")                  # task B still based on v1
    c = orch.commit(t.id, "w1", doc("B"), ["B"])
    assert c.status == "conflict" and orch.s.tasks[t.id].base_version == 2
    t2, c2 = run_to_commit(orch, text="B")
    assert t2.id == t.id and c2.version == 3
    assert stale.status == "accepted"


def test_worker_crash_lease_expiry_requeues_and_fences_old_worker(env):
    orch, clock = env
    create(orch)
    t = orch.claim_next("w1")
    clock.now += 31
    orch.expire()
    assert t.state == TaskState.QUEUED
    assert orch.commit(t.id, "w1", doc(), ["x"]).status == "rejected"  # zombie worker can't commit
    t_again = orch.claim_next("w2")
    assert t_again.id == t.id and orch.commit(t.id, "w2", doc(), ["x"]).status == "committed"


def test_retries_exhausted_emits_failure_with_instructions(env):
    orch, _ = env
    create(orch)
    for w in ("w1", "w2", "w3"):
        t = orch.claim_next(w)
        orch.fail(t.id, w, "llm_timeout", retryable=True)
    assert t.state == TaskState.FAILED
    failed = [e for e in orch.s.events["s1"] if e.type == "task.failed"]
    assert failed and failed[0].payload["instructions"] == ["leave letter"]


def test_question_answer_and_default_on_expiry(env):
    orch, clock = env
    create(orch)
    t = orch.claim_next("w1")
    q = orch.ask(t.id, "w1", "Include GST?", default="no", ttl_s=20)
    assert t.state == TaskState.AWAITING_INPUT
    assert orch.handle(Answer(session_id="s1", epoch=1, turn_id="t5", question_id=q, value="yes")).status == "accepted"
    assert t.state == TaskState.RUNNING and orch.s.questions[q].answer == "yes"
    q2 = orch.ask(t.id, "w1", "Landscape?", default="no", ttl_s=20)
    orch.go_offline("s1")                       # caller hung up mid-question
    clock.now += 21
    orch.expire()
    assert orch.s.questions[q2].answer == "no" and t.state == TaskState.RUNNING


def test_a_question_goes_to_the_call_it_is_asked_on(env):
    """A long document's question goes to whichever of the caller's calls is live (a callback, or a
    call they made later) and only that call may answer it."""
    orch, _ = env
    r = create(orch)
    t = orch.claim_next("w1")
    orch.go_offline("s1")                                     # they hung up; we call back
    orch.open_session("cb", "pstn", "+919812345678")
    assert orch.ask(t.id, "w1", "Loan kitne ka?", "", session_id="cb") is None  # its document isn't open there
    orch.attach("cb", r.doc_id)
    q = orch.ask(t.id, "w1", "Loan kitne ka?", "", session_id="cb")
    assert [e.type for e in orch.s.events["cb"]] == ["task.question"] and orch.snapshot("cb")["question"]["id"] == q
    assert orch.snapshot("s1")["question"] is None
    late = Answer(session_id="s1", epoch=1, turn_id="t6", question_id=q, value="8 lakh")
    assert orch.handle(late).reason == "task_not_waiting"    # not the call it was asked on
    ok = orch.handle(Answer(session_id="cb", epoch=1, turn_id="t7", question_id=q, value="8 lakh"))
    assert ok.status == "accepted" and orch.s.questions[q].answer == "8 lakh" and t.state == TaskState.RUNNING


def test_cancel_before_and_after_commit(env):
    orch, _ = env
    r = create(orch)
    run_to_commit(orch)
    e = edit(orch, r.doc_id, 1, "x", "t2")
    assert orch.handle(Cancel(session_id="s1", epoch=1, turn_id="t3", task_id=e.task_id)).status == "accepted"
    assert orch.s.tasks[e.task_id].state == TaskState.CANCELLED
    e2 = edit(orch, r.doc_id, 1, "y", "t4")
    run_to_commit(orch, text="y")              # v2
    undo = orch.handle(Cancel(session_id="s1", epoch=1, turn_id="t5", task_id=e2.task_id))
    assert undo.status == "accepted" and orch.s.tasks[undo.task_id].kind == "undo"


def test_undo_needs_two_versions_and_deliver_needs_verified_recipient(env):
    orch, _ = env
    r = create(orch)
    run_to_commit(orch)
    assert orch.handle(Undo(session_id="s1", epoch=1, turn_id="t2", doc_id=r.doc_id)).reason == "nothing_to_undo"
    bad = orch.handle(Deliver(session_id="s1", epoch=1, turn_id="t3", doc_id=r.doc_id, channel="sms",
                              recipient="+919999999999"))
    assert bad.reason == "unverified_recipient"


def test_finalize_updates_once_per_new_version(env):
    orch, _ = env
    r = create(orch)
    run_to_commit(orch)
    orch.handle(Deliver(session_id="s1", epoch=1, turn_id="t2", doc_id=r.doc_id, channel="sms",
                        recipient="+919812345678"))
    edit(orch, r.doc_id, 1, "x", "t3")
    run_to_commit(orch, text="x")
    assert len(orch.finalize_updates("s1")) == 1
    assert orch.finalize_updates("s1") == []   # idempotent: nothing new to send
