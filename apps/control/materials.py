"""Files a caller adds for BolDoc to use: a project estimate, a price list, last year's report, a photo of
a quotation. Signed-up owners add them on the website: on a long document's page (for the writer working on it, or
for later changes) or on their home page, for their next document (the next long document they ask for on a call
takes them: service.py, at create).

Only the text is kept, never the file. PDF, Word, Excel, PowerPoint, CSV and text are read in a process of their own
(extract.py); a photo, or a PDF that is a scan, by Sarvam's document reader (vision.py). Hermes reads the text with
get_material (bigdoc.py): the caller's own facts, information never instructions. Kept 30 days (retention.py).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from .extract import extract_async
from .vision import SarvamDocs, VisionError

# extension -> (kind, media type)
KINDS: Dict[str, tuple] = {
    ".pdf": ("pdf", "application/pdf"),
    ".docx": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".pptx": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ".csv": ("csv", "text/csv"), ".txt": ("txt", "text/plain"),
    ".jpg": ("image", "image/jpeg"), ".jpeg": ("image", "image/jpeg"), ".png": ("image", "image/png"),
}
MAX_FILES = 10                  # for a document, or for the owner's next one
MAX_BYTES = 20_000_000          # all of them together
# what a kind's file starts with: a mislabelled file is turned away before it is read
_MAGIC = {"pdf": (b"%PDF-",), "docx": (b"PK",), "xlsx": (b"PK",), "pptx": (b"PK",),
          "image": (b"\xff\xd8\xff", b"\x89PNG")}
READABLE = "BolDoc reads PDF, Word, Excel, PowerPoint, CSV and text files, and photos (JPG, PNG)."


class MaterialError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def kind_of(name: str, data: bytes) -> tuple:
    """(kind, media type) of a file, by its name and checked against its first bytes."""
    ext = os.path.splitext(name.lower())[1]
    if ext not in KINDS:
        raise MaterialError(415, "file_type", READABLE)
    kind, mime = KINDS[ext]
    if kind in _MAGIC and not data.startswith(_MAGIC[kind]):
        raise MaterialError(415, "file_type", f"This file isn't really a {ext[1:].upper()} file. {READABLE}")
    return kind, mime


def _view(r) -> Dict[str, Any]:
    return {"id": r["id"], "name": r["name"], "kind": r["kind"], "size": r["size_bytes"], "status": r["status"],
            "pages": r["pages"], "read_by": r["method"], "error": r["error"], "doc_id": r["doc_id"],
            "added_at": r["created_at"].isoformat()}


class Materials:
    def __init__(self, pool, vision: Optional[SarvamDocs] = None):
        self.pool, self.vision = pool, vision
        self._reading: Set[asyncio.Task] = set()

    # -------------------------------------------------------------- the owner's files
    async def add(self, user_id: int, name: str, data: bytes, doc_id: Optional[str] = None,
                  lang: Optional[str] = None) -> Dict[str, Any]:
        """Keep a file's details and read it in the background: {"id", "status": "reading", ...}."""
        name = " ".join(os.path.basename(name or "file").split())[:120] or "file"
        if not data:
            raise MaterialError(400, "empty_file", "That file is empty.")
        if len(data) > MAX_BYTES:
            raise MaterialError(413, "file_too_big", "That file is over 20 MB. Add a smaller one, or a few pages "
                                                     "of it.")
        kind, mime = kind_of(name, data)
        async with self.pool.acquire() as con:
            have = await con.fetchrow(
                "select count(*) as n, coalesce(sum(size_bytes), 0) as b from materials where user_id = $1 and "
                "status <> 'failed' and (doc_id = $2 or ($2::text is null and doc_id is null and task_id is null))",
                user_id, doc_id)
            if have["n"] >= MAX_FILES:
                raise MaterialError(409, "too_many_files", f"{MAX_FILES} files is the most a document can use. "
                                                           "Remove one to add another.")
            if have["b"] + len(data) > MAX_BYTES:
                raise MaterialError(413, "too_much", "Together these files would be over 20 MB. Remove one, or add "
                                                     "a smaller one.")
            mid = "mat_" + secrets.token_hex(8)
            row = await con.fetchrow(
                "insert into materials (id, user_id, doc_id, name, kind, size_bytes, sha256) values ($1, $2, $3, $4, "
                "$5, $6, $7) returning *", mid, user_id, doc_id, name, kind, len(data),
                hashlib.sha256(data).hexdigest())
        task = asyncio.get_running_loop().create_task(self._read(mid, kind, mime, name, data, lang))
        self._reading.add(task)
        task.add_done_callback(self._reading.discard)
        return _view(row)

    async def list(self, user_id: int, doc_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """A document's files, or (no doc_id) the ones waiting for the owner's next document."""
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                "select * from materials where user_id = $1 and (doc_id = $2 or ($2::text is null and doc_id is null "
                "and task_id is null)) order by created_at", user_id, doc_id)
        return [_view(r) for r in rows]

    async def remove(self, user_id: int, mid: str) -> bool:
        async with self.pool.acquire() as con:
            return (await con.execute("delete from materials where id = $1 and user_id = $2", mid,
                                      user_id)).endswith(" 1")

    # -------------------------------------------------------------- the writer's side
    async def for_doc(self, doc_id: str) -> List[Dict[str, Any]]:
        """A document's files: the text of those read, and the others' status (reading, failed)."""
        async with self.pool.acquire() as con:
            rows = await con.fetch("select name, kind, status, pages, method, text, error from materials where "
                                   "doc_id = $1 order by created_at", doc_id)
        return [dict(r) for r in rows]

    async def attach_staged(self, user_id: int, doc_id: str, task_id: str) -> List[str]:
        """The files waiting for the owner's next document go to this one (a long document asked for on a call)."""
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                "update materials set doc_id = $2, task_id = $3, updated_at = now() where user_id = $1 and doc_id is "
                "null and task_id is null and status <> 'failed' returning name", user_id, doc_id, task_id)
        return [r["name"] for r in rows]

    async def staged(self, user_id: int) -> List[str]:
        """Names of the files waiting for the owner's next document (BolDoc mentions them on a call)."""
        async with self.pool.acquire() as con:
            rows = await con.fetch("select name from materials where user_id = $1 and doc_id is null and task_id is "
                                   "null and status <> 'failed' order by created_at", user_id)
        return [r["name"] for r in rows]

    # -------------------------------------------------------------- reading
    async def _read(self, mid: str, kind: str, mime: str, name: str, data: bytes, lang: Optional[str]) -> None:
        text: Optional[str] = None
        pages: Optional[int] = None
        method = error = None
        try:
            if kind == "image":
                text, pages = await self._vision(data, name, mime, lang, 1)
                method = "vision"
            else:
                text, pages, scanned = await extract_async(kind, data)
                method = "text"
                if scanned:  # a PDF of page images: the reader reads it
                    text, pages = await self._vision(data, name, mime, lang, pages)
                    method = "vision"
            text = (text or "").strip()
            if not text:
                raise ValueError("there's no text in it BolDoc could read")
        except (ValueError, VisionError) as e:
            text, error = None, str(e)[:200]
        except Exception as e:  # never leave a file "reading" for ever
            logger.exception(f"material {mid}: reading failed")
            text, error = None, f"it couldn't be read ({type(e).__name__})"
        status = "ready" if text else "failed"
        async with self.pool.acquire() as con:
            await con.execute("update materials set status = $2, text = $3, pages = $4, method = $5, error = $6, "
                              "updated_at = now() where id = $1", mid, status, text, pages, method, error)
        logger.info(f"material {mid}: {status} ({kind}, {method or '-'}, {pages or 0} pages, {len(text or '')} chars)")

    async def _vision(self, data: bytes, name: str, mime: str, lang: Optional[str], pages: int) -> tuple:
        if self.vision is None:
            raise ValueError("photos and scans can't be read right now")
        return await self.vision.read(data, name, mime, lang or "en-IN", max(1, pages or 1))

    async def settle(self) -> None:
        """Wait for the files being read (tests; a clean shutdown)."""
        if self._reading:
            await asyncio.gather(*self._reading, return_exceptions=True)
