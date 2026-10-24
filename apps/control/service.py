"""Control plane HTTP service: the single writer for sessions, documents, tasks and deliveries.

Internal API (talker -> control; Docker network only, never routed by Caddy, plus a shared key):
  POST /internal/calls/admit                 Plivo answered: admit or refuse the call, write its call record
  POST /internal/calls/{uuid}/hangup         Plivo's hangup CDR (duration, cost, cause); closes the call record
  POST /internal/sessions                    open or take over a call session (takeover bumps the epoch); the
                                             caller's identity comes from the call record, never the stream
  POST /internal/commands                    a talker tool call (fenced by epoch, idempotent by idem_key)
  GET  /internal/sessions/{id}/events        long-poll for durable events after `after` (+ fresh snapshot)
  POST /internal/sessions/{id}/end           the call ended: work continues, the update SMS goes out
Public:
  GET  /api/config, /api/d/{token}, /api/d/{token}/download/{fmt}     link pages (read model in Postgres)
  POST /api/t                                anonymous funnel counters (no cookies, no third-party analytics)
  POST /plivo/sms-status                     Plivo delivery receipts (V2 signature-checked)
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl

import phonenumbers
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from loguru import logger
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from apps.thinker import catalogue as thinker_catalogue
from apps.thinker import intake as thinker_intake
from apps.thinker import scope as thinker_scope
from apps.thinker.llm import ThinkerError
from docengine.renderers import content_disposition
from protocol.llm import endpoint as llm_endpoint
from protocol.messages import Command

from . import api_errors, eta, mcp_http
from . import links as link_access
from . import live as live_view
from . import preview as doc_preview
from .accounts import browser as accounts_browser
from .accounts import callme as accounts_callme
from .accounts import documents as accounts_documents
from .accounts import files as accounts_files
from .accounts import ledger
from .accounts import numbers as accounts_numbers
from .accounts import web as accounts_web
from .accounts.pricing import PRICING
from .admin import api as admin_api
from .agent_config import AgentConfig, talker_view
from .bus import Bus
from .calls import LANGS as CALL_LANGS
from .calls import CallDesk
from .core.orchestrator import Orchestrator
from .phones import indian_mobile
from .settings import SETTINGS
from .sms import PlivoSms
from .store_pg import PgStore
from .testcall.runner import TestCalls
from .worker import ETA_S, Background

_command = TypeAdapter(Command)


class _State:
    orch: Orchestrator
    store: PgStore
    bg: Background
    bus: Bus
    calls: CallDesk
    flows = None  # TemporalFlows, when Temporal is reachable
    materials = None  # materials.Materials: files a caller adds for their long documents
    intakes: Dict[str, dict]  # session -> what the document asked for needs, and whether BolDoc may write it
    scopes: Dict[str, dict]   # session -> is it a long document (Hermes writes it), its type and outline


S = _State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    S.orch = Orchestrator(clock=time.time)  # wall clock: leases and timers survive a restart
    S.intakes = {}  # in memory only: the conversation's facts never land in a table for this
    S.scopes = {}
    S.store = await PgStore.connect(SETTINGS.database_url, S.orch)
    n = await S.store.load()
    requeued = S.orch.recover_after_restart()
    await S.store.flush()
    sms = None
    if SETTINGS.sms_enabled and SETTINGS.plivo_auth_id and SETTINGS.sms_src:
        sms = PlivoSms(SETTINGS.plivo_auth_id, SETTINGS.plivo_auth_token, SETTINGS.sms_src,
                       f"https://{SETTINGS.public_host}/plivo/sms-status")
    thinker = llm_endpoint("thinker")  # THINKER_LLM: any configured provider (protocol/llm.py)
    S.bg = Background(S.orch, S.store, thinker if thinker.api_key else SETTINGS.sarvam_api_key, SETTINGS.public_host,
                      sms, SETTINGS.link_ttl_days)
    if SETTINGS.hermes_url and SETTINGS.hermes_key and SETTINGS.hermes_mcp_key and thinker.api_key:
        from .bigdoc import BigDocs, Drafts, HermesClient
        S.bg.big = BigDocs(S.bg, HermesClient(SETTINGS.hermes_url, SETTINGS.hermes_key), Drafts(S.store.pool),
                           endpoint=thinker, work_s=SETTINGS.big_doc_work_s, answer_s=SETTINGS.big_doc_answer_s,
                           token_cap=SETTINGS.big_doc_token_cap,
                           research_token_cap=SETTINGS.big_doc_research_token_cap)
        if SETTINGS.serper_key:  # web research for the documents whose type needs it
            from .research import Research, Serper
            S.bg.big.research = Research(Serper(SETTINGS.serper_key))
        S.orch.big_docs = True
        mcp_http.CTX.big, mcp_http.CTX.key, mcp_http.CTX.endpoint = S.bg.big, SETTINGS.hermes_mcp_key, thinker
    S.bus = Bus(SETTINGS.redis_url)
    if S.bg.big is not None:  # a long document's questions reach a caller who hung up: a callback, then an SMS link
        from .callback import Callbacks
        S.bg.big.callbacks, S.bg.big.test_numbers = Callbacks(S.bus), tuple(SETTINGS.otp_test_numbers)
    S.calls = CallDesk(S.store.pool, S.bus, cap=SETTINGS.max_concurrent_calls, max_call_s=SETTINGS.max_call_s,
                       primary_cap=SETTINGS.primary_llm_calls, overflow=bool(SETTINGS.overflow_talker))
    S.calls.on_refused = _notify_refused
    w = accounts_web.CTX
    w.pool, w.bus, w.sms, w.host, w.dial_in = S.store.pool, S.bus, sms, SETTINGS.public_host, SETTINGS.dial_in_e164
    w.store = S.store
    w.member_dial_in = SETTINGS.member_dial_in_e164
    lv = live_view.CTX
    lv.store, lv.orch, lv.hosts = S.store, S.orch, tuple(SETTINGS.public_hosts)
    b = accounts_browser.CTX
    b.auth_id, b.auth_token = SETTINGS.plivo_auth_id, SETTINGS.plivo_auth_token
    b.endpoint, b.app_id, b.cap = SETTINGS.plivo_web_endpoint, SETTINGS.plivo_app_id, SETTINGS.max_concurrent_calls
    cm = accounts_callme.CTX
    # Call me rings from the number the console shows its users
    cm.host, cm.caller_id = SETTINGS.public_host, SETTINGS.member_dial_in_e164 or SETTINGS.dial_in_e164
    cm.max_call_s = SETTINGS.max_call_s
    # Agent settings published from the admin console: prices and limits for the next admission or charge
    # (PRICING), the longest call for the call desk and Call me; the talker reads its own at each call's start.
    S.agent = AgentConfig(S.store.pool, S.bus.r)

    def _applied(settings: Dict[str, Any]) -> None:
        S.calls.max_call_s = cm.max_call_s = int(settings["max_call_minutes"]) * 60
    S.agent.on_apply = _applied
    await S.agent.refresh()
    w.secret = SETTINGS.otp_secret or SETTINGS.internal_key
    adm = admin_api.CTX
    adm.pool, adm.bus, adm.hosts, adm.secure_cookie = S.store.pool, S.bus, SETTINGS.admin_hosts, SETTINGS.cookie_secure
    adm.agent = S.agent
    adm.second_factor = SETTINGS.admin_second_factor
    # the admin's test calls.
    adm.tests = TestCalls(pool=S.store.pool, bus=S.bus, calls=S.calls, sarvam_key=SETTINGS.sarvam_api_key,
                          stream_url=SETTINGS.talker_stream_url, stream_key=SETTINGS.stream_key,
                          caller=SETTINGS.dial_in_e164, cap=SETTINGS.max_concurrent_calls,
                          audit=admin_api.audit_entry)
    w.test_numbers, w.secure_cookie = frozenset(SETTINGS.otp_test_numbers), SETTINGS.cookie_secure
    # files a caller adds for their long documents: scans and photos are read by Sarvam's reader
    from .materials import Materials
    from .vision import SarvamDocs
    S.materials = Materials(S.store.pool, SarvamDocs(SETTINGS.sarvam_api_key) if SETTINGS.sarvam_api_key else None)
    accounts_files.CTX.materials, accounts_files.CTX.orch = S.materials, S.orch
    if S.bg.big is not None:
        S.bg.big.materials = S.materials
    if S.bus.enabled:
        S.store.on_durable_events = S.bus.publish_events
        S.bg.bus = S.bus
        S.bg.sms_daily_limit = SETTINGS.sms_daily_limit
    S.flows = None
    if SETTINGS.temporal_address:
        try:
            from .flows import TemporalFlows

            S.flows = await TemporalFlows.connect(SETTINGS.temporal_address, SETTINGS.temporal_namespace)
            S.flows.start_worker(S.bg)
        except Exception as e:  # keep serving calls: in-process loops do the same work, minus durability
            logger.warning(f"Temporal unavailable ({type(e).__name__}); running tasks in-process")
            S.flows = None
    S.bg.start(SETTINGS.thinker_workers, S.flows)
    logger.info(f"control plane up: {n} recent sessions loaded, {len(requeued)} task(s) requeued, "
                f"sms={'on' if sms else 'off'}, redis={'on' if await S.bus.ping() else 'off'}, mode={S.bg.mode}, "
                f"thinker={thinker.label}")
    yield
    await S.bg.stop()
    if S.flows is not None:
        await S.flows.stop()
    if sms:
        await sms.aclose()
    if accounts_browser.CTX.http is not None:
        await accounts_browser.CTX.http.aclose()
        accounts_browser.CTX.http = None
    await S.bus.close()
    await S.store.close()


app = FastAPI(title="boldoc-control", lifespan=lifespan)
api_errors.install(app)
app.include_router(accounts_web.router)
app.include_router(accounts_browser.router)
app.include_router(live_view.router)
app.include_router(accounts_documents.router)
app.include_router(accounts_files.router)
app.include_router(accounts_numbers.router)
app.include_router(accounts_callme.router)
app.include_router(admin_api.router)  # the admin console: only on its own hosts (ADMIN_HOSTS)
app.include_router(mcp_http.router)   # Hermes' tools and its metering proxy (internal network only)


def _internal(key: Optional[str]) -> None:
    if not SETTINGS.internal_key or not key or not secrets.compare_digest(key, SETTINGS.internal_key):
        raise HTTPException(status_code=401, detail="unauthorized")


# ------------------------------------------------------------------------------------------------ internal
class AdmitCall(BaseModel):
    call_uuid: str
    from_: str = Field("", alias="from")
    to: str = ""
    resumed: bool = False
    session_token: Optional[str] = None  # a browser call's X-PH-Session header (the website's one-time session)
    call_me: Optional[str] = None        # "Call me": the one-time token in the answer URL of a call we placed


async def _notify_refused(adm) -> None:
    """A caller whose free trial is over gets the sign-up link by SMS (once a day: SMS costs money)."""
    if adm.reason != "trial_over" or not adm.caller or S.bg.sms is None:
        return
    if not await S.bus.allow(f"trialsms:{adm.caller}", 1, 86400, fail_open=False):
        return
    text = (f"BolDoc: your free calls are used. Sign in with this number and get Rs {PRICING.signup_bonus} free: "
            f"https://{SETTINGS.public_host}/sign-in")
    try:
        await S.bg.sms.send(adm.caller, text)
    except Exception as e:
        logger.warning(f"trial-over SMS not sent: {type(e).__name__}")


@app.get("/internal/agent-config")
async def agent_config_live(x_internal_key: Optional[str] = Header(None)) -> dict:
    """The talker's settings for a call starting now (the newest published version, else today's defaults)."""
    _internal(x_internal_key)
    live = await S.agent.live()
    return talker_view(live["version"], live["settings"])


@app.get("/internal/agent-config/draft/{draft_id}")
async def agent_config_draft(draft_id: str, x_internal_key: Optional[str] = Header(None)) -> dict:
    """A draft's settings, for the admin's test call on it (30 minutes)."""
    _internal(x_internal_key)
    settings = await S.agent.draft(draft_id)
    if settings is None:
        raise HTTPException(status_code=404, detail="no such draft")
    return talker_view("draft", settings)


@app.get("/api/prices")
async def prices() -> JSONResponse:
    """What the website shows: the live prices and the free trial's terms (they change with a publish)."""
    p = PRICING
    return JSONResponse({"minute": p.minute, "document": p.doc_create, "edit": p.doc_edit, "big_document":
                         p.doc_create_big, "signup_bonus": p.signup_bonus,
                         "trial": {"calls": p.trial_calls, "minutes": p.trial_max_call_s // 60,
                                   "docs_per_call": p.trial_docs_per_call, "edits_per_doc": p.trial_edits_per_doc}},
                        headers={"Cache-Control": "public, max-age=60"})


@app.post("/internal/calls/admit")
async def admit_call(body: AdmitCall, x_internal_key: Optional[str] = Header(None)) -> dict:
    _internal(x_internal_key)
    adm = await S.calls.admit(body.call_uuid, body.from_, body.to, resumed=body.resumed,
                              session_token=body.session_token, call_me=body.call_me)
    return adm.as_dict()


@app.post("/internal/calls/{call_uuid}/hangup")
async def call_hangup(call_uuid: str, request: Request, x_internal_key: Optional[str] = Header(None)) -> dict:
    """Plivo's hangup webhook, forwarded by the talker after its signature check. Also the safety net for a talker
    that died mid-call: the session is ended here if it never was, so the "updated" SMS still goes out."""
    _internal(x_internal_key)
    p = await request.json()
    await S.calls.hangup(call_uuid, p if isinstance(p, dict) else {})
    sess = S.orch.s.sessions.get(call_uuid)
    ended = sess is not None and sess.state.value != "offline"
    if ended:
        await _end(call_uuid)
    return {"ok": True, "session_ended": ended}


class OpenSession(BaseModel):
    session_id: str
    channel: str = "pstn"
    test: bool = False                   # a scripted test call (stream opened with the test flag and the key)
    late_admit: Optional[AdmitCall] = None  # the talker admitted this call while control was unreachable


@app.post("/internal/sessions")
async def open_session(body: OpenSession, x_internal_key: Optional[str] = Header(None)) -> dict:
    _internal(x_internal_key)
    record = await S.calls.get(body.session_id)
    if record is None and body.late_admit is not None:
        a = body.late_admit
        await S.calls.admit(a.call_uuid, a.from_, a.to, late=True)
        record = await S.calls.get(body.session_id)
    if record is None and not body.test:
        logger.warning(f"session refused call={body.session_id[-8:]}: no admission for this call")
        raise HTTPException(status_code=403, detail="call not admitted")
    if record is not None and (record["decision"] != "admit" or record["ended_at"] is not None):
        raise HTTPException(status_code=403, detail="call not admitted")
    caller = record["caller"] if record else None  # an unadmitted test call is anonymous: no number, no SMS
    meta = (json.loads(record["meta"]) if isinstance(record["meta"], str) else record["meta"]) if record else {}
    plan = meta.get("plan") or ("test" if record is None or record["test"] else None)
    sess = S.orch.open_session(body.session_id, record["channel"] if record else "test", caller,
                               user_id=record["user_id"] if record else None, plan=plan)
    callback = meta.get("callback") if meta.get("via") == "callback" else None
    if callback and callback.get("doc_id") and (callback["doc_id"] in S.orch.s.docs
                                                 or await S.store.load_doc(callback["doc_id"])):
        S.orch.attach(body.session_id, callback["doc_id"])  # we rang them about this document's questions
    await _attach_waiting(sess)
    mobile = indian_mobile(caller)
    if mobile and mobile not in SETTINGS.otp_test_numbers:
        # SMS goes to the number that called. A spoofed caller ID can't receive it, so this is safe to send.
        # Test numbers (OTP_TEST_NUMBERS) never get one: such a number may well belong to a stranger.
        S.orch.verify_recipient(body.session_id, mobile)
    await S.store.flush()
    if record is not None:
        await S.calls.stream_started(body.session_id)
    admission = {"max_call_s": int(record["max_call_s"]) if record else SETTINGS.max_call_s,
                 "first_call": bool(record["first_call"]) if record else False,
                 "test": bool(record["test"]) if record else True, "caller": caller, "plan": plan,
                 "llm": meta.get("llm") or "primary",
                 "limits": {"docs_per_call": PRICING.trial_docs_per_call, "edits_per_doc": PRICING.trial_edits_per_doc,
                            "signup_bonus": PRICING.signup_bonus}
                 if plan == "trial" else None,
                 "name": await _saved_name(sess.user_id),  # "My details": the welcome greets them by name
                 # a callback about a long document's questions: the talker opens with them
                 "via": meta.get("via"), "callback": {"title": callback.get("title"), "n": callback.get("n")}
                 if callback else None}
    recent = await S.store.recent_docs(sess.user_id, caller, body.session_id)
    # files they added on the website for their next document: BolDoc mentions them
    files = await S.materials.staged(sess.user_id) if S.materials is not None and sess.user_id else []
    return {"epoch": sess.epoch, "snapshot": S.orch.snapshot(body.session_id), "admission": admission,
            "recent_docs": recent, "files": files}


async def _saved_name(user_id: Optional[int]) -> Optional[str]:
    if not user_id:
        return None
    async with S.store.pool.acquire() as con:
        return await con.fetchval("select nullif(trim(name), '') from profiles where user_id=$1", user_id)


class CallerName(BaseModel):
    name: str = Field(..., min_length=2, max_length=60)


class IntakeBody(BaseModel):
    transcript: str = Field(..., max_length=20000)
    written: List[str] = Field(default_factory=list, max_length=20)  # the call's documents so far (titles)
    earlier: List[str] = Field(default_factory=list, max_length=20)  # the caller's documents from earlier calls


@app.post("/internal/sessions/{session_id}/intake")
async def intake(session_id: str, body: IntakeBody, x_internal_key: Optional[str] = Header(None)) -> dict:
    """What the document the caller is asking for needs, and whether BolDoc may write it (apps/thinker/intake.py):
    the talker asks for every missing fact before writing and reads back only what was said. Kept for the call, so
    a request that isn't allowed is refused here too, not only by the talker."""
    _internal(x_internal_key)
    sess = S.orch.s.sessions.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="unknown session")
    known = await S.store.known_facts(sess.user_id) if sess.user_id else {}
    # whether the request makes sense as said, asked alongside (its own small call: apps/thinker/intake.py SENSE)
    sense_task = asyncio.create_task(thinker_intake.sense_check(S.bg.key, body.transcript))
    # and whether it is a long document (Hermes writes it after the call): only when long documents are on
    scope_task = asyncio.create_task(thinker_scope.scope_check(S.bg.key, body.transcript)) if S.orch.big_docs \
        else None
    try:
        it, stats = await thinker_intake.prepare(S.bg.key, body.transcript, known,
                                                 written=[str(t)[:120] for t in body.written],
                                                 earlier=[str(t)[:120] for t in body.earlier])
    except ThinkerError as e:
        logger.warning(f"intake for …{session_id[-8:]} failed: {e.code}")
        # a refusal stands; a checklist from an earlier turn would be stale
        prev = S.intakes.get(session_id)
        if not (prev and prev.get("allowed") is False):
            S.intakes.pop(session_id, None)
            prev = None
        sense_task.cancel()
        if scope_task is not None:
            scope_task.cancel()
        return {"intake": prev, "error": e.code}
    S.intakes[session_id] = it.as_dict()
    try:
        sense, _ = await asyncio.wait_for(sense_task, 3.0)  # it usually finished long ago (p50 0.6 s)
    except Exception as e:  # no verdict is "ok": never block writing on a failed check
        logger.warning(f"sense check for …{session_id[-8:]} failed: {type(e).__name__}")
        sense = {"to": "", "verdict": "ok", "ask": ""}
    scope = await _scope(session_id, sess, scope_task)
    return {"intake": S.intakes[session_id], "sense": sense, "scope": scope, "ms": stats.get("latency_ms")}


async def _scope(session_id: str, sess, scope_task) -> Optional[dict]:
    """A long document's scope for the talker: its type, the outline to read back, how long it takes and costs, and
    whether the caller must sign up first."""
    if scope_task is None:
        return None
    try:
        sc, _ = await asyncio.wait_for(scope_task, 3.0)
    except Exception as e:  # no verdict: today's path
        logger.warning(f"scope check for …{session_id[-8:]} failed: {type(e).__name__}")
        return S.scopes.get(session_id)
    if sc.get("size") != "long":
        S.scopes.pop(session_id, None)
        return None
    outline, size, unit = thinker_catalogue.outline_for(sc.get("kind"), sc.get("sections"), sc.get("length"))
    k = thinker_catalogue.kind(sc.get("kind"))
    lo_s, hi_s = await eta.estimate(getattr(S.store, "pool", None), unit)  # from the last ones written
    lo_m, hi_m = eta.minutes(lo_s, hi_s)
    S.scopes[session_id] = {**sc, "outline": outline, "size_n": size, "unit": unit,
                            "name": k.name if k else (sc.get("title") or "long document"),
                            "formats": list(k.formats) if k else ["pdf", "docx"],
                            "doc_type": k.doc_type if k else "report",
                            "asks": [{"what": w, "question": q} for w, q in (k.asks if k else ())],
                            "research": k.research if k else True,  # this type is written with web research
                            "price": PRICING.price_for("doc_create_big"), "minutes": hi_m, "minutes_lo": lo_m,
                            "signup_needed": sess.plan in ("trial", "anonymous")}
    return S.scopes[session_id]


@app.post("/internal/sessions/{session_id}/caller-name")
async def caller_name(session_id: str, body: CallerName, x_internal_key: Optional[str] = Header(None)) -> dict:
    """The caller said their name on the call: kept for the next call's welcome. A name they saved in "My details"
    is never overwritten."""
    _internal(x_internal_key)
    sess = S.orch.s.sessions.get(session_id)
    if sess is None or not sess.user_id:
        return {"saved": False}
    async with S.store.pool.acquire() as con:
        status = await con.execute(
            "insert into profiles (user_id, name) values ($1, $2) on conflict (user_id) do update set name = "
            "excluded.name where profiles.name is null or trim(profiles.name) = ''", sess.user_id, body.name.strip())
    return {"saved": status.endswith("1")}


@app.post("/internal/commands")
async def command(request: Request, x_internal_key: Optional[str] = Header(None)) -> dict:
    _internal(x_internal_key)
    try:
        cmd = _command.validate_python(await request.json())
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_url=False)[:5]) from e
    limits = {"create_doc": SETTINGS.max_docs_per_call, "edit_doc": SETTINGS.max_edits_per_call}
    # A retried command (same idempotency key) gets its first answer and never counts against a limit twice.
    await _attach_if_owned(cmd)
    if cmd.cmd == "create_doc":
        cmd = await _with_brief(cmd)
    refused = await _plan_refusal(cmd)
    if not refused and cmd.cmd == "create_doc" and (S.intakes.get(cmd.session_id) or {}).get("allowed") is False:
        refused = "not_allowed"  # the intake found a forged / impersonating / harmful request
    if refused:
        result = S.orch.reject(cmd, refused)
        if refused == "signup_needed":
            await _send_signup_link(cmd.session_id)
    elif cmd.cmd in limits and not S.orch.seen(cmd) and not await S.bus.allow(
            f"cmd:{cmd.session_id}:{cmd.cmd}", limits[cmd.cmd], 6 * 3600):
        result = S.orch.reject(cmd, "limit_reached")
    else:
        result = S.orch.handle(cmd)
    await S.store.flush()
    if result.status == "accepted":
        S.bg.task_wake.set()
        S.bg.delivery_wake.set()
    task = S.orch.s.tasks.get(result.task_id or "")
    if result.status == "accepted" and task is not None and task.tier == "T3" and getattr(cmd, "callback_ok", False):
        await _consent(cmd.session_id, "callback-v1", getattr(cmd, "callback_words", None))
    files_used: List[str] = []
    if result.status == "accepted" and task is not None and task.tier == "T3" and S.materials is not None:
        sess = S.orch.s.sessions.get(cmd.session_id)
        if sess is not None and sess.user_id:  # the files waiting for their next document go to this one
            files_used = await S.materials.attach_staged(sess.user_id, task.doc_id, task.id)
    eta_lo = eta_s = ETA_S.get(task.kind) if task else None
    if task is not None and task.tier == "T3":
        eta_lo, eta_s = await eta.estimate(getattr(S.store, "pool", None),
                                           (getattr(cmd, "brief", None) or {}).get("unit"))
    return {"status": result.status, "task_id": result.task_id, "doc_id": result.doc_id,
            "merged_into": result.merged_into, "reason": result.reason, "files_used": files_used,
            "tier": task.tier if task else None, "eta_s": eta_s, "eta_lo_s": eta_lo,
            "snapshot": S.orch.snapshot(cmd.session_id)}


async def _with_brief(cmd):
    """A long document's brief, built at the moment it is asked for: the outline the caller heard, the facts given
    and not given, their saved details and what they said. It goes with the task, because Hermes writes after the
    caller has hung up (apps/control/bigdoc.py)."""
    scope = S.scopes.get(cmd.session_id)
    sess = S.orch.s.sessions.get(cmd.session_id)
    if not S.orch.big_docs or not scope or scope.get("size") != "long" or sess is None:
        return cmd
    it = S.intakes.get(cmd.session_id) or {}
    facts = {f["label"]: f["value"] for f in it.get("facts") or [] if f.get("value")}
    not_given = [f["label"] for f in it.get("facts") or [] if not f.get("value")]
    saved = await S.store.known_facts(sess.user_id) if sess.user_id else {}
    brief = {"size": "long", "kind": scope.get("kind"), "document": scope.get("title") or scope.get("name"),
             "title": scope.get("title") or "", "doc_type": scope.get("doc_type"), "language": cmd.doc_lang,
             "formats": list(cmd.formats) or scope.get("formats"), "target_pages": scope.get("size_n"),
             "unit": scope.get("unit"), "outline": scope.get("outline"), "facts": facts, "not_given": not_given,
             "saved_details": saved, "transcript": (cmd.transcript or "")[-12000:],
             "callback_ok": bool(getattr(cmd, "callback_ok", False)),
             # web research for the types that need it, unasked, unless the caller refused it
             "research_ok": bool(scope.get("research")) and getattr(cmd, "research_ok", None) is not False}
    return cmd.model_copy(update={"brief": brief})


async def _consent(session_id: str, notice: str, words: Optional[str]) -> None:
    """Keep what the caller agreed to on the call, with their own words (a callback needs a recorded yes;
    "don't call me" is recorded the same way)."""
    sess = S.orch.s.sessions.get(session_id)
    if sess is None or not sess.caller:
        return
    subject = f"user:{sess.user_id}:{sess.caller[-4:]}" if sess.user_id else f"call:{session_id}"
    try:
        async with S.store.pool.acquire() as con:
            await con.execute("insert into consents (subject, notice_version, channel, words) values ($1, $2, 'call', "
                              "$3)", subject, notice, (words or "")[:300] or None)
    except Exception as e:  # a missing record never breaks the call; the log says so
        logger.warning(f"consent {notice} for call {session_id[-8:]} not recorded: {type(e).__name__}")


class OptOutBody(BaseModel):
    words: str = Field("", max_length=300)


@app.post("/internal/sessions/{session_id}/callbacks-off")
async def callbacks_off(session_id: str, body: OptOutBody, x_internal_key: Optional[str] = Header(None)) -> dict:
    """"Mujhe call mat karna" on a callback: no more callbacks to this number (callback.Callbacks.opt_out)."""
    _internal(x_internal_key)
    sess = S.orch.s.sessions.get(session_id)
    if sess is None or not sess.caller:
        raise HTTPException(status_code=404, detail="unknown session")
    if S.bg.big is not None and S.bg.big.callbacks is not None:
        await S.bg.big.callbacks.opt_out(sess.caller)
    await _consent(session_id, "callback-off-v1", body.words)
    logger.info(f"call {session_id[-8:]}: no more callbacks to this number")
    return {"ok": True}


async def _send_signup_link(session_id: str) -> None:
    """A trial caller asked for a long document: the sign-up link by SMS (once a day: SMS costs money)."""
    sess = S.orch.s.sessions.get(session_id)
    if sess is None or not sess.caller or S.bg.sms is None or sess.caller in SETTINGS.otp_test_numbers:
        return
    if not await S.bus.allow(f"signupsms:{sess.caller}", 1, 86400, fail_open=False):
        return
    text = (f"BolDoc: big documents need a free sign-up. Sign in with this number and get Rs "
            f"{PRICING.signup_bonus} free: https://{SETTINGS.public_host}/sign-in")
    try:
        await S.bg.sms.send(sess.caller, text)
    except Exception as e:
        logger.warning(f"sign-up SMS not sent: {type(e).__name__}")


async def _owns(sess, doc_id: str) -> bool:
    """Is this document the caller's: same account, or created from the same number before accounts existed?"""
    if not sess.user_id and not sess.caller:
        return False
    owner = await S.store.doc_owner(doc_id)
    if owner is None:
        return False
    return (sess.user_id is not None and sess.user_id in (owner["owner_user_id"], owner["call_user"],
                                                          owner["owner_linked_to"])) or (
        owner["owner_user_id"] is None and sess.caller is not None and owner["owner"] == sess.caller)


async def _attach_if_owned(cmd) -> None:
    """A command about a document from one of the caller's earlier calls: open it on this call, if it is theirs.
    Otherwise the orchestrator refuses."""
    doc_id = getattr(cmd, "doc_id", None)
    sess = S.orch.s.sessions.get(cmd.session_id)
    if not doc_id or sess is None:
        return
    doc = S.orch.s.docs.get(doc_id)
    if doc is not None and S.orch.usable(doc, cmd.session_id):
        return
    if await _owns(sess, doc_id) and await S.store.load_doc(doc_id):
        S.orch.attach(cmd.session_id, doc_id)
        logger.info(f"call {cmd.session_id[-8:]} opened earlier document {doc_id[-6:]}")


async def _attach_waiting(sess) -> None:
    """Their long documents whose writer is waiting for answers: open them on this call, so the
    questions are asked here, whichever call it is (a callback, or one they made)."""
    for t in list(S.orch.s.tasks.values()):
        if t.tier != "T3" or not (t.big or {}).get("waiting") or t.doc_id not in S.orch.s.docs:
            continue
        if not S.orch.usable(S.orch.s.docs[t.doc_id], sess.id) and await _owns(sess, t.doc_id):
            S.orch.attach(sess.id, t.doc_id)
            logger.info(f"call {sess.id[-8:]} can answer the questions for document {t.doc_id[-6:]}")


async def _plan_refusal(cmd) -> Optional[str]:
    """Why this call's plan doesn't allow this document command (None: it does). Fails closed: if the balance
    can't be read, the request errors instead of writing for free."""
    if cmd.cmd not in ("create_doc", "edit_doc") or S.orch.seen(cmd):
        return None
    sess = S.orch.s.sessions.get(cmd.session_id)
    if sess is None:
        return None
    if sess.plan in ("trial", "anonymous"):
        if cmd.cmd == "create_doc" and getattr(cmd, "brief", {}).get("size") == "long":
            return "signup_needed"  # big documents are for signed-up callers
        if cmd.cmd == "create_doc":
            made = sum(1 for d in S.orch.s.docs.values() if d.session_id == cmd.session_id)
            return "trial_doc_limit" if made >= PRICING.trial_docs_per_call else None
        # every edit the caller asked for, whether it became a task or joined one already running
        edits = sum(1 for ev in S.orch.s.events.get(cmd.session_id, []) if ev.type == "task.accepted"
                    and ev.doc_id == cmd.doc_id and ev.payload.get("via") in ("inbox", "coalesced", "queued"))
        return "trial_edit_limit" if edits >= PRICING.trial_edits_per_doc else None
    if sess.plan == "credits" and sess.user_id:
        async with S.store.pool.acquire() as con:
            if await ledger.balance(con, sess.user_id) <= 0:
                return "no_credits"
    return None


@app.get("/internal/sessions/{session_id}/events")
async def events(session_id: str, after: int = 0, wait: float = 20.0,
                 x_internal_key: Optional[str] = Header(None)) -> dict:
    _internal(x_internal_key)
    if session_id not in S.orch.s.sessions:
        raise HTTPException(status_code=404, detail="unknown session")
    evs = await S.store.wait_events(session_id, after, min(max(wait, 0.0), 25.0))
    return {"events": [e.model_dump() for e in evs], "snapshot": S.orch.snapshot(session_id)}


class SessionEnd(BaseModel):
    lang: Optional[str] = None  # the language the call ended in: the caller's next call starts in it


@app.post("/internal/sessions/{session_id}/end")
async def end_session(session_id: str, body: Optional[SessionEnd] = None,
                      x_internal_key: Optional[str] = Header(None)) -> dict:
    _internal(x_internal_key)
    sess = S.orch.s.sessions.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="unknown session")
    if body is not None and body.lang in CALL_LANGS and sess.user_id:
        # remembered for the account, so phone, "Call me" and the browser all start in it
        async with S.store.pool.acquire() as con:
            await con.execute("update users set call_lang=$2 where id=$1", sess.user_id, body.lang)
    return {"updates": await _end(session_id)}


async def _end(session_id: str) -> int:
    """The call ended (talker said so, or Plivo's hangup did): work continues; the "updated" SMS goes out."""
    S.orch.go_offline(session_id)
    S.intakes.pop(session_id, None)
    updates = S.orch.finalize_updates(session_id)
    await S.store.flush()
    S.bg.wake()
    if S.flows is not None:
        await S.flows.signal(f"updates-{session_id}", "call_ended")
    await S.bus.clear_active(session_id)
    return len(updates)


@app.post("/internal/sessions/{session_id}/trace")
async def store_trace(session_id: str, request: Request, x_internal_key: Optional[str] = Header(None)) -> dict:
    """The talker's per-call timeline (see apps/talker/trace.py); the call report is built from it."""
    _internal(x_internal_key)
    trace = await request.json()
    if not isinstance(trace, dict) or not isinstance(trace.get("events"), list):
        raise HTTPException(status_code=422, detail="expected {call_id, started_wall, events: [...]}")
    await S.store.store_trace(session_id, trace)
    try:
        report = await S.calls.store_report(session_id, trace)
        s = report["summary"]
        logger.info(f"call report call={session_id[-8:]} turns={s['caller_turns']} p50={s['v2v_p50_s']} "
                    f"p90={s['v2v_p90_s']} ttfw={s.get('ttfw_s')} failed_checks="
                    f"{[c['check'] for c in report['checks'] if not c['ok']]}")
    except Exception as e:  # the raw trace is stored either way; the report can be rebuilt from it
        logger.warning(f"call report not built: {type(e).__name__}: {e}")
    return {"stored": len(trace["events"])}


# -------------------------------------------------------------------------------------------------- public
@app.get("/api/config")
async def config() -> dict:
    n = phonenumbers.parse(SETTINGS.dial_in_e164, None)
    return {"product": "BolDoc", "dial_in_e164": SETTINGS.dial_in_e164, "mode": "live",
            "dial_in_display": phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.INTERNATIONAL)}


def _mask(recipient: str) -> str:
    return "••••• " + recipient[-4:] if len(recipient) >= 4 else "•••••"


_NO_STORE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}


async def _link_and_view(token: str):
    link = await S.store.resolve_link(token)
    view = await S.store.document_view(link["doc_id"]) if link else None
    return (link, view) if view is not None else (None, None)


_LINK_GONE = {"message": "This link has expired or doesn't exist.", "code": "link_not_found"}


def _link_closed(state: str, view: dict) -> JSONResponse:
    """Not on this browser: never the document, at most the last digits of the number it went to."""
    if state == "claim":
        return JSONResponse({"message": "Open it on this phone or computer: the link then works only here.",
                             "code": "link_unclaimed"}, status_code=403, headers=_NO_STORE)
    d = view["delivery"]
    return JSONResponse({"message": "This link is already open on another phone or computer. To open it here, sign "
                                    "in with the number it was sent to.", "code": "link_locked",
                         "sent_to": _mask(d["recipient"]) if d else None}, status_code=403, headers=_NO_STORE)


@app.get("/api/d/{token}")
async def public_document(token: str, request: Request) -> JSONResponse:
    link, view = await _link_and_view(token)
    if view is None:
        return JSONResponse(_LINK_GONE, status_code=404, headers=_NO_STORE)
    state = await link_access.access(request, link)
    if state != "open":
        return _link_closed(state, view)
    return JSONResponse(_public_view(token, link, view), headers=_NO_STORE)


@app.post("/api/d/{token}/open")
async def open_link(token: str, request: Request) -> JSONResponse:
    """The first tap on a link: this browser keeps it (links.py). A tap, not the page load, so a link preview or a
    scanner that runs the page never takes it."""
    if request.headers.get("X-BolDoc-Client") != "web":
        raise api_errors.ApiError(403, "forbidden", "This request must come from the BolDoc website.")
    link, view = await _link_and_view(token)
    if view is None:
        return JSONResponse(_LINK_GONE, status_code=404, headers=_NO_STORE)
    state = await link_access.access(request, link)
    cookie = None
    if state == "claim":
        device = link_access.device_of(request)
        if device is None:
            cookie, device = link_access.new_device()
        state = "open" if await S.store.claim_link(token, device) else "locked"
    if state != "open":
        return _link_closed(state, view)
    resp = JSONResponse(_public_view(token, link, view), headers=_NO_STORE)
    if cookie:
        link_access.set_device(resp, cookie)
    return resp


def _public_view(token: str, link: dict, view: dict) -> dict:
    d = view["delivery"]
    delivery = None
    if d:
        delivery = {"channel": d["channel"], "recipient_masked": _mask(d["recipient"]),
                    "status": d["status"] if d["status"] in ("delivered", "failed") else "submitted",
                    "version": d["version"], "at": view["updated_at"]}
    return {"doc": view["doc"], "versions": view["versions"], "formats": view["formats"], "delivery": delivery,
            "link": f"https://{SETTINGS.public_host}/d/{token}", "expires_at": link["expires_at"].isoformat()}


@app.get("/api/d/{token}/download/{fmt}")
async def download(token: str, fmt: str, request: Request, v: Optional[int] = None) -> Response:
    link = await S.store.resolve_link(token)
    if link is None:
        raise HTTPException(status_code=404, detail="This link has expired or doesn't exist.")
    if await link_access.access(request, link) != "open":
        raise HTTPException(status_code=403, detail="This link opens only on the phone or computer that opened it "
                                                    "first.")
    f = await S.store.rendition(link["doc_id"], fmt.lower(), v)
    if f is None:
        raise HTTPException(status_code=404, detail="That format isn't available for this document.")
    view = await S.store.document_view(link["doc_id"])
    title, version = view["doc"]["title"], v or view["doc"]["version"]
    return Response(content=bytes(f["data"]), media_type=f["mime"], headers={
        **_NO_STORE, "Content-Disposition": content_disposition(title, fmt.lower(), version),
        "ETag": f'"{f["sha256"][:32]}"', "X-Content-Type-Options": "nosniff"})


@app.get("/api/d/{token}/preview/{fmt}")
async def preview_link(token: str, fmt: str, request: Request, v: Optional[int] = None) -> Response:
    """A format as it looks when downloaded (apps/control/preview.py), for the link page."""
    link = await S.store.resolve_link(token)
    if link is None:
        raise HTTPException(status_code=404, detail="This link has expired or doesn't exist.")
    if await link_access.access(request, link) != "open":
        raise HTTPException(status_code=403, detail="This link opens only on the phone or computer that opened it "
                                                    "first.")
    p = await doc_preview.preview(S.store, link["doc_id"], fmt, v)
    if p is None:
        raise HTTPException(status_code=404, detail="There's no preview of that format; it can still be downloaded.")
    return doc_preview.response(p, fmt.lower(), _NO_STORE)


# The owner's documents, from the console --------------------------------------------------------------------
@app.post("/api/documents/{doc_id}/link")
async def new_link(doc_id: str, request: Request) -> JSONResponse:
    """Stop the document's links and get a new one (it opens on the first device that taps it, links.py)."""
    if request.headers.get("X-BolDoc-Client") != "web":
        raise api_errors.ApiError(403, "forbidden", "This request must come from the BolDoc website.")
    await link_access.owner_or_raise(request, doc_id)
    stopped = await S.store.stop_links(doc_id)
    S.bg.links.pop(doc_id, None)  # the next SMS for it carries the new link, never a stopped one
    url = S.bg.link_for(doc_id)
    await S.store.flush()
    logger.info(f"document {doc_id[-6:]}: {stopped} link(s) stopped, new link made")
    return JSONResponse({"link": url, "stopped": stopped}, headers=_NO_STORE)


@app.post("/api/documents/{doc_id}/send")
async def send_to_phone(doc_id: str, request: Request) -> JSONResponse:
    """"Send to my phone": the link by SMS to the number the account signs in with (a browser call sends no SMS
    by itself)."""
    if request.headers.get("X-BolDoc-Client") != "web":
        raise api_errors.ApiError(403, "forbidden", "This request must come from the BolDoc website.")
    user = await link_access.owner_or_raise(request, doc_id)
    phone = indian_mobile(user["phone_e164"])
    if phone is None or phone in SETTINGS.otp_test_numbers:
        raise api_errors.ApiError(409, "no_mobile", "We can only send SMS to an Indian mobile number.")
    if not await S.bus.allow(f"send:u:{user['id']}", 10, 86400, fail_open=False):
        raise api_errors.ApiError(429, "too_many_requests", "That's enough SMS for today. Try again tomorrow.")
    if not await S.store.load_doc(doc_id):
        raise api_errors.ApiError(404, "not_found", "This document doesn't exist or isn't yours.")
    res = S.orch.send_on_request(doc_id, phone)
    if res.status == "rejected":
        why = {"resend_limit": "It's been sent to your phone several times already. Open it from your documents.",
               "not_ready": "It isn't ready yet. Try again in a few seconds."}
        raise api_errors.ApiError(409, res.reason or "rejected", why.get(res.reason or "", "It can't be sent now."))
    await S.store.flush()
    S.bg.delivery_wake.set()
    return JSONResponse({"status": res.status, "sent_to": _mask(phone)}, headers=_NO_STORE)


async def _form(request: Request) -> dict:
    params = dict(request.query_params)
    if request.method == "POST":
        params.update(dict(parse_qsl((await request.body()).decode(), keep_blank_values=True)))
    return params


def _signature_ok(request: Request, params: dict) -> bool:
    """Plivo signs messaging callbacks with V2: HMAC-SHA256 over URL + nonce (X-Plivo-Signature-V2[-Nonce])."""
    mode = SETTINGS.signature_mode
    if mode == "off":
        return True
    from plivo.utils import validate_signature

    sig = request.headers.get("X-Plivo-Signature-V2")
    nonce = request.headers.get("X-Plivo-Signature-V2-Nonce")
    host = request.headers.get("host", "").split(":")[0]  # the URL Plivo called (and signed), if it is ours
    host = host if host in SETTINGS.public_hosts else SETTINGS.public_host
    url = f"https://{host}{request.url.path}" + (f"?{request.url.query}" if request.url.query else "")
    ok = False
    if sig and nonce and SETTINGS.plivo_auth_token:
        try:
            ok = bool(validate_signature(url, nonce, sig, SETTINGS.plivo_auth_token))
        except Exception as e:
            logger.warning(f"sms-status signature check error: {type(e).__name__}")
    logger.info(f"sms-status signature {'ok' if ok else 'MISMATCH'} mode={mode} "
                f"headers={'present' if sig and nonce else 'missing'}")
    return ok or mode == "log"


@app.api_route("/plivo/sms-status", methods=["GET", "POST"])
async def sms_status(request: Request) -> Response:
    p = await _form(request)
    if not _signature_ok(request, p):
        return Response("forbidden", status_code=403)
    try:
        await S.calls.store_sms_receipt(p)  # cost and operator per message, for the admin console
    except Exception as e:
        logger.warning(f"sms receipt row not stored: {type(e).__name__}")
    changed = S.bg.on_receipt(p.get("MessageUUID", ""), p.get("Status", ""), p.get("ErrorCode") or None)
    if changed:
        await S.store.flush()
        d = next((x for x in S.orch.s.deliveries.values() if x.provider_ref == p.get("MessageUUID")), None)
        if d is not None and S.flows is not None and d.status in ("delivered", "failed"):
            await S.flows.signal(f"dlv-{d.id}", "receipt_received", d.status)
    logger.info(f"sms receipt {p.get('Status')} err={p.get('ErrorCode')} changed={changed}")
    return Response("ok")


WEB_EVENTS = {"visit", "cta_tel", "cta_browser", "cta_qr", "demo_play", "link_open", "download", "signin_start",
              "signin_done", "share_whatsapp", "start_open", "cta_callme"}


@app.post("/api/t")
async def track(request: Request) -> Response:
    """Funnel counters from the website: a kind and nothing else (no cookies, no identifiers, no IP stored)."""
    try:
        kind = str((await request.json()).get("k", ""))
    except Exception:
        kind = ""
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "").split(",")[0].strip()
    if kind in WEB_EVENTS and await S.bus.allow(f"t:{ip}", 120, 3600):
        async with S.store.pool.acquire() as con:
            await con.execute("insert into web_events (day, kind, n) values (current_date, $1, 1) on conflict "
                              "(day, kind) do update set n = web_events.n + 1", kind)
    return Response(status_code=204)


@app.get("/control/healthz")
async def healthz() -> dict:
    s = S.orch.s
    return {"ok": True, "sessions": len(s.sessions), "docs": len(s.docs), "mode": S.bg.mode,
            "redis": await S.bus.ping(), "queued": sum(1 for t in s.tasks.values() if t.state.value == "queued")}
