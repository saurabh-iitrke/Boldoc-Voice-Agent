"""More numbers on one account (Settings → "Your numbers"). Each is proven by a code sent to it, up to
PRICING.linked_numbers besides the number the account signs in with. A call from a linked number is the account's
call (its plan, its credits, its documents); the SMS link still goes to the number that called. What a number made
before it was linked shows in the account while it stays linked.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request
from loguru import logger

from ..api_errors import ApiError
from ..phones import masked
from . import users
from .pricing import PRICING
from .web import (
    CTX,
    OtpRequest,
    OtpVerify,
    _limit,
    _mask,
    _phone,
    _require_client_header,
    check_code,
    require_user,
    send_code,
)

router = APIRouter()
WHY = {"own_number": "That's the number you sign in with.",
       "other_account": "This number has its own BolDoc account. Sign in with it instead, or remove it there first.",
       "too_many": f"You can add up to {PRICING.linked_numbers} numbers."}


async def _payload(con, user: Dict[str, Any]) -> Dict[str, Any]:
    rows = await con.fetch("select phone_e164, added_at from user_phones where user_id=$1 order by added_at",
                           user["id"])
    return {"numbers": [{"phone_e164": user["phone_e164"], "phone_masked": _mask(user["phone_e164"]), "primary": True}]
            + [{"phone_e164": r["phone_e164"], "phone_masked": _mask(r["phone_e164"]), "primary": False,
                "added_at": r["added_at"].isoformat()} for r in rows],
            "max_added": PRICING.linked_numbers}


@router.get("/api/numbers")
async def list_numbers(request: Request) -> Dict[str, Any]:
    async with CTX.pool.acquire() as con:
        return await _payload(con, await require_user(request, con))


@router.post("/api/numbers/code")
async def send_number_code(body: OtpRequest, request: Request) -> Dict[str, Any]:
    """Step 1: a code to the number being added (refused before any SMS if it can't be added)."""
    _require_client_header(request)
    phone = _phone(body.phone)
    async with CTX.pool.acquire() as con:
        user = await require_user(request, con)
        why = await users.can_link(con, user, phone)
    if why:
        raise ApiError(409, why, WHY[why])
    await _limit(f"otp:link:u:{user['id']}", 10, 86400, "Too many numbers tried today. Try again tomorrow.")
    return {"resend_after_s": await send_code(request, phone, "link")}


@router.post("/api/numbers")
async def add_number(body: OtpVerify, request: Request) -> Dict[str, Any]:
    """Step 2: the code that came to that number adds it to the account."""
    _require_client_header(request)
    phone = _phone(body.phone)
    async with CTX.pool.acquire() as con:
        user = await require_user(request, con)
    await check_code(phone, body.code, "link")
    async with CTX.pool.acquire() as con:
        why = await users.link(con, user, phone)
        if why:
            raise ApiError(409, why, WHY[why])
        logger.info(f"user={user['id']} added {masked(phone)}")
        return await _payload(con, user)


@router.delete("/api/numbers/{phone}")
async def remove_number(phone: str, request: Request) -> Dict[str, Any]:
    _require_client_header(request)
    number = _phone(phone)
    async with CTX.pool.acquire() as con:
        user = await require_user(request, con)
        if number == user["phone_e164"]:
            raise ApiError(409, "own_number", "The number you sign in with stays on the account.")
        if await users.unlink(con, user, number):
            logger.info(f"user={user['id']} removed {masked(number)}")
        return await _payload(con, user)
