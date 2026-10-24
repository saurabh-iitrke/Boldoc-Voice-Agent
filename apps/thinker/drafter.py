"""T2 drafting: conversation -> canonical Document (DocEngine), using sarvam-105b.

The model writes a compact JSON shape (easy to produce reliably); we convert it into the DocEngine model, which
assigns stable block IDs and enforces invariants. Facts come only from the conversation: what the document needs is
asked on the call before it is written (the intake), and a fact the caller chose to leave out is a placeholder,
recorded in `assumptions` and printed as a blank to fill in by hand, never an invented or generic value.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from docengine.i18n import localize_dates
from docengine.model import Document, detect_script

from .llm import chat_json

IST = timezone(timedelta(hours=5, minutes=30))

SYSTEM = """You are BolDoc's document writer. From a phone conversation, write the document the caller asked for,
ready to send. Output ONE JSON object and nothing else.

JSON shape:
{
  "title": "short title, in the DOCUMENT LANGUAGE (Hindi example: \"अवकाश हेतु आवेदन\")",
  "lang": "en-IN" | "hi-IN" | "ta-IN" | ... (language of the document text),
  "doc_type": one of "letter", "application", "report", "notes", "invoice", "resume", "minutes", "table",
              "slides", "other",
  "meta": {"date": "...", "to": "...", "from": "...", "subject": "..."}
          (only keys that apply; letters and applications use them),
  "blocks": [
    {"type": "paragraph", "text": "..."},
    {"type": "heading", "level": 1, "text": "..."},
    {"type": "list", "ordered": false, "items": ["...", "..."]},
    {"type": "table", "caption": "...", "columns": [{"name": "...", "type": "text|number|currency|date|percent"}],
     "rows": [["...", 123]], "total_row": true},
    {"type": "kv", "pairs": [["Key", "Value"]]},
    {"type": "signature", "closing": "Yours sincerely,", "name": "...", "designation": "..."},
    {"type": "slide", "title": "...", "bullets": ["..."], "notes": "..."}
  ],
  "assumptions": ["each [placeholder] left in the text"]
}

Rules:
- Use only facts the caller gave. Never invent names, dates, amounts, addresses or reasons, and never put a
  generic phrase in place of a specific fact they didn't give. The caller gets a complete document: BolDoc asked for
  what it needs on the call. If a fact the document needs is still missing (the caller chose to leave it out), write
  a placeholder like [Your employee ID] where it belongs AND add it to "assumptions": it is printed as a blank to
  fill in by hand.
- Never make commitments or statements on the caller's behalf that they didn't make: no "I will hand over my
  work", "I have ensured my work will be managed", "I will be available on phone", "I will rejoin on …" unless they
  said so. Polite standard openings and closings ("I request you to kindly approve…", "Thanking you") are fine.
- Polish the language: correct grammar, a tone that fits the document (formal for applications and letters),
  well-structured paragraphs. Keep it concise; one page for letters and applications.
- Write the whole document in the DOCUMENT LANGUAGE given in the request, whatever language the call was in.
  English documents are fully in English (translate Hindi facts); Hindi documents use Devanagari throughout.
- Placeholders and "assumptions" must match: every [placeholder] in the text appears in "assumptions", and nothing
  else is listed there (no notes about what you chose or left general: they are never shown to anyone).
- Get tense right for future plans ("I will hand over my work to … by 24 October").
- Keep the addressee exactly as complete as the caller gave it (name, designation, organisation, city).
- Hindi documents, first-person verbs: use the gender the caller used about themselves in the conversation
  ("main available rahunga" -> masculine forms; "rahungi" -> feminine). If they never did, use gender-neutral
  constructions ("अवकाश प्रदान करने की कृपा करें", "निवेदन है कि …"). Never infer gender from a name.
- Do not repeat date/to/from/subject as body paragraphs; they belong only in "meta".
- Indian conventions: dates like "25 October 2026"; money in rupees with Indian grouping ("₹2,50,000"); in tables put
  numbers as numbers (not strings) and use type "currency" for rupee columns.
- Letters and applications: put date/to/subject in "meta", start with a salutation paragraph, end with a
  "signature" block using the caller's name if given (else "[Your name]").
- Tables (expenses, lists of items): use a "table" block with typed columns and total_row true when totals matter.
- Slides: one "slide" block per slide, 3-5 short bullets each.
"""


def _to_document(raw: Any, doc_id: str, doc_lang: Optional[str] = None) -> Document:
    """Model JSON -> validated Document. `doc_lang` (what was requested) wins over the model's own label."""
    if not isinstance(raw, dict):
        raise ValueError("expected a JSON object")
    blocks: List[dict] = []
    for b in raw.get("blocks") or []:
        if not isinstance(b, dict) or "type" not in b:
            continue
        b = dict(b)
        b.pop("id", None)  # IDs are ours, never the model's
        if b["type"] == "paragraph":
            text = str(b.get("text") or "").strip()
            if not text:
                continue
            b = {"type": "paragraph", "runs": [{"text": text}]}
        blocks.append(b)
    blocks = _drop_meta_echo(blocks, raw.get("meta") or {})
    title_key = re.sub(r"\W+", "", str(raw.get("title") or "")).lower()
    blocks = [b for b in blocks if not (b.get("type") == "heading" and title_key
                                        and re.sub(r"\W+", "", str(b.get("text", ""))).lower() == title_key)]
    lang = doc_lang or str(raw.get("lang") or "en-IN")
    raw = dict(raw, title=localize_dates(str(raw.get("title") or ""), lang),
               meta={k: localize_dates(str(v), lang) for k, v in (raw.get("meta") or {}).items()})
    blocks = [_localize_block(b, lang) for b in blocks]
    if not blocks:
        raise ValueError("document has no content blocks")
    text_for_script = " ".join([str(raw.get("title", ""))] + [str(x) for x in (raw.get("meta") or {}).values()])
    doc = Document.model_validate({
        "id": doc_id,
        "title": str(raw.get("title") or "Document").strip()[:120],
        "lang": lang,
        "script": detect_script(text_for_script + " " + str(blocks)[:2000]),
        "doc_type": raw.get("doc_type") if raw.get("doc_type") in Document.model_fields["doc_type"].annotation.__args__
        else "other",
        "meta": {k: str(v) for k, v in (raw.get("meta") or {}).items()
                 if v and k in ("date", "to", "from", "subject", "ref")},
        "blocks": blocks,
        "assumptions": [],
    })
    doc.assumptions = _real_assumptions([str(a) for a in (raw.get("assumptions") or []) if a], doc)
    return doc


def _localize_block(b: Any, lang: str) -> Any:
    """Apply date localisation to every text field of a block, but never to table data (it stays machine-readable)."""
    if isinstance(b, str):
        return localize_dates(b, lang)
    if isinstance(b, list):
        return [_localize_block(x, lang) for x in b]
    if isinstance(b, dict):
        return {k: (v if k in ("rows", "columns", "type", "id") else _localize_block(v, lang)) for k, v in b.items()}
    return b


def localize_document(doc: Document) -> Document:
    """Deterministic date localisation for the document's language (title, meta, text; never table data)."""
    data = doc.model_dump()
    data["title"] = localize_dates(data["title"], doc.lang)
    data["meta"] = {k: localize_dates(v, doc.lang) for k, v in data["meta"].items()}
    data["blocks"] = [_localize_block(b, doc.lang) for b in data["blocks"]]
    return Document.model_validate(data)


# "[Your X]"; never a citation ("[12]", "[2, 5]": a researched page, apps/control/bigdoc.py)
_PLACEHOLDER = re.compile(r"\[(?![\d\s,;]+\])[^\[\]]{2,60}\]")
def _real_assumptions(items: List[str], doc: Document) -> List[str]:
    """What is blank in the document: the placeholders that really are in its text (printed as blanks to fill in). Notes
    about what the writer chose or kept general are dropped: the document says only what the caller gave, and what it
    needs is asked on the call."""
    text = doc.all_text()
    keep: List[str] = []
    for item in items:
        ph = _PLACEHOLDER.findall(item)
        if ph and any(p in text for p in ph) and item not in keep:
            keep.append(item)
    # Any placeholder in the text that the model forgot to list is added, so nothing slips through unflagged.
    for p in _PLACEHOLDER.findall(text):
        if not any(p in k for k in keep):
            keep.append(f"Fill in {p}")
    return keep[:10]


_META_PREFIX = re.compile(r"^\s*(सेवा में|विषय|दिनांक|प्रेषक|to|subject|date|from|ref)\s*[:,]?\s*", re.I)


def _drop_meta_echo(blocks: List[dict], meta: dict) -> List[dict]:
    """Remove body paragraphs that only repeat a meta line (renderers already print meta)."""
    values = {re.sub(r"\W+", "", str(v)).lower() for v in meta.values() if v}
    out = []
    for b in blocks:
        if b.get("type") == "paragraph":
            text = "".join(r.get("text", "") for r in b.get("runs", []))
            core = re.sub(r"\W+", "", _META_PREFIX.sub("", text)).lower()
            if not core or core in values or (_META_PREFIX.match(text) and len(text) < 160 and
                                              any(core and (core in v or v in core) for v in values)):
                continue
        out.append(b)
    return out


def conversation_text(turns: List[dict]) -> str:
    """[{"role": "user"|"assistant", "content": ...}] -> a plain transcript the drafter reads."""
    lines = []
    for t in turns:
        who = "Caller" if t.get("role") == "user" else "BolDoc"
        content = t.get("content")
        if isinstance(content, str) and content.strip():
            lines.append(f"{who}: {content.strip()}")
    return "\n".join(lines)


LANG_NAMES = {"en-IN": "English", "hi-IN": "Hindi (Devanagari)", "ta-IN": "Tamil", "te-IN": "Telugu",
              "kn-IN": "Kannada", "ml-IN": "Malayalam", "mr-IN": "Marathi", "bn-IN": "Bengali", "gu-IN": "Gujarati",
              "pa-IN": "Punjabi", "od-IN": "Odia"}


REVIEW = """You are the reviewer for a document drafted from a phone call. Fix the draft so it passes EVERY check,
then output the full corrected document as ONE JSON object in exactly the same shape as the draft. No prose.

Checks:
1. Facts: every fact the caller gave (names, designation, organisation, dates, places, amounts) is used where it
   belongs. Never leave a placeholder for a fact that was given. Never add facts that were not given, and remove
   any promise the caller didn't make (handover, availability, return date) unless the conversation has it.
2. Language: the whole document, including title and every meta value (subject, date), is in the DOCUMENT LANGUAGE.
   Proper names stay as spoken. Hindi dates look like "25 अक्टूबर 2026".
3. Hindi only, first-person verbs: match the gender the caller used about themselves in the conversation
   ("rahunga" = masculine, "rahungi" = feminine), consistently everywhere. If the caller never used a gendered
   form, rephrase neutrally ("आपकी अति कृपा होगी", "कृपा करें"). Never infer gender from a name.
   No "/" forms like पाऊँगा/पाऊँगी.
4. Placeholders like [Your employee ID] only for facts that are genuinely missing and needed, never a generic
   phrase in place of one. "assumptions" lists exactly those placeholders, nothing else.
5. Tone and tense fit the document; grammar is correct; nothing is repeated.
6. Date, to, from and subject appear only in "meta", never repeated as body paragraphs."""


def add_usage(a: Optional[dict], b: Optional[dict]) -> dict:
    """Sum two OpenAI-style usage objects (prompt/completion/total tokens)."""
    out = dict(a or {})
    for k, v in (b or {}).items():
        if isinstance(v, (int, float)) and isinstance(out.get(k, 0), (int, float)):
            out[k] = out.get(k, 0) + v
    return out


async def review(api_key: str, transcript: str, doc: Document, doc_lang: str) -> tuple[Document, dict]:
    """Second fast pass: a focused checklist catches what a single fast draft misses (~2 s, no reasoning)."""
    compact = {"title": doc.title, "lang": doc.lang, "doc_type": doc.doc_type, "meta": doc.meta,
               "blocks": [_compact_block(b.model_dump()) for b in doc.blocks], "assumptions": doc.assumptions}
    user = (f"DOCUMENT LANGUAGE: {LANG_NAMES.get(doc_lang, doc_lang)} ({doc_lang}).\n\n"
            f"Verbatim conversation:\n{transcript}\n\nDraft JSON:\n{json.dumps(compact, ensure_ascii=False)}")
    return await chat_json(api_key, REVIEW, user, lambda raw: _to_document(raw, doc.id, doc_lang),
                           reasoning=None)


def _compact_block(b: dict) -> dict:
    b = {k: v for k, v in b.items() if k != "id" and v not in (None, [], "")}
    if b.get("type") == "paragraph":
        b = {"type": "paragraph", "text": "".join(r.get("text", "") for r in b.pop("runs", []))}
    return b


def facts_block(known_facts: Optional[dict]) -> str:
    """The caller's saved "My details" (from their account): facts they never have to say again on a call."""
    labels = {"name": "Name", "designation": "Designation", "organisation": "Organisation", "address": "Address"}
    lines = [f"- {labels[k]}: {v}" for k, v in (known_facts or {}).items() if k in labels and v]
    if not lines:
        return ""
    return ("The caller's own saved details (true; use them where the document needs them, e.g. the sender block "
            "or signature, unless the conversation says otherwise):\n" + "\n".join(lines) + "\n\n")


async def draft(api_key: str, transcript: str, intent: str, doc_id: str, formats: List[str], doc_lang: str = "en-IN",
                today: Optional[datetime] = None, reasoning: Optional[str] = None,
                known_facts: Optional[dict] = None) -> tuple[Document, dict]:
    now = localize_dates((today or datetime.now(IST)).strftime("%d %B %Y").lstrip("0"), doc_lang)
    user = (f"DOCUMENT LANGUAGE: {LANG_NAMES.get(doc_lang, doc_lang)} ({doc_lang}).\n"
            f"Today's date: {now}.\nRequested formats: {', '.join(formats) or 'pdf'}.\n"
            f"What the caller wants (agent's summary, may be imprecise): {intent}\n\n"
            + facts_block(known_facts)
            + f"Verbatim conversation (source of truth for facts):\n{transcript}")
    doc, stats = await chat_json(api_key, SYSTEM, user, lambda raw: _to_document(raw, doc_id, doc_lang),
                                 reasoning=reasoning)
    try:
        reviewed, rstats = await review(api_key, transcript, doc, doc_lang)
        stats = {"attempts": stats["attempts"] + rstats["attempts"], "reviewed": True, "model": stats.get("model"),
                 "latency_ms": stats["latency_ms"] + rstats["latency_ms"],
                 "usage": add_usage(stats.get("usage"), rstats.get("usage"))}  # the review call costs too
        return reviewed, stats
    except Exception as e:
        stats["review_error"] = str(e)[:200]
        return doc, stats
