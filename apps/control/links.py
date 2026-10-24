"""Who may open a document link (/d/<token>).

The link goes by SMS to the caller's number, and anyone holding it could open it (a forwarded SMS, a screenshot). So a
link opens on one device: the first browser that opens it, with a tap (link previews and scanners never tap), keeps it.
Anywhere else, the owner signs in with the number it went to; a signed-in owner opens their documents on any device,
without claiming the link, so a fresh link they share still goes to whoever opens it first. The owner can stop a link
and get a new one from the console.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any, Dict, Optional, Tuple

from fastapi import Request, Response

from .accounts import documents as account_docs
from .accounts.web import CTX, current_user
from .api_errors import ApiError

COOKIE = "boldoc_dv"
DEVICE_DAYS = 400


def _sig(raw: str) -> str:
    return hmac.new(f"link-device:{CTX.secret}".encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]


def _hash(raw: str) -> str:
    return hashlib.sha256(f"link-device:{raw}".encode()).hexdigest()[:32]


def device_of(request: Request) -> Optional[str]:
    """This browser's id: a hash of its signed cookie (the cookie itself is never stored), or None."""
    raw, _, sig = (request.cookies.get(COOKIE) or "").partition(".")
    if not raw or not hmac.compare_digest(sig, _sig(raw)):
        return None
    return _hash(raw)


def new_device() -> Tuple[str, str]:
    """(cookie value, device id) for a browser that has none yet."""
    raw = secrets.token_urlsafe(16)
    return f"{raw}.{_sig(raw)}", _hash(raw)


def set_device(response: Response, cookie: str) -> None:
    response.set_cookie(COOKIE, cookie, max_age=DEVICE_DAYS * 86400, httponly=True, secure=CTX.secure_cookie,
                        samesite="lax", path="/")


async def signed_in_owner(request: Request, doc_id: str) -> Optional[Dict[str, Any]]:
    """The signed-in account, if this document is theirs (any of their numbers, any of their calls)."""
    if CTX.pool is None:
        return None
    async with CTX.pool.acquire() as con:
        user = await current_user(request, con)
        if user and await account_docs.owned_doc_ids(con, user, doc_id):
            return user
    return None


async def owner_or_raise(request: Request, doc_id: str) -> Dict[str, Any]:
    """For the console's actions on one document: signed out is 401, someone else's document 404."""
    async with CTX.pool.acquire() as con:
        user = await current_user(request, con)
        if user is None:
            raise ApiError(401, "signed_out", "Please sign in again.")
        if not await account_docs.owned_doc_ids(con, user, doc_id):
            raise ApiError(404, "not_found", "This document doesn't exist or isn't yours.")
    return user


async def access(request: Request, link: Dict[str, Any]) -> str:
    """'open' (the signed-in owner, or the browser that holds the link), 'claim' (no browser holds it yet) or
    'locked' (another browser holds it)."""
    if await signed_in_owner(request, link["doc_id"]) is not None:
        return "open"
    if link.get("device_hash") is None:
        return "claim"
    return "open" if device_of(request) == link["device_hash"] else "locked"
