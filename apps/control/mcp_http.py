"""What Hermes reaches on control (internal network only; Caddy routes nothing under /internal):

  /internal/mcp                     BolDoc's document tools over MCP (streamable HTTP, stateless, JSON replies): the
                                    JSON-RPC methods a tool client uses (initialize, tools/list, tools/call, ping)
  /internal/llm/v1/chat/completions the metering proxy: Hermes' model calls go to the writer's endpoint
                                    (THINKER_LLM) with its key, which Hermes never sees; each call's tokens count
                                    toward the run's cap and the document's cost

Both take `Authorization: Bearer <HERMES_MCP_KEY>`. Written by hand rather than with the MCP SDK: control's tests run
on Python 3.9, which the SDK doesn't support, and a tool server needs only these four methods.
"""
from __future__ import annotations

import contextlib
import json
import secrets
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from loguru import logger

router = APIRouter()


class _Ctx:
    big = None          # BigDocs
    key: str = ""       # HERMES_MCP_KEY
    endpoint = None     # the writer's LLMEndpoint (protocol.llm)
    http: Optional[httpx.AsyncClient] = None


CTX = _Ctx()

_BLOCK = {
    "type": "object",
    "description": 'One block, by "type": heading {text, level 1-3}; paragraph {text}; list {items: [text], '
                   'ordered}; table {caption?, columns: [{name, type: text|number|currency|percent|date}], rows: '
                   '[[cell, ...]], total_row: true or false (the total is computed: never write a total row)}; kv '
                   '{pairs: [[key, value]]}; page_break {}; chart {kind: column|bar|line|pie, title?, categories: '
                   '[text], series: [{name, values: [number]}] (one value per category; pie: one series), unit: '
                   'number|currency|percent}; slide {title, bullets, notes?, layout?: bullets|title|section|two_column|'
                   'quote|closing, right? (two_column: the right-hand bullets), quote? (quote: one line or number '
                   'shown large)}.',
    "properties": {"type": {"type": "string",
                            "enum": ["heading", "paragraph", "list", "table", "kv", "page_break", "chart", "slide"]}},
    "required": ["type"],
}
TOOLS = [
    {"name": "get_brief",
     "description": "The document to write: its type, language, formats, the approved outline (section ids, titles "
                    "and target_words, in order), the caller's facts (the only source for names, numbers and dates "
                    "about them), their saved details, what they didn't give (not_given) and what they said.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "write_section",
     "description": "Write (or rewrite) one whole section of the outline as blocks, in order. Calling it again for "
                    "the same section replaces it. A fact about the caller you don't have is written as a "
                    "placeholder like [Your loan amount], never invented.",
     "inputSchema": {"type": "object", "properties": {
         "section_id": {"type": "string", "description": "an id from the outline"},
         "blocks": {"type": "array", "items": _BLOCK}}, "required": ["section_id", "blocks"]}},
    {"name": "ask_caller",
     "description": "Ask the caller, once, for facts about THEM that the document can't be written without: every "
                    "such question in this one call, at most 3, each under 120 characters, plain words. BolDoc asks "
                    "them and returns their answers (this can take minutes). Never ask about your own work "
                    "(length, sections, formats), and never for OTPs, PINs, ID or account numbers. An answer of "
                    "null wasn't given: write a placeholder like [Your loan amount] and carry on.",
     "inputSchema": {"type": "object", "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
                     "required": ["questions"]}},
    {"name": "check",
     "description": "What still needs work: sections not written (missing), sections under 75% of their target "
                    "length (too_short: write them again, longer), a total in the text that no table adds up to "
                    "(totals_wrong), and placeholders left.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "finish",
     "description": "Call once every section is written and check reports nothing to fix. title: the "
                    "document's title in the document's language, as on its cover. subtitle: one line under it "
                    "saying what it is for and for whom. author: who presents it (name, role, business, town). "
                    "note: one line on anything the reader should know.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "subtitle": {"type": "string"},
                                                      "author": {"type": "string"}, "note": {"type": "string"}}}},
    {"name": "get_material",
     "description": "Read the files the caller added for this document (get_brief lists them): their own facts, "
                    "quoted. Without a name, the start of each; with one, more of that file.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}}},
    {"name": "web_search",
     "description": "Search the web (Google, results for India), only when the brief says research is on. Use "
                    "general terms (the scheme, the trade, the place), never the caller's name, business name, "
                    "address or numbers. Returns titles, links and snippets; read a page before using it.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "read_page",
     "description": "Read one page (or PDF) that web_search found, or an official .gov.in / .nic.in page. Returns "
                    "its source number and the passages about what you're looking for. Cite what you use as [n].",
     "inputSchema": {"type": "object", "properties": {
         "url": {"type": "string"},
         "looking_for": {"type": "string", "description": "what you need from the page, in a few words"}},
         "required": ["url"]}},
]


def _authorized(authorization: Optional[str]) -> bool:
    if not CTX.key or not authorization or not authorization.startswith("Bearer "):
        return False
    return secrets.compare_digest(authorization[7:].strip(), CTX.key)


def _reply(msg_id: Any, result: Optional[dict] = None, error: Optional[dict] = None) -> JSONResponse:
    body: Dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id}
    body["error" if error is not None else "result"] = error if error is not None else result
    return JSONResponse(body)


async def _call(name: str, args: Dict[str, Any]) -> dict:
    big = CTX.big
    if name == "get_brief":
        return await big.get_brief()
    if name == "write_section":
        return await big.write_section(str(args.get("section_id", "")), args.get("blocks") or [])
    if name == "ask_caller":
        return await big.ask_caller(list(args.get("questions") or []))
    if name == "check":
        return await big.check()
    if name == "finish":
        return await big.finish(str(args.get("note") or ""), str(args.get("title") or ""),
                                str(args.get("subtitle") or ""), str(args.get("author") or ""))
    if name == "get_material":
        return await big.get_material(str(args.get("name") or ""))
    if name == "web_search":
        return await big.web_search(str(args.get("query") or ""))
    if name == "read_page":
        return await big.read_page(str(args.get("url") or ""), str(args.get("looking_for") or ""))
    return {"ok": False, "error": f"unknown tool {name}"}


@router.post("/internal/mcp")
async def mcp(request: Request, authorization: Optional[str] = Header(None)):
    if not _authorized(authorization):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        msg = await request.json()
    except ValueError:
        return _reply(None, error={"code": -32700, "message": "parse error"})
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _reply(None, error={"code": -32600, "message": "invalid request"})
    if "id" not in msg:  # a notification (notifications/initialized, cancelled, ...): nothing to answer
        return Response(status_code=202)
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return _reply(msg["id"], {"protocolVersion": params.get("protocolVersion") or "2025-06-18",
                                  "capabilities": {"tools": {"listChanged": False}},
                                  "serverInfo": {"name": "boldoc", "version": "1"}})
    if method == "ping":
        return _reply(msg["id"], {})
    if method == "tools/list":
        return _reply(msg["id"], {"tools": TOOLS})
    if method == "tools/call":
        if CTX.big is None:
            return _reply(msg["id"], {"content": [{"type": "text", "text": "long documents are off"}],
                                      "isError": True})
        name = str(params.get("name") or "")
        crashed = False
        try:
            out = await _call(name, params.get("arguments") or {})
        except Exception as e:  # a tool bug must not take the run down: the model sees the error and goes on
            logger.exception(f"mcp tool {name} failed")
            out, crashed = {"ok": False, "error": f"{type(e).__name__}: the tool failed; try again"}, True
        text = json.dumps(out, ensure_ascii=False)
        # Only a crash is an MCP error. An answer like "ok": false ("the site asks robots not to read this page") is a
        # result the model reads: Hermes pauses a server for a minute after three errors in a row, and three unreadable
        # pages in a row cut the writer off from every tool, get_brief and write_section too.
        return _reply(msg["id"], {"content": [{"type": "text", "text": text}], "structuredContent": out,
                                  "isError": crashed})
    return _reply(msg["id"], error={"code": -32601, "message": f"method not found: {method}"})


@router.get("/internal/mcp")
async def mcp_stream() -> Response:
    return Response(status_code=405)  # no server-initiated messages: the client polls nothing


@router.delete("/internal/mcp")
async def mcp_end() -> Response:
    return Response(status_code=405)  # stateless: no session to end


# ------------------------------------------------------------------------------------------------ metering proxy
@router.get("/internal/llm/v1/models")
async def models(authorization: Optional[str] = Header(None)):
    if not _authorized(authorization):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    model = getattr(CTX.endpoint, "model", "writer")
    return {"object": "list", "data": [{"id": model, "object": "model", "owned_by": "boldoc"}]}


def _usage(rec: dict) -> None:
    if CTX.big is not None:
        CTX.big.add_usage(rec.get("prompt_tokens") or 0, rec.get("completion_tokens") or 0)


@router.post("/internal/llm/v1/chat/completions")
async def chat(request: Request, authorization: Optional[str] = Header(None)):
    if not _authorized(authorization):
        return JSONResponse({"error": {"message": "unauthorized"}}, status_code=401)
    ep = CTX.endpoint
    if ep is None:
        return JSONResponse({"error": {"message": "no writer model configured"}}, status_code=503)
    if CTX.big is not None and CTX.big.over_cap():
        return JSONResponse({"error": {"message": "this document's token budget is used up", "type": "budget"}},
                            status_code=429)
    body = await request.json()
    body["model"] = ep.model  # Hermes names a model; the writer's endpoint decides which one answers
    for k, v in (getattr(ep, "extra", None) or {}).items():
        body.setdefault(k, v)
    CTX.http = CTX.http or httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10))
    t0 = time.time()
    if not body.get("stream"):
        r = await CTX.http.post(ep.chat_url, json=body, headers=ep.headers())
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else \
            {"error": {"message": r.text[:300]}}
        if r.status_code == 200:
            _usage(data.get("usage") or {})
        return JSONResponse(data, status_code=r.status_code)
    body.setdefault("stream_options", {})["include_usage"] = True

    async def relay():
        usage: dict = {}
        async with CTX.http.stream("POST", ep.chat_url, json=body, headers=ep.headers()) as r:
            if r.status_code != 200:
                text = (await r.aread()).decode("utf-8", "replace")
                logger.warning(f"writer model HTTP {r.status_code}: {text[:200]}")
                yield f"data: {json.dumps({'error': {'message': text[:300]}})}\n\n"
                return
            async for line in r.aiter_lines():
                if line.startswith("data: ") and '"usage"' in line:
                    with contextlib.suppress(ValueError):
                        usage = json.loads(line[6:]).get("usage") or usage
                yield line + "\n"
        _usage(usage)
        logger.debug(f"writer call {int((time.time() - t0) * 1000)} ms, usage {usage}")
    return StreamingResponse(relay(), media_type="text/event-stream")
