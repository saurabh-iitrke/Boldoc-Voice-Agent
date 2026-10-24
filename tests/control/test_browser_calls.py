"""'Talk in browser': the website's one-time session makes a WebRTC call the signed-in account's call."""
import asyncio
import json

import httpx
import pytest

from tests.control.conftest import configure
from tests.control.helpers import H
from tests.control.test_accounts import TEST_NUMBER, W, sign_in, with_redis

pytest.importorskip("asyncpg")
pytest.importorskip("fakeredis")
SIP = "sip:boldocweb262294678398013331755703@phone.plivo.com"


def admit(c, call, session=None):
    body = {"call_uuid": call, "from": SIP, "to": "sip:boldoc@phone.plivo.com"}
    if session:
        body["session_token"] = session
    return c.post("/internal/calls/admit", json=body, headers=H).json()


def test_a_browser_call_is_the_signed_in_accounts_call_and_its_session_works_once(app_factory, plivo):
    from apps.control.accounts import browser
    with app_factory() as c:
        with_redis(c)
        sign_in(c)
        r = c.post("/api/calls/browser-token", headers=W)
        assert r.status_code == 200, r.text
        t = r.json()
        assert t["token"] == "jwt-1" and t["destination"] == "boldoc" and len(t["session"]) >= 22
        assert plivo == [t["max_call_s"] + 300]          # the token lives as long as the longest call it can start

        adm = admit(c, "b-1", t["session"])
        assert (adm["decision"], adm["channel"], adm["caller"], adm["caller_kind"]) == \
            ("admit", "sip", TEST_NUMBER, "browser")
        assert adm["user_id"] is not None and adm["plan"] == "credits"
        assert admit(c, "b-1", t["session"])["decision"] == "admit"     # Plivo retried the webhook: same answer
        again = admit(c, "b-2", t["session"])                           # a second call with the same session
        assert (again["decision"], again["reason"]) == ("refuse", "browser_unauthenticated")
        assert c.portal.call(browser.call_for, t["session"]) == {"call_uuid": "b-1", "user_id": adm["user_id"]}

        s = c.post("/internal/sessions", json={"session_id": "b-1"}, headers=H).json()
        assert s["admission"]["caller"] == TEST_NUMBER    # the account's phone (a test number: never sent an SMS)
        assert s["snapshot"]["verified_recipients"] == []


def test_browser_calls_need_the_website_session(app_factory, plivo):
    with app_factory() as c:
        with_redis(c)
        assert admit(c, "x-1")["reason"] == "browser_unauthenticated"               # no session header
        assert admit(c, "x-2", "made-up-session-1234567")["reason"] == "browser_unauthenticated"
        assert c.post("/api/calls/browser-token").status_code == 403                # not from the website
        r = c.post("/api/calls/browser-token", headers=W)
        assert r.status_code == 401 and r.json()["code"] == "signed_out"
        assert plivo == []


def test_talking_in_the_browser_is_only_for_signed_up_accounts(app_factory, plivo):
    """No free try in the browser : a browser call needs a signed-in account's session."""
    with app_factory() as c:
        with_redis(c)
        r = c.post("/api/calls/browser-token", headers=W)
        assert r.status_code == 401 and r.json()["code"] == "signed_out"
        for gone in ("/api/calls/trial-challenge", "/api/calls/trial-token", "/api/calls/claim"):
            assert c.post(gone, json={}, headers=W).status_code == 404
        refused = admit(c, "anon-1", "a-session-nobody-signed-in-for")
        assert (refused["decision"], refused["reason"], refused["clip"]) == \
            ("refuse", "browser_unauthenticated", "refused")
        assert plivo == []                                   # no Plivo token for anyone signed out


def test_no_token_when_the_call_would_be_refused_anyway(app_factory, plivo, pg_url):
    from apps.control import service
    with app_factory() as c:
        with_redis(c)
        me = sign_in(c)

        async def spend():  # use up the sign-up credits
            async with service.S.store.pool.acquire() as con:
                from apps.control.accounts import ledger
                await ledger.post(con, me["id"], "adjust", -await ledger.balance(con, me["id"]), "drain", reason="test")
        c.portal.call(spend)
        r = c.post("/api/calls/browser-token", headers=W)
        assert r.status_code == 402 and r.json()["code"] == "no_credits"
        assert plivo == []


def test_busy_is_said_before_the_microphone_prompt(app_factory, plivo, monkeypatch):
    configure(monkeypatch, max_concurrent_calls=0)
    with app_factory() as c:
        with_redis(c)
        sign_in(c)
        r = c.post("/api/calls/browser-token", headers=W)
        assert r.status_code == 503 and r.json()["code"] == "busy" and plivo == []


def test_a_plivo_failure_leaves_no_usable_session(app_factory, monkeypatch):
    from apps.control.accounts import browser

    async def down(ttl_s):
        raise httpx.ConnectError("plivo down")
    monkeypatch.setattr(browser, "plivo_token", down)
    configure(monkeypatch)
    with app_factory() as c:
        svc = with_redis(c)
        sign_in(c)
        r = c.post("/api/calls/browser-token", headers=W)
        assert r.status_code == 503 and r.json()["code"] == "browser_unavailable"
        keys = c.portal.call(svc.S.bus.r.keys, "boldoc:bt:*")
        assert keys == []


def test_the_plivo_token_is_outgoing_only_for_the_shared_endpoint(monkeypatch):
    from apps.control.accounts import browser
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"api_id": "a", "token": "eyJ.jwt"})
    monkeypatch.setattr(browser.CTX, "auth_id", "MAXXX")
    monkeypatch.setattr(browser.CTX, "auth_token", "tok")
    monkeypatch.setattr(browser.CTX, "endpoint", "boldocweb1")
    monkeypatch.setattr(browser.CTX, "app_id", "123")

    async def go():
        browser.CTX.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await browser.plivo_token(600)
        finally:
            await browser.CTX.http.aclose()
            browser.CTX.http = None
    assert asyncio.run(go()) == "eyJ.jwt"
    b = seen["body"]
    assert seen["url"] == "https://api.plivo.com/v1/Account/MAXXX/JWT/Token/" and seen["auth"].startswith("Basic ")
    assert (b["iss"], b["sub"], b["app"]) == ("MAXXX", "boldocweb1", "123")
    assert b["per"] == {"voice": {"incoming_allow": False, "outgoing_allow": True}}
    assert b["exp"] - b["nbf"] == 660    # valid from a minute ago (clock skew) for the call's length


def test_the_language_a_call_ends_in_starts_the_accounts_next_call_however_they_call(app_factory, plivo):
    """The talker reports the language at the end; admission hands it to the next call of the same account; a language
    picked on the website for a call still comes first (the talker)."""
    with app_factory() as c:
        with_redis(c)
        sign_in(c)
        assert c.get("/api/me", headers=W).json()["call_lang"] is None
        t = c.post("/api/calls/browser-token", headers=W).json()
        first = admit(c, "lang-1", t["session"])
        assert first["decision"] == "admit" and first["remembered_lang"] is None and first["lang"] is None
        c.post("/internal/sessions", json={"session_id": "lang-1"}, headers=H)
        assert c.post("/internal/sessions/lang-1/end", json={"lang": "hi-IN"}, headers=H).status_code == 200
        c.post("/internal/calls/lang-1/hangup", json={"Duration": "40"}, headers=H)
        assert c.get("/api/me", headers=W).json()["call_lang"] == "hi-IN"

        phone = c.post("/internal/calls/admit", json={"call_uuid": "lang-2", "from": TEST_NUMBER[1:]},
                       headers=H).json()  # the same account, calling from its phone
        assert phone["decision"] == "admit" and phone["remembered_lang"] == "hi-IN"
        c.post("/internal/sessions", json={"session_id": "lang-2"}, headers=H)
        # an unknown language, or none (a call they said nothing in), changes nothing
        assert c.post("/internal/sessions/lang-2/end", json={"lang": "xx-IN"}, headers=H).status_code == 200
        c.post("/internal/calls/lang-2/hangup", json={"Duration": "20"}, headers=H)

        picked = c.post("/api/calls/browser-token", json={"lang": "en-IN"}, headers=W).json()
        third = admit(c, "lang-3", picked["session"])
        assert (third["lang"], third["remembered_lang"]) == ("en-IN", "hi-IN")  # the talker starts in the pick
        c.post("/internal/sessions", json={"session_id": "lang-3"}, headers=H)
        assert c.post("/internal/sessions/lang-3/end", headers=H).status_code == 200
        assert c.get("/api/me", headers=W).json()["call_lang"] == "hi-IN"
