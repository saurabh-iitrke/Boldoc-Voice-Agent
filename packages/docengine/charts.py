"""Charts, drawn natively by every format that can: PowerPoint and Word get real charts (the chart XML python-pptx
writes, so the numbers stay editable), Excel its own (xlsx_r.py), and HTML/PDF the shapes in an SVG with every
label as HTML text around it, so Devanagari and Tamil labels are shaped. Text formats give the data as a table.
"""
from __future__ import annotations

import math
from html import escape
from typing import List

from .deck import THEMES
from .i18n import XLSX_FORMATS, format_indian
from .model import Chart, Column, Table

# One meaning per colour, as on the website: BolDoc's orange first, then ink, then greys (web/src/styles/tokens.css)
PALETTES = {
    "boldoc": ["FF4D1A", "121212", "6B6B66", "C9C9C2", "C2380D", "1F7A4D"],
    "plain": ["1F3A5F", "4A6FA5", "8AA9D6", "C9D6EA", "5B5B5B", "A3A3A3"],
    "dark": ["FF4D1A", "F3F3F1", "A3A39C", "6B6B66", "FE8A66", "3FB27F"],
}


def as_table(c: Chart) -> Table:
    """The chart's numbers as a table (text formats, and the data beside an Excel chart)."""
    cols = [Column(name=c.title or "Category")] + [Column(name=s.name, type=c.unit if c.unit != "number" else "number")
                                                   for s in c.series]
    rows = [[cat] + [s.values[i] for s in c.series] for i, cat in enumerate(c.categories)]
    return Table(caption=c.title, columns=cols, rows=rows)


# ------------------------------------------------------------------------------------------------ native (OOXML)
def ooxml_type(c: Chart):
    from pptx.enum.chart import XL_CHART_TYPE
    return {"column": XL_CHART_TYPE.COLUMN_CLUSTERED, "bar": XL_CHART_TYPE.BAR_CLUSTERED,
            "line": XL_CHART_TYPE.LINE_MARKERS, "pie": XL_CHART_TYPE.PIE}[c.kind]


def ooxml_data(c: Chart):
    from pptx.chart.data import CategoryChartData
    data = CategoryChartData(number_format=XLSX_FORMATS["currency" if c.unit == "currency" else
                                                         "percent" if c.unit == "percent" else "number"])
    data.categories = c.categories
    for s in c.series:
        data.add_series(s.name, s.values)
    return data


def style_native(chart, c: Chart, theme: str, latin: str, cs: str) -> None:
    """Colours, legend and labels for a python-pptx chart object (PowerPoint)."""
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_LEGEND_POSITION
    from pptx.util import Pt
    palette = PALETTES.get(theme, PALETTES["boldoc"])
    chart.has_legend = len(c.series) > 1 or c.kind == "pie"
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
    if c.title:
        chart.has_title = True
        chart.chart_title.text_frame.text = c.title
    plot = chart.plots[0]
    if c.kind == "pie":
        for i, point in enumerate(plot.series[0].points):
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = RGBColor.from_string(palette[i % len(palette)])
        plot.has_data_labels = True
        plot.data_labels.show_percentage = True
        plot.data_labels.show_value = False
    else:
        for i, s in enumerate(plot.series):
            color = RGBColor.from_string(palette[i % len(palette)])
            if c.kind == "line":
                s.format.line.color.rgb = color
                s.format.line.width = Pt(2.25)
                s.smooth = False
            else:
                s.format.fill.solid()
                s.format.fill.fore_color.rgb = color
        if len(c.categories) <= 8:
            plot.has_data_labels = True
            plot.data_labels.font.size = Pt(10)
    chart.font.size = Pt(11)
    chart.font.name = latin


def chart_xml(c: Chart, workbook_rid: str) -> bytes:
    """A chartSpace part for Word (word/charts/chartN.xml): the same XML PowerPoint charts are written with, its numbers
    cached in the part, and its workbook embedded (relationship `workbook_rid`): Word's "Edit data", and Apple's
    Office importer, which previews files on iPhones and Macs, hangs on a chart without one."""
    from lxml import etree
    from pptx.chart.xmlwriter import ChartXmlWriter
    xml = ChartXmlWriter(ooxml_type(c), ooxml_data(c)).xml
    root = etree.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
    ext = etree.Element(f"{C}externalData",
                        {"{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id": workbook_rid})
    etree.SubElement(ext, f"{C}autoUpdate", val="0")
    later = [x for x in (root.find(f"{C}{t}") for t in ("printSettings", "userShapes", "extLst")) if x is not None]
    if later:  # schema order in c:chartSpace: chart, spPr, txPr, externalData, printSettings, userShapes, extLst
        later[0].addprevious(ext)
    else:
        root.append(ext)
    if c.title:
        _xml_title(root, c.title)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _xml_title(root, title: str) -> None:
    from lxml import etree
    C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
    A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    chart = root.find(f"{C}chart")
    if chart is None or chart.find(f"{C}title") is not None:
        return
    t = etree.Element(f"{C}title")
    tx = etree.SubElement(t, f"{C}tx")
    rich = etree.SubElement(tx, f"{C}rich")
    etree.SubElement(rich, f"{A}bodyPr")
    p = etree.SubElement(rich, f"{A}p")
    r = etree.SubElement(p, f"{A}r")
    etree.SubElement(r, f"{A}t").text = title
    etree.SubElement(t, f"{C}overlay", val="0")
    chart.insert(0, t)
    atd = chart.find(f"{C}autoTitleDeleted")
    if atd is not None:
        atd.set("val", "0")


# ------------------------------------------------------------------------------------------------ HTML/PDF WeasyPrint
# draws SVG text letter by letter (weasyprint/svg/text.py, v70), so Devanagari or Tamil inside an SVG comes out
# unshaped. Charts for HTML and PDF therefore draw only shapes in the SVG and set every label as HTML text, laid out by
# Pango/HarfBuzz like the rest of the page.
CHART_CSS = """
figure.chart figcaption { font-weight: 700; text-align: center; margin: 0 0 1.5mm; }
.cbox { position: relative; }
.cbox svg { position: absolute; overflow: visible; }
.cbox .cl { position: absolute; line-height: 1.3; white-space: nowrap; overflow: hidden; }
.cbox .lg { position: absolute; white-space: nowrap; }
.cbox .lg span { display: inline-block; margin-right: 5mm; }
.cbox .lg i { display: inline-block; width: 2.6mm; height: 2.6mm; margin-right: 1.2mm; vertical-align: -0.2mm; }
"""
PT_MM = 0.3528


def _ticks(lo: float, hi: float, n: int = 4) -> List[float]:
    """Round axis ticks from 0 (or below it, for a loss) to just above the largest value."""
    lo, hi = min(lo, 0.0), max(hi, 0.0)
    if hi == lo:
        hi = lo + 1.0
    raw = (hi - lo) / n
    exp = 10 ** math.floor(math.log10(raw))
    step = next(m * exp for m in (1, 2, 2.5, 5, 10) if m * exp >= raw - 1e-12)
    start = math.floor(lo / step + 1e-9) * step
    count = math.ceil((hi - start) / step - 1e-9)
    return [round(start + i * step, 10) for i in range(count + 1)]


def _axis_label(v: float, unit: str) -> str:
    """An axis value: rupees without paise, Indian grouping ("₹40,00,000")."""
    if unit == "percent":
        return f"{v:g}%"
    whole = float(v).is_integer()
    return format_indian(v, 0 if whole or unit == "currency" else 1, currency=unit == "currency")


def _span(text: str, left: float, top: float, width: float, align: str, color: str = "") -> str:
    style = f"left:{left:.2f}mm;top:{top:.2f}mm;width:{width:.2f}mm;text-align:{align}" + (
        f";color:{color}" if color else "")
    return f'<span class="cl" style="{style}">{escape(text)}</span>'


def chart_html(c: Chart, theme: str = "boldoc", width: float = 174.0, height: float = 88.0, font_pt: float = 8.5,
               title: bool = True) -> str:
    """The chart for HTML and PDF, sized in mm: shapes in an SVG, every label HTML text placed around it."""
    palette = ["#" + h for h in PALETTES.get(theme, PALETTES["boldoc"])]
    ink = {k: "#" + v for k, v in THEMES.get(theme, THEMES["boldoc"]).items()}
    line, char = font_pt * PT_MM * 1.3, font_pt * PT_MM * 0.56
    parts: List[str] = []
    if c.kind == "pie":
        _pie_html(parts, c, palette, ink, width, height, line)
    else:
        _axes_html(parts, c, palette, ink, width, height, line, char)
    cap = f"<figcaption>{escape(c.title)}</figcaption>" if title and c.title else ""
    return (f'{cap}<div class="cbox" dir="ltr" style="width:{width:.1f}mm;height:{height:.1f}mm;'
            f'font-size:{font_pt}pt;color:{ink["text"]}">' + "".join(parts) + "</div>")


def _axes_html(parts: List[str], c: Chart, palette: List[str], ink: dict, width: float, height: float, line: float,
               char: float) -> None:
    values = [v for s in c.series for v in s.values]
    ticks = _ticks(min([*values, 0.0]), max([*values, 0.0]))
    lo, hi = ticks[0], ticks[-1]
    horizontal = c.kind == "bar"
    tick_text = [_axis_label(t, c.unit) for t in ticks]
    legend_h = line * 1.7 if len(c.series) > 1 else 0.0
    xlab_h = line + 1.5
    if horizontal:
        left_w = min(width * 0.38, max(len(x) for x in c.categories) * char + 2.0) if c.categories else 10.0
    else:
        left_w = max(len(x) for x in tick_text) * char + 2.0
    px, py = left_w + 1.5, line / 2 + 0.5
    pw, ph = width - px - 2.0, height - py - xlab_h - legend_h
    n, k = len(c.categories), max(1, len(c.series))
    band = (ph if horizontal else pw) / max(1, n)
    shapes: List[str] = []

    def val(v: float) -> float:  # position along the value axis, in mm from the axis start
        return (v - lo) / (hi - lo) * (pw if horizontal else ph)

    for t, label in zip(ticks, tick_text):  # gridlines and value labels
        d = val(t)
        if horizontal:
            shapes.append(f'<line x1="{d:.2f}" y1="0" x2="{d:.2f}" y2="{ph:.2f}" stroke="{ink["rule"]}" '
                          f'stroke-width="0.2"/>')
            parts.append(_span(label, px + d - 15, py + ph + 1.0, 30, "center", ink["muted"]))
        else:
            y = ph - d
            shapes.append(f'<line x1="0" y1="{y:.2f}" x2="{pw:.2f}" y2="{y:.2f}" stroke="{ink["rule"]}" '
                          f'stroke-width="0.2"/>')
            parts.append(_span(label, 0, py + y - line / 2, left_w, "right", ink["muted"]))
    zero = val(0.0)
    for i, cat in enumerate(c.categories):  # category labels
        if horizontal:
            parts.append(_span(cat, 0, py + band * (i + 0.5) - line / 2, left_w, "right"))
        else:
            parts.append(_span(cat, px + band * i, py + ph + 1.0, band, "center"))
    if c.kind == "line":
        for si, s in enumerate(c.series):
            color = palette[si % len(palette)]
            pts = [(band * (i + 0.5), ph - val(v)) for i, v in enumerate(s.values)]
            shapes.append(f'<polyline fill="none" stroke="{color}" stroke-width="0.7" stroke-linejoin="round" '
                          f'points="' + " ".join(f"{x:.2f},{y:.2f}" for x, y in pts) + '"/>')
            shapes += [f'<circle cx="{x:.2f}" cy="{y:.2f}" r="0.9" fill="{color}"/>' for x, y in pts]
    else:
        inner = band * 0.66
        bar = inner / k
        for i in range(n):
            for si, s in enumerate(c.series):
                v = s.values[i] if i < len(s.values) else 0.0
                a, b = sorted((zero, val(v)))
                off = band * i + (band - inner) / 2 + bar * si
                color = palette[si % len(palette)]
                if horizontal:
                    shapes.append(f'<rect x="{a:.2f}" y="{off:.2f}" width="{b - a:.2f}" height="{bar * 0.92:.2f}" '
                                  f'fill="{color}"/>')
                else:
                    shapes.append(f'<rect x="{off:.2f}" y="{ph - b:.2f}" width="{bar * 0.92:.2f}" '
                                  f'height="{b - a:.2f}" fill="{color}"/>')
    axis = (f'<line x1="{zero:.2f}" y1="0" x2="{zero:.2f}" y2="{ph:.2f}"' if horizontal else
            f'<line x1="0" y1="{ph - zero:.2f}" x2="{pw:.2f}" y2="{ph - zero:.2f}"')
    shapes.append(f'{axis} stroke="{ink["muted"]}" stroke-width="0.3"/>')
    parts.insert(0, f'<svg width="{pw:.2f}mm" height="{ph:.2f}mm" viewBox="0 0 {pw:.2f} {ph:.2f}" '
                    f'style="left:{px:.2f}mm;top:{py:.2f}mm" role="img">' + "".join(shapes) + "</svg>")
    if len(c.series) > 1:
        items = "".join(f'<span><i style="background:{palette[i % len(palette)]}"></i>{escape(s.name)}</span>'
                        for i, s in enumerate(c.series))
        parts.append(f'<div class="lg" style="left:{px:.2f}mm;top:{py + ph + xlab_h + 0.5:.2f}mm">{items}</div>')


def _pie_html(parts: List[str], c: Chart, palette: List[str], ink: dict, width: float, height: float,
              line: float) -> None:
    values = [max(0.0, v) for v in (c.series[0].values if c.series else [])]
    total = sum(values) or 1.0
    d = min(height - 2.0, width * 0.45)
    r = cx = cy = d / 2
    shapes, angle = [], -math.pi / 2
    for i, v in enumerate(values):
        sweep = 2 * math.pi * v / total
        color = palette[i % len(palette)]
        if v >= total:
            shapes.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="{color}"/>')
        elif v > 0:
            x1, y1 = cx + r * math.cos(angle), cy + r * math.sin(angle)
            x2, y2 = cx + r * math.cos(angle + sweep), cy + r * math.sin(angle + sweep)
            shapes.append(f'<path d="M{cx:.2f},{cy:.2f} L{x1:.2f},{y1:.2f} A{r:.2f},{r:.2f} 0 '
                          f'{1 if sweep > math.pi else 0} 1 {x2:.2f},{y2:.2f} Z" fill="{color}" stroke="{ink["bg"]}" '
                          f'stroke-width="0.4"/>')
        angle += sweep
    parts.append(f'<svg width="{d:.2f}mm" height="{d:.2f}mm" viewBox="0 0 {d:.2f} {d:.2f}" '
                 f'style="left:2mm;top:1mm" role="img">' + "".join(shapes) + "</svg>")
    rows = "".join(f'<div><i style="display:inline-block;width:2.6mm;height:2.6mm;margin-right:1.6mm;'
                   f'background:{palette[i % len(palette)]}"></i>{escape(cat)} ({v / total:.0%})</div>'
                   for i, (cat, v) in enumerate(zip(c.categories, values)))
    parts.append(f'<div class="lg" style="left:{d + 9:.2f}mm;top:{max(1.0, d / 2 - line * len(values) / 2):.2f}mm;'
                 f'line-height:1.7">{rows}</div>')
