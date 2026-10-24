"""What the caller has actually heard, from Plivo's own confirmations.

Pipecat sends a reply's audio at real-time pace and calls the reply finished ("bot stopped speaking") when its last
chunk has gone out; Plivo still has the network's worth of it to play. So a checkpoint follows each reply's last
audio, and Plivo answers it with playedStream once everything before it has played. Pipecat also sends clearAudio on
every interruption, which Plivo answers with clearedAudio once its queue is empty. Plivo's docs don't say whether a
checkpoint still queued at a clear is answered; either way that reply was not heard to its end.

The trace records how long each confirmation took after its checkpoint went out ("played") and after the clear
("audio_cleared"). The session hangs up once the goodbye has played (heard_since); a line that confirms nothing (the
scripted test caller before it learned to, or Plivo not answering) keeps the estimate.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Set


class Playout:
    def __init__(self, trace: Any):
        self.trace = trace
        self._n = 0
        self._sent: Dict[str, float] = {}  # checkpoints out, not yet confirmed: name -> when it went out
        self._cleared: Set[str] = set()    # out when the caller cut in: never counted as heard
        self._clears: List[float] = []     # clearAudio sent, not yet confirmed (oldest first)
        self._played_upto = 0.0            # when the newest confirmed checkpoint went out
        self.confirmed = 0

    def name(self) -> str:
        """A name for the checkpoint behind the reply being queued now."""
        self._n += 1
        return f"r{self._n}"

    def sent(self, name: str) -> None:
        """The checkpoint went out, right behind its reply's last audio."""
        self._sent[name] = time.monotonic()

    def cut(self) -> None:
        """clearAudio went out (an interruption): whatever Plivo still had queued is never heard."""
        self._clears.append(time.monotonic())
        self._cleared.update(self._sent)
        self._sent.clear()

    def on_event(self, msg: Dict[str, Any]) -> None:
        """playedStream or clearedAudio from Plivo."""
        now = time.monotonic()
        if msg.get("event") == "playedStream":
            name = str(msg.get("name") or "")
            if name in self._cleared:
                self._cleared.discard(name)
                self.trace.add("played_after_clear", name=name)
            elif name in self._sent:
                sent_at = self._sent.pop(name)
                self.confirmed += 1
                self._played_upto = max(self._played_upto, sent_at)
                self.trace.add("played", name=name, ms=round((now - sent_at) * 1000))
        elif msg.get("event") == "clearedAudio" and self._clears:
            self.trace.add("audio_cleared", ms=round((now - self._clears.pop(0)) * 1000))

    async def heard_since(self, since: float, wait_s: float = 2.0, estimate_s: float = 0.6) -> str:
        """Wait until a reply that started playing at `since` has been heard to its end: Plivo has confirmed a
        checkpoint sent after that. A line that has confirmed none gets the estimate: `estimate_s` after the reply's
        last audio went out. Returns "played", "timeout" or "estimate"."""
        if not self.confirmed:
            await asyncio.sleep(estimate_s)
            return "estimate"
        deadline = time.monotonic() + wait_s
        while self._played_upto < since:
            if time.monotonic() >= deadline:
                return "timeout"
            await asyncio.sleep(0.02)
        return "played"
