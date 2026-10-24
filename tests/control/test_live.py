"""The browser call's live view: server events translated to the UI's LiveEvent, only for the caller who started it."""
import asyncio
import json

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.control.conftest import configure
from tests.control.helpers import FakeSms, H, create_cmd, wait_for
from tests.control.test_accounts import W, sign_in, with_redis
from tests.control.test_browser_calls import SIP

pytest.importorskip("asyncpg")
pytest.importorskip("fakeredis")
ORIGIN = {"origin": "https://boldoc.test"}


def test_talker_and_control_events_become_the_ui_events():
    from apps.control.live import Translator

    class Store:
        async def document_view(self, doc_id):
            return {"doc": {"id": doc_id, "title": "Leave Application", "version": 2, "blocks": []}}
    tr = Translator(Store(), lambda dlv: "+919988776655" if dlv == "dlv_1" else None)
    assert tr.talker({"kind": "state", "state": "thinking"}) == {"kind": "agent", "state": "thinking"}
    assert tr.talker({"kind": "caller", "text": " PDF chahiye "}) == \
        {"kind": "caption", "id": "c1", "speaker": "you", "text": "PDF chahiye", "final": True}
    assert tr.talker({"kind": "agent", "text": "Ji."})["id"] == "a2"
    assert tr.talker({"kind": "caller", "text": ""}) is None
    assert tr.talker({"kind": "ended"}) == {"kind": "call", "state": "ended"}

    async def go():
        progress = await tr.event({"seq": 3, "type": "task.progress", "task_id": "t1", "payload": {"stage": "started"}})
        done = await tr.event({"seq": 4, "type": "task.completed", "task_id": "t1", "doc_id": "doc_1",
                               "head_version": 2, "payload": {"diff_summary": ["dates: 26-27 October"]}})
        sms = await tr.event({"seq": 5, "type": "delivery.delivered", "doc_id": "doc_1", "head_version": 2,
                              "ts": 1792643600, "payload": {"delivery_id": "dlv_1", "channel": "sms"}})
        again = await tr.event({"seq": 4, "type": "task.completed", "doc_id": "doc_1"})   # replayed, then published
        return progress, done, sms, again
    progress, done, sms, again = asyncio.run(go())
    assert progress["stage"] == "Writing" and progress["type"] == "progress"   # words, not the internal "started"
    assert done["kind"] == "doc" and done["doc"]["version"] == 2 and done["changes"] == ["dates: 26-27 October"]
    assert sms["delivery"] == {"channel": "sms", "recipient_masked": "••••• 6655", "status": "delivered",
                               "version": 2, "at": "2026-10-22T04:33:20+00:00"}
    assert again is None


def browser_call(c):
    """A signed-in user's browser call, admitted, with a document written on it."""
    sign_in(c)
    session = c.post("/api/calls/browser-token", headers=W).json()["session"]
    adm = c.post("/internal/calls/admit", json={"call_uuid": "b-1", "from": SIP, "session_token": session},
                 headers=H).json()
    assert adm["decision"] == "admit"
    r = c.post("/internal/sessions", json={"session_id": "b-1"}, headers=H).json()
    c.post("/internal/commands", json=create_cmd("b-1", r["epoch"]), headers=H)
    wait_for(c, "b-1", 0, "task.completed")
    return session


@pytest.fixture
def live_cfg(plivo, monkeypatch):
    configure(monkeypatch, public_hosts=("boldoc.test",))
    return plivo


def test_the_caller_sees_their_call_live_and_a_reconnect_misses_nothing(app_factory, live_cfg):
    from apps.control import service
    from protocol.bus import live_channel
    with app_factory() as c:
        svc = with_redis(c)
        session = browser_call(c)
        with c.websocket_connect(f"/api/live?session={session}", headers=ORIGIN) as ws:
            first = [ws.receive_json() for _ in range(3)]       # the durable events so far, replayed
            assert [e["kind"] for e in first] == ["task", "task", "doc"]
            assert first[0]["type"] == "accepted" and first[2]["doc"]["title"] == "Leave Application"
            assert FakeSms.sent == []   # a test number never gets an SMS: it may belong to a stranger

            def say(**m):
                c.portal.call(svc.S.bus.r.publish, live_channel("b-1"), json.dumps(m))
            say(kind="caller", text="dates 26 se 27 kar dijiye")
            assert ws.receive_json() == {"kind": "caption", "id": "c1", "speaker": "you",
                                         "text": "dates 26 se 27 kar dijiye", "final": True}
            say(kind="ended")
            assert ws.receive_json() == {"kind": "call", "state": "ended"}
            last = first[2]["seq"]
        # a reconnect after the last event it saw gets nothing twice
        with c.websocket_connect(f"/api/live?session={session}&after={last}", headers=ORIGIN) as ws:
            c.portal.call(service.S.bus.r.publish, live_channel("b-1"), json.dumps({"kind": "state",
                                                                                  "state": "listening"}))
            assert ws.receive_json() == {"kind": "agent", "state": "listening"}


def test_only_the_caller_on_our_own_pages_can_watch(app_factory, live_cfg):
    with app_factory() as c:
        with_redis(c)
        session = browser_call(c)
        evil = {"origin": "https://evil.example"}   # another site's page, with the user's cookie
        with pytest.raises(WebSocketDisconnect), c.websocket_connect(f"/api/live?session={session}", headers=evil):
            pass
        c.post("/api/auth/logout", headers=W)
        with pytest.raises(WebSocketDisconnect), c.websocket_connect(f"/api/live?session={session}", headers=ORIGIN):
            pass  # signed out
