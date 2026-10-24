"""DOCX renderer (python-docx).

Complex scripts (Devanagari, Tamil, Urdu, ...) need the *complex-script* run properties set
explicitly — w:rFonts/@w:cs, w:bCs, w:iCs, w:szCs — otherwise Word ignores bold/size/font for
those characters. RTL scripts additionally need w:bidi (paragraph) and w:rtl (run).

Long documents (style.cover / toc / page_numbers): a cover page, a contents field that Word fills in when the file
opens (its cached text lists the headings, for readers that don't update fields), numbered sections, a running
header and "Page X of Y". Charts are real Word charts (a chart part beside the document). Table header rows repeat
on every page.

The look follows the PDF (html_r.py): A4, section numbers in the accent, tables ruled above, below and between rows
with a shaded header and total (no grid), whole rupees where every amount is whole, and the details still to check
in a box at the end.
"""
from __future__ import annotations

import io

from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

from ..charts import chart_xml, ooxml_data
from ..deck import THEMES
from ..i18n import SCRIPT_LANG, fonts_for, format_cell, meta_label, parse_number, whole_rupees
from ..model import Chart, Document, Heading, KeyValue, ListBlock, PageBreak, Paragraph, Run, Signature, Slide, Table

META_ORDER = ("date", "from", "to", "subject", "ref")
_T = THEMES["boldoc"]
ACCENT, MUTED, RULE, PANEL, DARK = _T["accent"], _T["muted"], _T["rule"], _T["panel"], "1C1917"
TEXT_W_MM = 210 - 2 * 22  # A4 less the side margins


# OOXML child-element order (ECMA-376 CT_RPr / CT_PPr). Word rejects files whose children are out of order,
# so every element we add goes in its schema position — never appended blindly.
RPR_SEQ = ("w:rStyle", "w:rFonts", "w:b", "w:bCs", "w:i", "w:iCs", "w:caps", "w:smallCaps", "w:strike",
           "w:dstrike", "w:outline", "w:shadow", "w:emboss", "w:imprint", "w:noProof", "w:snapToGrid", "w:vanish",
           "w:webHidden", "w:color", "w:spacing", "w:w", "w:kern", "w:position", "w:sz", "w:szCs", "w:highlight",
           "w:u", "w:effect", "w:bdr", "w:shd", "w:fitText", "w:vertAlign", "w:rtl", "w:cs", "w:em", "w:lang",
           "w:eastAsianLayout", "w:specVanish", "w:oMath")
PPR_SEQ = ("w:pStyle", "w:keepNext", "w:keepLines", "w:pageBreakBefore", "w:framePr", "w:widowControl", "w:numPr",
           "w:suppressLineNumbers", "w:pBdr", "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap",
           "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
           "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap",
           "w:jc", "w:textDirection", "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl", "w:divId",
           "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange")
TBLPR_SEQ = ("w:tblStyle", "w:tblpPr", "w:tblOverlap", "w:bidiVisual", "w:tblStyleRowBandSize",
             "w:tblStyleColBandSize", "w:tblW", "w:jc", "w:tblCellSpacing", "w:tblInd", "w:tblBorders", "w:shd",
             "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription")
TCPR_SEQ = ("w:cnfStyle", "w:tcW", "w:gridSpan", "w:hMerge", "w:vMerge", "w:tcBorders", "w:shd", "w:noWrap",
            "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark")


def get_or_add_ordered(parent, tag: str, seq: tuple):
    el = parent.find(qn(tag))
    if el is not None:
        return el
    el = OxmlElement(tag)
    successors = {qn(t) for t in seq[seq.index(tag) + 1:]}
    for child in parent:
        if child.tag in successors:
            child.addprevious(el)
            return el
    parent.append(el)
    return el


def _set_flag(parent, tag: str, on: bool, seq: tuple) -> None:
    if on:
        get_or_add_ordered(parent, tag, seq)
    else:
        el = parent.find(qn(tag))
        if el is not None:
            parent.remove(el)


def _style_run(run, *, latin: str, cs: str, size_pt: float, bold=False, italic=False, underline=False,
               rtl=False) -> None:
    run.font.name = latin
    run.font.size = Pt(size_pt)
    run.bold = bold or None  # None removes the element instead of writing w:val="0"
    run.italic = italic or None
    run.underline = underline or None
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.get_or_add_rFonts()
    for attr, val in (("w:ascii", latin), ("w:hAnsi", latin), ("w:cs", cs)):
        fonts.set(qn(attr), val)
    _set_flag(rpr, "w:bCs", bold, RPR_SEQ)
    _set_flag(rpr, "w:iCs", italic, RPR_SEQ)
    get_or_add_ordered(rpr, "w:szCs", RPR_SEQ).set(qn("w:val"), str(int(size_pt * 2)))
    _set_flag(rpr, "w:rtl", rtl, RPR_SEQ)


def _para_rtl(paragraph) -> None:
    get_or_add_ordered(paragraph._element.get_or_add_pPr(), "w:bidi", PPR_SEQ)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT


class _Ctx:
    def __init__(self, doc: Document):
        f = fonts_for(doc.script)
        self.latin = fonts_for("Latn")["office"]
        self.cs = f["office"]
        self.base = doc.style.base_font_pt
        self.rtl = doc.rtl
        self.lang = doc.lang

    def add_runs(self, paragraph, runs, size=None, bold_all=False, color=None):
        for r in runs:
            run = paragraph.add_run(r.text)
            _style_run(run, latin=self.latin, cs=self.cs, size_pt=size or self.base, bold=r.bold or bold_all,
                       italic=r.italic, underline=r.underline, rtl=self.rtl)
            if color:
                run.font.color.rgb = RGBColor.from_string(color)
        if self.rtl:
            _para_rtl(paragraph)
        return paragraph


def _set_document_defaults(d, doc: Document, ctx: _Ctx) -> None:
    normal = d.styles["Normal"]
    normal.font.name = ctx.latin
    normal.font.size = Pt(ctx.base)
    rpr = normal.element.get_or_add_rPr()
    rpr.get_or_add_rFonts().set(qn("w:cs"), ctx.cs)
    lang = get_or_add_ordered(rpr, "w:lang", RPR_SEQ)
    lang.set(qn("w:val"), "en-IN")
    lang.set(qn("w:bidi"), SCRIPT_LANG.get(doc.script, doc.lang))
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15
    props = d.core_properties
    props.title = doc.title
    props.author = "BolDoc"
    props.language = doc.lang
    props.revision = doc.version


def _edges(parent, edges: dict) -> None:
    """Border edges, given in their schema order: {"top": ("single", eighths of a point, colour), "left": ("nil",)}."""
    for edge, spec in edges.items():
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), spec[0])
        if spec[0] != "nil":
            el.set(qn("w:sz"), str(spec[1]))
            el.set(qn("w:space"), "0" if len(spec) < 4 else str(spec[3]))
            el.set(qn("w:color"), spec[2])
        parent.append(el)


def _shade(cell, fill: str) -> None:
    shd = get_or_add_ordered(cell._tc.get_or_add_tcPr(), "w:shd", TCPR_SEQ)
    for k, v in (("w:val", "clear"), ("w:color", "auto"), ("w:fill", fill)):
        shd.set(qn(k), v)


def _clean(tbl, header: bool = True, total: bool = False) -> None:
    """Rules above, below and between rows, a shaded header with a dark rule under it, a shaded total with one
    over it; no vertical lines; the full text width."""
    tbl_pr = tbl._tbl.tblPr
    width = get_or_add_ordered(tbl_pr, "w:tblW", TBLPR_SEQ)
    width.set(qn("w:type"), "pct")
    width.set(qn("w:w"), "5000")
    borders = get_or_add_ordered(tbl_pr, "w:tblBorders", TBLPR_SEQ)
    _edges(borders, {"top": ("single", 8, DARK), "left": ("nil",), "bottom": ("single", 8, DARK),
                     "right": ("nil",), "insideH": ("single", 4, RULE), "insideV": ("nil",)})
    margins = get_or_add_ordered(tbl_pr, "w:tblCellMar", TBLPR_SEQ)
    for edge, w in (("top", 50), ("left", 115), ("bottom", 50), ("right", 115)):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:w"), str(w))
        el.set(qn("w:type"), "dxa")
        margins.append(el)
    rows = list(tbl.rows)
    for row, edge in ((rows[0], "bottom") if header else (None, ""), (rows[-1], "top") if total else (None, "")):
        if row is None:
            continue
        for cell in row.cells:
            _shade(cell, PANEL)
            _edges(get_or_add_ordered(cell._tc.get_or_add_tcPr(), "w:tcBorders", TCPR_SEQ),
                   {edge: ("single", 8, DARK)})


def _table(d, ctx: _Ctx, t: Table, doc_type: str) -> None:
    if t.caption:
        cap = ctx.add_runs(d.add_paragraph(), [Run(text=t.caption, bold=True)], size=ctx.base - 0.5)
        cap.paragraph_format.space_before, cap.paragraph_format.space_after = Pt(6), Pt(4)
        cap.paragraph_format.keep_with_next = True
    numeric = [c.type in ("number", "currency") for c in t.columns]
    whole = whole_rupees(t, doc_type)
    size = ctx.base - 1
    has_total = bool(t.total_row and any(numeric) and t.rows)
    n_rows = 1 + len(t.rows) + (1 if has_total else 0)
    tbl = d.add_table(rows=n_rows, cols=len(t.columns))
    tbl.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))  # repeats on every page
    for ci, col in enumerate(t.columns):
        p = ctx.add_runs(tbl.cell(0, ci).paragraphs[0], [Run(text=col.name, bold=True)], size=size)
        if numeric[ci]:
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for ri, row in enumerate(t.rows, start=1):
        for ci, val in enumerate(row):
            p = tbl.cell(ri, ci).paragraphs[0]
            ctx.add_runs(p, [Run(text=format_cell(val, t.columns[ci].type, whole[ci]))], size=size)
            if numeric[ci]:
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    if has_total:
        last = n_rows - 1
        first_text = next((i for i, n in enumerate(numeric) if not n), None)
        if first_text is not None:
            ctx.add_runs(tbl.cell(last, first_text).paragraphs[0], [Run(text=meta_label("total", ctx.lang), bold=True)],
                         size=size)
        for ci, is_num in enumerate(numeric):
            if is_num:
                total = sum(parse_number(r[ci]) or 0 for r in t.rows)
                p = tbl.cell(last, ci).paragraphs[0]
                ctx.add_runs(p, [Run(text=format_cell(total, t.columns[ci].type, whole[ci]), bold=True)], size=size)
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for row in tbl.rows:  # cell paragraphs keep no space after: the cell margins give the air
        for cell in row.cells:
            for p in cell.paragraphs:
                p.paragraph_format.space_after = Pt(0)
    _clean(tbl, header=True, total=has_total)
    d.add_paragraph().paragraph_format.space_after = Pt(2)


def _kv(d, ctx: _Ctx, b: KeyValue) -> None:
    pairs = [p[:2] for p in b.pairs if len(p) >= 2]
    if not pairs:
        return
    tbl = d.add_table(rows=len(pairs), cols=2)
    for i, (k, v) in enumerate(pairs):
        ctx.add_runs(tbl.cell(i, 0).paragraphs[0], [Run(text=k)], size=ctx.base - 0.5, color=MUTED)
        ctx.add_runs(tbl.cell(i, 1).paragraphs[0], [Run(text=v, bold=True)], size=ctx.base - 0.5)
        for c in (0, 1):
            tbl.cell(i, c).paragraphs[0].paragraph_format.space_after = Pt(0)
    _clean(tbl, header=False)
    d.add_paragraph().paragraph_format.space_after = Pt(2)


def _para_edges(paragraph, edges: dict) -> None:
    _edges(get_or_add_ordered(paragraph._element.get_or_add_pPr(), "w:pBdr", PPR_SEQ), edges)


CHART_CT = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
CHART_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart"
XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PACKAGE_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/package"
_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
       'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
       'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
# w:settings children after updateFields (ECMA-376 CT_Settings): it goes before the first of these present
_AFTER_UPDATE_FIELDS = ("w:hdrShapeDefaults", "w:footnotePr", "w:endnotePr", "w:compat", "w:docVars", "w:rsids",
                        "m:mathPr", "w:attachedSchema", "w:themeFontLang", "w:clrSchemeMapping",
                        "w:doNotIncludeSubdocsInStats", "w:doNotAutoCompressPictures", "w:forceUpgrade",
                        "w:captions", "w:readModeInkLockDown", "w:smartTagType", "w:schemaLibrary",
                        "w:shapeDefaults", "w:doNotEmbedSmartTags", "w:decimalSymbol", "w:listSeparator")


def _chart(d, c: Chart, n: int) -> None:
    """A real Word chart: the chart XML as its own part, its numbers cached there and in an embedded workbook
    (Word's "Edit data"; Apple's previewer hangs without it), shown inline."""
    package = d.part.package
    part = Part(PackURI(f"/word/charts/chart{n}.xml"), CHART_CT, b"", package)
    book = Part(PackURI(f"/word/embeddings/Microsoft_Excel_Worksheet{n}.xlsx"), XLSX_CT, ooxml_data(c).xlsx_blob,
                package)
    part._blob = chart_xml(c, part.relate_to(book, PACKAGE_RT))
    rid = d.part.relate_to(part, CHART_RT)
    run = d.add_paragraph().add_run()
    run._r.append(parse_xml(
        f'<w:drawing {_NS}><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="5760000" '
        f'cy="{3240000 if c.kind != "pie" else 3600000}"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f'<wp:docPr id="{5000 + n}" name="Chart {n}"/><wp:cNvGraphicFramePr/><a:graphic><a:graphicData '
        f'uri="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart r:id="{rid}"/></a:graphicData>'
        f'</a:graphic></wp:inline></w:drawing>'))


def _fld(paragraph, instr: str, shown: str) -> None:
    paragraph._p.append(parse_xml(f'<w:fldSimple {_NS} w:instr="{instr}"><w:r><w:t>{shown}</w:t></w:r>'
                                  f'</w:fldSimple>'))


def _update_fields_on_open(d) -> None:
    settings = d.settings.element
    if settings.find(qn("w:updateFields")) is not None:
        return
    el = OxmlElement("w:updateFields")
    el.set(qn("w:val"), "true")
    for tag in _AFTER_UPDATE_FIELDS:
        try:
            anchor = settings.find(qn(tag))
        except KeyError:  # a prefix python-docx doesn't map
            continue
        if anchor is not None:
            anchor.addprevious(el)
            return
    settings.append(el)


def _cover(d, ctx: _Ctx, doc: Document) -> None:
    """The title, what the document is for (meta subtitle), a short accent rule, any letter-style details, and at
    the foot of the page who presents it and when (meta author, date): as the PDF's cover."""
    m = doc.meta
    title = ctx.add_runs(d.add_paragraph(), [Run(text=doc.title, bold=True)], size=ctx.base + 17)
    title.paragraph_format.space_before, title.paragraph_format.space_after = Pt(150), Pt(8)
    title.paragraph_format.line_spacing = 1.05
    lines = len(doc.title) // 34 + 1
    if m.get("subtitle"):
        sub = ctx.add_runs(d.add_paragraph(), [Run(text=m["subtitle"])], size=ctx.base + 2.5, color=MUTED)
        lines += len(m["subtitle"]) // 70 + 1
        sub.paragraph_format.space_after = Pt(0)
    rule = d.add_paragraph()
    rule.paragraph_format.space_before, rule.paragraph_format.space_after = Pt(16), Pt(18)
    if ctx.rtl:
        rule.paragraph_format.left_indent = Mm(TEXT_W_MM - 26)
    else:
        rule.paragraph_format.right_indent = Mm(TEXT_W_MM - 26)  # a 26 mm rule: the border spans the indents
    _para_edges(rule, {"bottom": ("single", 24, ACCENT)})
    for key in META_ORDER:
        if key in m and key != "date":
            ctx.add_runs(d.add_paragraph(), [Run(text=f"{meta_label(key, doc.lang)}: ", bold=True), Run(text=m[key])],
                         size=ctx.base + 1)
            lines += 1
    by = [k for k in ("author", "date") if m.get(k)]
    for i, key in enumerate(by):
        p = ctx.add_runs(d.add_paragraph(), [Run(text=m[key], bold=key == "author")], size=ctx.base + 0.5,
                         color=MUTED if key == "date" else None)
        p.paragraph_format.space_after = Pt(2)
        if i == 0:  # the byline sits near the foot of the page
            p.paragraph_format.space_before = Pt(max(40, 330 - 26 * lines))
    d.add_page_break()


def _toc(d, ctx: _Ctx, doc: Document, numbers: dict) -> None:
    ctx.add_runs(d.add_paragraph(), [Run(text=meta_label("contents", doc.lang), bold=True)], size=ctx.base + 5)
    heads = [b for b in doc.blocks if isinstance(b, Heading) and b.level <= 2]
    first = d.add_paragraph()
    first._p.append(parse_xml(f'<w:r {_NS}><w:fldChar w:fldCharType="begin"/></w:r>'))
    first._p.append(parse_xml(f'<w:r {_NS}><w:instrText xml:space="preserve"> TOC \\o "1-2" \\h \\z \\u '
                              f'</w:instrText></w:r>'))
    first._p.append(parse_xml(f'<w:r {_NS}><w:fldChar w:fldCharType="separate"/></w:r>'))
    last = first
    for i, h in enumerate(heads):  # what readers that don't update fields show: the headings, in order
        p = first if i == 0 else d.add_paragraph()
        indent = "    " if h.level == 2 else ""
        ctx.add_runs(p, [Run(text=f"{indent}{numbers.get(h.id, '')}  {h.text}")])
        last = p
    last._p.append(parse_xml(f'<w:r {_NS}><w:fldChar w:fldCharType="end"/></w:r>'))
    _update_fields_on_open(d)
    d.add_page_break()


def _header_footer(d, ctx: _Ctx, doc: Document) -> None:
    section = d.sections[0]
    section.different_first_page_header_footer = doc.style.cover  # the cover page stays clean
    head = section.header.paragraphs[0]
    ctx.add_runs(head, [Run(text=doc.title)], size=ctx.base - 2)
    head.alignment = WD_ALIGN_PARAGRAPH.LEFT if ctx.rtl else WD_ALIGN_PARAGRAPH.RIGHT
    for r in head.runs:
        r.font.color.rgb = RGBColor(0x6B, 0x6B, 0x66)
    foot = section.footer.paragraphs[0]
    foot.alignment = WD_ALIGN_PARAGRAPH.CENTER
    ctx.add_runs(foot, [Run(text=f"{meta_label('page', doc.lang)} ")], size=ctx.base - 2)
    _fld(foot, "PAGE", "1")
    ctx.add_runs(foot, [Run(text=f" {meta_label('of', doc.lang)} ")], size=ctx.base - 2)
    _fld(foot, "NUMPAGES", "1")


def _page(d) -> None:
    """A4, with the PDF's margins (python-docx's own template is US Letter)."""
    sec = d.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.top_margin, sec.bottom_margin, sec.left_margin, sec.right_margin = Mm(22), Mm(20), Mm(22), Mm(22)
    sec.header_distance, sec.footer_distance = Mm(10), Mm(10)


def _heading(d, ctx: _Ctx, b: Heading, number: str) -> None:
    p = d.add_paragraph(style=f"Heading {b.level}")
    size = ctx.base + {1: 4, 2: 2, 3: 1}[b.level]
    if number:
        ctx.add_runs(p, [Run(text=f"{number}  ", bold=True)], size=size, color=ACCENT if b.level == 1 else MUTED)
    ctx.add_runs(p, [Run(text=b.text, bold=True)], size=size, color=DARK)
    pf = p.paragraph_format
    pf.space_before, pf.space_after, pf.keep_with_next = Pt({1: 20, 2: 14, 3: 10}[b.level]), Pt(6), True
    if b.level == 1:
        _para_edges(p, {"bottom": ("single", 6, RULE, 4)})


def render(doc: Document) -> bytes:
    d = DocxDocument()
    ctx = _Ctx(doc)
    _page(d)
    _set_document_defaults(d, doc, ctx)
    numbers = doc.heading_numbers() if doc.style.toc else {}
    if doc.style.cover:
        _cover(d, ctx, doc)
    else:
        ctx.add_runs(d.add_paragraph(), [Run(text=doc.title, bold=True)], size=ctx.base + 7)
        for key in META_ORDER:
            if key in doc.meta:
                label = meta_label(key, doc.lang)
                ctx.add_runs(d.add_paragraph(), [Run(text=f"{label}: ", bold=True), Run(text=doc.meta[key])])
    if doc.style.toc:
        _toc(d, ctx, doc, numbers)
    if doc.style.page_numbers:
        _header_footer(d, ctx, doc)
    n_chart = 0
    sources_word, prev = meta_label("sources", doc.lang), None

    for b in doc.blocks:
        if isinstance(b, Heading):
            _heading(d, ctx, b, numbers.get(b.id, ""))
        elif isinstance(b, Chart):
            n_chart += 1
            _chart(d, b, n_chart)
        elif isinstance(b, Paragraph):
            ctx.add_runs(d.add_paragraph(), b.runs)
        elif isinstance(b, ListBlock):
            style = "List Number" if b.ordered else "List Bullet"
            sources = isinstance(prev, Heading) and prev.text.strip() == sources_word  # set smaller, as references
            for item in b.items:
                p = ctx.add_runs(d.add_paragraph(style=style), [Run(text=item)],
                                 size=ctx.base - 1.5 if sources else None)
                p.paragraph_format.space_after = Pt(3)
        elif isinstance(b, Table):
            _table(d, ctx, b, doc.doc_type)
        elif isinstance(b, KeyValue):
            _kv(d, ctx, b)
        elif isinstance(b, Signature):
            ctx.add_runs(d.add_paragraph(), [Run(text=b.closing)])
            d.add_paragraph()
            ctx.add_runs(d.add_paragraph(), [Run(text=b.name, bold=True)])
            if b.designation:
                ctx.add_runs(d.add_paragraph(), [Run(text=b.designation)])
        elif isinstance(b, PageBreak):
            d.add_page_break()
        elif isinstance(b, Slide):
            ctx.add_runs(d.add_paragraph(style="Heading 2"), [Run(text=b.title, bold=True)], size=ctx.base + 3)
            for bullet in b.bullets:
                ctx.add_runs(d.add_paragraph(style="List Bullet"), [Run(text=bullet)])
            if b.quote:
                ctx.add_runs(d.add_paragraph(), [Run(text=b.quote, bold=True)], size=ctx.base + 4)
            for bullet in b.right:
                ctx.add_runs(d.add_paragraph(style="List Bullet"), [Run(text=bullet)])
            if b.notes:
                ctx.add_runs(d.add_paragraph(), [Run(text=b.notes, italic=True)])
        prev = b

    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
