"""The document preview shows each format as the file itself : the PDF as it is; a Word, Excel or PowerPoint file as
LibreOffice lays it out, converted to PDF once per version and kept with its renditions ("docx.pdf"); text formats as
their text. The website draws the PDFs with pdf.js, so what the caller sees is what they download.

Conversions run one at a time in their own container (infra/convert), CPU-capped, so they never slow a live call.
"""
from __future__ import annotations

import asyncio
from typing import Any, Iterable, Optional

import httpx
from loguru import logger

from .settings import SETTINGS

OFFICE = ("docx", "xlsx", "pptx")
TEXT = ("md", "txt", "csv")
_ONE_AT_A_TIME: Optional[asyncio.Semaphore] = None  # made on first use, inside the running loop


async def convert(data: bytes, filename: str, url: Optional[str] = None, timeout_s: float = 40.0) -> Optional[bytes]:
    """An office file as a PDF (Gotenberg's LibreOffice route); None if the converter isn't there or fails."""
    global _ONE_AT_A_TIME
    if _ONE_AT_A_TIME is None:
        _ONE_AT_A_TIME = asyncio.Semaphore(1)
    async with _ONE_AT_A_TIME:
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                r = await client.post(f"{url or SETTINGS.convert_url}/forms/libreoffice/convert",
                                      files={"files": (filename, data)})
                r.raise_for_status()
                return r.content if r.content.startswith(b"%PDF") else None
        except Exception as e:  # the preview says it isn't available; downloads are unaffected
            logger.warning(f"preview: converting {filename.rsplit('.', 1)[-1]} failed ({type(e).__name__})")
            return None


async def preview(store: Any, doc_id: str, fmt: str, version: Optional[int] = None) -> Optional[dict]:
    """What a format looks like: {"mime", "data"}, the PDF for pdf and office formats, the text for text ones."""
    fmt = fmt.lower()
    if fmt == "pdf" or fmt in TEXT or fmt == "html":
        f = await store.rendition(doc_id, fmt, version)
        return {"mime": f["mime"], "data": bytes(f["data"])} if f else None
    if fmt not in OFFICE:
        return None
    if version is None:
        version = await store.head_version(doc_id)
    cached = await store.rendition(doc_id, f"{fmt}.pdf", version)
    if cached is not None:
        return {"mime": "application/pdf", "data": bytes(cached["data"])}
    src = await store.rendition(doc_id, fmt, version)
    if src is None:
        return None
    pdf = await convert(bytes(src["data"]), src["filename"])
    if pdf is None:
        return None
    await store.save_rendition(doc_id, version, f"{fmt}.pdf", src["filename"].rsplit(".", 1)[0] + ".pdf",
                               "application/pdf", pdf)
    return {"mime": "application/pdf", "data": pdf}


async def warm(store: Any, doc_id: str, version: int, formats: Iterable[str]) -> None:
    """Right after a version is written: its office formats converted ahead, so the preview opens at once."""
    for fmt in formats:
        if fmt in OFFICE:
            await preview(store, doc_id, fmt, version)


def response(p: dict, fmt: str, no_store: dict) -> Any:
    """Shown in the page, never downloaded: a PDF inline, text as plain text, the HTML file sandboxed (its own
    content only: no scripts, no requests)."""
    from fastapi import Response
    headers = {**no_store, "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"}
    if fmt in TEXT:
        return Response(content=p["data"], media_type="text/plain; charset=utf-8", headers=headers)
    if fmt == "html":
        headers["Content-Security-Policy"] = ("sandbox; default-src 'none'; style-src 'unsafe-inline'; "
                                              "img-src data:; font-src data:")
    return Response(content=p["data"], media_type=p["mime"], headers=headers)
