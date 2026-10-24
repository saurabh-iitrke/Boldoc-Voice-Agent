"""A document link opens on one device (the first that taps it) and for the signed-in owner anywhere; the owner can
stop it and get a new one; a browser call's document goes by SMS only when asked ("Send to my phone")."""
import re

import pytest

from tests.control.helpers import CALLER, FakeSms, H, create_cmd, open_call, until, wait_for
from tests.control.test_accounts import W, sign_in, with_redis
from tests.control.test_browser_calls import admit

asyncpg = pytest.importorskip("asyncpg")
OWNER = "+" + CALLER


def first_link(c):
    r = open_call(c, "call-1").json()
    doc_id = c.post("/internal/commands", json=create_cmd("call-1", r["epoch"]), headers=H).json()["doc_id"]
    wait_for(c, "call-1", 0, "task.completed")
    until(lambda: any("/d/" in text for _, text in FakeSms.sent))
    text = next(text for _, text in FakeSms.sent if "/d/" in text)
    return doc_id, re.search(r"/d/([A-Za-z0-9_-]{22})", text).group(1)


def test_a_link_opens_on_the_first_device_that_taps_it_and_for_the_signed_in_owner(app_factory):
    with app_factory() as c:
        with_redis(c)
        _, token = first_link(c)
        closed = c.get(f"/api/d/{token}")  # loading the page (or a link preview) never takes it
        assert closed.status_code == 403 and closed.json()["code"] == "link_unclaimed" and "doc" not in closed.json()
        assert c.post(f"/api/d/{token}/open").status_code == 403  # only the website's own page can tap
        opened = c.post(f"/api/d/{token}/open", headers=W)
        assert opened.status_code == 200 and opened.json()["doc"]["title"] == "Leave Application"
        held = c.cookies.get("boldoc_dv")
        assert held and c.get(f"/api/d/{token}").status_code == 200
        assert c.get(f"/api/d/{token}/download/docx").status_code == 200
        c.cookies.clear()  # another phone or computer
        locked = c.get(f"/api/d/{token}").json()
        assert locked["code"] == "link_locked" and locked["sent_to"].endswith("6655") and "doc" not in locked
        assert c.post(f"/api/d/{token}/open", headers=W).json()["code"] == "link_locked"
        assert c.get(f"/api/d/{token}/download/docx").status_code == 403
        sign_in(c, OWNER)  # the owner, signed in with the number it went to, opens it anywhere
        assert c.get(f"/api/d/{token}").status_code == 200
        c.cookies.clear()
        c.cookies.set("boldoc_dv", held)  # ...and the first device still has it
        assert c.get(f"/api/d/{token}").status_code == 200


def test_the_owner_stops_a_link_and_gets_a_new_one(app_factory):
    with app_factory() as c:
        with_redis(c)
        doc_id, old = first_link(c)
        assert c.post(f"/api/documents/{doc_id}/link", headers=W).status_code == 401  # signed out
        sign_in(c, OWNER)
        new = c.post(f"/api/documents/{doc_id}/link", headers=W).json()
        token = re.search(r"/d/([A-Za-z0-9_-]{22})$", new["link"]).group(1)
        assert new["stopped"] == 1 and token != old
        assert c.get(f"/api/d/{old}").status_code == 404
        assert c.get(f"/api/d/{token}").status_code == 200  # the owner opens it without taking it
        c.cookies.clear()
        assert c.get(f"/api/d/{token}").json()["code"] == "link_unclaimed"  # whoever they share it with taps first


def test_a_browser_calls_document_goes_by_sms_only_when_asked(app_factory, plivo):
    phone = "+919876500022"
    with app_factory() as c:
        with_redis(c)
        sign_in(c, phone)
        t = c.post("/api/calls/browser-token", headers=W).json()
        assert admit(c, "web-1", t["session"])["decision"] == "admit"
        r = c.post("/internal/sessions", json={"session_id": "web-1"}, headers=H).json()
        assert r["snapshot"]["on_screen"] and r["snapshot"]["verified_recipients"] == [phone]
        doc_id = c.post("/internal/commands", json=create_cmd("web-1", r["epoch"]), headers=H).json()["doc_id"]
        wait_for(c, "web-1", 0, "task.completed")
        c.post("/internal/sessions/web-1/end", headers=H)
        sent = len(FakeSms.sent)
        assert not any("/d/" in text for _, text in FakeSms.sent)  # on screen: no SMS by itself, not even at the end
        first = c.post(f"/api/documents/{doc_id}/send", headers=W).json()
        assert first == {"status": "accepted", "sent_to": first["sent_to"]} and first["sent_to"].endswith("0022")
        until(lambda: len(FakeSms.sent) == sent + 1)
        assert FakeSms.sent[-1][0] == phone and "/d/" in FakeSms.sent[-1][1]
        for _ in range(3):  # again on request, a few times
            assert c.post(f"/api/documents/{doc_id}/send", headers=W).json()["status"] == "accepted"
        assert c.post(f"/api/documents/{doc_id}/send", headers=W).json()["code"] == "resend_limit"
