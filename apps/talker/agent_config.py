"""The agent's settings for one call: published from the admin console, read from control when a call starts,
and kept for the whole call (a publish never changes a call already running). If control can't be reached, the last
settings read are used, else today's defaults, so a call never waits on this.

The keys and their ranges are control's (apps/control/agent_config.py); this side only reads them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Optional, Tuple

import httpx
from loguru import logger

from .settings import SETTINGS
from .voices import voice_map


@dataclass(frozen=True)
class CallSettings:
    version: Any = 0                       # the published version a call ran with ("draft" for a test of one)
    noise_cancellation: bool = field(default_factory=lambda: SETTINGS.noise_cancellation)
    noise_cancellation_level: int = field(default_factory=lambda: SETTINGS.noise_cancellation_level)
    default_language: str = "en-IN"        # en-IN | hinglish: a new caller's first language
    voices: Dict[str, Tuple[str, str]] = field(default_factory=voice_map)
    unfinished_hold_s: float = 3.5         # quiet through an unfinished sentence
    listening_sound: bool = True           # "हम्म" while the caller pauses
    listening_sound_after_s: float = 1.2
    short_answer_s: float = 0.5            # a short whole answer ends the turn after this much silence
    reask_after_s: float = 6               # an unanswered question is asked again after
    idle_check_after_s: float = 12         # "anything else?" after quiet, once the work is done
    idle_goodbye_after_s: float = 10       # and a goodbye after that
    offer_hang_up: bool = True             # a big document: offer to end the call (its link comes by SMS)
    wait_tone: bool = True                 # two soft notes while a caller waits on the line
    progress_lines: bool = True            # a big document's progress, while the caller is quiet
    progress_every_s: float = 40


_KEYS = ("noise_cancellation", "noise_cancellation_level", "default_language", "unfinished_hold_s", "listening_sound",
         "listening_sound_after_s", "short_answer_s", "reask_after_s", "idle_check_after_s", "idle_goodbye_after_s",
         "offer_hang_up", "wait_tone", "progress_lines", "progress_every_s")


def from_published(version: Any, settings: Dict[str, Any]) -> CallSettings:
    """Control's talker view ({key: value}) as a call's settings; anything missing stays at today's default."""
    base = CallSettings()
    voices = dict(base.voices)
    for key, value in settings.items():
        if key.startswith("voice_") and isinstance(value, str) and "/" in value:
            model, _, speaker = value.partition("/")
            voices[key[len("voice_"):]] = (model, speaker)
    known = {k: settings[k] for k in _KEYS if k in settings}
    return replace(base, version=version, voices=voices, **known)


_last: Optional[CallSettings] = None
_last_at = 0.0


async def for_call(draft_id: Optional[str] = None, client: Optional[httpx.AsyncClient] = None,
                   timeout_s: float = 1.5) -> CallSettings:
    """The settings a call starting now runs with: the live version (or a draft, for a test call of it)."""
    global _last, _last_at
    path = f"/internal/agent-config/draft/{draft_id}" if draft_id else "/internal/agent-config"
    try:
        own = client is None
        http = client or httpx.AsyncClient(base_url=SETTINGS.control_url,
                                           headers={"X-Internal-Key": SETTINGS.internal_key})
        try:
            r = await http.get(path, timeout=timeout_s)
            r.raise_for_status()
            body = r.json()
        finally:
            if own:
                await http.aclose()
        settings = from_published(body.get("version", 0), body.get("settings") or {})
        if not draft_id:
            _last, _last_at = settings, time.monotonic()
        return settings
    except Exception as e:  # never hold a call up for this
        logger.warning(f"agent settings: using {'the last read' if _last else 'the defaults'} ({type(e).__name__})")
        return _last or CallSettings()
