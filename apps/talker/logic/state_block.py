"""State block: the ONLY source of document/task facts the talker LLM may state.

Rendered from the control plane's session snapshot (`Orchestrator.snapshot`) before every LLM call and sent as the
last message, so the model never relies on stale memory ("I've added the table") when the thinker hasn't actually
done it. Pure function of plain data: the talker process needs no orchestrator, only the latest snapshot.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import List, Optional, Set

from protocol.messages import Event

_TASK_WORDS = {
    "queued": "about to start", "running": "in progress", "awaiting_input": "waiting for the caller's answer",
    "committing": "saving",
}
_KIND_WORDS = {"create": "writing", "edit": "changing", "undo": "undoing the last change on"}
_DELIVERY_WORDS = {
    "pending": "about to be sent", "sending": "being sent", "submitted": "sent (delivery not confirmed yet)",
    "delivered": "delivered", "failed": "FAILED",
}
_FORMAT_WORDS = {"pdf": "PDF", "docx": "Word", "xlsx": "Excel", "pptx": "PowerPoint", "csv": "CSV", "md": "Markdown",
                 "txt": "text", "html": "web page"}


def mask_number(e164: str) -> str:
    return "••" + e164[-4:] if len(e164) >= 4 else "••"


def _title(doc: Optional[dict]) -> str:
    if doc is None:
        return "a document"
    return f'"{doc["title"]}"' if doc.get("head_version") else "the new document (still being written)"


# What to tell the caller when a request is refused (said in their language, briefly, without blame).
REJECTION_HINTS = {
    "trial_doc_limit": "the free trial makes up to 2 new documents per call. Say so; to make more, they can sign in "
                       "on the BolDoc website with this number (free credits)",
    "trial_edit_limit": "the free trial allows 3 changes per document. Say so; signing in on the BolDoc website "
                        "with this number gives free credits for more",
    "no_credits": "their BolDoc credits are used up; they can add balance on the BolDoc website",
    "limit_reached": "this call has reached its limit of requests; offer to continue on a new call",
    "not_allowed": "BolDoc can't write this kind of document (it would pretend to come from someone else, or could "
                   "harm someone). Say so kindly and offer what BolDoc can write instead",
}


def intake_line(it: Optional[dict]) -> Optional[str]:
    """The document writer's checklist for what the caller is asking for (apps/thinker/intake.py)."""
    if not it or it.get("allowed") is False or not it.get("document"):
        # A refusal is never spoken from here: on unclear words the check guessed a forbidden document and BolDoc
        # refused twice on a real call. It applies when BolDoc writes (create_document), where the request is explicit;
        # until then BolDoc just asks what the caller needs.
        return None
    facts = it.get("facts") or []
    given = [f for f in facts if f.get("value")]
    missing = [f for f in facts if f.get("required") and not f.get("value")]
    line = f"from the document writer: the caller wants {it['document']}."
    if given:
        line += " Given so far: " + "; ".join(f"{f['label']}: {f['value']}" for f in given) + "."
    if missing:
        line += (" Still needed before writing (this list is worked out a moment after each turn, so it can lag: "
                 "anything the caller has just said counts as given, and if their last words ask for a different "
                 "document, this list is not for it: work on what they asked for now), one at a time in this order: "
                 + "; ".join(f["label"] for f in missing) + f'. Ask next: "{missing[0].get("question", "")}". If '
                 "the caller doesn't know one or asks you to write it anyway, write it without it (it is left "
                 "as a blank to fill in).")
    else:
        line += " Every needed fact is given and the writer needs nothing else (do not ask for more)."
    return line


def describe_event(ev: Event, snap: dict) -> str:
    """One plain line about an event, phrased so the talker can say it naturally."""
    doc = next((d for d in snap.get("docs", []) if d["id"] == ev.doc_id), None)
    p = ev.payload
    if ev.type == "task.completed":
        changes = "; ".join(p.get("diff_summary") or [])
        ver = ev.head_version or (doc or {}).get("head_version")
        if ver == 1:
            where = ("on their screen" if snap.get("on_screen") else
                     "and its link is on the way by SMS" if snap.get("verified_recipients") else
                     "in their BolDoc account")
            return (f"{_title(doc)} is ready, {where}. Ask the caller to take a look and tell you whether everything "
                    "in it is right or something should change")
        return f"{_title(doc)} is updated to version {ver}" + (f" ({changes})" if changes else "")
    if ev.type == "task.progress":
        stage = str(p.get("stage") or "")
        m = re.match(r"section (\d+) of (\d+)", stage)
        how = (f"{m.group(1)} of its {m.group(2)} sections are written" if m else
               "the writer is looking up facts for it online" if stage == "web research" else "it is being written")
        return (f"{_title(doc)} is still being written: {how}. It will be ready in a little while: ask them to stay on "
                "the line, and ask nothing else")
    if ev.type == "task.failed":
        return f"writing {_title(doc)} failed ({p.get('code', 'error')}); say so plainly and offer to try again"
    if ev.type == "task.cancelled":
        return f"the work on {_title(doc)} was cancelled"
    if ev.type == "task.question":
        return f'the document needs one more fact; ask the caller: "{p.get("text", "")}"'
    if ev.type.startswith("delivery."):
        d = next((x for x in (doc or {}).get("deliveries", []) if x["id"] == p.get("delivery_id")), None)
        to = mask_number(d["recipient"]) if d else "the caller"
        if ev.type == "delivery.delivered":
            return f"the SMS with the link to {_title(doc)} was delivered to the number ending {to[-4:]}"
        if ev.type == "delivery.failed":
            return f"the SMS to the number ending {to[-4:]} FAILED; offer to send it again"
        return f"the SMS with the link is on its way to the number ending {to[-4:]}"
    if ev.type == "command.rejected":
        return f"your last request was not done: {REJECTION_HINTS.get(p.get('reason'), p.get('reason', 'rejected'))}"
    if ev.type == "call.time_warning":
        return ("about one minute is left in this call (its time limit). Tell the caller briefly; anything already "
                "being written will still be finished and saved")
    if ev.type == "call.reask":
        asked = str(p.get("question") or "").strip()
        return ("the caller hasn't answered your last question" + (f" (\"{asked[-200:]}\")" if asked else "")
                + " (they may not have heard it). Ask that same question again briefly, in a few different words, "
                "in the language you are speaking; if it was whether they need anything else, say they can simply "
                "say they're done")
    if ev.type == "call.idle_check":
        if not snap.get("docs"):  # still working out what to write: not "anything else?"
            return ("the caller has been quiet for a while. Ask warmly, in one short sentence, whether they are still "
                    "there and want to carry on with what you were discussing")
        return ("the caller has been quiet for a while. Ask warmly, in one short sentence, whether they need anything "
                "else: a change, or another document")
    if ev.type == "call.question_nudge":
        return ("the caller hasn't answered the document's question yet. Ask it again gently, in one short sentence, "
                "and say you can leave it out if they don't have it at hand")
    if ev.type == "call.idle_goodbye":
        return ("the caller is still quiet. Say a warm goodbye in one short sentence (if a document was sent, it's on "
                "the SMS link; if not, they can call again any time)")
    if ev.type == "call.time_up":
        return ("the call's time is up. Say a short, warm goodbye now (their document is saved; mention the SMS "
                "link only if an SMS is possible)")
    return ev.type.replace(".", " ")


def days_ago(iso: Optional[str], now: Optional[datetime] = None) -> str:
    """'2026-10-19T…' -> '3 days ago' (how the talker can refer to an earlier document)."""
    try:
        days = ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(str(iso))).days
    except (TypeError, ValueError):
        return "earlier"
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"


def earlier_documents_line(recent: List[dict], open_here: Set[str], limit: int = 3) -> Optional[str]:
    """The caller's documents from earlier calls, so "change my leave application" works on a new call.

    Kept apart from this call's work, each title once.
    """
    seen: Set[str] = set()
    items = []
    for d in recent:
        title = str(d.get("title") or "").strip()
        if d.get("id") in open_here or not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        items.append(f'"{title}" ({days_ago(d.get("updated_at"))})')
    if not items:
        return None
    return ("finished earlier calls (not part of this call) left this caller's documents " + ", ".join(items[:limit])
            + ". Never guess from these what the caller wants now: if they haven't said, ask what they need. A "
            "request for a document is a NEW document: create_document, even if an earlier title is similar. "
            "Only when the caller clearly means an earlier one (\"pichhli wali\", \"jo pehle banayi thi\", \"last "
            "time's\"), confirm its title, then call edit_document with document set to that title. Never read an "
            "earlier document aloud.")


# How BolDoc asks for the go-ahead and addresses the caller, in the call's language. English calls ask in English; the
# yes-checks (turns.asked_to_write, asked_to_change) know both. Other languages keep the Hinglish words those checks
# know.
PHRASES = {
    "write": ("Shall I write it now?", "Kya main ise likh doon?"),
    "change": ("Shall I make this change?", "Kar doon?"),
    "change_again": ("Shall I change it, yes or no?", "Badal doon?"),
    "changing": ("I'm making the change now.", "Badal rahi hoon, bas kuch second."),
    "end_call": ("Shall I end the call now? The link will come by SMS.", "Call end kar doon? Link SMS pe aa jayega."),
    "write_back": ('"So: leave from the 25th to the 27th of October for your sister\'s wedding, from you. Shall I '
                   'write it now?"', '"Toh: 25 se 27 October ki chhutti, behen ki shaadi, aapke naam se. Kya main ise '
                   'likh doon?"'),
    "change_back": ('"So, the 26th instead of the 25th. Shall I change it?"', '"Toh 25 ki jagah 26 October kar doon?"'),
    "not_heard": ("Sorry, I didn't catch that.", "Maaf kijiye, theek se sunai nahi diya."),
    "yes_or_no": ("yes or no", "haan ya nahi"),
    "which_language": ('"Should I write it in English or in Hindi?"', '"English mein likhun ya Hindi mein?"'),
    "callback": ('"If I need to ask you something while writing it, may I call you back on this number?"',
                 '"Likhte waqt kuch poochna pada to isi number se call kar sakti hoon?"'),
}


def phrase(key: str, family: str) -> str:
    """What BolDoc says for `key` in this call's language: English in an English call, else the Hinglish words."""
    english, hinglish = PHRASES[key]
    return english if family == "en-IN" else hinglish


def address(first: str, family: str) -> str:
    """How BolDoc addresses the caller by name: "Saurabh ji" in Hindi/Hinglish, "Saurabh" in English."""
    return first if family == "en-IN" else f"{first} ji"


def approval_line(said_yes_to: Optional[str], change_waiting: bool = False, has_docs: bool = False,
                  family: str = "hinglish") -> Optional[str]:
    """The caller's yes to what BolDoc said back is the go-ahead."""
    if said_yes_to == "change":
        return ("the caller just said yes to the change you said back: call edit_document now with the exact new "
                "facts. It is not done until it is called.")
    if said_yes_to == "write":
        if has_docs:
            return ("the caller just said yes to your read-back. If it adds to or changes a document already on this "
                    "call, call edit_document (document: its title); if it is a new, different document, call "
                    "create_document. Nothing happens until you call one.")
        return "the caller just said yes to your read-back: call create_document now. Nothing is written until then."
    if change_waiting:
        return (f"their reply to your \"{phrase('change', family)}\" wasn't a yes, so nothing has changed. If they "
                "said no, leave it. If it was unclear, ask once more in a few new words, ending "
                f"\"{phrase('change_again', family)}\". Don't say it's changed.")
    return None


def build_state_block(snap: dict, mode: str, must_mention: Optional[List[Event]] = None,
                      announce: Optional[Event] = None, eta_s: Optional[dict] = None, max_tasks: int = 4,
                      deferred_tool: Optional[str] = None, extra: Optional[str] = None) -> str:
    lines = ["[STATE — facts you may state about documents. If it's not here, it hasn't happened.]",
             f"mode: {mode}"]
    if deferred_tool:
        lines.append(f"NOT STARTED: your {deferred_tool} call was put off because the caller was still talking. "
                     f"If they have now finished and confirmed, call {deferred_tool} now; saying you will do it is "
                     "not doing it.")
    docs = snap.get("docs", [])
    by_id = {d["id"]: d for d in docs}
    if docs:
        lines.append("documents:")
        for d in docs:
            formats = ", ".join(_FORMAT_WORDS.get(f, f) for f in d.get("formats", []))
            line = f"- {_title(d)} (ref {d['id'][-6:]}) version {d['head_version'] or '-'}; formats: {formats}"
            if d.get("deliveries"):
                last = d["deliveries"][-1]
                line += (f"; {last['channel'].upper()} with the link to {mask_number(last['recipient'])}: "
                         f"{_DELIVERY_WORDS.get(last['status'], last['status'])} (version {last['version']})")
            if d.get("head_version", 0) > 1 and d.get("last_changes"):
                line += "; last change: " + "; ".join(d["last_changes"][:3])
            lines.append(line)
    active = snap.get("tasks", [])
    if not docs and not active:
        lines.append("documents on this call: none yet. Nothing is being written until you call create_document "
                     "(saying you are writing it is not writing it).")
    if active:
        lines.append("work in progress (not done yet). Anything the caller adds or changes now goes into it: call "
                     "edit_document (say a name, date or amount back first). Never say it is added until a [STATE] "
                     "note says so. While it is being written, don't ask whether they need anything else and don't "
                     "repeat yourself: if they speak, answer briefly; if they wait quietly, let them:")
        for t in active[:max_tasks]:
            eta = (eta_s or {}).get(t["id"])
            # past its estimate: no countdown
            when = (f", about {int(eta)} s left" if eta is not None and eta > 3 else
                    ", taking a little longer than usual: say it will be ready in a moment, with no number of seconds"
                    if eta is not None else "")
            if t.get("tier") == "T3":  # a long document: written over minutes, after the call if need be
                soon = "nearly done" if eta is not None and eta <= 30 else (
                    f"about {max(1, math.ceil((eta if eta is not None else 300) / 60))} min left")
                when = (f", {soon} (a long document: the caller may "
                        "hang up, and the link comes by SMS when it's ready. If they stay on the line, don't ask "
                        "whether they need anything else and don't repeat yourself: when they speak, answer briefly "
                        "and say it will be ready in a little while)")
            pending = f", +{t['queued_requests']} newer request(s) queued" if t.get("queued_requests") else ""
            lines.append(f"- {_KIND_WORDS.get(t['kind'], t['kind'])} {_title(by_id.get(t['doc_id']))}: "
                         f"{_TASK_WORDS.get(t['state'], t['state'])}{when}{pending}")
    q = snap.get("question")
    if q:
        lines.append(f'the document still needs a fact; ask the caller (unless you just did): "{q["text"]}" Their '
                     "reply reaches the writer by itself: just acknowledge it in a few words.")
    if docs and not active and not q and all(int(d.get("head_version") or 0) > 0 for d in docs):
        lines.append("everything the caller asked for is written. Answer whatever they still ask; the call is "
                     "ended for you when they want nothing more.")
    if must_mention:
        lines.append("must mention in this reply:")
        lines += [f"- {describe_event(ev, snap)}" for ev in must_mention]
    if announce is not None:
        lines.append("say this now in one short sentence (the caller is waiting quietly): "
                     + describe_event(announce, snap))
    if extra:
        lines.append(extra)
    if snap.get("on_screen"):  # a browser call (signed in): the caller watches the page; SMS only if they ask
        lines.append("ON SCREEN: a browser call. The caller sees the document on their screen as it is written, with "
                     "downloads, and it stays in their BolDoc account. No SMS while they're on the call unless they "
                     "tap \"Send to my phone\" there; if they hang up before a document is ready, its link comes by "
                     "SMS when it is. Don't promise an SMS otherwise.")
    elif snap.get("verified_recipients"):
        lines.append("verified mobile for SMS: " + ", ".join(mask_number(r) for r in snap["verified_recipients"]))
    else:
        lines.append("SMS: NOT possible on this call (no mobile number). Never promise an SMS or a link.")
    return "\n".join(lines)
