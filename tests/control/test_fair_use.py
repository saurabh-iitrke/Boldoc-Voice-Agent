"""Fair use of the published number: calls and talk time a day per number, bursts, withheld numbers, and a day's
budget for all free-trial calls together. Short dial-and-drop calls count too."""
import dataclasses

from apps.control import calls
from apps.control.calls import classify_from, decide
from tests.control.helpers import CALLER, FakeSms, H
from tests.control.test_accounts import hangup, sql

BASE = dict(prior=None, resumed=False, active=0, cap=3, caller_busy=False, max_call_s=1800, first_call=False)
TRIAL = {"plan": "trial", "max_call_s": 300, "refuse": None}


def fair(**kw):
    return {"calls_day": 0, "calls_burst": 0, "calls_day_max": 3, "burst_max": 3, "secs_left": 600, **kw}


def admit(c, uuid, frm=CALLER):
    return c.post("/internal/calls/admit", json={"call_uuid": uuid, "from": frm}, headers=H).json()


def test_fair_use_rules():
    phone = classify_from("919988776655")
    ok = decide(phone, **BASE, allowance=TRIAL, fair=fair())
    assert (ok.decision, ok.max_call_s) == ("admit", 300)
    assert decide(phone, **BASE, allowance=TRIAL, fair=fair(secs_left=200)).max_call_s == 200  # never past the day
    for f, reason in [({"withheld": True}, "withheld"), (fair(calls_burst=3), "too_frequent"),
                      (fair(calls_day=3), "daily_limit"), (fair(secs_left=59), "daily_limit"),
                      (fair(trial_budget_left=30), "trial_budget")]:
        adm = decide(phone, **BASE, allowance=TRIAL, fair=f)
        assert (adm.decision, adm.reason, adm.clip) == ("refuse", reason, calls.CLIPS[reason])
    # a used-up trial is still what the caller hears (it comes with the sign-up link)
    assert decide(phone, **BASE, allowance={**TRIAL, "refuse": "trial_over"}, fair=fair(calls_burst=9)).reason \
        == "trial_over"
    assert decide(phone, **BASE, allowance=TRIAL, fair=None).decision == "admit"  # test calls, the owner's phones


def test_a_number_gets_three_calls_in_half_an_hour_and_its_talk_time_a_day(app_factory, pg_url):
    with app_factory() as c:
        for n in range(3):
            assert admit(c, f"b{n}")["decision"] == "admit"
            hangup(c, f"b{n}", 5)  # dial-and-drop calls count
        burst = admit(c, "b3")
        assert (burst["decision"], burst["reason"], burst["clip"]) == ("refuse", "too_frequent", "too-frequent")
        sql(pg_url, "update call_records set admitted_at = admitted_at - interval '1 hour'")
        assert admit(c, "b4")["reason"] == "daily_limit"  # the burst is over; today's 3 calls are not
        sql(pg_url, "update call_records set admitted_at = admitted_at - interval '1 day'")
        assert admit(c, "b5")["max_call_s"] == 300  # a new day
        hangup(c, "b5", 290)
        sql(pg_url, "update call_records set admitted_at = admitted_at - interval '1 hour'")
        assert admit(c, "b6")["max_call_s"] == 300
        hangup(c, "b6", 250)
        sql(pg_url, "update call_records set admitted_at = admitted_at - interval '1 hour'")
        last = admit(c, "b7")  # 540 s of today's 600 s used: the call is cut to what is left
        assert (last["decision"], last["max_call_s"]) == ("admit", 60)
    assert not FakeSms.sent  # only a used-up trial sends the sign-up link


def test_withheld_numbers_are_refused_and_the_owners_phones_are_never_limited(app_factory, monkeypatch):
    monkeypatch.setattr(calls, "PRICING", dataclasses.replace(calls.PRICING, unlimited_numbers=("+919988776655",)))
    with app_factory() as c:
        withheld = admit(c, "w1", "")
        assert (withheld["decision"], withheld["reason"], withheld["clip"]) == ("refuse", "withheld", "withheld")
        for n in range(5):
            assert admit(c, f"u{n}")["decision"] == "admit"
            hangup(c, f"u{n}", 5)


def test_free_trials_share_a_daily_budget(app_factory, monkeypatch):
    monkeypatch.setattr(calls, "PRICING", dataclasses.replace(calls.PRICING, trial_minutes_budget=10))
    with app_factory() as c:
        for n in (1, 2):
            assert admit(c, f"g{n}", f"91987650000{n}")["decision"] == "admit"
            hangup(c, f"g{n}", 290)
        third = admit(c, "g3", "919876500003")  # 580 s of the 600 s budget gone
        assert (third["decision"], third["reason"], third["clip"]) == ("refuse", "trial_budget", "trial-budget")
