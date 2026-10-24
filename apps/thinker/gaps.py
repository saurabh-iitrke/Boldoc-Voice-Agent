"""Facts a draft still lacks: asked on the call, never assumed.

The drafter leaves a placeholder ("[Your employee ID]") for a needed fact the conversation didn't give. Each one becomes
a question the talker asks. The caller's reply comes back as they said it, and one short model call decides what it
means: the fact (put in place of the placeholder by code, in the document's own script and formats), a "no" (the field
is taken out by the editor), or something else (the question may be asked once more), plus any other change they asked
for in the same breath.

Why the reply is passed on by code and judged here: given a [STATE] question, both talker models acknowledged the answer
without any tool call in most benchmark runs, never called a tool for "I don't remember, leave it", and answered "first
make it 26 to 27" with "done" and no tool call.
"""
from __future__ import annotations

import json
import re
from typing import Any, List, NamedTuple, Optional, Tuple

import httpx

from docengine.i18n import localize_dates
from docengine.model import Document

from .drafter import _PLACEHOLDER, LANG_NAMES
from .llm import chat_json

_ON_PAPER = re.compile(r"sign|stamp|seal|photo|logo|attach", re.I)   # done on paper, not something to say
# Placeholders joined by commas ("[Your Address], [City], [PIN Code]") are one fact: asked once, filled once.
_ONE = r"\[(?![\d\s,;]+\])[^\[\]\n]{2,60}\]"  # a placeholder, never a citation ("[12]")
_RUN = re.compile(_ONE + r"(?:[ \t]*,[ \t]*" + _ONE + r")*")
_SEP = r"[ \t]*[,;/|–-][ \t]*"


def members(gap: str) -> List[str]:
    return _PLACEHOLDER.findall(gap) or [gap]


def open_gaps(doc: Document) -> List[str]:
    """Placeholders still in the document (title, meta, body), in reading order: facts the caller hasn't given."""
    text = doc.model_copy(update={"assumptions": []}).all_text()
    runs = dict.fromkeys(m.group(0) for m in _RUN.finditer(text))
    return [r for r in runs if not all(_ON_PAPER.search(p) for p in members(r))]


def _label(placeholder: str) -> str:
    label = placeholder.strip("[] ")
    if not re.search(r"[A-Za-z]", label):
        return label
    words = [w if w.isupper() or not w[:1].isupper() else w.lower() for w in label.split()]
    return " ".join(words if words[0] == "your" else ["the", *words])


def label_of(gap: str) -> str:
    """'[Your Employee ID]' -> 'your employee ID'; '[Your Address], [City], [PIN Code]' -> 'your address, city and
    PIN code'; 'the HR manager's name' for others."""
    labels = [_label(p) for p in members(gap)]
    if len(labels) == 1:
        return labels[0]
    rest = [re.sub(r"^(?:the|your) ", "", lb) for lb in labels[1:]]
    return ", ".join([labels[0], *rest[:-1]]) + f" and {rest[-1]}"


def question_for(gap: str) -> str:
    """'[Your employee ID]' -> 'What is your employee ID?' (the talker asks it in the caller's language)."""
    label = label_of(gap)
    if not re.search(r"[A-Za-z]", label):
        return f"What should the document say for {label}?"
    return f"What is {label}?"


INTERPRET = """On a phone call, BolDoc asked the caller for one fact a document needs. Decide what the caller's reply
means. Output ONE JSON object and nothing else: {"kind": "answer" | "declined" | "other", "value": "...",
"change": "..."}

- "answer": the reply gives the fact. "value" is the fact exactly as the document should show it, in the DOCUMENT
  LANGUAGE's script (names as spoken; Hindi documents write them in Devanagari), dates like "25 October 2026"
  (Hindi: "25 अक्टूबर 2026"), money like "₹12,000". Only what the caller said: never add to it or guess.
- "declined": they don't know it, don't have it, or don't want it in the document. "value" is "".
- "other": the reply doesn't answer the question (another request, a question, "one minute"). "value" is "".
- "change": any OTHER change to the document the caller asked for in this reply ("make the dates 26 to 27"),
  in their words; "" if none. The answer itself is never a change."""


class Reading(NamedTuple):
    kind: str        # answer | declined | other
    value: str       # the fact as the document should show it (answers only)
    change: str      # another change asked for in the same reply, in the caller's words


async def interpret(api_key: Any, placeholder: str, question: str, reply: str, doc_lang: str,
                    client: Optional[httpx.AsyncClient] = None) -> Tuple[Reading, dict]:
    """What the caller's reply to a gap's question means, and the call's stats."""
    user = (f"DOCUMENT LANGUAGE: {LANG_NAMES.get(doc_lang, doc_lang)} ({doc_lang}).\n"
            f"The document has the placeholder {placeholder}.\nBolDoc asked: {question}\n"
            f"The caller replied: {reply}")

    def parse(raw: Any) -> Reading:
        kind = str(raw.get("kind") or "").strip().lower()
        if kind not in ("answer", "declined", "other"):
            raise ValueError('"kind" must be "answer", "declined" or "other"')
        value = " ".join(str(raw.get("value") or "").split())
        if kind == "answer" and (not value or len(value) > 160 or "[" in value or "]" in value):
            raise ValueError('an "answer" needs "value": the fact as the document should show it')
        change = " ".join(str(raw.get("change") or "").split())[:300]
        return Reading(kind, value if kind == "answer" else "", change)
    return await chat_json(api_key, INTERPRET, user, parse, reasoning=None, max_tokens=300, client=client)


def _without_notes(data: dict, gap: str) -> None:
    data["assumptions"] = [a for a in data["assumptions"] if not any(p in a for p in members(gap))]


def fill(doc: Document, gap: str, value: str) -> Document:
    """The caller's answer in place of the gap, wherever it appears; its "fill in" note goes."""
    data = doc.model_dump()
    _without_notes(data, gap)
    esc = lambda s: json.dumps(s, ensure_ascii=False)[1:-1]  # noqa: E731
    text = json.dumps(data, ensure_ascii=False).replace(esc(gap), esc(localize_dates(value, doc.lang)))
    return Document.model_validate_json(text)


def _without(text: str, gap: str) -> str:
    """'Saurabh Kumar, [Your Address], [City]' without the gap and the separator it leaves behind."""
    g = re.escape(gap)
    for pat in (_SEP + g, g + _SEP, g):
        out, n = re.subn(pat, "", text)
        if n:
            return out.strip()
    return text


def remove_gap(doc: Document, gap: str) -> Tuple[Document, bool]:
    """A fact the caller doesn't have or doesn't want, taken out by code wherever it sits in a structured place
    (header lines, details lines, list items, the signature, table cells, slide bullets). True when it is also in
    running text, which only the editor can rewrite well."""
    data = doc.model_dump()
    _without_notes(data, gap)
    data["title"] = _without(data["title"], gap)
    data["meta"] = {k: _without(v, gap) for k, v in data["meta"].items() if _without(v, gap)}
    in_text = False
    for b in data["blocks"]:
        kind = b["type"]
        if kind == "kv":
            b["pairs"] = [[k, _without(v, gap)] for k, v in b["pairs"] if _without(v, gap)]
        elif kind == "list":
            b["items"] = [x for x in (_without(i, gap) for i in b["items"]) if x]
        elif kind == "signature":
            b["closing"], b["name"] = _without(b["closing"], gap), _without(b["name"], gap)
            b["designation"] = _without(b["designation"], gap) or None if b.get("designation") else None
        elif kind == "table":
            b["rows"] = [[_without(c, gap) if isinstance(c, str) else c for c in row] for row in b["rows"]]
            b["caption"] = _without(b["caption"], gap) or None if b.get("caption") else None
        elif kind == "slide":
            b["bullets"] = [x for x in (_without(i, gap) for i in b["bullets"]) if x]
            b["notes"] = _without(b["notes"], gap) or None if b.get("notes") else None
            in_text = in_text or gap in b["title"]
        elif kind == "paragraph":
            in_text = in_text or gap in "".join(r.get("text", "") for r in b["runs"])
        elif kind == "heading":
            in_text = in_text or gap in b["text"]
    data["blocks"] = [b for b in data["blocks"] if not (b["type"] == "kv" and not b["pairs"])]
    return Document.model_validate(data), in_text


def removal(gap: str) -> str:
    """The editor's instruction for a fact the caller doesn't have, left in running text."""
    return (f"The caller doesn't have {label_of(gap)} or doesn't want it in the document: remove {gap} and any "
            "words that only introduce it.")
