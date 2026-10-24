"""The admin's test calls : signed-in admins only, one at a time, never the last free line, every run in the audit log,
and a run that can be followed from "calling" to its report's checks. The scripted call itself is faked here (it
stores a real trace, so the report is built as on the server); the step loop is tested in tests/talker."""
import asyncio
import time

import pytest

from tests.control.test_accounts import with_redis
from tests.control.test_admin import HOST, A, audit, make_admin, sign_in
from tests.control.test_report import EVENTS

pytest.importorskip("asyncpg")
pytest.importorskip("argon2")


class FakeCall:
    """Stands in for testcall.caller.ScriptedCall: says its steps, then leaves a trace like the talker does."""
    gate: asyncio.Event = None
    last_url = ""

    def __init__(self, key, url, scenario, call_id, on_event=None):
        self.url, self.scenario, self.call_id, self.on_event = url, scenario, call_id, on_event
        FakeCall.last_url = url

    async def run(self):
        from apps.control import service
        from apps.control.testcall.caller import CallResult, StepResult
        await self.on_event("connected", {})
        await self.on_event("greeting", {"seconds": 5.2, "audio": b"\xff" * 1600})
        await self.on_event("step", {"i": 1, "total": 2, "kind": "say", "text": "Namaste. Mujhe ek letter likhna hai."})
        if FakeCall.gate is not None:
            await FakeCall.gate.wait()  # held "mid-call" while the test looks at it
        await self.on_event("asked_again", {"i": 1, "heard": "Maaf kijiye, ek baar phir boliye?",
                                            "again": "हाँ जी, लिख दीजिए।"})
        await self.on_event("replied", {"i": 1, "label": "Namaste…", "latency": 0.91, "audio": b"\xff" * 800})
        await self.on_event("ended", {"by_bolDoc": True})
        await service.S.store.store_trace(self.call_id, {"call_id": self.call_id, "started_wall": time.time() - 30,
                                                         "content": True, "events": EVENTS})
        return CallResult(self.call_id, b"\xff" * 1600, [StepResult("Namaste…", 0.91, b"\xff" * 800)], True, 0)


@pytest.fixture
def tests_ready(monkeypatch):
    """The runner as the server makes it, with the scripted call and speech-to-text faked."""
    from apps.control.testcall import runner

    async def heard(key, audio):
        return "Namaste, main BolDoc hoon."
    monkeypatch.setattr(runner, "transcribe", heard)

    def ready():
        from apps.control.admin import api as admin_api
        t = admin_api.CTX.tests
        t.call_factory, t.sarvam_key, t.stream_key, t.report_wait_s = FakeCall, "sarvam-test", "stream-test", 3.0
        return t
    return ready


def wait_until_done(c, run_id, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = c.get(f"/admin/api/test-calls/{run_id}", headers=HOST).json()["run"]
        if run["status"] in ("passed", "failed", "error"):
            return run
        time.sleep(0.1)
    raise AssertionError("the test call never finished")


def test_an_admin_runs_one_test_call_at_a_time_and_follows_it_to_its_checks(app_factory, pg_url, capsys,
                                                                           tests_ready):
    with app_factory() as c:
        with_redis(c)
        tests_ready()
        assert c.get("/admin/api/test-calls", headers=HOST).status_code == 401  # signed out
        secret = make_admin(pg_url, capsys)
        assert sign_in(c, secret).status_code == 200
        assert c.post("/admin/api/test-calls", headers=HOST, json={"scenario": "doc"}).status_code == 403  # header
        assert c.post("/admin/api/test-calls", headers=A, json={"scenario": "karaoke"}).status_code == 422
        page = c.get("/admin/api/test-calls", headers=HOST).json()
        assert {x["id"] for x in page["choices"]} >= {"doc", "interrupt"} and page["running"] is None
        assert page["free_lines"] == 2 and [x["id"] for x in page["langs"]] == ["en-IN", "hi-IN"]

        async def new_gate():
            return asyncio.Event()

        async def open_gate():
            FakeCall.gate.set()
        FakeCall.gate = c.portal.call(new_gate)
        try:
            started = c.post("/admin/api/test-calls", headers=A, json={"scenario": "doc", "lang": "hi-IN"})
            assert started.status_code == 202
            run = started.json()["run"]
            assert run["status"] == "starting" and run["call_id"].startswith("test-") and run["lang"] == "hi-IN"
            deadline = time.time() + 5
            while time.time() < deadline:  # mid-call: the step being said, and the line so far
                mid = c.get(f"/admin/api/test-calls/{run['id']}", headers=HOST).json()["run"]
                if mid["step"] == 1:
                    break
                time.sleep(0.05)
            assert mid["status"] == "calling" and mid["now"].startswith("Namaste") and mid["total"] == 10
            assert any(line["who"] == "caller" for line in mid["lines"])
            again = c.post("/admin/api/test-calls", headers=A, json={"scenario": "interrupt"})
            assert again.status_code == 409 and again.json()["running"] == run["id"]  # one at a time
        finally:
            c.portal.call(open_gate)
            FakeCall.gate = None
        done = wait_until_done(c, run["id"])
        # the synthetic trace has dead air and nothing written: the doc scenario's checks say so
        assert done["status"] == "failed" and done["ended_at"] and done["error"] is None
        checks = {x["check"]: x["ok"] for x in done["result"]["checks"]}
        assert checks["a document was written"] is False and checks["no dead air > 3s"] is False
        assert done["result"]["p50_s"] is not None and done["summary"]["failed"] >= 2
        # BolDoc's words sit where it said them (transcribed later): the greeting, then each reply after its step
        said = [line["text"] for line in done["lines"]]
        assert said.index("BolDoc: Namaste, main BolDoc hoon.") == said.index("BolDoc greeted (5.2 s)") + 1
        assert said.index("BolDoc, step 1: Namaste, main BolDoc hoon.") == said.index("BolDoc answered in 0.91 s") + 1
        at = said.index("BolDoc, step 1: Maaf kijiye, ek baar phir boliye?")  # asked again: what, and the yes again
        assert said[at + 1:at + 3] == ["BolDoc asked again", "Caller: हाँ जी, लिख दीजिए।"]
        # the talker's stream, as the gate calls it: its key, a test call, the picked language
        assert "k=stream-test" in FakeCall.last_url and "test=1" in FakeCall.last_url
        assert FakeCall.last_url.endswith("&lang=hi-IN")
        listed = c.get("/admin/api/test-calls", headers=HOST).json()
        assert listed["runs"][0]["id"] == run["id"] and listed["running"] is None
        # the call is a test call in the record, so the console's call page shows its words
        from tests.control.test_accounts import sql
        rec = sql(pg_url, "select test, caller from call_records where call_uuid=$1", run["call_id"])
        assert rec == [{"test": True, "caller": "+918035449833"}]
        trail = [(actor, action, target, detail) for actor, action, target, detail in audit(pg_url)
                 if action.startswith("test_call")]
        assert trail[0] == ("ops", "test_call", run["id"], {"scenario": "doc", "lang": "hi-IN"})
        assert trail[1][:3] == ("ops", "test_call_finished", run["id"]) and trail[1][3]["result"] == "failed"
        assert "a document was written" in trail[1][3]["failed"]


def test_a_test_call_never_takes_the_last_free_line(app_factory, pg_url, capsys, tests_ready):
    with app_factory() as c:
        service = with_redis(c)
        tests_ready()
        secret = make_admin(pg_url, capsys)
        sign_in(c, secret)
        for call in ("real-1", "real-2"):  # two real calls on three lines: the third is a real caller's
            c.portal.call(service.S.bus.mark_active, call)
        r = c.post("/admin/api/test-calls", headers=A, json={"scenario": "interrupt"})
        assert r.status_code == 503 and r.json()["code"] == "no_free_line"
        assert c.get("/admin/api/test-calls", headers=HOST).json()["free_lines"] == 0
        assert not [a for a in audit(pg_url) if a[1].startswith("test_call")]  # nothing ran


def test_every_test_call_the_admin_can_pick_is_a_scripted_scenario():
    from apps.control.testcall.caller import EXPECT, SCENARIOS, lines_of
    from apps.control.testcall.runner import CHOICES
    assert set(CHOICES) <= set(SCENARIOS)
    assert EXPECT["sense"] == ("sense", "create")
    assert lines_of(SCENARIOS["sense"])[0].startswith("Namaste. Mujhe gaay ko")
