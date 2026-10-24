"""Scans and photos read by Sarvam's document reader (vision.py)."""
import asyncio
import io
import json
import zipfile

import httpx
import pytest

from apps.control import vision
from apps.control.vision import SarvamDocs, VisionError, md_text


def zipped(md: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("quote.png/quote.md", md)
        z.writestr("quote.png/metadata/page_001.json", "{}")
        z.writestr("manifest.json", json.dumps({"status": "completed"}))
    return buf.getvalue()


MD = ("QUOTATION No. 2026/118\n\n<table><tr><td>Item</td><td>Amount</td></tr><tr><td>Cafe chairs</td><td>50,000</td>"
      "</tr><tr><td>Total</td><td></td><td>1,00,000</td><td></td></tr></table>")


def reader(states, md=MD, create_status=201):
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, req.headers.get("api-subscription-key")))
        if req.url.path.endswith("/digitise"):
            assert b'name="output_format"' in req.content and b"md" in req.content
            return httpx.Response(create_status, json={"job_id": "j1", "status": "pending"})
        if req.url.path.endswith("/status"):
            st = states.pop(0) if len(states) > 1 else states[0]
            return httpx.Response(200, json={"job_id": "j1", "status": st,
                                             "usage": {"pages_succeeded": 1 if st == "completed" else 0}})
        if req.url.path.endswith("/download-url"):
            return httpx.Response(200, json={"method": "GET", "url": "https://blob.example/j1.zip"})
        return httpx.Response(200, content=zipped(md))
    return SarvamDocs("k-test", httpx.AsyncClient(transport=httpx.MockTransport(handler)), poll_s=0.01), seen


def test_a_photo_is_read_through_a_job_and_its_tables_come_back_row_by_row():
    docs, seen = reader(["pending", "processing", "completed"])
    text, pages = asyncio.run(docs.read(b"\x89PNG...", "quote.png", "image/png", "en-IN"))
    assert text == "QUOTATION No. 2026/118\n\nItem | Amount\nCafe chairs | 50,000\nTotal |  | 1,00,000" and pages == 1
    assert seen[0] == ("POST", "/doc-ai/v1/job/digitise", "k-test") and seen[-1][1] == "/j1.zip"


def test_a_job_that_fails_or_is_refused_is_said_plainly():
    with pytest.raises(VisionError, match="couldn't read it"):
        asyncio.run(reader(["failed"])[0].read(b"\x89PNG", "x.png", "image/png"))
    with pytest.raises(VisionError, match="refused it"):
        asyncio.run(reader(["completed"], create_status=400)[0].read(b"\x89PNG", "x.png", "image/png"))


def test_a_long_scan_goes_in_parts_of_ten_pages():
    pypdf = pytest.importorskip("pypdf")
    w = pypdf.PdfWriter()
    for _ in range(12):
        w.add_blank_page(200, 200)
    buf = io.BytesIO()
    w.write(buf)
    parts = vision.pdf_parts(buf.getvalue(), 12)
    assert [len(pypdf.PdfReader(io.BytesIO(p)).pages) for p in parts] == [10, 2]
    assert len(vision.pdf_parts(buf.getvalue() * 1, 12)) == 2 and vision.READ_PAGES == 30


def test_markdown_tables_and_tags_become_plain_text():
    assert md_text("<b>Hi</b>\n\n\n\nthere") == "Hi\n\nthere"
    assert md_text("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>") == "A | B\n1 | 2"
