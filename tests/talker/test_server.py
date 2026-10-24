"""Answer and hangup webhooks: admission decides the XML; the stream never carries identity; CDRs reach control."""
import dataclasses

import pytest

pytest.importorskip("pipecat")

from fastapi.testclient import TestClient

from apps.talker import server


@pytest.fixture
def client(monkeypatch):
    cfg = dataclasses.replace(server.SETTINGS, signature_mode="off", stream_key="sk", public_host="boldoc.in",
                              public_hosts=("boldoc.in", "www.boldoc.in"), max_call_s=1800)
    monkeypatch.setattr(server, "SETTINGS", cfg)
    return TestClient(server.app)


def test_admitted_call_streams_with_a_time_backstop_and_no_identity_in_headers(client, monkeypatch):
    seen = {}

    async def admit(body):
        seen.update(body)
        return {"decision": "admit", "max_call_s": 300, "first_call": True}
    monkeypatch.setattr(server, "admit", admit)
    r = client.post("/plivo/answer", data={"CallUUID": "uuid-1", "From": "919988776655", "To": "918035449833"},
                    headers={"host": "boldoc.in"})
    xml = r.text
    assert r.status_code == 200 and "<Stream" in xml and 'streamTimeout="360"' in xml
    assert 'extraHeaders="resumed=0"' in xml and "caller" not in xml  # identity comes from control, not headers
    assert "wss://boldoc.in/plivo/stream?k=sk" in xml and "https://boldoc.in/plivo/answer?resume=1" in xml
    assert seen == {"call_uuid": "uuid-1", "from": "919988776655", "to": "918035449833", "resumed": False}
    assert server.take_admission("uuid-1")["admission"]["first_call"] is True


def test_refused_call_hears_a_clip_on_the_host_plivo_called(client, monkeypatch):
    async def admit(body):
        return {"decision": "refuse", "reason": "busy", "clip": "busy"}
    monkeypatch.setattr(server, "admit", admit)
    r = client.post("/plivo/answer?resume=1", data={"CallUUID": "uuid-2", "From": "919988776655"},
                    headers={"host": "www.boldoc.in"})
    assert r.text == ("<Response><Play>https://www.boldoc.in/clips/busy.wav</Play><Hangup/></Response>")
    r = client.post("/plivo/answer", data={"CallUUID": "uuid-3", "From": "9190"}, headers={"host": "evil.example"})
    assert "https://boldoc.in/clips/" in r.text  # an unknown Host header never ends up in our XML


def test_control_outage_admits_phone_calls_degraded_but_not_browser_calls(client, monkeypatch):
    class Down:
        async def post(self, *a, **k):
            raise ConnectionError("control down")
    monkeypatch.setattr(server, "control", lambda: Down())
    r = client.post("/plivo/answer", data={"CallUUID": "uuid-4", "From": "919988776655"})
    assert 'streamTimeout="660"' in r.text  # 600 s degraded limit + 60 s backstop
    late = server.take_admission("uuid-4")["late_admit"]
    assert late == {"call_uuid": "uuid-4", "from": "919988776655", "to": "", "resumed": False}
    r = client.post("/plivo/answer", data={"CallUUID": "uuid-5", "From": "sip:web123@phone.plivo.com",
                                           "X-PH-Session": "tok"})
    assert "<Hangup/>" in r.text and server.take_admission("uuid-5") is None


def test_hangup_forwards_the_cdr_to_control(client, monkeypatch):
    sent = {}

    class Control:
        async def post(self, path, json=None, **k):
            sent["path"], sent["json"] = path, json

            class R:
                def raise_for_status(self):
                    pass
            return R()
    monkeypatch.setattr(server, "control", lambda: Control())
    r = client.post("/plivo/hangup", data={"CallUUID": "uuid-6", "From": "919988776655", "Duration": "42",
                                           "BillDuration": "60", "TotalCost": "0.00450", "HangupCauseName":
                                           "Normal Hangup", "Junk": "x"})
    assert r.text == "ok" and sent["path"] == "/internal/calls/uuid-6/hangup"
    assert sent["json"] == {"From": "919988776655", "Duration": "42", "BillDuration": "60", "TotalCost": "0.00450",
                            "HangupCauseName": "Normal Hangup"}


def test_signatures_are_checked_against_the_url_plivo_called(monkeypatch):
    from plivo.utils.signature_v3 import construct_post_url, get_signature_v3

    cfg = dataclasses.replace(server.SETTINGS, signature_mode="enforce", plivo_auth_token="tok",
                              public_host="boldoc.in", public_hosts=("boldoc.in", "www.boldoc.in"))
    monkeypatch.setattr(server, "SETTINGS", cfg)

    async def admit(body):
        return {"decision": "admit", "max_call_s": 300}
    monkeypatch.setattr(server, "admit", admit)
    c = TestClient(server.app)
    params = {"CallUUID": "uuid-7", "From": "919988776655"}
    for host in ("boldoc.in", "www.boldoc.in"):
        base = construct_post_url(f"https://{host}/plivo/answer", params).decode()
        sig = get_signature_v3(b"tok", base, b"nonce1").decode()
        r = c.post("/plivo/answer", data=params, headers={"host": host, "X-Plivo-Signature-V3": sig,
                                                          "X-Plivo-Signature-V3-Nonce": "nonce1"})
        assert r.status_code == 200, host
    r = c.post("/plivo/answer", data=params, headers={"host": "boldoc.in", "X-Plivo-Signature-V3": "bad",
                                                      "X-Plivo-Signature-V3-Nonce": "nonce1"})
    assert r.status_code == 403


def test_talker_llm_is_built_for_the_configured_provider():
    from apps.talker.bot import BolDocLLM, BolDocOpenAILLM, make_llm
    from protocol.llm import LLMEndpoint

    sarvam = make_llm(LLMEndpoint("sarvam", "sarvam-105b", "https://api.sarvam.ai/v1", "k",
                                  "header:api-subscription-key", {"reasoning_effort": None}), lambda: "[STATE]")
    other = make_llm(LLMEndpoint("india", "qwen3-32b", "https://llm.example.in/v1", "tok", "bearer", {}),
                     lambda: "[STATE]")
    assert isinstance(sarvam, BolDocLLM) and isinstance(other, BolDocOpenAILLM)
    assert sarvam.BOLDOC_LLM and other.BOLDOC_LLM  # the observer finds the talker's LLM either way


def test_the_stream_asks_plivo_for_noise_cancellation_at_95(monkeypatch):
    """(Plivo: noiseCancellation "true"/"false", noiseCancellationLevel 60-100, default 85)."""
    cfg = dataclasses.replace(server.SETTINGS, stream_key="sk")
    monkeypatch.setattr(server, "SETTINGS", cfg)
    xml = server.stream_xml("boldoc.in", 1800, False)
    assert 'noiseCancellation="true" noiseCancellationLevel="95" streamTimeout="1860"' in xml
    monkeypatch.setattr(server, "SETTINGS", dataclasses.replace(cfg, noise_cancellation=False))
    xml = server.stream_xml("boldoc.in", 1800, False)
    assert "noiseCancellation" not in xml and 'audio/x-mulaw;rate=8000" streamTimeout="1860"' in xml
