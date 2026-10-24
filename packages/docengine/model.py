"""Canonical document model.

The LLM never writes DOCX/XLSX/PPTX bytes. It produces (or patches) this JSON model; deterministic
renderers turn it into files. Every block has a stable `id` so spoken references ("the second point")
resolve to the same block across versions, and edits can target blocks precisely.

Kept Python 3.9-compatible (typing.Optional/List) because pydantic evaluates annotations at runtime.
"""
from __future__ import annotations

import secrets
import unicodedata
from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator
from typing_extensions import Annotated

BLOCK_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alikes (0/o, 1/l/i)


def new_block_id() -> str:
    return "b_" + "".join(secrets.choice(BLOCK_ID_ALPHABET) for _ in range(6))


class Run(BaseModel):
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False


def _id_field() -> Field:  # type: ignore[valid-type]
    return Field(default_factory=new_block_id)


class Heading(BaseModel):
    type: Literal["heading"] = "heading"
    id: str = _id_field()
    level: int = Field(1, ge=1, le=3)
    text: str


class Paragraph(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    id: str = _id_field()
    runs: List[Run]

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


class ListBlock(BaseModel):
    type: Literal["list"] = "list"
    id: str = _id_field()
    ordered: bool = False
    items: List[str]


ColumnType = Literal["text", "number", "currency", "percent", "date"]


class Column(BaseModel):
    name: str
    type: ColumnType = "text"


class Table(BaseModel):
    type: Literal["table"] = "table"
    id: str = _id_field()
    caption: Optional[str] = None
    columns: List[Column]
    rows: List[List[Optional[Union[float, int, str]]]] = Field(default_factory=list)
    total_row: bool = False  # renderers add a SUM row for number/currency columns

    @field_validator("rows")
    @classmethod
    def _row_width(cls, rows, info):
        cols = info.data.get("columns") or []
        for i, r in enumerate(rows):
            if len(r) != len(cols):
                raise ValueError(f"row {i} has {len(r)} cells, expected {len(cols)}")
        return rows


class KeyValue(BaseModel):
    type: Literal["kv"] = "kv"
    id: str = _id_field()
    pairs: List[List[str]]  # [[key, value], ...]


class Signature(BaseModel):
    type: Literal["signature"] = "signature"
    id: str = _id_field()
    closing: str = "Sincerely,"
    name: str
    designation: Optional[str] = None


class PageBreak(BaseModel):
    type: Literal["page_break"] = "page_break"
    id: str = _id_field()


SlideLayout = Literal["bullets", "title", "section", "two_column", "quote", "closing"]


class Slide(BaseModel):
    """A deck's slide. Layouts: bullets (title + points), title (a title slide), section (a divider), two_column
    (bullets on the left, `right` on the right), quote (one line or a big number, `quote`), closing (thanks, contact).
    A table or chart block that follows a slide gets a slide of its own."""
    type: Literal["slide"] = "slide"
    id: str = _id_field()
    title: str
    bullets: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    layout: SlideLayout = "bullets"
    right: List[str] = Field(default_factory=list)   # two_column: the right-hand column
    quote: Optional[str] = None                      # quote: the line (or number) shown large


class ChartSeries(BaseModel):
    name: str
    values: List[float]


class Chart(BaseModel):
    """Numbers drawn: a bar (horizontal), column, line or pie chart. Every format draws it natively where it can
    (PowerPoint, Excel, Word charts; SVG in PDF and HTML); text formats give its data as a table."""
    type: Literal["chart"] = "chart"
    id: str = _id_field()
    kind: Literal["column", "bar", "line", "pie"] = "column"
    title: Optional[str] = None
    categories: List[str]
    series: List[ChartSeries]
    unit: Literal["number", "currency", "percent"] = "number"

    @field_validator("series")
    @classmethod
    def _series_fit(cls, series, info):
        cats = info.data.get("categories") or []
        if not series:
            raise ValueError("a chart needs at least one series")
        for s in series:
            if len(s.values) != len(cats):
                raise ValueError(f"series {s.name!r} has {len(s.values)} values, expected {len(cats)}")
        if info.data.get("kind") == "pie" and len(series) != 1:
            raise ValueError("a pie chart has exactly one series")
        return series


Block = Annotated[
    Union[Heading, Paragraph, ListBlock, Table, KeyValue, Signature, PageBreak, Slide, Chart],
    Field(discriminator="type"),
]

DocType = Literal["letter", "application", "report", "notes", "invoice", "resume", "minutes",
                  "table", "slides", "other"]


class Style(BaseModel):
    template: Literal["clean", "formal"] = "clean"
    base_font_pt: int = Field(11, ge=8, le=16)
    # long documents: a cover page, a contents page, page numbers and a running header
    cover: bool = False
    toc: bool = False
    page_numbers: bool = False
    theme: Literal["boldoc", "plain", "dark"] = "boldoc"   # decks


class Document(BaseModel):
    id: str
    title: str
    lang: str = "en-IN"          # BCP-47 of the document content
    script: str = "Latn"         # ISO 15924, e.g. Deva, Taml, Beng, Arab (Urdu)
    doc_type: DocType = "other"
    meta: Dict[str, str] = Field(default_factory=dict)   # to/from/date/subject/...
    blocks: List[Block] = Field(default_factory=list)
    style: Style = Field(default_factory=Style)
    version: int = 1
    assumptions: List[str] = Field(default_factory=list)  # placeholders/defaults the thinker had to assume

    def block(self, block_id: str):
        for b in self.blocks:
            if b.id == block_id:
                return b
        return None

    def index_of(self, block_id: str) -> int:
        for i, b in enumerate(self.blocks):
            if b.id == block_id:
                return i
        return -1

    def heading_numbers(self) -> Dict[str, str]:
        """Section numbers for a long document's headings, by block id: 1, 1.1, 1.1.1."""
        out, count = {}, [0, 0, 0]
        for b in self.blocks:
            if isinstance(b, Heading):
                count[b.level - 1] += 1
                for i in range(b.level, 3):
                    count[i] = 0
                out[b.id] = ".".join(str(n) for n in count[:b.level])
        return out

    def all_text(self) -> str:
        parts: List[str] = [self.title, *self.meta.values(), *self.assumptions]
        for b in self.blocks:
            if isinstance(b, (Heading, Paragraph)):
                parts.append(b.text)
            elif isinstance(b, ListBlock):
                parts.extend(b.items)
            elif isinstance(b, Table):
                parts.extend(c.name for c in b.columns)
                parts.extend(str(c) for row in b.rows for c in row if c is not None)
                if b.caption:
                    parts.append(b.caption)
            elif isinstance(b, KeyValue):
                parts.extend(x for pair in b.pairs for x in pair)
            elif isinstance(b, Signature):
                parts.extend([b.closing, b.name, b.designation or ""])
            elif isinstance(b, Slide):
                parts.extend([b.title, *b.bullets, *b.right, b.quote or "", b.notes or ""])
            elif isinstance(b, Chart):
                parts.extend([b.title or "", *b.categories, *(s.name for s in b.series)])
        return "\n".join(p for p in parts if p)

    @property
    def rtl(self) -> bool:
        return self.script in {"Arab", "Hebr"}


def detect_script(text: str) -> str:
    """Dominant non-Latin script of `text` (ISO 15924), else 'Latn'. Used to pick fonts."""
    names = {
        "DEVANAGARI": "Deva", "BENGALI": "Beng", "GURMUKHI": "Guru", "GUJARATI": "Gujr", "ORIYA": "Orya",
        "TAMIL": "Taml", "TELUGU": "Telu", "KANNADA": "Knda", "MALAYALAM": "Mlym", "ARABIC": "Arab",
    }
    counts: Dict[str, int] = {}
    for ch in text:
        if ord(ch) < 0x0590:  # Latin/Greek/Cyrillic etc.; Arabic starts at U+0600
            continue
        try:
            first = unicodedata.name(ch).split(" ")[0]
        except ValueError:
            continue
        if first in names:
            counts[names[first]] = counts.get(names[first], 0) + 1
    return max(counts, key=counts.get) if counts else "Latn"
