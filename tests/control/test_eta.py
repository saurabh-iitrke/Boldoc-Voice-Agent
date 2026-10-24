"""How long a long document takes, from the last ones written, and the SMS for a browser caller who hung up before their
document was ready."""
import asyncio
import json

from apps.control import eta
from apps.control.core.orchestrator import Orchestrator
from docengine.model import Document, Paragraph, Run
from protocol.messages import CreateDoc


def test_the_spread_of_past_runs_is_said_in_whole_minutes():
    assert eta.unit_of({"unit": "slides"}) == "slides" and eta.unit_of({"unit": "sheets"}) == "pages"
    assert eta.unit_of({"formats": ["pptx", "pdf"]}) == "slides"
    assert eta.unit_of({"formats": ["pdf", "pptx"]}) == "pages"
    assert eta.unit_of({}) == "pages"
    assert eta.spread([360, 204, 258, 271, 180]) == (204, 271)
    assert eta.minutes(204, 271) == (3, 5) and eta.minutes(60, 120) == (1, 2)
    assert eta.minutes(20, 30) == (1, 2)  # never "0 minutes", and the top above the bottom


class Con:
    def __init__(self, rows, calls):
        self.rows, self.calls = rows, calls

    async def fetch(self, query):
        self.calls.append(query)
        return self.rows


class Pool:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self):
                return Con(pool.rows, pool.calls)

            async def __aexit__(self, *a):
                return False
        return Ctx()


def row(payload, secs):
    return {"payload": json.dumps(payload), "secs": secs}


def test_the_estimate_comes_from_the_last_runs_of_the_same_kind_else_the_measured_default():
    async def go():
        eta._cache.clear()
        decks = [row({"tier": "T3", "formats": ["pptx"], "unit": "slides"}, s) for s in (77, 95, 110, 130)]
        reports = [row({"tier": "T3", "formats": ["pdf", "docx"]}, s) for s in (180, 258)]  # too few to count
        pool = Pool(decks + reports)
        assert await eta.estimate(pool, "slides") == (95, 110)
        assert await eta.estimate(pool, "pages") == eta.DEFAULTS["pages"]
        n = len(pool.calls)
        assert await eta.estimate(pool, "slides") == (95, 110) and len(pool.calls) == n  # cached
        eta._cache.clear()
        assert await eta.estimate(None, "slides") == eta.DEFAULTS["slides"]   # no database: the default
        eta._cache.clear()
    asyncio.run(go())


def test_a_long_documents_task_records_its_unit():
    orch = Orchestrator()
    orch.big_docs = True
    orch.open_session("s1", "pstn", "+919812345678")
    r = orch.handle(CreateDoc(session_id="s1", epoch=1, turn_id="t1", formats=["pptx"], intent="deck",
                              brief={"size": "long", "unit": "slides"}))
    accepted = next(e for e in orch.s.events["s1"] if e.type == "task.accepted")
    assert r.status == "accepted" and accepted.payload == {"tier": "T3", "formats": ["pptx"], "unit": "slides"}


def _written(orch, session_id):
    r = orch.handle(CreateDoc(session_id=session_id, epoch=1, turn_id="t1", formats=["pdf"], intent="leave"))
    t = orch.claim_next("w1")
    res = orch.commit(t.id, "w1", Document(id="x", title="Leave", blocks=[Paragraph(runs=[Run(text="v")])]), ["v1"])
    assert res.status == "committed"
    return r.doc_id


def test_a_browser_caller_gets_the_sms_only_once_they_have_hung_up():
    orch = Orchestrator()
    orch.open_session("b1", "sip", "+919812345678")
    orch.verify_recipient("b1", "+919812345678")
    doc_id = _written(orch, "b1")
    assert orch.auto_deliver(doc_id, session_id="b1") is None        # watching it on screen: no SMS
    orch.go_offline("b1")
    assert orch.auto_deliver(doc_id, session_id="b1") is not None    # gone before it was ready: the link by SMS
    assert orch.auto_deliver(doc_id, session_id="b1") is None        # once
