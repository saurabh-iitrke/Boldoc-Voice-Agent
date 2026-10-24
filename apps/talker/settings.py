"""Talker configuration (environment variables; secrets come from files mounted by the deployer)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _secret(env: str, file_env: str) -> str:
    """Read a secret from `$env`, or from the file named by `$file_env` (first line), never from the repo."""
    if os.environ.get(env):
        return os.environ[env].strip()
    path = os.environ.get(file_env)
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8").strip().splitlines()[0].strip()
    return ""


@dataclass(frozen=True)
class TalkerSettings:
    public_host: str = field(default_factory=lambda: os.environ.get("PUBLIC_HOST", "localhost"))
    # Every host Plivo may call us on (the domain and its sslip.io alias): webhook signatures are checked against
    # the URL Plivo actually used, so switching the Plivo app between them never breaks validation.
    public_hosts: tuple = field(default_factory=lambda: tuple(h.strip() for h in (
        os.environ.get("PUBLIC_HOSTS", "") or os.environ.get("PUBLIC_HOST", "localhost")).split(",") if h.strip()))
    sarvam_api_key: str = field(default_factory=lambda: _secret("SARVAM_API_KEY", "SARVAM_API_KEY_FILE"))
    plivo_auth_id: str = field(default_factory=lambda: _secret("PLIVO_AUTH_ID", "PLIVO_AUTH_ID_FILE"))
    plivo_auth_token: str = field(default_factory=lambda: _secret("PLIVO_AUTH_TOKEN", "PLIVO_AUTH_TOKEN_FILE"))
    stream_key: str = field(default_factory=lambda: os.environ.get("STREAM_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.environ.get("TALKER_LLM", "sarvam-105b"))
    tts_model: str = field(default_factory=lambda: os.environ.get("TALKER_TTS", "bulbul:v4-flash"))
    tts_voice: str = field(default_factory=lambda: os.environ.get("TALKER_VOICE", "kavya_hi_conversational"))
    stt_model: str = field(default_factory=lambda: os.environ.get("TALKER_STT", "saaras:v3-realtime"))
    # enforce = reject invalid webhooks (default); log = verify and log only (shadow rollout); off = skip
    signature_mode: str = field(default_factory=lambda: {"1": "enforce", "0": "off"}.get(
        os.environ.get("VALIDATE_PLIVO_SIGNATURES", "1"), os.environ.get("VALIDATE_PLIVO_SIGNATURES", "enforce")))
    max_call_s: int = field(default_factory=lambda: int(os.environ.get("MAX_CALL_S", "1800")))
    # Plivo's noise cancellation on the stream's audio (<Stream noiseCancellation noiseCancellationLevel>, level 60-100,
    # Plivo's default 85): on at 95. NOISE_CANCELLATION=0 turns it off.
    noise_cancellation: bool = field(default_factory=lambda: os.environ.get("NOISE_CANCELLATION", "1") != "0")
    noise_cancellation_level: int = field(default_factory=lambda: min(100, max(60, int(
        os.environ.get("NOISE_CANCELLATION_LEVEL", "95")))))
    control_url: str = field(default_factory=lambda: os.environ.get("CONTROL_URL", "http://control:8091"))
    internal_key: str = field(default_factory=lambda: os.environ.get("INTERNAL_KEY", ""))
    redis_url: str = field(default_factory=lambda: os.environ.get("REDIS_URL", ""))


SETTINGS = TalkerSettings()
