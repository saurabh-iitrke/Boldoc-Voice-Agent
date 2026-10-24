"""Real-model probe of the writer asking for missing facts, run on the server: no phone call, no SMS (an anonymous
test session). A conversation that leaves facts out -> the draft's placeholders -> one question at a time -> the
replies a caller might give -> the finished document, with timings.

  docker compose exec -T control python -m apps.control.probe
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Optional, Tuple

import asyncpg
import httpx

BASE = "http://localhost:8091"
TRANSCRIPT = (
    "BolDoc: Namaste! Main BolDoc, aapki AI assistant. Bataiye, aaj kya likhna hai?\n"
    "Caller: Mujhe apne bank ko letter likhna hai, apna savings account band karwana hai.\n"
    "BolDoc: Achha, account band karne ka letter. Kis bank mein account hai?\n"
    "Caller: State Bank of India mein.\n"
    "BolDoc: Aapka naam kya hai?\n"
    "Caller: Saurabh Kumar.\n"
    "BolDoc: Toh: SBI ko savings account band karne ka letter, aapke naam se. Likh doon?\n"
    "Caller: Haan, likh dijiye. PDF mein.")
# What a caller says to each kind of question (the first match wins); anything else: "I don't know, leave it".
REPLIES = [("account", "haan, account number hai 3 1 2 4 5 6 7 8 9 0"),
           ("branch", "branch ka naam abhi yaad nahi, chhod dijiye"),
           ("address", "Gomti Nagar, Lucknow"),
           ("phone", "nau aath saat chhe paanch chaar teen do ek shunya"),
           ("mobile", "nau aath saat chhe paanch chaar teen do ek shunya")]
DONT_KNOW = "pata nahi, chhod dijiye"


def caller_reply(question: str) -> str:
    return next((r for k, r in REPLIES if k in question.lower()), DONT_KNOW)


async def next_event(c: httpx.AsyncClient, sid: str, after: int, replied: Optional[str],
                     deadline: float) -> Tuple[Optional[dict], int, dict]:
    """The next question / completion / failure, or (None, after, snapshot) when the question just replied to is
    open again (the reply wasn't read as an answer)."""
    while time.monotonic() < deadline:
        r = (await c.get(f"/internal/sessions/{sid}/events", params={"after": after, "wait": 2})).json()
        for ev in r["events"]:
            after = max(after, ev["seq"])
            if ev["type"] in ("task.question", "task.completed", "task.failed"):
                return ev, after, r["snapshot"]
        q = r["snapshot"].get("question")
        if replied and q and q["id"] == replied:
            return None, after, r["snapshot"]
    raise TimeoutError("no event in time")


async def main() -> int:
    sid = f"probe-{int(time.time())}"
    async with httpx.AsyncClient(base_url=BASE, headers={"X-Internal-Key": os.environ["INTERNAL_KEY"]},
                                 timeout=30) as c:
        epoch = (await c.post("/internal/sessions", json={"session_id": sid, "test": True})).json()["epoch"]
        t0 = time.monotonic()
        created = (await c.post("/internal/commands", json={
            "cmd": "create_doc", "session_id": sid, "epoch": epoch, "turn_id": "p1", "formats": ["pdf"],
            "doc_lang": "en-IN", "intent": "Letter to SBI to close my savings account, from Saurabh Kumar",
            "transcript": TRANSCRIPT})).json()
        after, replied, n, done = 0, None, 0, None
        while done is None:
            ev, after, snap = await next_event(c, sid, after, replied, t0 + 240)
            at = time.monotonic() - t0
            if ev is None or ev["type"] == "task.question":
                if ev is None:
                    qid, text, reply = replied, snap["question"]["text"], DONT_KNOW
                    print(f"{at:6.1f}s   (not read as an answer: the question is open again)")
                else:
                    n += 1
                    qid, text = ev["payload"]["question_id"], ev["payload"]["text"]
                    reply = caller_reply(text)
                    print(f"{at:6.1f}s question {n}: {text}")
                await asyncio.sleep(2.5)  # BolDoc asks it; the caller answers
                res = (await c.post("/internal/commands", json={
                    "cmd": "answer", "session_id": sid, "epoch": epoch, "turn_id": f"reply-{qid}-{at:.0f}",
                    "question_id": qid, "value": reply})).json()
                print(f"{time.monotonic() - t0:6.1f}s   caller: {reply!r} -> {res['status']}")
                replied = qid
            else:
                done = ev
        await c.post(f"/internal/sessions/{sid}/end")
    print(f"{time.monotonic() - t0:6.1f}s {done['type']} {json.dumps(done.get('payload'), ensure_ascii=False)}")
    if done["type"] != "task.completed":
        return 1
    con = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        row = await con.fetchrow("select document from doc_versions where doc_id = $1 order by version desc limit 1",
                                 created["doc_id"])
    finally:
        await con.close()
    doc = json.loads(row["document"]) if isinstance(row["document"], str) else row["document"]
    print("\n" + doc["title"], json.dumps(doc["meta"], ensure_ascii=False))
    for b in doc["blocks"]:
        text = "".join(r.get("text", "") for r in b.get("runs", [])) or b.get("text") or b.get("name") or ""
        print(f"  [{b['type']}] {text}")
    print("assumptions:", doc.get("assumptions"))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
