"""What Hermes reaches on control: the document tools over MCP, and the metering proxy to the writer's model."""
import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.control import mcp_http
from protocol.llm import LLMEndpoint

KEY = "k" * 32
AUTH = {"Authorization": f"Bearer {KEY}"}


class FakeBig:
    def __init__(self):
        self.usage, self.cap_hit, self.calls = [], False, []

    async def get_brief(self):
        return {"outline": [{"id": "s1", "title": "Summary", "words": 300}]}

    async def write_section(self, section_id, blocks):
        self.calls.append(("write_section", section_id, blocks))
        return {"ok": False, "error": "blocks not valid"} if not blocks else {"ok": True}

    async def check(self):
        raise RuntimeError("boom")

    async def web_search(self, query):
        self.calls.append(("web_search", query))
        return {"ok": True, "results": []}

    async def read_page(self, url, looking_for):
        self.calls.append(("read_page", url, looking_for))
        return {"ok": True, "source": 1}

    def add_usage(self, p, c):
        self.usage.append((p, c))

    def over_cap(self):
        return self.cap_hit


def client(upstream=None):
    app = FastAPI()
    app.include_router(mcp_http.router)
    mcp_http.CTX.big, mcp_http.CTX.key = FakeBig(), KEY
    mcp_http.CTX.endpoint = LLMEndpoint(provider="bedrock", model="qwen.qwen3-235b-a22b-2507",
                                        base_url="https://llm.test/v1", api_key="secret-upstream")
    mcp_http.CTX.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream)) if upstream else None
    return TestClient(app)


def rpc(c, method, params=None, msg_id=1):
    return c.post("/internal/mcp", headers=AUTH, json={"jsonrpc": "2.0", "id": msg_id, "method": method,
                                                       "params": params or {}})


def test_the_tools_speak_mcp_to_hermes_and_nobody_else():
    c = client()
    assert c.post("/internal/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    init = rpc(c, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                 "clientInfo": {"name": "hermes", "version": "0.22.4"}}).json()
    assert init["result"]["protocolVersion"] == "2025-11-25" and "tools" in init["result"]["capabilities"]
    assert c.post("/internal/mcp", headers=AUTH, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
                  ).status_code == 202
    names = [t["name"] for t in rpc(c, "tools/list").json()["result"]["tools"]]
    assert names == ["get_brief", "write_section", "ask_caller", "check", "finish", "get_material", "web_search",
                     "read_page"]
    brief = rpc(c, "tools/call", {"name": "get_brief", "arguments": {}}).json()["result"]
    assert brief["structuredContent"]["outline"][0]["id"] == "s1" and brief["isError"] is False
    assert json.loads(brief["content"][0]["text"]) == brief["structuredContent"]
    refused = rpc(c, "tools/call", {"name": "write_section", "arguments": {"section_id": "s1", "blocks": []}})
    # a refusal is a result the model reads, never an MCP error: three errors in a row and Hermes pauses the server
    assert refused.json()["result"]["isError"] is False
    assert refused.json()["result"]["structuredContent"]["ok"] is False
    crashed = rpc(c, "tools/call", {"name": "check", "arguments": {}}).json()["result"]  # a tool bug isn't fatal
    assert crashed["isError"] and "try again" in crashed["structuredContent"]["error"]
    assert rpc(c, "resources/list").json()["error"]["code"] == -32601
    rpc(c, "tools/call", {"name": "web_search", "arguments": {"query": "MUDRA limit"}})
    rpc(c, "tools/call", {"name": "read_page", "arguments": {"url": "https://www.mudra.org.in/", "looking_for": "x"}})
    assert mcp_http.CTX.big.calls[-2:] == [("web_search", "MUDRA limit"),
                                           ("read_page", "https://www.mudra.org.in/", "x")]
    assert c.get("/internal/mcp", headers=AUTH).status_code == 405


def test_the_proxy_keeps_the_key_names_the_model_and_counts_tokens():
    seen = {}

    def upstream(req: httpx.Request) -> httpx.Response:
        seen["auth"], seen["body"] = req.headers.get("authorization"), json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                         "usage": {"prompt_tokens": 900, "completion_tokens": 40}})
    c = client(upstream)
    r = c.post("/internal/llm/v1/chat/completions", headers=AUTH,
               json={"model": "anything", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200 and seen["auth"] == "Bearer secret-upstream"
    assert seen["body"]["model"] == "qwen.qwen3-235b-a22b-2507" and mcp_http.CTX.big.usage == [(900, 40)]
    assert c.post("/internal/llm/v1/chat/completions", json={"messages": []}).status_code == 401
    mcp_http.CTX.big.cap_hit = True  # the run's token budget is used up: Hermes' run fails, today's writer takes over
    assert c.post("/internal/llm/v1/chat/completions", headers=AUTH, json={"messages": []}).status_code == 429


def test_a_streamed_reply_is_relayed_and_its_usage_counted():
    def upstream(req: httpx.Request) -> httpx.Response:
        assert json.loads(req.content)["stream_options"] == {"include_usage": True}
        chunks = ('data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
                  'data: {"choices":[],"usage":{"prompt_tokens":1200,"completion_tokens":7}}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, content=chunks.encode(), headers={"content-type": "text/event-stream"})
    c = client(upstream)
    r = c.post("/internal/llm/v1/chat/completions", headers=AUTH, json={"messages": [], "stream": True})
    assert r.status_code == 200 and '"he"' in r.text and "[DONE]" in r.text
    assert mcp_http.CTX.big.usage == [(1200, 7)]
