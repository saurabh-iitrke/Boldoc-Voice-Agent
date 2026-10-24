"""What a deck looks like in every format (PowerPoint, and the PDF/HTML of a deck): themes, how much fits on a
slide, and the title slide's subtitle."""
from __future__ import annotations

from .model import Document

SLIDE_BULLETS = 6  # bullets on one slide; the rest continue on the next
SLIDE_ROWS = 10    # table rows on one slide; the total goes on the last
THEMES = {
    "boldoc": {"bg": "FFFFFF", "title": "121212", "text": "121212", "muted": "6B6B66", "accent": "FF4D1A",
               "panel": "F3F3F1", "rule": "DCDCD7", "head_text": "121212"},
    "plain": {"bg": "FFFFFF", "title": "1F3A5F", "text": "1F1F1F", "muted": "5B5B5B", "accent": "1F3A5F",
              "panel": "EEF2F7", "rule": "C9D6EA", "head_text": "1F3A5F"},
    "dark": {"bg": "121212", "title": "FFFFFF", "text": "F3F3F1", "muted": "A3A39C", "accent": "FF4D1A",
             "panel": "262625", "rule": "3A3A38", "head_text": "FFFFFF"},
}


def theme(doc: Document) -> dict:
    return THEMES.get(doc.style.theme, THEMES["boldoc"])


def subtitle(doc: Document) -> str:
    """The title slide's line: a long document's purpose, presenter and date (meta subtitle, author, date), else a
    letter-style document's from / date / subject / to."""
    m = doc.meta
    if m.get("subtitle") or m.get("author"):
        return "   ·   ".join(v for v in (m.get("subtitle"), m.get("author"), m.get("date")) if v)
    return "   ·   ".join(v for k, v in m.items() if k in ("from", "date", "subject", "to") and v)
