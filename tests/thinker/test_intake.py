"""Intake: the writer's checklist for a requested document, and whether BolDoc may write it."""
import asyncio
import json

import httpx
import pytest

from apps.thinker.intake import Intake, _parse, prepare


def test_the_checklist_keeps_what_was_said_and_what_is_missing():
    it = _parse({"document": "bank loan application", "allowed": True, "facts": [
        {"label": "bank", "question": "Which bank?", "value": "SBI", "required": True},
        {"label": "loan purpose", "question": "What is the loan for?", "value": "", "required": True},
        {"label": "repayment plan", "question": "How will you repay?", "value": "", "required": False},
        {"label": "", "question": "noise"}]})
    assert isinstance(it, Intake) and it.allowed and it.refusal == ""
    assert [f.label for f in it.missing()] == ["loan purpose"]          # optional facts are never pressed for
    assert it.as_dict()["facts"][0] == {"label": "bank", "question": "Which bank?", "value": "SBI", "required": True}


def test_a_refusal_must_say_why_and_what_instead():
    it = _parse({"document": "medical certificate", "allowed": False,
                 "refusal": "It would pretend to come from a doctor; I can write a leave application instead."})
    assert not it.allowed and "leave application" in it.refusal
    with pytest.raises(ValueError):
        _parse({"document": "x", "allowed": False})
    # no document named: not grounded in anything the caller asked for (unclear words), so not a refusal
    assert _parse({"document": "", "allowed": False, "refusal": "police verification"}).allowed
    with pytest.raises(ValueError):
        _parse({"document": "x", "allowed": "yes"})


def test_the_callers_saved_details_count_as_said():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
            {"document": "leave application", "allowed": True, "facts": []})}}]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await prepare("k", "Caller: leave chahiye", known={"name": "Saurabh Kumar"}, client=client)
    (it, _), = [asyncio.run(go())]
    user = calls[0]["messages"][1]["content"]
    assert it.document == "leave application" and "- Name: Saurabh Kumar" in user and "Caller: leave chahiye" in user


def test_a_common_kind_of_document_asks_every_fact_of_its_form():
    """A document of a known kind asks every fact of its form, in its order, with what was said kept and nothing asked
    twice; others the request needs come after."""
    raw = {"document": "leave application", "kind": "leave_office", "allowed": True, "facts": [
        {"label": "recipient", "value": "HR", "required": True},
        {"label": "leave start date", "value": "27 October", "required": True},
        {"label": "end date", "value": "31 October", "required": True},
        {"label": "reason for leave", "value": "family function", "required": True},
        {"label": "caller's name", "value": "Saurabh", "required": True},
        {"label": "caller's name", "value": "Saurabh", "required": True},
        {"label": "contact during leave", "value": "", "required": False}]}
    it = _parse(raw)
    assert it.kind == "leave_office"
    assert [f.label for f in it.facts] == ["recipient", "from date", "to date", "reason", "your name",
                                           "your designation", "organisation or department", "contact during leave"]
    assert [f.value for f in it.facts][:5] == ["HR", "27 October", "31 October", "family function", "Saurabh"]
    assert [f.label for f in it.missing()] == ["your designation", "organisation or department"]
    other = _parse({**raw, "kind": "other"})
    assert other.kind == "" and "your designation" not in [f.label for f in other.facts]


def test_the_writer_knows_the_callers_earlier_documents_and_a_change_to_one_is_no_new_document():
    """"change the company name in my earlier resignation letter" got a new letter's checklist, asked one by one, and a
    new letter. Measured (Qwen3-235B, 9 cases x 3): changes to an earlier document seen as
    such 0/15 without the titles, 15/15 with; new ones stay new."""
    from apps.thinker.intake import written_block
    block = written_block(["Leave Application"], ["Resignation Letter", "Invoice"])
    assert "Already written on this call" in block and '"Leave Application"' in block
    assert "earlier calls" in block and '"Resignation Letter"; "Invoice"' in block
    assert "another document of the same kind" in block
    assert written_block(None, None) == "" and "earlier calls" not in written_block(["A"], [])
