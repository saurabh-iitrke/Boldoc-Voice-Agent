"""BolDoc's voice in every language: one persona ("Simran") so a caller who switches language hears the same assistant.
Chosen by measurement : every candidate was intelligible on 8 kHz μ-law (character error ≤ 2%) and streamed first
audio in ~200 ms (v4-flash and v3 alike), so the choice is persona consistency; Hindi/Hinglish uses the voice Sarvam
built for code-mixed speech. Override per family with TALKER_VOICES='{"hinglish":
"bulbul:v4-flash/aparna_hi_customer", ...}'.
"""
from __future__ import annotations

import json
import os
from typing import Dict, Tuple

V4, V3 = "bulbul:v4-flash", "bulbul:v3"
DEFAULT: Dict[str, Tuple[str, str]] = {  # family -> (model, speaker)
    "hinglish": (V4, "simran_enhi_customer"),
    "en-IN": (V4, "simran_en_customer"),
    **{lang: (V3, "simran") for lang in ("mr-IN", "bn-IN", "gu-IN", "kn-IN", "te-IN", "pa-IN", "ta-IN", "ml-IN",
                                           "od-IN")},
}
TTS_LANGUAGE = {"hinglish": "hi-IN"}  # everything else is its own code


def voice_map() -> Dict[str, Tuple[str, str]]:
    voices = dict(DEFAULT)
    raw = os.environ.get("TALKER_VOICES", "").strip()
    if raw:
        for family, spec in json.loads(raw).items():
            model, _, speaker = spec.partition("/")
            voices[family] = (model, speaker)
    return voices


def tts_language(family: str) -> str:
    return TTS_LANGUAGE.get(family, family)
