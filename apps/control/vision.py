"""Scans and photos read by Sarvam's document reader (Document Digitization, `POST /doc-ai/v1/job/digitise`), for the
files a caller adds that have no text of their own.

A job takes one file of at most 10 pages: a longer scan goes in 10-page parts (READ_PAGES at most). The job is polled
until it ends; its result is a ZIP (fetched from the link Sarvam gives) with the text as Markdown, tables as HTML, which
comes back here as text with a table row a line and cells joined by " | ", as extract.py gives tables.
"""
from __future__ import annotations

import asyncio
import io
import re
import time
import zipfile
from typing import List, Optional, Tuple

import httpx

BASE = "https://api.sarvam.ai/doc-ai/v1/job"
JOB_PAGES = 10          # Sarvam's limit for one job
READ_PAGES = 30         # of a scan, at most
DONE = ("completed", "partially_completed", "failed", "rejected")


class VisionError(Exception):
    """The reader couldn't read the file: the message is for the person who added it."""


_CELL = re.compile(r"<t[dh]\b[^>]*>", re.I)   # where a cell starts
_ROW_END = re.compile(r"</tr\s*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def md_text(md: str) -> str:
    """Sarvam's Markdown as plain text: an HTML table a row a line, cells joined by " | "."""
    def table(m: re.Match) -> str:
        rows = []
        for row in _ROW_END.split(m.group(0)):
            cells = [c.strip() for c in _TAG.sub("", _CELL.sub("\x00", row)).split("\x00")][1:]
            while cells and not cells[-1]:
                cells.pop()
            if any(cells):
                rows.append(" | ".join(cells))
        return "\n" + "\n".join(rows) + "\n"
    text = re.sub(r"<table\b.*?</table\s*>", table, md, flags=re.I | re.S)
    text = _TAG.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def pdf_parts(data: bytes, pages: int) -> List[bytes]:
    """A scan longer than one job, in parts of JOB_PAGES (READ_PAGES at most)."""
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for start in range(0, min(pages, READ_PAGES), JOB_PAGES):
        w = PdfWriter()
        for p in reader.pages[start:min(start + JOB_PAGES, pages, READ_PAGES)]:
            w.add_page(p)
        buf = io.BytesIO()
        w.write(buf)
        parts.append(buf.getvalue())
    return parts


class SarvamDocs:
    def __init__(self, key: str, http: Optional[httpx.AsyncClient] = None, poll_s: float = 1.5,
                 timeout_s: float = 180.0):
        self.key, self.poll_s, self.timeout_s = key, poll_s, timeout_s
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))

    def _h(self) -> dict:
        return {"api-subscription-key": self.key}

    async def _job(self, name: str, data: bytes, mime: str, language: Optional[str]) -> Tuple[str, int]:
        form = {"output_format": "md", **({"language": language} if language else {})}
        try:
            r = await self.http.post(f"{BASE}/digitise", headers=self._h(), files={"file": (name, data, mime)},
                                     data=form)
            if r.status_code >= 300:
                raise VisionError(f"the reader refused it (HTTP {r.status_code})")
            job = r.json()["job_id"]
            end, st = time.monotonic() + self.timeout_s, {}
            while time.monotonic() < end:
                st = (await self.http.get(f"{BASE}/{job}/status", headers=self._h())).json()
                if str(st.get("status") or st.get("job_state") or "").lower() in DONE:
                    break
                await asyncio.sleep(self.poll_s)
            state = str(st.get("status") or st.get("job_state") or "").lower()
            if state not in ("completed", "partially_completed"):
                raise VisionError("the reader couldn't read it" if state in DONE else "the reader took too long")
            dl = (await self.http.get(f"{BASE}/{job}/download-url", headers=self._h())).json()
            z = await self.http.request(dl.get("method") or "GET", dl["url"])
            if z.status_code != 200:
                raise VisionError(f"the reader's result couldn't be fetched (HTTP {z.status_code})")
        except httpx.HTTPError as e:
            raise VisionError(f"the reader couldn't be reached ({type(e).__name__})") from None
        zf = zipfile.ZipFile(io.BytesIO(z.content))
        md = [zf.read(n).decode("utf-8", "replace") for n in sorted(zf.namelist()) if n.endswith(".md")]
        pages = int(((st.get("usage") or {}).get("pages_succeeded")) or 0)
        return md_text("\n\n".join(md)), pages

    async def read(self, data: bytes, name: str, mime: str, language: Optional[str] = None,
                   pages: int = 1) -> Tuple[str, int]:
        """(text, pages read). A PDF of more than JOB_PAGES pages goes in parts."""
        if mime == "application/pdf" and pages > JOB_PAGES:
            parts = await asyncio.to_thread(pdf_parts, data, pages)
        else:
            parts = [data]
        texts, read = [], 0
        for i, part in enumerate(parts):
            text, n = await self._job(name if len(parts) == 1 else f"part{i + 1}-{name}", part, mime, language)
            texts.append(text)
            read += n
        return "\n\n".join(t for t in texts if t), read
