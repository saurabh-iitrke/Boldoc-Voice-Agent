"""HTML renderer: the HTML download and the source for PDF (WeasyPrint shapes Indic text via HarfBuzz).

A long document (style.cover, toc, page_numbers) gets a cover page, contents with page numbers (target-counter),
numbered sections, its title as a running header and "Page X of Y" (CSS paged media, laid out by WeasyPrint;
browsers ignore those rules). A deck (any Slide block) prints one 16:9 page per slide in its theme, laid out like
the PowerPoint. Charts are inline SVG, so the PDF needs nothing extra.
"""
from __future__ import annotations

from html import escape
from typing import Dict, List, Optional, Tuple

from ..charts import CHART_CSS, chart_html
from ..deck import SLIDE_BULLETS, SLIDE_ROWS, THEMES, subtitle, theme
from ..i18n import fonts_for, format_cell, meta_label, parse_number, whole_rupees
from ..model import Chart, Document, Heading, KeyValue, ListBlock, PageBreak, Paragraph, Signature, Slide, Table

META_ORDER = ("date", "from", "to", "subject", "ref")


def _runs(p: Paragraph) -> str:
    out = []
    for r in p.runs:
        t = escape(r.text)
        if r.bold:
            t = f"<strong>{t}</strong>"
        if r.italic:
            t = f"<em>{t}</em>"
        if r.underline:
            t = f"<u>{t}</u>"
        out.append(t)
    return "".join(out)


def _css_str(text: str) -> str:
    """A CSS string literal, for generated content such as the page footer."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _ul(items: List[str]) -> str:
    return "<ul>" + "".join(f"<li>{escape(i)}</li>" for i in items) + "</ul>"


def _table(t: Table, total_word: str, doc_type: str = "report", rows: Optional[list] = None,
           with_total: bool = True, caption: bool = True) -> str:
    """rows: this page's share of a table split across slides; the total (of every row) goes with the last."""
    numeric = [c.type in ("number", "currency") for c in t.columns]
    whole = whole_rupees(t, doc_type)
    head = "".join(f"<th{' class=num' if n else ''}>{escape(c.name)}</th>" for c, n in zip(t.columns, numeric))
    body = []
    for row in t.rows if rows is None else rows:
        cells = "".join(f"<td{' class=num' if numeric[i] else ''}>"
                        f"{escape(format_cell(v, t.columns[i].type, whole[i]))}</td>" for i, v in enumerate(row))
        body.append(f"<tr>{cells}</tr>")
    foot = ""
    if with_total and t.total_row and any(numeric) and t.rows:
        first_text = next((i for i, n in enumerate(numeric) if not n), None)
        cells = []
        for i, col in enumerate(t.columns):
            if numeric[i]:
                total = sum(parse_number(r[i]) or 0 for r in t.rows)
                cells.append(f"<td class=num>{escape(format_cell(total, col.type, whole[i]))}</td>")
            else:
                cells.append(f"<td>{escape(total_word) if i == first_text else ''}</td>")
        foot = f"<tfoot><tr>{''.join(cells)}</tr></tfoot>"
    cap = f"<caption>{escape(t.caption)}</caption>" if caption and t.caption else ""
    # wrapped: WeasyPrint lays a caption out apart from its rows, and would leave it alone at a page's foot
    rows_html = "".join(body)
    return f'<div class="tbl"><table>{cap}<thead><tr>{head}</tr></thead><tbody>{rows_html}</tbody>{foot}</table></div>'



# ------------------------------------------------------------------------------------------------ documents
def _block(b, numbers: Dict[str, str], total_word: str, theme_name: str, doc_type: str, cls: str = "") -> str:
    bid = escape(b.id)
    if isinstance(b, Heading):
        lvl = b.level + 1  # h1 is the document title
        num = f'<span class="num">{numbers[b.id]}</span>' if b.id in numbers else ""
        return f'<h{lvl} id="{bid}">{num}{escape(b.text)}</h{lvl}>'
    if isinstance(b, Paragraph):
        return f'<p id="{bid}">{_runs(b)}</p>'
    if isinstance(b, ListBlock):
        tag = "ol" if b.ordered else "ul"
        return (f'<{tag} id="{bid}"' + (f' class="{cls}"' if cls else "") + ">"
                + "".join(f"<li>{escape(i)}</li>" for i in b.items) + f"</{tag}>")
    if isinstance(b, Table):
        return _table(b, total_word, doc_type)
    if isinstance(b, Chart):
        return f'<figure class="chart" id="{bid}">{chart_html(b, theme_name)}</figure>'
    if isinstance(b, KeyValue):
        rows = "".join(f"<tr><th>{escape(p[0])}</th><td>{escape(p[1])}</td></tr>" for p in b.pairs if len(p) >= 2)
        return f'<table class="kv" id="{bid}">{rows}</table>'
    if isinstance(b, Signature):
        return (f'<div class="sig"><p>{escape(b.closing)}</p><p class="name">{escape(b.name)}</p>'
                + (f"<p>{escape(b.designation)}</p>" if b.designation else "") + "</div>")
    if isinstance(b, PageBreak):
        return '<div class="pagebreak"></div>'
    return ""


def _cover(doc: Document, title: str) -> str:
    """The cover of a long document: its title, what it is for (meta subtitle), and at the foot who presents it
    and when (meta author, date); any letter-style details (to, subject) under the rule."""
    m, lang = doc.meta, doc.lang
    sub = f'<p class="subtitle">{escape(m["subtitle"])}</p>' if m.get("subtitle") else ""
    labelled = "".join(f'<p class="meta"><strong>{escape(meta_label(k, lang))}:</strong> {escape(m[k])}</p>'
                       for k in META_ORDER if k in m and k != "date")
    byline = "".join(f'<p class="{k}">{escape(m[k])}</p>' for k in ("author", "date") if m.get(k))
    return (f'<section class="cover"><div class="top">{title}{sub}<div class="rule"></div>{labelled}</div>'
            + (f'<div class="byline">{byline}</div>' if byline else "") + "</section>")


def _document(doc: Document, family: str) -> Tuple[str, str]:
    st, lang = doc.style, doc.lang
    numbers = doc.heading_numbers() if st.toc else {}
    title = f'<h1 class="doctitle">{escape(doc.title)}</h1>'
    if st.cover:
        parts = [_cover(doc, title)]
    else:
        parts = [title + "".join(f'<p class="meta"><strong>{escape(meta_label(k, lang))}:</strong> '
                                 f"{escape(doc.meta[k])}</p>" for k in META_ORDER if k in doc.meta)]
    if st.toc:
        entries = "".join(f'<li class="l{b.level}"><a href="#{escape(b.id)}"><span class="num">{numbers[b.id]}</span>'
                          f"{escape(b.text)}</a></li>"
                          for b in doc.blocks if isinstance(b, Heading) and b.level <= 2)
        parts.append(f'<nav class="toc"><h2>{escape(meta_label("contents", lang))}</h2><ol>{entries}</ol></nav>')
    total_word, sources_word = meta_label("total", lang), meta_label("sources", lang)
    prev = None
    for b in doc.blocks:
        # a researched document's Sources list (apps/control/bigdoc.py) is set smaller, like references
        cls = "sources" if isinstance(b, ListBlock) and isinstance(prev, Heading) and \
            prev.text.strip() == sources_word else ""
        parts.append(_block(b, numbers, total_word, st.theme, doc.doc_type, cls))
        prev = b

    th = {k: "#" + v for k, v in THEMES["boldoc"].items()} if st.theme == "dark" else \
        {k: "#" + v for k, v in theme(doc).items()}  # a dark theme is for slides; paper stays light
    accent, muted, rule, panel = th["accent"], th["muted"], th["rule"], th["panel"]
    paged = ""
    if st.page_numbers:
        side = "left" if doc.rtl else "right"
        box = f"font-family: {family}; font-size: 8.5pt; color: {muted};"
        footer = (f"{_css_str(meta_label('page', lang) + ' ')} counter(page) "
                  f"{_css_str(' ' + meta_label('of', lang) + ' ')} counter(pages)")
        paged = (f"@page {{ @top-{side} {{ content: string(doctitle); {box} }} "
                 f"@bottom-center {{ content: {footer}; {box} }} }} "
                 f"@page :first {{ @top-{side} {{ content: none; }} "
                 + ("@bottom-center { content: none; } " if st.cover else "") + "}")
    start = "right" if doc.rtl else "left"
    css = f"""
    @page {{ size: A4; margin: 22mm 22mm 20mm; }}
    {paged}
    @page cover {{ margin: 0; }}  /* the cover runs to the sheet's edge: its accent band, as a deck's title slide */
    body {{ font-family: {family}; font-size: {st.base_font_pt}pt; line-height: 1.55; color: #1c1917;
           overflow-wrap: break-word; }}  /* a long link (a Sources list) wraps, never overflows */
    p {{ margin: 0 0 .75em; orphans: 2; widows: 2; }}
    h1 {{ font-size: 1.7em; margin: 0 0 .4em; }}
    h2 {{ font-size: 1.32em; line-height: 1.3; margin: 1.5em 0 .6em; padding-bottom: .28em;
          border-bottom: .8pt solid {rule}; }}
    h3 {{ font-size: 1.1em; line-height: 1.35; margin: 1.25em 0 .4em; }} h4 {{ font-size: 1em; margin: 1em 0 .3em; }}
    h1.doctitle {{ string-set: doctitle content(); }}
    h2, h3, h4 {{ break-after: avoid; }}
    h2 .num, h3 .num, h4 .num {{ margin-inline-end: .6em; color: {muted}; }} h2 .num {{ color: {accent}; }}
    .meta {{ margin: .1em 0; }}
    ul, ol {{ margin: .2em 0 .9em; padding-inline-start: 1.4em; }} li {{ margin: .25em 0; padding-inline-start: .1em; }}
    li::marker {{ color: {accent}; }} ol > li::marker {{ font-weight: 700; }}
    table {{ border-collapse: collapse; width: 100%; margin: .5em 0 1.2em; font-size: .93em; line-height: 1.4; }}
    .tbl {{ break-inside: avoid; }}  /* a table that fits on a page keeps its caption and rows together */
    thead {{ display: table-header-group; }} tfoot {{ display: table-row-group; }} tr {{ break-inside: avoid; }}
    th, td {{ padding: 5px 8px; text-align: start; vertical-align: top; border-bottom: .6pt solid {rule}; }}
    thead th {{ background: {panel}; border-top: 1pt solid #1c1917; border-bottom: 1pt solid #1c1917; }}
    tfoot td {{ font-weight: 700; background: {panel}; border-top: 1pt solid #1c1917;
               border-bottom: 1pt solid #1c1917; }}
    td.num, th.num {{ text-align: end; font-variant-numeric: tabular-nums; }}
    caption {{ caption-side: top; font-weight: 700; text-align: start; font-size: .95em; padding-bottom: 6px; }}
    table.kv {{ width: auto; min-width: 55%; border-top: 1pt solid #1c1917; border-bottom: 1pt solid #1c1917; }}
    table.kv th {{ background: none; color: {muted}; font-weight: 600; padding-inline-end: 2.2em; }}
    table.kv td {{ font-weight: 600; }}
    figure.chart {{ margin: 1em 0; break-inside: avoid; }}
    .sig {{ margin-top: 2em; break-inside: avoid; }} .sig .name {{ font-weight: 700; margin-top: 2.5em; }}
    .pagebreak {{ break-after: page; }}
    .cover {{ page: cover; break-after: page; height: 296mm; position: relative; border-{start}: 7mm solid {accent};
              padding: 0 24mm 0 22mm; }}
    .cover .top {{ padding-top: 78mm; }}
    .cover h1 {{ font-size: 2.45em; line-height: 1.15; margin: 0; }}
    .cover .subtitle {{ font-size: 1.2em; line-height: 1.45; color: {muted}; margin: 5mm 0 0; }}
    .cover .rule {{ width: 26mm; height: 1.4mm; background: {accent}; margin: 9mm 0; }}
    .cover .meta {{ font-size: 1.05em; margin: .35em 0; }}
    .cover .byline {{ position: absolute; bottom: 24mm; {start}: 22mm; }}
    .cover .byline p {{ margin: .2em 0; }} .cover .author {{ font-weight: 700; }} .cover .date {{ color: {muted}; }}
    nav.toc {{ break-after: page; }} nav.toc h2 {{ margin-top: 0; border-bottom: none; }}
    nav.toc ol {{ list-style: none; margin: 0; padding: 0; }} nav.toc li {{ margin: .4em 0; padding: 0; }}
    nav.toc li.l1 {{ font-weight: 700; margin-top: .8em; }} nav.toc li.l2 {{ padding-inline-start: 2.2em; }}
    nav.toc a {{ color: inherit; text-decoration: none; }}
    nav.toc a::after {{ content: leader('.') target-counter(attr(href), page); font-weight: 400; }}
    nav.toc .num {{ display: inline-block; min-width: 2.2em; color: {muted}; }}
    nav.toc li.l1 .num {{ color: {accent}; }}
    ol.sources {{ font-size: .86em; line-height: 1.45; }} ol.sources li {{ margin: .45em 0; }}
    """
    return "".join(parts), css + CHART_CSS


# ------------------------------------------------------------------------------------------------ decks
def _deck(doc: Document, family: str) -> Tuple[str, str]:
    t = {k: "#" + v for k, v in theme(doc).items()}
    total_word = meta_label("total", doc.lang)
    slides: List[str] = []

    def cover(title: str, sub: str) -> None:
        slides.append(f'<section class="slide cover"><div class="band"></div><h1 class="doctitle">{escape(title)}'
                      f"</h1>" + (f'<p class="sub">{escape(sub)}</p>' if sub else "") + "</section>")

    def add(cls: str, title: str, inner: str) -> None:
        slides.append(f'<section class="slide {cls}"><h2>{escape(title)}</h2><div class="bar"></div>{inner}'
                      f'<p class="foot">{escape(doc.title)}   ·   {len(slides) + 1}</p></section>')

    def bullets(title: str, items: List[str]) -> None:
        items = [i for i in items if i and i.strip()] or [""]
        for n in range(0, len(items), SLIDE_BULLETS):
            add("bullets", title if n == 0 else f"{title} (contd.)", _ul(items[n:n + SLIDE_BULLETS]))

    def table(tb: Table, fallback: str) -> None:
        title = tb.caption or fallback
        chunks = [tb.rows[i:i + SLIDE_ROWS] for i in range(0, max(1, len(tb.rows)), SLIDE_ROWS)] or [[]]
        for n, rows in enumerate(chunks):
            add("table", title if n == 0 else f"{title} (contd.)",
                _table(tb, total_word, doc.doc_type, rows=rows, with_total=n == len(chunks) - 1, caption=False))

    cover(doc.title, subtitle(doc))
    last_title, pending = doc.title, []

    def flush() -> None:
        if pending:
            bullets(last_title, list(pending))
        pending.clear()

    for b in doc.blocks:
        if isinstance(b, Slide):
            flush()
            last_title = b.title
            if b.layout == "title":
                cover(b.title, " · ".join(b.bullets[:2]))
            elif b.layout == "section":
                slides.append(f'<section class="slide section"><div class="panel"><h2>{escape(b.title)}</h2>'
                              f'<div class="bar"></div></div></section>')
            elif b.layout == "two_column":
                add("two", b.title, f'<div class="cols"><div>{_ul(b.bullets[:SLIDE_BULLETS])}</div>'
                                    f"<div>{_ul(b.right[:SLIDE_BULLETS])}</div></div>")
            elif b.layout == "quote":
                line = b.quote or " ".join(b.bullets[:1]) or b.title
                add("quote", b.title, f'<p class="{"big" if len(line) < 20 else "line"}">{escape(line)}</p>')
            elif b.layout == "closing":
                slides.append(f'<section class="slide closing"><h2>{escape(b.title)}</h2>'
                              + "".join(f"<p>{escape(x)}</p>" for x in b.bullets[:4]) + "</section>")
            else:
                bullets(b.title, b.bullets)
        elif isinstance(b, Heading):
            flush()
            last_title = b.text
            slides.append(f'<section class="slide section"><div class="panel"><h2>{escape(b.text)}</h2>'
                          f'<div class="bar"></div></div></section>')
        elif isinstance(b, Table):
            flush()
            table(b, last_title)
        elif isinstance(b, Chart):
            flush()
            add("chart", b.title or last_title,
                f'<figure class="chart">{chart_html(b, doc.style.theme, 262, 104, 11, title=False)}</figure>')
        elif isinstance(b, Paragraph) and b.text.strip():
            pending.append(b.text.strip())
        elif isinstance(b, ListBlock):
            pending.extend(b.items)
        elif isinstance(b, KeyValue):
            pending.extend(f"{p[0]}: {p[1]}" for p in b.pairs if len(p) >= 2)
        elif isinstance(b, Signature):
            pending.append(" — ".join(x for x in (b.name, b.designation) if x))
    flush()

    side = "left" if doc.rtl else "right"
    css = f"""
    @page {{ size: 297mm 167.06mm; margin: 0; }}
    html, body {{ margin: 0; padding: 0; }}
    body {{ font-family: {family}; color: {t['text']}; background: {t['bg']}; overflow-wrap: break-word; }}
    .slide {{ box-sizing: border-box; width: 297mm; height: 167.06mm; padding: 10mm 15.6mm 0; position: relative;
             overflow: hidden; background: {t['bg']}; break-after: page; }}
    .slide:last-child {{ break-after: auto; }}
    .slide h2 {{ font-size: 26pt; line-height: 1.2; margin: 0; color: {t['title']}; }}
    .slide .bar {{ width: 24.5mm; height: 1.35mm; background: {t['accent']}; margin: 4.5mm 0 7mm; }}
    .slide ul {{ margin: 0; padding-inline-start: 1.2em; font-size: 19pt; line-height: 1.3; }}
    .slide li {{ margin-bottom: .45em; }} .slide li::marker {{ color: {t['accent']}; }}
    .slide .foot {{ position: absolute; bottom: 5.5mm; {side}: 15.6mm; margin: 0; font-size: 8pt;
                   color: {t['muted']}; }}
    .slide.cover {{ padding: 0 15.6mm 0 30mm; }}
    .slide.cover .band {{ position: absolute; top: 0; bottom: 0; left: 0; width: 7.8mm; background: {t['accent']}; }}
    .slide.cover h1 {{ font-size: 38pt; line-height: 1.15; margin: 52mm 0 4mm; color: {t['title']}; }}
    .slide.cover .sub {{ font-size: 17pt; color: {t['muted']}; margin: 0; }}
    .slide.section .panel {{ position: absolute; left: 0; right: 0; top: 50mm; height: 51mm; box-sizing: border-box;
                            padding: 15mm 15.6mm 0; background: {t['panel']}; }}
    .slide.section .panel h2 {{ font-size: 31pt; }} .slide.section .panel .bar {{ margin: 4mm 0 0; }}
    .slide.quote .big {{ font-size: 42pt; font-weight: 700; color: {t['accent']}; text-align: center;
                        margin: 26mm 0 0; }}
    .slide.quote .line {{ font-size: 26pt; line-height: 1.3; text-align: center; margin: 20mm 12mm 0; }}
    .slide .cols {{ display: flex; }} .slide .cols > div {{ flex: 1; }}
    .slide .cols > div + div {{ border-inline-start: .5mm solid {t['rule']}; padding-inline-start: 7mm; }}
    .slide.closing {{ text-align: center; padding-top: 50mm; }} .slide.closing h2 {{ font-size: 35pt; }}
    .slide.closing p {{ font-size: 17pt; color: {t['muted']}; margin: 2.5mm 0 0; }}
    .slide table {{ width: 100%; border-collapse: collapse; font-size: 12pt; }}
    .slide th {{ background: {t['accent']}; color: #FFFFFF; text-align: start; padding: 1.8mm 2.6mm; }}
    .slide td {{ padding: 1.6mm 2.6mm; }} .slide tbody tr:nth-child(even) td {{ background: {t['panel']}; }}
    .slide tfoot td {{ font-weight: 700; border-top: .5mm solid {t['text']}; }}
    .slide .num {{ text-align: end; font-variant-numeric: tabular-nums; }}
    .slide figure.chart {{ margin: 0; }}
    """
    return "".join(slides), css + CHART_CSS


def render_html(doc: Document) -> str:
    f = fonts_for(doc.script)
    family = f"'{f['noto']}', 'Noto Sans', 'Noto Sans Devanagari', sans-serif"
    is_deck = any(isinstance(b, Slide) for b in doc.blocks)
    body, css = _deck(doc, family) if is_deck else _document(doc, family)
    direction = "rtl" if doc.rtl else "ltr"
    return (f"<!doctype html><html lang=\"{escape(doc.lang)}\" dir=\"{direction}\"><head><meta charset=\"utf-8\">"
            f"<title>{escape(doc.title)}</title><style>{css}</style></head><body>{body}</body></html>")


def render(doc: Document) -> bytes:
    return render_html(doc).encode("utf-8")
