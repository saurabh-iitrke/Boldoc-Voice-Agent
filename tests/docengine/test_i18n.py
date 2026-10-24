import pytest

from docengine.i18n import format_cell, format_indian, group_indian, parse_number
from docengine.model import detect_script


@pytest.mark.parametrize("digits,expected", [
    ("999", "999"), ("1000", "1,000"), ("100000", "1,00,000"), ("1234567", "12,34,567"),
    ("10000000", "1,00,00,000"),
])
def test_group_indian(digits, expected):
    assert group_indian(digits) == expected


def test_format_indian_currency_and_rounding():
    assert format_indian(1234567.5, 2, currency=True) == "₹12,34,567.50"
    assert format_indian(-250000, 0) == "-2,50,000"
    assert format_indian(0.005, 2) == "0.01"  # half-up, not banker's rounding


@pytest.mark.parametrize("raw,expected", [
    ("₹12,34,567", 1234567), ("2.5 lakh", 250000), ("1.2 crore", 12000000), ("15k", 15000),
    ("12%", 12), (" 3,400.50 ", 3400.5), (42, 42), ("abc", None), ("", None), (None, None),
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


def test_format_cell_types():
    assert format_cell("1.5 lakh", "currency") == "₹1,50,000.00"
    assert format_cell(1234567, "number") == "12,34,567"
    assert format_cell("not a number", "currency") == "not a number"
    assert format_cell(None, "text") == ""


@pytest.mark.parametrize("text,script", [
    ("छुट्टी के लिए आवेदन", "Deva"), ("விடுப்பு", "Taml"), ("چھٹی", "Arab"), ("ছুটি", "Beng"),
    ("Leave application", "Latn"), ("Leave / छुट्टी", "Deva"),
])
def test_detect_script(text, script):
    assert detect_script(text) == script
