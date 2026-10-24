"""A signed-up owner's files for BolDoc: add, list, remove.

A file comes as the request's body (no multipart: nothing to parse but the bytes), its name in X-File-Name, and the
site's X-BolDoc-Client header like every change. It is for a long document of theirs (?doc_id=, being written or
written: for the writer, and for later changes) or, without one, for their next document. Reading happens in the
background (materials.py); the list shows each file's status until it's read.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from fastapi import APIRouter, Request

from ..api_errors import ApiError
from ..materials import MAX_BYTES, MaterialError
from . import web
from .documents import owned_doc_ids
from .web import _require_client_header, require_user

router = APIRouter()
UPLOADS_A_DAY = 60


class Ctx:
    materials = None   # materials.Materials
    orch = None        # the orchestrator (which documents are long, their language)


CTX = Ctx()


def _materials():
    if CTX.materials is None:
        raise ApiError(503, "files_off", "Adding files isn't available right now. Try again later.")
    return CTX.materials


async def _target(con, user: Dict[str, Any], doc_id: Optional[str]) -> Optional[str]:
    """A long document of the owner's, or None: their next document."""
    if not doc_id:
        return None
    if not await owned_doc_ids(con, user, doc_id):
        raise ApiError(404, "not_found", "This document doesn't exist or isn't yours.")
    rec = CTX.orch.s.docs.get(doc_id) if CTX.orch is not None else None
    if rec is None or not rec.long:
        raise ApiError(409, "not_long", "Files are for long documents: reports, plans, proposals and decks.")
    return doc_id


@router.post("/api/files")
async def add_file(request: Request, doc_id: Optional[str] = None) -> Dict[str, Any]:
    _require_client_header(request)
    mats = _materials()
    if int(request.headers.get("content-length") or 0) > MAX_BYTES:
        raise ApiError(413, "file_too_big", "That file is over 20 MB. Add a smaller one, or a few pages of it.")
    async with web.CTX.pool.acquire() as con:
        user = await require_user(request, con)
        target = await _target(con, user, doc_id)
    if web.CTX.bus is not None and not await web.CTX.bus.allow(f"files:{user['id']}", UPLOADS_A_DAY, 86400):
        raise ApiError(429, "too_many_files", "That's a lot of files for one day. Try again tomorrow.")
    rec = CTX.orch.s.docs.get(target) if target and CTX.orch is not None else None
    try:
        return await mats.add(user["id"], unquote(request.headers.get("X-File-Name") or ""), await request.body(),
                              target, rec.doc_lang if rec else None)
    except MaterialError as e:
        raise ApiError(e.status, e.code, e.message) from None


@router.get("/api/files")
async def list_files(request: Request, doc_id: Optional[str] = None) -> List[Dict[str, Any]]:
    mats = _materials()
    async with web.CTX.pool.acquire() as con:
        user = await require_user(request, con)
        if doc_id and not await owned_doc_ids(con, user, doc_id):
            raise ApiError(404, "not_found", "This document doesn't exist or isn't yours.")
    return await mats.list(user["id"], doc_id or None)


@router.delete("/api/files/{file_id}")
async def remove_file(file_id: str, request: Request) -> Dict[str, Any]:
    _require_client_header(request)
    mats = _materials()
    async with web.CTX.pool.acquire() as con:
        user = await require_user(request, con)
    if not await mats.remove(user["id"], file_id):
        raise ApiError(404, "not_found", "That file isn't there any more.")
    return {"ok": True}
