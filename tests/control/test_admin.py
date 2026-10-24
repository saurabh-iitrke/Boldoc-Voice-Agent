"""The admin console's API: only on the admin host, password + TOTP or a code by SMS to the admin's own mobile (a code
works once), sign-in limits, a cookie for that host alone, every look at personal data in the audit log; the overview
pools every turn before percentiles."""
import asyncio
import json
import re
import time

import pytest

from tests.control.test_accounts import sql, with_redis

asyncpg = pytest.importorskip("asyncpg")
pytest.importorskip("argon2")
HOST = {"Host": "admin.boldoc.in"}
A = {**HOST, "X-BolDoc-Admin": "1"}
PASSWORD = "correct horse battery staple"


def make_admin(pg_url, capsys, username="ops"):
    from apps.control.admin.__main__ import run
    assert asyncio.run(run(["create-admin", username], pg_url, password=PASSWORD)) == 0
    out = capsys.readouterr().out
    assert "otpauth://totp/" in out and PASSWORD not in out
    return re.search(r"TOTP secret: ([A-Z2-7]+)", out).group(1)


def code(secret, offset=0):
    from apps.control.admin import auth
    return auth.totp_code(secret, int(time.time() // auth.STEP_S) + offset)


def sign_in(c, secret, username="ops", offset=0):
    return c.post("/admin/api/login", headers=A, json={"username": username, "password": PASSWORD,
                                                        "code": code(secret, offset)})


def audit(pg_url):
    return [(r["actor"], r["action"], r["target"], json.loads(r["detail"]))
            for r in sql(pg_url, "select actor, action, target, detail from audit_log order by id")]


def test_the_admin_api_answers_only_on_the_admin_host_and_needs_a_password_and_a_fresh_code(app_factory, pg_url,
                                                                                            capsys):
    with app_factory() as c:
        with_redis(c)
        secret = make_admin(pg_url, capsys)
        assert c.get("/admin/api/me").status_code == 404                     # boldoc.in and others: no such thing
        assert c.get("/admin/api/me", headers=HOST).status_code == 401
        assert c.post("/admin/api/login", headers=HOST, json={"username": "ops", "password": PASSWORD,
                                                              "code": code(secret)}).status_code == 403  # header
        wrong = c.post("/admin/api/login", headers=A, json={"username": "ops", "password": "nope nope nope",
                                                             "code": code(secret)})
        assert wrong.status_code == 401 and wrong.json()["code"] == "bad_login"
        assert c.post("/admin/api/login", headers=A, json={"username": "ops", "password": PASSWORD,
                                                            "code": "000000" if code(secret) != "000000" else "111111"}
                      ).status_code == 401
        used = code(secret)
        ok = c.post("/admin/api/login", headers=A, json={"username": "ops", "password": PASSWORD, "code": used})
        assert ok.status_code == 200 and ok.json() == {"username": "ops"}
        cookie = ok.headers["set-cookie"].lower()
        assert "boldoc_admin=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
        assert "domain=" not in cookie                                         # the admin host's cookie alone
        assert c.get("/admin/api/me", headers=HOST).json() == {"username": "ops"}
        c.post("/admin/api/logout", headers=A)
        assert c.get("/admin/api/me", headers=HOST).status_code == 401
        again = c.post("/admin/api/login", headers=A, json={"username": "ops", "password": PASSWORD, "code": used})
        assert again.status_code == 401                                        # the same code, again: refused
        assert [(a, x, d.get("why")) for a, x, _, d in audit(pg_url)] == [
            ("cli", "create_admin", None), ("ops", "login_failed", "password"), ("ops", "login_failed", "code"),
            ("ops", "login", None), ("ops", "logout", None), ("ops", "login_failed", "code")]


def test_the_username_and_password_alone_sign_in_while_the_code_is_optional(app_factory, pg_url, capsys):
    """A code given is still checked; ADMIN_SECOND_FACTOR=required asks for one every time."""
    from apps.control.admin import api as admin_api
    with app_factory() as c:
        with_redis(c)
        secret = make_admin(pg_url, capsys)
        assert admin_api.CTX.second_factor == "optional"                       # the default
        wrong = c.post("/admin/api/login", headers=A, json={"username": "ops", "password": "nope nope nope"})
        assert wrong.status_code == 401 and wrong.json()["code"] == "bad_login"
        ok = c.post("/admin/api/login", headers=A, json={"username": "ops", "password": PASSWORD})
        assert ok.status_code == 200 and c.get("/admin/api/me", headers=HOST).json() == {"username": "ops"}
        c.post("/admin/api/logout", headers=A)
        wrong_code = "000000" if code(secret) != "000000" else "111111"
        bad_code = c.post("/admin/api/login", headers=A, json={"username": "ops", "password": PASSWORD,
                                                                "code": wrong_code})
        assert bad_code.status_code == 401                                     # a code given must be right
        assert sign_in(c, secret).status_code == 200                           # the authenticator still works
        c.post("/admin/api/logout", headers=A)
        admin_api.CTX.second_factor = "required"
        try:
            needed = c.post("/admin/api/login", headers=A, json={"username": "ops", "password": PASSWORD})
            assert needed.status_code == 401 and needed.json()["code"] == "code_needed"
        finally:
            admin_api.CTX.second_factor = "optional"
        methods = [d.get("method") for a, x, _, d in audit(pg_url) if x == "login"]
        assert methods == ["password", "totp"]


def test_sign_in_attempts_are_limited(app_factory, pg_url, capsys):
    with app_factory() as c:
        with_redis(c)
        make_admin(pg_url, capsys)
        for _ in range(5):
            r = c.post("/admin/api/login", headers=A, json={"username": "ops", "password": "x" * 13, "code": "1"})
            assert r.status_code == 401
        r = c.post("/admin/api/login", headers=A, json={"username": "ops", "password": "x" * 13, "code": "1"})
        assert r.status_code == 429


def test_an_admin_can_sign_in_with_a_code_by_sms_to_their_own_mobile(app_factory, pg_url, capsys):
    from apps.control.admin.__main__ import run
    from tests.control.helpers import FakeSms
    with app_factory() as c:
        with_redis(c)
        make_admin(pg_url, capsys)
        assert asyncio.run(run(["set-phone", "ops", "99887 76655"], pg_url)) == 0
        assert asyncio.run(run(["set-phone", "ops", "12345"], pg_url)) == 1                # not an Indian mobile
        nope = c.post("/admin/api/login/sms", headers=A, json={"username": "ops", "password": "nope nope nope"})
        assert nope.status_code == 401 and FakeSms.sent == []                               # no password, no SMS
        sent = c.post("/admin/api/login/sms", headers=A, json={"username": "ops", "password": PASSWORD})
        assert sent.status_code == 200 and sent.json() == {"sent_to": "…6655", "resend_after_s": 30}
        dst, text = FakeSms.sent[-1]
        assert dst == "+919988776655" and "admin sign-in code" in text and "@admin.boldoc.in #" in text
        sms = text.split()[0]
        # an admin's code never signs a caller in on the website (codes for different purposes never mix)
        user = c.post("/api/auth/verify", headers={"X-BolDoc-Client": "web"}, json={"phone": "9988776655", "code": sms})
        assert user.status_code == 400 and user.json()["code"] == "code_expired"
        wrong = c.post("/admin/api/login", headers=A, json={"username": "ops", "password": PASSWORD, "method": "sms",
                                                             "code": "000000" if sms != "000000" else "111111"})
        assert wrong.status_code == 401 and wrong.json()["code"] == "wrong_code" and wrong.json()["attempts_left"] == 4
        ok = c.post("/admin/api/login", headers=A, json={"username": "ops", "password": PASSWORD, "method": "sms",
                                                          "code": sms})
        assert ok.status_code == 200 and ok.json() == {"username": "ops"}
        assert c.get("/admin/api/me", headers=HOST).json() == {"username": "ops"}
        c.post("/admin/api/logout", headers=A)
        again = c.post("/admin/api/login", headers=A, json={"username": "ops", "password": PASSWORD, "method": "sms",
                                                             "code": sms})
        assert again.status_code == 401 and again.json()["code"] == "code_expired"           # a code works once
        rows = [(a, x, d) for a, x, _, d in audit(pg_url)]
        assert ("ops", "login_code_sent", {"to": "…6655"}) in rows
        assert ("ops", "login", {"method": "sms"}) in rows
        assert ("ops", "login_failed", {"why": "password", "method": "sms"}) in rows


def test_an_admin_made_without_a_password_cant_sign_in_until_one_is_set(app_factory, pg_url, capsys):
    from apps.control.admin.__main__ import run
    from tests.control.helpers import FakeSms
    with app_factory() as c:
        with_redis(c)
        made = asyncio.run(run(["create-admin", "reviewer", "--no-password", "--phone", "+91 99887 76655"], pg_url))
        assert made == 0
        out = capsys.readouterr().out
        assert "TOTP secret" not in out and "otpauth" not in out and "…6655" in out  # nothing secret
        assert asyncio.run(run(["list"], pg_url)) == 0
        listed = capsys.readouterr().out
        assert "reviewer" in listed and "NO PASSWORD YET" in listed and "SMS to …6655" in listed
        for pw in ("", PASSWORD):
            r = c.post("/admin/api/login/sms", headers=A, json={"username": "reviewer", "password": pw})
            assert r.status_code == 401
        assert FakeSms.sent == []
        assert asyncio.run(run(["set-password", "reviewer"], pg_url, password=PASSWORD)) == 0
        sent = c.post("/admin/api/login/sms", headers=A, json={"username": "reviewer", "password": PASSWORD})
        assert sent.status_code == 200
        sms = FakeSms.sent[-1][1].split()[0]
        ok = c.post("/admin/api/login", headers=A, json={"username": "reviewer", "password": PASSWORD, "method": "sms",
                                                          "code": sms})
        assert ok.status_code == 200 and ok.json() == {"username": "reviewer"}
        # an admin with no mobile is told so, once the password is right (and nothing is sent)
        make_admin(pg_url, capsys, username="ops")
        no = c.post("/admin/api/login/sms", headers=A, json={"username": "ops", "password": PASSWORD})
        assert no.status_code == 409 and no.json()["code"] == "no_phone" and len(FakeSms.sent) == 1


def _turn(t0, v2v, tools=False):
    """One caller turn in a talker trace: speech ends at t0 + 1, BolDoc's voice starts v2v later."""
    ev = [{"e": "turn_start", "t": t0}, {"e": "vad_start", "t": t0}, {"e": "vad_stop", "t": t0 + 1.0},
          {"e": "turn_end", "t": t0 + 1.3, "words": 4, "speech_s": 1.0}, {"e": "llm_start", "t": t0 + 1.35},
          {"e": "llm_first_token", "t": t0 + 1.5}, {"e": "tts_first_audio", "t": t0 + 1.9}]
    if tools:
        ev.append({"e": "tool", "t": t0 + 1.6, "tool": "create_document", "status": "accepted", "ms": 30})
    return [*ev, {"e": "bot_start", "t": t0 + 1.0 + v2v}]


def _store_call(pg_url, call_id, v2vs, caller="+919988776655", channel="pstn", content=False, extra=(), ago_s=0):
    events = [e for i, v in enumerate(v2vs) for e in _turn(10.0 * i, v, tools=(i == 0))] + list(extra)
    trace = {"call_id": call_id, "started_wall": time.time() - 120, "content": content, "events": events}
    sql(pg_url, "insert into call_records (call_uuid, channel, caller, caller_kind, decision, max_call_s, duration_s, "
                "meta, admitted_at) values ($1, $2, $3, 'mobile', 'admit', 300, 60, '{\"plan\": \"trial\"}', "
                "now() - make_interval(secs => $4))", call_id, channel, caller, float(ago_s))
    sql(pg_url, "insert into call_traces (session_id, call_id, started_at, trace) values ($1, $1, now(), $2::jsonb)",
        call_id, json.dumps(trace))


def test_the_overview_pools_every_turn_before_taking_percentiles(app_factory, pg_url, capsys):
    from apps.control.admin import metrics
    metrics._CACHE.clear()
    with app_factory() as c:
        with_redis(c)
        secret = make_admin(pg_url, capsys)
        _store_call(pg_url, "call-a", [3.0, 3.0])                     # a slow two-turn call
        _store_call(pg_url, "call-b", [0.8] * 8, channel="sip")       # a quick eight-turn browser call
        sql(pg_url, "insert into call_records (call_uuid, channel, caller_kind, decision, reason) values "
                    "('call-c', 'pstn', 'withheld', 'refuse', 'withheld')")
        assert sign_in(c, secret).status_code == 200
        o = c.get("/admin/api/overview?range=7d", headers=HOST).json()
        assert o["calls"]["admitted"] == 2 and o["calls"]["refused"] == {"withheld": 1}
        assert o["calls"]["by_channel"] == {"phone": 1, "browser": 1} and o["calls"]["minutes"] == 2.0
        assert o["latency"]["v2v"]["n"] == 10
        assert o["latency"]["v2v"]["p50"] == 0.8      # 8 of 10 turns are quick; per-call medians would say 1.9
        assert o["latency"]["v2v"]["p95"] == 3.0
        assert o["latency"]["v2v_tool"]["n"] == 2 and o["latency"]["ttft"]["p50"] == 0.15
        assert sum(o["cost"]["by_component"].values()) > 0
        assert len(o["trend"]) == 7 and [b["calls"] for b in o["trend"]] == [0] * 6 + [2]   # every day, today last
        listed = c.get("/admin/api/calls?kind=all&min_v2v=2", headers=HOST).json()["calls"]
        assert [x["id"] for x in listed] == ["call-a"] and listed[0]["caller"] == "+91 99••• ••655"


def test_every_look_at_personal_data_is_audited(app_factory, pg_url, capsys):
    from apps.control.admin import metrics
    metrics._CACHE.clear()
    with app_factory() as c:
        with_redis(c)
        secret = make_admin(pg_url, capsys)
        sql(pg_url, "insert into users (phone_e164, source, status) values ('+919876500022', 'web', 'active')")
        uid = sql(pg_url, "select id from users where phone_e164 = '+919876500022'")[0]["id"]
        sql(pg_url, "insert into user_phones (phone_e164, user_id) values ('+919876500033', $1)", uid)
        _store_call(pg_url, "call-t", [1.0], caller="+919876500022", content=True)
        assert sign_in(c, secret).status_code == 200
        found = c.get("/admin/api/users?q=500033", headers=HOST).json()["users"]   # found by a linked number
        assert [(u["id"], u["phone"], u["linked"]) for u in found] == [(uid, "+91 98••• ••022", 1)]
        assert c.post(f"/admin/api/users/{uid}/reveal", headers=HOST).status_code == 403  # header
        numbers = c.post(f"/admin/api/users/{uid}/reveal", headers=A).json()["numbers"]
        assert numbers == ["+919876500022", "+919876500033"]
        detail = c.get("/admin/api/calls/call-t", headers=HOST).json()
        assert detail["report"]["content"] is True and detail["call"]["caller"] == "+91 98••• ••022"
        assert [(x, t) for _, x, t, _ in audit(pg_url) if x in ("reveal_number", "view_transcript")] == [
            ("reveal_number", f"user:{uid}"), ("view_transcript", "call-t")]


def test_the_cli_makes_admins_and_disabling_one_signs_it_out(app_factory, pg_url, capsys):
    from apps.control.admin.__main__ import run
    with app_factory() as c:
        with_redis(c)
        secret = make_admin(pg_url, capsys)
        assert asyncio.run(run(["create-admin", "ops"], pg_url, password=PASSWORD)) == 1   # already there
        assert asyncio.run(run(["create-admin", "x"], pg_url, password=PASSWORD)) == 1     # name too short
        assert asyncio.run(run(["create-admin", "two"], pg_url, password="short")) == 1    # password too short
        capsys.readouterr()
        assert sign_in(c, secret).status_code == 200
        assert asyncio.run(run(["disable", "ops"], pg_url)) == 0
        assert c.get("/admin/api/me", headers=HOST).status_code == 401                    # its session ended
        assert sign_in(c, secret, offset=1).status_code == 401
        assert ("cli", "disable", "ops") in [(a, x, t) for a, x, t, _ in audit(pg_url)]


def test_the_last_call_opens_in_one_click_with_what_it_takes_to_analyse_it(app_factory, pg_url, capsys):
    from apps.control.admin import metrics
    metrics._CACHE.clear()
    with app_factory() as c:
        with_redis(c)
        secret = make_admin(pg_url, capsys)
        assert c.get("/admin/api/calls/latest", headers=HOST).status_code == 401          # signed in only
        assert sign_in(c, secret).status_code == 200
        assert c.get("/admin/api/calls/latest", headers=HOST).json()["code"] == "no_calls"
        _store_call(pg_url, "call-old", [1.0], ago_s=600)
        noise_and_news = [
            {"e": "vad_start", "t": 41.0}, {"e": "usage", "t": 41.1, "kind": "llm"},       # per-frame noise: left out
            {"e": "lang_switch", "t": 2.5, "family": "hinglish"},
            {"e": "claim_guard", "t": 12.0, "why": "unacted", "text": "kar di hai"},       # words: this call kept none
            {"e": "tool", "t": 30.0, "tool": "end_call", "status": "ending", "ms": 0}]
        _store_call(pg_url, "call-new", [0.9, 1.1], extra=noise_and_news, ago_s=60)
        sql(pg_url, "insert into documents (id, session_id, title, head_version, formats) values "
                    "('doc-1', 'call-new', 'Leave Application', 2, '[\"pdf\", \"docx\"]')")
        d = c.get("/admin/api/calls/latest", headers=HOST).json()
        call, timeline = d["call"], d["timeline"]
        assert call["id"] == "call-new" and call["caller"] == "+91 99••• ••655" and d["report"]["content"] is False
        assert call["ended"]["how"] == "end_call" and call["language"]["switches"] == [{"t": 2.5, "family": "hinglish"}]
        kinds = [e["e"] for e in timeline["events"]]
        assert "vad_start" not in kinds and "usage" not in kinds
        assert {"lang_switch", "claim_guard", "tool"} <= set(kinds)
        assert all("text" not in e for e in timeline["events"]) and timeline["truncated"] is False
        assert [(x["title"], x["versions"], x["formats"], x["this_call"]) for x in d["documents"]] == [
            ("Leave Application", 2, ["pdf", "docx"], True)]
        o = c.get("/admin/api/overview?range=7d", headers=HOST).json()
        assert o["last_call"]["id"] == "call-new" and o["last_call"]["ended"]["how"] == "end_call"
        assert c.get("/admin/api/calls/latest?kind=test", headers=HOST).status_code == 404
        # the full number, once asked for and logged; a call that kept its words logs who opened them
        assert c.post("/admin/api/calls/call-new/reveal", headers=HOST).status_code == 403      # header
        assert c.post("/admin/api/calls/call-new/reveal", headers=A).json() == {"number": "+919988776655"}
        _store_call(pg_url, "call-words", [1.0], content=True)
        assert c.get("/admin/api/calls/latest", headers=HOST).json()["call"]["id"] == "call-words"
        assert [(x, t) for _, x, t, _ in audit(pg_url) if x in ("reveal_number", "view_transcript")] == [
            ("reveal_number", "call:call-new"), ("view_transcript", "call-words")]


def test_how_a_call_ended_is_told_from_the_trace_and_the_hangup_record():
    from apps.control.admin.metrics import ending
    assert ending({"decision": "refuse"}, [])["how"] == "refused"
    assert ending({"decision": "admit"}, [{"e": "time_limit", "stage": "call.time_up"}])["how"] == "time_limit"
    assert ending({"decision": "admit"}, [{"e": "announce", "type": "call.idle_goodbye"}])["how"] == "idle_goodbye"
    bye = ending({"decision": "admit", "hangup_source": "Caller"}, [{"e": "tool", "tool": "end_call",
                                                                      "status": "ending_again"}])
    assert (bye["how"], bye["hangup_source"]) == ("end_call", "Caller")
    hung = ending({"decision": "admit", "meta": {"cdr": {"HangupSource": "Caller", "HangupCause": "NORMAL_CLEARING"}}},
                  [])
    assert (hung["label"], hung["hangup_cause"]) == ("The caller hung up", "NORMAL_CLEARING")
    assert ending(None, [])["how"] == "stream_closed"


def test_agent_settings_are_published_checked_versioned_audited_and_restorable(app_factory, pg_url, capsys):
    """A publish needs the console's header and a note, values outside the whitelist or their ranges are
    refused, a stale version is refused, and a publish applies at once (the website's prices, the talker's next
    call)."""
    import dataclasses

    from apps.control.accounts.pricing import PRICING
    from tests.control.helpers import H
    before = dataclasses.replace(PRICING)
    try:
        with app_factory() as c:
            with_redis(c)
            secret = make_admin(pg_url, capsys)
            assert sign_in(c, secret).status_code == 200
            v = c.get("/admin/api/agent", headers=HOST).json()
            assert v["live"]["version"] == 0 and [g["id"] for g in v["groups"]] == ["call", "timing", "prices"]
            assert v["live"]["settings"]["price_minute"] == PRICING.minute and len(v["schema"]) >= 30
            change = {"base_version": 0, "changes": {"price_minute": 5, "wait_tone": False}, "note": "try it"}
            assert c.post("/admin/api/agent/publish", headers=HOST, json=change).status_code == 403  # no header
            bad = c.post("/admin/api/agent/publish", headers=A, json={**change, "changes": {"unfinished_hold_s": 9}})
            assert bad.status_code == 400 and "between 2 and 5" in bad.json()["errors"]["unfinished_hold_s"]
            no_note = c.post("/admin/api/agent/publish", headers=A, json={**change, "note": "  "})
            assert no_note.status_code == 400 and "note" in no_note.json()["errors"]
            one = c.post("/admin/api/agent/publish", headers=A, json=change).json()
            assert one["live"]["version"] == 1 and one["published"]["changes"]["price_minute"] == [4, 5]
            assert c.get("/api/prices").json()["minute"] == 5                                    # the website
            talker = c.get("/internal/agent-config", headers=H).json()
            assert talker["version"] == 1 and talker["settings"]["wait_tone"] is False           # the next call
            stale = c.post("/admin/api/agent/publish", headers=A, json={**change, "changes": {"price_minute": 6}})
            assert stale.status_code == 409 and stale.json()["live_version"] == 1
            two = c.post("/admin/api/agent/publish", headers=A,
                         json={"base_version": 1, "changes": {"price_minute": 6}, "note": "dearer"}).json()
            assert two["live"]["settings"]["price_minute"] == 6
            back = c.post("/admin/api/agent/restore", headers=A, json={"version": 1, "note": "as before"}).json()
            assert back["live"]["version"] == 3 and back["live"]["settings"]["price_minute"] == 5
            assert back["history"][0]["restored_from"] == 1 and len(back["history"]) == 3
        assert [a[1] for a in audit(pg_url) if a[1].startswith("agent_")] == ["agent_publish", "agent_publish",
                                                                               "agent_restore"]
    finally:
        for f in dataclasses.fields(PRICING):
            setattr(PRICING, f.name, getattr(before, f.name))
