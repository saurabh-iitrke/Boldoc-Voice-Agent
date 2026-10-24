"""Calling the caller back when Hermes needs answers they didn't give before hanging up.

Only after their own yes on the call (the brief's callback_ok), only to the number that asked for the document, only
between 09:00 and 21:00 IST, at most CALLBACKS_PER_DAY to a number, and never after they asked not to be called. It
reuses "Call me" (accounts/callme.py): a single-use token in Redis, Plivo's Call API from our number (with machine
detection: a voicemail is no answer), and admission redeems the token (calls.admit), where a callback is free and
never refused by fair use. The attempt's status is Call me's: calling, answered or refused; still calling after the
ring time means nobody picked up.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Protocol

from loguru import logger

from .accounts import callme

IST = timezone(timedelta(hours=5, minutes=30))
HOURS = (9, 21)            # IST: a callback rings from 09:00 to 20:59
RING_S = callme.RING_S      # how long Plivo rings (40 s)
NO_ANSWER_AFTER_S = RING_S + 25
RETRY_AFTER_S = 8 * 60     # one retry, this long after a callback nobody answered
CALLBACKS_PER_DAY = 3      # to one number
TOKEN_TTL_S = 120          # Plivo fetches the answer URL when the call is picked up
MAX_CALL_S = 300           # a callback asks a few questions: five minutes at most


class Dialer(Protocol):
    async def place(self, number: str, token: str) -> str: ...


class PlivoDialer:
    async def place(self, number: str, token: str) -> str:
        return await callme.place_call(number, token, machine_detection=True)


def in_hours(now: Optional[float] = None) -> bool:
    hour = datetime.fromtimestamp(time.time() if now is None else now, IST).hour
    return HOURS[0] <= hour < HOURS[1]


def _number_key(kind: str, number: str) -> str:
    return f"boldoc:cb:{kind}:" + hashlib.sha256(number.encode()).hexdigest()[:24]


class Callbacks:
    def __init__(self, bus, dialer: Optional[Dialer] = None, clock=time.time):
        self.bus, self.dialer, self.clock = bus, dialer or PlivoDialer(), clock

    @property
    def _r(self):
        return getattr(self.bus, "r", None)

    async def opted_out(self, number: str) -> bool:
        return bool(self._r is not None and await self._r.exists(_number_key("off", number)))

    async def opt_out(self, number: str) -> None:
        """"Mujhe call mat karna": no more callbacks to this number."""
        if self._r is not None:
            await self._r.set(_number_key("off", number), "1")

    async def why_not(self, number: Optional[str]) -> Optional[str]:
        """Why a callback can't ring now (None: it can)."""
        if not number:
            return "no_number"
        if self._r is None:
            return "no_redis"
        if not in_hours(self.clock()):
            return "quiet_hours"
        if await self.opted_out(number):
            return "opted_out"
        used = await self._r.get(_number_key("day", number))
        if used is not None and int(used) >= CALLBACKS_PER_DAY:
            return "daily_cap"
        return None

    async def place(self, *, user_id: int, number: str, task_id: str, doc_id: str, title: str, n: int,
                    lang: Optional[str]) -> Dict[str, Any]:
        """Ring the caller about their document's questions: {"id", "placed_at"} or {"refused": reason}."""
        why = await self.why_not(number)
        if why:
            return {"refused": why}
        rid, token = secrets.token_urlsafe(9), secrets.token_urlsafe(18)
        await self._r.set(callme._token_key(token), json.dumps({
            "user_id": user_id, "number": number, "id": rid, "lang": lang, "kind": "callback", "task_id": task_id,
            "doc_id": doc_id, "title": title, "n": n}), ex=TOKEN_TTL_S)
        await self._r.set(callme._status_key(rid), json.dumps({"user_id": user_id, "status": "calling"}),
                          ex=callme.STATUS_TTL_S)
        day = _number_key("day", number)
        await self._r.incr(day)
        await self._r.expire(day, 86400)
        try:
            await self.dialer.place(number, token)
        except Exception as e:  # Plivo refused or unreachable: counted as a failed attempt
            await self._r.delete(callme._token_key(token))
            logger.warning(f"callback: couldn't place the call ({type(e).__name__})")
            return {"refused": "dial_failed"}
        logger.info(f"callback: calling …{number[-4:]} about task {task_id[-6:]} ({n} questions)")
        return {"id": rid, "placed_at": self.clock()}

    async def status(self, attempt: Dict[str, Any]) -> str:
        """calling | answered | refused | no_answer (still ringing after the ring time: nobody picked up)."""
        raw = await self._r.get(callme._status_key(attempt["id"])) if self._r is not None else None
        st = (json.loads(raw) if raw else {}).get("status", "no_answer")
        if st == "calling" and self.clock() - attempt["placed_at"] > NO_ANSWER_AFTER_S:
            return "no_answer"
        return st
