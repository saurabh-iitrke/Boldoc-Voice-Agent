"""T1 edits: one fast sarvam-105b call turns the caller's change request into validated DocEngine patch ops.

Untouched blocks can't drift (ops only touch the blocks they name), block IDs stay stable, and "what changed" is
derived from the applied ops, never from the model's own claim. A request that needs restructuring (or ops that
fail validation twice) falls back to a full redraft (T2) from the conversation.
"""
from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from docengine.model import Document
from docengine.patch import apply_patch, parse_ops

from .drafter import LANG_NAMES, _compact_block, draft, localize_document
from .llm import ThinkerError, chat_json

EDIT_SYSTEM = """You edit an existing document exactly as the caller asked on a phone call.
Output ONE JSON object and nothing else: {"ops": [...], "needs_redraft": false}

Ops (use block ids exactly as given; never invent ids):
  {"op": "update_text", "id": "<paragraph/heading id>", "text": "the block's complete new text"}
  {"op": "set_title", "text": "..."}
  {"op": "set_meta", "key": "date" | "to" | "from" | "subject" | "ref", "value": "..." }   (null value deletes)
  {"op": "insert_block", "after_id": "<id, or null>", "at": "start" | "end",
   "block": {"type": "paragraph", "text": "..."}}
  {"op": "delete_block", "id": "..."}
  {"op": "move_block", "id": "...", "after_id": "<id, or null for the start>"}
  {"op": "list_set_item" | "list_insert_item" | "list_delete_item", "id": "...", "index": 0, "text": "..."}
  {"op": "table_set_cell", "id": "...", "row": 0, "col": "<column name>", "value": "..." or 123}
  {"op": "table_add_row", "id": "...", "values": [...]}      {"op": "table_delete_row", "id": "...", "row": 0}
  {"op": "kv_set", "id": "<kv id>", "key": "<the line's key as shown>", "value": "..."}   (a new key adds a line)
  {"op": "kv_delete", "id": "<kv id>", "key": "..."}
  {"op": "replace_block", "id": "...", "block": {...complete block in the same shape as shown...}}

Rules:
- Change only what was asked. Everything else stays exactly as it is.
- A fact that appears in several places (e.g. a date in the subject and in the body) is changed everywhere.
- New text is in the DOCUMENT LANGUAGE, matches the document's tone, and is grammatical. In Hindi keep the
  first-person gender the document already uses.
- Use only facts from the conversation; never invent names, dates or amounts.
- If the request needs the whole document rewritten or restructured (another language, a different kind of
  document, "make it a table"), return {"ops": [], "needs_redraft": true}. If it asks for the document in another
  language, also give that language: {"ops": [], "needs_redraft": true, "language": "en-IN"} (en-IN, hi-IN, mr-IN,
  bn-IN, gu-IN, pa-IN, ta-IN, te-IN, kn-IN, ml-IN or od-IN).
- If the document already says exactly what was asked, return {"ops": [], "already_done": true}.
"""


def compact_with_ids(doc: Document) -> dict:
    return {"title": doc.title, "meta": doc.meta,
            "blocks": [{"id": b.id, **_compact_block(b.model_dump())} for b in doc.blocks]}


def _normalize_block(block: Any) -> Any:
    """Models write paragraphs as {"type": "paragraph", "text": ...}; DocEngine stores runs."""
    if isinstance(block, dict) and block.get("type") == "paragraph" and "runs" not in block:
        block = {k: v for k, v in block.items() if k != "text"} | {"runs": [{"text": str(block.get("text", ""))}]}
    if isinstance(block, dict):
        block = {k: v for k, v in block.items() if k != "id"}  # ids are ours
    return block


def _parse(raw: Any, base: Document, asks: str = "", redraft: Optional[dict] = None) -> Optional[tuple]:
    if not isinstance(raw, dict):
        raise ValueError("expected a JSON object")
    if raw.get("needs_redraft"):
        if redraft is not None and raw.get("language") in LANG_NAMES:
            redraft["language"] = raw["language"]  # the whole document, wanted in another language
        return None
    if raw.get("already_done") and not raw.get("ops"):
        # a change asked twice (once in the caller's own words, once by BolDoc) is not a reason to redraft; but a
        # number the request names must really be in the document already
        missing = set(_NUM.findall(asks)) - set(_NUM.findall(json.dumps(base.model_dump(), ensure_ascii=False)))
        if missing:
            raise ValueError(f"the document doesn't have {', '.join(sorted(missing))} yet: return the ops")
        return base, []
    ops_raw = []
    for op in raw.get("ops") or []:
        if isinstance(op, dict) and "block" in op:
            op = dict(op, block=_normalize_block(op["block"]))
        ops_raw.append(op)
    if not ops_raw:
        raise ValueError("no ops: return the ops that make the change, or needs_redraft true")
    result = apply_patch(base, parse_ops(ops_raw))  # unknown ids / bad shapes raise -> one repair retry
    return localize_document(result.document), result.changes


_NUM = re.compile(r"\d+")
STALE_SYSTEM = """A document was just edited on a phone call. Its body now uses new values, but some header fields
still show the old ones. Output ONE JSON object and nothing else: {"ops": [...]} using only
  {"op": "set_meta", "key": "<one of the fields shown>", "value": "the corrected value"} and
  {"op": "set_title", "text": "..."}
to make those fields agree with the body. Change nothing else; keep each field's wording and language."""


def _body_numbers(doc: Document) -> set:
    return set(_NUM.findall(json.dumps([b.model_dump() for b in doc.blocks], ensure_ascii=False)))


def stale_fields(base: Document, new: Document) -> dict:
    """Header fields (subject, date, title...) still holding a number this edit removed from the body: the
    model changed "25 to 27" in the text but left the subject saying 25. Only when the body also gained a number
    (a replacement, not a deletion)."""
    removed, added = _body_numbers(base) - _body_numbers(new), _body_numbers(new) - _body_numbers(base)
    if not removed or not added:
        return {}
    fields = {k: v for k, v in (new.meta or {}).items() if isinstance(v, str)}
    fields["title"] = new.title or ""
    return {k: v for k, v in fields.items() if set(_NUM.findall(v)) & removed}


async def _fix_stale(api_key, doc: Document, stale: dict, asks: str) -> Optional[tuple]:
    user = (f"Change requested on the call:\n{asks}\n\nThe body now reads:\n"
            f"{json.dumps([_compact_block(b.model_dump()) for b in doc.blocks], ensure_ascii=False)[:4000]}\n\n"
            f"Fields still showing old values:\n{json.dumps(stale, ensure_ascii=False)}")

    def parse(raw: Any) -> tuple:
        ops = [o for o in (raw.get("ops") or []) if isinstance(o, dict) and o.get("op") in ("set_meta", "set_title")
               and (o.get("op") == "set_title" or o.get("key") in stale)]
        if not ops:
            raise ValueError("no set_meta/set_title ops for the fields shown")
        result = apply_patch(doc, parse_ops(ops))
        return localize_document(result.document), result.changes
    try:
        return await chat_json(api_key, STALE_SYSTEM, user, parse, reasoning=None, max_tokens=600)
    except ThinkerError:
        return None  # the body edit stands; a stale header is better than a failed edit


async def edit(api_key: str, base: Document, instructions: List[str], transcript: str, doc_lang: str,
               formats: List[str], intent: str = "", allow_redraft: bool = True) -> tuple[Document, List[str], dict]:
    """Returns (new document, human-readable changes, stats). Stats say which tier actually ran. A long document
    (allow_redraft=False) is never rewritten whole for a change: one draft call can't hold it
    (docs/design/hermes.md)."""
    asks = "\n".join(f"- {i}" for i in instructions)
    user = (f"DOCUMENT LANGUAGE: {LANG_NAMES.get(doc_lang, doc_lang)} ({doc_lang}).\n\n"
            f"Current document (block ids are stable):\n{json.dumps(compact_with_ids(base), ensure_ascii=False)}\n\n"
            f"Conversation so far (latest at the end):\n{transcript[-6000:]}\n\nChange requested now:\n{asks}")
    redraft: dict = {}
    try:
        parsed, stats = await chat_json(api_key, EDIT_SYSTEM, user, lambda raw: _parse(raw, base, asks, redraft),
                                        reasoning=None, max_tokens=2500)
        if parsed is not None:
            doc, changes = parsed
            stale = stale_fields(base, doc)
            if stale:
                fixed = await _fix_stale(api_key, doc, stale, asks)
                if fixed is not None:
                    (doc, more), fstats = fixed
                    changes = changes + more
                    stats = {**stats, "latency_ms": stats["latency_ms"] + fstats["latency_ms"],
                             "stale_fixed": sorted(stale)}
            return doc, changes, {**stats, "tier": "T1"}
        reason = "needs_redraft"
    except ThinkerError as e:
        if e.code == "provider_unavailable":
            raise
        reason = e.code
    if not allow_redraft:
        raise ThinkerError("needs_rewrite", f"the change needs more than an edit ({reason})")
    # Another language: the rewrite is written in it, and the document stays in it.
    lang = redraft.get("language") or doc_lang
    doc, stats = await draft(api_key, transcript, f"{intent}. Then the caller asked: {'; '.join(instructions)}",
                             base.id, formats, doc_lang=lang)
    return doc, [f"rewrote the document ({'; '.join(instructions)[:120]})"], {**stats, "tier": "T2",
                                                                            "escalated": reason, "doc_lang": lang}
