"""A caller changes a document from an earlier call ("call again and say what to change")."""
import asyncio

import pytest

from tests.control.helpers import FakeSms, H, create_cmd, open_call, until, wait_for

asyncpg = pytest.importorskip("asyncpg")


def edit_cmd(sid, epoch, doc_id, base, turn="e1", text="change the dates to 26 to 27 October"):
    return {"cmd": "edit_doc", "session_id": sid, "epoch": epoch, "turn_id": turn, "doc_id": doc_id,
            "base_version": base, "instruction": text, "transcript": f"Caller: {text}"}


def make_first_doc(c):
    r = open_call(c, "call-1").json()
    res = c.post("/internal/commands", json=create_cmd("call-1", r["epoch"]), headers=H).json()
    wait_for(c, "call-1", 0, "task.completed")
    c.post("/internal/sessions/call-1/end", headers=H)
    c.post("/internal/calls/call-1/hangup", json={"Duration": "60"}, headers=H)
    return res["doc_id"]


def test_a_later_call_changes_an_earlier_document_and_hears_about_it(app_factory):
    with app_factory() as c:
        doc_id = make_first_doc(c)
        until(lambda: len(FakeSms.sent) == 1)                       # the first version's link
        r = open_call(c, "call-2").json()
        assert [d["id"] for d in r["recent_docs"]] == [doc_id]     # the talker can offer it by title
        assert r["recent_docs"][0]["title"] == "Leave Application" and r["snapshot"]["docs"] == []
        res = c.post("/internal/commands", json=edit_cmd("call-2", r["epoch"], doc_id, 1), headers=H).json()
        assert res["status"] == "accepted"
        assert [d["id"] for d in res["snapshot"]["docs"]] == [doc_id]   # now part of this call
        ev, _, _ = wait_for(c, "call-2", 0, "task.completed")
        assert ev["doc_id"] == doc_id and ev["head_version"] == 2   # the call that asked hears it's done
        c.post("/internal/sessions/call-2/end", headers=H)          # call ends: one "updated" SMS for v2
        until(lambda: len(FakeSms.sent) == 2)
        assert "was updated" in FakeSms.sent[1][1]


def test_someone_elses_document_stays_out_of_reach(app_factory):
    with app_factory() as c:
        doc_id = make_first_doc(c)
        other = c.post("/internal/calls/admit", json={"call_uuid": "x-1", "from": "919876543210"}, headers=H).json()
        assert other["decision"] == "admit"
        r = c.post("/internal/sessions", json={"session_id": "x-1"}, headers=H).json()
        assert r["recent_docs"] == []
        res = c.post("/internal/commands", json=edit_cmd("x-1", r["epoch"], doc_id, 1), headers=H).json()
        assert res["status"] == "rejected" and res["reason"] == "unknown_doc"


def test_documents_older_than_the_startup_window_load_when_needed(app_factory, pg_url):
    with app_factory() as c:
        doc_id = make_first_doc(c)

    async def age():  # pretend the first call was two weeks ago: startup loads only the last 7 days
        con = await asyncpg.connect(pg_url)
        try:
            await con.execute("update entities set updated_at = now() - interval '14 days'")
        finally:
            await con.close()
    asyncio.run(age())
    with app_factory() as c:  # a restart on the same database (the fixture wipes it only once per test)
        from apps.control import service
        assert doc_id not in service.S.orch.s.docs
        r = open_call(c, "call-3").json()
        res = c.post("/internal/commands", json=edit_cmd("call-3", r["epoch"], doc_id, 1), headers=H).json()
        assert res["status"] == "accepted" and doc_id in service.S.orch.s.docs
        ev, _, _ = wait_for(c, "call-3", 0, "task.completed")
        assert ev["head_version"] == 2

