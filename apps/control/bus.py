"""Redis for the control plane: fan-out of durable events (website live view, other instances) and rate limits.
Optional by design: without REDIS_URL everything still works on one instance (no fan-out, no limits)."""
from __future__ import annotations

import json
import time
from typing import List, Optional

from loguru import logger

from protocol.bus import ACTIVE_CALLS, CALL_LLM, PRESENCE_TTL_S, events_channel

try:
    import redis.asyncio as aioredis
except ImportError:  # local 3.9 test env without redis: the bus is simply off
    aioredis = None


class Bus:
    def __init__(self, url: str = ""):
        self.r = aioredis.from_url(url, decode_responses=True) if (url and aioredis is not None) else None

    @property
    def enabled(self) -> bool:
        return self.r is not None

    async def ping(self) -> bool:
        if self.r is None:
            return False
        try:
            return bool(await self.r.ping())
        except Exception:
            return False

    async def publish_events(self, rows: List[tuple]) -> None:
        """rows: (session_id, seq, event_json) just made durable by the store."""
        if self.r is None or not rows:
            return
        try:
            async with self.r.pipeline(transaction=False) as p:
                for sid, _seq, data in rows:
                    p.publish(events_channel(sid), data)
                await p.execute()
        except Exception as e:
            logger.warning(f"event fan-out failed: {type(e).__name__}")  # durable in Postgres regardless

    async def allow(self, key: str, limit: int, window_s: int, fail_open: bool = True) -> bool:
        """Fixed-window counter. INCR and EXPIRE NX run in one transaction, so a key can never be left without a
        TTL (which would lock it forever). Per-call caps fail open (a Redis outage must never break a call);
        anything that costs money passes fail_open=False."""
        if self.r is None:
            return fail_open
        try:
            k = f"boldoc:limit:{key}"
            async with self.r.pipeline(transaction=True) as p:
                p.incr(k)
                p.expire(k, window_s, nx=True)
                n, _ = await p.execute()
            return n <= limit
        except Exception as e:
            logger.warning(f"rate limit check {'skipped' if fail_open else 'failed closed'}: {type(e).__name__}")
            return fail_open

    # ------------------------------------------------------------------ calls in progress (admission control)
    async def active_calls(self) -> List[str]:
        """Calls with a fresh heartbeat. Empty when Redis is unreachable: admission then fails open."""
        if self.r is None:
            return []
        try:
            now = time.time()
            async with self.r.pipeline(transaction=True) as p:
                p.zremrangebyscore(ACTIVE_CALLS, "-inf", now - PRESENCE_TTL_S)
                p.zrangebyscore(ACTIVE_CALLS, now - PRESENCE_TTL_S, "+inf")
                _, members = await p.execute()
            return list(members)
        except Exception as e:
            logger.warning(f"active calls unknown: {type(e).__name__}")
            return []

    async def mark_active(self, call_uuid: str, llm: str = "primary") -> None:
        if self.r is not None:
            try:
                async with self.r.pipeline(transaction=True) as p:
                    p.zadd(ACTIVE_CALLS, {call_uuid: time.time()})
                    p.hset(CALL_LLM, call_uuid, llm)
                    await p.execute()
            except Exception as e:
                logger.warning(f"call presence not recorded: {type(e).__name__}")

    async def clear_active(self, call_uuid: str) -> None:
        if self.r is not None:
            try:
                async with self.r.pipeline(transaction=True) as p:
                    p.zrem(ACTIVE_CALLS, call_uuid)
                    p.hdel(CALL_LLM, call_uuid)
                    await p.execute()
            except Exception as e:
                logger.warning(f"call presence not cleared: {type(e).__name__}")

    async def primary_count(self, call_uuids: List[str]) -> int:
        """How many of these calls in progress use the primary LLM (its rate limit is the scarce one)."""
        if self.r is None or not call_uuids:
            return 0
        try:
            return sum(1 for v in await self.r.hmget(CALL_LLM, call_uuids) if v in (None, "primary"))
        except Exception as e:
            logger.warning(f"call LLM split unknown: {type(e).__name__}")
            return len(call_uuids)

    async def get_json(self, key: str) -> Optional[dict]:
        if self.r is None:
            return None
        raw = await self.r.get(key)
        return json.loads(raw) if raw else None

    async def close(self) -> None:
        if self.r is not None:
            await self.r.aclose()
