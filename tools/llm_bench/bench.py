"""Talker: our system prompt, [STATE] note and tools, eight scenarios (Hinglish request, confirming details ->
create_document with PDF + Word, a Tamil caller, a voice edit -> edit_document, an English request, and a returning
caller with earlier documents: a new request -> create_document, "pichhli wali" -> edit it (or confirm it first), a
change to this call's document -> edit_document; the writer's question for a missing fact: the answer and "I don't
remember" -> a short acknowledgement and no tool (the reply goes to the writer by code), a change instead ->
edit_document). Thinker.

Any endpoint from protocol/llm.py works; a Bedrock key file adds Amazon Bedrock (Mumbai, OpenAI-compatible):
  .venv312/bin/python tools/llm_bench/bench.py --bedrock-key amazon_bedrock.txt \\
      --talker sarvam:sarvam-105b bedrock:qwen.qwen3-235b-a22b-2507 ... --thinker sarvam:sarvam-105b ... --reps 3
Writes a JSON of every call and prints a summary table. Keys are read from files and never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages"))

from apps.talker.bot import TOOLS as BOT_TOOLS  # noqa: E402
from apps.talker.logic.claims import is_completion_claim, split_sentences  # noqa: E402
from apps.talker.logic.language import LanguageTracker, detect  # noqa: E402
from apps.talker.logic.scripts import gurmukhi_to_devanagari  # noqa: E402
from apps.talker.logic.state_block import (  # noqa: E402
    approval_line,
    build_state_block,
    earlier_documents_line,
    intake_line,
)
from apps.talker.prompts import GREETING, SYSTEM_PROMPT  # noqa: E402
from protocol.llm import LLMEndpoint, endpoint  # noqa: E402

BEDROCK_URL = "https://bedrock-mantle.ap-south-1.api.aws/v1"
# Per-model request extras: reasoning models must think briefly or the caller waits.
EXTRAS = {"openai.gpt-oss-120b": {"reasoning_effort": "low"}, "openai.gpt-oss-20b": {"reasoning_effort": "low"}}

# The talker's own tool schemas (apps/talker/bot.py), so the benchmark tests what the calls use.
TOOLS = [{"type": "function", "function": f.to_default_dict()} for f in BOT_TOOLS.standard_tools]

NO_DOCS = {"session_id": "s", "epoch": 1, "state": "converse", "verified_recipients": ["+919988776655"],
           "docs": [], "tasks": [], "question": None, "last_seq": 0}
ONE_DOC = {**NO_DOCS, "docs": [{"id": "doc_1", "title": "Leave Application", "head_version": 1,
                                "formats": ["pdf", "docx"], "deliveries": []}]}

# A returning caller: earlier calls left documents.
TODAY, LAST_WEEK = "2026-10-22T03:40:00+00:00", "2026-10-19T10:00:00+00:00"
EARLIER = [{"id": f"old_{i}", "title": "Leave Application", "head_version": 2, "updated_at": TODAY} for i in range(5)]
EARLIER += [{"id": "old_9", "title": "GST Invoice for Sharma Traders", "head_version": 1, "updated_at": LAST_WEEK}]
EARLIER_LINE = earlier_documents_line(EARLIER, set())
ASK_DETAILS = "Zaroor. Aapka naam aur company ka naam bata dijiye, aur PDF chahiye ya Word?"
# The document writer needs a fact the conversation lacked, and BolDoc has just asked for it.
WRITER_ASKS = {**NO_DOCS, "docs": [{"id": "doc_1", "title": "", "head_version": 0, "formats": ["pdf", "docx"],
                                    "deliveries": []}],
               "tasks": [{"id": "task_1", "kind": "create", "doc_id": "doc_1", "state": "awaiting_input",
                          "tier": "T2", "queued_requests": 0, "created_at": 0}],
               "question": {"id": "q_1", "text": "What is your employee ID?", "default": "", "task_id": "task_1"}}
ASKED = [("user", "मुझे अपने HR manager के लिए leave application चाहिए, 25 से 27 October, बहन की शादी है। मेरा नाम सौरभ "
                  "कुमार है। PDF और Word में लिख दीजिए।"),
         ("call", "create_document", {"summary": "Leave application to the HR manager, 25-27 October, sister's "
                                                 "wedding, from Saurabh Kumar", "formats": ["pdf", "docx"],
                                      "language": "en-IN"},
          {"status": "started", "done": False, "eta_seconds": 15,
           "sms": "the link goes by SMS to the number ending 6655 when it is ready",
           "say": "Tell the caller you are writing it now and about how long it takes. It is NOT ready yet."}),
         ("assistant", "Ji, likh rahi hoon, bas pandrah second. Ek cheez aur chahiye: aapka employee ID kya hai?")]
READ_BACK = r"likh\s*doon|लिख\s*दूँ|लिख\s*दूं"  # the document is already being written
# The writer's intake checklist in [STATE] (apps/thinker/intake.py): ask what is missing, write when all is given,
# and never write what isn't allowed.
LOAN = {"document": "a bank loan application", "allowed": True, "facts": [
    {"label": "bank", "question": "Which bank?", "value": "SBI", "required": True},
    {"label": "loan amount", "question": "How much?", "value": "5 crore", "required": True},
    {"label": "loan purpose", "question": "What is the loan for?", "value": "", "required": True},
    {"label": "applicant name", "question": "Your name?", "value": "Saurabh Kumar", "required": True}]}
LOAN_ALL = dict(LOAN, facts=[dict(f, value=f["value"] or "a new house") for f in LOAN["facts"]])
CERT = {"document": "a doctor's medical certificate", "allowed": False,
        "refusal": "it would pretend to come from a doctor; BolDoc can write a medical leave application instead"}
LOAN_TALK = [("user", "मुझे bank को loan application लिखनी है, पाँच करोड़ का, SBI से। मेरा नाम सौरभ कुमार है। लिख दीजिए।")]
SCENARIOS: List[Dict[str, Any]] = [
    {"name": "hinglish_request", "snap": NO_DOCS, "turns": [
        ("user", "मुझे अपने HR manager के लिए leave application चाहिए, 25 से 27 October तक, बहन की शादी है।")],
     "expect_tool": None, "scripts": {"hi-IN", "en-IN"}},
    {"name": "confirm_create", "snap": NO_DOCS, "turns": [
        ("user", "मुझे अपने HR manager के लिए leave application चाहिए, 25 से 27 October तक, बहन की शादी है।"),
        ("assistant", ASK_DETAILS),
        ("user", "मेरा नाम सौरभ कुमार है, Acme Technologies में Senior Engineer हूँ। PDF और Word दोनों चाहिए। "
                 "हाँ, यही लिख दीजिए।")],
     # read back first, even after "likh dijiye"
     "expect_tool": None, "must_say_any": ("likh doon", "likh du", "likh dun", "लिख दूँ", "लिख दूं"),
     "scripts": {"hi-IN", "en-IN"}},
    {"name": "tamil_caller", "snap": NO_DOCS, "turns": [
        ("user", "எனக்கு ஒரு விடுப்பு கடிதம் வேண்டும். அக்டோபர் 25 முதல் 27 வரை, என் தங்கையின் திருமணம்.")],
     "expect_tool": None, "scripts": {"ta-IN"}, "extra": "caller's language: Tamil (ta-IN). Reply in Tamil, in its "
     "own script, short and natural; keep English words people use in Tamil (PDF, Word) as they are."},
    # a change is said back and confirmed before it is made
    {"name": "voice_edit", "snap": ONE_DOC, "turns": [
        ("user", "मुझे leave application चाहिए, 25 से 27 October."), ("assistant", "Leave application ready hai. "
         "Link SMS par bhej diya. Kuch badalna hai?"),
        ("user", "हाँ, छुट्टी 26 से 27 October तक कर दीजिए।")],
     "expect_tool": None, "must_say_any": ("kar doon", "kar du", "kar dun", "कर दूँ", "कर दूं", "badal doon",
                                           "badal du", "बदल दूँ"), "scripts": {"hi-IN", "en-IN"}},
    {"name": "voice_edit_confirmed", "snap": ONE_DOC, "turns": [
        ("user", "मुझे leave application चाहिए, 25 से 27 October."), ("assistant", "Leave application ready hai. "
         "Link SMS par bhej diya. Kuch badalna hai?"),
        ("user", "हाँ, छुट्टी 26 से 27 October तक कर दीजिए।"),
        ("assistant", "Toh chhutti 25 ki jagah 26 se 27 October kar doon?"), ("user", "हाँ")],
     "extra": approval_line("change"), "expect_tool": "edit_document", "scripts": {"hi-IN", "en-IN"}},
    {"name": "returning_new_doc", "snap": NO_DOCS, "extra": EARLIER_LINE, "turns": [
        ("user", "मुझे अपने HR manager के लिए leave application चाहिए, 25 से 27 October तक, बहन की शादी है।"),
        ("assistant", ASK_DETAILS),
        ("user", "मेरा नाम सौरभ कुमार है, Acme Technologies में Senior Engineer हूँ। PDF और Word दोनों चाहिए। "
                 "हाँ, यही लिख दीजिए।")],
     "expect_tool": "create_document", "scripts": {"hi-IN", "en-IN"}},
    {"name": "returning_edit_earlier", "snap": NO_DOCS, "extra": EARLIER_LINE, "turns": [
        ("user", "पिछली बार वाली leave application में छुट्टी 26 से 27 October कर दीजिए।")],
     "expect_tool": ("edit_document", None), "must_say": "leave",  # editing it, or confirming it first
     "scripts": {"hi-IN", "en-IN"}},
    {"name": "returning_edit_current", "snap": ONE_DOC, "extra": EARLIER_LINE, "turns": [
        ("user", "मुझे leave application चाहिए, 25 से 27 October."), ("assistant", "Leave application ready hai. "
         "Link SMS par bhej diya. Kuch badalna hai?"),
        ("user", "हाँ, छुट्टी 26 से 27 October तक कर दीजिए।")],
     "expect_tool": "edit_document", "scripts": {"hi-IN", "en-IN"}},
    {"name": "writer_question_answer", "snap": WRITER_ASKS, "turns": [*ASKED, ("user", "मेरा employee ID E 4521 है।")],
     # the reply goes to the writer by code; an edit_document with the answer too is a harmless duplicate
     "expect_tool": (None, "edit_document"), "expect_arg": "4521", "forbid": READ_BACK, "scripts": {"hi-IN", "en-IN"}},
    {"name": "writer_question_skip", "snap": WRITER_ASKS,
     "turns": [*ASKED, ("user", "वो मुझे अभी याद नहीं है, उसे छोड़ दीजिए।")],
     "expect_tool": None, "forbid": READ_BACK, "scripts": {"hi-IN", "en-IN"}},
    {"name": "writer_question_change", "snap": WRITER_ASKS,
     "turns": [*ASKED, ("user", "एक मिनट, पहले छुट्टी 26 से 27 October कर दीजिए।")],  # the writer gets it too
     "expect_tool": ("edit_document", None), "forbid": READ_BACK, "scripts": {"hi-IN", "en-IN"}},
    {"name": "intake_missing", "snap": NO_DOCS, "extra": intake_line(LOAN), "turns": LOAN_TALK,
     "expect_tool": None, "must_say_any": ("purpose", "kaam", "kis liye", "kyon", "wajah", "उद्देश्य", "किस", "क्यों",
                                           "kis cheez", "kisliye"), "scripts": {"hi-IN", "en-IN"}},
    {"name": "intake_complete", "snap": NO_DOCS, "extra": intake_line(LOAN_ALL),
     "turns": [("user", "मुझे bank को loan application लिखनी है, पाँच करोड़ का, SBI से, नया घर लेने के लिए। मेरा नाम "
                        "सौरभ कुमार है। PDF में लिख दीजिए।")],
     # writing now, or one read-back and "Likh doon?" first (never more questions: the checklist is complete)
     "expect_tool": ("create_document", None), "formats": {"pdf"}, "must_say_any": ("likh doon", "लिख दूँ", "लिख दूं"),
     "scripts": {"hi-IN", "en-IN"}},
    {"name": "intake_refused", "snap": NO_DOCS, "extra": intake_line(CERT),
     "turns": [("user", "मुझे doctor का medical certificate चाहिए कि मैं पाँच दिन बीमार था, office में देना है।")],
     "expect_tool": None, "must_say_any": ("leave", "application", "छुट्टी", "आवेदन", "chhutti"),
     "scripts": {"hi-IN", "en-IN"}},
    {"name": "english_request", "snap": NO_DOCS, "turns": [
        ("user", "Hi, I need an invoice for twelve thousand rupees plus GST for Sharma Traders, for website work.")],
     "expect_tool": None, "scripts": {"en-IN"}},
]
# A Hindi speaker whose words speech-to-text wrote in Gurmukhi. When the caller is done the code decides (talker
# session._decide_done) and [STATE] asks for the goodbye:
GOODBYE = ("the caller wants nothing more: say a short, warm goodbye in one sentence (their document is on the SMS "
           "link). Don't ask anything; the call ends after it.")
BYE_WORDS = ("dhanyavaad", "dhanyawad", "shukriya", "thank", "namaste", "bye", "alvida", "aaram", "dhyan", "khayal",
             "धन्यवाद", "शुक्रिया", "नमस्ते")
_PA_REPLY = "ਸਤ ਸ੍ਰੀ ਅਕਾਲ ਜੀ! ਦੱਸੋ, ਅੱਜ ਮੈਂ ਤੁਹਾਡੇ ਲਈ ਕੀ ਲਿਖਾਂ?"
SCENARIOS += [
    {"name": "gurmukhi_hello", "snap": NO_DOCS, "turns": [("user", "ਹੈਲੋ ਜੀ")], "expect_tool": None,
     "scripts": {"hi-IN", "en-IN"}},
    {"name": "gurmukhi_then_hindi", "snap": NO_DOCS, "turns": [
        ("user", "ਹੈਲੋ ਜੀ"), ("assistant", _PA_REPLY), ("user", "मुझे एक letter लिखना है बैंक के लिए")],
     "expect_tool": None, "scripts": {"hi-IN", "en-IN"}},
    {"name": "confirm_create_after_readback", "snap": NO_DOCS, "extra": approval_line("write"), "turns": [
        ("user", "मुझे अपने HR manager के लिए leave application चाहिए, 25 से 27 October तक, बहन की शादी है। मेरा नाम "
                 "सौरभ कुमार है, Acme Technologies में Senior Engineer हूँ।"),
        ("assistant", "Toh: Acme Technologies ke HR manager ko 25 se 27 October ki chhutti, behen ki shaadi, Saurabh "
                      "Kumar ke naam se. Likh doon?"), ("user", "हाँ, लिख दीजिए")],
     "expect_tool": "create_document", "scripts": {"hi-IN", "en-IN"}},
    # Asking again (or calling edit_document) is right; claiming it's done never is.
    *({"name": f"garbled_reply_to_kar_doon{tag}", "snap": ONE_DOC, "extra": extra, "turns": [
        ("user", "मुझे leave application चाहिए, 25 से 27 October."), ("assistant", "Leave application ready hai. "
         "Link SMS par bhej diya. Kuch badalna hai?"),
        ("user", "छुट्टी 26 से 27 October तक कर दीजिए।"),
        ("assistant", "Toh chhutti 25 ki jagah 26 se 27 October kar doon?"), ("user", "How Give me the car.")],
      "expect_tool": (None, "edit_document"), "no_claim": True, "scripts": {"hi-IN", "en-IN"},
      # asked again as a change question (a "Likh doon?" yes would read as writing a new one), the note unsaid
      "must_say_any": ("kar doon", "kar du", "kar dun", "badal doon", "badal du", "badal dun", "कर दूँ", "कर दूं",
                       "बदल दूँ", "बदल दूं"), "forbid": r"\[STATE|nothing has changed|wasn'?t a yes"}
      for tag, extra in (("", approval_line(None, change_waiting=True)), ("_no_state_line", None))),
    {"name": "unclear_reply_to_likh_doon", "snap": NO_DOCS, "turns": [
        ("user", "LIC agent को letter लिखना है, loan के लिए। मेरा नाम Saurabh है।"),
        ("assistant", "Saurabh ji, toh: LIC agent ko loan application, aapke naam se. Likh doon?"),
        ("user", "is the name sir")],
     "expect_tool": None, "scripts": {"hi-IN", "en-IN"}},
    {"name": "done_nahi_karna", "snap": ONE_DOC, "turns": [
        ("user", "मुझे leave application चाहिए, 25 से 27 October."),
        ("assistant", "Saurabh ji, aapki Leave Application ready hai, link SMS par aa gaya hoga. Koi aur letter "
                      "likhna hai?"), ("user", "Nahi karna")],
     "extra": GOODBYE, "expect_tool": (None, "end_call"), "must_say_any": BYE_WORDS, "scripts": {"hi-IN", "en-IN"}},
    {"name": "done_night", "snap": ONE_DOC, "turns": [
        ("user", "मुझे leave application चाहिए, 25 से 27 October."),
        ("assistant", "Saurabh ji, aapki Leave Application ready hai, link SMS par aa gaya hoga. Koi aur letter "
                      "likhna hai?"), ("user", "Night")],
     "extra": GOODBYE, "expect_tool": (None, "end_call"), "must_say_any": BYE_WORDS, "scripts": {"hi-IN", "en-IN"}},
    {"name": "done_but_asks", "snap": ONE_DOC, "turns": [
        ("user", "मुझे leave application चाहिए, 25 से 27 October."),
        ("assistant", "Saurabh ji, aapki Leave Application ready hai, link SMS par aa gaya hoga. Koi aur letter "
                      "likhna hai?"), ("user", "Isme kya likha hai?")],
     "expect_tool": None, "scripts": {"hi-IN", "en-IN"}},
    {"name": "asks_hindi_in_gurmukhi", "snap": NO_DOCS, "extra": LanguageTracker(chosen="hi-IN").prompt_line(),
     "turns": [("user", "ਹੈਲੋ ਜੀ"), ("assistant", _PA_REPLY), ("user", "ਮੈਂ ਹਿੰਦੀ ਸਪੀਕਰ ਹਾਂ")],
     "expect_tool": None, "scripts": {"hi-IN", "en-IN"}},
    # what the model gets now (processors.EchoFilter): the request back in Devanagari, BolDoc's earlier Punjabi
    # reply still in its history (it had switched on a real Punjabi sentence)
    {"name": "asks_hindi_converted", "snap": NO_DOCS, "extra": LanguageTracker(chosen="hi-IN").prompt_line(),
     "turns": [("user", "ਮੈਨੂੰ ਇੱਕ ਲੈਟਰ ਲਿਖਣਾ ਹੈ ਬੈਂਕ ਲਈ"), ("assistant", _PA_REPLY),
               ("user", gurmukhi_to_devanagari("ਮੈਂ ਹਿੰਦੀ ਸਪੀਕਰ ਹਾਂ"))],
     "expect_tool": None, "scripts": {"hi-IN", "en-IN"}},
]


def messages_for(sc: Dict[str, Any]) -> List[Dict[str, str]]:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "assistant", "content": GREETING}]
    for turn in sc["turns"]:
        if turn[0] == "call":  # ("call", tool, arguments, result): a tool BolDoc already called on this call
            cid = f"call_{len(msgs)}"
            msgs.append({"role": "assistant", "content": "", "tool_calls": [{
                "id": cid, "type": "function",
                "function": {"name": turn[1], "arguments": json.dumps(turn[2], ensure_ascii=False)}}]})
            msgs.append({"role": "tool", "tool_call_id": cid, "content": json.dumps(turn[3], ensure_ascii=False)})
        else:
            msgs.append({"role": turn[0], "content": turn[1]})
    msgs.append({"role": "system", "content": build_state_block(sc["snap"], mode="converse", extra=sc.get("extra"))})
    return msgs


async def talker_call(client: httpx.AsyncClient, ep: LLMEndpoint, sc: Dict[str, Any]) -> Dict[str, Any]:
    body = {"model": ep.model, "messages": messages_for(sc), "tools": TOOLS, "temperature": 0.5, "max_tokens": 300,
            "stream": True, **ep.extra, **EXTRAS.get(ep.model, {})}
    t0 = time.perf_counter()
    first_any = first_answer = None
    text, tool_name, tool_args, err = [], None, [], None
    try:
        async with client.stream("POST", ep.chat_url, json=body, headers=ep.headers(), timeout=60) as r:
            if r.status_code != 200:
                err = f"HTTP {r.status_code}: {(await r.aread()).decode(errors='replace')[:160]}"
            else:
                async for line in r.aiter_lines():
                    if not line.startswith("data:") or line.strip() == "data: [DONE]":
                        continue
                    chunk = json.loads(line[5:])
                    for ch in chunk.get("choices") or []:
                        d = ch.get("delta") or {}
                        now = time.perf_counter() - t0
                        if first_any is None and (d.get("content") or d.get("reasoning_content") or d.get("reasoning")
                                                  or d.get("tool_calls")):
                            first_any = now
                        if d.get("content"):
                            first_answer = first_answer or now
                            text.append(d["content"])
                        for tc in d.get("tool_calls") or []:
                            first_answer = first_answer or now
                            fn = tc.get("function") or {}
                            tool_name = tool_name or fn.get("name")
                            if fn.get("arguments"):
                                tool_args.append(fn["arguments"])
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:120]}"
    total = time.perf_counter() - t0
    reply = "".join(text).strip()
    args: Any = "".join(tool_args)
    with contextlib.suppress(ValueError):
        args = json.loads(args) if args else {}
    return {"ttfa": first_answer, "first_any": first_any, "total": total, "reply": reply, "tool": tool_name,
            "args": args, "error": err}


def judge(sc: Dict[str, Any], out: Dict[str, Any]) -> Dict[str, Any]:
    allowed = sc["expect_tool"] if isinstance(sc["expect_tool"], tuple) else (sc["expect_tool"],)
    ok_tool = out["tool"] in allowed
    if ok_tool and out["tool"] is None and sc.get("must_say"):  # asked first instead: it must name the document
        ok_tool = sc["must_say"] in out["reply"].lower()
    if ok_tool and out["tool"] == "create_document":
        fm = set((out["args"] or {}).get("formats") or []) if isinstance(out["args"], dict) else set()
        ok_tool = sc.get("formats", {"pdf", "docx"}) <= fm
    if ok_tool and out["tool"] is None and sc.get("must_say_any"):
        ok_tool = any(w in out["reply"].lower() for w in sc["must_say_any"])
    if ok_tool and out["tool"] == "edit_document":
        ok_tool = sc.get("expect_arg", "26") in json.dumps(out["args"], ensure_ascii=False).lower()
    if ok_tool and sc.get("forbid"):
        ok_tool = not re.search(sc["forbid"], out["reply"], re.I)
    if ok_tool and sc.get("no_claim"):  # "kar di hai" with nothing done (the claim guard's own test)
        ok_tool = not any(is_completion_claim(x) for x in split_sentences(out["reply"] + " ")[0])
    lang = detect(out["reply"]) if out["reply"] else None
    ok_lang = (lang in sc["scripts"]) if out["reply"] else (out["tool"] is not None)
    return {"tool_ok": ok_tool, "lang_ok": ok_lang, "words": len(out["reply"].split())}


THINKER_TRANSCRIPT = (
    "BolDoc: Namaste! Main BolDoc hoon. Aaj kya likhna hai?\nCaller: मुझे अपने HR manager के लिए एक leave application "
    "लिखनी है।\nBolDoc: Aapka naam kya hai aur kaunsi chutti chahiye?\nCaller: 25 से 27 October तक छुट्टी चाहिए। मेरी "
    "बहन की शादी है जयपुर में। मेरा नाम सौरभ कुमार है, मैं Acme Technologies में Senior Engineer हूँ।\nBolDoc: Samajh gayi. "
    "English mein likhun?\nCaller: हाँ, English में, PDF और Word दोनों।")


async def thinker_call(client: httpx.AsyncClient, ep: LLMEndpoint) -> Dict[str, Any]:
    from apps.thinker import drafter
    from apps.thinker.llm import chat_json
    user = (f"DOCUMENT LANGUAGE: English (en-IN).\nToday's date: 22 October 2026.\nRequested formats: pdf, docx.\n"
            f"What the caller wants (agent's summary, may be imprecise): leave application 25-27 October\n\n"
            f"Verbatim conversation (source of truth for facts):\n{THINKER_TRANSCRIPT}")
    ep2 = LLMEndpoint(ep.provider, ep.model, ep.base_url, ep.api_key, ep.auth,
                      {**ep.extra, **EXTRAS.get(ep.model, {})} if ep.provider != "sarvam" else {})
    t0 = time.perf_counter()
    try:
        doc, stats = await chat_json(ep2, drafter.SYSTEM, user, lambda raw: drafter._to_document(raw, "doc_b", "en-IN"),
                                     reasoning=None, client=client, max_tokens=4000)  # as production drafts
        return {"ok": True, "s": time.perf_counter() - t0, "attempts": stats["attempts"], "usage": stats["usage"],
                "title": doc.title, "subject": doc.meta.get("subject"), "blocks": len(doc.blocks),
                "first_para": next((b.model_dump().get("runs", [{}])[0].get("text", "") for b in doc.blocks
                                    if b.type == "paragraph"), "")[:160]}
    except Exception as e:
        return {"ok": False, "s": time.perf_counter() - t0, "error": f"{type(e).__name__}: {str(e)[:160]}"}


def pct(xs: List[float], q: float) -> Optional[float]:
    xs = sorted(x for x in xs if x is not None)
    return round(xs[min(len(xs) - 1, round(q * (len(xs) - 1)))], 3) if xs else None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--talker", nargs="*", default=[])
    ap.add_argument("--thinker", nargs="*", default=[])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--bedrock-key", default="")
    ap.add_argument("--out", default="llm_bench.json")
    ap.add_argument("--scenarios", nargs="*", default=[], help="only these talker scenarios (by name)")
    a = ap.parse_args()
    scenarios = [sc for sc in SCENARIOS if not a.scenarios or sc["name"] in a.scenarios]
    if a.bedrock_key:
        providers = json.loads(os.environ.get("LLM_PROVIDERS", "{}") or "{}")
        providers["bedrock"] = {"base_url": BEDROCK_URL, "key_file": a.bedrock_key}
        os.environ["LLM_PROVIDERS"] = json.dumps(providers)
    os.environ.setdefault("SARVAM_API_KEY_FILE", str(ROOT / "sarvam_key.txt"))
    results: Dict[str, Any] = {"talker": {}, "thinker": {}}
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as client:
        for spec in a.talker:
            ep = endpoint("talker", spec)
            rows = []
            for rep in range(a.reps):
                for sc in scenarios:
                    out = await talker_call(client, ep, sc)
                    rows.append({"scenario": sc["name"], "rep": rep, **out, **judge(sc, out)})
                    if ep.provider == "sarvam":
                        await asyncio.sleep(1.6)  # stay well under 40 requests/min (live calls share it)
            results["talker"][spec] = rows
            ok = [r for r in rows if not r["error"]]
            ttfa = [r["ttfa"] for r in ok]
            tools_ok, lang_ok = sum(r["tool_ok"] for r in ok), sum(r["lang_ok"] for r in ok)
            words = round(statistics.mean([r["words"] for r in ok]), 1) if ok else "-"
            print(f"{spec:42s} first word p50 {pct(ttfa, .5)} s  p90 {pct(ttfa, .9)} s  tools {tools_ok}/{len(rows)}"
                  f"  language {lang_ok}/{len(rows)}  words {words}  errors {len(rows) - len(ok)}", flush=True)
            for sc in scenarios:
                mine = [r for r in rows if r["scenario"] == sc["name"]]
                print(f"    {sc['name']:24s} tools {sum(r['tool_ok'] for r in mine)}/{len(mine)}  called "
                      f"{[r['tool'] or '-' for r in mine]}", flush=True)
        for spec in a.thinker:
            ep = endpoint("thinker", spec)
            rows = [await thinker_call(client, ep) for _ in range(max(1, a.reps - 1))]
            results["thinker"][spec] = rows
            good = [r for r in rows if r["ok"]]
            print(f"{spec:42s} draft ok {len(good)}/{len(rows)}  time p50 {pct([r['s'] for r in good], .5)} s  "
                  f"subject: {good[0]['subject'] if good else rows[0].get('error')}", flush=True)
    Path(a.out).write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
