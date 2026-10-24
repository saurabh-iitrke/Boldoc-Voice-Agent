"""Call me: the account asks BolDoc to ring one of its own numbers; picked up, it is that number's phone call (same
plan, credits and documents). Never a number that isn't the account's; the token works once."""
import pytest

from tests.control.helpers import H
from tests.control.test_accounts import TEST_NUMBER, W, sign_in, with_redis

asyncpg = pytest.importorskip("asyncpg")
PHONE = "+919876500022"


@pytest.fixture
def rang(monkeypatch):
    """Plivo's Call API, faked: the numbers rung and the answer-URL tokens."""
    from apps.control.accounts import callme
    calls = []

    async def place(number, token):
        calls.append((number, token))
        return "req-1"
    monkeypatch.setattr(callme, "place_call", place)
    return calls


def answer(c, call_uuid, token):
    """What Plivo does when the phone is picked up: our number is From, the account's number is To."""
    return c.post("/internal/calls/admit", headers=H, json={"call_uuid": call_uuid, "from": "918035449833",
                                                            "to": PHONE[1:], "call_me": token}).json()


def test_call_me_rings_the_accounts_number_and_it_is_their_call(app_factory, rang):
    with app_factory() as c:
        with_redis(c)
        me = sign_in(c, PHONE)
        r = c.post("/api/calls/call-me", json={"phone": PHONE}, headers=W).json()
        assert r["calling"].endswith("022") and rang == [(PHONE, rang[0][1])]
        assert c.get(f"/api/calls/call-me/{r['id']}").json()["status"] == "calling"
        adm = answer(c, "cm-1", rang[0][1])
        assert (adm["decision"], adm["caller"], adm["plan"], adm["user_id"]) == ("admit", PHONE, "credits", me["id"])
        assert c.get(f"/api/calls/call-me/{r['id']}").json()["status"] == "answered"
        again = answer(c, "cm-2", rang[0][1])  # the token works once: a replay is not our call
        assert (again["decision"], again["reason"]) == ("refuse", "call_me_expired")
        s = c.post("/internal/sessions", json={"session_id": "cm-1"}, headers=H).json()
        assert s["snapshot"]["verified_recipients"] == [PHONE]  # the link goes to the number we rang
        # hung up: the console's panel learns the call has ended, and closes
        c.post("/internal/calls/cm-1/hangup", headers=H, json={"Duration": "40", "BillDuration": "60"})
        assert c.get(f"/api/calls/call-me/{r['id']}").json()["status"] == "ended"


def test_call_me_only_rings_the_accounts_own_numbers(app_factory, rang):
    with app_factory() as c:
        with_redis(c)
        assert c.post("/api/calls/call-me", json={"phone": PHONE}, headers=W).status_code == 401  # signed out
        sign_in(c, PHONE)
        assert c.post("/api/calls/call-me", json={"phone": PHONE}).status_code == 403  # not from the website
        stranger = c.post("/api/calls/call-me", json={"phone": "+919876500099"}, headers=W).json()
        assert stranger["code"] == "not_your_number" and rang == []
        for _ in range(3):
            assert c.post("/api/calls/call-me", json={"phone": PHONE}, headers=W).status_code == 200
        assert c.post("/api/calls/call-me", json={"phone": PHONE}, headers=W).status_code == 429  # a few at a time
        sign_in(c, TEST_NUMBER)  # another account can't watch this one's calls
        assert c.get("/api/calls/call-me/whatever").status_code == 404


def test_signed_in_users_get_the_member_number_and_call_me_rings_from_it(app_factory, monkeypatch, rang):
    from apps.control.accounts import callme
    from tests.control.conftest import configure
    configure(monkeypatch, member_dial_in_e164="+918035449820")
    with app_factory() as c:
        with_redis(c)
        me = sign_in(c, PHONE)
        assert me["dial_in"] == "+918035449820" and c.get("/api/config").json()["dial_in_e164"] == "+918035449833"
        assert callme.CTX.caller_id == "+918035449820"


def test_a_call_that_cant_be_placed_says_so(app_factory, monkeypatch):
    from apps.control.accounts import callme

    async def down(number, token):
        raise RuntimeError("plivo down")
    monkeypatch.setattr(callme, "place_call", down)
    with app_factory() as c:
        with_redis(c)
        sign_in(c, PHONE)
        r = c.post("/api/calls/call-me", json={"phone": PHONE}, headers=W)
        assert r.status_code == 503 and r.json()["code"] == "call_failed"
