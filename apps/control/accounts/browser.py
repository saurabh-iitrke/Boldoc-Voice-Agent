"""Calls from the website ("Talk in browser"): a Plivo access token for the browser, and a one-time session that
ties the WebRTC call to the signed-in account.

POST /api/calls/browser-token (signed in) checks that the account may call now (credits, capacity) before the
browser asks for the microphone, then returns
* `session`: single use, 120 s, only its hash kept (Redis). The browser sends it as the X-PH-Session SIP header on
  its call; admission redeems it once and the call becomes the account's, under the same rules as a phone call
  (allowance, one call at a time, capacity). A browser call without a valid session is refused. The session also
  names the call for the website's live view while the call lasts.
* `token`: a Plivo JWT for the website's shared endpoint, outgoing calls only. Plivo mints it (tokens signed
  locally are rejected); it lives as long as the longest call it can start, and is useless without a session.

One endpoint serves every browser: Plivo allows several logins on one endpoint and calls that reliable for
outbound-only use, which is all the website does. Plivo caps concurrent logins per endpoint (login error 10010), so
the browser logs in only when a call starts and logs out when it ends.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Request
from loguru import logger

from ..api_errors import ApiError
from . import users
from .pricing import PRICING
from .web import CTX as WEB
from .web import _ip, _limit, _require_client_header, require_user

router = APIRouter()
SESSION_TTL_S = 120
DESTINATION = "boldoc"  # what the browser dials: any name works, the endpoint's application answers every call
UNAVAILABLE = "Browser calls aren't available right now. You can call our number instead."


class Ctx:
    """Set by the control service at startup."""
    auth_id = ""
    auth_token = ""
    endpoint = ""     # the shared endpoint's username (PLIVO_WEB_ENDPOINT, made by tools/m0/plivo_setup.py)
    app_id = ""       # its Plivo application (PLIVO_APP_ID): the one whose answer URL admits the call
    cap = 3           # calls at once (MAX_CONCURRENT_CALLS): above it the browser hears "busy" before the mic prompt
    http: Optional[httpx.AsyncClient] = None


CTX = Ctx()


def fingerprint(session: str) -> str:
    """Eight hex characters that tell tokens apart in the logs and reveal nothing of them."""
    return hashlib.sha256(session.encode()).hexdigest()[:8]


def _key(session: str) -> str:
    return "boldoc:bt:" + hashlib.sha256(session.encode()).hexdigest()


def _redis():
    if WEB.bus is None or WEB.bus.r is None:
        raise ApiError(503, "browser_unavailable", UNAVAILABLE)
    return WEB.bus.r


async def plivo_token(ttl_s: int) -> str:
    """A Plivo access token for the shared endpoint: outgoing calls only, valid from a minute ago (clock skew)."""
    now = int(time.time())
    body: Dict[str, Any] = {"iss": CTX.auth_id, "sub": CTX.endpoint, "nbf": now - 60, "exp": now + ttl_s,
                            "per": {"voice": {"incoming_allow": False, "outgoing_allow": True}}}
    if CTX.app_id:
        body["app"] = CTX.app_id
    if CTX.http is None:
        CTX.http = httpx.AsyncClient(timeout=10)
    r = await CTX.http.post(f"https://api.plivo.com/v1/Account/{CTX.auth_id}/JWT/Token/", json=body,
                            auth=(CTX.auth_id, CTX.auth_token))
    r.raise_for_status()
    return r.json()["token"]


@router.post("/api/calls/browser-token")
async def browser_token(request: Request) -> Dict[str, Any]:
    _require_client_header(request)
    try:  # {"lang": "hi-IN"}: the language picked on the page; when none, their last call's (else English)
        lang = str((await request.json() or {}).get("lang") or "") or None
    except Exception:
        lang = None
    if not (CTX.endpoint and CTX.auth_id):
        raise ApiError(503, "browser_unavailable", UNAVAILABLE)
    r = _redis()
    async with WEB.pool.acquire() as con:
        user = await require_user(request, con)
        allowance = await users.allowance(con, user)
    refuse = allowance.get("refuse")
    if refuse == "no_credits":
        raise ApiError(402, "no_credits", "Your balance has run out, so BolDoc can't take this call. Adding "
                       "money is coming soon.")
    if refuse == "trial_over":
        raise ApiError(402, "trial_over", f"Your free calls are used. Sign in with your number to get "
                       f"₹{PRICING.signup_bonus:,} and keep calling.")
    if refuse:
        raise ApiError(403, "account_unavailable", "This account can't make calls. Write to support.")
    # 30 an hour: 10 stopped a signed-up caller testing on a busy afternoon, refused calls counting too
    await _limit(f"bt:u:{user['id']}", 30, 3600, "Too many call attempts. Wait a few minutes and try again.")
    await _limit(f"bt:ip:{_ip(request)}", 30, 3600, "Too many call attempts from this network. Try again later.")
    if len(await WEB.bus.active_calls()) >= CTX.cap:
        raise ApiError(503, "busy", "BolDoc is on as many calls as it can take. Try again in a few minutes.")
    max_call_s = int(allowance.get("max_call_s") or 0)
    # hex only: with url-safe base64 ("-" and "_" in about half the tokens) 3 of 6 browser calls in a row came in
    # without a token that matched and were refused as unauthenticated; 0-9a-f passes every SIP hop
    session = secrets.token_hex(18)
    await r.set(_key(session), json.dumps({"user_id": user["id"], "lang": lang}), ex=SESSION_TTL_S)
    try:
        token = await plivo_token(max_call_s + 300)
    except Exception as e:
        await r.delete(_key(session))
        logger.warning(f"browser token: Plivo refused or unreachable ({type(e).__name__})")
        raise ApiError(503, "browser_unavailable", UNAVAILABLE) from e
    # a fingerprint, never the token: the talker logs the same for the call that arrives with it
    logger.info(f"browser call token for user {user['id']} (calls up to {max_call_s}s) fp={fingerprint(session)}")
    return {"token": token, "destination": DESTINATION, "session": session, "max_call_s": max_call_s}


async def redeem(session: Optional[str]) -> Optional[Dict[str, Any]]:
    """Admission's side: whose browser call this is. A session works once; an unknown or used one is None."""
    if not session or WEB.bus is None or WEB.bus.r is None:
        return None
    raw = await WEB.bus.r.getdel(_key(session))
    return json.loads(raw) if raw else None


async def remember_call(session: str, call_uuid: str, user_id: int, ttl_s: int) -> None:
    """The live view finds the call by the browser's session for as long as the call can last."""
    if WEB.bus is not None and WEB.bus.r is not None:
        await WEB.bus.r.set(_key(session) + ":call", json.dumps({"call_uuid": call_uuid, "user_id": user_id}),
                            ex=ttl_s)


async def call_for(session: str) -> Optional[Dict[str, Any]]:
    """The call a browser session started: {call_uuid, user_id}, or None."""
    if not session or WEB.bus is None or WEB.bus.r is None:
        return None
    raw = await WEB.bus.r.get(_key(session) + ":call")
    return json.loads(raw) if raw else None
