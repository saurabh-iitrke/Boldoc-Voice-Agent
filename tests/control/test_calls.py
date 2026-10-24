"""Admission and call records: who may call, identity only from the record, CDRs, limits, funnel counters."""
import asyncio
import time

import pytest

from apps.control.calls import Caller, classify_from, decide
from tests.control.helpers import CALLER, H, create_cmd, open_call, wait_for

asyncpg = pytest.importorskip("asyncpg")


def test_from_is_classified_before_any_digit_stripping():
    assert classify_from("919988776655") == Caller("pstn", "+919988776655", "mobile")
    assert classify_from("+91 99887 76655") == Caller("pstn", "+919988776655", "mobile")
    assert classify_from("918035449833") == Caller("pstn", "+918035449833", "other")    # landline: no SMS
    assert classify_from("sip:web0042@phone.plivo.com") == Caller("sip", None, "browser")  # not "+0042"
    assert classify_from("") == Caller("pstn", None, "withheld")
    assert classify_from("anonymous") == Caller("pstn", None, "withheld")


def test_admission_rules():
    phone = classify_from("919988776655")
    base = dict(prior=None, resumed=False, active=0, cap=3, caller_busy=False, max_call_s=1800, first_call=True)
    adm = decide(phone, **base)
    assert (adm.decision, adm.max_call_s, adm.first_call, adm.clip) == ("admit", 1800, True, None)
    assert decide(phone, **{**base, "active": 3}).reason == "busy"
    assert decide(phone, **{**base, "active": 3}).clip == "busy"
    assert decide(phone, **{**base, "caller_busy": True}).reason == "already_on_call"
    assert decide(classify_from("sip:x@phone.plivo.com"), **base).reason == "browser_unauthenticated"
    # a resumed stream continues its call even when the line is full; past the limit it hears "time is up"
    prior = {"decision": "admit", "ended_at": None, "channel": "pstn", "caller": "+919988776655",
             "caller_kind": "mobile", "max_call_s": 300, "test": False}
    resumed = decide(phone, **{**base, "prior": prior, "resumed": True, "active": 9})
    assert resumed.decision == "admit" and resumed.max_call_s == 300 and not resumed.first_call
    assert decide(phone, **{**base, "prior": prior, "resumed": True, "elapsed_s": 301}).reason == "time_up"
    assert decide(phone, **{**base, "prior": {**prior, "ended_at": 1}, "resumed": True}).reason == "not_admitted"
    # admitted by the talker during a control outage: recorded, never refused mid-call
    late = decide(phone, **{**base, "active": 9, "late": True})
    assert late.decision == "admit" and late.degraded


def test_stream_identity_comes_only_from_the_call_record(app_factory):
    with app_factory() as c:
        # no admission: a forged stream (even one claiming a caller) gets nothing
        r = c.post("/internal/sessions", json={"session_id": "forged", "caller": CALLER}, headers=H)
        assert r.status_code == 403
        # a scripted test stream with no record is anonymous: no number, so no SMS to anyone
        r = c.post("/internal/sessions", json={"session_id": "test-1", "test": True}, headers=H).json()
        assert r["snapshot"]["verified_recipients"] == [] and r["admission"]["caller"] is None
        # admitted on the answer webhook: the caller's number comes from the record
        first = open_call(c, "call-a").json()
        assert first["snapshot"]["verified_recipients"] == ["+919988776655"] and first["admission"]["first_call"]
        c.post("/internal/calls/call-a/hangup", json={"Duration": "10"}, headers=H)
        again = open_call(c, "call-b").json()
        assert again["admission"]["first_call"] is False  # the notice is for a number's first call only
        # a refused call can't open a session either
        refused = c.post("/internal/calls/admit", json={"call_uuid": "web", "from": "sip:w@phone.plivo.com"},
                         headers=H).json()
        assert refused["decision"] == "refuse" and refused["clip"] == "refused"
        assert c.post("/internal/sessions", json={"session_id": "web"}, headers=H).status_code == 403
        # the talker admitted during an outage: control records it late, with the signed answer parameters
        late = c.post("/internal/sessions", headers=H, json={"session_id": "late-1", "late_admit": {
            "call_uuid": "late-1", "from": "919988776655"}}).json()
        assert late["snapshot"]["verified_recipients"] == ["+919988776655"]


def test_webhook_retries_get_the_same_answer_and_capacity_is_enforced(app_factory):
    from apps.control import service
    with app_factory() as c:
        a1 = c.post("/internal/calls/admit", json={"call_uuid": "u1", "from": CALLER}, headers=H).json()
        service.S.calls.cap = 0  # the line is now full...
        a2 = c.post("/internal/calls/admit", json={"call_uuid": "u1", "from": CALLER}, headers=H).json()
        assert a1["decision"] == a2["decision"] == "admit"  # ...but a retried webhook is the same call
        busy = c.post("/internal/calls/admit", json={"call_uuid": "u2", "from": "919876543210"}, headers=H).json()
        assert busy["decision"] == "refuse" and busy["clip"] == "busy"
        resumed = c.post("/internal/calls/admit", json={"call_uuid": "u1", "from": CALLER, "resumed": True},
                         headers=H).json()
        assert resumed["decision"] == "admit"  # a resumed stream takes no new capacity


def test_hangup_cdr_closes_the_record_and_ends_a_session_the_talker_never_ended(app_factory, pg_url):
    with app_factory() as c:
        r = open_call(c, "call-h").json()
        assert c.post("/internal/commands", json=create_cmd("call-h", r["epoch"]), headers=H).json()["status"] \
            == "accepted"
        out = c.post("/internal/calls/call-h/hangup", headers=H, json={
            "From": CALLER, "Duration": "95", "BillDuration": "120", "TotalCost": "0.00900", "BillRate": "0.0045",
            "HangupCauseName": "Normal Hangup", "HangupSource": "Caller"}).json()
        assert out == {"ok": True, "session_ended": True}
        assert c.post("/internal/calls/call-h/hangup", headers=H, json={"Duration": "95"}).json()[
            "session_ended"] is False  # Plivo retried: nothing ends twice
        # a call that ended can't be resumed into a new session
        assert c.post("/internal/sessions", json={"session_id": "call-h"}, headers=H).status_code == 403
        # hangup for a call we never admitted (e.g. answered before a deploy): still recorded, cost not lost
        c.post("/internal/calls/ghost/hangup", headers=H, json={"From": CALLER, "Duration": "5",
                                                                "TotalCost": "0.0045"})

    async def rows():
        con = await asyncpg.connect(pg_url)
        try:
            return {r["call_uuid"]: dict(r) for r in await con.fetch("select * from call_records")}
        finally:
            await con.close()
    recs = asyncio.run(rows())
    h = recs["call-h"]
    assert (h["decision"], h["duration_s"], h["bill_s"], float(h["cost_usd"]), h["hangup_cause"]) == \
        ("admit", 95, 120, 0.009, "Normal Hangup")
    assert h["ended_at"] is not None and h["streams"] == 1 and h["first_call"] is True
    assert recs["ghost"]["decision"] == "unrecorded" and float(recs["ghost"]["cost_usd"]) == 0.0045


def test_limit_rejections_are_events_and_retries_never_count_twice(app_factory):
    fakeredis = pytest.importorskip("fakeredis")
    import dataclasses

    from apps.control import service
    with app_factory() as c:
        async def fake_redis():  # created on the app's own event loop (Python 3.9 needs one at construction)
            return fakeredis.FakeAsyncRedis(decode_responses=True)
        service.S.bus.r = c.portal.call(fake_redis)
        service.SETTINGS = dataclasses.replace(service.SETTINGS, max_docs_per_call=1)
        r = open_call(c, "call-l").json()
        first = c.post("/internal/commands", json=create_cmd("call-l", r["epoch"], "t1"), headers=H).json()
        retry = c.post("/internal/commands", json=create_cmd("call-l", r["epoch"], "t1"), headers=H).json()
        second = c.post("/internal/commands", json={**create_cmd("call-l", r["epoch"], "t2"),
                                                    "intent": "another one"}, headers=H).json()
        assert (first["status"], retry["status"]) == ("accepted", "duplicate")
        assert second["status"] == "rejected" and second["reason"] == "limit_reached"
        ev, _, _ = wait_for(c, "call-l", 0, "command.rejected")
        assert ev["payload"] == {"cmd": "create_doc", "reason": "limit_reached"}


def test_funnel_beacon_counts_known_kinds_only(app_factory, pg_url):
    with app_factory() as c:
        for k in ("visit", "visit", "cta_tel", "nonsense"):
            assert c.post("/api/t", json={"k": k}).status_code == 204
        assert c.post("/api/t", content=b"not json").status_code == 204

    async def counts():
        con = await asyncpg.connect(pg_url)
        try:
            return {r["kind"]: r["n"] for r in await con.fetch("select kind, n from web_events")}
        finally:
            await con.close()
    assert asyncio.run(counts()) == {"visit": 2, "cta_tel": 1}


def test_schema_migrations_apply_once(pg_url):
    from apps.control.migrations import MIGRATIONS, migrate
    from apps.control.store_pg import SCHEMA

    async def go():
        con = await asyncpg.connect(pg_url)
        try:
            await con.execute("drop schema public cascade; create schema public;")
            await con.execute(SCHEMA)  # the baseline, as startup creates it first
            first = await migrate(con)
            again = await migrate(con)
            return first, again
        finally:
            await con.close()
    first, again = asyncio.run(go())
    assert first == [v for v, _, _ in MIGRATIONS] and again == []


def test_call_cost_uses_the_cdr_when_present():
    from apps.control.rates import DEFAULT_CARD, call_cost
    summary = {"duration_s": 120, "tts_chars": 1000, "llm_prompt_tokens": 100_000, "llm_completion_tokens": 2_000}
    est = call_cost(summary)
    assert est["telephony_source"] == "estimate"
    assert est["inr"]["stt"] == round(2 * DEFAULT_CARD["stt_inr_per_min"], 3)
    assert est["inr"]["tts"] == 2.0 and est["inr"]["llm"] == round(1.5 + 0.12, 3)
    real = call_cost(summary, {"duration_s": 120, "bill_s": 120, "cost_usd": 0.009}, card=DEFAULT_CARD)
    assert real["telephony_source"] == "cdr" and real["inr"]["telephony"] == round(0.009 * 88, 3)
    assert real["per_min_inr"] == round(real["total_inr"] / 2, 2)
    _ = time  # (module imported for symmetry with the service tests)


def test_sms_receipts_are_v2_signed_on_any_of_our_hosts_and_kept(app_factory, pg_url):
    import base64
    import dataclasses
    import hashlib
    import hmac

    from apps.control import service
    with app_factory() as c:
        service.SETTINGS = dataclasses.replace(service.SETTINGS, signature_mode="enforce", plivo_auth_token="tok",
                                               public_host="boldoc.test", public_hosts=("boldoc.test", "alias.test"))
        receipt = {"MessageUUID": "m1", "Status": "delivered", "Units": "1", "TotalAmount": "0.08000",
                   "TotalRate": "0.08", "MCC": "405", "MNC": "857"}
        for host in ("boldoc.test", "alias.test"):  # links sent before a domain switch report on the old host
            url = f"https://{host}/plivo/sms-status"
            sig = base64.b64encode(hmac.new(b"tok", (url + "n1").encode(), hashlib.sha256).digest()).decode()
            r = c.post("/plivo/sms-status", data=receipt, headers={
                "host": host, "X-Plivo-Signature-V2": sig, "X-Plivo-Signature-V2-Nonce": "n1"})
            assert r.status_code == 200, host
        forged = c.post("/plivo/sms-status", data=receipt, headers={
            "host": "boldoc.test", "X-Plivo-Signature-V2": "AAAA", "X-Plivo-Signature-V2-Nonce": "n1"})
        assert forged.status_code == 403

    async def rows():
        con = await asyncpg.connect(pg_url)
        try:
            return [dict(r) for r in await con.fetch("select status, units, amount_usd, mcc, mnc from sms_receipts")]
        finally:
            await con.close()
    got = asyncio.run(rows())
    assert len(got) == 2 and got[0]["units"] == 1 and float(got[0]["amount_usd"]) == 0.08 and got[0]["mnc"] == "857"


def test_busy_times_overflow_to_the_second_llm_instead_of_refusing():
    phone = classify_from("919988776655")
    base = dict(prior=None, resumed=False, active=4, cap=8, caller_busy=False, max_call_s=1800, first_call=False)
    assert decide(phone, **base, primary_active=2, primary_cap=3, overflow=True).llm == "primary"
    over = decide(phone, **base, primary_active=3, primary_cap=3, overflow=True)
    assert over.decision == "admit" and over.llm == "overflow"
    assert decide(phone, **base, primary_active=3, primary_cap=3, overflow=False).reason == "busy"
    assert decide(phone, **{**base, "active": 8}, primary_active=3, primary_cap=3, overflow=True).reason == "busy"


def test_overflow_assignment_is_recorded_and_reaches_the_talker(app_factory):
    fakeredis = pytest.importorskip("fakeredis")
    from apps.control import service
    with app_factory() as c:
        async def fake_redis():
            return fakeredis.FakeAsyncRedis(decode_responses=True)
        service.S.bus.r = c.portal.call(fake_redis)
        service.S.calls.primary_cap, service.S.calls.overflow, service.S.calls.cap = 1, True, 5
        a = c.post("/internal/calls/admit", json={"call_uuid": "o1", "from": CALLER}, headers=H).json()
        b = c.post("/internal/calls/admit", json={"call_uuid": "o2", "from": "919876543210"}, headers=H).json()
        assert (a["llm"], b["llm"]) == ("primary", "overflow")
        r = c.post("/internal/sessions", json={"session_id": "o2"}, headers=H).json()
        assert r["admission"]["llm"] == "overflow"
        c.post("/internal/calls/o1/hangup", json={"Duration": "30"}, headers=H)  # the primary slot frees up
        third = c.post("/internal/calls/admit", json={"call_uuid": "o3", "from": "919812345678"}, headers=H).json()
        assert third["llm"] == "primary"


def test_llm_cost_is_priced_per_model():
    from apps.control.rates import DEFAULT_CARD, call_cost
    summary = {"duration_s": 60, "llm_by_model": {"sarvam-105b": [100_000, 1_000],
                                                  "qwen.qwen3-235b-a22b-2507": [100_000, 1_000]}}
    cost = call_cost(summary, card=DEFAULT_CARD)
    sarvam = 100_000 * 15 / 1e6 + 1_000 * 60 / 1e6
    qwen = (100_000 * 0.26 + 1_000 * 1.04) / 1e6 * 88
    assert cost["inr"]["llm"] == round(sarvam + qwen, 3)


def test_sms_is_counted_at_indias_transactional_rate():
    from apps.control.rates import DEFAULT_CARD, call_cost
    cost = call_cost({"duration_s": 60, "sms_sent": 2}, card=DEFAULT_CARD)
    assert cost["inr"]["sms"] == round(2 * 0.18, 3) and "sms_usd_fallback" not in DEFAULT_CARD
    assert call_cost({"duration_s": 60}, card=DEFAULT_CARD)["inr"]["sms"] == 0
