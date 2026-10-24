"""The website's live call view: one WebSocket per browser call, fed by the call's two Redis channels.

WebSocket /api/live?session=<browser session>&after=<seq>
* Who may watch: the signed-in user whose browser session started the call (admission ties the call to the session,
  accounts/browser.py), from one of our own pages (Origin checked). The socket may open before Plivo has asked us to
  answer the call, so it waits up to ADMIT_WAIT_S for the admission.
* What it sends: the UI's LiveEvent shapes (web/src/lib/types.ts), translated here from
  - the talker's feed (boldoc:live:<call>): {t, kind: state | caller | agent | ended, text?}
  - control's durable events (boldoc:events:<call>): {seq, type: task.* | delivery.* ..., doc_id, payload}
    A finished task becomes a `doc` event carrying the document itself (the view the document page reads).
* Reconnects: the client passes the last durable `seq` it saw (after=). Missed events are replayed from Postgres, so
  a dropped socket never loses a document or a delivery. Captions and agent state are live only.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from protocol.bus import events_channel, live_channel

from .accounts import browser
from .accounts.web import COOKIE
from .accounts.web import CTX as WEB

router = APIRouter()
ADMIT_WAIT_S = 30.0
LINGER_S = 120.0   # after the call: the "updated" SMS and its delivery receipt still arrive
AGENT_STATES = ("listening", "thinking", "speaking", "writing")
STAGES = {"started": "Writing"}
TASK_TYPES = ("accepted", "progress", "question", "failed", "cancelled")


class Ctx:
    """Set by the control service at startup."""
    store = None
    orch = None
    hosts: tuple = ()   # our public hosts: the only origins allowed to open the socket


CTX = Ctx()


def _iso(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat()


class Translator:
    """Server events -> the UI's LiveEvent. One per socket: caption ids and the replay position are per watcher."""

    def __init__(self, store, recipient_of: Callable[[str], Optional[str]], after: int = 0):
        self.store, self.recipient_of = store, recipient_of
        self.n = 0
        self.last_seq = after

    def talker(self, m: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        kind = m.get("kind")
        if kind == "state" and m.get("state") in AGENT_STATES:
            return {"kind": "agent", "state": m["state"]}
        if kind in ("caller", "agent") and str(m.get("text") or "").strip():
            self.n += 1
            # one caption per BolDoc reply: the talker sends it again (same id) as more of the reply plays
            cid = f"r{m['id']}" if kind == "agent" and m.get("id") is not None else f"{kind[0]}{self.n}"
            return {"kind": "caption", "id": cid, "speaker": "you" if kind == "caller" else "agent",
                    "text": str(m["text"]).strip(), "final": True}
        if kind == "ended":
            return {"kind": "call", "state": "ended"}
        return None

    async def event(self, e: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        seq = int(e.get("seq") or 0)
        if seq and seq <= self.last_seq:
            return None  # sent already (replayed, then also published)
        self.last_seq = max(self.last_seq, seq)
        kind, p = str(e.get("type") or ""), e.get("payload") or {}
        if kind == "task.completed" and e.get("doc_id"):
            view = await self.store.document_view(e["doc_id"])
            if view is None:
                return None
            return {"kind": "doc", "doc": view["doc"], "changes": list(p.get("diff_summary") or []),
                    "changed_block_ids": [], "seq": seq}
        if kind.startswith("task.") and kind[5:] in TASK_TYPES:
            # which document: a call can make several, and a task may start a new one or change one it made
            out: Dict[str, Any] = {"kind": "task", "type": kind[5:], "task_id": e.get("task_id") or "",
                                   "doc_id": e.get("doc_id") or "", "seq": seq}
            if p.get("stage"):
                out["stage"] = STAGES.get(p["stage"], str(p["stage"]).capitalize())
            if p.get("text"):
                out["text"] = p["text"]
            return out
        if kind.startswith("delivery.") and e.get("doc_id"):
            status = kind.split(".", 1)[1]
            recipient = self.recipient_of(str(p.get("delivery_id") or "")) or ""
            return {"kind": "delivery", "doc_id": e["doc_id"], "seq": seq, "delivery": {
                "channel": p.get("channel") or "sms", "recipient_masked": "••••• " + recipient[-4:],
                "status": status if status in ("delivered", "failed") else "submitted",
                "version": int(e.get("head_version") or 0), "at": _iso(e.get("ts"))}}
        return None


def _recipient_of(delivery_id: str) -> Optional[str]:
    if CTX.orch is None:
        return None
    d = next((x for x in CTX.orch.s.deliveries.values() if x.id == delivery_id), None)
    return d.recipient if d else None


async def _who(ws: WebSocket) -> Optional[int]:
    """The signed-in user on this socket (the sign-in cookie, as for the rest of the account API)."""
    token = ws.cookies.get(COOKIE)
    if not token or WEB.pool is None:
        return None
    async with WEB.pool.acquire() as con:
        return await con.fetchval(
            "select u.id from auth_sessions s join users u on u.id = s.user_id where s.id_hash=$1 and "
            "s.revoked_at is null and s.expires_at > now() and u.status in ('active', 'trial')",
            hashlib.sha256(token.encode()).hexdigest())


def _origin_ok(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin") or ""
    host = origin.split("://", 1)[-1].split(":")[0]
    return bool(origin) and host in CTX.hosts


@router.websocket("/api/live")
async def live(ws: WebSocket) -> None:
    session = ws.query_params.get("session") or ""
    try:
        after = max(0, int(ws.query_params.get("after") or 0))
    except ValueError:
        after = 0
    user_id = await _who(ws) if _origin_ok(ws) else None
    if user_id is None or WEB.bus is None or WEB.bus.r is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    call = None
    for _ in range(int(ADMIT_WAIT_S * 2)):  # Plivo answers a few seconds after the browser dials
        call = await browser.call_for(session)
        if call is not None:
            break
        await asyncio.sleep(0.5)
    if call is None or call["user_id"] != user_id:
        await ws.close(code=4404)
        return
    sid = call["call_uuid"]
    tr = Translator(CTX.store, _recipient_of, after)
    ps = WEB.bus.r.pubsub()
    await ps.subscribe(live_channel(sid), events_channel(sid))
    try:
        async with WEB.pool.acquire() as con:  # what this watcher missed (or everything, on the first connect)
            rows = await con.fetch("select data from events where session_id=$1 and seq>$2 order by seq", sid, after)
        for r in rows:
            out = await tr.event(json.loads(r["data"]) if isinstance(r["data"], str) else r["data"])
            if out:
                await ws.send_json(out)
        ended_at: Optional[float] = None
        loop = asyncio.get_running_loop()
        receive = asyncio.ensure_future(ws.receive())  # notices the browser leaving
        while ended_at is None or loop.time() - ended_at < LINGER_S:
            msg = await ps.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if receive.done():
                break
            if msg is None:
                continue
            data = json.loads(msg["data"])
            out = tr.talker(data) if msg["channel"] == live_channel(sid) else await tr.event(data)
            if out:
                await ws.send_json(out)
                if out["kind"] == "call" and out["state"] == "ended":
                    ended_at = loop.time()
        receive.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as e:
        logger.warning(f"live view for call {sid[-8:]} stopped: {type(e).__name__}")
    finally:
        with contextlib.suppress(Exception):
            await ps.unsubscribe()
            await ps.aclose()
        with contextlib.suppress(Exception):
            await ws.close()
