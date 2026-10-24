"""Users: one per phone number. A number's first call creates a trial user; signing in on the web (phone OTP)
makes it active and adds the sign-up bonus once per number, ever (deleted numbers leave a hashed tombstone).

Everything a number did before signing in (calls, documents) is already that user's: calls are linked to the
user when they are admitted, so signing in "links" nothing by copying, it only changes the plan.

An account can add more numbers (user_phones), each proven by a code sent to it. A linked number is the account:
its calls are the account's calls and it signs in to the account. What it made before it was linked shows in the
account while it stays linked (read by number, nothing is moved).
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from . import ledger
from .pricing import PRICING, Pricing


def tombstone(phone: str) -> str:
    return hashlib.sha256(f"boldoc-phone:{phone}".encode()).hexdigest()


LINKED = "select u.* from user_phones p join users u on u.id = p.user_id where p.phone_e164=$1"


async def by_phone(con, phone: str) -> Optional[Dict[str, Any]]:
    """The account a number belongs to: the one it was linked to, else its own."""
    r = await con.fetchrow(LINKED, phone) or await con.fetchrow("select * from users where phone_e164=$1", phone)
    return dict(r) if r else None


async def by_id(con, user_id: int) -> Optional[Dict[str, Any]]:
    r = await con.fetchrow("select * from users where id=$1", user_id)
    return dict(r) if r else None


async def ensure_caller(con, phone: str) -> Dict[str, Any]:
    """The user for a calling number (or the account it was linked to), created as a trial user on its first call."""
    user = await by_phone(con, phone)
    if user is not None:
        return user
    await con.execute("insert into users (phone_e164, source) values ($1, 'call') on conflict (phone_e164) do nothing",
                      phone)
    return await by_phone(con, phone)


async def activate(con, phone: str, pricing: Pricing = PRICING) -> Dict[str, Any]:
    """Verified on the web: the number becomes an active account, with the sign-up bonus the first time."""
    async with con.transaction():
        linked = await con.fetchrow(LINKED, phone)
        if linked is not None:  # a linked number signs in to the account it was added to
            return dict(linked)
        await con.execute("insert into users (phone_e164, source) values ($1, 'web') on conflict (phone_e164) "
                          "do nothing", phone)
        user = dict(await con.fetchrow("select * from users where phone_e164=$1 for update", phone))
        if user["status"] in ("suspended", "deleted"):
            return user
        if user["status"] != "active":
            await con.execute("update users set status='active', verified_at=coalesce(verified_at, now()) "
                              "where id=$1", user["id"])
        reborn = await con.fetchval("select exists(select 1 from phone_tombstones where hash=$1)", tombstone(phone))
        if not reborn and pricing.signup_bonus > 0:
            await ledger.post(con, user["id"], "signup_bonus", pricing.signup_bonus, f"signup:{user['id']}",
                              reason="sign-up gift")
        return dict(await con.fetchrow("select * from users where id=$1", user["id"]))


async def allowance(con, user: Optional[Dict[str, Any]], pricing: Pricing = PRICING) -> Dict[str, Any]:
    """What this caller may do on a new call: the plan, what's left, the call's time limit, or why not."""
    if user is None:  # withheld number: a short anonymous call (no SMS possible anyway)
        return {"plan": "anonymous", "max_call_s": pricing.trial_max_call_s, "refuse": None}
    if user["status"] in ("suspended", "deleted"):
        return {"plan": user["status"], "max_call_s": 0, "refuse": "suspended"}
    if user["status"] == "trial":
        used = await con.fetchval("select count(*) from call_records where user_id=$1 and counted", user["id"])
        left = max(0, pricing.trial_calls - int(used or 0))
        return {"plan": "trial", "calls_left": left, "max_call_s": pricing.trial_max_call_s,
                "refuse": None if left > 0 else "trial_over"}
    bal = await ledger.balance(con, user["id"])
    if bal < pricing.minute:
        return {"plan": "credits", "balance": bal, "max_call_s": 0, "refuse": "no_credits"}
    return {"plan": "credits", "balance": bal, "refuse": None,
            "max_call_s": min(pricing.max_call_s, max(60, bal // pricing.minute * 60))}


async def phones(con, user: Dict[str, Any]) -> List[str]:
    """Every number of an account: the one it signs in with, then the ones added to it."""
    rows = await con.fetch("select phone_e164 from user_phones where user_id=$1 order by added_at", user["id"])
    return [user["phone_e164"]] + [r["phone_e164"] for r in rows]


async def can_link(con, user: Dict[str, Any], phone: str, pricing: Pricing = PRICING) -> Optional[str]:
    """Why this number can't be added to this account, or None. Already on it: None (adding again changes nothing)."""
    if phone == user["phone_e164"]:
        return "own_number"
    owner = await con.fetchval("select user_id from user_phones where phone_e164=$1", phone)
    if owner is not None:
        return None if owner == user["id"] else "other_account"
    if await con.fetchval("select status from users where phone_e164=$1", phone) in ("active", "suspended"):
        return "other_account"  # it signs in to an account of its own
    n = await con.fetchval("select count(*) from user_phones where user_id=$1", user["id"])
    return "too_many" if n >= pricing.linked_numbers else None


async def link(con, user: Dict[str, Any], phone: str, pricing: Pricing = PRICING) -> Optional[str]:
    """Add a number whose code was just checked. None when it is on the account; else why not (see can_link)."""
    async with con.transaction():
        await con.execute("select id from users where id=$1 for update", user["id"])  # one change at a time
        why = await can_link(con, user, phone, pricing)
        if why:
            return why
        await con.execute("insert into user_phones (phone_e164, user_id) values ($1, $2) on conflict (phone_e164) "
                          "do nothing", phone, user["id"])
        owner = await con.fetchval("select user_id from user_phones where phone_e164=$1", phone)
        return None if owner == user["id"] else "other_account"  # another account won a race for it


async def unlink(con, user: Dict[str, Any], phone: str) -> bool:
    """Take a number off the account (never the sign-in number). Its calls from now on are its own again."""
    return (await con.execute("delete from user_phones where user_id=$1 and phone_e164=$2", user["id"],
                              phone)).endswith(" 1")
