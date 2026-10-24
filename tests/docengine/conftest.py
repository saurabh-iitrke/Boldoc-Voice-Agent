import pytest

from docengine.model import (
    Chart,
    ChartSeries,
    Column,
    Document,
    Heading,
    KeyValue,
    ListBlock,
    Paragraph,
    Run,
    Signature,
    Slide,
    Style,
    Table,
)


def _letter_en() -> Document:
    return Document(
        id="doc_en", title="Leave Application", doc_type="application",
        meta={"date": "21-10-2026", "to": "The HR Manager, Acme Pvt Ltd", "subject": "Leave from 25 to 27 October"},
        blocks=[
            Paragraph(runs=[Run(text="Dear Sir/Madam,")]),
            Paragraph(runs=[Run(text="I request leave from "), Run(text="25 to 27 October 2026", bold=True),
                            Run(text=" to attend my sister's wedding in Jaipur.")]),
            ListBlock(items=["Handover to Priya by 24 October", "Reachable on phone for emergencies"]),
            Signature(closing="Yours sincerely,", name="Saurabh Kumar", designation="Senior Engineer"),
        ],
    )


def _letter_hi() -> Document:
    return Document(
        id="doc_hi", title="छुट्टी के लिए आवेदन", lang="hi-IN", script="Deva", doc_type="application",
        meta={"date": "21-10-2026", "to": "मानव संसाधन प्रबंधक"},
        blocks=[
            Heading(level=1, text="आवेदन"),
            Paragraph(runs=[Run(text="आदरणीय महोदय, "),
                            Run(text="मैं 25 से 27 अक्टूबर तक छुट्टी चाहता हूँ।", bold=True)]),
            ListBlock(ordered=True, items=["काम प्रिया को सौंप दूँगा", "आपात स्थिति में फ़ोन पर उपलब्ध रहूँगा"]),
            Signature(closing="भवदीय,", name="सौरभ कुमार"),
        ],
    )


def _expenses() -> Document:
    return Document(
        id="doc_tbl", title="Trip Expenses", doc_type="table",
        blocks=[
            Heading(level=1, text="Jaipur trip"),
            Table(caption="Expenses", total_row=True,
                  columns=[Column(name="Item"), Column(name="Amount", type="currency"),
                           Column(name="Date", type="date")],
                  rows=[["Hotel", 12500, "25-10-2026"], ["Train", "₹2,340", "24-10-2026"],
                        ["Gift", "1.5 lakh", "26-10-2026"]]),
            KeyValue(pairs=[["Paid by", "Saurabh"], ["Mode", "UPI"]]),
        ],
    )


def _slides_ta() -> Document:
    return Document(
        id="doc_ta", title="விடுப்பு திட்டம்", lang="ta-IN", script="Taml", doc_type="slides",
        blocks=[Slide(title="நோக்கம்", bullets=["குடும்ப திருமணம்", "மூன்று நாட்கள்"], notes="சுருக்கமாக"),
                Slide(title="ஒப்படைப்பு", bullets=["பிரியா பொறுப்பு"])],
    )


def _urdu() -> Document:
    return Document(
        id="doc_ur", title="چھٹی کی درخواست", lang="ur-IN", script="Arab",
        blocks=[Paragraph(runs=[Run(text="جناب، مجھے تین دن کی چھٹی درکار ہے۔")])],
    )


def _pitch_deck() -> Document:
    """Every slide layout, a table that continues on a second slide, a chart and a list that splits."""
    costs = [[f"Item {i}", i * 10000] for i in range(1, 14)]
    return Document(
        id="doc_deck", title="Kumar Tea & Snacks: pitch", doc_type="slides", meta={"from": "Saurabh Kumar"},
        blocks=[
            Slide(title="The problem", notes="Open with the story",
                  bullets=["Tea stalls lose customers to cafes",
                           "Office workers want a clean place to sit for twenty minutes, with Wi-Fi, a menu they "
                           "trust and a price they can pay every day of the week",
                           "No seating at the stall"]),
            Slide(title="Our numbers", layout="quote", quote="Rs 2.4 lakh / month"),
            Slide(title="Before and after", layout="two_column", bullets=["Stall", "2 helpers"],
                  right=["20-seat cafe", "4 staff"]),
            Slide(title="Money", layout="section"),
            Table(caption="Project cost", total_row=True,
                  columns=[Column(name="Item"), Column(name="Amount", type="currency")], rows=costs),
            Chart(kind="column", title="Sales projection", categories=["Y1", "Y2", "Y3"], unit="currency",
                  series=[ChartSeries(name="Sales", values=[2880000, 3168000, 3484800])]),
            Slide(title="Long list", bullets=[f"Point {i}" for i in range(9)]),
            Slide(title="Thank you", layout="closing", bullets=["Saurabh Kumar", "Kumar Tea & Snacks, Jaipur"]),
        ],
    )


def _project_report() -> Document:
    """A long report: cover, contents, page numbers, numbered sections, tables with totals and charts."""
    return Document(
        id="doc_dpr", title="Project Report: Kumar Tea & Snacks Cafe", doc_type="report",
        meta={"from": "Saurabh Kumar", "date": "23-10-2026", "to": "Punjab National Bank, Jaipur"},
        style=Style(cover=True, toc=True, page_numbers=True),
        blocks=[
            Heading(level=1, text="Executive summary"),
            Paragraph(runs=[Run(text="Kumar Tea & Snacks seeks a MUDRA Kishore loan of "),
                            Run(text="₹8,00,000", bold=True),
                            Run(text=" to turn its tea stall into a 20-seat cafe near the Jaipur secretariat.")]),
            Heading(level=1, text="Project cost"),
            Heading(level=2, text="Capital items"),
            Table(caption="Capital cost", total_row=True,
                  columns=[Column(name="Item"), Column(name="Qty", type="number"),
                           Column(name="Amount", type="currency")],
                  rows=[["Counter and seating", 1, 350000], ["Kitchen equipment", 1, 280000],
                        ["Interiors", 1, 170000], ["Security deposit", 1, 100000]]),
            Heading(level=2, text="Means of finance"),
            KeyValue(pairs=[["Loan", "₹8,00,000"], ["Own contribution", "₹1,00,000"]]),
            Heading(level=1, text="Projections"),
            Chart(kind="line", title="Sales and profit", categories=["Y1", "Y2", "Y3"], unit="currency",
                  series=[ChartSeries(name="Sales", values=[2880000, 3168000, 3484800]),
                          ChartSeries(name="Net profit", values=[312000, 398000, 471000])]),
            Table(caption="Profit and loss", total_row=False,
                  columns=[Column(name="Year"), Column(name="Sales", type="currency"),
                           Column(name="Net profit", type="currency")],
                  rows=[["Y1", 2880000, 312000], ["Y2", 3168000, 398000], ["Y3", 3484800, 471000]]),
            Chart(kind="pie", title="Cost split", categories=["Counter", "Kitchen", "Interiors", "Deposit"],
                  unit="currency", series=[ChartSeries(name="Cost", values=[350000, 280000, 170000, 100000])]),
            Heading(level=1, text="Risks"),
            ListBlock(items=["A new cafe chain nearby", "Milk prices", "Staff turnover"]),
            Signature(closing="Submitted by,", name="Saurabh Kumar", designation="Proprietor"),
        ],
    )


def _deck_hi() -> Document:
    return Document(
        id="doc_deck_hi", title="चाय की दुकान: योजना", lang="hi-IN", script="Deva", doc_type="slides",
        style=Style(theme="plain"),
        blocks=[Slide(title="समस्या", bullets=["बैठने की जगह नहीं", "ग्राहक कैफ़े जाते हैं"], notes="कहानी से शुरू करें"),
                Slide(title="हमारी बिक्री", layout="quote", quote="₹2.4 लाख / महीना"),
                Chart(kind="bar", title="बिक्री का अनुमान", categories=["साल 1", "साल 2"], unit="currency",
                      series=[ChartSeries(name="बिक्री", values=[2880000, 3168000])]),
                Slide(title="धन्यवाद", layout="closing", bullets=["सौरभ कुमार"])],
    )


SAMPLES = {"letter_en": _letter_en, "letter_hi": _letter_hi, "expenses": _expenses,
           "slides_ta": _slides_ta, "urdu": _urdu, "pitch_deck": _pitch_deck, "project_report": _project_report,
           "deck_hi": _deck_hi}


@pytest.fixture(params=sorted(SAMPLES))
def sample_doc(request) -> Document:
    return SAMPLES[request.param]()


@pytest.fixture
def letter_en() -> Document:
    return _letter_en()


@pytest.fixture
def letter_hi() -> Document:
    return _letter_hi()


@pytest.fixture
def expenses() -> Document:
    return _expenses()


@pytest.fixture
def urdu() -> Document:
    return _urdu()


@pytest.fixture
def pitch_deck() -> Document:
    return _pitch_deck()


@pytest.fixture
def project_report() -> Document:
    return _project_report()


@pytest.fixture
def deck_hi() -> Document:
    return _deck_hi()
