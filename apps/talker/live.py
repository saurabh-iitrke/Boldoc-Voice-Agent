"""Live call feed for the website's call view: caller text, BolDoc's replies and agent state, published to Redis.
Best effort by design: never awaited on the audio path, and a Redis outage never affects the call."""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Optional, Set

from protocol.bus import ACTIVE_CALLS, HEARTBEAT_S, live_channel

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None


class LiveFeed:
    def __init__(self, url: str, session_id: str):
        self.call_id = session_id
        self.channel = live_channel(session_id)
        self.r = aioredis.from_url(url, decode_responses=True) if (url and aioredis is not None) else None
        self._pending: Set[asyncio.Task] = set()
        self.state: Optional[str] = None

    def publish(self, kind: str, **data) -> None:
        if self.r is None:
            return
        payload = json.dumps({"t": round(time.time(), 3), "kind": kind, **data}, ensure_ascii=False)
        task = asyncio.get_event_loop().create_task(self._send(payload))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def set_state(self, state: str) -> None:
        """listening | thinking | speaking | writing — only changes are published."""
        if state != self.state:
            self.state = state
            self.publish("state", state=state)

    async def _send(self, payload: str) -> None:
        with contextlib.suppress(Exception):
            await self.r.publish(self.channel, payload)

    async def presence(self) -> None:
        """Heartbeat while the call runs: admission counts calls with a fresh heartbeat (protocol.bus)."""
        if self.r is None:
            return
        while True:
            with contextlib.suppress(Exception):
                await self.r.zadd(ACTIVE_CALLS, {self.call_id: time.time()})
            await asyncio.sleep(HEARTBEAT_S)

    async def close(self) -> None:
        if self.r is not None:
            await asyncio.gather(*self._pending, return_exceptions=True)
            with contextlib.suppress(Exception):
                await self.r.zrem(ACTIVE_CALLS, self.call_id)
            await self.r.aclose()
