"""What the admin console shows: the agent's performance over a range of calls, the calls one by one, the users.

Every per-call number comes from the call report (report.py: the same turn analysis, checks and cost model as the
CLI). The overview pools every turn of every call before taking percentiles: a percentile of per-call percentiles
would weigh a two-turn call like a twenty-turn one.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..rates import call_cost
from ..report import _pct, build_report, load_inputs

IST = timezone(timedelta(hours=5, minutes=30))
RANGES = ("today", "7d", "30d")
CACHE_S = 300  # a finished call's report changes only when an SMS receipt lands
_CACHE: Dict[str, Tuple[float, Optional[dict]]] = {}
FLAGS = {  # summary field -> what the console calls it
    "talk_over": "talked over the caller", "bot_cut_off": "cut off by the caller", "dead_air_turns": "dead air",
    "empty_transcripts": "empty transcripts", "empty_llm_replies": "empty replies", "claim_guard": "claims stopped",
    "false_claims": "false 'done' claims", "repeats_prevented": "repeats stopped", "repeats_spoken": "repeats heard",
    "dropped_replies": "replies dropped", "echo_drops": "echo dropped", "tool_deferrals": "tool calls put off",
}
CAUGHT = ("repeats_prevented", "claim_guard", "echo_drops", "tool_deferrals")  # a guard stopped it: not a call's flag


def mask(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    d = "".join(ch for ch in phone if ch.isdigit())
    return f"+{d[:-10]} {d[-10:-8]}••• ••{d[-3:]}" if len(d) >= 10 else "•••" + d[-3:]


def window(range_key: str, now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """[since, until) in UTC: whole days in India, today included ("7d" is today and the six days before it), so
    the daily trend's buckets are all full days but today."""
    now = now or datetime.now(timezone.utc)
    days = {"today": 0, "7d": 6, "30d": 29}[range_key]
    midnight = now.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
    return midnight.astimezone(timezone.utc), now


def _buckets(since: datetime, until: datetime, hourly: bool) -> List[str]:
    """Every bucket of the window in order, the empty ones too (a gap in the chart is a quiet hour, not a jump)."""
    first, last = since.astimezone(IST), until.astimezone(IST)
    if hourly:
        return [f"{h:02d}:00" for h in range(first.hour, last.hour + 1)]
    return [_day_label(first + timedelta(days=i)) for i in range((last.date() - first.date()).days + 1)]


def _day_label(local: datetime) -> str:
    return f"{local.day} {local:%b}"


def _loads(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


async def report_for(con, sid: str, record: Optional[dict]) -> Optional[dict]:
    """The call's report (cached a few minutes), or None when the call left no trace (refused, or never streamed)."""
    hit = _CACHE.get(sid)
    if hit is not None and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    row = await con.fetchrow("select trace from call_traces where session_id=$1 order by created_at desc limit 1", sid)
    report = None
    if row is not None:
        tasks, events, deliveries = await load_inputs(con, sid)
        report = build_report(_loads(row["trace"]), tasks, events, deliveries, session_id=sid, record=record)
        report["cost"] = call_cost(report["summary"], record)
    _CACHE[sid] = (time.monotonic(), report)
    return report


def _kind_sql(kind: str) -> str:
    return {"real": " and not test", "test": " and test"}.get(kind, "")


async def _records(con, since: datetime, until: datetime, kind: str) -> List[dict]:
    rows = await con.fetch("select * from call_records where admitted_at >= $1 and admitted_at < $2"
                           + _kind_sql(kind) + " order by admitted_at desc", since, until)
    return [dict(r) for r in rows]


def _timed(turns: List[dict]) -> List[dict]:
    """The turns the latency SLOs cover (as in the report): answered, and not held for a caller mid-thought."""
    return [t for t in turns if "v2v_s" in t and t.get("verdict") != "incomplete"]


def _spread(values: List[float]) -> Dict[str, Optional[float]]:
    return {"p50": _pct(values, 0.5), "p95": _pct(values, 0.95), "p99": _pct(values, 0.99), "n": len(values)}


async def overview(con, range_key: str = "7d", kind: str = "real", now: Optional[datetime] = None) -> Dict[str, Any]:
    since, until = window(range_key, now)
    records = await _records(con, since, until, kind)
    admitted = [r for r in records if r["decision"] == "admit"]
    refused = Counter(r["reason"] or "other" for r in records if r["decision"] == "refuse")
    turns_all: List[dict] = []
    v2v: List[float] = []
    parts: Dict[str, List[float]] = defaultdict(list)
    ttfw: List[float] = []
    flags: Counter = Counter()
    docs: Dict[str, Counter] = {"create": Counter(), "edit": Counter()}
    doc_time: Dict[str, List[float]] = {"create": [], "edit": []}
    sms: Counter = Counter()
    cost: Counter = Counter()
    costed_min, costed_calls = 0.0, 0
    hourly = range_key == "today"
    trend = {k: {"bucket": k, "calls": 0, "v2v": []} for k in _buckets(since, until, hourly)}
    for rec in admitted:
        r = await report_for(con, rec["call_uuid"], rec)
        local = rec["admitted_at"].astimezone(IST)
        bucket = f"{local.hour:02d}:00" if hourly else _day_label(local)
        b = trend.setdefault(bucket, {"bucket": bucket, "calls": 0, "v2v": []})
        b["calls"] += 1
        if r is None:
            continue
        costed_calls += 1
        s = r["summary"]
        timed = _timed(r["turns"])
        turns_all += r["turns"]
        v2v += [t["v2v_s"] for t in timed]
        b["v2v"] += [t["v2v_s"] for t in timed]
        parts["v2v_plain"] += [t["v2v_s"] for t in timed if not t.get("tools")]
        parts["v2v_tool"] += [t["v2v_s"] for t in timed if t.get("tools")]
        for key in ("ttft_s", "tts_s", "hold_s"):
            parts[key] += [t[key] for t in r["turns"] if key in t]
        parts["eot_s"] += [t["eot_s"] for t in r["turns"]]
        if s.get("ttfw_s") is not None:
            ttfw.append(s["ttfw_s"])
        for key in FLAGS:
            flags[key] += int(s.get(key) or 0)
        for row in r["thinker"]:
            if row["kind"] in docs:
                docs[row["kind"]][row["state"]] += 1
                if row["state"] == "completed" and row.get("total_s") is not None:
                    doc_time[row["kind"]].append(row["total_s"])
        for d in r["delivery"]:
            sms[d["status"]] += 1
        for k, v in r["cost"]["inr"].items():
            cost[k] += v
        costed_min += float(rec.get("duration_s") or s.get("duration_s") or 0) / 60
    minutes = sum(float(r.get("duration_s") or 0) for r in admitted) / 60
    total_cost = round(sum(cost.values()), 2)
    created = docs["create"]["completed"]
    return {
        "range": range_key, "kind": kind, "since": since.isoformat(), "until": until.isoformat(),
        "calls": {"admitted": len(admitted), "refused": dict(refused), "refused_total": sum(refused.values()),
                  "minutes": round(minutes, 1), "unique_callers": len({r["caller"] for r in admitted if r["caller"]}),
                  "by_channel": dict(Counter("browser" if r["channel"] == "sip" else "phone" for r in admitted)),
                  "with_trace": costed_calls},
        "latency": {"v2v": _spread(v2v), "v2v_plain": _spread(parts["v2v_plain"]),
                    "v2v_tool": _spread(parts["v2v_tool"]), "eot": _spread(parts["eot_s"]),
                    "ttft": _spread(parts["ttft_s"]), "tts": _spread(parts["tts_s"]),
                    "hold": _spread(parts["hold_s"]), "ttfw": _spread(ttfw),
                    "slow_share": round(sum(1 for x in v2v if x > 1.5) / len(v2v), 3) if v2v else None,
                    "turns": len(turns_all)},
        "flags": [{"key": k, "label": FLAGS[k], "count": flags[k]} for k in FLAGS],
        "documents": {
            kind_: {"completed": c["completed"], "failed": c["failed"],
                    "success_rate": round(c["completed"] / (c["completed"] + c["failed"]), 3)
                    if c["completed"] + c["failed"] else None,
                    "time_p50_s": _pct(doc_time[kind_], 0.5), "time_p95_s": _pct(doc_time[kind_], 0.95)}
            for kind_, c in docs.items()},
        "sms": {"submitted": sum(sms.values()), "delivered": sms["delivered"], "failed": sms["failed"],
                "by_status": dict(sms)},
        "cost": {"total_inr": total_cost, "by_component": {k: round(v, 2) for k, v in cost.items()},
                 "per_call_inr": round(total_cost / costed_calls, 2) if costed_calls else None,
                 "per_min_inr": round(total_cost / costed_min, 2) if costed_min else None,
                 "per_doc_inr": round(total_cost / created, 2) if created else None},
        "trend": [{"bucket": b["bucket"], "calls": b["calls"], "v2v_p50": _pct(b["v2v"], 0.5),
                   "v2v_p95": _pct(b["v2v"], 0.95)} for b in trend.values()],
        "last_call": await last_call(con, kind),
    }


def _call_row(rec: dict, r: Optional[dict]) -> Dict[str, Any]:
    meta = _loads(rec.get("meta")) or {}
    row: Dict[str, Any] = {
        "id": rec["call_uuid"], "at": rec["admitted_at"].isoformat(),
        "channel": "browser" if rec["channel"] == "sip" else ("test" if rec.get("test") else "phone"),
        "caller": mask(rec.get("caller")), "decision": rec["decision"], "reason": rec.get("reason"),
        "duration_s": rec.get("duration_s"), "plan": meta.get("plan"), "via": meta.get("via"),
        "test": bool(rec.get("test")),
    }
    if r is not None:
        s = r["summary"]
        row.update({"turns": s["caller_turns"], "v2v_p50_s": s["v2v_p50_s"], "v2v_p90_s": s["v2v_p90_s"],
                    "v2v_max_s": s["v2v_max_s"], "cost_inr": r["cost"]["total_inr"],
                    "docs": {k: sum(1 for t in r["thinker"] if t["kind"] == k and t["state"] == "completed")
                             for k in ("create", "edit")},
                    "sms": [d["status"] for d in r["delivery"]],
                    "flags": [FLAGS[k] for k in FLAGS if int(s.get(k) or 0) > 0 and k not in CAUGHT],
                    "checks_failed": [c["check"] for c in r["checks"] if not c["ok"]]})
    return row


async def call_list(con, since: datetime, until: datetime, kind: str = "real", channel: str = "", q: str = "",
                    min_v2v: Optional[float] = None, has_doc: bool = False, flag: str = "",
                    limit: int = 200) -> List[Dict[str, Any]]:
    sql = "select * from call_records where admitted_at >= $1 and admitted_at < $2" + _kind_sql(kind)
    args: List[Any] = [since, until]
    if channel in ("phone", "browser"):
        sql += " and channel = $3"
        args.append("sip" if channel == "browser" else "pstn")
    q = q.strip().lower()
    if CALL_ID.match(q) and (re.search(r"[a-f-]", q) or q.startswith("t")):  # a call id, or its first characters
        sql += f" and call_uuid like ${len(args) + 1}"
        args.append(f"{q}%")
    elif "".join(ch for ch in q if ch.isdigit()):
        sql += f" and caller like ${len(args) + 1}"
        args.append(f"%{''.join(ch for ch in q if ch.isdigit())}%")
    rows = await con.fetch(sql + " order by admitted_at desc limit 500", *args)
    out = []
    for rec in (dict(x) for x in rows):
        r = await report_for(con, rec["call_uuid"], rec) if rec["decision"] == "admit" else None
        row = _call_row(rec, r)
        if min_v2v is not None and (row.get("v2v_max_s") is None or row["v2v_max_s"] < min_v2v):
            continue
        if has_doc and not sum((row.get("docs") or {}).values()):
            continue
        if flag and flag not in (row.get("flags") or []):
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


async def _trace(con, sid: str) -> Optional[dict]:
    row = await con.fetchrow("select trace, started_at from call_traces where session_id=$1 order by created_at desc "
                             "limit 1", sid)
    if row is None:
        return None
    trace = _loads(row["trace"]) or {}
    trace["started_at"] = row["started_at"].isoformat() if row["started_at"] else None
    return trace


def _events(trace: Optional[dict]) -> List[dict]:
    return sorted((trace or {}).get("events", []), key=lambda e: e.get("t", 0))


# Plivo's HangupSource on the hangup webhook (kept in call_records), in words
HANGUP_BY = {"caller": "The caller hung up", "callee": "BolDoc hung up", "api request": "BolDoc hung up",
             "answer xml": "BolDoc hung up", "plivo": "The phone network ended it", "error": "Ended by an error"}


def ending(record: Optional[dict], evs: List[dict]) -> Dict[str, Any]:
    """How the call ended: BolDoc's side from the trace (time limit, idle goodbye, end_call), else the caller's or the
    network's from Plivo's hangup record; a scripted test call just closes its audio stream."""
    rec = record or {}
    cdr = (_loads(rec.get("meta")) or {}).get("cdr") or {}
    source = rec.get("hangup_source") or cdr.get("HangupSource")
    cause = rec.get("hangup_cause") or cdr.get("HangupCause")
    seen = lambda kind, **f: any(e.get("e") == kind and all(  # noqa: E731
        str(e.get(k) or "").startswith(v) for k, v in f.items()) for e in evs)
    if rec.get("decision") == "refuse":
        how, label = "refused", "Turned away before BolDoc picked up"
    elif seen("refused"):
        how, label = "refused", "Not admitted: BolDoc said why and hung up"
    elif seen("time_limit", stage="call.time_up"):
        how, label = "time_limit", "The call's time limit: BolDoc said goodbye"
    elif seen("announce", type="call.idle_goodbye"):
        how, label = "idle_goodbye", "The caller went quiet: BolDoc said goodbye"
    elif seen("tool", tool="end_call", status="ending"):
        how, label = "end_call", "The caller was done: BolDoc said goodbye"
    elif source:
        how, label = "hangup", HANGUP_BY.get(str(source).lower(), f"Hung up ({source})")
    else:
        how, label = "stream_closed", "The audio stream closed (no hang-up record)"
    return {"how": how, "label": label, "hangup_source": source, "hangup_cause": cause}


def languages(record: Optional[dict], evs: List[dict]) -> Dict[str, Any]:
    """The language the call started in (recorded when picked on the website) and every switch the talker made."""
    meta = _loads((record or {}).get("meta")) or {}
    return {"start": meta.get("lang"),
            "switches": [{"t": round(float(e.get("t", 0)), 1), "family": e.get("family")}
                         for e in evs if e.get("e") == "lang_switch"]}


# The call page's timeline: the trace's events but the per-frame noise (voice activity, raw transcripts, usage
# counters, sentence fingerprints), at most this many.
TIMELINE_SKIP = {"vad_start", "vad_stop", "stt_final", "usage", "said"}
TIMELINE_MAX = 1500


def timeline(evs: List[dict], content: bool) -> Dict[str, Any]:
    kept = [e for e in evs if e.get("e") not in TIMELINE_SKIP]
    events = [{k: v for k, v in e.items() if k != "mono" and (content or k != "text")} for e in kept[:TIMELINE_MAX]]
    return {"events": events, "total": len(kept), "truncated": len(kept) > TIMELINE_MAX}


def without_words(report: Optional[dict]) -> Optional[dict]:
    """A report whose call kept no words shows none, whatever the trace held: the numbers stay."""
    if report is None or report.get("content"):
        return report
    turns = [{k: v for k, v in t.items() if k not in ("text", "reply_text")} for t in report["turns"]]
    return {**report, "turns": turns, "false_claims": ["(words not kept)"] * len(report.get("false_claims") or [])}


async def documents_of(con, sid: str) -> List[Dict[str, Any]]:
    """Documents this call wrote, and the earlier ones it changed (an edit task's document)."""
    edited = [r["doc_id"] for r in await con.fetch(
        "select data->>'doc_id' as doc_id from entities where kind='task' and session_id=$1", sid) if r["doc_id"]]
    rows = await con.fetch("select id, session_id, title, head_version, formats, doc_lang, created_at, updated_at "
                           "from documents where session_id=$1 or id = any($2::text[]) order by created_at",
                           sid, edited)
    return [{"id": r["id"], "title": r["title"] or "Untitled", "versions": r["head_version"],
             "formats": _loads(r["formats"]) or [], "lang": r["doc_lang"], "this_call": r["session_id"] == sid,
             "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat()} for r in rows]


CALL_ID = re.compile(r"^(?:test-|t-)?[0-9a-f][0-9a-f-]{5,}$")  # 6+ characters of a call id ("08edee88")


async def resolve_call_id(con, call_id: str) -> str:
    """The whole CallUUID for the first characters of one (as the console shows them), when exactly one call has
    them; else the id as given."""
    if not CALL_ID.match(call_id) or await con.fetchval("select 1 from call_records where call_uuid=$1", call_id):
        return call_id
    rows = await con.fetch("select call_uuid from call_records where call_uuid like $1 || '%' "
                           "order by admitted_at desc limit 2", call_id)
    return rows[0]["call_uuid"] if len(rows) == 1 else call_id


async def call_detail(con, call_id: str) -> Optional[Dict[str, Any]]:
    """Everything the call page shows: the call record, its report (words only where kept), how it ended, its
    languages, the trace's timeline and its documents."""
    call_id = await resolve_call_id(con, call_id)
    rec = await con.fetchrow("select * from call_records where call_uuid=$1", call_id)
    record = dict(rec) if rec else None
    r = await report_for(con, call_id, record)
    if r is None and record is None:
        return None
    trace = await _trace(con, call_id)
    evs = _events(trace)
    call = _call_row(record, r) if record else {"id": call_id, "at": (trace or {}).get("started_at")}
    call.update({"ended": ending(record, evs), "language": languages(record, evs),
                 "called": (record or {}).get("called"), "first_call": bool((record or {}).get("first_call"))})
    return {"call": call, "report": without_words(r), "timeline": timeline(evs, bool(r and r.get("content"))),
            "documents": await documents_of(con, call_id)}


async def latest_call_id(con, kind: str = "real") -> Optional[str]:
    """The newest answered call (refused ones left no trace to analyse)."""
    return await con.fetchval("select call_uuid from call_records where decision='admit'" + _kind_sql(kind)
                              + " order by admitted_at desc limit 1")


async def last_call(con, kind: str = "real") -> Optional[Dict[str, Any]]:
    """The overview's "Last call" card: who, when, how long, how it ended, what it made, what failed."""
    rec = await con.fetchrow("select * from call_records where decision='admit'" + _kind_sql(kind)
                             + " order by admitted_at desc limit 1")
    if rec is None:
        return None
    record = dict(rec)
    r = await report_for(con, record["call_uuid"], record)
    evs = _events(await _trace(con, record["call_uuid"]))
    return {**_call_row(record, r), "ended": ending(record, evs), "language": languages(record, evs)}


async def users(con, q: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    digits = "".join(ch for ch in q if ch.isdigit())
    rows = await con.fetch(
        "select u.id, u.phone_e164, u.status, u.created_at, u.verified_at, "
        "(select count(*) from user_phones p where p.user_id = u.id) as linked, "
        "(select count(*) from call_records c where c.user_id = u.id and c.decision = 'admit' and not c.test) "
        "  as calls, "
        "(select coalesce(sum(c.duration_s), 0) from call_records c where c.user_id = u.id and not c.test) as secs, "
        "(select count(*) from documents d where d.head_version > 0 and (d.owner_user_id = u.id or d.session_id in "
        "  (select call_uuid from call_records where user_id = u.id))) as docs, "
        "coalesce((select balance_after from credit_ledger l where l.user_id = u.id order by l.id desc limit 1), 0) "
        "  as credits "
        "from users u where ($1 = '' or u.phone_e164 like '%' || $1 || '%' or exists (select 1 from user_phones p "
        "  where p.user_id = u.id and p.phone_e164 like '%' || $1 || '%')) "
        "order by u.created_at desc limit $2", digits, limit)
    return [{"id": r["id"], "phone": mask(r["phone_e164"]), "status": r["status"],
             "plan": "credits" if r["status"] == "active" else r["status"], "linked": r["linked"],
             "calls": r["calls"], "minutes": round(float(r["secs"]) / 60, 1), "documents": r["docs"],
             "credits": r["credits"], "created_at": r["created_at"].isoformat(),
             "verified_at": r["verified_at"].isoformat() if r["verified_at"] else None} for r in rows]


async def user_numbers(con, user_id: int) -> Optional[List[str]]:
    """Unmasked: every number of the account (the caller of this writes the audit entry)."""
    row = await con.fetchrow("select phone_e164 from users where id=$1", user_id)
    if row is None:
        return None
    linked = await con.fetch("select phone_e164 from user_phones where user_id=$1 order by added_at", user_id)
    return [row["phone_e164"]] + [r["phone_e164"] for r in linked]
