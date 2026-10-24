"""The numbers review (apps/thinker/numbers.py): what it accepts from the model, and that it never holds a document
up."""
import asyncio

import pytest

from apps.thinker import numbers
from apps.thinker.llm import ThinkerError


def test_the_reviews_answer_is_checked_and_trimmed():
    issues = numbers._parse({"issues": [
        {"section": " s6 ", "problem": "Year 2 net profit:  text ₹81,360,  table ₹8,13,600"},
        {"section": "s1", "problem": "   "}, "not an issue"]})
    assert issues == [{"section": "s6", "problem": "Year 2 net profit: text ₹81,360, table ₹8,13,600"}]
    assert numbers._parse({"issues": []}) == []
    with pytest.raises(ValueError):
        numbers._parse({"problems": []})
    assert numbers.with_numbers("No figures here. The loan is ₹3,00,000. Repaid in 7 years!") == [
        "The loan is ₹3,00,000.", "Repaid in 7 years!"]


def test_a_review_that_fails_or_has_nothing_to_read_finds_nothing(monkeypatch):
    called = []

    async def down(*args, **kwargs):
        called.append(1)
        raise ThinkerError("provider_unavailable", "bedrock HTTP 503")
    monkeypatch.setattr(numbers, "chat_json", down)
    assert asyncio.run(numbers.review_numbers("key", "No numbers at all.")) == ([], {}) and called == []
    failed = asyncio.run(numbers.review_numbers("key", "The loan is ₹3,00,000."))
    assert failed == ([], {"error": "provider_unavailable"})
