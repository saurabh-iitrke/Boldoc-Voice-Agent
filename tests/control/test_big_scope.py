"""A long document on the call: scoped alongside the intake, written with its brief, and only for
signed-up callers."""
import pytest

from apps.thinker.intake import Fact, Intake
from tests.control.helpers import FakeSms, H, create_cmd, open_call, wait_for
from tests.control.test_intake_endpoint import fake_prepare, fake_sense

pytest.importorskip("asyncpg")

LONG = {"size": "long", "kind": "project_report", "title": "MUDRA project report", "length": None, "sections": []}


def fake_scope(result):
    async def scope_check(api_key, transcript, client=None):
        return dict(result), {"latency_ms": 600}
    return scope_check


def setup(monkeypatch, scope=LONG):
    from apps.thinker import intake
    from apps.thinker import scope as thinker_scope
    monkeypatch.setattr(intake, "sense_check", fake_sense())
    monkeypatch.setattr(intake, "prepare", fake_prepare(Intake("project report", True, "", [
        Fact("loan amount", "Kitna loan chahiye?", "", True), Fact("business", "Kaun sa business?", "chai ki dukaan",
                                                                   True)])))
    monkeypatch.setattr(thinker_scope, "scope_check", fake_scope(scope))


def test_a_long_document_is_scoped_on_the_call_and_written_with_its_brief(app_factory, monkeypatch):
    setup(monkeypatch)
    from apps.control import eta, service
    eta._cache.clear()  # no runs written yet: the measured defaults (a report takes 3-5 minutes)
    with app_factory() as c:
        service.S.orch.big_docs = True  # Hermes configured (here nothing answers: today's writer takes over)
        epoch = open_call(c, "b-1").json()["epoch"]
        service.S.orch.s.sessions["b-1"].plan = "test"
        r = c.post("/internal/sessions/b-1/intake", json={"transcript": "Caller: MUDRA loan project report"},
                   headers=H).json()
        sc = r["scope"]
        assert sc["kind"] == "project_report" and len(sc["outline"]) == 9 and sc["price"] == 60
        assert (sc["minutes_lo"], sc["minutes"]) == (3, 5) and not sc["signup_needed"]
        assert sc["asks"][0]["what"] == "bank and loan"
        assert sc["research"] is True  # a project report is written with web research
        res = c.post("/internal/commands", json=dict(create_cmd("b-1", epoch), callback_ok=True), headers=H).json()
        assert res["status"] == "accepted" and res["tier"] == "T3" and (res["eta_lo_s"], res["eta_s"]) == (180, 300)
        task = service.S.orch.s.tasks[res["task_id"]]
        brief = task.big["brief"]
        assert task.tier == "T3" and brief["facts"] == {"business": "chai ki dukaan"}
        assert brief["not_given"] == ["loan amount"] and brief["callback_ok"] is True
        assert brief["research_ok"] is True  # by its type, unasked
        assert brief["outline"][0] == {"id": "s1", "title": "Executive summary", "words": sc["outline"][0]["words"]}
        wait_for(c, "b-1", 0, "task.completed")
        meta = next(m for (d, v), m in service.S.orch.s.version_meta.items() if d == res["doc_id"])
        assert meta["thinker"].get("tier") != "T3"  # written by today's writer: Hermes isn't running in tests


def test_a_yes_to_a_callback_is_recorded_with_the_callers_words_and_a_no_to_calls_is_kept(app_factory, monkeypatch):
    """A callback needs a recorded yes (with the caller's own words); "mujhe call mat karna" on a
    callback stops them, and is recorded the same way."""
    setup(monkeypatch)
    from apps.control import service
    with app_factory() as c:
        service.S.orch.big_docs = True
        epoch = open_call(c, "b-4").json()["epoch"]
        service.S.orch.s.sessions["b-4"].plan = "test"
        c.post("/internal/sessions/b-4/intake", json={"transcript": "Caller: MUDRA loan project report"}, headers=H)
        res = c.post("/internal/commands", json=dict(create_cmd("b-4", epoch), callback_ok=True,
                                                     callback_words="Haan ji, isi number pe call kar lijiye",
                                                     research_ok=False), headers=H).json()
        assert res["status"] == "accepted"
        assert service.S.orch.s.tasks[res["task_id"]].big["brief"]["research_ok"] is False  # they said no to it

        off = []

        class Calls:
            async def opt_out(self, number):
                off.append(number)
        service.S.bg.big = type("Big", (), {"callbacks": Calls()})()
        assert c.post("/internal/sessions/b-4/callbacks-off", json={"words": "Mujhe call mat karna"},
                      headers=H).json() == {"ok": True}
        assert off == ["+919988776655"]

        async def consents():
            async with service.S.store.pool.acquire() as con:
                return [tuple(r) for r in await con.fetch("select notice_version, channel, words from consents "
                                                          "order by id")]
        assert c.portal.call(consents)[-2:] == [("callback-v1", "call", "Haan ji, isi number pe call kar lijiye"),
                                                ("callback-off-v1", "call", "Mujhe call mat karna")]
        service.S.bg.big = None


def test_a_short_document_never_takes_the_long_path(app_factory, monkeypatch):
    setup(monkeypatch, scope={"size": "short", "kind": "", "title": "", "length": None, "sections": []})
    from apps.control import service
    with app_factory() as c:
        service.S.orch.big_docs = True
        epoch = open_call(c, "b-2").json()["epoch"]
        r = c.post("/internal/sessions/b-2/intake", json={"transcript": "Caller: leave application"},
                   headers=H).json()
        assert r["scope"] is None
        res = c.post("/internal/commands", json=create_cmd("b-2", epoch), headers=H).json()
        assert service.S.orch.s.tasks[res["task_id"]].tier == "T2"


def test_big_documents_are_for_signed_up_callers(app_factory, monkeypatch):
    setup(monkeypatch)
    from apps.control import service
    with app_factory() as c:
        service.S.orch.big_docs = True

        async def allow(*a, **k):
            return True
        monkeypatch.setattr(service.S.bus, "allow", allow)
        epoch = open_call(c, "b-3").json()["epoch"]  # a first call: the free trial
        r = c.post("/internal/sessions/b-3/intake", json={"transcript": "Caller: project report chahiye"},
                   headers=H).json()
        assert r["scope"]["signup_needed"] is True
        res = c.post("/internal/commands", json=create_cmd("b-3", epoch), headers=H).json()
        assert res["status"] == "rejected" and res["reason"] == "signup_needed"
        assert any("sign-in" in text and dst.endswith("9988776655") for dst, text in FakeSms.sent)
        # a short document is still fine on the trial
        setup(monkeypatch, scope={"size": "short", "kind": "", "title": "", "length": None, "sections": []})
        c.post("/internal/sessions/b-3/intake", json={"transcript": "Caller: theek hai, chhota letter"}, headers=H)
        again = c.post("/internal/commands", json=create_cmd("b-3", epoch, turn="tc2"), headers=H).json()
        assert again["status"] == "accepted"


def test_web_research_is_done_only_where_it_helps(app_factory, monkeypatch):
    """Minutes of a meeting are the caller's own record: no web research, even if the caller asked for it."""
    setup(monkeypatch, scope={**LONG, "kind": "meeting_minutes", "title": "Board meeting minutes"})
    from apps.control import service
    with app_factory() as c:
        service.S.orch.big_docs = True
        epoch = open_call(c, "b-9").json()["epoch"]
        service.S.orch.s.sessions["b-9"].plan = "test"
        sc = c.post("/internal/sessions/b-9/intake", json={"transcript": "Caller: board meeting minutes"},
                    headers=H).json()["scope"]
        assert sc["kind"] == "meeting_minutes" and sc["research"] is False
        res = c.post("/internal/commands", json=dict(create_cmd("b-9", epoch), research_ok=True), headers=H).json()
        assert service.S.orch.s.tasks[res["task_id"]].big["brief"]["research_ok"] is False


def test_files_added_for_the_next_document_are_mentioned_on_the_call_and_go_to_the_long_one(app_factory,
                                                                                          monkeypatch):
    """Files a signed-up owner added on their home page wait for their next document; a call hears of
    them (session open), and the next long document asked for takes them; a short one doesn't."""
    setup(monkeypatch)
    from apps.control import service
    from tests.control.test_accounts import W, sign_in, with_redis
    from tests.control.test_materials import upload, word_file
    with app_factory() as c:
        with_redis(c)
        sign_in(c, "+919988776655")
        assert upload(c, "Cafe quotation.docx", word_file()).status_code == 200
        c.portal.call(service.S.materials.settle)
        service.S.orch.big_docs = True
        opened = open_call(c, "bf-1").json()
        assert opened["files"] == ["Cafe quotation.docx"]
        service.S.orch.s.sessions["bf-1"].plan = "test"
        c.post("/internal/sessions/bf-1/intake", json={"transcript": "Caller: MUDRA loan project report"}, headers=H)
        res = c.post("/internal/commands", json=create_cmd("bf-1", opened["epoch"]), headers=H).json()
        assert res["status"] == "accepted" and res["files_used"] == ["Cafe quotation.docx"]
        assert c.get("/api/files", headers=W).json() == []  # nothing waits any more
        assert [f["name"] for f in c.get(f"/api/files?doc_id={res['doc_id']}").json()] == ["Cafe quotation.docx"]
        mats = c.portal.call(service.S.materials.for_doc, res["doc_id"])
        assert mats[0]["status"] == "ready" and "coffee machine" in mats[0]["text"]
