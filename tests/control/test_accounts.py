"""Accounts: the free trial, credits and the ledger, web sign-in (OTP / verify by calling), "My details"."""
import asyncio
import dataclasses

import pytest

from apps.control.accounts.pricing import PRICING
from tests.control.helpers import CALLER, FakeSms, H, create_cmd, open_call, until, wait_for

B = PRICING.signup_bonus  # the sign-up credits


asyncpg = pytest.importorskip("asyncpg")
fakeredis = pytest.importorskip("fakeredis")
W = {"X-BolDoc-Client": "web"}
TEST_NUMBER = "+919999900001"


def with_redis(c):
    """Give the app a fake Redis, created on the app's own event loop."""
    from apps.control import service

    async def make():
        return fakeredis.FakeAsyncRedis(decode_responses=True)
    service.S.bus.r = c.portal.call(make)
    return service


def sql(pg_url, query, *args):
    async def go():
        con = await asyncpg.connect(pg_url)
        try:
            return [dict(r) for r in await con.fetch(query, *args)]
        finally:
            await con.close()
    return asyncio.run(go())


def hangup(c, call, seconds):
    return c.post(f"/internal/calls/{call}/hangup", headers=H, json={"Duration": str(seconds),
                                                                      "BillDuration": str(seconds)})


def sign_in(c, phone=TEST_NUMBER):
    """OTP sign-in with an allowlisted test number: no SMS; the code is read where the server keeps it."""
    from apps.control import service

    async def cooled():  # as if the 30 s resend wait had passed
        await service.S.bus.r.delete(f"boldoc:otp:cool:{phone}")
    c.portal.call(cooled)
    assert c.post("/api/auth/otp", json={"phone": phone}, headers=W).status_code == 200

    async def code():
        return await service.S.bus.r.get(f"boldoc:otp:test:{phone}")
    # a test number's code is kept on the server; a real number's comes by (fake) SMS
    otp = c.portal.call(code) if phone == TEST_NUMBER else FakeSms.sent[-1][1].split()[0]
    r = c.post("/api/auth/verify", json={"phone": phone, "code": otp}, headers=W)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_free_trial_counts_real_calls_and_ends_with_a_sign_up_sms(app_factory, pg_url, monkeypatch):
    from apps.control import calls
    # five calls in a row: the trial's own rules, without the daily fair-use limits (tests/control/test_fair_use.py)
    monkeypatch.setattr(calls, "PRICING", dataclasses.replace(calls.PRICING, burst_calls=99, trial_calls_per_day=99))
    with app_factory() as c:
        with_redis(c)
        first = c.post("/internal/calls/admit", json={"call_uuid": "t1", "from": CALLER}, headers=H).json()
        got = (first["decision"], first["plan"], first["max_call_s"], first["calls_left"])
        assert got == ("admit", "trial", 300, 3)
        hangup(c, "t1", 8)          # 8 s and nothing written: a wrong number doesn't use up the trial
        for n in (2, 3, 4):
            a = c.post("/internal/calls/admit", json={"call_uuid": f"t{n}", "from": CALLER}, headers=H).json()
            assert a["decision"] == "admit", a
            hangup(c, f"t{n}", 45)
        over = c.post("/internal/calls/admit", json={"call_uuid": "t5", "from": CALLER}, headers=H).json()
        assert over["decision"] == "refuse" and over["reason"] == "trial_over" and over["clip"] == "trial-over"
        until(lambda: any("free calls are used" in text for _, text in FakeSms.sent))
        c.post("/internal/calls/admit", json={"call_uuid": "t6", "from": CALLER}, headers=H)
        assert sum("free calls are used" in text for _, text in FakeSms.sent) == 1  # once a day, not every call
    counted = sql(pg_url, "select call_uuid, counted from call_records where call_uuid like 't%' order by call_uuid")
    assert [r["counted"] for r in counted if r["call_uuid"] in ("t1", "t2", "t3", "t4")] == [False, True, True, True]


def test_trial_calls_have_per_call_document_and_edit_limits(app_factory):
    with app_factory() as c:
        with_redis(c)
        r = open_call(c, "tr").json()
        assert r["admission"]["plan"] == "trial"
        assert r["admission"]["limits"] == {"docs_per_call": 2, "edits_per_doc": 3, "signup_bonus": B}
        e = r["epoch"]
        a = c.post("/internal/commands", json=create_cmd("tr", e, "c1"), headers=H).json()
        b = c.post("/internal/commands", json={**create_cmd("tr", e, "c2"), "intent": "second"}, headers=H).json()
        third = c.post("/internal/commands", json={**create_cmd("tr", e, "c3"), "intent": "third"}, headers=H).json()
        assert (a["status"], b["status"]) == ("accepted", "accepted")
        assert third["status"] == "rejected" and third["reason"] == "trial_doc_limit"
        ev, _, _ = wait_for(c, "tr", 0, "command.rejected")
        assert ev["payload"]["reason"] == "trial_doc_limit"
        wait_for(c, "tr", 0, "task.completed")
        doc_id = a["doc_id"]
        results = []
        for n in range(4):
            cmd = {"cmd": "edit_doc", "session_id": "tr", "epoch": e, "turn_id": f"e{n}", "doc_id": doc_id,
                   "base_version": 1, "instruction": f"change {n}", "transcript": "..."}
            results.append(c.post("/internal/commands", json=cmd, headers=H).json())
        assert results[-1]["status"] == "rejected" and results[-1]["reason"] == "trial_edit_limit"


def test_sign_in_with_otp_grants_the_bonus_once_and_a_session_cookie(app_factory, pg_url):
    with app_factory() as c:
        with_redis(c)
        assert c.get("/api/me").status_code == 204  # signed out is a normal state, not an error
        assert c.get("/api/credits").json() == {"message": "Please sign in again.", "code": "signed_out"}
        assert c.post("/api/auth/otp", json={"phone": TEST_NUMBER}).status_code == 403  # no client header: CSRF
        bad = c.post("/api/auth/otp", json={"phone": "12345"}, headers=W)
        assert bad.status_code == 422 and bad.json()["code"] == "invalid_number"
        assert c.post("/api/auth/otp", json={"phone": "99999 00001"}, headers=W).json() == {"resend_after_s": 30}
        again = c.post("/api/auth/otp", json={"phone": TEST_NUMBER}, headers=W)
        assert again.status_code == 429 and again.json()["code"] == "resend_too_soon"
        wrong = c.post("/api/auth/verify", json={"phone": TEST_NUMBER, "code": "000000"}, headers=W)
        if wrong.status_code == 400:  # (1 in a million: the random code really was 000000)
            assert wrong.json()["code"] == "wrong_code" and wrong.json()["attempts_left"] == 4
        from apps.control import service

        async def code():
            return await service.S.bus.r.get(f"boldoc:otp:test:{TEST_NUMBER}")
        me = c.post("/api/auth/verify", json={"phone": TEST_NUMBER, "code": c.portal.call(code)}, headers=W).json()
        assert me["status"] == "active" and me["credits"] == {"balance": B, "docs_estimate": PRICING.docs_estimate(B)}
        assert me["phone_masked"] == "+91 99••• ••001" and "boldoc_session" in c.cookies
        assert c.get("/api/me").json()["id"] == me["id"]
        assert me["dial_in"] == "+918035449833"  # no member number configured: the same one as the public pages
        credits = c.get("/api/credits").json()
        assert credits["ledger"][0]["kind"] == "signup_bonus" and credits["prices"]["document"] == 10
        assert c.post("/api/auth/logout", headers=W).status_code == 204
        assert c.get("/api/me").status_code == 204
        sign_in(c)  # signing in again: no second bonus
        assert c.get("/api/me").json()["credits"]["balance"] == B
    tokens = sql(pg_url, "select id_hash from auth_sessions")
    assert tokens and all(len(t["id_hash"]) == 64 for t in tokens)  # only hashes are stored


def test_real_numbers_get_the_code_by_sms_with_the_webotp_line(app_factory):
    with app_factory() as c:
        with_redis(c)
        assert c.post("/api/auth/otp", json={"phone": "+91 99887 76655"}, headers=W).status_code == 200
        dst, text = FakeSms.sent[-1]
        code = text.split()[0]
        assert dst == "+919988776655" and len(code) == 6 and text.endswith(f"@boldoc.test #{code}")


def test_otp_fails_closed_without_redis(app_factory):
    with app_factory() as c:  # no fake Redis attached: the bus has no client
        r = c.post("/api/auth/otp", json={"phone": TEST_NUMBER}, headers=W)
        assert r.status_code == 503 and r.json()["code"] == "signin_unavailable"


def test_verify_by_calling_signs_in_when_that_number_calls(app_factory):
    with app_factory() as c:
        with_redis(c)
        start = c.post("/api/auth/verify-by-call", json={"phone": "9988776655"}, headers=W).json()
        assert start["call"] == "+918035449833" and start["expires_in_s"] == 120
        assert c.get(f"/api/auth/verify-by-call/{start['nonce']}").json() == {"status": "pending"}
        call = c.post("/internal/calls/admit", json={"call_uuid": "v1", "from": CALLER}, headers=H).json()
        assert call["decision"] == "refuse" and call["reason"] == "verified" and call["clip"] == "verified"
        done = c.get(f"/api/auth/verify-by-call/{start['nonce']}").json()
        assert done["status"] == "verified" and done["me"]["status"] == "active"
        assert c.get("/api/me").json()["phone_e164"] == "+919988776655"
        assert c.get(f"/api/auth/verify-by-call/{start['nonce']}").status_code == 410  # used once
        # the next call from that number is a normal call on credits
        nxt = c.post("/internal/calls/admit", json={"call_uuid": "v2", "from": CALLER}, headers=H).json()
        assert nxt["decision"] == "admit" and nxt["plan"] == "credits" and nxt["max_call_s"] == 1800


def test_credits_pay_for_minutes_and_documents_exactly_once(app_factory, pg_url):
    with app_factory() as c:
        with_redis(c)
        sign_in(c, "+919988776655")          # active, with the sign-up credits
        r = open_call(c, "p1").json()
        assert r["admission"]["plan"] == "credits"
        res = c.post("/internal/commands", json=create_cmd("p1", r["epoch"]), headers=H).json()
        assert res["status"] == "accepted"
        wait_for(c, "p1", 0, "task.completed")
        hangup(c, "p1", 125)                  # 3 started minutes
        hangup(c, "p1", 125)                  # Plivo retried the webhook
        ledger = c.get("/api/credits").json()["ledger"]
        kinds = sorted((x["kind"], x["amount"]) for x in ledger)
        assert kinds == [("call_minutes", -12), ("doc_create", -10), ("signup_bonus", B)]
        assert c.get("/api/me").json()["credits"]["balance"] == B - 12 - 10
    rows = sql(pg_url, "select idem_key from credit_ledger order by id")
    assert len(rows) == 3 and rows[1]["idem_key"].startswith(("task:", "call:"))


def test_no_credits_means_no_new_calls_and_no_new_documents(app_factory, pg_url):
    with app_factory() as c:
        with_redis(c)
        me = sign_in(c, "+919988776655")
        sql(pg_url, "insert into credit_ledger (user_id, kind, amount, balance_after, idem_key) values "
                    f"($1, 'admin_adjust', {-B}, 0, 'test:drain')", me["id"])
        refused = c.post("/internal/calls/admit", json={"call_uuid": "n1", "from": CALLER}, headers=H).json()
        assert refused["decision"] == "refuse" and refused["reason"] == "no_credits"


def test_my_details_reach_the_thinker(app_factory, monkeypatch):
    from apps.control import worker
    from tests.control.helpers import fake_draft
    seen = {}

    async def capturing_draft(*a, **k):
        seen.update(k.get("known_facts") or {})
        return await fake_draft(*a, **k)
    monkeypatch.setattr(worker, "draft", capturing_draft)
    with app_factory() as c:
        with_redis(c)
        sign_in(c, "+919988776655")
        put = c.put("/api/profile", headers=W, json={"name": "Saurabh Kumar", "designation": "Senior Engineer",
                                                     "organisation": "Acme Technologies", "retention_days": 30})
        assert put.status_code == 200 and put.json()["designation"] == "Senior Engineer"
        bad = c.put("/api/profile", headers=W, json={"retention_days": 7})
        assert bad.status_code == 422 and bad.json()["code"] == "invalid_retention"
        r = open_call(c, "md").json()
        c.post("/internal/commands", json=create_cmd("md", r["epoch"]), headers=H)
        wait_for(c, "md", 0, "task.completed")
    assert seen == {"name": "Saurabh Kumar", "designation": "Senior Engineer", "organisation": "Acme Technologies"}


def test_ledger_is_idempotent_and_serializes_concurrent_writes(pg_url):
    from apps.control.accounts import ledger, users
    from apps.control.migrations import migrate
    from apps.control.store_pg import SCHEMA

    async def go():
        pool = await asyncpg.create_pool(pg_url, min_size=2, max_size=6)
        async with pool.acquire() as con:
            await con.execute("drop schema public cascade; create schema public;")
            await con.execute(SCHEMA)
            await migrate(con)
            user = await users.activate(con, "+919988776655")
            await users.activate(con, "+919988776655")  # verified again: still one bonus

        async def debit(i):
            async with pool.acquire() as con:
                return await ledger.post(con, user["id"], "doc_create", -10, f"task:{i % 5}")
        await asyncio.gather(*(debit(i) for i in range(20)))  # 20 attempts, 5 distinct tasks
        async with pool.acquire() as con:
            bal = await ledger.balance(con, user["id"])
            rows = await ledger.history(con, user["id"])
        await pool.close()
        return bal, rows
    bal, rows = asyncio.run(go())
    assert bal == B - 5 * 10 and len(rows) == 6
    assert [r["balance_after"] for r in reversed(rows)] == [B, B - 10, B - 20, B - 30, B - 40, B - 50]


def test_a_long_documents_questions_are_answered_on_its_page_by_its_owner_only(app_factory):
    """The SMS link opens the document's page, which shows the writer's questions; typed answers go to
    the writer (bigdoc._web_answers). Answers given on a call show as answered."""
    with app_factory() as c:
        with_redis(c)
        sign_in(c, "+919988776655")
        r = open_call(c, "qa").json()
        doc_id = c.post("/internal/commands", json=create_cmd("qa", r["epoch"]), headers=H).json()["doc_id"]
        wait_for(c, "qa", 0, "task.completed")
        assert c.get(f"/api/documents/{doc_id}").json()["questions"] == []        # nobody is waiting
        assert c.post(f"/api/documents/{doc_id}/answers", json={"answers": {"x": "y"}}, headers=W).status_code == 409
        from apps.control import service
        t = next(t for t in service.S.orch.s.tasks.values() if t.doc_id == doc_id)
        t.tier, t.big = "T3", {"waiting": True, "phone_answers": {"Loan kitne ka?": "8 lakh"},
                               "asked": {"questions": ["Loan kitne ka?", "Kaun sa bank?"]}}
        assert c.get(f"/api/documents/{doc_id}").json()["questions"] == [
            {"question": "Loan kitne ka?", "answer": "8 lakh"}, {"question": "Kaun sa bank?", "answer": None}]
        bad = c.post(f"/api/documents/{doc_id}/answers", json={"answers": {"Something else?": "x"}}, headers=W)
        assert bad.status_code == 400                                          # only what the writer asked
        ok = c.post(f"/api/documents/{doc_id}/answers", json={"answers": {"Kaun sa bank?": " PNB, Jaipur "}},
                    headers=W).json()
        assert ok["questions"][1] == {"question": "Kaun sa bank?", "answer": "PNB, Jaipur"}
        assert t.big["web_answers"] == {"Kaun sa bank?": "PNB, Jaipur"}
        c.post("/api/auth/logout", headers=W)
        sign_in(c, "+919876500044")                                            # someone else
        assert c.post(f"/api/documents/{doc_id}/answers", json={"answers": {"Kaun sa bank?": "x"}},
                      headers=W).status_code == 404


def test_a_long_document_being_written_opens_on_its_page_with_its_questions(app_factory):
    """A long document being written opens: how far the writer is, what it will cover, its questions (answered there),
    and it is in the owner's list as being written."""
    with app_factory() as c:
        with_redis(c)
        sign_in(c, "+919988776655")
        r = open_call(c, "pw").json()
        from apps.control import service
        from protocol.messages import CreateDoc
        outline = [{"id": "s1", "title": "Executive summary", "words": 300},
                   {"id": "s2", "title": "Repayment schedule", "words": 300}]

        def start():
            service.S.orch.big_docs = True
            res = service.S.orch.handle(CreateDoc(session_id="pw", epoch=r["epoch"], turn_id="t1", formats=["pdf"],
                                                  intent="project report", brief={"size": "long", "outline": outline,
                                                                                  "title": "MUDRA project report"}))
            t, _ = service.S.orch.claim(res.task_id, "w-test")  # the writer has it: nothing is committed yet
            t.big.update(waiting=True, asked={"questions": ["Kaun sa bank?"]})
            service.S.orch.progress(t.id, "section 1 of 2")
            return res
        res = c.portal.call(start)
        c.portal.call(service.S.store.flush)
        page = c.get(f"/api/documents/{res.doc_id}").json()
        assert page["doc"]["title"] == "MUDRA project report" and page["doc"]["version"] == 0
        assert page["writing"]["outline"] == ["Executive summary", "Repayment schedule"]
        assert page["writing"]["stage"] == "section 1 of 2" and page["writing"]["waiting"] is True
        assert page["formats"] == [] and page["questions"] == [{"question": "Kaun sa bank?", "answer": None}]
        ok = c.post(f"/api/documents/{res.doc_id}/answers", json={"answers": {"Kaun sa bank?": "PNB"}}, headers=W)
        assert ok.status_code == 200 and ok.json()["questions"][0]["answer"] == "PNB"
        listed = {d["id"]: d for d in c.get("/api/documents").json()}
        assert listed[res.doc_id]["head_version"] == 0  # in the list, as being written
        c.post("/api/auth/logout", headers=W)
        sign_in(c, "+919876500044")  # someone else
        assert c.get(f"/api/documents/{res.doc_id}").status_code == 404


def test_signed_in_users_see_and_download_only_their_documents(app_factory):
    with app_factory() as c:
        with_redis(c)
        sign_in(c, "+919988776655")
        r = open_call(c, "own").json()
        c.post("/internal/commands", json=create_cmd("own", r["epoch"]), headers=H)
        wait_for(c, "own", 0, "task.completed")
        other = c.post("/internal/calls/admit", json={"call_uuid": "theirs", "from": "919876543210"}, headers=H)
        assert other.json()["decision"] == "admit"
        r2 = c.post("/internal/sessions", json={"session_id": "theirs"}, headers=H).json()
        c.post("/internal/commands", json=create_cmd("theirs", r2["epoch"]), headers=H)
        wait_for(c, "theirs", 0, "task.completed")
        docs = c.get("/api/documents").json()
        assert [d["title"] for d in docs] == ["Leave Application"] and docs[0]["head_version"] == 1
        assert docs[0]["hint"] == "I request leave from 25 to 27 October."  # its subject is one word: not a hint
        mine = docs[0]["id"]
        detail = c.get(f"/api/documents/{mine}").json()
        assert detail["doc"]["title"] == "Leave Application" and "docx" in detail["formats"]
        f = c.get(f"/api/documents/{mine}/download/docx")
        assert f.status_code == 200 and "attachment" in f.headers["content-disposition"]
        from apps.control import service
        theirs = next(d.id for d in service.S.orch.s.docs.values() if d.session_id == "theirs")
        assert c.get(f"/api/documents/{theirs}").status_code == 404
        assert c.get(f"/api/documents/{theirs}/download/docx").status_code == 404
        c.post("/api/auth/logout", headers=W)
        assert c.get("/api/documents").status_code == 401


def test_a_test_number_gets_a_session_from_the_server_cli_and_nobody_else_does(app_factory, monkeypatch):
    """Automated browser tests sign in without spending OTPs; only allowlisted test numbers, only on the server."""

    from apps.control import service
    from apps.control.accounts import cli, users
    from apps.control.accounts.web import new_session

    monkeypatch.setattr(cli, "SETTINGS", dataclasses.replace(cli.SETTINGS, otp_test_numbers=(TEST_NUMBER,)))
    assert asyncio.run(cli._main(["session", "+919876543210"])) == 1     # a real number: refused
    with app_factory() as c:
        async def make():
            async with service.S.store.pool.acquire() as con:
                user = await users.activate(con, TEST_NUMBER)
                return await new_session(con, user["id"], "cli", "test")
        token = c.portal.call(make)
        c.cookies.set("boldoc_session", token)
        me = c.get("/api/me")
        assert me.status_code == 200 and me.json()["phone_e164"] == TEST_NUMBER


def test_the_talker_learns_the_callers_saved_name_for_the_welcome(app_factory):
    with app_factory() as c:
        with_redis(c)
        sign_in(c)
        c.put("/api/profile", json={"name": "Saurabh Kumar"}, headers=W)
        adm = c.post("/internal/calls/admit", json={"call_uuid": "w-1", "from": TEST_NUMBER.lstrip("+")},
                     headers=H).json()
        assert adm["decision"] == "admit"
        s = c.post("/internal/sessions", json={"session_id": "w-1"}, headers=H).json()
        assert s["admission"]["name"] == "Saurabh Kumar"


def test_a_name_said_on_a_call_is_kept_but_never_overwrites_my_details(app_factory):
    with app_factory() as c:
        with_redis(c)
        assert c.post("/internal/calls/admit", json={"call_uuid": "n-1", "from": "919876543210"}, headers=H).json()[
            "decision"] == "admit"
        c.post("/internal/sessions", json={"session_id": "n-1"}, headers=H)
        r = c.post("/internal/sessions/n-1/caller-name", json={"name": "Saurabh"}, headers=H)
        assert r.json() == {"saved": True}
        again = c.post("/internal/sessions/n-1/caller-name", json={"name": "Someone Else"}, headers=H)
        assert again.json() == {"saved": False}                     # a name is already there: kept
        c.post("/internal/sessions/n-1/end", headers=H)
        assert c.post("/internal/calls/admit", json={"call_uuid": "n-2", "from": "919876543210"}, headers=H).json()[
            "decision"] == "admit"
        s = c.post("/internal/sessions", json={"session_id": "n-2"}, headers=H).json()
        assert s["admission"]["name"] == "Saurabh"                     # the next call's welcome greets them by it
