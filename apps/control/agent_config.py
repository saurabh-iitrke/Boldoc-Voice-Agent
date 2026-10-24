"""Agent settings published from the admin console: a whitelist of what may change without a deploy, each
range-checked, every publish a version with a note and an audit entry, any version restorable.

The groups chosen with them: call and voice, timing and waiting, prices and plan limits. The defaults are today's values
(the environment and the code's constants), so nothing changes until the first publish.

A call keeps the settings it started with: the talker takes a snapshot when a call starts (apps/talker/
agent_config.py); control applies prices and limits from the next admission or charge.
"""
from __future__ import annotations

import dataclasses
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

from .accounts.pricing import PRICING

V4, V3 = "bulbul:v4-flash", "bulbul:v3"


def _voices(*pairs: Tuple[str, str, str]) -> List[Tuple[str, str]]:
    return [(f"{model}/{speaker}", label) for model, speaker, label in pairs]


# The voices measured intelligible on 8 kHz phone audio (tools/voices/voice_eval.py: 2% character errors or less),
# female only: BolDoc speaks of herself in the feminine ("likh rahi hoon").
VOICES: Dict[str, List[Tuple[str, str]]] = {
    "hinglish": _voices((V4, "simran_enhi_customer", "Simran (Hindi-English)"),
                        (V4, "simran_hi_conversation", "Simran (Hindi, conversational)"),
                        (V4, "kavya_hi_conversational", "Kavya"), (V4, "aparna_hi_customer", "Aparna"),
                        (V4, "aarti_hi_customer", "Aarti"), (V4, "ishita_enhi_customer", "Ishita"),
                        (V4, "shalini_enhi_customer", "Shalini")),
    "en-IN": _voices((V4, "simran_en_customer", "Simran"), (V4, "aparna_en_companion", "Aparna"),
                     (V4, "sanchita_en_companion", "Sanchita"), (V4, "ishita_en_customer", "Ishita"),
                     (V4, "simran_enhi_customer", "Simran (Hindi-English)")),
    "ta-IN": _voices((V3, "simran", "Simran"), (V3, "priya", "Priya"), (V3, "kavya", "Kavya")),
    "te-IN": _voices((V3, "simran", "Simran"), (V4, "kavitha_te_conversation", "Kavitha"),
                     (V4, "pooja_te_conversation", "Pooja")),
    "kn-IN": _voices((V3, "simran", "Simran"), (V4, "chaitra_kn_conversation", "Chaitra")),
    "mr-IN": _voices((V3, "simran", "Simran"), (V4, "ishita_mr_conversational", "Ishita")),
    "bn-IN": _voices((V3, "simran", "Simran"), (V4, "roopa_bn_conversational", "Roopa")),
    "gu-IN": _voices((V3, "simran", "Simran"), (V4, "pooja_gu_conversational", "Pooja"),
                     (V4, "pooja_gu_customer", "Pooja (customer)")),
    "pa-IN": _voices((V3, "simran", "Simran")),
    "ml-IN": _voices((V3, "simran", "Simran"), (V3, "priya", "Priya"), (V3, "kavya", "Kavya")),
    "od-IN": _voices((V3, "simran", "Simran"), (V3, "priya", "Priya"), (V3, "kavya", "Kavya")),
}
LANGUAGE_NAMES = {"hinglish": "Hindi and Hinglish", "en-IN": "English", "ta-IN": "Tamil", "te-IN": "Telugu",
                  "kn-IN": "Kannada", "mr-IN": "Marathi", "bn-IN": "Bengali", "gu-IN": "Gujarati", "pa-IN": "Punjabi",
                  "ml-IN": "Malayalam", "od-IN": "Odia"}


@dataclass(frozen=True)
class Setting:
    key: str
    group: str                  # call | timing | prices
    label: str
    help: str
    kind: str                   # bool | int | float | choice
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    unit: str = ""
    choices: Tuple[Tuple[str, str], ...] = ()
    applies: str = "talker"     # talker (from the next call) | control (from the next admission or charge)
    pricing: str = ""           # the Pricing field it sets (control)
    scale: int = 1              # Pricing value = setting x scale (minutes -> seconds)

    def as_dict(self) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        d["choices"] = [{"value": v, "label": lab} for v, lab in self.choices]
        d.pop("pricing")
        d.pop("scale")
        return d


BASE = dataclasses.replace(PRICING)  # what the environment set, before any publish


def _price(key, label, help_, field_, lo, hi, unit="₹", scale=1, group="prices") -> Setting:
    return Setting(key, group, label, help_, "int", getattr(BASE, field_) // scale, lo, hi, 1, unit,
                   applies="control", pricing=field_, scale=scale)


SCHEMA: List[Setting] = [
    # Call and voice
    Setting("noise_cancellation", "call", "Plivo noise cancellation",
            "Plivo cleans the caller's audio before BolDoc hears it.", "bool",
            os.environ.get("NOISE_CANCELLATION", "1") != "0"),
    Setting("noise_cancellation_level", "call", "Noise cancellation level",
            "Higher removes more background noise; lower if voices sound distorted.", "int",
            min(100, max(60, int(os.environ.get("NOISE_CANCELLATION_LEVEL", "95")))), 60, 100, 1),
    _price("max_call_minutes", "Longest call", "For signed-up callers; BolDoc says goodbye at the limit.",
           "max_call_s", 5, 30, "min", 60, "call"),
    Setting("default_language", "call", "Language for a new caller",
            "BolDoc greets and talks in this until the caller uses another.", "choice", "en-IN",
            choices=(("en-IN", "English"), ("hinglish", "Hindi and Hinglish"))),
    *[Setting(f"voice_{fam}", "call", f"Voice: {LANGUAGE_NAMES[fam]}", "Measured clear on phone lines.", "choice",
              VOICES[fam][0][0], choices=tuple(VOICES[fam])) for fam in VOICES],
    # Timing and waiting
    Setting("unfinished_hold_s", "timing", "Wait through an unfinished sentence",
            "How long BolDoc stays quiet when the caller stops mid-sentence.", "float", 3.5, 2.0, 5.0, 0.1, "s"),
    Setting("listening_sound", "timing", "\"हम्म\" while the caller pauses",
            "A short listening sound, so a pause doesn't feel like a dropped line.", "bool", True),
    Setting("listening_sound_after_s", "timing", "Say \"हम्म\" after", "", "float", 1.2, 0.8, 2.0, 0.1, "s"),
    Setting("short_answer_s", "timing", "A short answer ends the turn after",
            "\"Haan\", \"PDF\", a name: BolDoc answers after this much silence.", "float", 0.5, 0.3, 1.0, 0.05, "s"),
    Setting("reask_after_s", "timing", "Ask an unanswered question again after", "", "int", 6, 4, 15, 1, "s"),
    Setting("idle_check_after_s", "timing", "\"Anything else?\" after quiet", "Once the work is done.", "int", 12,
            8, 30, 1, "s"),
    Setting("idle_goodbye_after_s", "timing", "Then a goodbye after", "", "int", 10, 6, 30, 1, "s"),
    Setting("offer_hang_up", "timing", "Offer to hang up for a big document",
            "\"Call rakh dijiye, ready hote hi SMS pe link bhej dungi\".", "bool", True),
    Setting("wait_tone", "timing", "Wait tone", "Two soft notes every few seconds while a caller waits on the line.",
            "bool", True),
    Setting("progress_lines", "timing", "Say a big document's progress",
            "\"9 mein se 4 hisse likh liye hain\", only while the caller is quiet.", "bool", True),
    Setting("progress_every_s", "timing", "Progress at most every", "", "int", 40, 20, 120, 5, "s"),
    Setting("big_documents", "timing", "Big documents written by Hermes",
            "Off: reports, plans and decks are written by the short writer.", "bool", True, applies="control"),
    Setting("web_research", "timing", "Web research for big documents",
            "For the types that use it (reports, plans, decks).", "bool", True, applies="control"),
    # Prices and plan limits (1 credit = ₹1)
    _price("price_minute", "A call minute", "", "minute", 1, 20),
    _price("price_doc", "A new document", "", "doc_create", 1, 100),
    _price("price_edit", "A change to a document", "Undo is always free.", "doc_edit", 0, 20),
    _price("price_big_doc", "A big document", "", "doc_create_big", 10, 500),
    _price("signup_bonus", "Sign-up bonus", "Added once per number when it signs up.", "signup_bonus", 0, 5000),
    _price("trial_calls", "Free calls before sign-up", "", "trial_calls", 0, 10, ""),
    _price("trial_call_minutes", "Minutes in a free call", "", "trial_max_call_s", 1, 15, "min", 60),
    _price("trial_docs_per_call", "New documents in a free call", "", "trial_docs_per_call", 1, 5, ""),
    _price("trial_edits_per_doc", "Changes to a free call's document", "", "trial_edits_per_doc", 0, 10, ""),
    _price("burst_calls", "Calls from one number in any 30 minutes", "", "burst_calls", 1, 10, ""),
    _price("trial_calls_per_day", "Free calls from one number a day", "", "trial_calls_per_day", 1, 10, ""),
    _price("trial_minutes_per_day", "Free minutes for one number a day", "", "trial_minutes_per_day", 1, 60, "min"),
    _price("calls_per_day", "Signed-up calls from one number a day", "", "calls_per_day", 1, 100, ""),
    _price("minutes_per_day", "Signed-up minutes for one number a day", "", "minutes_per_day", 10, 600, "min"),
    _price("trial_minutes_budget", "All free callers' minutes a day", "Together, before trial calls are turned away.",
           "trial_minutes_budget", 10, 2000, "min"),
]
BY_KEY: Dict[str, Setting] = {s.key: s for s in SCHEMA}
TALKER_KEYS = [s.key for s in SCHEMA if s.applies == "talker"]


def defaults() -> Dict[str, Any]:
    return {s.key: s.default for s in SCHEMA}


def _coerce(s: Setting, value: Any) -> Tuple[Any, Optional[str]]:
    if s.kind == "bool":
        return (value, None) if isinstance(value, bool) else (None, "on or off")
    if s.kind == "choice":
        return (value, None) if value in {v for v, _ in s.choices} else (None, "not one of the choices")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, "a number"
    if s.kind == "int" and float(value) != int(value):
        return None, "a whole number"
    v = int(value) if s.kind == "int" else round(float(value), 3)
    if (s.min is not None and v < s.min) or (s.max is not None and v > s.max):
        return None, f"between {s.min:g} and {s.max:g}{(' ' + s.unit) if s.unit and s.unit != '₹' else ''}"
    return v, None


def validate(base: Dict[str, Any], changes: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """(the settings after these changes, errors by key). Unknown keys, wrong types, out-of-range values and the
    cross-checks are errors; a value equal to the current one is no change."""
    errors: Dict[str, str] = {}
    out = dict(base)
    for key, value in (changes or {}).items():
        s = BY_KEY.get(key)
        if s is None:
            errors[key] = "not a setting that can be changed here"
            continue
        v, err = _coerce(s, value)
        if err:
            errors[key] = f"must be {err}"
        else:
            out[key] = v
    if not errors and out["trial_call_minutes"] > out["max_call_minutes"]:
        errors["trial_call_minutes"] = "can't be longer than the longest call"
    return out, errors


def diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, List[Any]]:
    return {k: [before.get(k), after[k]] for k in after if before.get(k) != after[k]}


def apply_to_control(settings: Dict[str, Any]) -> None:
    """Prices and limits for the next admission or charge: PRICING changed in place, so every module holding it sees
    the same values (a publish never re-prices anything already charged)."""
    for s in SCHEMA:
        if s.pricing:
            setattr(PRICING, s.pricing, int(settings[s.key]) * s.scale)


class AgentConfig:
    """The live settings (newest published version, else the defaults), publishing, history and test drafts."""

    def __init__(self, pool: Any = None, redis: Any = None, refresh_s: float = 30.0):
        self.pool, self.redis, self.refresh_s = pool, redis, refresh_s
        self.version, self.settings, self.meta = 0, defaults(), {}
        self._loaded_at = 0.0
        self.on_apply: Optional[Callable[[Dict[str, Any]], None]] = None  # control's other holders of a setting
        self._drafts: Dict[str, Tuple[float, Dict[str, Any]]] = {}  # without Redis (tests): in memory

    async def live(self) -> Dict[str, Any]:
        if time.monotonic() - self._loaded_at > self.refresh_s:
            await self.refresh()
        return {"version": self.version, "settings": dict(self.settings), **self.meta}

    async def refresh(self) -> None:
        """The newest version from Postgres (another control process may have published it)."""
        self._loaded_at = time.monotonic()
        if self.pool is None:
            return
        try:
            async with self.pool.acquire() as con:
                row = await con.fetchrow("select version, settings, note, published_by, published_at from agent_config "
                                         "order by version desc limit 1")
        except Exception as e:  # no table yet, or Postgres away a moment: keep what's in hand
            logger.warning(f"agent config: not read ({type(e).__name__})")
            return
        if row is not None and row["version"] != self.version:
            stored = row["settings"] if isinstance(row["settings"], dict) else json.loads(row["settings"])
            self.version = row["version"]
            self.settings = {**defaults(), **{k: v for k, v in stored.items() if k in BY_KEY}}
            self.meta = {"note": row["note"], "published_by": row["published_by"],
                         "published_at": row["published_at"].isoformat()}
            apply_to_control(self.settings)
            if self.on_apply is not None:
                self.on_apply(self.settings)

    async def publish(self, base_version: int, changes: Dict[str, Any], note: str, who: str,
                      restored_from: Optional[int] = None, audit: Any = None) -> Dict[str, Any]:
        """A new version from the live one plus these changes. ValueError(errors) when a value isn't allowed;
        LookupError when someone published since `base_version`."""
        note = " ".join((note or "").split())[:300]
        if not note:
            raise ValueError({"note": "say what this change is for"})
        async with self.pool.acquire() as con, con.transaction():
            await con.execute("lock table agent_config in exclusive mode")
            row = await con.fetchrow("select version, settings from agent_config order by version desc limit 1")
            live_version = row["version"] if row else 0
            if base_version != live_version:
                raise LookupError(live_version)
            before = {**defaults(), **(json.loads(row["settings"]) if row and isinstance(row["settings"], str)
                                       else (row["settings"] if row else {}))}
            after, errors = validate(before, changes)
            if errors:
                raise ValueError(errors)
            changed = diff(before, after)
            if not changed:
                raise ValueError({"_": "nothing changed"})
            version = await con.fetchval(
                "insert into agent_config (settings, changes, note, published_by, restored_from) values ($1, $2, $3, "
                "$4, $5) returning version", json.dumps(after), json.dumps(changed), note, who, restored_from)
            if audit is not None:
                await audit(con, who, "agent_restore" if restored_from else "agent_publish", f"agent:v{version}",
                            {"changes": changed, "note": note, **({"from": restored_from} if restored_from else {})})
        self._loaded_at = 0.0
        await self.refresh()
        if self.redis is not None:
            try:
                await self.redis.publish("boldoc:agent-config", str(version))
            except Exception as e:  # the talker's own refresh picks it up within seconds anyway
                logger.warning(f"agent config: notice not published ({type(e).__name__})")
        return {"version": version, "changes": changed}

    async def restore(self, version: int, note: str, who: str, audit: Any = None) -> Dict[str, Any]:
        async with self.pool.acquire() as con:
            row = await con.fetchrow("select settings from agent_config where version=$1", version)
        if row is None:
            raise KeyError(version)
        old = row["settings"] if isinstance(row["settings"], dict) else json.loads(row["settings"])
        live = await self.live()
        return await self.publish(live["version"], {k: v for k, v in old.items() if k in BY_KEY}, note, who,
                                  restored_from=version, audit=audit)

    async def history(self, limit: int = 50) -> List[Dict[str, Any]]:
        if self.pool is None:
            return []
        async with self.pool.acquire() as con:
            rows = await con.fetch("select version, changes, note, published_by, published_at, restored_from from "
                                   "agent_config order by version desc limit $1", max(1, min(limit, 200)))
        return [{"version": r["version"], "note": r["note"], "published_by": r["published_by"],
                 "published_at": r["published_at"].isoformat(), "restored_from": r["restored_from"],
                 "changes": r["changes"] if isinstance(r["changes"], dict) else json.loads(r["changes"])}
                for r in rows]

    async def save_draft(self, changes: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Draft settings for one test call (30 minutes): (id, the settings). ValueError(errors) if not allowed."""
        live = await self.live()
        after, errors = validate(live["settings"], changes)
        if errors:
            raise ValueError(errors)
        draft_id = secrets.token_urlsafe(9)
        if self.redis is not None:
            await self.redis.set(f"boldoc:agent-draft:{draft_id}", json.dumps(after), ex=1800)
        else:
            self._drafts[draft_id] = (time.monotonic() + 1800, after)
        return draft_id, after

    async def draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        if self.redis is not None:
            raw = await self.redis.get(f"boldoc:agent-draft:{draft_id}")
            return json.loads(raw) if raw else None
        held = self._drafts.get(draft_id)
        return held[1] if held and held[0] > time.monotonic() else None


def talker_view(version: Any, settings: Dict[str, Any]) -> Dict[str, Any]:
    """What a call needs (the talker's settings), with the version it ran with for the trace."""
    return {"version": version, "settings": {k: settings[k] for k in TALKER_KEYS}}
