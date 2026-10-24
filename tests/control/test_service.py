"""Control plane end to end on a real Postgres: talker commands -> thinker -> versions, files, link page, SMS,
delivery receipts, edits, call end, restart recovery. LLM calls and Plivo are faked; rendering is real."""
import asyncio
import re
import time

import pytest

from tests.control.helpers import FakeSms, H, create_cmd, open_call, until, wait_for

asyncpg = pytest.importorskip("asyncpg")


def test_call_to_document_to_sms_to_edit_to_update(app_factory):
    with app_factory() as c:
        assert c.post("/internal/sessions", json={"session_id": "call-1"}).status_code == 401
        r = open_call(c, "call-1").json()
        assert r["epoch"] == 1 and r["snapshot"]["verified_recipients"] == ["+919988776655"]
        assert r["admission"]["caller"] == "+919988776655" and r["admission"]["first_call"] is True

        res = c.post("/internal/commands", json=create_cmd("call-1", 1), headers=H).json()
        assert res["status"] == "accepted" and res["eta_s"] == 15
        assert res["snapshot"]["tasks"][0]["kind"] == "create"
        assert c.post("/internal/commands", json=create_cmd("call-1", 1), headers=H).json()["status"] == "duplicate"
        bad = c.post("/internal/commands", json={"cmd": "create_doc", "session_id": "call-1"}, headers=H)
        assert bad.status_code == 422

        done, after, snap = wait_for(c, "call-1", 0, "task.completed")
        assert done["head_version"] == 1 and snap["docs"][0]["title"] == "Leave Application"

        until(lambda: len(FakeSms.sent) == 1)
        dst, text = FakeSms.sent[0]
        assert dst == "+919988776655" and len(text) <= 160 and '"Leave Application" is ready' in text
        token = re.search(r"https://boldoc\.test/d/([A-Za-z0-9_-]{22})", text).group(1)

        assert c.get(f"/api/d/{token}").json()["code"] == "link_unclaimed"  # it opens with a tap, on one device
        page = c.post(f"/api/d/{token}/open", headers={"X-BolDoc-Client": "web"})
        assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
        body = page.json()
        assert body["doc"]["title"] == "Leave Application" and body["doc"]["version"] == 1
        assert body["formats"] == ["docx", "md"] and body["delivery"]["recipient_masked"].endswith("6655")
        assert "9988776655" not in page.text  # the public page never shows the full number
        dl = c.get(f"/api/d/{token}/download/docx")
        assert dl.status_code == 200 and dl.content[:2] == b"PK"
        assert "Leave_Application_v1.docx" in dl.headers["content-disposition"]
        assert c.get(f"/api/d/{token}/download/pptx").status_code == 404
        assert c.get("/api/d/notatoken0000000000000").status_code == 404

        # delivery receipt -> delivered event (duplicates/late receipts change nothing)
        assert c.post("/plivo/sms-status", data={"MessageUUID": "msg-1", "Status": "delivered"}).status_code == 200
        wait_for(c, "call-1", after, "delivery.delivered")
        c.post("/plivo/sms-status", data={"MessageUUID": "msg-1", "Status": "sent"})
        assert c.get(f"/api/d/{token}").json()["delivery"]["status"] == "delivered"

        # an edit makes version 2 on the same link; no SMS during the call
        doc_id = snap["docs"][0]["id"]
        edit = {"cmd": "edit_doc", "session_id": "call-1", "epoch": 1, "turn_id": "tc2", "doc_id": doc_id,
                "base_version": 1, "instruction": "make it 26 to 27", "transcript": "Caller: 26 se kar do"}
        assert c.post("/internal/commands", json=edit, headers=H).json()["status"] == "accepted"
        done2, after, snap = wait_for(c, "call-1", after, "task.completed")
        assert done2["head_version"] == 2
        body = c.get(f"/api/d/{token}").json()
        assert body["doc"]["version"] == 2 and [v["version"] for v in body["versions"]] == [2, 1]
        assert "26 to 27" in str(body["doc"]["blocks"]) and len(FakeSms.sent) == 1

        # call ends -> exactly one "updated" SMS with the same link
        assert c.post("/internal/sessions/call-1/end", headers=H).json()["updates"] == 1
        until(lambda: len(FakeSms.sent) == 2)
        assert "was updated" in FakeSms.sent[1][1] and token in FakeSms.sent[1][1]
        assert c.post("/internal/sessions/call-1/end", headers=H).json()["updates"] == 0

        # a reconnecting talker takes over (new epoch); the old one is fenced off
        assert c.post("/internal/sessions", json={"session_id": "call-1"}, headers=H).json()["epoch"] == 2
        stale = c.post("/internal/commands", json=create_cmd("call-1", 1, "tc9"), headers=H).json()
        assert stale["status"] == "rejected" and stale["reason"] == "stale_epoch"


def test_restart_recovers_state_and_never_double_sends(app_factory):
    with app_factory() as c:
        open_call(c, "call-2", "+91 99887 76655")
        c.post("/internal/commands", json=create_cmd("call-2", 1), headers=H)
        wait_for(c, "call-2", 0, "task.completed")
        until(lambda: len(FakeSms.sent) == 1)
        token = re.search(r"/d/([A-Za-z0-9_-]{22})", FakeSms.sent[0][1]).group(1)
    with app_factory() as c:  # same database, fresh process
        h = c.get("/control/healthz").json()
        assert h["sessions"] == 1 and h["docs"] == 1
        assert c.post(f"/api/d/{token}/open", headers={"X-BolDoc-Client": "web"}).json()["doc"]["version"] == 1
        r = c.post("/internal/sessions", json={"session_id": "call-2"}, headers=H).json()
        assert r["epoch"] == 2 and r["snapshot"]["docs"][0]["deliveries"][0]["status"] == "submitted"
        time.sleep(0.5)
        assert len(FakeSms.sent) == 1  # nothing re-sent after the restart


def test_trace_is_stored_and_reported(app_factory, pg_url):
    from apps.control.report import load
    from tests.control.test_report import EVENTS
    with app_factory() as c:
        open_call(c, "call-3")
        bad = c.post("/internal/sessions/call-3/trace", json={"nope": 1}, headers=H)
        assert bad.status_code == 422
        r = c.post("/internal/sessions/call-3/trace", headers=H,
                   json={"call_id": "call-3", "started_wall": time.time(), "content": False, "events": EVENTS})
        assert r.json() == {"stored": len(EVENTS)}
    report = asyncio.run(load(pg_url, "latest"))
    assert report["session_id"] == "call-3" and report["summary"]["caller_turns"] == 3
    assert report["cost"]["total_inr"] >= 0 and report["cost"]["telephony_source"] == "estimate"

    async def stored():  # one row per caller turn (fleet percentiles) and the report itself, kept at trace time
        con = await asyncpg.connect(pg_url)
        try:
            turns = await con.fetch("select turn, reply, v2v_ms from call_turns where session_id='call-3' "
                                    "order by turn")
            kept = await con.fetchval("select report->'summary'->>'caller_turns' from call_reports "
                                      "where session_id='call-3'")
            return [tuple(t) for t in turns], kept
        finally:
            await con.close()
    turns, kept = asyncio.run(stored())
    assert len(turns) == 3 and kept == "3" and turns[0][0] == 1


def test_landline_and_foreign_callers_are_not_auto_verified():
    from apps.control.phones import indian_mobile
    assert indian_mobile("919988776655") == "+919988776655"
    assert indian_mobile("09988776655") == "+919988776655"
    assert indian_mobile("9988776655") == "+919988776655"
    assert indian_mobile("918035449833") is None        # Bengaluru landline
    assert indian_mobile("14155550123") is None         # US
    assert indian_mobile("sip:m0browser@phone.plivo.com") is None
    assert indian_mobile(None) is None


def test_bus_rate_limit_and_event_fanout():
    fakeredis = pytest.importorskip("fakeredis")
    from apps.control.bus import Bus

    async def go():
        bus = Bus()
        bus.r = fakeredis.FakeAsyncRedis(decode_responses=True)
        ok = [await bus.allow("sms:+919988776655:20261022", 3, 60) for _ in range(5)]
        sub = bus.r.pubsub()
        await sub.subscribe("boldoc:events:s1")
        await sub.get_message(timeout=1)  # subscribe confirmation
        await bus.publish_events([("s1", 1, '{"type": "task.completed"}')])
        msg = await sub.get_message(timeout=1)
        await sub.aclose()
        return ok, msg
    ok, msg = asyncio.run(go())
    assert ok == [True, True, True, False, False]
    assert msg and msg["data"] == '{"type": "task.completed"}'
    assert asyncio.run(Bus().allow("anything", 0, 1)) is True  # no Redis: limits fail open, never break a call
    assert asyncio.run(Bus().allow("otp", 5, 60, fail_open=False)) is False  # ...unless money is involved


def test_rate_limit_keys_always_expire_and_call_presence_expires():
    fakeredis = pytest.importorskip("fakeredis")
    from apps.control.bus import Bus
    from protocol.bus import ACTIVE_CALLS

    async def go():
        bus = Bus()
        bus.r = fakeredis.FakeAsyncRedis(decode_responses=True)
        await bus.allow("k", 5, 60)
        ttl = await bus.r.ttl("boldoc:limit:k")
        await bus.allow("k", 5, 60)  # a second hit never extends or resets the window
        ttl2 = await bus.r.ttl("boldoc:limit:k")
        await bus.mark_active("call-a")
        await bus.r.zadd(ACTIVE_CALLS, {"call-stale": time.time() - 120})  # talker died two minutes ago
        active = await bus.active_calls()
        await bus.clear_active("call-a")
        return ttl, ttl2, active, await bus.active_calls()
    ttl, ttl2, active, after = asyncio.run(go())
    assert 0 < ttl <= 60 and 0 < ttl2 <= ttl
    assert active == ["call-a"] and after == []
