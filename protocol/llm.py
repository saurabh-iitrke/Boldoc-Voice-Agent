"""Which LLM answers each role, by configuration: switch providers without touching code.

Roles: `talker` (the live call), `thinker` (drafting and editing documents) and `judge` (the one-word "has the
caller finished?" classifier). Each is "<provider>:<model>" in TALKER_LLM / THINKER_LLM / JUDGE_LLM; a bare model
name means Sarvam (so TALKER_LLM=sarvam-105b keeps working). JUDGE_LLM defaults to the talker's endpoint.
OVERFLOW_TALKER_LLM, if set, is the talker for calls admitted beyond the primary LLM's capacity
(PRIMARY_LLM_CALLS): busy times get a slightly slower model instead of a busy signal.

Sarvam is built in. Any OpenAI-compatible provider (a hosted API, or a vLLM/TGI server we run) is declared in
LLM_PROVIDERS, a JSON object; its key is read from a file, never from the repo:

  LLM_PROVIDERS='{"krutrim": {"base_url": "https://cloud.olakrutrim.com/v1", "key_file": "/run/secrets/krutrim",
                              "thinker_extra": {"response_format": {"type": "json_object"}}}}'
  TALKER_LLM=krutrim:<model>

`auth` is "bearer" (default) or "header:<name>". `<role>_extra` are request-body fields a provider needs for that
role, e.g. Sarvam's reasoning_effort, or chat_template_kwargs to turn a vLLM model's thinking off.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT = "sarvam:sarvam-105b"
BUILTIN: Dict[str, Dict[str, Any]] = {
    "sarvam": {
        "base_url": "https://api.sarvam.ai/v1", "auth": "header:api-subscription-key",
        "key_env": "SARVAM_API_KEY", "key_file_env": "SARVAM_API_KEY_FILE",
        # /v1 honours reasoning_effort=null (fast, real tokens); the thinker reasons a little ("low")
        "talker_extra": {"reasoning_effort": None}, "judge_extra": {"reasoning_effort": None},
        "thinker_extra": {"reasoning_effort": "low"},
    },
}


@dataclass(frozen=True)
class LLMEndpoint:
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    auth: str = "bearer"
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def headers(self) -> Dict[str, str]:
        if self.auth.startswith("header:"):
            return {self.auth.split(":", 1)[1]: self.api_key}
        return {"Authorization": f"Bearer {self.api_key}"}

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


def providers() -> Dict[str, Dict[str, Any]]:
    merged = {name: dict(cfg) for name, cfg in BUILTIN.items()}
    raw = os.environ.get("LLM_PROVIDERS", "").strip()
    if raw:
        for name, cfg in json.loads(raw).items():
            merged[name] = {**merged.get(name, {}), **cfg}
    return merged


def _key(cfg: Dict[str, Any]) -> str:
    if cfg.get("key_env") and os.environ.get(cfg["key_env"]):
        return os.environ[cfg["key_env"]].strip()
    path = cfg.get("key_file") or (os.environ.get(cfg["key_file_env"], "") if cfg.get("key_file_env") else "")
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8").strip().splitlines()[0].strip()
    return ""


def parse_spec(spec: str) -> tuple:
    spec = spec.strip()
    if ":" in spec:
        provider, model = spec.split(":", 1)
        return provider.strip(), model.strip()
    return "sarvam", spec


def endpoint(role: str, spec: Optional[str] = None) -> LLMEndpoint:
    """The endpoint for `role` ('talker' | 'thinker' | 'judge'), from `spec` or the role's environment variable."""
    spec = spec or os.environ.get(f"{role.upper()}_LLM", "").strip()
    if not spec and role == "judge":
        spec = os.environ.get("TALKER_LLM", "").strip()
    provider, model = parse_spec(spec or DEFAULT)
    cfg = providers().get(provider)
    if cfg is None:
        raise ValueError(f"unknown LLM provider {provider!r}: declare it in LLM_PROVIDERS")
    return LLMEndpoint(provider, model, cfg["base_url"], _key(cfg), cfg.get("auth", "bearer"),
                       dict(cfg.get(f"{role}_extra") or {}))


def overflow_endpoint(role: str = "talker") -> Optional[LLMEndpoint]:
    """The fallback model (OVERFLOW_TALKER_LLM), or None if not configured: the talker for calls beyond the primary
    LLM's capacity or after it fails mid-call, and (role "judge") those calls' one-word checks."""
    spec = os.environ.get("OVERFLOW_TALKER_LLM", "").strip()
    return endpoint(role, spec) if spec else None
