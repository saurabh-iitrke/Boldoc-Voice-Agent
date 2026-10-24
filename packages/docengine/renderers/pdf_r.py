"""PDF renderer: HTML -> WeasyPrint (Pango/HarfBuzz shaping; fonts embedded & subset automatically).

Server needs: `apt install weasyprint fonts-noto-core fonts-noto-extra` (or pip weasyprint + Pango libs).
"""
from __future__ import annotations

from ..model import Document
from . import RendererUnavailable
from .html_r import render_html


def render(doc: Document) -> bytes:
    try:
        from weasyprint import HTML  # heavy import; keep lazy
    except Exception as e:  # ImportError or missing Pango shared libs (OSError)
        raise RendererUnavailable(f"PDF rendering needs WeasyPrint + Pango: {e}") from e
    return HTML(string=render_html(doc)).write_pdf()
