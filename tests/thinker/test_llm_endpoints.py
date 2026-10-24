"""LLM providers by configuration: the talker, thinker and turn judge can each be switched with an env var."""
import asyncio
import json

import httpx
import pytest

from apps.thinker.llm import chat_json
from protocol.llm import endpoint


def test_specs_default_to_sarvam_and_unknown_providers_fail_loudly(monkeypatch, tmp_path):
    for var in ("TALKER_LLM", "THINKER_LLM", "JUDGE_LLM", "LLM_PROVIDERS", "SARVAM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    key = tmp_path / "sarvam"
    key.write_text("sk-sarvam\n")
    monkeypatch.setenv("SARVAM_API_KEY_FILE", str(key))
    ep = endpoint("talker")
    assert (ep.provider, ep.model, ep.api_key) == ("sarvam", "sarvam-105b", "sk-sarvam")
    assert ep.headers() == {"api-subscription-key": "sk-sarvam"} and ep.extra == {"reasoning_effort": None}
    assert endpoint("talker", "sarvam-105b-conversations").model == "sarvam-105b-conversations"  # bare = Sarvam
    assert endpoint("thinker").extra == {"reasoning_effort": "low"}
    with pytest.raises(ValueError, match="LLM_PROVIDERS"):
        endpoint("talker", "nope:model")
    assert "sk-sarvam" not in repr(ep)  # keys never end up in logs via repr


def test_any_openai_compatible_provider_is_one_env_var_away(monkeypatch, tmp_path):
    key = tmp_path / "k"
    key.write_text("tok-123")
    monkeypatch.setenv("LLM_PROVIDERS", json.dumps({"india": {
        "base_url": "https://llm.example.in/v1/", "key_file": str(key),
        "talker_extra": {"chat_template_kwargs": {"enable_thinking": False}},
        "thinker_extra": {"response_format": {"type": "json_object"}}}}))
    monkeypatch.setenv("TALKER_LLM", "india:qwen3-32b")
    monkeypatch.delenv("JUDGE_LLM", raising=False)
    talker, judge = endpoint("talker"), endpoint("judge")
    assert talker.chat_url == "https://llm.example.in/v1/chat/completions"
    assert talker.headers() == {"Authorization": "Bearer tok-123"}
    assert judge.label == "india:qwen3-32b"  # the judge follows the talker unless JUDGE_LLM says otherwise
    assert endpoint("thinker", "india:big-model").extra == {"response_format": {"type": "json_object"}}


def test_thinker_calls_the_configured_endpoint_with_its_extras(monkeypatch, tmp_path):
    key = tmp_path / "k"
    key.write_text("tok-9")
    monkeypatch.setenv("LLM_PROVIDERS", json.dumps({"india": {
        "base_url": "https://llm.example.in/v1", "key_file": str(key),
        "thinker_extra": {"response_format": {"type": "json_object"}}}}))
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), dict(request.headers), json.loads(request.content)))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": 1}'}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 3}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            a = await chat_json(endpoint("thinker", "india:m1"), "sys", "user", lambda d: d, client=client)
            b = await chat_json("sarvam-key", "sys", "user", lambda d: d, reasoning=None, client=client)
            return a, b
    (out, stats), (_, sstats) = asyncio.run(go())
    assert out == {"ok": 1} and stats["model"] == "india:m1" and stats["usage"]["prompt_tokens"] == 10
    (url, headers, body), (surl, sheaders, sbody) = seen
    assert url == "https://llm.example.in/v1/chat/completions" and headers["authorization"] == "Bearer tok-9"
    assert body["model"] == "m1" and body["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in body  # a Sarvam-only parameter never goes to other providers
    assert surl == "https://api.sarvam.ai/v1/chat/completions" and sheaders["api-subscription-key"] == "sarvam-key"
    assert sbody["reasoning_effort"] is None and sstats["model"] == "sarvam:sarvam-105b"


def test_turn_judge_uses_the_configured_endpoint():
    pytest.importorskip("loguru")
    from apps.talker.turn_judge import classify
    from protocol.llm import LLMEndpoint
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Incomplete."}}]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ep = LLMEndpoint("india", "small", "https://llm.example.in/v1", "t", "bearer",
                             {"chat_template_kwargs": {"enable_thinking": False}})
            return await classify(client, ep, "Kaunsi date?", "25 se")
    assert asyncio.run(go()) == "incomplete"
    assert seen[0]["model"] == "small" and seen[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen[0]["max_tokens"] == 3
