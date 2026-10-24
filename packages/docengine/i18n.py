"""Indian locale helpers: lakh/crore digit grouping, ₹ formatting, number parsing, fonts per script."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Dict, List, Optional, Union

Number = Union[int, float]

# Office font names resolve on Windows/Mac Office (Word substitutes per script if missing);
# Noto families are what we embed in PDFs (Ubuntu: fonts-noto-core / fonts-noto-extra).
SCRIPT_FONTS: Dict[str, Dict[str, str]] = {
    "Latn": {"office": "Calibri", "noto": "Noto Sans"},
    "Deva": {"office": "Nirmala UI", "noto": "Noto Sans Devanagari"},
    "Beng": {"office": "Nirmala UI", "noto": "Noto Sans Bengali"},
    "Guru": {"office": "Nirmala UI", "noto": "Noto Sans Gurmukhi"},
    "Gujr": {"office": "Nirmala UI", "noto": "Noto Sans Gujarati"},
    "Orya": {"office": "Nirmala UI", "noto": "Noto Sans Oriya"},
    "Taml": {"office": "Nirmala UI", "noto": "Noto Sans Tamil"},
    "Telu": {"office": "Nirmala UI", "noto": "Noto Sans Telugu"},
    "Knda": {"office": "Nirmala UI", "noto": "Noto Sans Kannada"},
    "Mlym": {"office": "Nirmala UI", "noto": "Noto Sans Malayalam"},
    "Arab": {"office": "Urdu Typesetting", "noto": "Noto Nastaliq Urdu"},
}

SCRIPT_LANG = {"Deva": "hi-IN", "Beng": "bn-IN", "Guru": "pa-IN", "Gujr": "gu-IN", "Orya": "or-IN",
               "Taml": "ta-IN", "Telu": "te-IN", "Knda": "kn-IN", "Mlym": "ml-IN", "Arab": "ur-IN"}


def fonts_for(script: str) -> Dict[str, str]:
    return SCRIPT_FONTS.get(script, SCRIPT_FONTS["Latn"])


def group_indian(integer_digits: str) -> str:
    """'1234567' -> '12,34,567' (last 3 digits, then groups of 2)."""
    if len(integer_digits) <= 3:
        return integer_digits
    head, tail = integer_digits[:-3], integer_digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def format_indian(value: Number, decimals: int = 0, currency: bool = False) -> str:
    q = Decimal(str(value)).quantize(Decimal(1) if decimals == 0 else Decimal("1." + "0" * decimals),
                                     rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    text = f"{abs(q):f}"
    int_part, _, frac = text.partition(".")
    out = group_indian(int_part) + (f".{frac}" if decimals else "")
    return f"{sign}{'₹' if currency else ''}{out}"


_NUM_RE = re.compile(r"^[\s₹$]*(-)?[\s₹$]*([\d,]+(?:\.\d+)?)\s*(%|lakh|lakhs|lac|crore|crores|cr|k)?\s*$", re.I)
_MULT = {"lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "crore": 10_000_000, "crores": 10_000_000,
         "cr": 10_000_000, "k": 1_000, "%": 1}


def parse_number(value) -> Optional[Number]:
    """Best-effort parse of spoken/written amounts: '₹12,34,567', '2.5 lakh', '15k', '12%'."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    m = _NUM_RE.match(value)
    if not m:
        return None
    try:
        num = Decimal(m.group(2).replace(",", ""))
    except InvalidOperation:
        return None
    unit = (m.group(3) or "").lower()
    num *= _MULT.get(unit, 1)
    if m.group(1):
        num = -num
    return int(num) if num == num.to_integral_value() else float(num)


def format_cell(value, col_type: str, whole: bool = False) -> str:
    """A table cell as the reader sees it. `whole`: a currency column of whole amounts shows whole rupees
    (₹3,00,000, as reports write them) instead of ₹3,00,000.00 (see whole_rupees)."""
    if value is None:
        return ""
    num = parse_number(value) if col_type in ("number", "currency", "percent") else None
    if num is None:
        return str(value)
    if col_type == "currency":
        return format_indian(num, 0 if whole and float(num).is_integer() else 2, currency=True)
    if col_type == "percent":
        return f"{num:g}%"
    return format_indian(num, 0 if float(num).is_integer() else 2)


def whole_rupees(table, doc_type: str = "report") -> List[bool]:
    """Per column: whether its amounts show as whole rupees. A currency column whose amounts (and so its total) are
    all whole does, except in an invoice, which keeps its paise (₹1,18,000.00) as Indian invoices are written."""
    out = []
    for i, col in enumerate(table.columns):
        nums = [parse_number(r[i]) for r in table.rows if i < len(r) and r[i] is not None]
        out.append(col.type == "currency" and doc_type != "invoice"
                   and all(n is None or float(n).is_integer() for n in nums))
    return out


# Excel custom formats with Indian grouping (conditions: crore, lakh, default).
XLSX_FORMATS = {
    "currency": '[>=10000000]"₹"#\\,##\\,##\\,##0.00;[>=100000]"₹"#\\,##\\,##0.00;"₹"#,##0.00',
    "currency_whole": '[>=10000000]"₹"#\\,##\\,##\\,##0;[>=100000]"₹"#\\,##\\,##0;"₹"#,##0',
    "number": "[>=10000000]#\\,##\\,##\\,##0;[>=100000]#\\,##\\,##0;#,##0",
    "percent": '0.##"%"',
    "date": "DD-MM-YYYY",
}


META_LABELS: Dict[str, Dict[str, str]] = {
    "en": {"date": "Date", "from": "From", "to": "To", "subject": "Subject", "ref": "Ref"},
    "hi": {"date": "दिनांक", "from": "प्रेषक", "to": "सेवा में", "subject": "विषय", "ref": "संदर्भ"},
    "mr": {"date": "दिनांक", "from": "प्रेषक", "to": "प्रति", "subject": "विषय", "ref": "संदर्भ"},
    "bn": {"date": "তারিখ", "from": "প্রেরক", "to": "প্রাপক", "subject": "বিষয়", "ref": "সূত্র"},
    "gu": {"date": "તારીખ", "from": "મોકલનાર", "to": "પ્રતિ", "subject": "વિષય", "ref": "સંદર્ભ"},
    "pa": {"date": "ਮਿਤੀ", "from": "ਭੇਜਣ ਵਾਲਾ", "to": "ਵੱਲ", "subject": "ਵਿਸ਼ਾ", "ref": "ਹਵਾਲਾ"},
    "ta": {"date": "தேதி", "from": "அனுப்புநர்", "to": "பெறுநர்", "subject": "பொருள்", "ref": "குறிப்பு"},
    "te": {"date": "తేదీ", "from": "పంపినవారు", "to": "స్వీకర్త", "subject": "విషయం", "ref": "సూచన"},
    "kn": {"date": "ದಿನಾಂಕ", "from": "ಕಳುಹಿಸುವವರು", "to": "ಸ್ವೀಕರಿಸುವವರು", "subject": "ವಿಷಯ", "ref": "ಉಲ್ಲೇಖ"},
    "ml": {"date": "തീയതി", "from": "അയയ്ക്കുന്നയാൾ", "to": "സ്വീകർത്താവ്", "subject": "വിഷയം", "ref": "സൂചന"},
    "od": {"date": "ତାରିଖ", "from": "ପ୍ରେରକ", "to": "ପ୍ରାପକ", "subject": "ବିଷୟ", "ref": "ସନ୍ଦର୍ଭ"},
}

DOC_WORDS: Dict[str, Dict[str, str]] = {  # words added for the writer: a total row, contents, "Page 2 of 9",
    # a researched document's Sources list and the date each page was read
    "en": {"total": "Total", "contents": "Contents", "page": "Page", "of": "of",
           "sources": "Sources", "read": "read {date}"},
    "hi": {"total": "कुल", "contents": "विषय-सूची", "page": "पृष्ठ", "of": "/",
           "sources": "स्रोत", "read": "{date} को देखा गया"},
    "mr": {"total": "एकूण", "contents": "अनुक्रमणिका", "page": "पृष्ठ", "of": "/",
           "sources": "स्रोत", "read": "{date} रोजी पाहिले"},
    "bn": {"total": "মোট", "contents": "সূচিপত্র", "page": "পৃষ্ঠা", "of": "/",
           "sources": "সূত্র", "read": "{date} তারিখে দেখা হয়েছে"},
    "gu": {"total": "કુલ", "contents": "અનુક્રમણિકા", "page": "પૃષ્ઠ", "of": "/",
           "sources": "સ્રોત", "read": "{date}ના રોજ જોયું"},
    "pa": {"total": "ਕੁੱਲ", "contents": "ਤਤਕਰਾ", "page": "ਪੰਨਾ", "of": "/",
           "sources": "ਸਰੋਤ", "read": "{date} ਨੂੰ ਦੇਖਿਆ ਗਿਆ"},
    "ta": {"total": "மொத்தம்", "contents": "பொருளடக்கம்", "page": "பக்கம்", "of": "/",
           "sources": "ஆதாரங்கள்", "read": "{date} அன்று பார்க்கப்பட்டது"},
    "te": {"total": "మొత్తం", "contents": "విషయ సూచిక", "page": "పేజీ", "of": "/",
           "sources": "మూలాలు", "read": "{date}న చూడబడింది"},
    "kn": {"total": "ಒಟ್ಟು", "contents": "ಪರಿವಿಡಿ", "page": "ಪುಟ", "of": "/",
           "sources": "ಮೂಲಗಳು", "read": "{date}ರಂದು ನೋಡಲಾಗಿದೆ"},
    "ml": {"total": "ആകെ", "contents": "ഉള്ളടക്കം", "page": "പേജ്", "of": "/",
           "sources": "ഉറവിടങ്ങൾ", "read": "{date}-ന് കണ്ടത്"},
    "od": {"total": "ମୋଟ", "contents": "ସୂଚୀପତ୍ର", "page": "ପୃଷ୍ଠା", "of": "/",
           "sources": "ଉତ୍ସ", "read": "{date}ରେ ଦେଖାଯାଇଛି"},
}
for _lang, _words in DOC_WORDS.items():
    META_LABELS[_lang].update(_words)


def meta_label(key: str, lang: str) -> str:
    """'subject', 'hi-IN' -> 'विषय'. Falls back to English for languages not yet translated."""
    labels = META_LABELS.get(lang.split("-")[0].lower(), META_LABELS["en"])
    return labels.get(key, META_LABELS["en"].get(key, key.capitalize()))


_EN_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
              "November", "December")
MONTHS: Dict[str, tuple] = {
    "hi": ("जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"),
}
_MONTH_ALT = "|".join("Sep(?:t|tember)?" if m == "September" else f"{m[:3]}(?:{m[3:]})?" for m in _EN_MONTHS)
_DATE_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MONTH_ALT + r")\b\.?(,?\s+\d{4})?", re.I)


def localize_dates(text: str, lang: str) -> str:
    """'25 October 2026' -> '25 अक्टूबर 2026' for languages with a month table; unchanged otherwise."""
    months = MONTHS.get(lang.split("-")[0].lower())
    if not months or not text:
        return text

    def sub(m: re.Match[str]) -> str:
        idx = next(i for i, en in enumerate(_EN_MONTHS) if en.lower().startswith(m.group(2)[:3].lower()))
        year = (m.group(3) or "").replace(",", "")
        return f"{int(m.group(1))} {months[idx]}{year}"
    return _DATE_RE.sub(sub, text)
