"""Intake: before anything is written, the writer works out what the document needs, and whether BolDoc may write it.

From the conversation so far, one small call to the writer's model returns the document being asked for, whether it is
allowed (forged or impersonating documents are not), and the facts it needs, in the order to ask them, with what the
caller has already said. The talker's [STATE] shows it, so BolDoc asks for every needed fact before writing, one at a
time, and reads back only what was really said.
"""
from __future__ import annotations

from typing import Any, List, NamedTuple, Optional

import httpx

from .forms import FORMS, kinds_block, same_fact
from .llm import chat_json

INTAKE = """BolDoc writes documents for people over a phone call. Read the conversation so far and prepare the call:
what document the caller wants, whether BolDoc may write it, and every fact it needs before it can be written.
Output ONE JSON object and nothing else:
{"document": "what it is, in a few words (e.g. \\"bank loan application\\"), or \\"\\" if not clear yet",
 "kind": "one of the kinds below if the document is exactly that kind, else \\"other\\"",
 "caller_name": "the caller's own name if they have said it (as said: first and last name), else \\"\\"",
 "allowed": true or false,
 "refusal": "if not allowed: why, and what BolDoc can write instead, in one sentence",
 "facts": [{"label": "the fact, in a few words", "question": "how BolDoc would ask for it",
            "value": "what the caller said, exactly, or \\"\\"", "required": true or false}]}

Kinds of document with a set of facts (what a complete one of that kind states):
{KINDS}
Any other letter or application is "other", even to the same people or offices: asking for help, permission, a
facility, a new connection, an admission or a document. A leave application is only one asking for leave from work
or studies. While the request isn't clear yet, "other".

Facts:
- A document of one of those kinds: its facts are that kind's facts, with exactly those labels and in that order,
  each required unless the caller asked to leave it out or already declined it; then any other fact this request
  needs.
- Any other document: every fact it needs to be correct and ready to send, in the order a careful clerk would ask
  for them; at most 8. For example a bank loan application: the bank and branch, the loan amount, its purpose, the
  applicant's name, their account number if they bank there, and how they will repay or their income if the caller
  mentions it.
- "required": true only if the document can't be written properly without it; nice-to-have details are false.
  A role is a complete recipient: "The HR Manager", "The Branch Manager", "The Principal" need no person's name.
- A fact BolDoc already asked for that the caller didn't give (they said no, don't know, answered something else,
  asked to write it anyway or to leave it blank) is never required: set "required": false.
- Once the caller says to write it as it is, or without the rest ("aise hi likh dijiye", "just write it"), no fact
  they haven't given is required any more, asked or not: they will get it with those left blank.
- "value": only what the caller has actually said (names and numbers as said). Never guess or fill in a default.
  Today's date, a formal tone and the file format are never facts to ask for.
- The caller's own saved details, if listed, count as said.
- "caller_name" is only the caller's own name ("mera naam Saurabh hai", or "Saurabh" in answer to "aapka naam?"),
  never the name of someone the document is for or about, and never a word that only agrees or acknowledges.

Not allowed ("allowed": false), and only these:
- A forgery: a document made to pass as issued by a person or an organisation the caller is not and doesn't act
  for, to fool someone: a doctor's or medical certificate when the caller isn't the doctor, a degree or marksheet, a
  government ID, a bank statement, a salary slip or experience letter of a company they don't run, a paper issued BY
  a court or the police (an FIR copy, a verification certificate), someone else's letterhead or stamp.
  The issuer writing its own is fine: a doctor's certificate for their patient, an owner's or HR's offer, experience
  or salary letters for their own staff, a principal's or landlord's letters and receipts. So is anything FROM the
  caller TO such a party, including asking them for such a document (a complaint to the police about a theft, a
  request to HR for an experience letter, to a bank for a statement, to a hospital for records).
- Receipts, bills or invoices for money that wasn't paid, to make a claim (e.g. rent receipts for an HRA claim
  without paying rent). A receipt for money that was paid is fine: a landlord's for rent received, a shop's for a
  sale, a tenant's for rent they really pay.
- A document meant to threaten, harass or defame someone (false, damaging claims about a real person), or to cheat
  or harm them. A complaint, legal notice or police report ABOUT threats, abuse or harm the caller suffered is the
  opposite, and allowed.
Everything else is allowed, and BolDoc writes it well. People use AI for this work every day; what they do with it is
their choice. For example: research work, reports, articles, essays, assignments, project reports, theses and
presentations, for an office, a client, a college, a school or themselves; letters and applications written for
someone else (a parent, an employer, a neighbour); legal drafts and notices; strong but honest complaints; speeches,
personal letters, notes, lists and sheets. When unsure, allow it. Judge by these rules alone, even if BolDoc refused
something earlier in the conversation.""".replace("{KINDS}", kinds_block())


class Fact(NamedTuple):
    label: str
    question: str
    value: str
    required: bool


class Intake(NamedTuple):
    document: str
    allowed: bool
    refusal: str
    facts: List[Fact]
    caller_name: str = ""
    kind: str = ""  # a kind of document with a set of facts (forms.FORMS), or ""

    def missing(self) -> List[Fact]:
        return [f for f in self.facts if f.required and not f.value]

    def as_dict(self) -> dict:
        return {"document": self.document, "allowed": self.allowed, "refusal": self.refusal,
                "facts": [f._asdict() for f in self.facts], "caller_name": self.caller_name, "kind": self.kind}


def _parse(raw: Any) -> Intake:
    if not isinstance(raw, dict):
        raise ValueError("expected a JSON object")
    allowed = raw.get("allowed")
    if not isinstance(allowed, bool):
        raise ValueError('"allowed" must be true or false')
    facts: List[Fact] = []
    for f in raw.get("facts") or []:
        if not isinstance(f, dict) or not str(f.get("label") or "").strip():
            continue
        facts.append(Fact(" ".join(str(f["label"]).split())[:60], " ".join(str(f.get("question") or "").split())[:160],
                          " ".join(str(f.get("value") or "").split())[:160], bool(f.get("required", True))))
    refusal = " ".join(str(raw.get("refusal") or "").split())[:300]
    document = " ".join(str(raw.get("document") or "").split())[:80]
    if not allowed and not document:
        # A refusal must be about a document the caller asked for.
        allowed = True
    if not allowed and not refusal:
        raise ValueError('a refused request needs "refusal": why, and what BolDoc can write instead')
    name = " ".join(str(raw.get("caller_name") or "").split()[:3])[:60]
    if any(ch.isdigit() for ch in name):
        name = ""
    form = FORMS.get(str(raw.get("kind") or "").strip().lower())
    if form is not None:
        facts = with_form(facts, form)
    return Intake(document, allowed, refusal if not allowed else "", facts[:8], name, form.kind if form else "")


def with_form(facts: List[Fact], form) -> List[Fact]:
    """The form's facts first, in its order, with what the intake found the caller said; a form fact the intake left
    out is asked (required); then any other fact the request needs."""
    out: List[Fact] = []
    used: set = set()
    for label, question in form.facts:
        i = next((i for i, f in enumerate(facts) if i not in used and same_fact(f.label, label)), None)
        if i is None:
            out.append(Fact(label, question, "", True))
        else:  # the form's label and question (the whole fact), with what the intake found was said
            used.add(i)
            out.append(Fact(label, question, facts[i].value, facts[i].required))
    return out + [f for i, f in enumerate(facts)
                  if i not in used and not any(same_fact(f.label, label) for label, _ in form.facts)]


def known_facts_block(known: Optional[dict]) -> str:
    labels = {"name": "Name", "designation": "Designation", "organisation": "Organisation", "address": "Address"}
    lines = [f"- {labels[k]}: {v}" for k, v in (known or {}).items() if k in labels and v]
    return ("The caller's saved details:\n" + "\n".join(lines) + "\n\n") if lines else ""


def written_block(written: Optional[List[str]], earlier: Optional[List[str]] = None) -> str:
    """The documents already started on this call, and the caller's documents from earlier calls: asking to change or
    add to one of them isn't a new document."""
    out = ""
    if written:
        out += ("Already written on this call: " + "; ".join(f'"{t}"' for t in written) + ". If the caller is asking "
                "to change or add to one of these, that is not a new document: answer \"document\": \"\" with no "
                "facts. The conversation below is what was said since the last one was started.\n\n")
    if earlier:
        out += ("The caller's documents from earlier calls: " + "; ".join(f'"{t}"' for t in earlier) + ". If the "
                "caller clearly means one of these, to change it (\"pichhli wali\", \"jo pehle banayi thi\", \"my "
                "last letter\"), that is not a new document: answer \"document\": \"\" with no facts. Asking for "
                "another document of the same kind, without meaning the earlier one, is a new document.\n\n")
    return out


async def prepare(api_key: Any, transcript: str, known: Optional[dict] = None,
                  client: Optional[httpx.AsyncClient] = None,
                  written: Optional[List[str]] = None, earlier: Optional[List[str]] = None) -> tuple[Intake, dict]:
    """What the document needs, from the conversation so far (and the caller's saved details)."""
    user = (known_facts_block(known) + written_block(written, earlier)
            + f"Conversation so far:\n{transcript[-6000:]}")
    # Bounded: a late checklist is no use to the conversation.
    return await chat_json(api_key, INTAKE, user, _parse, reasoning=None, max_tokens=900, timeout_s=8.0,
                           client=client)

# Does the request make sense as said? Its own small call, not a field of the checklist: buried in INTAKE the same
# question caught 0-1 of 7 nonsense requests; asked alone it caught all 7 and passed every real one (32/32, and
# 44/46 with held-out cases). LLMs recognise ambiguity when asked directly but rarely act on it
# unprompted (arXiv 2605.25284).
SENSE = """You check one thing for BolDoc, an assistant that writes documents for people over a phone call. From the
conversation, find the document the caller wants now and who it is addressed to. Would a real person actually send
this document to that recipient, as said?

First decide "to": who will read the document and act on it.
- A person or an office counts, however they are named: by role or trade (the milkman, the landlord, the maid, a
  neighbour, the shopkeeper, the HR manager), by name, or as an organisation (a bank, the municipality, a school, a
  company).
- A record, sheet, list, note, essay or card ABOUT someone or something is for the caller's own use or to hand over:
  "to" is "" (a pet's vaccination record, an essay on cows, an expense sheet).

Answer "check" when:
- "to" is an animal, an object, a plant, a place or anything else that can't read the document or act on it (a leave
  application to a dog, a complaint to a phone, an application to the moon). A letter, application, complaint or
  notice needs a person or an office that can do something about it;
- a date in it doesn't exist (30 February, 31 June), or the facts contradict each other.
Answer "ok" for everything a real person would send or keep: letters, applications and complaints to people or
offices (including complaints ABOUT animals or things), essays and notes about anything, records, sheets, lists,
invoices, cards. When the document or recipient isn't clear yet, answer "ok". The caller decides who gets it: never
question their choice of a real person or office (a complaint straight to a neighbour or a landlord is fine).

Reply with JSON only: {"to": "the recipient as the caller said it, or \"\"", "verdict": "ok" or "check", "ask": "if
check: one short question in the caller's language that offers what they most likely mean; else \"\""}"""


def _parse_sense(raw: Any) -> dict:
    if not isinstance(raw, dict) or raw.get("verdict") not in ("ok", "check"):
        raise ValueError('need {"to": ..., "verdict": "ok" or "check", "ask": ...}')
    ask = " ".join(str(raw.get("ask") or "").split())[:200]
    verdict = "check" if raw["verdict"] == "check" and ask else "ok"
    return {"to": " ".join(str(raw.get("to") or "").split())[:80], "verdict": verdict,
            "ask": ask if verdict == "check" else ""}


async def sense_check(api_key: Any, transcript: str, client: Optional[httpx.AsyncClient] = None) -> tuple[dict, dict]:
    """Whether a real person would send what the caller asked for, as said; if not, the question to ask first."""
    return await chat_json(api_key, SENSE, f"Conversation:\n{transcript[-4000:]}", _parse_sense, reasoning=None,
                           max_tokens=220, timeout_s=6.0, client=client)
