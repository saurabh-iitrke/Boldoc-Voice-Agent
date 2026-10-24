"""Per-call trace: a structured timeline of everything the talker did, shipped to the control plane at call end.

Each event is {"t": seconds since the call started (monotonic), "e": name, ...fields}. The control plane joins it with
the thinker's task timings and SMS receipts and builds the call report (latency per turn, dead air, talk-over, false
claims, thinker speed). Words appear only when `content` is allowed.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


class CallTrace:
    MAX_EVENTS = 5000

    def __init__(self, call_id: str, content: bool = False):
        self.call_id = call_id
        self.content = content
        self.started_wall = time.time()
        self.started_mono = time.monotonic()
        self.events: List[Dict[str, Any]] = []

    def at(self, mono: Optional[float] = None) -> float:
        return round((mono if mono is not None else time.monotonic()) - self.started_mono, 3)

    def add(self, e: str, mono: Optional[float] = None, text: Optional[str] = None, **fields: Any) -> None:
        if len(self.events) >= self.MAX_EVENTS:
            return
        ev: Dict[str, Any] = {"t": self.at(mono), "e": e, **{k: v for k, v in fields.items() if v is not None}}
        if text is not None and self.content:
            ev["text"] = text[:500]
        self.events.append(ev)

    def payload(self) -> Dict[str, Any]:
        return {"call_id": self.call_id, "started_wall": self.started_wall, "content": self.content,
                "events": self.events}
