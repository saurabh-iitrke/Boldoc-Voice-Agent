"""Calling the caller back about a long document's questions: only between 09:00 and 21:00 IST, never
after "don't call me", at most three a day to a number; the call reuses Call me's one-time token and is free."""
import asyncio
import json
from datetime import datetime

import pytest

from apps.control.callback import IST, Callbacks, in_hours
from tests.control.helpers import H
from tests.control.test_accounts import sign_in, with_redis

fakeredis = pytest.importorskip("fakeredis")
PHONE = "+919876500033"


def at(hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, 23, hour, minute, tzinfo=IST).timestamp()


class Bus:
    def __init__(self):
        self.r = fakeredis.FakeAsyncRedis(decode_responses=True)


class Dialer:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def place(self, number, token):
        if self.fail:
            raise RuntimeError("plivo down")
        self.calls.append((number, token))
        return "req-1"


def place(cb, **kw):
    args = dict(user_id=7, number=PHONE, task_id="task_1", doc_id="doc_1", title="Project report", n=2,
                lang="hi-IN")
    return cb.place(**{**args, **kw})


def test_callbacks_ring_only_in_the_day():
    assert not in_hours(at(8, 59)) and in_hours(at(9, 0)) and in_hours(at(20, 59)) and not in_hours(at(21, 0))


def test_a_callback_is_placed_with_a_one_time_token_and_refused_when_it_shouldnt_ring():
    async def go():
        now = [at(10)]
        dialer = Dialer()
        cb = Callbacks(Bus(), dialer, clock=lambda: now[0])
        first = await place(cb)
        assert first["id"] and dialer.calls[0][0] == PHONE
        token = dialer.calls[0][1]
        from apps.control.accounts import callme
        held = json.loads(await cb.bus.r.get(callme._token_key(token)))
        assert (held["kind"], held["task_id"], held["n"], held["number"]) == ("callback", "task_1", 2, PHONE)
        assert await cb.status(first) == "calling"
        now[0] += 70                                   # nobody picked up in the ring time
        assert await cb.status(first) == "no_answer"
        assert (await place(cb))["id"] and (await place(cb))["id"]
        assert await place(cb) == {"refused": "daily_cap"}            # three a day to a number
        late = Callbacks(Bus(), Dialer(), clock=lambda: at(21, 30))
        assert await place(late) == {"refused": "quiet_hours"}
        off = Callbacks(Bus(), Dialer(), clock=lambda: at(11))
        await off.opt_out(PHONE)
        assert await place(off) == {"refused": "opted_out"}
        broken = Callbacks(Bus(), Dialer(fail=True), clock=lambda: at(11))
        assert await place(broken) == {"refused": "dial_failed"}
    asyncio.run(go())


def test_a_callback_is_admitted_free_short_and_only_a_suspension_refuses_it(app_factory, monkeypatch):
    from apps.control import callback as callback_mod
    monkeypatch.setattr(callback_mod, "in_hours", lambda now=None: True)
    with app_factory() as c:
        with_redis(c)
        me = sign_in(c, PHONE)
        from apps.control import service
        dialer = Dialer()
        cb = Callbacks(service.S.bus, dialer)
        c.portal.call(lambda: place(cb, user_id=me["id"]))
        token = dialer.calls[0][1]

        from apps.control.accounts import users

        async def no_credits(con, user):  # out of credits: an ordinary call would be refused
            return {"plan": "credits", "balance": 0, "max_call_s": 0, "refuse": "no_credits"}
        monkeypatch.setattr(users, "allowance", no_credits)
        adm = c.post("/internal/calls/admit", headers=H, json={"call_uuid": "cb-1", "from": "918035449833",
                                                                "to": PHONE[1:], "call_me": token}).json()
        assert (adm["decision"], adm["caller"], adm["max_call_s"]) == ("admit", PHONE, 300)
        rec = c.portal.call(service.S.calls.get, "cb-1")
        meta = rec["meta"] if isinstance(rec["meta"], dict) else json.loads(rec["meta"])
        assert meta["via"] == "callback" and meta["callback"]["task_id"] == "task_1"
        s = c.post("/internal/sessions", json={"session_id": "cb-1"}, headers=H).json()
        assert s["admission"]["via"] == "callback" and s["admission"]["callback"] == {"title": "Project report",
                                                                                       "n": 2}
        c.portal.call(service.S.calls.hangup, "cb-1", {"Duration": "95", "BillDuration": "120"})
        rec = c.portal.call(service.S.calls.get, "cb-1")

        async def minutes_charged():
            async with service.S.store.pool.acquire() as con:
                return await con.fetchval("select count(*) from credit_ledger where user_id=$1 and kind="
                                          "'call_minutes'", me["id"])
        assert rec["counted"] is not True and c.portal.call(minutes_charged) == 0  # free, never counted

        async def suspended(con, user):
            return {"plan": "suspended", "max_call_s": 0, "refuse": "suspended"}
        monkeypatch.setattr(users, "allowance", suspended)
        c.portal.call(lambda: place(cb, user_id=me["id"]))
        refused = c.post("/internal/calls/admit", headers=H, json={"call_uuid": "cb-2", "from": "918035449833",
                                                                    "to": PHONE[1:], "call_me": dialer.calls[1][1]})
        assert refused.json()["decision"] == "refuse" and refused.json()["reason"] == "suspended"
