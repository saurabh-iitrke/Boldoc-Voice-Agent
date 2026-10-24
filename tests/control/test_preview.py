"""The document preview shows each format as the file itself."""
import asyncio

from apps.control import preview


class Store:
    def __init__(self):
        self.files = {("d", 2, "pdf"): b"%PDF-1.7 doc", ("d", 2, "docx"): b"PK docx", ("d", 2, "md"): b"# Leave"}
        self.saved = []

    async def head_version(self, doc_id):
        return 2

    async def rendition(self, doc_id, fmt, version=None):
        data = self.files.get((doc_id, version or 2, fmt))
        mime = {"pdf": "application/pdf", "md": "text/markdown; charset=utf-8"}.get(fmt, "application/pdf")
        if data is None:
            return None
        return {"filename": f"Leave_v2.{fmt.split('.')[0]}", "mime": mime, "data": data, "sha256": "x"}

    async def save_rendition(self, doc_id, version, fmt, filename, mime, data):
        self.saved.append((doc_id, version, fmt, filename))
        self.files[(doc_id, version, fmt)] = data


def test_an_office_file_is_converted_once_and_kept_with_its_version(monkeypatch):
    calls = []

    async def convert(data, filename, url=None, timeout_s=40.0):
        calls.append(filename)
        return b"%PDF-1.7 from " + data

    monkeypatch.setattr(preview, "convert", convert)
    s = Store()

    async def go():
        first = await preview.preview(s, "d", "docx")
        again = await preview.preview(s, "d", "DOCX", 2)
        return first, again
    first, again = asyncio.run(go())
    assert first == again == {"mime": "application/pdf", "data": b"%PDF-1.7 from PK docx"}
    assert calls == ["Leave_v2.docx"] and s.saved == [("d", 2, "docx.pdf", "Leave_v2.pdf")]
    assert asyncio.run(preview.preview(s, "d", "pdf"))["data"] == b"%PDF-1.7 doc"  # the PDF is the file itself
    assert asyncio.run(preview.preview(s, "d", "xlsx")) is None                    # no such format: nothing


def test_a_failed_conversion_is_no_preview_and_nothing_is_kept(monkeypatch):
    async def convert(data, filename, url=None, timeout_s=40.0):
        return None
    monkeypatch.setattr(preview, "convert", convert)
    s = Store()
    assert asyncio.run(preview.preview(s, "d", "docx")) is None and s.saved == []


def test_text_shows_as_text_and_html_is_sandboxed():
    s = Store()
    md = asyncio.run(preview.preview(s, "d", "md"))
    r = preview.response(md, "md", {"Cache-Control": "no-store"})
    assert r.media_type.startswith("text/plain") and r.headers["content-disposition"] == "inline"
    h = preview.response({"mime": "text/html; charset=utf-8", "data": b"<p>x</p>"}, "html", {})
    assert h.headers["content-security-policy"].startswith("sandbox;")
