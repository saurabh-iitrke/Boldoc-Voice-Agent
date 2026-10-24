"""Admin credentials: argon2id passwords, RFC 6238 TOTP (stdlib only) or a code by SMS to the admin's own mobile,
cookie sessions, the audit log."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

PH = PasswordHasher()
_DUMMY = PH.hash("no admin has this password")  # an unknown username takes as long as a wrong password
SESSION_HOURS = 12
STEP_S = 30
MIN_PASSWORD = 12
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"the password needs at least {MIN_PASSWORD} characters")
    return PH.hash(password)


def password_ok(pw_hash: Optional[str], password: str) -> bool:
    try:
        return bool(PH.verify(pw_hash or _DUMMY, password)) and pw_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_step(code: str, secret: str, last_step: Optional[int], now: Optional[float] = None) -> Optional[int]:
    """The time step a 6-digit code belongs to (now, or one step either side for clock drift), if not used before."""
    code = "".join(ch for ch in code or "" if ch.isdigit())
    if len(code) != 6:
        return None
    t = int((time.time() if now is None else now) // STEP_S)
    for step in (t, t - 1, t + 1):
        if (last_step is None or step > last_step) and hmac.compare_digest(totp_code(secret, step), code):
            return step
    return None


def provisioning_uri(username: str, secret: str) -> str:
    issuer = quote("BolDoc admin")
    return (f"otpauth://totp/{issuer}:{quote(username)}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6"
            f"&period={STEP_S}")


async def audit(con, actor: str, action: str, target: Optional[str] = None,
                detail: Optional[Dict[str, Any]] = None, ip: Optional[str] = None) -> None:
    await con.execute("insert into audit_log (actor, action, target, detail, ip) values ($1, $2, $3, $4::jsonb, $5)",
                      actor[:64], action, target, json.dumps(detail or {}), ip)


async def create_admin(con, username: str, password: Optional[str], phone: Optional[str] = None) -> Tuple[str, str]:
    """A new admin; returns (TOTP secret, otpauth URI). With no password the admin can't sign in until one is set
    (set-password); `phone`: their own mobile, for sign-in codes by SMS."""
    username = username.strip().lower()
    if not USERNAME.match(username):
        raise ValueError("username: 3-32 characters, lowercase letters, digits, . _ -")
    secret = new_totp_secret()
    row = await con.fetchrow("insert into admin_users (username, pw_hash, totp_secret, phone_e164) values "
                             "($1, $2, $3, $4) on conflict (username) do nothing returning id", username,
                             hash_password(password) if password is not None else None, secret, phone)
    if row is None:
        raise ValueError(f"an admin called {username} already exists")
    await audit(con, "cli", "create_admin", username)
    return secret, provisioning_uri(username, secret)


async def new_session(con, admin_id: int, ip: str, ua: str) -> str:
    token = secrets.token_urlsafe(32)
    await con.execute("insert into admin_sessions (id_hash, admin_id, expires_at, ip, ua) values ($1, $2, $3, $4, $5)",
                      _hash(token), admin_id, datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS), ip,
                      (ua or "")[:200])
    return token


async def session_admin(con, token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    row = await con.fetchrow(
        "select a.id, a.username from admin_sessions s join admin_users a on a.id = s.admin_id where s.id_hash=$1 "
        "and s.revoked_at is null and s.expires_at > now() and a.disabled_at is null", _hash(token))
    return dict(row) if row else None


async def end_session(con, token: Optional[str]) -> None:
    if token:
        await con.execute("update admin_sessions set revoked_at = now() where id_hash=$1", _hash(token))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
