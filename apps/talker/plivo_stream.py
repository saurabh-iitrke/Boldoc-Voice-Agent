"""Plivo stream helpers with no Pipecat dependency (unit-testable anywhere)."""
from __future__ import annotations

import json
import os
from collections import OrderedDict
from typing import Optional


def debug_caller(caller) -> bool:
    """Callers whose transcripts may be logged while debugging (the developer's own phone). Off unless set."""
    allow = {"".join(ch for ch in d if ch.isdigit()) for d in os.environ.get("DEBUG_CALLERS", "").split(",")}
    # digits only on both sides: callers are "+91…" since admission, the setting is "91…"; the mismatch silently
    # switched this off and the words of a real problem call were not captured
    return bool(caller) and "".join(ch for ch in str(caller) if ch.isdigit()) in allow - {""}


# What the answer webhook decided, by CallUUID, for the stream that follows a moment later: the greeting variant
# (first call) and, if control was unreachable, the signed answer parameters so control can record the call late.
# Identity never comes from here: control's call record is the only source. Bounded; entries live for seconds.
_ADMISSIONS: OrderedDict[str, dict] = OrderedDict()


def remember_admission(call_uuid: str, admission: dict, late_admit: Optional[dict] = None) -> None:
    if call_uuid:
        _ADMISSIONS[call_uuid] = {"admission": admission, "late_admit": late_admit}
        while len(_ADMISSIONS) > 500:
            _ADMISSIONS.popitem(last=False)


def take_admission(call_uuid: str) -> Optional[dict]:
    return _ADMISSIONS.pop(call_uuid, None)


def ph_header(params: dict, name: str) -> Optional[str]:
    """A custom SIP header the browser set (X-PH-<name>), as Plivo passes it to the answer URL (any case)."""
    want = f"x-ph-{name}".lower()
    return next((str(v) for k, v in params.items() if str(k).lower() == want and v), None)


def parse_extra_headers(raw) -> dict:
    """Plivo delivers <Stream extraHeaders="a=1,b=2"> as the string '{X-PH-a: 1, X-PH-b: 2}'; our test caller sends
    'a=1,b=2'. Accept both (and JSON); keys lose the X-PH- prefix and are lower-cased."""
    if isinstance(raw, dict):
        items = raw.items()
    else:
        text = str(raw or "").strip()
        try:
            loaded = json.loads(text) if text.startswith("{") and '"' in text else None
        except ValueError:
            loaded = None
        if isinstance(loaded, dict):
            items = loaded.items()
        else:
            pairs = []
            for part in text.strip("{}").replace(";", ",").split(","):
                sep = "=" if "=" in part else ":" if ":" in part else None
                if sep:
                    k, v = part.split(sep, 1)
                    pairs.append((k, v))
            items = pairs
    out = {}
    for k, v in items:
        key = str(k).strip().strip('"').strip()
        if key.lower().startswith("x-ph-"):
            key = key[5:]
        out[key.lower()] = str(v).strip().strip('"').strip()
    return out
