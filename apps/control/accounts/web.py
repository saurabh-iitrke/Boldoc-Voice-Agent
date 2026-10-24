"""Web sign-in and the account API: phone OTP (or "verify by calling"), cookie sessions, credits, my details.

Sign-in is a phone number and a 6-digit code by SMS (5-minute TTL, at most 5 attempts, resend after 30 s then
60 s). Every limit that costs money fails closed: per number (5/hour, 10/day), per IP (30/hour) and a global daily
SMS budget. When the SMS doesn't arrive, the user can call our number from that phone instead: admission sees the
caller ID, marks the number verified and hangs up after one sentence (caller-ID strength, so changing the number
or deleting the account still needs the SMS code).

Sessions are an httpOnly cookie holding a random token; only its SHA-256 is stored. Mutating requests must carry
the X-BolDoc-Client header (a custom header a cross-site form can't send) as CSRF protection.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, Response
from loguru import logger
from pydantic import BaseModel

from ..api_errors import ApiError
from ..phones import indian_mobile, masked
from . import ledger, users
from .pricing import PRICING

router = APIRouter()
COOKIE = "boldoc_session"
SESSION_DAYS = 30
OTP_TTL_S, OTP_ATTEMPTS = 300, 5
RESEND_FIRST_S, RESEND_NEXT_S = 30, 60
VERIFY_BY_CALL_S = 120


class Ctx:
    """Set by the control service at startup."""
    pool = None
    bus = None
    sms = None                     # PlivoSms, or None (then only test numbers can sign in)
    secret = ""                    # HMAC key for OTP hashes (server-only)
    host = "localhost"             # for the WebOTP line: "@<host> #<code>"
    dial_in = ""                   # our number, for "verify by calling"
    member_dial_in = ""            # the number shown to signed-in users ("" = the same one)
    test_numbers: frozenset = frozenset()   # allowlisted test numbers: no SMS; code readable on the server only
    sms_daily_budget = 300
    secure_cookie = True
    store = None                   # PgStore (document read model)


CTX = Ctx()


# ------------------------------------------------------------------------------------------------- helpers
def _ip(request: Request) -> str:
    return request.headers.get("X-Forwarded-For", request.client.host if request.client else "").split(",")[0].strip()


def _require_client_header(request: Request) -> None:
    if request.headers.get("X-BolDoc-Client") != "web":
        raise ApiError(403, "forbidden", "This request must come from the BolDoc website.")


def _phone(raw: str) -> str:
    phone = indian_mobile(raw)
    if phone is None:
        raise ApiError(422, "invalid_number", "Enter a 10-digit Indian mobile number.")
    return phone


def _otp_hash(phone: str, code: str) -> str:
    return hmac.new(CTX.secret.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def _redis():
    if CTX.bus is None or CTX.bus.r is None:
        raise ApiError(503, "signin_unavailable", "Sign-in is unavailable right now. Try again in a minute.")
    return CTX.bus.r


async def _limit(key: str, limit: int, window_s: int, message: str) -> None:
    if not await CTX.bus.allow(key, limit, window_s, fail_open=False):
        raise ApiError(429, "too_many_requests", message)


def _mask(phone: str) -> str:
    d = phone[-10:]
    return f"+91 {d[:2]}••• ••{d[-3:]}"


async def me_payload(con, user: Dict[str, Any]) -> Dict[str, Any]:
    profile = await con.fetchrow("select name from profiles where user_id=$1", user["id"])
    out: Dict[str, Any] = {"id": user["id"], "phone_e164": user["phone_e164"],
                           "phone_masked": _mask(user["phone_e164"]),
                           "status": user["status"], "email": user["email"],
                           "name": profile["name"] if profile else None,
                           "call_lang": user.get("call_lang"),  # BolDoc starts their calls in it (their last call's)
                           "dial_in": CTX.member_dial_in or CTX.dial_in}  # the number they call, signed in
    if user["status"] == "active":
        bal = await ledger.balance(con, user["id"])
        out["credits"] = {"balance": bal, "docs_estimate": PRICING.docs_estimate(bal)}
    else:
        allowance = await users.allowance(con, user)
        out["trial"] = {"calls_left": allowance.get("calls_left", 0)}
    return out


async def new_session(con, user_id: int, ip: str, ua: str) -> str:
    """A sign-in session: a random token for the cookie; only its SHA-256 is stored."""
    token = secrets.token_urlsafe(32)
    await con.execute("insert into auth_sessions (id_hash, user_id, expires_at, ip, ua) values ($1,$2,$3,$4,$5)",
                      hashlib.sha256(token.encode()).hexdigest(), user_id,
                      datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS), ip, ua[:200])
    return token


async def _start_session(con, response: Response, request: Request, user: Dict[str, Any]) -> None:
    token = await new_session(con, user["id"], _ip(request), request.headers.get("user-agent") or "")
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, secure=CTX.secure_cookie,
                        samesite="lax", path="/")


async def current_user(request: Request, con) -> Optional[Dict[str, Any]]:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    row = await con.fetchrow(
        "select u.* from auth_sessions s join users u on u.id = s.user_id where s.id_hash=$1 and s.revoked_at is "
        "null and s.expires_at > now() and u.status in ('active', 'trial')", hashlib.sha256(token.encode()).hexdigest())
    return dict(row) if row else None


async def require_user(request: Request, con) -> Dict[str, Any]:
    user = await current_user(request, con)
    if user is None:
        raise ApiError(401, "signed_out", "Please sign in again.")
    return user


# ------------------------------------------------------------------------------------------------- sign-in
class OtpRequest(BaseModel):
    phone: str


class OtpVerify(BaseModel):
    phone: str
    code: str


SMS_TEXT = {"signin": "{code} is your BolDoc code. It expires in 5 minutes. Don't share it.",
            "link": "{code} is your BolDoc code to add this number to your account. It expires in 5 minutes. "
                    "Don't share it.",
            "admin": "{code} is your BolDoc admin sign-in code. It expires in 5 minutes. Don't share it."}


def _otp_key(purpose: str) -> str:
    """Codes for different purposes never stand in for each other (sign-in keys are as they always were)."""
    return "boldoc:otp" if purpose == "signin" else f"boldoc:otp:{purpose}"


@router.post("/api/auth/otp")
async def request_otp(body: OtpRequest, request: Request) -> Dict[str, Any]:
    _require_client_header(request)
    return {"resend_after_s": await send_code(request, _phone(body.phone))}


async def send_code(request: Request, phone: str, purpose: str = "signin", host: Optional[str] = None) -> int:
    """A 6-digit code by SMS that proves the number is yours (to sign in, to add it to your account, or for an admin
    to sign in to the admin console). The limits per network, per number and per day are shared by every purpose.
    `host`: the site the code is for (its WebOTP line), when it isn't the public site. Returns the wait before
    another code."""
    k = _otp_key(purpose)
    by_call = purpose != "admin"  # the admin console has no "verify by calling": its other way is the authenticator
    cool = await _redis().ttl(f"{k}:cool:{phone}")
    if cool and cool > 0:
        raise ApiError(429, "resend_too_soon", f"Wait {cool} seconds before asking for another code.",
                       {"resend_after_s": cool})
    await _limit(f"otp:ip:{_ip(request)}", 30, 3600, "Too many sign-in attempts from this network. Try again later.")
    await _limit(f"otp:h:{phone}", 5, 3600, "Too many codes for this number. Try again in an hour.")
    await _limit(f"otp:d:{phone}", 10, 86400, "Too many codes for this number today.")
    code = f"{secrets.randbelow(10**6):06d}"
    sent_before = await CTX.bus.r.exists(f"{k}:{phone}")
    await CTX.bus.r.set(f"{k}:{phone}", json.dumps({"h": _otp_hash(phone, code), "attempts": 0}), ex=OTP_TTL_S)
    resend = RESEND_NEXT_S if sent_before else RESEND_FIRST_S
    await CTX.bus.r.set(f"{k}:cool:{phone}", "1", ex=resend)
    if phone in CTX.test_numbers:
        await CTX.bus.r.set(f"{k}:test:{phone}", code, ex=OTP_TTL_S)  # read on the server only (CLI)
    else:
        await _limit("otp:global", CTX.sms_daily_budget, 86400, "Sign-in by SMS is paused for today. " + (
            "Try \"verify by calling\" instead." if by_call else "Use the authenticator app instead."))
        if CTX.sms is None:
            raise ApiError(503, "sms_unavailable", "We can't send codes right now. " + (
                "Try \"verify by calling\"." if by_call else "Use the authenticator app instead."))
        text = SMS_TEXT[purpose].format(code=code) + f"\n\n@{host or CTX.host} #{code}"
        try:
            await CTX.sms.send(phone, text)
        except Exception as e:
            logger.warning(f"otp sms to {masked(phone)} failed: {type(e).__name__}")
            raise ApiError(502, "sms_failed", "We couldn't send the code. " + (
                "Try again, or verify by calling." if by_call else "Try again, or use the authenticator app.")) from e
    logger.info(f"otp ({purpose}) sent to {masked(phone)}{' (test number)' if phone in CTX.test_numbers else ''}")
    return resend


@router.post("/api/auth/verify")
async def verify_otp(body: OtpVerify, request: Request, response: Response) -> Dict[str, Any]:
    _require_client_header(request)
    phone = _phone(body.phone)
    await check_code(phone, body.code)
    return await _signed_in(request, response, phone, "otp")


async def check_code(phone: str, raw_code: str, purpose: str = "signin") -> None:
    """Raises unless this is the live code sent to the number for this purpose; a used code is gone."""
    k = _otp_key(purpose)
    key = f"{k}:{phone}"
    raw = await _redis().get(key)
    if raw is None:
        raise ApiError(400, "code_expired", "That code has expired. Ask for a new one.")
    entry = json.loads(raw)
    if entry["attempts"] >= OTP_ATTEMPTS:
        await CTX.bus.r.delete(key)
        raise ApiError(429, "too_many_attempts", "Too many wrong codes. Ask for a new one.")
    code = "".join(ch for ch in raw_code if ch.isdigit())
    if not hmac.compare_digest(entry["h"], _otp_hash(phone, code)):
        entry["attempts"] += 1
        ttl = await CTX.bus.r.ttl(key)
        await CTX.bus.r.set(key, json.dumps(entry), ex=max(1, ttl))
        left = OTP_ATTEMPTS - entry["attempts"]
        raise ApiError(400, "wrong_code", "That code isn't right." + (f" {left} tries left." if left else ""),
                       {"attempts_left": left})
    await CTX.bus.r.delete(key, f"{k}:test:{phone}")


async def _signed_in(request: Request, response: Response, phone: str, how: str) -> Dict[str, Any]:
    async with CTX.pool.acquire() as con:
        user = await users.activate(con, phone)
        if user["status"] not in ("active",):
            raise ApiError(403, "account_unavailable", "This account can't sign in. Write to support.")
        await _start_session(con, response, request, user)
        logger.info(f"signed in user={user['id']} via {how}")
        return await me_payload(con, user)


# "verify by calling": the fallback when the SMS doesn't come ------------------------------------------------
@router.post("/api/auth/verify-by-call")
async def verify_by_call_start(body: OtpRequest, request: Request) -> Dict[str, Any]:
    _require_client_header(request)
    phone = _phone(body.phone)
    await _limit(f"vbc:ip:{_ip(request)}", 20, 3600, "Too many attempts from this network. Try again later.")
    nonce = secrets.token_urlsafe(16)
    await _redis().set(f"boldoc:vbc:{phone}", nonce, ex=VERIFY_BY_CALL_S)
    await CTX.bus.r.set(f"boldoc:vbc:n:{nonce}", json.dumps({"phone": phone, "status": "pending"}),
                        ex=VERIFY_BY_CALL_S + 60)
    return {"call": CTX.dial_in, "expires_in_s": VERIFY_BY_CALL_S, "nonce": nonce}


@router.get("/api/auth/verify-by-call/{nonce}")
async def verify_by_call_status(nonce: str, request: Request, response: Response) -> Dict[str, Any]:
    raw = await _redis().get(f"boldoc:vbc:n:{nonce}")
    if raw is None:
        raise ApiError(410, "expired", "That took too long. Start again.")
    entry = json.loads(raw)
    if entry["status"] != "verified":
        return {"status": "pending"}
    await CTX.bus.r.delete(f"boldoc:vbc:n:{nonce}")  # one sign-in per call
    return {"status": "verified", "me": await _signed_in(request, response, entry["phone"], "call")}


async def verified_by_call(phone: str) -> bool:
    """Admission's side: a call from a number waiting for "verify by calling" verifies it (and ends there)."""
    if CTX.bus is None or CTX.bus.r is None or not phone:
        return False
    nonce = await CTX.bus.r.get(f"boldoc:vbc:{phone}")
    if not nonce:
        return False
    await CTX.bus.r.delete(f"boldoc:vbc:{phone}")
    await CTX.bus.r.set(f"boldoc:vbc:n:{nonce}", json.dumps({"phone": phone, "status": "verified"}), ex=120)
    logger.info(f"verified by call: {masked(phone)}")
    return True


@router.post("/api/auth/logout", status_code=204)
async def logout(request: Request, response: Response) -> Response:
    _require_client_header(request)
    token = request.cookies.get(COOKIE)
    if token:
        async with CTX.pool.acquire() as con:
            await con.execute("update auth_sessions set revoked_at=now() where id_hash=$1",
                              hashlib.sha256(token.encode()).hexdigest())
    response.delete_cookie(COOKIE, path="/")
    response.status_code = 204
    return response


# ------------------------------------------------------------------------------------------------- account
@router.get("/api/me", response_model=None)
async def me(request: Request):
    """The signed-in user, or 204 when nobody is signed in (a normal state on public pages, not an error)."""
    async with CTX.pool.acquire() as con:
        user = await current_user(request, con)
        if user is None:
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        return await me_payload(con, user)


@router.get("/api/credits")
async def credits(request: Request) -> Dict[str, Any]:
    async with CTX.pool.acquire() as con:
        user = await require_user(request, con)
        bal = await ledger.balance(con, user["id"])
        rows = await ledger.history(con, user["id"])
        # a document's debit carries its id: the history names it ("New document: Leave Application")
        doc_ids = sorted({r["ref"] for r in rows if r["kind"] in ("doc_create", "doc_edit") and r["ref"]})
        titles = {t["id"]: t["title"] for t in await con.fetch(
            "select id, title from documents where id = any($1::text[])", doc_ids)} if doc_ids else {}
    return {"balance": bal, "docs_estimate": PRICING.docs_estimate(bal),
            "prices": {"minute": PRICING.minute, "document": PRICING.doc_create, "edit": PRICING.doc_edit},
            "ledger": [{"id": r["id"], "kind": r["kind"], "amount": r["amount"], "balance_after": r["balance_after"],
                        "reason": r["reason"], "doc_title": titles.get(r["ref"]) or None,
                        "at": r["created_at"].isoformat()} for r in rows]}


class Profile(BaseModel):
    name: Optional[str] = None
    designation: Optional[str] = None
    organisation: Optional[str] = None
    address: Optional[str] = None
    default_lang: Optional[str] = None
    ui_lang: Optional[str] = None
    retention_days: Optional[int] = None


PROFILE_FIELDS = ("name", "designation", "organisation", "address", "default_lang", "ui_lang", "retention_days")


@router.get("/api/profile")
async def get_profile(request: Request) -> Dict[str, Any]:
    async with CTX.pool.acquire() as con:
        user = await require_user(request, con)
        row = await con.fetchrow("select * from profiles where user_id=$1", user["id"])
    return {k: (row[k] if row else None) for k in PROFILE_FIELDS} | {"email": user["email"]}


@router.put("/api/profile")
async def put_profile(body: Profile, request: Request) -> Dict[str, Any]:
    _require_client_header(request)
    data = {k: (v.strip()[:300] if isinstance(v, str) else v) for k, v in body.model_dump().items()}
    if data["retention_days"] not in (None, 0, 30):
        raise ApiError(422, "invalid_retention", "Keep transcripts for 30 days, or don't keep them.")
    async with CTX.pool.acquire() as con:
        user = await require_user(request, con)
        await con.execute(
            "insert into profiles (user_id, name, designation, organisation, address, default_lang, ui_lang, "
            "retention_days) values ($1,$2,$3,$4,$5,$6,$7,coalesce($8, 30)) on conflict (user_id) do update set "
            "name=excluded.name, designation=excluded.designation, organisation=excluded.organisation, "
            "address=excluded.address, default_lang=excluded.default_lang, ui_lang=excluded.ui_lang, "
            "retention_days=excluded.retention_days, updated_at=now()", user["id"], data["name"], data["designation"],
            data["organisation"], data["address"], data["default_lang"], data["ui_lang"], data["retention_days"])
    return await get_profile(request)


async def known_facts(con, user_id: Optional[int]) -> Dict[str, str]:
    """The caller's saved "My details", for the thinker (only what they filled in)."""
    if not user_id:
        return {}
    row = await con.fetchrow("select name, designation, organisation, address from profiles where user_id=$1", user_id)
    return {k: row[k] for k in ("name", "designation", "organisation", "address") if row and row[k]}
