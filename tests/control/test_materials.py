"""Files a caller adds for BolDoc: only signed-up owners, read in the background, at most 10 and 20 MB
for a document (or for their next one), a long document only; the text kept, never the file."""
import io

import pytest

from tests.control.helpers import H, create_cmd, open_call, wait_for
from tests.control.test_accounts import W, sign_in, with_redis

pytest.importorskip("asyncpg")


def word_file(text="Quotation: coffee machine Rs 1,20,000") -> bytes:
    from docx import Document
    d = Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def upload(c, name, data, doc_id=None, headers=None):
    q = f"?doc_id={doc_id}" if doc_id else ""
    return c.post(f"/api/files{q}", content=data, headers={**W, "X-File-Name": name,
                                                           "Content-Type": "application/octet-stream",
                                                           **(headers or {})})


class FakeReader:
    """Sarvam's document reader, without the network: what a photo says."""
    async def read(self, data, name, mime, language, pages=1):
        return "Sharma Furniture Works quotation\nCafe chairs | 20 | 50,000", 1


def test_an_owner_adds_files_for_their_next_document_and_they_are_read(app_factory):
    from apps.control import service
    with app_factory() as c:
        with_redis(c)
        assert upload(c, "q.docx", word_file()).status_code == 401  # signed out
        sign_in(c, "+919988776655")
        service.S.materials.vision = FakeReader()
        first = upload(c, "Quotation for cafe.docx", word_file())
        assert first.status_code == 200 and first.json()["status"] == "reading" and first.json()["doc_id"] is None
        assert upload(c, "photo of quote.jpg", b"\xff\xd8\xff\xe0 a photo").json()["status"] == "reading"
        c.portal.call(service.S.materials.settle)
        files = c.get("/api/files").json()
        assert [(f["name"], f["status"], f["read_by"]) for f in files] == [
            ("Quotation for cafe.docx", "ready", "text"), ("photo of quote.jpg", "ready", "vision")]
        # not readable, not what it says, too big, too many: said plainly
        assert upload(c, "virus.exe", b"MZ...").status_code == 415
        assert upload(c, "fake.pdf", b"PK\x03\x04 not a pdf").json()["code"] == "file_type"
        assert upload(c, "big.pdf", b"%PDF-", headers={"Content-Length": str(25_000_000)}).status_code == 413
        for i in range(8):
            assert upload(c, f"note{i}.txt", b"some notes").status_code == 200
        assert upload(c, "eleventh.txt", b"x").json()["code"] == "too_many_files"
        assert c.post("/api/files", content=b"x", headers={"X-File-Name": "a.txt"}).status_code == 403  # no site header
        # removed: gone, and only by its owner
        fid = files[0]["id"]
        assert c.delete(f"/api/files/{fid}", headers=W).json() == {"ok": True}
        assert c.delete(f"/api/files/{fid}", headers=W).status_code == 404
        c.portal.call(service.S.materials.settle)


def test_files_go_to_a_long_document_only_and_only_the_owners(app_factory):
    from apps.control import service
    with app_factory() as c:
        with_redis(c)
        sign_in(c, "+919988776655")
        r = open_call(c, "fm1").json()
        short = c.post("/internal/commands", json=create_cmd("fm1", r["epoch"]), headers=H).json()["doc_id"]
        wait_for(c, "fm1", 0, "task.completed")
        assert upload(c, "q.docx", word_file(), doc_id=short).json()["code"] == "not_long"
        service.S.orch.s.docs[short].long = True  # a long document (Hermes wrote it): files may go to it
        ok = upload(c, "q.docx", word_file(), doc_id=short)
        assert ok.status_code == 200 and ok.json()["doc_id"] == short
        c.portal.call(service.S.materials.settle)
        assert [f["name"] for f in c.get(f"/api/files?doc_id={short}").json()] == ["q.docx"]
        assert c.get("/api/files").json() == []  # not waiting for a next document
        c.post("/api/auth/logout", headers=W)
        sign_in(c, "+919876500044")  # someone else
        assert upload(c, "q.docx", word_file(), doc_id=short).status_code == 404
        assert c.get(f"/api/files?doc_id={short}").status_code == 404
