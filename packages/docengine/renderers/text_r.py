"""Plain formats: Markdown, TXT, CSV (UTF-8 BOM so Excel shows Indic text correctly), canonical JSON."""
from __future__ import annotations

import csv
import io
from typing import List

from ..charts import as_table
from ..i18n import format_cell, meta_label, parse_number, whole_rupees
from ..model import Chart, Document, Heading, KeyValue, ListBlock, PageBreak, Paragraph, Signature, Slide, Table

META_ORDER = ("date", "from", "to", "subject", "ref")


def _blocks(doc: Document) -> list:
    """The document's blocks, with each chart given as the table of its numbers (text formats can't draw)."""
    return [as_table(b) if isinstance(b, Chart) else b for b in doc.blocks]


def _md_escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _table_rows(t: Table, total_word: str, doc_type: str = "report") -> List[List[str]]:
    whole = whole_rupees(t, doc_type)
    rows = [[format_cell(v, t.columns[i].type, whole[i]) for i, v in enumerate(r)] for r in t.rows]
    numeric = [c.type in ("number", "currency") for c in t.columns]
    if t.total_row and any(numeric) and t.rows:
        first_text = next((i for i, n in enumerate(numeric) if not n), None)
        total = []
        for i, col in enumerate(t.columns):
            if numeric[i]:
                total.append(format_cell(sum(parse_number(r[i]) or 0 for r in t.rows), col.type, whole[i]))
            else:
                total.append(total_word if i == first_text else "")
        rows.append(total)
    return rows


def render_md(doc: Document) -> bytes:
    out = [f"# {doc.title}", ""]
    out += [f"**{meta_label(k, doc.lang)}:** {doc.meta[k]}  " for k in META_ORDER if k in doc.meta]
    for b in _blocks(doc):
        out.append("")
        if isinstance(b, Heading):
            out.append("#" * (b.level + 1) + " " + b.text)
        elif isinstance(b, Paragraph):
            out.append("".join(
                (f"**{r.text}**" if r.bold else f"*{r.text}*" if r.italic else r.text) for r in b.runs))
        elif isinstance(b, ListBlock):
            out += [(f"{i + 1}. " if b.ordered else "- ") + it for i, it in enumerate(b.items)]
        elif isinstance(b, Table):
            if b.caption:
                out += [f"**{b.caption}**", ""]
            out.append("| " + " | ".join(_md_escape_cell(c.name) for c in b.columns) + " |")
            out.append("|" + "|".join("---:" if c.type in ("number", "currency") else "---" for c in b.columns) + "|")
            rows = _table_rows(b, meta_label("total", doc.lang))
            out += ["| " + " | ".join(_md_escape_cell(x) for x in r) + " |" for r in rows]
        elif isinstance(b, KeyValue):
            out += [f"- **{p[0]}:** {p[1]}" for p in b.pairs if len(p) >= 2]
        elif isinstance(b, Signature):
            out += [b.closing, "", f"**{b.name}**"] + ([b.designation] if b.designation else [])
        elif isinstance(b, PageBreak):
            out.append("---")
        elif isinstance(b, Slide):
            out += ([f"## {b.title}"] + ([f"> **{b.quote}**"] if b.quote else [])
                    + [f"- {x}" for x in b.bullets + b.right] + ([f"> {b.notes}"] if b.notes else []))
    return ("\n".join(out).strip() + "\n").encode("utf-8")


def render_txt(doc: Document) -> bytes:
    out = [doc.title, "=" * min(len(doc.title), 60), ""]
    out += [f"{meta_label(k, doc.lang)}: {doc.meta[k]}" for k in META_ORDER if k in doc.meta]
    for b in _blocks(doc):
        out.append("")
        if isinstance(b, Heading):
            out += [b.text, ("-" if b.level > 1 else "=") * min(len(b.text), 60)]
        elif isinstance(b, Paragraph):
            out.append(b.text)
        elif isinstance(b, ListBlock):
            out += [(f"{i + 1}. " if b.ordered else "* ") + it for i, it in enumerate(b.items)]
        elif isinstance(b, Table):
            rows = [[c.name for c in b.columns], *_table_rows(b, meta_label("total", doc.lang))]
            widths = [max(len(r[i]) for r in rows) for i in range(len(b.columns))]
            if b.caption:
                out.append(b.caption)
            out += ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip() for r in rows]
        elif isinstance(b, KeyValue):
            out += [f"{p[0]}: {p[1]}" for p in b.pairs if len(p) >= 2]
        elif isinstance(b, Signature):
            out += [b.closing, "", b.name] + ([b.designation] if b.designation else [])
        elif isinstance(b, PageBreak):
            out.append("\f")
        elif isinstance(b, Slide):
            out += ([b.title] + ([b.quote] if b.quote else []) + [f"* {x}" for x in b.bullets + b.right]
                    + ([f"(notes: {b.notes})"] if b.notes else []))
    return ("\n".join(out).strip() + "\n").encode("utf-8")


def render_csv(doc: Document) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    tables = [b for b in _blocks(doc) if isinstance(b, Table)]
    if not tables:  # no tabular data: one text column so the file is still useful
        w.writerow(["text"])
        for line in render_txt(doc).decode("utf-8").splitlines():
            if line.strip():
                w.writerow([line])
    for n, t in enumerate(tables):
        if n:
            w.writerow([])
        if len(tables) > 1 and t.caption:
            w.writerow([t.caption])
        w.writerow([c.name for c in t.columns])
        # Raw values (not display-formatted) so spreadsheets can compute on them.
        w.writerows([["" if v is None else v for v in r] for r in t.rows])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def render_json(doc: Document) -> bytes:
    return doc.model_dump_json(indent=2).encode("utf-8")
