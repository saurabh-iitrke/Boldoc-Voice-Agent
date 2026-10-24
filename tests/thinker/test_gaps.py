"""Facts a draft still lacks: which ones, the question for each, what the caller's reply means, and filling it in."""
import asyncio
import json

import httpx
import pytest

from apps.thinker.gaps import Reading, fill, interpret, label_of, open_gaps, question_for, removal, remove_gap
from docengine.model import Document, KeyValue, Paragraph, Run, Signature


def leave(**kw):
    return Document(id="d", title="Leave Application", meta={"to": "[HR Manager's Name]"}, blocks=[
        Paragraph(runs=[Run(text="Employee ID: [Your employee ID]. Please sign below: [Signature]")]),
        Signature(closing="Yours sincerely,", name="[Your Name]")],
        assumptions=["Fill in [Your employee ID]", "Fill in [Old placeholder]"], **kw)


def test_each_gap_once_in_reading_order_with_a_plain_question():
    doc = leave()
    # nothing done on paper (signatures), nothing only the assumptions list mentions
    assert open_gaps(doc) == ["[HR Manager's Name]", "[Your employee ID]", "[Your Name]"]
    assert [question_for(g) for g in open_gaps(doc)] == [
        "What is the HR manager's name?", "What is your employee ID?", "What is your name?"]
    assert label_of("[Your Employee ID]") == "your employee ID"
    assert question_for("[आपका पता]") == "What should the document say for आपका पता?"
    assert removal("[Your employee ID]").startswith("The caller doesn't have your employee ID")


def test_placeholders_joined_by_commas_are_one_fact():
    doc = Document(id="d", title="Letter", meta={"from": "Saurabh Kumar, [Your Address], [City], [PIN Code]"},
                   blocks=[Paragraph(runs=[Run(text="Hello.")])])
    run = "[Your Address], [City], [PIN Code]"
    assert open_gaps(doc) == [run]
    assert question_for(run) == "What is your address, city and PIN code?"
    assert label_of("[Your Address], [City]") == "your address and city"
    assert fill(doc, run, "Gomti Nagar, Lucknow 226010").meta["from"] == "Saurabh Kumar, Gomti Nagar, Lucknow 226010"


def test_a_fact_the_caller_doesnt_have_is_taken_out_by_code_where_it_can_be():
    doc = Document(id="d", title="Letter",
                   meta={"to": "The Branch Manager, SBI, [Branch Name]", "ref": "[Branch Name]"},
                   blocks=[Paragraph(runs=[Run(text="Please close my account.")]),
                           KeyValue(pairs=[["Account Number", "3124567890"], ["Branch", "[Branch Name]"]]),
                           Signature(closing="Yours faithfully,", name="Saurabh Kumar", designation="[Branch Name]")],
                   assumptions=["Fill in [Branch Name]"])
    out, in_text = remove_gap(doc, "[Branch Name]")
    assert not in_text and out.meta == {"to": "The Branch Manager, SBI"}          # an emptied field goes
    assert out.blocks[1].pairs == [["Account Number", "3124567890"]] and out.blocks[2].designation is None
    assert out.assumptions == [] and "[Branch Name]" not in out.all_text()
    # in running text only the editor can rewrite the sentence: say so
    doc2 = doc.model_copy(update={"blocks": [Paragraph(runs=[Run(text="My branch is [Branch Name].")])]})
    assert remove_gap(doc2, "[Branch Name]")[1] is True
    # a details section left with nothing goes
    doc3 = doc.model_copy(update={"blocks": [KeyValue(pairs=[["Branch", "[Branch Name]"]])]})
    assert remove_gap(doc3, "[Branch Name]")[0].blocks == []


def test_an_answer_goes_in_everywhere_and_its_note_goes():
    doc = fill(leave(), "[Your employee ID]", 'E-4521 "B"')
    assert 'Employee ID: E-4521 "B".' in doc.all_text()           # quotes survive (JSON-escaped on the way)
    assert doc.assumptions == ["Fill in [Old placeholder]"]
    assert open_gaps(doc) == ["[HR Manager's Name]", "[Your Name]"]
    hindi = Document(id="h", title="अवकाश", lang="hi-IN", meta={}, blocks=[
        Paragraph(runs=[Run(text="दिनांक [Start Date] से")])])
    assert "25 अक्टूबर 2026" in fill(hindi, "[Start Date]", "25 October 2026").all_text()


def _ask(content, reply="mera employee ID E 4521 hai"):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": content[len(calls) - 1]}}]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await interpret("k", "[Your employee ID]", "What is your employee ID?", reply, "en-IN",
                                   client=client)
    return asyncio.run(go()), calls


def test_the_reply_is_read_as_an_answer_a_no_or_something_else():
    (said, _), calls = _ask(['{"kind": "answer", "value": "E-4521", "change": ""}'])
    assert said == Reading("answer", "E-4521", "")
    user = calls[0]["messages"][1]["content"]
    assert "[Your employee ID]" in user and "mera employee ID E 4521 hai" in user and "English" in user
    assert _ask(['{"kind": "declined", "value": "x"}'])[0][0] == Reading("declined", "", "")
    assert _ask(['{"kind": "other"}'])[0][0] == Reading("other", "", "")
    # another change asked in the same breath comes along, in the caller's words
    said = _ask(['{"kind": "answer", "value": "E-4521", "change": "make the dates 26 to 27"}'])[0][0]
    assert said.change == "make the dates 26 to 27"
    # an "answer" with no value, or a placeholder for a value, is sent back once to be fixed
    (said, _), calls = _ask(['{"kind": "answer", "value": "[Your employee ID]"}',
                             '{"kind": "answer", "value": "E-4521"}'])
    assert said.value == "E-4521" and len(calls) == 2


def test_a_reply_that_cant_be_read_is_an_error_not_a_guess():
    from apps.thinker.llm import ThinkerError
    with pytest.raises(ThinkerError):
        _ask(['{"kind": "maybe"}', '{"kind": "perhaps"}'])
