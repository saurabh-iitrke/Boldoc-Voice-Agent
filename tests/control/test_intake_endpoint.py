"""The intake endpoint: the talker's checklist for the document being asked for, and requests BolDoc refuses."""
import pytest

from apps.thinker.intake import Fact, Intake
from tests.control.helpers import H, create_cmd, open_call

pytest.importorskip("asyncpg")


def fake_prepare(result):
    async def prepare(api_key, transcript, known=None, client=None, written=None, earlier=None):
        prepare.seen = transcript
        return result, {"latency_ms": 900}
    return prepare


def fake_sense(verdict="ok", ask="", to=""):
    async def sense_check(api_key, transcript, client=None):
        sense_check.seen = transcript
        return {"to": to, "verdict": verdict, "ask": ask}, {"latency_ms": 500}
    return sense_check


def test_the_checklist_comes_back_and_a_refused_request_cannot_be_written(app_factory, monkeypatch):
    from apps.thinker import intake
    monkeypatch.setattr(intake, "sense_check", fake_sense())
    loan = Intake("bank loan application", True, "", [Fact("loan purpose", "What is the loan for?", "", True)])
    monkeypatch.setattr(intake, "prepare", fake_prepare(loan))
    with app_factory() as c:
        epoch = open_call(c, "i-1").json()["epoch"]
        r = c.post("/internal/sessions/i-1/intake", json={"transcript": "Caller: loan application chahiye"},
                   headers=H).json()
        assert r["intake"]["document"] == "bank loan application" and r["ms"] == 900
        assert r["intake"]["facts"][0]["label"] == "loan purpose"
        cert = Intake("medical certificate", False, "It would pretend to come from a doctor.", [])
        monkeypatch.setattr(intake, "prepare", fake_prepare(cert))
        c.post("/internal/sessions/i-1/intake", json={"transcript": "Caller: doctor ka certificate"}, headers=H)
        res = c.post("/internal/commands", json=create_cmd("i-1", epoch), headers=H).json()
        assert res["status"] == "rejected" and res["reason"] == "not_allowed"   # not only the talker's word
        assert c.post("/internal/sessions/nope/intake", json={"transcript": "x"}, headers=H).status_code == 404


def test_a_request_nobody_would_send_comes_back_with_the_question_to_ask_first(app_factory, monkeypatch):
    """"write an application to the cow" was written as said."""
    from apps.thinker import intake
    monkeypatch.setattr(intake, "prepare", fake_prepare(Intake("complaint application", True, "", [])))
    monkeypatch.setattr(intake, "sense_check", fake_sense(
        "check", "Gaay ko application? Ya doodh wale ko shikayat likhni hai?", "the cow"))
    with app_factory() as c:
        open_call(c, "i-2")
        r = c.post("/internal/sessions/i-2/intake", json={"transcript": "Caller: write an application to the cow"},
                   headers=H).json()
        assert r["sense"] == {"to": "the cow", "verdict": "check",
                              "ask": "Gaay ko application? Ya doodh wale ko shikayat likhni hai?"}

        async def broken(api_key, transcript, client=None):
            raise RuntimeError("model down")
        monkeypatch.setattr(intake, "sense_check", broken)  # a failed check never blocks writing
        r = c.post("/internal/sessions/i-2/intake", json={"transcript": "Caller: leave application"}, headers=H).json()
        assert r["sense"]["verdict"] == "ok" and r["intake"]["document"] == "complaint application"


def test_the_sense_check_parses_strictly():
    from apps.thinker.intake import _parse_sense
    assert _parse_sense({"to": "the cow", "verdict": "check", "ask": "Gaay ko?"})["verdict"] == "check"
    assert _parse_sense({"to": "x", "verdict": "check", "ask": ""})["verdict"] == "ok"  # nothing to ask: no check
    assert _parse_sense({"to": "HR", "verdict": "ok", "ask": "stray"})["ask"] == ""
    with pytest.raises(ValueError):
        _parse_sense({"verdict": "maybe"})
