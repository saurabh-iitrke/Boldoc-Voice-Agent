"""Retention: the privacy page's promises, enforced (transcripts, unclaimed documents, words in traces, memory)."""
import asyncio
import time

import pytest

from tests.control.helpers import H, create_cmd, open_call, wait_for

asyncpg = pytest.importorskip("asyncpg")
DAY = 86400


def sql(pg_url, query, *args):
    async def go():
        con = await asyncpg.connect(pg_url)
        try:
            return [dict(r) for r in await con.fetch(query, *args)]
        finally:
            await con.close()
    return asyncio.run(go())


def run(c, days_later=0.0, **kw):
    from apps.control import service
    from apps.control.retention import Retention
    r = Retention(service.S.orch, service.S.store, clock=lambda: time.time() + days_later * DAY, **kw)
    return c.portal.call(r.run)


def make_doc(c, sid):
    r = open_call(c, sid).json()
    c.post("/internal/commands", json=create_cmd(sid, r["epoch"]), headers=H)
    wait_for(c, sid, 0, "task.completed")
    c.post(f"/internal/sessions/{sid}/end", headers=H)


def test_conversation_text_goes_after_30_days_and_never_while_work_is_unfinished(app_factory, pg_url):
    from apps.control import service
    from apps.control.retention import REDACTED
    with app_factory() as c:
        make_doc(c, "r1")
        task = next(t for t in service.S.orch.s.tasks.values() if t.session_id == "r1")
        assert task.transcript
        task.big = {"brief": {"facts": {"loan": "4 lakh"}}, "answers": [{"question": "Bank?", "answer": "PNB"}]}
        stay = {"memory_days": 60}  # keep the session in memory: this test is about the in-memory path
        assert run(c, days_later=10, **stay)["transcripts_in_memory"] == 0   # 10 days: kept
        assert run(c, days_later=31, **stay)["transcripts_in_memory"] == 1   # 31 days: removed
        assert task.transcript == "" and task.instructions == [REDACTED]
        assert task.big == {}  # a long document's brief and the caller's answers go with the conversation
    stored = sql(pg_url, "select data->>'transcript' as t, data->'instructions' as i from entities where kind='task'")
    assert stored and all(r["t"] == "" for r in stored)  # flushed: a later flush can't write it back


def test_replies_to_the_writers_questions_go_with_the_rest_of_the_conversation(app_factory, pg_url):
    from apps.control import service
    from apps.control.core.orchestrator import Question
    from apps.control.retention import REDACTED
    with app_factory() as c:
        make_doc(c, "r5")
        s = service.S.orch.s
        task = next(t for t in s.tasks.values() if t.session_id == "r5")
        s.questions["q_r5"] = Question(id="q_r5", task_id=task.id, text="What is your employee ID?",
                                       default="no answer", expires_at=0, answer="mera employee ID E-4521 hai")
        c.portal.call(service.S.store.flush)
        run(c, days_later=31, memory_days=60)
        assert s.questions["q_r5"].answer == REDACTED
    stored = sql(pg_url, "select data->>'answer' as a from entities where kind = 'question'")
    assert [r["a"] for r in stored] == [REDACTED]


def test_replies_only_in_the_database_are_redacted_there(app_factory, pg_url):
    from apps.control import service
    from apps.control.core.orchestrator import Question
    from apps.control.retention import REDACTED
    with app_factory() as c:
        make_doc(c, "r6")
        task = next(t for t in service.S.orch.s.tasks.values() if t.session_id == "r6")
        service.S.orch.s.questions["q_r6"] = Question(id="q_r6", task_id=task.id, text="?", default="no answer",
                                                      expires_at=0, answer="E-4521")
        c.portal.call(service.S.store.flush)
        assert run(c, days_later=8)["sessions_evicted"] == 1        # left memory after a week
        sql(pg_url, "update entities set updated_at = now() - interval '31 days' where kind = 'question'")
        assert run(c)["answers_stored"] == 1
    stored = sql(pg_url, "select data->>'answer' as a from entities where kind = 'question'")
    assert [r["a"] for r in stored] == [REDACTED]


def test_tasks_only_in_the_database_are_redacted_there(app_factory, pg_url):
    from apps.control import service
    with app_factory() as c:
        make_doc(c, "r3")
        assert run(c, days_later=8)["sessions_evicted"] == 1        # left memory after a week
        assert not [t for t in service.S.orch.s.tasks.values() if t.session_id == "r3"]
        sql(pg_url, "update entities set updated_at = now() - interval '31 days' where kind = 'task'")
        assert run(c)["transcripts_stored"] == 1
    stored = sql(pg_url, "select data->>'transcript' as t from entities where kind='task'")
    assert [r["t"] for r in stored] == [""]


def test_dont_keep_removes_the_text_at_the_next_run(app_factory, pg_url):
    from apps.control import service
    with app_factory() as c:
        make_doc(c, "r2")
        user_id = service.S.orch.s.sessions["r2"].user_id
        sql(pg_url, "insert into profiles (user_id, retention_days) values ($1, 0)", user_id)
        assert run(c)["transcripts_in_memory"] == 1


def test_unclaimed_documents_go_after_30_days_claimed_ones_stay(app_factory, pg_url):
    from apps.control import service
    with app_factory() as c:
        make_doc(c, "u1")                       # caller 919988776655: trial, never signed in
        claimed = c.post("/internal/calls/admit", json={"call_uuid": "u2", "from": "919876543210"}, headers=H)
        assert claimed.json()["decision"] == "admit"
        r = c.post("/internal/sessions", json={"session_id": "u2"}, headers=H).json()
        c.post("/internal/commands", json=create_cmd("u2", r["epoch"]), headers=H)
        wait_for(c, "u2", 0, "task.completed")
        sql(pg_url, "update users set status='active' where phone_e164='+919876543210'")
        sql(pg_url, "update documents set updated_at = now() - interval '40 days'")
        for sid in ("u1", "u2"):  # make them old enough to leave memory too
            for d in [d for d in service.S.orch.s.docs.values() if d.session_id == sid]:
                service.S.orch.s.docs.pop(d.id)
        out = run(c)
        assert out["unclaimed_documents"] == 1
    left = sql(pg_url, "select owner from documents")
    assert [r["owner"] for r in left] == ["+919876543210"]
    assert sql(pg_url, "select count(*) as n from renditions r join documents d on d.id = r.doc_id")[0]["n"] > 0


def test_words_in_test_traces_go_after_30_days(app_factory, pg_url):
    from tests.control.test_report import EVENTS
    with app_factory() as c:
        open_call(c, "t1")
        events = [dict(e, text="something the caller said") if e["e"] == "turn_end" else e for e in EVENTS]
        c.post("/internal/sessions/t1/trace", headers=H,
               json={"call_id": "t1", "started_wall": time.time(), "content": True, "events": events})
        sql(pg_url, "update call_traces set created_at = now() - interval '31 days'")
        sql(pg_url, "update call_reports set created_at = now() - interval '31 days'")
        out = run(c)
        assert out["trace_words"] == 1 and out["report_words"] == 1
    trace = sql(pg_url, "select trace::text as t from call_traces")[0]["t"]
    report = sql(pg_url, "select report::text as r from call_reports")[0]["r"]
    assert "something the caller said" not in trace and "something the caller said" not in report


def test_finished_sessions_leave_memory_after_a_week(app_factory):
    from apps.control import service
    with app_factory() as c:
        make_doc(c, "m1")
        assert run(c, days_later=3)["sessions_evicted"] == 0
        assert run(c, days_later=8)["sessions_evicted"] == 1
        assert "m1" not in service.S.orch.s.sessions
        assert not [d for d in service.S.orch.s.docs.values() if d.session_id == "m1"]
