"""What a call cost us, by component: provider bills where we have them, usage × a dated list price elsewhere.

Plivo's hangup CDR carries the call's real cost (TotalCost, USD). Sarvam bills by usage, so speech-to-text is the call's
duration, text-to-speech is characters spoken and the LLM is tokens. SMS is counted at India's transactional (DLT) rate
per message part: what the product pays in India. Prices are list prices on `as_of`; override any of them with
RATE_CARD_JSON (a JSON object of the same keys).
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, Optional

DEFAULT_CARD: Dict[str, Any] = {
    "as_of": "2026-10-22",
    "usd_inr": 88.0,
    "stt_inr_per_min": 30.0 / 60,          # Sarvam saaras streaming: ₹30 per hour of audio (billed on stream time)
    "tts_inr_per_char": 20.0 / 10_000,     # Sarvam bulbul: ₹20 per 10,000 characters
    "llm_in_inr_per_token": 15.0 / 1e6,    # sarvam-105b: ₹15 per million input tokens
    "llm_out_inr_per_token": 60.0 / 1e6,   # ...and ₹60 per million output tokens
    # Other talker/thinker models, USD per million tokens (input, output): Amazon Bedrock, ap-south-1, standard tier
    "llm_usd_per_m": {
        "qwen.qwen3-235b-a22b-2507": [0.26, 1.04], "mistral.mistral-large-3-675b-instruct": [0.59, 1.76],
        "openai.gpt-oss-120b": [0.18, 0.71], "deepseek.v3.2": [0.74, 2.22],
    },
    "plivo_voice_usd_per_min": 0.0045,     # fallback when the CDR has no TotalCost yet (India inbound, ~₹0.40/min)
    "sms_inr_per_part": 0.18,              # India transactional SMS (DLT): ₹0.10-0.18 at Indian providers, 2026
    "web_search_usd": 0.001,               # Serper (long documents' web research): $50 for 50,000 searches
}


def rate_card() -> Dict[str, Any]:
    card = dict(DEFAULT_CARD)
    raw = os.environ.get("RATE_CARD_JSON", "").strip()
    if raw:
        card.update(json.loads(raw))
    return card


def _llm_inr(summary: Dict[str, Any], card: Dict[str, Any]) -> float:
    """Per model where the trace says which model answered; Sarvam's price otherwise."""
    by_model = summary.get("llm_by_model") or {}
    if not by_model:
        return (float(summary.get("llm_prompt_tokens") or 0) * card["llm_in_inr_per_token"]
                + float(summary.get("llm_completion_tokens") or 0) * card["llm_out_inr_per_token"])
    total = 0.0
    for model, (tin, tout) in by_model.items():
        usd = card.get("llm_usd_per_m", {}).get(model)
        if usd:
            total += (tin * usd[0] + tout * usd[1]) / 1e6 * card["usd_inr"]
        else:
            total += tin * card["llm_in_inr_per_token"] + tout * card["llm_out_inr_per_token"]
    return total


def call_cost(summary: Dict[str, Any], record: Optional[Dict[str, Any]] = None,
              card: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """₹ per component for one call. `summary` is the call report's summary (usage sums); `record` its call record."""
    card = card or rate_card()
    record = record or {}
    talk_s = float(record.get("duration_s") or summary.get("duration_s") or 0)
    bill_s = float(record.get("bill_s") or talk_s)
    if record.get("cost_usd") is not None:
        telephony = float(record["cost_usd"]) * card["usd_inr"]
        telephony_source = "cdr"
    else:
        telephony = math.ceil(bill_s / 60) * card["plivo_voice_usd_per_min"] * card["usd_inr"]
        telephony_source = "estimate"
    parts = {
        "telephony": telephony,
        "stt": talk_s / 60 * card["stt_inr_per_min"],
        "tts": float(summary.get("tts_chars") or 0) * card["tts_inr_per_char"],
        "llm": _llm_inr(summary, card),
        "sms": float(summary.get("sms_sent") or 0) * card["sms_inr_per_part"],  # each fits one 160-character part
    }
    total = sum(parts.values())
    minutes = talk_s / 60
    return {"inr": {k: round(v, 3) for k, v in parts.items()}, "total_inr": round(total, 2),
            "per_min_inr": round(total / minutes, 2) if minutes > 0 else None,
            "telephony_source": telephony_source, "as_of": card["as_of"]}
