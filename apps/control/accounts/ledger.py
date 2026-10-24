"""The credit ledger: append-only, one row per credit or debit, balance derived from the last row.

Every write takes the user's row lock (`select ... for update`), so concurrent debits (a call ending while a
document commits) serialize, and carries a unique idempotency key, so a retried task or webhook never charges
twice. Debits may take the balance a little below zero within a call (a document is never cut off half-written);
admission refuses new calls at zero.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


async def balance(con, user_id: int) -> int:
    v = await con.fetchval("select balance_after from credit_ledger where user_id=$1 order by id desc limit 1",
                           user_id)
    return int(v or 0)


async def post(con, user_id: int, kind: str, amount: int, idem_key: str, *, ref: Optional[str] = None,
               actor: str = "system", reason: Optional[str] = None) -> Dict[str, Any]:
    """Credit (+) or debit (-) once per idem_key; returns the ledger row (the earlier one on a retry).
    Inside the caller's transaction when there is one (a debit commits together with what it pays for)."""
    if not con.is_in_transaction():
        async with con.transaction():
            return await _post(con, user_id, kind, amount, idem_key, ref, actor, reason)
    return await _post(con, user_id, kind, amount, idem_key, ref, actor, reason)


async def _post(con, user_id: int, kind: str, amount: int, idem_key: str, ref: Optional[str], actor: str,
                reason: Optional[str]) -> Dict[str, Any]:
    existing = await con.fetchrow("select * from credit_ledger where idem_key=$1", idem_key)
    if existing is not None:
        return dict(existing)
    await con.execute("select 1 from users where id=$1 for update", user_id)
    existing = await con.fetchrow("select * from credit_ledger where idem_key=$1", idem_key)  # raced and lost
    if existing is not None:
        return dict(existing)
    after = await balance(con, user_id) + amount
    row = await con.fetchrow(
        "insert into credit_ledger (user_id, kind, amount, balance_after, idem_key, ref, actor, reason) "
        "values ($1,$2,$3,$4,$5,$6,$7,$8) returning *", user_id, kind, amount, after, idem_key, ref, actor, reason)
    return dict(row)


async def history(con, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    rows = await con.fetch("select id, kind, amount, balance_after, ref, reason, created_at from credit_ledger "
                           "where user_id=$1 order by id desc limit $2", user_id, limit)
    return [dict(r) for r in rows]
