"""Call admission and call records: who may call, for how long, and what each call was like.

Admission runs on Plivo's signed answer webhook, before any audio flows. The talker asks control; control decides
from the caller, the capacity in use and (with accounts) the caller's allowance, and writes a call record. The audio
stream that follows trusts only this record for identity: a stream's own headers are ignored, so holding the
stream key is not enough to act as someone else.

The record is closed by Plivo's signed hangup webhook (duration, billed seconds, cost, cause), which arrives even
when the talker died mid-call. The talker's trace, when it arrives, adds the per-turn rows and the call report.

CLI (on the box), used by the e2e gate to admit a scripted call with a real caller number:
  docker compose exec control python -m apps.control.calls admit <call_uuid> <from> [--test]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from . import report as reports
from .accounts import browser, callme, ledger, users
from .accounts import web as accounts_web
from .accounts.pricing import PRICING
from .callback import MAX_CALL_S as CALLBACK_MAX_S
from .phones import indian_mobile, masked

# Why a call was refused -> the clip the caller hears (web/public/clips/<name>.wav, made by tools/clips). the languages
# a call can start in when the caller picks one on the website (the talker's voices) The languages a call can start in
# (picked on the website, or the caller's last call's). Not Punjabi : BolDoc doesn't talk in it, and a call that drifted
# into it must not start the next one there.
LANGS = {"en-IN", "hi-IN", "bn-IN", "gu-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "ta-IN", "te-IN"}
CLIPS = {"busy": "busy", "already_on_call": "already-on-call", "time_up": "time-up", "trial_over": "trial-over",
         "no_credits": "no-credits", "verified": "verified", "withheld": "withheld", "too_frequent": "too-frequent",
         "daily_limit": "daily-limit", "trial_budget": "trial-budget"}
DEFAULT_CLIP = "refused"


@dataclass
class Caller:
    channel: str            # pstn | sip
    number: Optional[str]   # +91… for Indian mobiles, +<digits> for other numbers; None for browser/withheld
    kind: str               # mobile | other | withheld | browser


def classify_from(raw: str) -> Caller:
    """Plivo's `From`: '919988776655' (phone) or 'sip:user@phone.plivo.com' (browser). SIP is recognised before
    any digit stripping: an endpoint name's digits are not a phone number."""
    raw = (raw or "").strip()
    if raw.lower().startswith("sip:"):
        return Caller("sip", None, "browser")
    mobile = indian_mobile(raw)
    if mobile:
        return Caller("pstn", mobile, "mobile")
    digits = "".join(ch for ch in raw if ch.isdigit()).lstrip("0")
    return Caller("pstn", "+" + digits, "other") if len(digits) >= 6 else Caller("pstn", None, "withheld")


@dataclass
class Admission:
    decision: str                 # admit | refuse
    reason: Optional[str]         # why refused: trial_over | no_credits | suspended | busy | already_on_call |
    #                               time_up | browser_unauthenticated | not_admitted
    channel: str
    caller: Optional[str]
    caller_kind: str
    max_call_s: int
    first_call: bool = False
    test: bool = False
    degraded: bool = False
    user_id: Optional[int] = None
    plan: Optional[str] = None    # trial | credits | anonymous (withheld number) | test
    calls_left: Optional[int] = None
    balance: Optional[int] = None
    llm: str = "primary"          # primary (Sarvam) | overflow (the slower model that keeps busy times answered)
    lang: Optional[str] = None    # the language picked on the website (browser call, Call me); None: English
    remembered_lang: Optional[str] = None  # the language the caller's last call ended in; the talker starts in it
    #                                        unless one was picked for this call

    @property
    def clip(self) -> Optional[str]:
        return None if self.decision == "admit" else CLIPS.get(self.reason or "", DEFAULT_CLIP)

    def as_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "clip": self.clip}


def decide(caller: Caller, *, prior: Optional[Dict[str, Any]], resumed: bool, active: int, cap: int,
           caller_busy: bool, max_call_s: int, first_call: bool, test: bool = False,
           late: bool = False, elapsed_s: float = 0.0, allowance: Optional[Dict[str, Any]] = None,
           primary_active: int = 0, primary_cap: Optional[int] = None, overflow: bool = False,
           fair: Optional[Dict[str, Any]] = None) -> Admission:
    """The admission rules, free of I/O.

    * A resumed stream (Plivo re-fetched the answer URL after a stream dropped) continues an admitted call: no new
      capacity is taken and nothing is counted twice. But a stream that ended because the call's time ran out
      (Plivo's streamTimeout) also lands here: past the limit, the caller hears "time is up" instead.
    * A browser (SIP) call is the signed-in account's call once admission has redeemed the website's one-time
      session (`caller.number` is then the account's phone); without one it is refused.
    * Above the concurrent-call cap callers hear "busy". The primary LLM's rate limit is the scarce resource: past
      `primary_cap` calls, new calls use the overflow LLM if one is configured (else they hear "busy" too).
    * One call at a time per number.
    * The caller's allowance (accounts.users.allowance): a trial with calls left, or credits; it also sets the call's
      length (5 minutes on the trial, what the balance covers otherwise). Checked before capacity: a caller whose
      trial is over should hear that, not "busy".
    * `late`: the talker admitted the call itself while control was unreachable and audio is already flowing, so
      only identity is recorded; refusing now would cut off a caller mid-sentence.
    * Fair use (`fair`, see CallDesk._fair_use): a withheld number can't be limited, so it is refused; then calls
      in a burst, calls and talk time a day per number, and the day's budget for all trial calls together. The
      call's length never runs past what is left of the day.
    """
    def refuse(reason: str) -> Admission:
        return Admission("refuse", reason, caller.channel, caller.number, caller.kind, 0, test=test)

    if resumed and prior is not None:
        if prior["decision"] == "admit" and prior.get("ended_at") is None and elapsed_s >= int(prior["max_call_s"]):
            return refuse("time_up")
        if prior["decision"] == "admit" and prior.get("ended_at") is None:
            meta = prior.get("meta") or {}
            meta = json.loads(meta) if isinstance(meta, str) else meta
            return Admission("admit", None, prior["channel"], prior["caller"], prior["caller_kind"],
                             int(prior["max_call_s"]), test=bool(prior.get("test")), user_id=prior.get("user_id"),
                             plan=meta.get("plan"), llm=meta.get("llm") or "primary")
        return refuse("not_admitted")
    if caller.channel == "sip" and caller.number is None:
        return refuse("browser_unauthenticated")
    allowance = allowance or {}
    limit = min(max_call_s, int(allowance.get("max_call_s") or max_call_s))
    extra = dict(plan=allowance.get("plan"), calls_left=allowance.get("calls_left"),
                 balance=allowance.get("balance"))
    if late:
        return Admission("admit", None, caller.channel, caller.number, caller.kind, limit, first_call=first_call,
                         test=test, degraded=True, **extra)
    if allowance.get("refuse"):
        return Admission("refuse", allowance["refuse"], caller.channel, caller.number, caller.kind, 0, test=test,
                         **extra)
    if fair:
        if fair.get("withheld"):
            return refuse("withheld")
        if fair["calls_burst"] >= fair["burst_max"]:
            return refuse("too_frequent")
        if fair["calls_day"] >= fair["calls_day_max"] or fair["secs_left"] < 60:
            return refuse("daily_limit")
        if fair.get("trial_budget_left") is not None and fair["trial_budget_left"] < 60:
            return refuse("trial_budget")
        limit = min(limit, int(fair["secs_left"]))
    if active >= cap:
        return refuse("busy")
    if caller_busy:
        return refuse("already_on_call")
    llm = "primary"
    if primary_cap is not None and primary_active >= primary_cap:
        if not overflow:
            return refuse("busy")
        llm = "overflow"
    return Admission("admit", None, caller.channel, caller.number, caller.kind, limit, first_call=first_call,
                     test=test, llm=llm, **extra)


def _via(placed: Dict[str, Any]) -> Dict[str, Any]:
    """How a call we placed is recorded: a Call me, or a callback about a long document's questions."""
    if placed.get("kind") == "callback":
        return {"via": "callback", "callback": {k: placed.get(k) for k in ("task_id", "doc_id", "title", "n")}}
    return {"via": "call_me", "call_me": placed.get("id")}  # the website's request: its panel learns of the end


def _meta(record: Dict[str, Any]) -> Dict[str, Any]:
    m = record.get("meta") or {}
    return json.loads(m) if isinstance(m, str) else m


def _int(v: Any) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class CallDesk:
    def __init__(self, pool, bus, *, cap: int, max_call_s: int, primary_cap: Optional[int] = None,
                 overflow: bool = False):
        self.pool, self.bus, self.cap, self.max_call_s = pool, bus, cap, max_call_s
        self.primary_cap, self.overflow = primary_cap, overflow
        self.on_refused = None  # async callback(Admission): e.g. the trial-over SMS

    async def _fair_use(self, con, caller: Caller, call_uuid: str, plan: Optional[str]) -> Optional[Dict[str, Any]]:
        """How much this number has used (connected calls, short ones too) for decide's fair-use rules. None: not
        limited (the owner's test phones, browser calls without a number)."""
        p = PRICING
        if caller.kind == "withheld":
            return {"withheld": True} if p.refuse_withheld else None
        if caller.number is None or caller.number in p.unlimited_numbers:
            return None
        row = await con.fetchrow(
            "select count(*) filter (where admitted_at > now() - interval '24 hours') as calls_day, "
            "count(*) filter (where admitted_at > now() - make_interval(secs => $2::int)) as calls_burst, "
            "coalesce(sum(least(coalesce(duration_s, extract(epoch from coalesce(ended_at, now()) - admitted_at)"
            "::int), max_call_s)) filter (where admitted_at > now() - interval '24 hours'), 0) as secs_day "
            "from call_records where caller=$1 and decision='admit' and not test and call_uuid<>$3",
            caller.number, p.burst_window_s, call_uuid)
        trial = plan in ("trial", "anonymous")
        day_s = (p.trial_minutes_per_day if trial else p.minutes_per_day) * 60
        fair = {"calls_day": int(row["calls_day"]), "calls_burst": int(row["calls_burst"]),
                "calls_day_max": p.trial_calls_per_day if trial else p.calls_per_day, "burst_max": p.burst_calls,
                "secs_left": max(0, day_s - int(row["secs_day"]))}
        if trial and p.trial_minutes_budget > 0:
            used = await con.fetchval(
                "select coalesce(sum(least(coalesce(duration_s, 0), max_call_s)), 0) from call_records where "
                "decision='admit' and not test and meta->>'plan' = 'trial' and admitted_at > now() - "
                "interval '24 hours'")
            fair["trial_budget_left"] = p.trial_minutes_budget * 60 - int(used or 0)
        return fair

    async def admit(self, call_uuid: str, raw_from: str, to: str = "", *, resumed: bool = False, test: bool = False,
                    late: bool = False, session_token: Optional[str] = None,
                    call_me: Optional[str] = None) -> Admission:
        caller = Caller("test", None, "test") if test and not raw_from else classify_from(raw_from)
        placed, expired, picked = None, False, None
        if call_me and not resumed:
            # "Call me" from the account: we rang one of its numbers, so the call is from that number (From is ours)
            placed = await callme.redeem(call_me)
            caller = Caller("pstn", placed["number"], "mobile") if placed else caller
            expired = placed is None
            picked = (placed or {}).get("lang")
        # A callback about a long document's questions (callback.py): asked for on the call, so free and never
        # refused by fair use; only a suspended account is refused
        callback = placed if placed and placed.get("kind") == "callback" else None
        async with self.pool.acquire() as con:
            prior = await con.fetchrow("select * from call_records where call_uuid=$1", call_uuid)
            if prior is not None and not resumed:
                # Plivo retried the answer webhook: same call, same answer (decided once).
                p = dict(prior)
                return Admission(p["decision"] if p["decision"] in ("admit", "refuse") else "refuse", p["reason"],
                                 p["channel"], p["caller"], p["caller_kind"], int(p["max_call_s"] or 0),
                                 first_call=p["first_call"], test=p["test"], degraded=p["degraded"],
                                 user_id=p.get("user_id"), plan=_meta(p).get("plan"),
                                 llm=_meta(p).get("llm") or "primary", lang=_meta(p).get("lang"),
                                 remembered_lang=_meta(p).get("remembered_lang"))
            if caller.channel == "sip" and not resumed:
                # "Talk in browser": the website's one-time session says whose call this is (used up here)
                who = await browser.redeem(session_token)
                account = await users.by_id(con, who["user_id"]) if who else None
                if account is not None:
                    caller = Caller("sip", account["phone_e164"], "browser")
                    picked = (who or {}).get("lang")
            first = caller.number is not None and not await con.fetchval(
                "select exists(select 1 from call_records where caller=$1 and decision='admit' and call_uuid<>$2)",
                caller.number, call_uuid)
            active = [u for u in await self.bus.active_calls() if u != call_uuid]
            caller_busy = bool(caller.number and active and await con.fetchval(
                "select exists(select 1 from call_records where call_uuid = any($1::text[]) and caller=$2 "
                "and ended_at is null)", active, caller.number))
            elapsed = 0.0
            if prior is not None:
                elapsed = (datetime.now(timezone.utc) - prior["admitted_at"]).total_seconds()
            # Who is calling and what they may do. Scripted test calls have no account (no trial to use up).
            user = None if test or caller.number is None else await users.ensure_caller(con, caller.number)
            allowance = {"plan": "test"} if test else await users.allowance(con, user)
            if callback is not None:
                allowance = {**allowance, "max_call_s": CALLBACK_MAX_S,
                             "refuse": "suspended" if allowance.get("refuse") == "suspended" else None}
            if not test and not resumed and caller.channel == "pstn" and caller.number and \
                    await accounts_web.verified_by_call(caller.number):
                # "verify by calling" from the website: this call only proves the number; one sentence, then bye
                allowance = {**allowance, "refuse": "verified"}
            primary_active = await self.bus.primary_count(active)
            fair = None if test or resumed or late or callback else await self._fair_use(
                con, caller, call_uuid, allowance.get("plan"))
            adm = decide(caller, prior=dict(prior) if prior else None, resumed=resumed, active=len(active),
                         cap=self.cap, caller_busy=caller_busy, max_call_s=self.max_call_s, first_call=first,
                         test=test, late=late, elapsed_s=elapsed, allowance=allowance,
                         primary_active=primary_active, primary_cap=self.primary_cap, overflow=self.overflow,
                         fair=fair)
            if expired:  # picked up too late, or not a call we placed: one sentence, then goodbye
                adm = Admission("refuse", "call_me_expired", "pstn", None, "call_me", 0)
            adm.user_id = user["id"] if user and not expired else None
            adm.lang = picked if picked in LANGS else None
            remembered = (user or {}).get("call_lang")  # phone, "Call me" or the browser: the same account
            adm.remembered_lang = remembered if remembered in LANGS and not expired else None
            if prior is None:
                await con.execute(
                    "insert into call_records (call_uuid, channel, caller, caller_kind, called, decision, reason, "
                    "max_call_s, test, first_call, degraded, user_id, meta) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,"
                    "$11,$12,$13::jsonb) on conflict (call_uuid) do nothing", call_uuid, adm.channel, adm.caller,
                    adm.caller_kind, to or None, adm.decision, adm.reason, adm.max_call_s, adm.test, adm.first_call,
                    adm.degraded, adm.user_id, json.dumps({"plan": adm.plan, "llm": adm.llm,
                                                           **(_via(placed) if placed else {}),
                                                           **({"lang": adm.lang} if adm.lang else {}),
                                                           **({"remembered_lang": adm.remembered_lang}
                                                              if adm.remembered_lang else {})}))
            else:
                await con.execute("update call_records set meta = jsonb_set(meta, '{resumes}', "
                                  "to_jsonb(coalesce((meta->>'resumes')::int, 0) + 1)) where call_uuid=$1", call_uuid)
        if placed:
            await callme.answered(placed, adm.decision == "admit")
        if adm.decision == "admit":
            await self.bus.mark_active(call_uuid, adm.llm)  # reserve the slot now; the talker's heartbeats keep it
            if caller.kind == "browser" and session_token and adm.user_id is not None:
                await browser.remember_call(session_token, call_uuid, adm.user_id, adm.max_call_s + 600)
        elif self.on_refused is not None:
            asyncio.get_running_loop().create_task(self.on_refused(adm))  # never delays the answer webhook
        logger.info(f"admission call={call_uuid[-8:]} {adm.decision}{' (' + adm.reason + ')' if adm.reason else ''} "
                    f"channel={adm.channel} caller={masked(adm.caller)} active={len(active)}/{self.cap} llm={adm.llm}"
                    f"{' resumed' if resumed else ''}{' late' if late else ''}")
        return adm

    async def get(self, call_uuid: str) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as con:
            r = await con.fetchrow("select * from call_records where call_uuid=$1", call_uuid)
        return dict(r) if r else None

    async def stream_started(self, call_uuid: str) -> None:
        async with self.pool.acquire() as con:
            await con.execute("update call_records set streams = streams + 1, first_stream_at = "
                              "coalesce(first_stream_at, now()) where call_uuid=$1", call_uuid)

    async def hangup(self, call_uuid: str, p: Dict[str, Any]) -> None:
        """Plivo's hangup webhook (forwarded by the talker after its signature check)."""
        meta = {k: p[k] for k in ("HangupCause", "HangupCauseCode", "HangupSource", "StartTime", "AnswerTime",
                                  "EndTime", "CallStatus", "Direction") if p.get(k)}
        caller = classify_from(str(p.get("From") or ""))
        async with self.pool.acquire() as con:
            await con.execute(
                "insert into call_records (call_uuid, channel, caller, caller_kind, called, decision) "
                "values ($1,$2,$3,$4,$5,'unrecorded') on conflict (call_uuid) do nothing",
                call_uuid, caller.channel, caller.number, caller.kind, p.get("To") or None)
            # coalesce: a retried or partial webhook never erases what we already have
            await con.execute(
                "update call_records set ended_at = coalesce(ended_at, now()), duration_s=coalesce($2, duration_s), "
                "bill_s=coalesce($3, bill_s), cost_usd=coalesce($4, cost_usd), bill_rate_usd=coalesce($5, "
                "bill_rate_usd), hangup_cause=coalesce($6, hangup_cause), hangup_source=coalesce($7, hangup_source), "
                "meta = meta || $8::jsonb where call_uuid=$1",
                call_uuid, _int(p.get("Duration")), _int(p.get("BillDuration")), _num(p.get("TotalCost")),
                _num(p.get("BillRate")), p.get("HangupCauseName") or p.get("HangupCause"), p.get("HangupSource"),
                json.dumps({"cdr": meta}))
            await self._settle(con, call_uuid)
            rec = await con.fetchrow("select user_id, meta from call_records where call_uuid=$1", call_uuid)
        await self.bus.clear_active(call_uuid)
        m = _meta(dict(rec)) if rec is not None else {}
        if m.get("via") == "call_me" and m.get("call_me") and rec["user_id"] is not None:
            await callme.ended(m["call_me"], rec["user_id"])  # the console's "Call me" panel closes itself

    async def _settle(self, con, call_uuid: str) -> None:
        """After the CDR: does this call count against the trial, and what do its minutes cost?"""
        rec = await con.fetchrow("select user_id, decision, test, duration_s, meta from call_records "
                                 "where call_uuid=$1", call_uuid)
        if rec is None or rec["user_id"] is None or rec["decision"] != "admit" or rec["test"]:
            return
        if _meta(dict(rec)).get("via") == "callback":  # we called them, about their own document: free, never counted
            return
        talk_s = int(rec["duration_s"] or 0)
        made_doc = await con.fetchval("select exists(select 1 from documents where session_id=$1)", call_uuid)
        counted = talk_s >= PRICING.counted_call_s or bool(made_doc)
        await con.execute("update call_records set counted=$2 where call_uuid=$1", call_uuid, counted)
        user = await users.by_id(con, rec["user_id"])
        if user is not None and user["status"] == "active" and talk_s > 0:
            minutes = -(-talk_s // 60)
            await ledger.post(con, user["id"], "call_minutes", -minutes * PRICING.minute, f"call:{call_uuid}:minutes",
                              ref=call_uuid, reason=f"{minutes} min call")

    async def store_report(self, session_id: str, trace: Dict[str, Any]) -> Dict[str, Any]:
        """Build the call report once, when the trace arrives, and keep it with one row per caller turn (fleet
        percentiles are computed over turns; a percentile of per-call percentiles would be wrong)."""
        async with self.pool.acquire() as con:
            tasks, events, deliveries = await reports.load_inputs(con, session_id)
            record = await con.fetchrow("select * from call_records where call_uuid=$1", session_id)
            rep = reports.build_report(trace, tasks, events, deliveries, session_id=session_id,
                                       record=dict(record) if record else None)
            started = float(trace.get("started_wall") or 0)
            rows = [(session_id, t["turn"], datetime.fromtimestamp(started + float(t.get("t", 0)), tz=timezone.utc),
                     t["reply"], t.get("verdict"), t.get("words"), _ms(t.get("v2v_s")), _ms(t.get("eot_s")),
                     _ms(t.get("llm_wait_s")), _ms(t.get("ttft_s")), _ms(t.get("tts_s")), _ms(t.get("hold_s")),
                     list(t.get("flags") or [])) for t in rep["turns"]]
            async with con.transaction():
                await con.execute("insert into call_reports (session_id, report) values ($1, $2::jsonb) on conflict "
                                  "(session_id) do update set report=excluded.report, created_at=now()",
                                  session_id, json.dumps(rep, ensure_ascii=False, default=str))
                await con.execute("delete from call_turns where session_id=$1", session_id)
                if rows:
                    await con.executemany(
                        "insert into call_turns (session_id, turn, at, reply, verdict, words, v2v_ms, eot_ms, "
                        "llm_wait_ms, ttft_ms, tts_ms, hold_ms, flags) values "
                        "($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)", rows)
        return rep

    async def store_sms_receipt(self, p: Dict[str, Any]) -> None:
        async with self.pool.acquire() as con:
            await con.execute(
                "insert into sms_receipts (message_uuid, status, error_code, units, amount_usd, rate_usd, mcc, mnc) "
                "values ($1,$2,$3,$4,$5,$6,$7,$8)", str(p.get("MessageUUID") or ""), p.get("Status"),
                p.get("ErrorCode") or None, _int(p.get("Units")), _num(p.get("TotalAmount")), _num(p.get("TotalRate")),
                p.get("MCC") or None, p.get("MNC") or None)


def _ms(seconds: Any) -> Optional[int]:
    return None if seconds is None else round(float(seconds) * 1000)


async def _cli(argv: List[str]) -> int:
    import asyncpg

    from .bus import Bus
    from .settings import SETTINGS

    if len(argv) < 3 or argv[0] != "admit":
        print(__doc__)
        return 2
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    bus = Bus(SETTINGS.redis_url)
    try:
        desk = CallDesk(pool, bus, cap=SETTINGS.max_concurrent_calls, max_call_s=SETTINGS.max_call_s)
        adm = await desk.admit(argv[1], argv[2], test="--test" in argv)
        print(json.dumps(adm.as_dict()))
        return 0 if adm.decision == "admit" else 1
    finally:
        await bus.close()
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli(sys.argv[1:])))
