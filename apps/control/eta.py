"""How long a long document takes to write, from the last ones written: what BolDoc says before and after it starts one,
instead of a fixed "10 minutes".

The estimate is the middle half (25th to 75th percentile) of the last runs of the same kind (decks, or other documents),
timed from task.accepted to task.completed in the event log. With fewer than 3 runs it is the measured default below.
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

DEFAULTS: Dict[str, Tuple[int, int]] = {"slides": (60, 120), "pages": (180, 300)}
MIN_RUNS = 3
RECENT = 20        # runs of a kind that count
CACHE_S = 600.0    # the event log is read at most this often
_cache: Dict[str, Tuple[float, Tuple[int, int]]] = {}

QUERY = """
with acc as (select data->>'task_id' t, (data->>'ts')::float ts, data->'payload' p from events
             where data->>'type' = 'task.accepted' and data->'payload'->>'tier' = 'T3'),
     done as (select data->>'task_id' t, (data->>'ts')::float ts from events where data->>'type' = 'task.completed')
select a.p::text payload, d.ts - a.ts secs from acc a join done d on d.t = a.t order by a.ts desc limit 200"""


def unit_of(payload: dict) -> str:
    """slides or pages: from the run's own record, else its first format (a deck is pptx first)."""
    unit = payload.get("unit")
    if unit in DEFAULTS:
        return unit
    if unit == "sheets":
        return "pages"
    formats = payload.get("formats") or []
    return "slides" if formats and formats[0] == "pptx" else "pages"


def spread(secs: Sequence[float]) -> Tuple[int, int]:
    """The 25th and 75th percentile, in whole seconds."""
    s = sorted(secs)

    def at(p: float) -> float:
        return s[min(len(s) - 1, max(0, round(p * (len(s) - 1))))]
    return int(at(0.25)), math.ceil(at(0.75))


def minutes(lo_s: int, hi_s: int) -> Tuple[int, int]:
    """As said on the call: "lagbhag 3-5 minute" (never "0", and the top of the range above the bottom)."""
    lo = max(1, round(lo_s / 60))
    return lo, max(lo + 1, math.ceil(hi_s / 60))


async def estimate(pool: Any, unit: Optional[str]) -> Tuple[int, int]:
    """(lo, hi) seconds a long document of this unit takes to write."""
    unit = unit if unit in DEFAULTS else "pages"
    now = time.monotonic()
    hit = _cache.get(unit)
    if hit is not None and now - hit[0] < CACHE_S:
        return hit[1]
    est = DEFAULTS[unit]
    if pool is not None:
        try:
            async with pool.acquire() as con:
                rows = await con.fetch(QUERY)
            secs: List[float] = []
            for r in rows:
                payload = json.loads(r["payload"]) if isinstance(r["payload"], str) else (r["payload"] or {})
                if r["secs"] and r["secs"] > 0 and unit_of(payload) == unit:
                    secs.append(float(r["secs"]))
            if len(secs) >= MIN_RUNS:
                est = spread(secs[:RECENT])
        except Exception as e:  # the default is a fine answer; the call never waits on this
            logger.warning(f"eta: past runs not read ({type(e).__name__}); using the default for {unit}")
    _cache[unit] = (now, est)
    return est
