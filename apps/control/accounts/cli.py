"""Server-side account tools (run inside the control container; never reachable from outside).

  python -m apps.control.accounts.cli otp <phone>                      the current code of an allowlisted test number
  python -m apps.control.accounts.cli session <phone>                  a sign-in session (cookie value) for an
                                                                        allowlisted test number: automated browser
                                                                        tests sign in without spending OTPs
  python -m apps.control.accounts.cli grant <phone> <credits> <reason>  activate a number and add credits (logged
                                                                        in the ledger as an admin adjustment)
"""
from __future__ import annotations

import asyncio
import sys

from ..phones import indian_mobile
from ..settings import SETTINGS


async def _grant(phone: str, credits: int, reason: str) -> int:
    import secrets as _secrets

    import asyncpg

    from . import ledger, users

    con = await asyncpg.connect(SETTINGS.database_url)
    try:
        user = await users.activate(con, phone)
        row = await ledger.post(con, user["id"], "admin_adjust", credits, f"grant:{_secrets.token_hex(8)}",
                                actor="cli", reason=reason)
        print(f"user {user['id']} {user['status']}: balance {row['balance_after']}")
        return 0
    finally:
        await con.close()


async def _session(phone: str) -> int:
    import asyncpg

    from . import users
    from .web import new_session

    con = await asyncpg.connect(SETTINGS.database_url)
    try:
        user = await users.activate(con, phone)   # what a web sign-in does for the number
        print(await new_session(con, user["id"], "cli", "accounts.cli session"))
        return 0
    finally:
        await con.close()


def _test_number(raw: str):
    phone = indian_mobile(raw)
    if phone is None or phone not in SETTINGS.otp_test_numbers:
        print("not an allowlisted test number (OTP_TEST_NUMBERS)")
        return None
    return phone


async def _main(argv) -> int:
    if len(argv) == 4 and argv[0] == "grant":
        phone = indian_mobile(argv[1])
        if phone is None:
            print("not an Indian mobile number")
            return 1
        return await _grant(phone, int(argv[2]), argv[3])
    if len(argv) != 2 or argv[0] not in ("otp", "session"):
        print(__doc__)
        return 2
    phone = _test_number(argv[1])
    if phone is None:
        return 1
    if argv[0] == "session":
        return await _session(phone)
    import redis.asyncio as aioredis

    r = aioredis.from_url(SETTINGS.redis_url, decode_responses=True)
    try:
        code = await r.get(f"boldoc:otp:test:{phone}")
    finally:
        await r.aclose()
    print(code or "no code pending")
    return 0 if code else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
