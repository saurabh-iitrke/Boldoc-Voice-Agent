"""Call me: from the account, BolDoc calls one of the account's own numbers and the AI agent answers when it is picked
up.

Only the account's numbers (the sign-in number and the ones added under "Your numbers", each proven by a code): a button
that could ring any typed-in number would be a harassment tool at our cost. A call placed this way is an ordinary phone
call from that number once answered: the same plan, credits, limits and documents, the SMS link to that number. A
one-time token in the answer URL tells admission whose call it is (calls.py). The tap is the person's request for the
call (a service call they asked for), recorded in consents.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel

from ..api_errors import ApiError
from . import browser, users
from .web import CTX as WEB
from .web import _limit, _mask, _phone, _require_client_header, require_user

router = APIRouter()
RING_S = 40           # how long the phone rings
TOKEN_TTL_S = 120     # the call must be answered within this
STATUS_TTL_S = 900


class Ctx:
    """Set by the control service at startup."""
    host = "localhost"   # where Plivo fetches the answer URL (the talker, behind Caddy)
    caller_id = ""       # our number, shown on the caller's phone
    max_call_s = 1800


CTX = Ctx()


class CallMe(BaseModel):
    phone: str
    lang: Optional[str] = None  # the language picked on the page; when none, their last call's (else English)


def _token_key(token: str) -> str:
    return "boldoc:cm:" + hashlib.sha256(token.encode()).hexdigest()


def _status_key(rid: str) -> str:
    return f"boldoc:cm:st:{rid}"


async def _set_status(rid: str, user_id: int, status: str) -> None:
    await WEB.bus.r.set(_status_key(rid), json.dumps({"user_id": user_id, "status": status}), ex=STATUS_TTL_S)


async def place_call(number: str, token: str, machine_detection: bool = False) -> str:
    """Plivo's Call API: ring the number from our number; when answered, Plivo fetches the answer URL. A callback
    (apps/control/callback.py) asks Plivo to hang up on a voicemail: that is no answer, not a call."""
    b = browser.CTX
    if b.http is None:
        b.http = httpx.AsyncClient(timeout=10)
    body = {"from": CTX.caller_id.lstrip("+"), "to": number.lstrip("+"),
            "answer_url": f"https://{CTX.host}/plivo/answer?callme={token}", "answer_method": "POST",
            "hangup_url": f"https://{CTX.host}/plivo/hangup", "hangup_method": "POST",
            "ring_timeout": RING_S, "time_limit": CTX.max_call_s + 60,
            **({"machine_detection": "hangup"} if machine_detection else {})}
    r = await b.http.post(f"https://api.plivo.com/v1/Account/{b.auth_id}/Call/", json=body,
                          auth=(b.auth_id, b.auth_token))
    r.raise_for_status()
    return str(r.json().get("request_uuid") or "")


@router.post("/api/calls/call-me")
async def call_me(body: CallMe, request: Request) -> Dict[str, Any]:
    _require_client_header(request)
    if not (browser.CTX.auth_id and CTX.caller_id) or WEB.bus is None or WEB.bus.r is None:
        raise ApiError(503, "call_me_unavailable", "Call me isn't available right now. Call us instead.")
    phone = _phone(body.phone)
    async with WEB.pool.acquire() as con:
        user = await require_user(request, con)
        if phone not in await users.phones(con, user):
            raise ApiError(403, "not_your_number", "We only call your own numbers. Add this one under Your numbers "
                                                   "first.")
        refuse = (await users.allowance(con, user)).get("refuse")
        if refuse == "no_credits":
            raise ApiError(402, "no_credits", "You're out of credits. Add balance to call BolDoc again.")
        if refuse == "trial_over":
            raise ApiError(402, "trial_over", "Your free calls are used. Add balance to keep calling.")
        if refuse:
            raise ApiError(403, "account_unavailable", "This account can't make calls. Write to support.")
        await _limit(f"cm:u:{user['id']}", 3, 600, "You asked for a few calls just now. Wait a few minutes and try "
                                                  "again.")
        await _limit(f"cm:d:{user['id']}", 10, 86400, "That's enough calls from here for today. Call us instead.")
        if len(await WEB.bus.active_calls()) >= browser.CTX.cap:
            raise ApiError(503, "busy", "BolDoc is on as many calls as it can take. Try again in a few minutes.")
        await con.execute("insert into consents (subject, notice_version, channel) values ($1, 'call-me-v1', 'web')",
                          f"user:{user['id']}:{phone[-4:]}")
    rid, token = secrets.token_urlsafe(9), secrets.token_urlsafe(18)
    await WEB.bus.r.set(_token_key(token), json.dumps({"user_id": user["id"], "number": phone, "id": rid,
                                                       "lang": body.lang}), ex=TOKEN_TTL_S)
    await _set_status(rid, user["id"], "calling")
    try:
        await place_call(phone, token)
    except Exception as e:
        await WEB.bus.r.delete(_token_key(token))
        await _set_status(rid, user["id"], "failed")
        logger.warning(f"call me: Plivo refused or unreachable ({type(e).__name__})")
        raise ApiError(503, "call_failed", "We couldn't place the call. Try again, or call us.") from e
    logger.info(f"call me: calling {_mask(phone)} for user {user['id']}")
    return {"id": rid, "calling": _mask(phone), "ring_s": RING_S}


@router.get("/api/calls/call-me/{rid}")
async def call_me_status(rid: str, request: Request) -> Dict[str, Any]:
    """calling (ringing), answered, refused (picked up, but the call couldn't be taken), failed, or ended."""
    async with WEB.pool.acquire() as con:
        user = await require_user(request, con)
    raw = await WEB.bus.r.get(_status_key(rid)) if WEB.bus is not None and WEB.bus.r is not None else None
    st = json.loads(raw) if raw else None
    if not st or st.get("user_id") != user["id"]:
        raise ApiError(404, "not_found", "We don't know that call.")
    return {"status": st["status"]}


async def redeem(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Admission's side: the call we placed ({user_id, number, id}); works once, within TOKEN_TTL_S."""
    if not token or WEB.bus is None or WEB.bus.r is None:
        return None
    raw = await WEB.bus.r.getdel(_token_key(token))
    return json.loads(raw) if raw else None


async def answered(who: Dict[str, Any], admitted: bool) -> None:
    await _set_status(who["id"], who["user_id"], "answered" if admitted else "refused")


async def ended(rid: str, user_id: int) -> None:
    """The call we placed has hung up: the console's panel says so and closes."""
    if WEB.bus is not None and WEB.bus.r is not None:
        await _set_status(rid, user_id, "ended")
