"""Long documents written by Hermes: the tools it writes through, the run control drives, the fallback."""
import asyncio
import time

import pytest
from pydantic import ValidationError

from apps.control.bigdoc import BigDocs, Drafts, parse_blocks
from apps.control.core.orchestrator import Orchestrator
from apps.thinker.llm import ThinkerError
from protocol.fsm import ConvState
from protocol.messages import Answer, CreateDoc


@pytest.fixture(autouse=True)
def no_numbers_review(monkeypatch):
    """The numbers review is a model call: these tests stub it (it finds nothing) unless they script one."""
    from apps.control import bigdoc

    async def review(api_key, draft, client=None):
        return [], {}
    monkeypatch.setattr(bigdoc, "review_numbers", review)


OUTLINE = [{"id": "s1", "title": "Executive summary", "words": 40},
           {"id": "s2", "title": "Cost of the project", "words": 40}]
BRIEF = {"size": "long", "document": "Project report for a MUDRA loan", "doc_type": "report", "outline": OUTLINE,
         "unit": "pages",
         "facts": {"business": "Kumar Tea & Snacks, Jaipur"}, "not_given": ["the loan amount"],
         "transcript": "Caller: MUDRA loan ke liye project report chahiye"}
WORDS = " ".join(["word"] * 40)
COSTS = {"type": "table", "caption": "Cost of the project",
         "columns": [{"name": "Item"}, {"name": "Amount", "type": "currency"}],
         "rows": [["Coffee machine", "Rs 1,20,000"], ["Furniture", "Rs 1,10,000"]], "total_row": True}


class FakeStore:
    async def flush(self):
        pass


class FakeBg:
    """The worker, as far as BigDocs uses it."""

    def __init__(self, orch):
        self.orch, self.store, self.big, self.woken, self.key = orch, FakeStore(), None, 0, "test-key"
        self.drafted = []

    def on_the_line(self, t):
        return self.orch.s.sessions[t.session_id].state not in (ConvState.OFFLINE, ConvState.RECONNECTING)

    def wake(self):
        self.woken += 1

    async def draft_t2(self, task, extra=""):
        self.drafted.append(extra)
        from docengine.model import Document
        return Document(id=task.doc_id, title="Short version"), ["wrote the first draft"], {"tier": "T2"}


def big_task(big_docs=True):
    orch = Orchestrator(clock=time.time)
    orch.big_docs = big_docs
    orch.open_session("s1", "pstn", caller="+919988776655", user_id=7, plan="credits")
    res = orch.handle(CreateDoc(session_id="s1", epoch=1, turn_id="t1", formats=["pdf", "xlsx"],
                                intent="project report", transcript=BRIEF["transcript"], brief=dict(BRIEF)))
    return orch, res


def claimed():
    orch, res = big_task()
    task, _ = orch.claim(res.task_id, "w1")
    bg = FakeBg(orch)
    big = BigDocs(bg, client=None, drafts=Drafts(), question_wait_s=0.5, answer_s=5, poll_s=0.01)
    big.active = (task.id, "w1")
    return orch, bg, big, task


def test_a_long_document_goes_to_hermes_with_its_brief_and_a_short_one_doesnt():
    orch, res = big_task()
    task, doc = orch.s.tasks[res.task_id], orch.s.docs[res.doc_id]
    assert task.tier == "T3" and task.big["brief"]["outline"] == OUTLINE and doc.long
    accepted = [e for e in orch.s.events["s1"] if e.type == "task.accepted"][-1]
    assert accepted.payload["tier"] == "T3"
    off, res2 = big_task(big_docs=False)  # Hermes not configured: today's writer
    assert off.s.tasks[res2.task_id].tier == "T2" and not off.s.docs[res2.doc_id].long
    # the brief control adds never changes the command's key: a retried create stays a duplicate
    again = orch.handle(CreateDoc(session_id="s1", epoch=1, turn_id="t1", formats=["pdf", "xlsx"],
                                  intent="project report", transcript=BRIEF["transcript"], brief={"size": "short"}))
    assert again.status == "duplicate" and again.task_id == res.task_id


def test_blocks_are_checked_and_amounts_become_numbers():
    blocks = parse_blocks([{"type": "paragraph", "text": "Hello"}, COSTS, {"type": "list", "items": ["a"]}])
    assert blocks[0].runs[0].text == "Hello" and blocks[1].rows == [["Coffee machine", 120000], ["Furniture", 110000]]
    with pytest.raises(ValidationError):
        parse_blocks([{"type": "poem", "text": "x"}])


def test_the_writer_writes_through_the_tools_and_check_says_what_is_left():
    orch, _bg, big, _task = claimed()

    async def go():
        brief = await big.get_brief()
        assert brief["outline"] == OUTLINE and brief["sections_written"] == [] and brief["not_given"]
        assert brief["unit"] == "pages"  # slides tell the writer to write a deck
        assert (await big.write_section("s9", [{"type": "paragraph", "text": "x"}]))["ok"] is False
        bad = await big.write_section("s1", [{"type": "chart"}])
        assert bad["ok"] is False and "not valid" in bad["error"]
        ok = await big.write_section("s1", [{"type": "paragraph", "text": "Short. [Your loan amount] is needed."}])
        assert ok["ok"] and ok["remaining"] == ["s2"]
        assert (await big.finish())["error"].startswith("sections not written yet")
        await big.write_section("s2", [COSTS, {"type": "paragraph",
                                               "text": "The total project cost is Rs 3,00,000."}])
        report = await big.check()
        assert [s["id"] for s in report["too_short"]] == ["s1", "s2"]
        assert report["totals_wrong"] and "3,00,000" in report["totals_wrong"][0]  # the table adds up to 2,30,000
        assert report["placeholders"] == ["[Your loan amount]"]
        assert "well under" in (await big.finish())["error"]   # once: write them again in depth
        assert (await big.finish())["ok"]                       # a writer that can't go longer still finishes
    asyncio.run(go())
    assert any(e.type == "task.progress" for e in orch.s.events["s1"])


def reads(monkeypatch, value):
    """The caller's words read as an answer (apps/thinker/gaps.interpret), without a model call."""
    from apps.control import bigdoc
    from apps.thinker.gaps import Reading

    async def interpret(api_key, placeholder, question, reply, doc_lang, client=None):
        return (Reading("answer", value, "") if value else Reading("other", "", "")), {}
    monkeypatch.setattr(bigdoc, "interpret", interpret)


async def answer_soon(orch, task, words):
    for _ in range(100):
        await asyncio.sleep(0.02)
        if task.question_id:
            q = orch.s.questions[task.question_id]
            orch.handle(Answer(session_id="s1", epoch=1, turn_id=f"r-{q.id}", question_id=q.id, value=words))
            return


def test_the_caller_is_asked_once_and_never_about_the_writers_own_work(monkeypatch):
    orch, _bg, big, task = claimed()
    reads(monkeypatch, "Rs 4,00,000")

    async def go():
        asking = asyncio.create_task(big.ask_caller(["What is the loan amount?", "Should I rewrite section 2?",
                                                     "Your OTP please?"]))
        await answer_soon(orch, task, "chaar lakh")
        out = await asking
        assert out["answers"] == [{"question": "What is the loan amount?", "answer": "Rs 4,00,000"}]
        assert "write every section" in out["next"]  # live: Qwen ended its turn after the answers
        assert task.big["asked"]["dropped"] == ["Should I rewrite section 2?", "Your OTP please?"]
        again = await big.ask_caller(["Which bank?"])  # a retry after a restart gets the same answers
        assert again["answers"] == out["answers"]
        assert (await big.get_brief())["caller_answers"] == out["answers"]
    asyncio.run(go())


def test_a_goodbye_is_no_answer_and_a_hang_up_ends_the_wait(monkeypatch):
    """U"theek hai shukriya, bas itna hi" reached the writer as the loan amount."""
    orch, _bg, big, task = claimed()
    reads(monkeypatch, "")  # read as no answer

    async def go():
        asking = asyncio.create_task(big.ask_caller(["What is the loan amount?", "Which bank?"]))
        await answer_soon(orch, task, "Theek hai shukriya. Bas itna hi.")
        await asyncio.sleep(0.05)
        orch.go_offline("s1")  # the caller hangs up while the second question waits
        t0 = asyncio.get_running_loop().time()
        out = await asking
        assert asyncio.get_running_loop().time() - t0 < 0.45  # not the whole wait
        assert [a["answer"] for a in out["answers"]] == [None, None] and "placeholder" in out["note"]
    asyncio.run(go())


def test_a_caller_who_has_hung_up_gets_placeholders_for_now():
    orch, _bg, big, _task = claimed()
    orch.go_offline("s1")

    async def go():
        out = await big.ask_caller(["What is the loan amount?"])
        assert out["answers"] == [{"question": "What is the loan amount?", "answer": None}]
        assert "placeholder" in out["note"]
    asyncio.run(go())


class FakeCallbacks:
    """callback.Callbacks: who was rung, and how each ring ended."""

    def __init__(self, outcome="answered"):
        self.placed, self.outcome = [], outcome

    async def place(self, **kw):
        self.placed.append(kw)
        return {"id": f"cb{len(self.placed)}", "placed_at": time.time()}

    async def status(self, attempt):
        return self.outcome


def test_a_caller_who_said_yes_to_a_callback_is_rung_and_answers_on_that_call(monkeypatch):
    """They hung up; BolDoc rings the number that asked for the document, asks there, and thanks them
    (callback.done) once nothing is left."""
    orch, _bg, big, task = claimed()
    task.big["brief"]["callback_ok"] = True
    reads(monkeypatch, "Rs 8,00,000")
    big.callbacks = FakeCallbacks()
    orch.go_offline("s1")

    async def go():
        asking = asyncio.create_task(big.ask_caller(["What is the loan amount?"]))
        await asyncio.sleep(0.05)
        rung = big.callbacks.placed
        assert len(rung) == 1 and (rung[0]["number"], rung[0]["user_id"], rung[0]["n"]) == ("+919988776655", 7, 1)
        assert task.big["waiting"] is True               # their next call can answer too (service._attach_waiting)
        orch.open_session("cb", "pstn", caller="+919988776655", user_id=7, plan="credits")  # they picked up
        orch.attach("cb", task.doc_id)
        for _ in range(100):
            await asyncio.sleep(0.02)
            if orch.snapshot("cb")["question"]:
                break
        q = orch.snapshot("cb")["question"]["id"]
        orch.handle(Answer(session_id="cb", epoch=1, turn_id="a1", question_id=q, value="aath lakh"))
        out = await asking
        assert out["answers"] == [{"question": "What is the loan amount?", "answer": "Rs 8,00,000"}]
        assert [e.type for e in orch.s.events["cb"]] == ["task.question", "callback.done"]
        assert task.big["waiting"] is False and task.big["reach"]["attempts"][0]["status"] == "answered"
    asyncio.run(go())


def test_no_answer_is_rung_once_more_then_sent_a_link_and_an_answer_typed_there_counts(monkeypatch):
    from apps.control import bigdoc
    from tests.control.helpers import FakeSms
    monkeypatch.setattr(bigdoc, "RETRY_AFTER_S", 0.05)
    orch, bg, big, task = claimed()
    task.big["brief"]["callback_ok"] = True
    bg.sms, bg.host, big.answer_s = FakeSms(), "boldoc.in", 1.0
    FakeSms.sent.clear()
    big.callbacks = FakeCallbacks(outcome="no_answer")
    orch.go_offline("s1")

    async def go():
        asking = asyncio.create_task(big.ask_caller(["What is the loan amount?", "Which bank?"]))
        await asyncio.sleep(0.4)
        assert len(big.callbacks.placed) == 2                        # one retry, then the link
        assert len(FakeSms.sent) == 1 and FakeSms.sent[0][0] == "+919988776655"
        assert f"https://boldoc.in/app/documents/{task.doc_id}" in FakeSms.sent[0][1]
        task.big["web_answers"] = {"Which bank?": "Punjab National Bank"}  # typed on the document's page
        out = await asking
        assert out["answers"] == [{"question": "What is the loan amount?", "answer": None},
                                  {"question": "Which bank?", "answer": "Punjab National Bank"}]
        assert task.big["reach"]["sms"] is True
    asyncio.run(go())


def test_without_a_yes_to_a_callback_the_link_goes_by_sms_at_once():
    orch, bg, big, _task = claimed()
    from tests.control.helpers import FakeSms
    bg.sms, bg.host, big.answer_s = FakeSms(), "boldoc.in", 0.3
    FakeSms.sent.clear()
    big.callbacks = FakeCallbacks()
    orch.go_offline("s1")

    async def go():
        out = await big.ask_caller(["What is the loan amount?"])
        assert big.callbacks.placed == [] and len(FakeSms.sent) == 1
        assert out["answers"][0]["answer"] is None and "placeholder" in out["note"]
    asyncio.run(go())


class FakeHermes:
    """Hermes' API: each status poll runs one scripted step (the writer calling our tools), then reports."""

    def __init__(self, big, script, final="completed"):
        self.big, self.script, self.final = big, list(script), final
        self.started, self.stopped = [], []

    async def start(self, idem, session_id, text, instructions):
        self.started.append((idem, text))
        return f"run_{len(self.started)}"

    async def status(self, run_id):
        if self.script:
            step = self.script.pop(0)
            await step(self.big)
            return {"status": "running"}
        return {"status": self.final}

    async def stop(self, run_id):
        self.stopped.append(run_id)


async def write_all(big):
    await big.write_section("s1", [{"type": "paragraph", "text": WORDS}])
    await big.write_section("s2", [COSTS, {"type": "paragraph", "text": WORDS}])
    await big.finish("done")


def test_a_finished_run_becomes_the_document_with_its_outline_and_cost():
    _orch, _bg, big, task = claimed()
    big.client = FakeHermes(big, [write_all])
    big.add_usage(0, 0)

    async def go():
        big.active = None
        return await big.write(task, "w1")
    doc, changes, stats = asyncio.run(go())
    heads = [b.text for b in doc.blocks if b.type == "heading"]
    assert heads == ["Executive summary", "Cost of the project"] and doc.title == "Project report for a MUDRA loan"
    assert stats["tier"] == "T3" and changes == ["wrote the document (2 sections)"]
    assert task.big["run_id"] == "run_1" and big.client.started[0][0] == f"boldoc-{task.id}-1"


def test_a_long_document_gets_its_cover_contents_chart_and_the_writers_title():
    """Formats: a chart block goes in as written; a long read gets a cover, contents and page numbers;
    the title is the writer's, in the document's language."""
    _orch, _bg, big, task = claimed()
    chart = {"type": "chart", "kind": "column", "title": "बिक्री", "categories": ["साल 1", "साल 2"], "unit": "currency",
             "series": [{"name": "बिक्री", "values": [2880000, 3168000]}]}

    async def write(big):
        await big.write_section("s1", [{"type": "paragraph", "text": WORDS}, chart])
        await big.write_section("s2", [COSTS, {"type": "paragraph", "text": WORDS}])
        await big.finish("done", "मुद्रा लोन के लिए प्रोजेक्ट रिपोर्ट")
    big.client = FakeHermes(big, [write])

    async def go():
        big.active = None
        return await big.write(task, "w1")
    doc, _, _ = asyncio.run(go())
    charts = [b for b in doc.blocks if b.type == "chart"]
    assert charts and charts[0].series[0].values == [2880000, 3168000]
    assert doc.title == "मुद्रा लोन के लिए प्रोजेक्ट रिपोर्ट"
    assert (doc.style.cover, doc.style.toc, doc.style.page_numbers) == (True, True, True)


def test_a_run_that_fails_or_runs_long_falls_back_to_todays_writer():
    _orch, bg, big, task = claimed()
    big.client = FakeHermes(big, [], final="failed")
    _doc, _, stats = asyncio.run(big.write(task, "w1"))
    assert stats["fallback_from"] == "T3" and stats["fallback_reason"] == "run_failed"
    assert "Executive summary" in bg.drafted[0]  # the outline goes to today's writer
    _orch, bg, big, task = claimed()
    big.work_s = 0.0

    async def slow(_):
        await asyncio.sleep(0.02)
    big.client = FakeHermes(big, [slow, slow, slow])
    _, _, stats = asyncio.run(big.write(task, "w1"))
    assert stats["fallback_reason"] == "over_time" and big.client.stopped


def test_a_restarted_worker_reattaches_to_the_same_run():
    _orch, _bg, big, task = claimed()
    task.big["run_id"], task.big["runs"] = "run_live", 1
    big.client = FakeHermes(big, [write_all])
    asyncio.run(big.write(task, "w1"))
    assert big.client.started == []  # no second run: the first one carried on


def test_a_run_that_stops_short_gets_one_more_run_for_the_missing_sections():
    _orch, _bg, big, task = claimed()

    async def first(b):
        await b.write_section("s1", [{"type": "paragraph", "text": WORDS}])

    async def rest(b):
        await b.write_section("s2", [COSTS, {"type": "paragraph", "text": WORDS}])
    client = FakeHermes(big, [first])
    big.client = client

    async def go():
        orig = client.start

        async def start(idem, session_id, text, instructions):
            if client.started:  # the second run writes the rest
                client.script = [rest]
            return await orig(idem, session_id, text, instructions)
        client.start = start
        return await big.write(task, "w1")
    _doc, _, stats = asyncio.run(go())
    assert stats["tier"] == "T3" and len(client.started) == 2 and "Cost of the project" in client.started[1][1]


def test_the_token_cap_is_enforced_by_the_proxy_accounting():
    _orch, _bg, big, task = claimed()
    big.token_cap = 100
    big.add_usage(60, 10)
    assert not big.over_cap()
    big.add_usage(30, 5)
    assert big.over_cap() and task.big["usage"]["calls"] == 2


def test_an_edit_to_a_long_document_is_never_a_full_rewrite(monkeypatch):
    from apps.thinker import editor
    from docengine.model import Document

    async def no_patch(*a, **k):
        raise ThinkerError("bad_json", "no ops")
    monkeypatch.setattr(editor, "chat_json", no_patch)
    base = Document(id="d", title="Report")
    with pytest.raises(ThinkerError) as e:
        asyncio.run(editor.edit("k", base, ["make it 15 slides"], "", "en-IN", ["pdf"], allow_redraft=False))
    assert e.value.code == "needs_rewrite"


def test_a_long_document_costs_its_own_price():
    from apps.control.accounts.pricing import PRICING
    assert PRICING.price_for("doc_create_big") == 60 and PRICING.price_for("doc_create") == 10


def test_the_reconciler_starts_a_long_documents_own_workflow():
    from apps.control.worker import Background
    orch, res = big_task()
    started = []

    class Flows:
        async def start_task(self, task_id, wid, big=False):
            started.append((wid, big))
            return True

        async def start_delivery(self, delivery_id, wid):
            return True

    async def go():
        bg = Background(orch, FakeStore(), "k", "boldoc.test", None)  # (its events need a running loop on 3.9)
        bg.flows = Flows()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(bg._reconcile(), 0.3)
    asyncio.run(go())
    assert started == [(f"big-{res.task_id}", True)]


def test_a_total_row_is_computed_never_written_twice():
    """Uthe writer sent the total row itself as total_row."""
    t = parse_blocks([dict(COSTS, total_row=["Total", "Rs 2,30,000"],
                           rows=COSTS["rows"] + [["Total", "Rs 2,30,000"]])])[0]
    assert t.total_row is True and [r[0] for r in t.rows] == ["Coffee machine", "Furniture"]


def test_check_asks_for_length_once_per_section_and_then_lets_the_writer_finish():
    """Uat 75% the writer rewrote sections 28 times, checked 25 times and hit its token cap."""
    _orch, _bg, big, task = claimed()

    async def go():
        await big.write_section("s1", [{"type": "paragraph", "text": "short"}])
        await big.write_section("s2", [{"type": "paragraph", "text": WORDS}])
        first = await big.check()
        assert [s["id"] for s in first["too_short"]] == ["s1"] and "once" in first["note"]
        await big.write_section("s1", [{"type": "paragraph", "text": "still short"}])
        assert (await big.check())["too_short"] == []  # its rewrite stands
        third = await big.check()
        assert "finish now" in third["note"] and task.big["checks"] == 3
    asyncio.run(go())


def test_a_run_stopped_with_every_section_written_still_delivers_the_long_document():
    """Uthe token cap was hit with all 9 sections written, and a short fallback went out instead."""
    _orch, bg, big, task = claimed()
    big.token_cap = 10

    async def write_and_overspend(b):
        await write_all(b)
        b.add_usage(100, 0)
    big.client = FakeHermes(big, [write_and_overspend, write_and_overspend])
    doc, changes, stats = asyncio.run(big.write(task, "w1"))
    assert stats["tier"] == "T3" and stats["stopped"] == "over_tokens" and bg.drafted == []
    assert [b.text for b in doc.blocks if b.type == "heading"] == ["Executive summary", "Cost of the project"]
    assert changes == ["wrote the document (2 sections)"] and big.client.stopped


class FakeResearch:
    """research.Research without the network: one canned search, and pages that say what they were asked."""

    def __init__(self):
        self.searcher, self.pages, self.queries, self.reads = self, self, [], []

    async def search(self, query):
        self.queries.append(query)
        return [{"title": "PMMY offerings", "url": "https://www.mudra.org.in/offerings", "snippet": "Kishore loans"},
                {"title": "A blog", "url": "https://blog.example.com/mudra", "snippet": "MUDRA explained"}]

    async def read(self, url, looking_for=""):
        from apps.control.research import ResearchError
        self.reads.append((url, looking_for))
        if "missing" in url:
            raise ResearchError("the page answered HTTP 404")
        title = next((t for host, t in (("mudra", "PMMY offerings"), ("blog", "A blog")) if host in url), "")
        return {"url": url, "title": title, "text": "Kishore: Rs 50,000 to Rs 5 lakh.", "more": False}


def researching(on=True, research=True):
    orch, bg, big, task = claimed()
    task.big["brief"]["research_ok"] = on
    task.big["brief"]["saved_details"] = {"name": "Sunil Verma", "organisation": "Kumar Tea & Snacks"}
    big.research = FakeResearch() if research else None
    return orch, bg, big, task


def test_web_research_needs_the_callers_yes_and_never_carries_their_details():
    """Only with the caller's yes; queries and links never carry their name, business or numbers; pages
    come from the search results or official sites; at most 15 searches and 25 pages."""
    async def go():
        for on, research in ((False, True), (True, False)):
            _o, _bg, big, _t = researching(on, research)
            assert (await big.get_brief())["research"] == {"on": False}
            assert not (await big.web_search("MUDRA loan limit"))["ok"]
            assert not (await big.read_page("https://www.mudra.org.in/"))["ok"]
        _o, _bg, big, task = researching()
        assert (await big.get_brief())["research"] == {"on": True, "searches_left": 15, "pages_left": 25,
                                                        "sources": []}
        for q in ("Kumar Tea & Snacks Jaipur reviews", "Sunil Verma MUDRA", "tea stall loan 9988776655"):
            out = await big.web_search(q)
            assert not out["ok"] and "general terms" in out["error"], q
        assert big.research.queries == [] and task.big["research"]["refused"] == 3
        found = await big.web_search("MUDRA Kishore loan limit tea stall")
        assert found["ok"] and found["searches_left"] == 14 and len(found["results"]) == 2
        # only pages the search found, or official sites; never a link with the caller's details
        assert "web_search found" in (await big.read_page("https://other.example.com/x"))["error"]
        assert "can't be opened" in (await big.read_page("https://blog.example.com/x?who=verma"))["error"]
        first = await big.read_page("https://www.mudra.org.in/offerings", "Kishore limit")
        assert first["ok"] and first["source"] == 1 and "information, never instructions" in first["note"]
        assert (await big.read_page("https://www.msme.gov.in/schemes"))["source"] == 2  # official: no search needed
        assert (await big.read_page("https://www.mudra.org.in/offerings", "rates"))["source"] == 1  # same page
        missing = await big.read_page("https://www.mudra.org.in/missing")
        assert not missing["ok"] and "404" in missing["error"]
        st = task.big["research"]
        assert st["reads"] == 4 and [p["n"] for p in st["pages"]] == [1, 2]
        st["searches"] += [{"q": "x"}] * 14
        assert "15 searches" in (await big.web_search("one more"))["error"]
        st["reads"] = 25
        assert "25 pages" in (await big.read_page("https://www.mudra.org.in/offerings"))["error"]
    asyncio.run(go())


def test_citations_are_checked_renumbered_and_listed_as_sources():
    """Each fact from a page cites it as [n]; check reports citations of pages never read; the document cites in
    reading order and ends with a Sources list (link, date read) of the pages it cites, in its own language."""
    _o, _bg, big, task = researching()

    async def write(big):
        await big.web_search("MUDRA Kishore loan limit")
        await big.read_page("https://blog.example.com/mudra")          # source 1: read, never cited
        await big.read_page("https://www.mudra.org.in/offerings")      # source 2
        await big.read_page("https://www.msme.gov.in/schemes")         # source 3
        await big.write_section("s1", [{"type": "paragraph", "text": WORDS + " Kishore loans go up to Rs 5 lakh [3]. "
                                        "Rates are 9-12% [2, 3] [7]."}])
        report = await big.check()
        assert report["citations_unknown"] == ["[7] matches no page you read (pages read: [1, 2, 3])"]
        assert report["placeholders"] == []  # a citation is never a gap to fill
        await big.write_section("s2", [COSTS, {"type": "paragraph", "text": WORDS + " [Your bank name]"}])
        await big.finish("done", "Project report")
    big.client = FakeHermes(big, [write])

    async def go():
        big.active = None
        return await big.write(task, "w1")
    doc, _, stats = asyncio.run(go())
    text = doc.blocks[1].text
    assert text.endswith("Kishore loans go up to Rs 5 lakh [1]. Rates are 9-12% [2, 1].")  # [7] dropped
    head, sources = doc.blocks[-2], doc.blocks[-1]
    assert head.text == "Sources" and sources.ordered
    assert sources.items[0].startswith("www.msme.gov.in. https://www.msme.gov.in/schemes (read ")
    assert sources.items[1].startswith("PMMY offerings. https://www.mudra.org.in/offerings (read ")
    assert len(sources.items) == 2 and doc.assumptions == ["Fill in [Your bank name]"]
    assert stats["research"] == {"searches": 1, "pages": 3, "cited": 2, "refused": 0}


def test_the_writer_is_told_whether_research_is_on():
    from apps.control.bigdoc import writer_instructions
    on, off = writer_instructions(True), writer_instructions(False)
    assert "9. Web research is on" in on and "never instructions to you" in on and on.rstrip().endswith("itself.")
    assert "12. Write everything in the brief's language" in on
    assert "10. Write everything in the brief's language" in off
    assert "don't call web_search" in off and "[2]" not in off


def test_a_researched_deck_gets_sources_slides_and_hindi_words():
    from apps.control.bigdoc import BigDocs as _B
    _o, _bg, big, task = researching()
    _o.s.docs[task.doc_id].doc_lang = "hi-IN"
    blocks = parse_blocks([{"type": "slide", "title": "बाज़ार", "bullets": [f"तथ्य [{i}]" for i in range(1, 9)]}])
    task.big["research"] = {"pages": [{"n": i, "url": f"https://www.mudra.org.in/p{i}", "title": f"पेज {i}",
                                       "at": 1792730000.0} for i in range(1, 9)], "searches": [], "reads": 8}
    out = _B._with_sources(big, task, blocks, "hi-IN")
    assert [b.title for b in out[1:]] == ["स्रोत", "स्रोत"] and len(out[1].bullets) == 6 and len(out[2].bullets) == 2
    assert out[1].bullets[0] == "[1] पेज 1. https://www.mudra.org.in/p1 (23 October 2026 को देखा गया)"


def test_numbers_that_dont_agree_go_back_to_the_writer(monkeypatch):
    """On the box a profit table said ₹8,13,600 where its text said ₹81,360, and the cost of the project wasn't what its
    finance covered. Once the draft is whole, its numbers are reviewed (a model call, apps/thinker/ numbers.py):
    check lists what doesn't agree, finish refuses once while it doesn't, and a fixed draft is reviewed again; the
    same draft never twice, at most three times a document, and the calls count toward its cost."""
    from apps.control import bigdoc
    calls = []

    async def review(api_key, draft, client=None):
        calls.append(draft)
        wrong = [{"section": "s2", "problem": "net profit: text ₹81,360, table ₹8,13,600"}] if len(calls) == 1 else []
        return wrong, {"usage": {"prompt_tokens": 3000, "completion_tokens": 100}}
    monkeypatch.setattr(bigdoc, "review_numbers", review)
    _orch, _bg, big, task = claimed()
    profit = {"type": "table", "caption": "Profit", "columns": [{"name": "Year"}, {"name": "Net", "type": "currency"}],
              "rows": [["Year 2", 813600]]}

    async def go():
        await big.write_section("s1", [{"type": "paragraph", "text": WORDS + ". Net profit is ₹81,360 in year 2."}])
        assert "numbers_wrong" not in await big.check() and calls == []  # a section still missing: not yet
        await big.write_section("s2", [COSTS, profit, {"type": "paragraph", "text": WORDS}])
        report = await big.check()
        assert report["numbers_wrong"] == ["s2: net profit: text ₹81,360, table ₹8,13,600"]
        assert "numbers_wrong" in report["note"]
        read = calls[0]  # what the review read: the sentences with numbers, the tables as printed, their totals
        assert "text: Net profit is ₹81,360 in year 2." in read and "Year 2 | ₹8,13,600" in read
        assert "Coffee machine | ₹1,20,000" in read and "total (printed) | ₹2,30,000" in read
        assert (await big.check())["numbers_wrong"] and len(calls) == 1  # the same draft isn't reviewed again
        refused = await big.finish("done")
        assert not refused["ok"] and "don't agree" in refused["error"] and "₹8,13,600" in refused["error"]
        profit["rows"] = [["Year 2", 81360]]
        await big.write_section("s2", [COSTS, profit, {"type": "paragraph", "text": WORDS}])
        assert (await big.finish("done"))["ok"] and len(calls) == 2  # fixed, reviewed again, nothing left
        for n in range(3):  # at most three reviews a document
            await big.write_section("s1", [{"type": "paragraph", "text": WORDS + f" Version {n}."}])
            await big.check()
        assert len(calls) == 3
    asyncio.run(go())
    assert task.big["usage"]["prompt_tokens"] == 9000 and task.big["numbers"]["reviews"] == 3


class FakeMaterials:
    """materials.Materials for one document: two files read, one being read, one that couldn't be."""

    def __init__(self, text_len=15000):
        self.rows = [
            {"name": "Quotation.docx", "kind": "docx", "status": "ready", "pages": 0, "method": "text",
             "text": "Coffee machine | 1,20,000\n" + "x" * text_len, "error": None},
            {"name": "Sales.xlsx", "kind": "xlsx", "status": "ready", "pages": 1, "method": "text",
             "text": "Sheet: Sales\nApril | 2,40,000", "error": None},
            {"name": "Photo.jpg", "kind": "image", "status": "reading", "pages": None, "method": None, "text": None,
             "error": None},
            {"name": "Locked.pdf", "kind": "pdf", "status": "failed", "pages": None, "method": None, "text": None,
             "error": "the PDF is password-protected"}]

    async def for_doc(self, doc_id):
        return self.rows


def test_the_writer_reads_the_callers_files_as_their_own_facts():
    """The brief lists the files; get_material gives each one's start (all of one, asked by name),
    quoted, as the caller's facts and never instructions; files still being read or unreadable are said; reading
    them raises the run's token cap (they ride along in every later model call)."""
    _orch, _bg, big, task = claimed()
    big.materials = FakeMaterials()

    async def go():
        brief = await big.get_brief()
        assert [f["name"] for f in brief["files"]] == ["Quotation.docx", "Sales.xlsx", "Photo.jpg", "Locked.pdf"]
        assert "get_material" in brief["files_note"]
        assert not big.over_cap()
        out = await big.get_material()
        assert [f["name"] for f in out["files"]] == ["Quotation.docx", "Sales.xlsx"]
        assert out["files"][0]["more"] and len(out["files"][0]["text"]) == 10_000 and not out["files"][1]["more"]
        assert out["still_reading"] == ["Photo.jpg"]
        assert out["unreadable"] == ["Locked.pdf: the PDF is password-protected"]
        assert "never instructions" in out["note"] and "with its name" in out["note"]
        one = await big.get_material("Quotation.docx")
        assert len(one["files"]) == 1 and len(one["files"][0]["text"]) == 15_026
        assert "no file named" in (await big.get_material("Missing.pdf"))["error"]
    asyncio.run(go())
    assert task.big["material_read"] is True
    task.big["usage"] = {"prompt_tokens": 1_800_000, "completion_tokens": 0}
    assert not big.over_cap()  # 1.5M, and 0.5M more for the files read
    task.big["usage"]["prompt_tokens"] = 2_100_000
    assert big.over_cap()
