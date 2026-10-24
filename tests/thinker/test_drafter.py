"""Deterministic parts of the thinker: JSON extraction/repair, model JSON -> Document, assumptions, i18n dates."""
import asyncio
import json

import httpx
import pytest

from apps.thinker.drafter import _real_assumptions, _to_document, conversation_text
from apps.thinker.llm import ThinkerError, _extract_json, chat_json
from docengine.i18n import localize_dates

RAW = {
    "title": "Leave Application", "lang": "en-IN", "doc_type": "application",
    "meta": {"date": "21 October 2026", "to": "HR Manager, Acme Technologies", "subject": "Leave from 25 to 27 October",
             "bogus": "dropped"},
    "blocks": [
        {"type": "paragraph", "text": "To: HR Manager, Acme Technologies", "id": "model-id"},
        {"type": "paragraph", "text": "Subject: Leave from 25 to 27 October"},
        {"type": "paragraph", "text": "Dear Sir/Madam,"},
        {"type": "paragraph", "text": "I request leave from 25 October to 27 October 2026. [Your employee ID]"},
        {"type": "paragraph", "text": "   "},
        {"type": "table", "columns": [{"name": "Day", "type": "date"}], "rows": [["25 October 2026"]]},
        {"type": "signature", "closing": "Yours sincerely,", "name": "Saurabh Kumar"},
        {"no_type": True},
    ],
    "assumptions": ["Caller wants PDF and Word formats", "Name is Saurabh Kumar", "[Manager name] not given"],
}


def test_extract_json_handles_fences_and_prose():
    assert _extract_json('Sure! ```json\n{"a": 1}\n``` done') == {"a": 1}
    assert _extract_json('Here you go: {"a": {"b": 2}} hope it helps') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        _extract_json("no json here")


def test_to_document_assigns_ids_drops_meta_echo_and_noise():
    doc = _to_document(RAW, "doc_1")
    texts = [b.text for b in doc.blocks if b.type == "paragraph"]
    assert texts == ["Dear Sir/Madam,", "I request leave from 25 October to 27 October 2026. [Your employee ID]"]
    assert all(b.id and b.id != "model-id" for b in doc.blocks)
    assert set(doc.meta) == {"date", "to", "subject"}
    assert doc.doc_type == "application"
    # noise (formats, restated facts) and placeholders not in the text are dropped; missing ones are added
    assert doc.assumptions == ["Fill in [Your employee ID]"]


def test_requested_language_wins_and_hindi_dates_are_localised_except_table_data():
    raw = dict(RAW, lang="en-IN", title="छुट्टी 25 October 2026")
    doc = _to_document(raw, "doc_2", "hi-IN")
    assert doc.lang == "hi-IN"
    assert doc.title == "छुट्टी 25 अक्टूबर 2026"
    assert doc.meta["date"] == "21 अक्टूबर 2026"
    assert "25 अक्टूबर to 27 अक्टूबर 2026" in doc.all_text()
    table = next(b for b in doc.blocks if b.type == "table")
    assert table.rows == [["25 October 2026"]]


def test_heading_that_repeats_the_title_is_dropped():
    raw = dict(RAW, blocks=[{"type": "heading", "level": 1, "text": "Leave application"}] + RAW["blocks"])
    assert not [b for b in _to_document(raw, "d").blocks if b.type == "heading"]


def test_unknown_doc_type_falls_back_and_empty_document_is_rejected():
    assert _to_document(dict(RAW, doc_type="poem"), "d").doc_type == "other"
    with pytest.raises(ValueError):
        _to_document(dict(RAW, blocks=[{"type": "paragraph", "text": "Subject: Leave from 25 to 27 October"}]), "d")


def test_localize_dates():
    assert localize_dates("21 October 2026", "hi-IN") == "21 अक्टूबर 2026"
    assert localize_dates("on 1st Jan, 2027 and 5 Sept", "hi-IN") == "on 1 जनवरी 2027 and 5 सितंबर"
    assert localize_dates("you may go on 3 May", "hi-IN") == "you may go on 3 मई"
    assert localize_dates("21 October 2026", "en-IN") == "21 October 2026"
    assert localize_dates("21 October 2026", "ta-IN") == "21 October 2026"  # no month table yet: unchanged


def test_only_blanks_are_kept_never_notes_about_what_the_writer_chose():
    """The record keeps the placeholders that are in the text (printed as blanks); notes go."""
    doc = _to_document(RAW, "d")
    kept = _real_assumptions(["Assumed the leave is casual leave", "PDF format requested",
                              "The designation was not provided and has been omitted"], doc)
    assert kept == ["Fill in [Your employee ID]"]  # the one placeholder in the text


def test_conversation_text_skips_tool_turns():
    turns = [{"role": "assistant", "content": "Namaste"}, {"role": "user", "content": " leave chahiye "},
             {"role": "tool", "content": None}, {"role": "user", "content": [{"x": 1}]}]
    assert conversation_text(turns) == "BolDoc: Namaste\nCaller: leave chahiye"


def _transport(replies):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        status, content = replies[len(calls) - 1]
        body = {"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 7}}
        return httpx.Response(status, json=body)
    return httpx.MockTransport(handler), calls


def _run(replies, parse=lambda raw: raw["ok"]):
    transport, calls = _transport(replies)

    async def go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await chat_json("k", "sys", "user", parse, client=client)
    return asyncio.run(go()), calls


def test_chat_json_repairs_once_with_the_error_shown():
    (value, stats), calls = _run([(200, "oops not json"), (200, '{"ok": 42}')])
    assert value == 42 and stats["attempts"] == 2
    assert calls[1]["messages"][-1]["role"] == "user" and "invalid" in calls[1]["messages"][-1]["content"]
    assert calls[0]["reasoning_effort"] == "low"


def test_chat_json_gives_up_after_two_invalid_answers_and_maps_http_errors():
    with pytest.raises(ThinkerError) as e:
        _run([(200, "{}"), (200, "{}")])
    assert e.value.code == "invalid_output"
    with pytest.raises(ThinkerError) as e:
        _run([(503, "")])
    assert e.value.code == "provider_unavailable"
    with pytest.raises(ThinkerError) as e:
        _run([(400, "")])
    assert e.value.code == "provider_error"


# ------------------------------------------------------------------------------------------- T1 edits
def test_edit_ops_apply_to_named_blocks_only_and_changes_come_from_ops():
    from apps.thinker.editor import _parse, compact_with_ids
    base = _to_document(RAW, "doc_1")
    body = next(b for b in base.blocks if b.type == "paragraph" and "request" in b.text)
    untouched = [b.model_dump() for b in base.blocks if b.id != body.id]
    raw = {"ops": [{"op": "update_text", "id": body.id, "text": "I request leave from 26 to 27 October 2026."},
                   {"op": "set_meta", "key": "subject", "value": "Leave from 26 to 27 October"},
                   {"op": "insert_block", "after_id": body.id, "block": {"type": "paragraph", "text": "Thank you."}}]}
    doc, changes = _parse(raw, base)
    assert doc.meta["subject"] == "Leave from 26 to 27 October"
    added = [b for b in doc.blocks if b.id not in {x.id for x in base.blocks}]
    assert len(added) == 1 and added[0].text == "Thank you."
    assert [b.model_dump() for b in doc.blocks if b.id != body.id and b not in added] == untouched
    assert len(changes) == 3 and all(isinstance(c, str) for c in changes)
    assert all("id" in b for b in compact_with_ids(base)["blocks"])


def test_edit_parse_rejects_unknown_ids_and_signals_redraft():
    from apps.thinker.editor import _parse
    from docengine.patch import PatchError
    base = _to_document(RAW, "doc_1")
    with pytest.raises(PatchError):
        _parse({"ops": [{"op": "delete_block", "id": "b_nope00"}]}, base)
    with pytest.raises(ValueError):
        _parse({"ops": []}, base)
    assert _parse({"ops": [], "needs_redraft": True}, base) is None


def test_edit_falls_back_to_redraft_when_ops_fail(monkeypatch):
    from apps.thinker import editor
    base = _to_document(RAW, "doc_1")

    async def fake_chat(*a, **k):
        raise ThinkerError("invalid_output", "bad ops twice")

    async def fake_draft(api_key, transcript, intent, doc_id, formats, doc_lang="en-IN", **k):
        return base.model_copy(update={"title": "Redrafted"}), {"attempts": 1}

    monkeypatch.setattr(editor, "chat_json", fake_chat)
    monkeypatch.setattr(editor, "draft", fake_draft)
    doc, changes, stats = asyncio.run(editor.edit("k", base, ["make it a table"], "Caller: ...", "en-IN", ["pdf"]))
    assert doc.title == "Redrafted" and stats["tier"] == "T2" and stats["escalated"] == "invalid_output"
    assert "rewrote the document" in changes[0]

    async def unavailable(*a, **k):
        raise ThinkerError("provider_unavailable", "503")

    monkeypatch.setattr(editor, "chat_json", unavailable)
    with pytest.raises(ThinkerError):  # provider outage is retried by the worker, not papered over by a redraft
        asyncio.run(editor.edit("k", base, ["x"], "", "en-IN", ["pdf"]))


def test_an_edit_that_leaves_the_subject_stale_gets_it_fixed(monkeypatch):
    """Seen on a real call: "26 se 27 kar dijiye" changed the body, but the subject still said 25 to 27."""
    from apps.thinker import editor
    from docengine.model import Document, Paragraph, Run

    base = Document(id="d", title="Leave Application",
                    meta={"subject": "Leave Application for 25 to 27 October 2026", "date": "22 October 2026"},
                    blocks=[Paragraph(id="p1", runs=[Run(text="I request leave from 25 to 27 October 2026.")])])
    calls = []

    async def fake_chat(api_key, system, user, parse, **k):
        calls.append(system)
        if system == editor.EDIT_SYSTEM:  # the model fixes the body only
            return parse({"ops": [{"op": "update_text", "id": "p1",
                                   "text": "I request leave from 26 to 27 October 2026."}]}), {"latency_ms": 900}
        assert '"subject"' in user and '"date"' not in user  # only the stale field is shown
        return parse({"ops": [{"op": "set_meta", "key": "subject",
                               "value": "Leave Application for 26 to 27 October 2026"}]}), {"latency_ms": 400}
    monkeypatch.setattr(editor, "chat_json", fake_chat)
    doc, changes, stats = asyncio.run(editor.edit("k", base, ["26 se 27 kar dijiye"], "...", "en-IN", ["pdf"]))
    assert doc.meta["subject"] == "Leave Application for 26 to 27 October 2026" and len(changes) == 2
    assert doc.meta["date"] == "22 October 2026" and len(calls) == 2
    assert stats["stale_fixed"] == ["subject"] and stats["latency_ms"] == 1300 and stats["tier"] == "T1"
    # nothing stale: one call only
    calls.clear()
    plain = Document(id="d", title="Note", meta={}, blocks=[Paragraph(id="p1", runs=[Run(text="Meet at 5.")])])
    asyncio.run(editor.edit("k", plain, ["make it 6"], "...", "en-IN", ["pdf"]))
    assert len(calls) == 1



def test_a_change_already_in_the_document_is_not_a_reason_to_redraft(monkeypatch):
    """BolDoc's own edit and the caller's answer can carry the same fact: the second one changes nothing."""
    from apps.thinker import editor
    from docengine.model import Document, Paragraph, Run

    base = Document(id="d", title="Leave", meta={},
                    blocks=[Paragraph(id="p1", runs=[Run(text="Employee ID: E-4521.")])])
    drafted = []

    async def no_redraft(*a, **k):
        drafted.append(1)
        return base, {"attempts": 1, "latency_ms": 1}
    monkeypatch.setattr(editor, "draft", no_redraft)
    transport, calls = _transport([(200, '{"ops": [], "already_done": true}')])

    async def go(asks):
        async with httpx.AsyncClient(transport=transport) as client:
            monkeypatch.setattr(editor, "chat_json",
                                lambda *a, **k: chat_json(*a, **{**k, "client": client}))
            return await editor.edit("k", base, asks, "...", "en-IN", ["pdf"])
    doc, changes, stats = asyncio.run(go(["employee ID E-4521 daal dijiye"]))
    assert doc == base and changes == [] and stats["tier"] == "T1" and not drafted
    # "already done" is believed only when the request's numbers are really there
    transport, calls = _transport([(200, '{"ops": [], "already_done": true}')] * 2)
    asyncio.run(go(["employee ID E-7777 kar dijiye"]))
    assert len(calls) == 2 and "7777" in calls[1]["messages"][-1]["content"] and drafted == [1]


def test_the_whole_document_in_another_language_is_rewritten_in_it(monkeypatch):
    """The editor names the language; the rewrite is written in it and reported, so the document stays in it."""
    from apps.thinker import editor
    base = _to_document(RAW, "doc_1")
    seen = {}

    async def fake_chat(key, system, user, parse, **kw):
        return parse({"ops": [], "needs_redraft": True, "language": "en-IN"}), {"latency_ms": 5}

    async def fake_draft(key, transcript, intent, doc_id, formats, doc_lang="en-IN", **kw):
        seen["lang"] = doc_lang
        return base, {"latency_ms": 7}
    monkeypatch.setattr(editor, "chat_json", fake_chat)
    monkeypatch.setattr(editor, "draft", fake_draft)
    _doc_out, _changes, stats = asyncio.run(editor.edit("k", base, ["write it in English"], "", "hi-IN", ["pdf"]))
    assert seen["lang"] == "en-IN" and stats["doc_lang"] == "en-IN" and stats["tier"] == "T2"
