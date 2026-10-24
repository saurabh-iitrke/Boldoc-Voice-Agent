"""A long document's numbers, checked before it goes out ("numbers that add up").

Hermes writes a draft section by section, and a slip it can't see from inside one section reaches the bank: on the box a
profit table said ₹8,13,600 where its own text said ₹81,360, and the cost of the project (₹4,90,000) wasn't what its
means of finance covered (₹4,00,000). The writer's check compared stated totals with table totals only. One model call
now reads the draft's tables, charts and every sentence that states an amount, and lists what doesn't agree;
apps/control/bigdoc.py hands the list to the writer at check and finish.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from .llm import ThinkerError, chat_json

NUMBERS = """You check the numbers in the draft of a long document (a project report, a business plan, a proposal)
before it goes to a bank or a client. You get its sections: the sentences that state amounts or percentages, its
tables (each row as cells, with the total the document will print) and its charts.

Report only real inconsistencies, each in one short line that says where and what, with both values:
- a number in the text that contradicts a table or a chart (the same item, a different value);
- a value off by a factor of 10 or 100 from its own text or from the values around it;
- totals that must agree but don't: the cost of the project and its means of finance; a loan that isn't the cost
  less the owner's contribution; the same total given differently in two places;
- a derived value that doesn't follow from the others: gross profit = sales - cost of sales; net = gross - expenses;
  a closing balance = opening - principal repaid; a growth rate the figures don't show.
Ignore differences under 1% (rounding), words like "about", and placeholders like [Your bank name]. Never report a
number as wrong because you would have assumed something else. If everything agrees, return no issues.

Reply with JSON only: {"issues": [{"section": "<section id>", "problem": "<one line>"}]}"""

MAX_INPUT = 24000  # characters of the draft read: a 15-page report's numbers fit


def _parse(raw: Any) -> List[Dict[str, str]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("issues"), list):
        raise ValueError('need {"issues": [{"section": "...", "problem": "..."}]}')
    out = []
    for x in raw["issues"][:12]:
        if isinstance(x, dict) and str(x.get("problem") or "").strip():
            out.append({"section": " ".join(str(x.get("section") or "").split())[:20],
                        "problem": " ".join(str(x["problem"]).split())[:300]})
    return out


_SENTENCE = re.compile(r"(?<=[.!?।])\s+")
_DIGIT = re.compile(r"\d")


def with_numbers(text: str) -> List[str]:
    """The sentences of a text that state a number."""
    return [s.strip() for s in _SENTENCE.split(text or "") if _DIGIT.search(s)]


async def review_numbers(api_key, draft: str, client=None) -> Tuple[List[Dict[str, str]], dict]:
    """(issues, stats). A review that fails finds nothing: the document is never held up by its checker."""
    if not _DIGIT.search(draft or ""):
        return [], {}
    try:
        return await chat_json(api_key, NUMBERS, draft[:MAX_INPUT], _parse, reasoning=None, max_tokens=900,
                               temperature=0.0, timeout_s=60.0, client=client)
    except ThinkerError as e:
        return [], {"error": e.code}
