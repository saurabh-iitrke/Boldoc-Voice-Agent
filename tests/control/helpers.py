"""Shared helpers for the control-plane tests (fixtures live in conftest.py)."""
import asyncio
import time
from typing import ClassVar, List

from docengine.model import Document, Paragraph, Run, Signature

H = {"X-Internal-Key": "test-key"}
CALLER = "919988776655"


class FakeSms:
    sent: ClassVar[List[tuple]] = []

    def __init__(self, *a, **k):
        pass

    async def send(self, dst, text):
        FakeSms.sent.append((dst, text))
        return f"msg-{len(FakeSms.sent)}"

    async def aclose(self):
        pass


async def fake_draft(api_key, transcript, intent, doc_id, formats, doc_lang="en-IN", **k):
    await asyncio.sleep(0.05)
    return Document(id=doc_id, title="Leave Application", doc_type="application", meta={"subject": "Leave"},
                    blocks=[Paragraph(runs=[Run(text="I request leave from 25 to 27 October.")]),
                            Signature(closing="Yours sincerely,", name="Saurabh Kumar")]), {"attempts": 1}


async def fake_edit(api_key, base, instructions, transcript, doc_lang, formats, intent=""):
    first = base.blocks[0]
    new = base.model_copy(deep=True)
    new.blocks[0] = first.model_copy(update={"runs": [Run(text="I request leave from 26 to 27 October.")]})
    return new, [f"updated the paragraph ({instructions[0]})"], {"tier": "T1"}


def wait_for(client, sid, after, type_, timeout=15.0):
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        r = client.get(f"/internal/sessions/{sid}/events", params={"after": after, "wait": 2}, headers=H).json()
        seen += r["events"]
        if r["events"]:
            after = r["events"][-1]["seq"]
        hit = [e for e in seen if e["type"] == type_]
        if hit:
            return hit[-1], after, r["snapshot"]
    raise AssertionError(f"no {type_} within {timeout}s; saw {[e['type'] for e in seen]}")


def until(pred, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def open_call(c, sid, caller=CALLER, **body):
    """What the talker does: admission on the answer webhook, then the session when the stream starts."""
    adm = c.post("/internal/calls/admit", json={"call_uuid": sid, "from": caller or ""}, headers=H).json()
    assert adm["decision"] == "admit", adm
    return c.post("/internal/sessions", json={"session_id": sid, **body}, headers=H)


def create_cmd(sid, epoch, turn="tc1"):
    return {"cmd": "create_doc", "session_id": sid, "epoch": epoch, "turn_id": turn, "formats": ["docx", "md"],
            "doc_lang": "en-IN", "intent": "leave application", "transcript": "Caller: leave chahiye 25-27 Oct"}
