"""Minimal chat client for the thinker: JSON-only answers, validated, with one repair retry.

Any OpenAI-compatible endpoint (protocol/llm.py: THINKER_LLM); a plain key string means Sarvam, as before."""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Optional, TypeVar, Union

import httpx

from protocol.llm import BUILTIN, LLMEndpoint

API = "https://api.sarvam.ai/v1/chat/completions"
T = TypeVar("T")


class ThinkerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _extract_json(text: str) -> Any:
    """Models sometimes wrap JSON in prose or ``` fences; take the outermost object."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


def _endpoint(api_key: Union[str, LLMEndpoint], model: str) -> LLMEndpoint:
    if isinstance(api_key, LLMEndpoint):
        return api_key
    sarvam = BUILTIN["sarvam"]
    return LLMEndpoint("sarvam", model, sarvam["base_url"], api_key, sarvam["auth"], {})


async def chat_json(api_key: Union[str, LLMEndpoint], system: str, user: str, parse: Callable[[Any], T], *,
                    model: str = "sarvam-105b", reasoning: Optional[str] = "low", max_tokens: int = 4000,
                    temperature: float = 0.3, timeout_s: float = 60.0,
                    client: Optional[httpx.AsyncClient] = None) -> tuple[T, dict]:
    """Ask for JSON, validate with `parse`; on a validation error, retry once with the error shown to the model.
    `reasoning` is Sarvam's reasoning_effort; other providers get their configured extras instead."""
    ep = _endpoint(api_key, model)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    stats: dict = {"attempts": 0, "latency_ms": 0, "usage": {}, "model": ep.label}
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout_s)
    try:
        for attempt in range(2):
            stats["attempts"] = attempt + 1
            body = {"model": ep.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
            if ep.provider == "sarvam":
                body["reasoning_effort"] = reasoning
            else:
                body.update(ep.extra)
            t0 = time.perf_counter()
            try:
                r = await client.post(ep.chat_url, json=body, headers=ep.headers())
            except (httpx.TimeoutException, httpx.TransportError) as e:  # Bedrock stalled 8-29 s at times
                raise ThinkerError("provider_unavailable", f"{ep.provider} {type(e).__name__}") from e
            stats["latency_ms"] += round((time.perf_counter() - t0) * 1000)
            if r.status_code == 429 or r.status_code >= 500:
                raise ThinkerError("provider_unavailable", f"{ep.provider} HTTP {r.status_code}")
            if r.status_code != 200:
                raise ThinkerError("provider_error", f"{ep.provider} HTTP {r.status_code}: {r.text[:200]}")
            data = r.json()
            stats["usage"] = data.get("usage", {})
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            try:
                return parse(_extract_json(content)), stats
            except Exception as e:
                if attempt == 1:
                    raise ThinkerError("invalid_output", f"model output failed validation twice: {e}") from e
                messages += [{"role": "assistant", "content": content[:6000]},
                             {"role": "user", "content": f"That JSON is invalid: {str(e)[:600]}. "
                                                         "Return the corrected JSON object only."}]
        raise ThinkerError("invalid_output", "unreachable")
    finally:
        if own:
            await client.aclose()
