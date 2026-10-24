"""Admin accounts, on the server (there is no default admin and no default password):

  docker compose exec control python -m apps.control.admin create-admin <username> [--phone <mobile>]
      asks for the password twice (not echoed), then prints the TOTP secret and its otpauth:// link once: add it to
      an authenticator app (Google Authenticator, 1Password, ...); the console asks for its 6-digit code at sign-in,
      or for a code sent by SMS to the admin's own mobile (--phone, or set-phone later)
  ... create-admin <username> --no-password [--phone <mobile>]
      an admin who can't sign in until someone runs set-password; nothing secret is printed (reset-totp shows the
      authenticator's secret when it's wanted)
  ... set-password <username> | set-phone <username> <mobile|none> | reset-totp <username> | disable <username> | list

Without a terminal (docker compose exec -T), the password is read from the first line of stdin.
"""
from __future__ import annotations

import asyncio
import getpass
import os
import sys
from typing import List, Optional

from ..phones import indian_mobile, masked
from . import auth


def _read_password() -> str:
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Password (12+ characters): ")
    if getpass.getpass("Again: ") != first:
        raise ValueError("the two passwords differ")
    return first


def _mobile(raw: str) -> Optional[str]:
    """An Indian mobile number in E.164, or None after saying why not."""
    phone = indian_mobile(raw)
    if phone is None:
        print("error: give a 10-digit Indian mobile number (or +91 and the number)")
    return phone


async def run(argv: List[str], dsn: str, password: Optional[str] = None) -> int:
    import asyncpg

    commands = ("create-admin", "set-password", "set-phone", "reset-totp", "disable", "list")
    if len(argv) < 1 or argv[0] not in commands or (argv[0] != "list" and len(argv) < 2) or (
            argv[0] == "set-phone" and len(argv) < 3):
        print(__doc__)
        return 2
    cmd, username = argv[0], (argv[1].strip().lower() if len(argv) > 1 else "")
    opts = argv[2:]
    phone: Optional[str] = None
    if cmd == "create-admin" and "--phone" in opts:
        i = opts.index("--phone")
        phone = _mobile(opts[i + 1] if i + 1 < len(opts) else "")
        if phone is None:
            return 1
    if cmd == "set-phone" and opts[0].lower() != "none":
        phone = _mobile(opts[0])
        if phone is None:
            return 1
    con = await asyncpg.connect(dsn)
    try:
        if not await con.fetchval("select to_regclass('admin_users') is not null"):
            print("error: no admin tables yet: start (or restart) control once, it applies the migrations")
            return 1
        if cmd == "list":
            for r in await con.fetch("select username, created_at, last_login_at, disabled_at, phone_e164, "
                                     "pw_hash is null as no_password from admin_users order by id"):
                print(f"{r['username']:<24} created {r['created_at']:%Y-%m-%d}  last sign-in "
                      f"{r['last_login_at'] or '-'}  SMS to {masked(r['phone_e164']) if r['phone_e164'] else '-'}"
                      f"{'  NO PASSWORD YET' if r['no_password'] else ''}{'  DISABLED' if r['disabled_at'] else ''}")
            return 0
        if cmd == "create-admin":
            if "--no-password" in opts:
                await auth.create_admin(con, username, None, phone)
                print(f"Admin {username} created without a password: it can't sign in until set-password is run."
                      + (f" Codes by SMS go to {masked(phone)}." if phone else ""))
                return 0
            secret, uri = await auth.create_admin(con, username, password if password is not None else _read_password(),
                                                  phone)
            print(f"Admin {username} created. Add this to an authenticator app now; it is not shown again.")
            print(f"  TOTP secret: {secret}")
            print(f"  otpauth URI: {uri}")
            if phone:
                print(f"  Codes by SMS go to {masked(phone)}.")
            return 0
        if await con.fetchval("select id from admin_users where username=$1", username) is None:
            print(f"no admin called {username}")
            return 1
        if cmd == "set-password":
            pw_hash = auth.hash_password(password if password is not None else _read_password())
            await con.execute("update admin_users set pw_hash=$2 where username=$1", username, pw_hash)
            await con.execute("update admin_sessions set revoked_at=now() where admin_id=(select id from admin_users "
                              "where username=$1) and revoked_at is null", username)
        elif cmd == "set-phone":
            await con.execute("update admin_users set phone_e164=$2 where username=$1", username, phone)
            print(f"  codes by SMS: {masked(phone) if phone else 'off (authenticator app only)'}")
        elif cmd == "reset-totp":
            secret = auth.new_totp_secret()
            await con.execute("update admin_users set totp_secret=$2, last_totp_step=null where username=$1",
                              username, secret)
            print(f"  TOTP secret: {secret}")
            print(f"  otpauth URI: {auth.provisioning_uri(username, secret)}")
        elif cmd == "disable":
            await con.execute("update admin_users set disabled_at=now() where username=$1", username)
            await con.execute("update admin_sessions set revoked_at=now() where admin_id=(select id from admin_users "
                              "where username=$1) and revoked_at is null", username)
        await auth.audit(con, "cli", cmd.replace("-", "_"), username)
        print(f"{cmd}: done for {username}")
        return 0
    except ValueError as e:
        print(f"error: {e}")
        return 1
    finally:
        await con.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1:], os.environ["DATABASE_URL"])))
