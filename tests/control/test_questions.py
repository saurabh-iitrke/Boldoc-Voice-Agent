"""Facts a draft still lacks are asked on the call, never assumed: one question at a time, the caller's reply
interpreted (an answer goes in, a "no" takes the field out), and the document finished with them."""
import re

import pytest

from docengine.model import Document, Paragraph, Run, Signature
from tests.control.helpers import H, create_cmd, open_call, until, wait_for

pytest.importorskip("asyncpg")


def draft_with(*gaps):
    """The drafter's output when the conversation lacked these facts: placeholders, in reading order."""
    async def fake(api_key, transcript, intent, doc_id, formats, doc_lang="en-IN", **k):
        return Document(id=doc_id, title="Leave Application", doc_type="application", meta={"subject": "Leave"},
                        blocks=[Paragraph(runs=[Run(text=f"Employee ID: {gaps[0]}. I request leave from 25 to 27 "
                                                         "October.")]),
                                Signature(closing="Yours sincerely,",
                                          name=gaps[1] if len(gaps) > 1 else "Saurabh Kumar")]), {"attempts": 1}
    return fake


def recording_edit(seen):
    """The editor, faked: takes out a field it is told to remove, records every request it gets."""
    async def fake(api_key, base, instructions, transcript, doc_lang, formats, intent=""):
        seen.append(list(instructions))
        data = base.model_dump_json()
        for ins in instructions:
            if m := re.search(r"remove (\[[^\]]+\])", ins):
                data = data.replace(m.group(1), "")
        return Document.model_validate_json(data), [f"{len(instructions)} change(s)"], {"tier": "T1"}
    return fake


async def reading(api_key, placeholder, question, reply, doc_lang, client=None):
    """gaps.interpret, faked: "nahi pata" declines, a reply with an ID or a capitalised name answers, and "aur ..."
    after it is another change."""
    from apps.thinker.gaps import Reading
    reply, _, change = reply.partition(", aur ")
    if "nahi pata" in reply:
        return Reading("declined", "", change), {}
    m = re.search(r"\b([A-Z][\w-]*\d[\w-]*|[A-Z][a-z]+(?:,? [A-Z][a-z]+)+)\b", reply)
    return (Reading("answer", m.group(1), change) if m else Reading("other", "", change)), {}


@pytest.fixture
def gaps(monkeypatch):
    from apps.control import worker
    seen = []
    monkeypatch.setattr(worker, "interpret", reading)

    def use(*placeholders):
        monkeypatch.setattr(worker, "draft", draft_with(*placeholders))
        monkeypatch.setattr(worker, "edit", recording_edit(seen))
        return seen
    return use


def reply(c, sid, epoch, q, words):
    """What the talker does with the caller's next words after they heard the question."""
    return c.post("/internal/commands", json={"cmd": "answer", "session_id": sid, "epoch": epoch,
                                              "turn_id": f"reply-{q['payload']['question_id']}",
                                              "question_id": q["payload"]["question_id"], "value": words},
                  headers=H).json()


def text_of(doc_id, version=1):
    from apps.control import service
    return service.S.orch.s.versions[(doc_id, version)].all_text()


def start(c, sid):
    epoch = open_call(c, sid).json()["epoch"]
    created = c.post("/internal/commands", json=create_cmd(sid, epoch), headers=H).json()
    return epoch, created["doc_id"]


def test_a_missing_fact_is_asked_and_the_answer_goes_in(app_factory, gaps):
    seen = gaps("[Your employee ID]")
    with app_factory() as c:
        epoch, doc_id = start(c, "q-1")
        q, after, snap = wait_for(c, "q-1", 0, "task.question")
        assert q["payload"]["text"] == "What is your employee ID?"
        assert snap["question"]["text"] == "What is your employee ID?"        # the talker's [STATE] shows it
        assert [t["state"] for t in snap["tasks"]] == ["awaiting_input"]
        assert reply(c, "q-1", epoch, q, "mera employee ID E-4521 hai")["status"] == "accepted"
        _, _, snap = wait_for(c, "q-1", after, "task.completed")
        assert "Employee ID: E-4521." in text_of(doc_id) and snap["question"] is None
        assert seen == []                                                       # filled in by code: no editor call
        assert reply(c, "q-1", epoch, q, "E-4521")["reason"] == "unknown_or_answered_question"
        assert [e["type"] for e in c.get("/internal/sessions/q-1/events", params={"after": 0},
                                         headers=H).json()["events"]].count("command.rejected") == 0


def test_a_field_the_caller_doesnt_have_is_taken_out_and_the_next_one_asked(app_factory, gaps):
    seen = gaps("[Your employee ID]", "[Your Name]")
    with app_factory() as c:
        epoch, doc_id = start(c, "q-2")
        q1, after, _ = wait_for(c, "q-2", 0, "task.question")
        reply(c, "q-2", epoch, q1, "wo toh nahi pata, chhod dijiye")
        q2, after, _ = wait_for(c, "q-2", after, "task.question")              # one question at a time
        assert q2["payload"]["text"] == "What is your name?"
        reply(c, "q-2", epoch, q2, "naam hai Saurabh Kumar")
        wait_for(c, "q-2", after, "task.completed")
        assert len(seen) == 1 and seen[0][0].startswith("The caller doesn't have your employee ID")  # in a sentence
        text = text_of(doc_id)
        assert "Saurabh Kumar" in text and "[" not in text


def open_question(c, sid):
    return c.get(f"/internal/sessions/{sid}/events", params={"after": 0}, headers=H).json()["snapshot"]["question"]


def test_details_lines_are_filled_and_taken_out_by_code_and_the_answers_join_the_conversation(app_factory, gaps,
                                                                                               monkeypatch):
    from apps.control import service, worker
    from docengine.model import KeyValue
    seen = gaps("[x]")

    async def letter(api_key, transcript, intent, doc_id, formats, doc_lang="en-IN", **k):
        return Document(id=doc_id, title="Account Closure", meta={"from": "Saurabh Kumar, [Your Address], [City]"},
                        blocks=[Paragraph(runs=[Run(text="Please close my savings account.")]),
                                KeyValue(pairs=[["Account Number", "[Your Account Number]"],
                                                ["Branch", "[Branch Name]"]])]), {"attempts": 1}
    monkeypatch.setattr(worker, "draft", letter)
    with app_factory() as c:
        epoch, doc_id = start(c, "q-9")
        after = 0
        for words in ("Gomti Nagar, Lucknow", "account number E-3124567890", "branch nahi pata, chhod do"):
            q, after, _ = wait_for(c, "q-9", after, "task.question")
            reply(c, "q-9", epoch, q, words)
        wait_for(c, "q-9", after, "task.completed")
        doc = service.S.orch.s.versions[(doc_id, 1)]
        assert seen == []                                       # no editor call at all: all done by code
        assert doc.meta["from"] == "Saurabh Kumar, Gomti Nagar, Lucknow"
        assert doc.blocks[1].pairs == [["Account Number", "E-3124567890"]]
        task = next(t for t in service.S.orch.s.tasks.values() if t.session_id == "q-9")
        assert "BolDoc: What is your account number?\nCaller: account number E-3124567890" in task.transcript


def test_a_reply_that_isnt_an_answer_leaves_the_question_open_for_the_next_words(app_factory, gaps):
    gaps("[Your employee ID]")
    with app_factory() as c:
        epoch, doc_id = start(c, "q-3")
        q, after, _ = wait_for(c, "q-3", 0, "task.question")
        reply(c, "q-3", epoch, q, "kitna time lagega?")
        until(lambda: (open_question(c, "q-3") or {}).get("id") == q["payload"]["question_id"])  # open again
        assert reply(c, "q-3", epoch, q, "haan, E-4521")["status"] == "accepted"
        wait_for(c, "q-3", after, "task.completed")
        assert "E-4521" in text_of(doc_id)
        events = c.get("/internal/sessions/q-3/events", params={"after": 0}, headers=H).json()["events"]
        assert [e["type"] for e in events].count("task.question") == 1      # never asked twice


def test_a_change_asked_along_with_the_answer_is_made_too(app_factory, gaps):
    """The talker models answered "first make it 26 to 27" with "done" and no tool call: the writer acts on the
    caller's own words."""
    seen = gaps("[Your employee ID]")
    with app_factory() as c:
        epoch, doc_id = start(c, "q-7")
        q, after, _ = wait_for(c, "q-7", 0, "task.question")
        reply(c, "q-7", epoch, q, "E-4521, aur dates 26 se 27 kar dijiye")
        wait_for(c, "q-7", after, "task.completed")
        assert seen == [["dates 26 se 27 kar dijiye"]] and "E-4521" in text_of(doc_id)


def test_a_caller_who_hangs_up_is_not_waited_for(app_factory, gaps):
    seen = gaps("[Your employee ID]")
    with app_factory() as c:
        _, doc_id = start(c, "q-4")
        _, after, _ = wait_for(c, "q-4", 0, "task.question")
        c.post("/internal/sessions/q-4/end", headers=H)
        wait_for(c, "q-4", after, "task.completed", timeout=8)                 # not the question's 45 s
        assert seen == [] and "[Your employee ID]" in text_of(doc_id)          # still flagged, never invented


def test_a_change_asked_while_a_question_is_open_goes_in_with_the_answer(app_factory, gaps):
    seen = gaps("[Your employee ID]")
    with app_factory() as c:
        epoch, doc_id = start(c, "q-5")
        q, after, _ = wait_for(c, "q-5", 0, "task.question")
        c.post("/internal/commands", json={"cmd": "edit_doc", "session_id": "q-5", "epoch": epoch, "turn_id": "te1",
                                           "doc_id": doc_id, "base_version": 0,
                                           "instruction": "dates 26 se 27 kar dijiye"}, headers=H)
        assert open_question(c, "q-5")["id"] == q["payload"]["question_id"]  # still waiting for the answer
        reply(c, "q-5", epoch, q, "E-4521")
        _, _, snap = wait_for(c, "q-5", after, "task.completed", timeout=8)
        assert "E-4521" in text_of(doc_id) and snap["question"] is None
        assert seen == [["dates 26 se 27 kar dijiye"]]                         # one edit, at the end


def test_a_later_change_doesnt_ask_again_about_a_gap_left_open(app_factory, gaps, monkeypatch):
    from apps.control import worker
    monkeypatch.setattr(worker, "QUESTION_WAIT_S", 1.0)
    seen = gaps("[Your employee ID]")
    with app_factory() as c:
        epoch, doc_id = start(c, "q-8")
        _, after, _ = wait_for(c, "q-8", 0, "task.completed", timeout=8)       # nobody answered: v1 keeps the gap
        c.post("/internal/commands", json={"cmd": "edit_doc", "session_id": "q-8", "epoch": epoch, "turn_id": "te1",
                                           "doc_id": doc_id, "base_version": 1,
                                           "instruction": "dates 26 se 27 kar dijiye"}, headers=H)
        wait_for(c, "q-8", after, "task.completed", timeout=8)
        events = c.get("/internal/sessions/q-8/events", params={"after": 0}, headers=H).json()["events"]
        assert [e["type"] for e in events].count("task.question") == 1 and seen == [["dates 26 se 27 kar dijiye"]]


def test_a_question_nobody_answers_ends_the_asking(app_factory, gaps, monkeypatch):
    from apps.control import worker
    monkeypatch.setattr(worker, "QUESTION_WAIT_S", 1.0)
    seen = gaps("[Your employee ID]", "[Your Name]")
    with app_factory() as c:
        _, doc_id = start(c, "q-6")
        wait_for(c, "q-6", 0, "task.completed", timeout=8)
        events = c.get("/internal/sessions/q-6/events", params={"after": 0}, headers=H).json()["events"]
        assert [e["type"] for e in events].count("task.question") == 1     # the caller isn't answering: stop
        assert seen == [] and "[Your employee ID]" in text_of(doc_id)
