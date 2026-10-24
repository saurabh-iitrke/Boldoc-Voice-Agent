"""Renderer registry: canonical Document -> file bytes for each supported format."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Dict

from ..model import Document

FORMATS = ("docx", "pdf", "xlsx", "pptx", "csv", "md", "txt", "html", "json")

MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "csv": "text/csv; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "json": "application/json",
}


class RendererUnavailable(RuntimeError):
    """Raised when a renderer's system dependency (e.g. WeasyPrint/Pango) is missing."""


@dataclass(frozen=True)
class RenderedFile:
    data: bytes
    fmt: str
    mime: str
    filename: str  # ASCII-safe; use content_disposition for the UTF-8 original


def ascii_slug(title: str, max_len: int = 60) -> str:
    """'छुट्टी आवेदन / Leave Application' -> 'Leave_Application' (fallback 'document')."""
    norm = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "_", norm).strip("_")[:max_len].strip("_")
    return slug or "document"


def content_disposition(title: str, fmt: str, version: int) -> str:
    """RFC 6266/5987 header: ASCII fallback + UTF-8 original filename."""
    from urllib.parse import quote

    ascii_name = f"{ascii_slug(title)}_v{version}.{fmt}"
    utf8_name = quote(f"{title.strip() or 'document'}_v{version}.{fmt}", safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"


def _registry() -> Dict[str, Callable[[Document], bytes]]:
    from . import docx_r, html_r, pdf_r, pptx_r, text_r, xlsx_r

    return {
        "docx": docx_r.render, "xlsx": xlsx_r.render, "pptx": pptx_r.render, "pdf": pdf_r.render,
        "html": html_r.render, "md": text_r.render_md, "txt": text_r.render_txt,
        "csv": text_r.render_csv, "json": text_r.render_json,
    }


BLANK = "______________"
_MARK = re.compile(r"\[[^\[\]]{2,60}\]")


def for_print(doc: Document) -> Document:
    """The document as the caller gets it: a fact they chose to leave out is a blank to fill in by hand, and the
    writer's own notes (doc.assumptions) are never printed. The blanks are exactly the placeholders the writer
    recorded there ("[Your designation]"), never other brackets."""
    marks = {m for a in doc.assumptions for m in _MARK.findall(a)}
    if not marks and not doc.assumptions:
        return doc

    def blank(v):
        if isinstance(v, str):
            for m in marks:
                v = v.replace(m, BLANK)
            return v
        if isinstance(v, list):
            return [blank(x) for x in v]
        if isinstance(v, dict):
            return {k: (x if k in ("id", "type") else blank(x)) for k, x in v.items()}
        return v
    data = blank(doc.model_dump())
    data["assumptions"] = []
    return Document.model_validate(data)


def render(doc: Document, fmt: str) -> RenderedFile:
    fmt = fmt.lower().lstrip(".")
    if fmt not in FORMATS:
        raise ValueError(f"unsupported format {fmt!r}; supported: {', '.join(FORMATS)}")
    data = _registry()[fmt](doc if fmt == "json" else for_print(doc))
    return RenderedFile(data=data, fmt=fmt, mime=MIME[fmt], filename=f"{ascii_slug(doc.title)}_v{doc.version}.{fmt}")
