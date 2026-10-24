import pytest

from apps.talker.logic.entities import (
    indian_mobile,
    read_back_mobile,
    spell_for_readback,
    spoken_amount,
    spoken_digits,
    spoken_email,
)


@pytest.mark.parametrize("utterance,expected", [
    ("my number is nine eight one two three four five six seven eight", "+919812345678"),
    ("98123 45678", "+919812345678"),
    ("double nine eight one two triple three four five", "+919981233345"),
    ("nau aath ek do teen chaar paanch chhe saat aath", "+919812345678"),
    ("plus nine one nine eight one two three four five six seven eight", "+919812345678"),
    ("zero nine eight one two three four five six seven eight", "+919812345678"),
    ("९८१२३४५६७८", "+919812345678"),
    ("mera number hai 98123-45678 ji", "+919812345678"),
])
def test_indian_mobile(utterance, expected):
    assert indian_mobile(utterance) == expected


@pytest.mark.parametrize("utterance", [
    "call me at 1800 123 4567",      # toll-free, not a mobile
    "five four three two one",       # too short
    "I need to go for a walk",       # ambiguous words outside a digit run are not digits
])
def test_not_a_mobile(utterance):
    assert indian_mobile(utterance) is None


def test_ambiguous_words_only_count_inside_digit_runs():
    assert spoken_digits("I want to go") == ""
    assert spoken_digits("nine eight two four") == "9824"   # 'two'/'four' inside a run


def test_read_back_grouping():
    assert read_back_mobile("+919812345678") == "98123 45678"


@pytest.mark.parametrize("utterance,expected", [
    ("saurabh dot k at the rate gmail dot com", "saurabh.k@gmail.com"),
    ("priya underscore 99 at gmail dot com", "priya_99@gmail.com"),
    ("my email is ravi hyphen kumar at the rate company dot co dot in", "ravi-kumar@company.co.in"),
    ("it's a.b@x.org", "a.b@x.org"),
])
def test_spoken_email(utterance, expected):
    assert spoken_email(utterance) == expected


def test_not_an_email():
    assert spoken_email("meet me at the station") is None


def test_spell_for_readback():
    assert spell_for_readback("a.k@") == ["A for Apple", "dot", "K for Kolkata", "at the rate"]


@pytest.mark.parametrize("utterance,expected", [
    ("dhai lakh", 250000), ("saade teen lakh", 350000), ("2.5 crore", 25_000_000), ("15 hazar", 15_000),
    ("paune do lakh", 175000), ("sawa ek lakh", 125000), ("dedh crore", 15_000_000), ("bas yahi", None),
])
def test_spoken_amount(utterance, expected):
    assert spoken_amount(utterance) == expected


@pytest.mark.parametrize("utterance,expected", [
    ("ravi at gmail dot com", "ravi@gmail.com"),
    ("s a u r a b h at gmail dot com", "saurabh@gmail.com"),
    ("send it to anita dot sharma 12 at the rate yahoo dot co dot in please", "anita.sharma12@yahoo.co.in"),
])
def test_spoken_email_more(utterance, expected):
    assert spoken_email(utterance) == expected
