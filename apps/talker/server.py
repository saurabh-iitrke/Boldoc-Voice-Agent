"""Talker HTTP/WebSocket server: Plivo answer webhook -> admission -> bidirectional audio stream -> Pipecat bot.

Every call is admitted by the control plane on the (signed) answer webhook before audio flows; a refused caller
hears a short clip in BolDoc's voice. The stream that follows carries no identity of its own: the session is
opened by CallUUID and control supplies the caller from its call record.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import sys
from typing import Optional, Tuple
from urllib.parse import parse_qsl
from xml.sax.saxutils import escape

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import PlainTextResponse, Response
from loguru import logger

from . import agent_config, eou
from .bot import CallInfo, run_bot
from .plivo_stream import parse_extra_headers, ph_header, remember_admission, take_admission
from .settings import SETTINGS
from .voices import voice_map

# Pipecat's DEBUG logs include whole prompts and transcripts: off unless LOG_LEVEL=DEBUG is set deliberately.
logger.remove()
logger.add(sys.stderr, level=os.environ.get("LOG_LEVEL", "INFO"))



@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(eou.load)  # before the first call: loading takes a second or two
    yield


app = FastAPI(title="boldoc-talker", lifespan=lifespan)
ADMIT_TIMEOUT_S = 2.0      # Plivo waits for the answer XML; never keep a caller ringing on a slow control plane
DEGRADED_MAX_CALL_S = 600  # admitted without control (outage): a shorter call, recorded late
CDR_FIELDS = ("From", "To", "Duration", "BillDuration", "TotalCost", "BillRate", "HangupCause", "HangupCauseName",
              "HangupCauseCode", "HangupSource", "StartTime", "AnswerTime", "EndTime", "CallStatus", "Direction")
_control: Optional[httpx.AsyncClient] = None


def control() -> httpx.AsyncClient:
    global _control
    if _control is None:
        _control = httpx.AsyncClient(base_url=SETTINGS.control_url, headers={"X-Internal-Key": SETTINGS.internal_key},
                                     timeout=httpx.Timeout(5.0))
    return _control


async def _plivo_params(request: Request) -> dict:
    params = dict(request.query_params)
    if request.method == "POST":
        params.update(dict(parse_qsl((await request.body()).decode(), keep_blank_values=True)))
    return params


def _called_url(request: Request) -> str:
    """The URL Plivo called (and signed): our public host as Caddy received it, if it is one of ours."""
    host = request.headers.get("host", "").split(":")[0]
    host = host if host in SETTINGS.public_hosts else SETTINGS.public_host
    return f"https://{host}{request.url.path}" + (f"?{request.url.query}" if request.url.query else "")


def _signature_ok(request: Request, params: dict) -> bool:
    """Plivo V3 webhook signature check. In `log` mode the result is recorded but never blocks (shadow rollout)."""
    mode = SETTINGS.signature_mode
    if mode == "off":
        return True
    from plivo.utils.signature_v3 import validate_v3_signature

    sig = request.headers.get("X-Plivo-Signature-V3")
    nonce = request.headers.get("X-Plivo-Signature-V3-Nonce")
    url = _called_url(request)
    post_params = {} if request.method == "GET" else {k: v for k, v in params.items() if k not in request.query_params}
    ok = False
    if sig and nonce and SETTINGS.plivo_auth_token:
        try:
            ok = bool(validate_v3_signature(request.method, url, nonce, SETTINGS.plivo_auth_token, sig, post_params))
        except Exception as e:
            logger.warning(f"signature check error: {type(e).__name__}: {e}")
    if not ok:
        logger.warning(f"signature MISMATCH mode={mode} method={request.method} url={url} "
                       f"headers={'present' if sig and nonce else 'missing'} params={sorted(post_params)}")
    return ok or mode == "log"


def stream_xml(host: str, max_call_s: int, resumed: bool, noise_cancellation: Optional[Tuple[bool, int]] = None) -> str:
    ws = f"wss://{host}/plivo/stream?k={SETTINGS.stream_key}"
    on, level = noise_cancellation or (SETTINGS.noise_cancellation, SETTINGS.noise_cancellation_level)
    noise = f'noiseCancellation="true" noiseCancellationLevel="{level}" ' if on else ""
    return (
        "<Response>"
        f'<Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000" {noise}'
        # the talker ends the call itself at the limit (after a goodbye); this is the backstop
        f'streamTimeout="{max_call_s + 60}" statusCallbackUrl="https://{host}/plivo/stream-status" '
        f'extraHeaders="resumed={1 if resumed else 0}">{escape(ws)}</Stream>'
        # If the stream drops (worker crash, network), Plivo moves on to this Redirect and we start a new stream.
        f"<Redirect>https://{host}/plivo/answer?resume=1</Redirect>"
        "</Response>"
    )


def refusal_xml(host: str, clip: str) -> str:
    return f"<Response><Play>https://{host}/clips/{escape(clip)}.wav</Play><Hangup/></Response>"


async def admit(body: dict) -> dict:
    """Ask control to admit the call. If control can't answer in time, admit a phone call anyway (shorter, and
    recorded late when the stream opens): an outage of ours must not become a caller's dead line."""
    try:
        r = await control().post("/internal/calls/admit", json=body, timeout=ADMIT_TIMEOUT_S)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"admission unavailable ({type(e).__name__}): admitting call={body['call_uuid'][-8:]} degraded")
        if str(body.get("from", "")).lower().startswith("sip:") or body.get("call_me"):
            # who is calling is known only to control (a browser session, or a call we placed)
            return {"decision": "refuse", "reason": "control_unavailable", "clip": "refused"}
        return {"decision": "admit", "max_call_s": min(SETTINGS.max_call_s, DEGRADED_MAX_CALL_S), "first_call": False,
                "degraded": True}


@app.api_route("/plivo/answer", methods=["GET", "POST"])
async def answer(request: Request) -> Response:
    params = await _plivo_params(request)
    if not _signature_ok(request, params):
        logger.warning("answer: invalid Plivo signature")
        return PlainTextResponse("forbidden", status_code=403)
    call_uuid = params.get("CallUUID", "")
    resumed = request.query_params.get("resume") == "1"
    body = {"call_uuid": call_uuid, "from": params.get("From", ""), "to": params.get("To", ""), "resumed": resumed}
    if request.query_params.get("callme"):  # "Call me": a call control placed to one of the account's numbers
        body["call_me"] = request.query_params["callme"]
    token = ph_header(params, "session")
    if token:
        body["session_token"] = token
    if str(params.get("From", "")).startswith("sip:"):  # a browser call: did its token come, and which one
        heads = sorted(k for k in params if str(k).lower().startswith("x-ph-"))
        fp = hashlib.sha256(token.encode()).hexdigest()[:8] if token else "-"
        logger.info(f"answer call={call_uuid[-8:]} browser headers={heads} fp={fp}")
    adm = await admit(body)
    host = request.headers.get("host", "").split(":")[0]
    host = host if host in SETTINGS.public_hosts else SETTINGS.public_host
    if adm.get("decision") != "admit":
        logger.info(f"answer call={call_uuid[-8:]} refused ({adm.get('reason')})")
        return Response(content=refusal_xml(host, adm.get("clip") or "refused"), media_type="application/xml")
    remember_admission(call_uuid, adm, late_admit=body if adm.get("degraded") else None)
    logger.info(f"answer call={call_uuid[-8:]} admitted max={adm.get('max_call_s')}s resumed={resumed}"
                f"{' first-call' if adm.get('first_call') else ''}{' DEGRADED' if adm.get('degraded') else ''}")
    cfg = await agent_config.for_call(client=control())  # the agent's settings (admin console)
    xml = stream_xml(host, int(adm.get("max_call_s") or SETTINGS.max_call_s), resumed,
                     (cfg.noise_cancellation, cfg.noise_cancellation_level))
    return Response(content=xml, media_type="application/xml")


@app.api_route("/plivo/hangup", methods=["GET", "POST"])
async def hangup(request: Request) -> PlainTextResponse:
    """Plivo's CDR for the call: forwarded to control, which closes the call record (and the session, if the
    talker never got to)."""
    p = await _plivo_params(request)
    if not _signature_ok(request, p):
        return PlainTextResponse("forbidden", status_code=403)
    call_uuid = p.get("CallUUID", "")
    logger.info(f"hangup call={call_uuid[-8:]} cause={p.get('HangupCauseName') or p.get('HangupCause')} "
                f"duration={p.get('Duration')} billed={p.get('BillDuration')}")
    if call_uuid:
        try:
            r = await control().post(f"/internal/calls/{call_uuid}/hangup",
                                     json={k: p[k] for k in CDR_FIELDS if p.get(k) not in (None, "")})
            r.raise_for_status()
        except Exception as e:
            logger.warning(f"hangup not recorded: {type(e).__name__}")
    return PlainTextResponse("ok")


@app.api_route("/plivo/stream-status", methods=["GET", "POST"])
async def stream_status(request: Request) -> PlainTextResponse:
    p = await _plivo_params(request)
    logger.info(f"stream-status {p.get('Event') or p.get('Status')} reason={p.get('StatusReason')}")
    return PlainTextResponse("ok")


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "llm": SETTINGS.llm_model, "end_of_turn": eou.REVISION if eou.ready() else None,
            "voices": {family: f"{model}/{speaker}" for family, (model, speaker) in voice_map().items()}}


@app.websocket("/plivo/stream")
async def stream(ws: WebSocket) -> None:
    if SETTINGS.stream_key and not secrets.compare_digest(ws.query_params.get("k", ""), SETTINGS.stream_key):
        await ws.close(code=4401)
        return
    await ws.accept()
    # Plivo's first message is the `start` event (call/stream ids, media format, our extra headers).
    while True:
        msg = json.loads(await ws.receive_text())
        if msg.get("event") == "start":
            break
    start = msg.get("start", {})
    extra = parse_extra_headers(msg.get("extra_headers") or start.get("extra_headers"))
    test = ws.query_params.get("test") == "1"
    call_id = start.get("callId", "")
    answered = take_admission(call_id) or {}
    logger.info(f"stream start call={call_id[-8:]} resumed={extra.get('resumed', '0')} test={test}")
    admission = answered.get("admission") or {}
    llm = admission.get("llm") or "primary"
    if test and ws.query_params.get("llm") in ("primary", "overflow"):
        llm = ws.query_params["llm"]  # scripted test calls can exercise the overflow talker
    call = CallInfo(call_id=call_id, stream_id=start.get("streamId", "") or msg.get("streamId", ""),
                    hang_up_on_end=not test, test=test, first_call=bool(admission.get("first_call")),
                    late_admit=answered.get("late_admit"), llm=llm,
                    intake=not (test and ws.query_params.get("intake") == "off"),  # A/B on scripted calls only
                    lang=admission.get("lang") or (ws.query_params.get("lang") if test else None),
                    remembered_lang=admission.get("remembered_lang") or (
                        ws.query_params.get("remembered") if test else None))
    await run_bot(ws, call)
