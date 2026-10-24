"""A signed-in user's documents: list, open, download. Read-only here; changes go through the orchestrator.

A document belongs to the account of the number whose call created it: calls are linked to the user at admission
(call_records.user_id), and documents from before accounts existed are matched by the calling number.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from docengine.renderers import content_disposition

from .. import preview as doc_preview
from ..api_errors import ApiError
from . import users
from .web import CTX, require_user

router = APIRouter()
NO_STORE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"}
# made by any of the account's numbers (users.phones), or on any of its calls
OWNED = ("(d.owner = any($2::text[]) or d.session_id in (select call_uuid from call_records where user_id = $1))")


# A long document being written has no version yet, and is its owner's all the same: its page shows how far the
# writer is, takes answers to its questions and files for it (the SMS link opens it while it's written).
WRITING = ("exists (select 1 from entities e where e.kind = 'task' and e.data->>'doc_id' = d.id and "
           "e.data->>'tier' = 'T3' and e.data->>'state' in ('queued', 'running', 'awaiting_input', 'committing'))")
SHOWN = f"(d.head_version > 0 or {WRITING})"


async def owned_doc_ids(con, user: Dict[str, Any], doc_id: Optional[str] = None) -> List[str]:
    if doc_id is None:
        rows = await con.fetch(f"select d.id from documents d where {OWNED} and {SHOWN} "
                               "order by d.updated_at desc limit 200", user["id"], await users.phones(con, user))
    else:
        rows = await con.fetch(f"select d.id from documents d where {OWNED} and d.id = $3 and {SHOWN}",
                               user["id"], await users.phones(con, user), doc_id)
    return [r["id"] for r in rows]


def _clip(text: str, limit: int = 140) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def hint_of(title: str, subject: Optional[str], blocks: Any) -> Optional[str]:
    """One line of what a document says, for the list : its subject when it says something, else its first heading other
    than the title, else its first sentence of some length (not "To," or "Respected Sir,")."""
    if subject and len(subject.split()) >= 3:
        return _clip(subject)
    for b in (json.loads(blocks) if isinstance(blocks, str) else blocks) or []:
        if b.get("type") == "heading" and len(str(b.get("text") or "").split()) >= 3 and \
                str(b["text"]).strip().lower() != (title or "").strip().lower():
            return _clip(str(b["text"]))
        if b.get("type") == "paragraph":
            text = "".join(str(r.get("text") or "") for r in b.get("runs") or [])
            if len(text.split()) >= 5:
                return _clip(text.removeprefix("Subject:").strip())
    return None


@router.get("/api/documents")
async def list_documents(request: Request) -> List[Dict[str, Any]]:
    async with CTX.pool.acquire() as con:
        user = await require_user(request, con)
        rows = await con.fetch(
            f"select d.id, d.title, d.head_version, d.formats, d.doc_lang, d.created_at, d.updated_at, "
            f"v.document->'meta'->>'subject' as subject, "
            f"jsonb_path_query_array(v.document, '$.blocks[0 to 7]') as first_blocks "
            f"from documents d left join doc_versions v on v.doc_id = d.id and v.version = d.head_version "
            f"where {OWNED} and {SHOWN} order by d.updated_at desc limit 200",
            user["id"], await users.phones(con, user))
    return [{"id": r["id"], "title": r["title"] or "Untitled", "head_version": r["head_version"],
             "formats": json.loads(r["formats"]) if isinstance(r["formats"], str) else r["formats"],
             "doc_lang": r["doc_lang"], "hint": hint_of(r["title"], r["subject"], r["first_blocks"]),
             "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat()} for r in rows]


@router.get("/api/documents/{doc_id}")
async def get_document(doc_id: str, request: Request) -> Dict[str, Any]:
    async with CTX.pool.acquire() as con:
        user = await require_user(request, con)
        if not await owned_doc_ids(con, user, doc_id):
            raise ApiError(404, "not_found", "This document doesn't exist or isn't yours.")
    view = await CTX.store.document_view(doc_id)
    if view is None:
        writing = _writing(doc_id)
        if writing is None:
            raise ApiError(404, "not_found", "This document doesn't exist or isn't yours.")
        return writing
    d = view["delivery"]
    delivery = None
    if d:
        delivery = {"channel": d["channel"], "recipient_masked": "••••• " + str(d["recipient"])[-4:],
                    "status": d["status"] if d["status"] in ("delivered", "failed") else "submitted",
                    "version": d["version"], "at": view["updated_at"]}
    rec = CTX.store.orch.s.docs.get(doc_id) if getattr(CTX.store, "orch", None) is not None else None
    return {"doc": view["doc"], "versions": view["versions"], "formats": view["formats"], "delivery": delivery,
            "questions": _questions(doc_id), "long": bool(rec and rec.long)}


def _writing_task(doc_id: str):
    orch = getattr(CTX.store, "orch", None)
    if orch is None:
        return None
    return next((t for t in orch.s.tasks.values() if t.doc_id == doc_id and t.tier == "T3"
                 and t.state.value in ("queued", "running", "awaiting_input", "committing")), None)


def _writing(doc_id: str) -> Optional[Dict[str, Any]]:
    """A long document still being written (nothing committed yet): its title, the outline the caller agreed, how
    far the writer is, and its questions (the SMS link with the questions opens this page)."""
    t = _writing_task(doc_id)
    if t is None:
        return None
    orch = CTX.store.orch
    rec, brief = orch.s.docs.get(doc_id), (t.big or {}).get("brief") or {}
    stage = next((e.payload.get("stage") for e in reversed(orch.s.events.get(t.session_id, []))
                  if e.type == "task.progress" and e.task_id == t.id), None)
    title = (rec.title if rec else "") or brief.get("title") or brief.get("document") or "Your document"
    lang = rec.doc_lang if rec else "en-IN"
    return {"doc": {"id": doc_id, "title": title, "lang": lang, "script": "", "doc_type": brief.get("doc_type") or
                    "report", "meta": {}, "blocks": [], "version": 0, "assumptions": []},
            "versions": [], "formats": [], "delivery": None, "questions": _questions(doc_id), "long": True,
            "writing": {"state": t.state.value, "stage": stage, "waiting": bool((t.big or {}).get("waiting")),
                        "outline": [str(x.get("title") or "") for x in brief.get("outline") or []],
                        "since": datetime.fromtimestamp(t.created_at, timezone.utc).isoformat()}}


def _waiting_task(doc_id: str):
    """The long document's writer waiting for the caller's answers (apps/control/bigdoc.py), if any."""
    orch = getattr(CTX.store, "orch", None)
    if orch is None:
        return None
    return next((t for t in orch.s.tasks.values() if t.doc_id == doc_id and t.tier == "T3"
                 and (t.big or {}).get("waiting") and (t.big or {}).get("asked")), None)


def _questions(doc_id: str) -> List[Dict[str, Any]]:
    """What the writer asked that the caller can answer here (the SMS link opens this page)."""
    t = _waiting_task(doc_id)
    if t is None:
        return []
    given = {**(t.big.get("phone_answers") or {}), **(t.big.get("web_answers") or {})}
    return [{"question": q, "answer": given.get(q)} for q in t.big["asked"].get("questions") or []]


class Answers(BaseModel):
    answers: Dict[str, str] = Field(..., max_length=3)


@router.post("/api/documents/{doc_id}/answers")
async def answer_questions(doc_id: str, body: Answers, request: Request) -> Dict[str, Any]:
    """The owner answers the writer's questions on the document's page instead of on a call."""
    async with CTX.pool.acquire() as con:
        user = await require_user(request, con)
        if not await owned_doc_ids(con, user, doc_id):
            raise ApiError(404, "not_found", "This document doesn't exist or isn't yours.")
    t = _waiting_task(doc_id)
    if t is None:
        raise ApiError(409, "not_waiting", "These questions don't need an answer any more.")
    asked = set(t.big["asked"].get("questions") or [])
    given = {q: a.strip()[:500] for q, a in body.answers.items() if q in asked and a.strip()}
    if not given:
        raise ApiError(400, "no_answers", "Write an answer to at least one question.")
    t.big["web_answers"] = {**(t.big.get("web_answers") or {}), **given}
    await CTX.store.flush()
    return {"questions": _questions(doc_id)}


@router.get("/api/documents/{doc_id}/preview/{fmt}")
async def preview_document(doc_id: str, fmt: str, request: Request, v: Optional[int] = None) -> Response:
    """A format as it looks when downloaded (apps/control/preview.py), for the owner's document page."""
    async with CTX.pool.acquire() as con:
        user = await require_user(request, con)
        if not await owned_doc_ids(con, user, doc_id):
            raise ApiError(404, "not_found", "This document doesn't exist or isn't yours.")
    p = await doc_preview.preview(CTX.store, doc_id, fmt, v)
    if p is None:
        raise ApiError(404, "preview_unavailable", "There's no preview of that format; it can still be downloaded.")
    return doc_preview.response(p, fmt.lower(), NO_STORE)


@router.get("/api/documents/{doc_id}/download/{fmt}")
async def download_document(doc_id: str, fmt: str, request: Request, v: Optional[int] = None) -> Response:
    async with CTX.pool.acquire() as con:
        user = await require_user(request, con)
        if not await owned_doc_ids(con, user, doc_id):
            raise ApiError(404, "not_found", "This document doesn't exist or isn't yours.")
    f = await CTX.store.rendition(doc_id, fmt.lower(), v)
    if f is None:
        raise ApiError(404, "format_unavailable", "That format isn't available for this document.")
    view = await CTX.store.document_view(doc_id)
    title, version = view["doc"]["title"], v or view["doc"]["version"]
    return Response(content=bytes(f["data"]), media_type=f["mime"], headers={
        **NO_STORE, "Content-Disposition": content_disposition(title, fmt.lower(), version),
        "X-Content-Type-Options": "nosniff"})
