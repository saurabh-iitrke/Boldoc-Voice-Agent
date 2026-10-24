"""Is what the caller wants a long document (a report, plan, proposal, deck or workbook Hermes writes section by
section) or a short one (today's writer)? One small call next to the intake and the sense check, until the first
document exists. Conservative: anything unclear is short, so a letter never waits for the long-document path.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from .catalogue import KINDS
from .llm import ThinkerError, chat_json

SCOPE = """You check one thing for BolDoc, an assistant that writes documents for people over a phone call: is the
document the caller wants now a LONG document or a SHORT one?

LONG: a document of several sections and pages that takes real work to write: a project report or DPR for a loan, a
business plan, a pitch deck or presentation of more than 5 slides, an internship report, a project or research
report on a topic (for school, college or office), a policy,
SOP or handbook, a grant or CSR proposal, a tender response, a sales or MIS report, minutes of a long meeting, a
lesson plan or course material, a question paper, a planner or tracker workbook with several sheets; or anything the
caller says should be 4 or more pages, or 6 or more slides.
SHORT: letters, applications, complaints, notices, invoices, receipts, certificates, a resume, a quotation for a few
items, a simple table or list, a one-page note. When the caller hasn't said what they want yet, or it isn't clear,
answer short.

The types of long document, each with its usual sections:
{kinds}
Pick the type by what the document is for, not by a word it shares with a type's name: a "project" can be a project
report for a bank or scheme loan (only when the caller speaks of a loan, a bank or a scheme), an internship report, or
a project or research report on a topic such as AI or climate change. Never assume a loan.

Reply with JSON only:
{{"size": "long" | "short",
  "kind": one of the type ids above, or "other" for a long document of another type, or "" when short,
  "title": a short title for it, in the caller's language ("" when short),
  "length": the number of pages or slides the caller asked for, or null if they didn't say,
  "sections": [] when the caller said nothing about the sections; if they named the sections they want, or asked
    to add, drop, rename or reorder any, the FULL list after their changes, starting from the type's usual sections}}"""


def _kinds_line() -> str:
    return "\n".join(f"- {k.id} ({k.name}): " + " | ".join(k.outline) for k in KINDS.values())


def _parse(raw: Any) -> dict:
    if not isinstance(raw, dict) or str(raw.get("size", "")).strip().lower() not in ("long", "short"):
        raise ValueError('need {"size": "long" or "short", "kind", "title", "length", "sections": [...]}')
    size = str(raw["size"]).strip().lower()
    kind = str(raw.get("kind") or "").strip()
    kind = kind if kind in KINDS or kind == "other" else ("other" if size == "long" else "")
    length = raw.get("length")
    try:
        length = int(length) if length not in (None, "", "null") else None
    except (TypeError, ValueError):
        length = None
    if length is not None and not 1 <= length <= 200:
        length = None
    sections = raw.get("sections") if isinstance(raw.get("sections"), list) else []
    return {"size": size, "kind": kind if size == "long" else "",
            "title": " ".join(str(raw.get("title") or "").split())[:120] if size == "long" else "",
            "length": length if size == "long" else None,
            "sections": [" ".join(str(x).split())[:80] for x in sections if str(x).strip()][:30] if size == "long"
            else []}


async def scope_check(api_key: Any, transcript: str, client: Optional[httpx.AsyncClient] = None) -> tuple:
    """({size, kind, title, length, sections}, stats). A failed check is "short": today's path, never blocked."""
    try:
        return await chat_json(api_key, SCOPE.format(kinds=_kinds_line()), f"Conversation:\n{transcript[-4000:]}",
                               _parse, reasoning=None, max_tokens=300, timeout_s=6.0, client=client)
    except ThinkerError:
        return {"size": "short", "kind": "", "title": "", "length": None, "sections": []}, {}
