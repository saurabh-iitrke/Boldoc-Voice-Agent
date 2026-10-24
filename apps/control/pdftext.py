"""A PDF's text, in a process of its own: pypdf is pure Python, and a long PDF would hold control's interpreter (and
the live calls it serves) for seconds. Reads the PDF on stdin, writes {"title", "text", "pages"} as JSON on stdout.

    python -m apps.control.pdftext [max_pages] < file.pdf
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
from typing import Tuple


def extract(data: bytes, max_pages: int = 40) -> Tuple[str, str, int]:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("the PDF is password-protected")
    meta = reader.metadata
    title = str((meta.title if meta else None) or "").strip()
    pages = reader.pages[:max_pages]
    return title, "\n\n".join((p.extract_text() or "").strip() for p in pages), len(pages)


async def pdf_text(data: bytes, max_pages: int = 40, timeout_s: float = 25.0) -> Tuple[str, str, int]:
    """(title, text, pages read). Raises ValueError when the PDF can't be read in time."""
    proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "apps.control.pdftext", str(max_pages),
                                                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(data), timeout_s)
    except asyncio.TimeoutError:
        raise ValueError("the PDF took too long to read") from None
    finally:
        if proc.returncode is None:  # timed out, or the read was cancelled: the process goes too
            proc.kill()
    if proc.returncode != 0:
        raise ValueError("the PDF couldn't be read")
    res = json.loads(out.decode("utf-8"))
    if res.get("error"):
        raise ValueError(res["error"])
    return res["title"], res["text"], res["pages"]


def main() -> None:
    max_pages = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    try:
        title, text, pages = extract(sys.stdin.buffer.read(), max_pages)
        out = {"title": title, "text": text, "pages": pages}
    except Exception as e:  # a broken or locked PDF: the caller says so, the process exits cleanly
        out = {"error": str(e)[:200] if isinstance(e, ValueError) else "the PDF couldn't be read"}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
