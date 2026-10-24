"""More numbers on one account: each added with a code sent to it; a call from one is the account's call, it signs in
to the account, and what it made before shows in the account (and can be changed) while it stays linked."""
import pytest

from tests.control.helpers import FakeSms, H, create_cmd, open_call, wait_for
from tests.control.test_accounts import TEST_NUMBER, W, sign_in, with_redis
from tests.control.test_docs_across_calls import edit_cmd

asyncpg = pytest.importorskip("asyncpg")

PHONE = "+919876500011"  # the phone being added: its code comes by (fake) SMS


def code_for(c, phone):
    r = c.post("/api/numbers/code", json={"phone": phone}, headers=W)
    assert r.status_code == 200, r.text
    assert FakeSms.sent[-1][0] == phone and "add this number to your account" in FakeSms.sent[-1][1]
    return FakeSms.sent[-1][1].split()[0]


def add(c, phone):
    return c.post("/api/numbers", json={"phone": phone, "code": code_for(c, phone)}, headers=W)


def test_an_added_number_is_the_account_until_it_is_removed(app_factory):
    with app_factory() as c:
        with_redis(c)
        r = open_call(c, "early", caller=PHONE[1:]).json()  # the phone wrote a document before it was added
        early = c.post("/internal/commands", json=create_cmd("early", r["epoch"]), headers=H).json()["doc_id"]
        wait_for(c, "early", 0, "task.completed")
        c.post("/internal/sessions/early/end", headers=H)
        c.post("/internal/calls/early/hangup", json={"Duration": "60"}, headers=H)
        me = sign_in(c)
        assert c.get("/api/documents").json() == []
        code = code_for(c, PHONE)
        wrong = c.post("/api/numbers", json={"phone": PHONE, "code": "111111" if code != "111111" else "222222"},
                       headers=W)
        assert wrong.status_code == 400 and wrong.json()["code"] == "wrong_code"
        added = c.post("/api/numbers", json={"phone": PHONE, "code": code}, headers=W).json()
        assert [(n["phone_e164"], n["primary"]) for n in added["numbers"]] == [(TEST_NUMBER, True), (PHONE, False)]
        assert [d["id"] for d in c.get("/api/documents").json()] == [early]  # what it made shows in the account
        adm = c.post("/internal/calls/admit", json={"call_uuid": "later", "from": PHONE[1:]}, headers=H).json()
        assert adm["plan"] == "credits" and adm["user_id"] == me["id"]  # its calls are the account's calls
        later = c.post("/internal/sessions", json={"session_id": "later"}, headers=H).json()
        assert [d["id"] for d in later["recent_docs"]] == [early]
        c.post("/internal/sessions/later/end", headers=H)
        c.post("/internal/calls/later/hangup", json={"Duration": "30"}, headers=H)
        own = open_call(c, "own", caller=TEST_NUMBER[1:]).json()  # the account's own number may change it too
        res = c.post("/internal/commands", json=edit_cmd("own", own["epoch"], early, 1), headers=H).json()
        assert res["status"] == "accepted"
        assert sign_in(c, PHONE)["id"] == me["id"]  # it signs in to the account, not to an account of its own
        assert c.delete(f"/api/numbers/{TEST_NUMBER}", headers=W).json()["code"] == "own_number"
        left = c.delete(f"/api/numbers/{PHONE}", headers=W).json()
        assert [n["phone_e164"] for n in left["numbers"]] == [TEST_NUMBER]
        assert c.get("/api/documents").json() == []  # removed: what it made before is its own again
        again = c.post("/internal/calls/admit", json={"call_uuid": "after", "from": PHONE[1:]}, headers=H).json()
        assert again["plan"] == "trial" and again["user_id"] != me["id"]


def test_which_numbers_can_be_added(app_factory):
    with app_factory() as c:
        with_redis(c)
        sign_in(c, "+919876500022")  # a number with an account of its own
        sign_in(c)
        why = c.post("/api/numbers/code", json={"phone": TEST_NUMBER}, headers=W).json()
        assert why["code"] == "own_number"
        sent = len(FakeSms.sent)
        why = c.post("/api/numbers/code", json={"phone": "+919876500022"}, headers=W).json()
        assert why["code"] == "other_account" and len(FakeSms.sent) == sent  # refused before any SMS
        for n in (1, 2, 3):
            assert add(c, f"+91987650003{n}").status_code == 200
        why = c.post("/api/numbers/code", json={"phone": "+919876500034"}, headers=W).json()
        assert why["code"] == "too_many"
        sign_in(c, "+919876500022")  # another account can't take a number that is already on one
        why = c.post("/api/numbers/code", json={"phone": "+919876500031"}, headers=W).json()
        assert why["code"] == "other_account"
        assert c.post("/api/numbers/code", json={"phone": "+919876500031"}).status_code == 403  # website only
