"""Has the caller finished, or only paused? Word rules first (instant), then a one-word Sarvam classifier.

In-reply turn markers (◐/○) were unreliable on sarvam-105b, so the decision lives in this separate call, which runs in
parallel with the reply (~150-180 ms p50) and only extends how long playback waits.
"""
from __future__ import annotations

import re
from typing import Optional, Union

import httpx

from protocol.llm import BUILTIN, LLMEndpoint

from .logic.turns import turn_shape, value_missing

CLASSIFIER = """You decide one thing on a phone call: has the caller finished speaking, or did they only pause?
Their words come from speech recognition while they talk. Reply with exactly one word.

incomplete = the words stop mid-thought, so they are surely going to continue:
  - the last word is a connector or postposition: aur, ki, ke, ka, ko, se, mein, par, ke liye, ya, lekin, and, or,
    for, to, with, because
  - an item is named but its value is missing: "expense mein flight" (amount missing), "mera number hai nau zero"
  - a list that is still being dictated
wait = they asked you to wait or are thinking: "ek minute", "rukiye", "hold on", "let me check"
complete = everything else. An answer to the assistant's question is complete even if it is one word ("chaar",
  "haan", "PDF", "teen log", "chaar log ja rahe hain"). A question to the assistant is complete. A full request is
  complete. When unsure, answer complete."""
_SHAPE = {"open": "incomplete", "wait": "wait", "closed": "complete"}


def answers_question(text: str, question: str) -> bool:
    """Does the caller's phrase end on the word BolDoc's question turned on? "Bank Manager के लिए" answers "यह letter
    किसके लिए है?", "kal se" answers "kab se chahiye?": complete, though it ends like a phrase left open."""
    words = text.split()
    if not words or "?" not in (question or ""):
        return False
    last = _same(re.sub(r"[^\w\u0900-\u097F]+", "", words[-1].lower()))
    asked = re.split(r"[.!।]", question[:question.rfind("?")])[-1].lower()
    return bool(last) and last in {_same(w) for w in re.findall(r"[\w\u0900-\u097F]+", asked)}


# The same connector in either script: BolDoc's question "kis liye hai?" is in Latin, the caller's answer "…के लिए"
# comes back from speech recognition in Devanagari.
_CONNECTORS = {"लिए": "liye", "lie": "liye", "से": "se", "में": "mein", "me": "mein", "mai": "mein", "को": "ko",
               "का": "ka", "की": "ki", "के": "ke", "पर": "par", "pe": "par", "पे": "par", "तक": "tak", "वाला": "wala",
               "वाली": "wali"}


_CONJUNCTIONS = {"aur", "ya", "lekin", "magar", "kyunki", "toh", "to", "ki", "jaise", "matlab", "yaani", "and", "or",
                 "but", "because", "so", "like", "और", "या", "लेकिन", "मगर", "क्योंकि", "तो", "कि", "जैसे", "मतलब", "यानी"}


def _ends_on_conjunction(text: str) -> bool:
    words = text.split()
    return bool(words) and re.sub(r"[^\w\u0900-\u097F]+", "", words[-1].lower()) in _CONJUNCTIONS


def _same(word: str) -> str:
    return _CONNECTORS.get(word, word)


def by_rules(text: str, speech_s: float, question: str = "") -> Optional[str]:
    shape = turn_shape(text)
    if shape == "open" and answers_question(text, question):
        return "complete"
    if value_missing(text):
        return "incomplete"  # the amount or number is still to come, however short the words so far
    if shape == "open" and len(text.split()) <= 4 and not _ends_on_conjunction(text):
        # "Bank Manager के लिए" after "kis baare mein hai?": a short phrase ending on a postposition is often the whole
        # answer. The classifier sees BolDoc's question (~0.2 s, in parallel with the reply); the rule held it 1.5 s.
        # A trailing conjunction ("…aur", "…lekin") still means more is coming.
        return None
    if shape:
        return _SHAPE[shape]
    if speech_s <= 1.0:
        return "complete"  # "haan", "PDF", "chaar": short answers are answers
    if text.rstrip().endswith(("।", ".", "?", "!", "॥")):
        # Sarvam punctuates a final when the sentence ended ("एक Excel sheet बनाना है।"); fragments come back bare
        # ("...Excel sheet में flight").
        return "complete"
    return None


async def classify(client: httpx.AsyncClient, api_key: Union[str, LLMEndpoint], last_bot_line: str,
                   caller_text: str) -> str:
    """One word from a fast model (JUDGE_LLM, default the talker's). `api_key` may be a Sarvam key or an endpoint."""
    ep = api_key if isinstance(api_key, LLMEndpoint) else LLMEndpoint(
        "sarvam", "sarvam-105b", BUILTIN["sarvam"]["base_url"], api_key, BUILTIN["sarvam"]["auth"],
        dict(BUILTIN["sarvam"]["judge_extra"]))
    user = f'Assistant\'s last line: "{last_bot_line[-300:]}"\nCaller\'s words so far: "{caller_text[-400:]}"\nAnswer:'
    body = {"model": ep.model, "messages": [{"role": "system", "content": CLASSIFIER},
                                             {"role": "user", "content": user}],
            "max_tokens": 3, "temperature": 0.0, **ep.extra}
    r = await client.post(ep.chat_url, json=body, headers=ep.headers(), timeout=1.0)
    r.raise_for_status()
    word = ((r.json()["choices"][0]["message"]["content"] or "").strip().lower().split() or ["complete"])[0]
    word = word.strip(".:,!")
    return word if word in ("complete", "incomplete", "wait") else "complete"


DONE_CHECK = """A phone call with BolDoc, an assistant that writes documents for the caller. Decide one thing from the
caller's last words: do they still want something from BolDoc now (a change, another document, an answer to a
question, or to go on talking), or are they done (no more requests: declining more help, thanking, saying bye,
in any language or words)? Reply with exactly one word: more or done. When unsure, answer more."""


async def caller_is_done(client: httpx.AsyncClient, api_key: Union[str, LLMEndpoint], last_bot_line: str,
                         caller_text: str, documents_ready: int) -> bool:
    """Asked only when the call's work is finished (nothing being written, asked or sent): whether the caller wants
    anything more, by meaning. Any error is "more": a wrong goodbye is worse than one more question."""
    ep = api_key if isinstance(api_key, LLMEndpoint) else LLMEndpoint(
        "sarvam", "sarvam-105b", BUILTIN["sarvam"]["base_url"], api_key, BUILTIN["sarvam"]["auth"],
        dict(BUILTIN["sarvam"]["judge_extra"]))
    user = (f"Documents ready on this call: {documents_ready}\nAssistant's last line: \"{last_bot_line[-300:]}\"\n"
            f"Caller's last words: \"{caller_text[-400:]}\"\nAnswer:")
    body = {"model": ep.model, "messages": [{"role": "system", "content": DONE_CHECK},
                                             {"role": "user", "content": user}],
            "max_tokens": 3, "temperature": 0.0, **ep.extra}
    try:
        r = await client.post(ep.chat_url, json=body, headers=ep.headers(), timeout=1.5)
        r.raise_for_status()
        word = ((r.json()["choices"][0]["message"]["content"] or "").strip().lower().split() or ["more"])[0]
    except Exception:  # see the docstring
        return False
    return word.strip(".:,!") == "done"


CLOSE_CHECK = """A phone call with BolDoc, an assistant that writes documents for the caller. Below are BolDoc's line,
the caller's answer, and BolDoc's reply to it. Decide one thing: has the conversation ended? It has only when the
caller's answer says they want nothing more: a "no" to BolDoc offering more help or asking what to write, thanks
with a goodbye, or a bye; AND BolDoc's reply is a goodbye that asks and offers nothing. A caller who gives BolDoc
information (their name, a date) or agrees to something ("haan") has not ended the conversation, whatever BolDoc's
reply says. Reply with exactly one word: end or continue. When unsure, answer continue."""


# Worked examples for the one-word judges (few-shot): wording sways a small judge, examples steady it. None of these is
# a bench case: the benches measure on others.
CLOSE_EXAMPLES = [
    ('BolDoc\'s line: "Bataiye, aaj kya likhna hai?"\nCaller\'s answer: "kuch nahi"\nBolDoc\'s reply: "Theek hai, '
     'jab chahein call kijiye. Dhanyavaad!"', "end"),
    ('BolDoc\'s line: "Which bank is it for?"\nCaller\'s answer: "HDFC"\nBolDoc\'s reply: "Thank you."', "continue"),
    ('BolDoc\'s line: "Kuch aur madad chahiye?"\nCaller\'s answer: "nahi ji, thank you"\nBolDoc\'s reply: "Aapka '
     'din achha ho!"', "end"),
    ('BolDoc\'s line: "Shall I write it?"\nCaller\'s answer: "yes"\nBolDoc\'s reply: "Great, thank you!"',
     "continue"),
]
YES_EXAMPLES = [
    ('BolDoc asked: "So, an invoice for 8,000 rupees to Mehta Traders. Shall I write it?"\nCaller answered: '
     '"Go for it"', "yes"),
    ('BolDoc asked: "Toh 12 ki jagah 13 November kar doon?"\nCaller answered: "Car do"', "yes"),
    ('BolDoc asked: "So, the amount changed to 9,000. Shall I make this change?"\nCaller answered: "make it 9,500"',
     "no"),
    ('BolDoc asked: "Toh: bijli vibhag ko shikayat, aapke naam se. Likh doon?"\nCaller answered: "ek minute, pehle '
     'meter number bhi daalo"', "no"),
    ('BolDoc asked: "Shall I write it?"\nCaller answered: "Hmm"', "unclear"),
]
CLAIM_EXAMPLES = [
    ('Caller asked: "Tamil mein kar do"\nBolDoc\'s reply: "Poora letter Tamil mein likh doon?"', "asks"),
    ('Caller asked: "Tamil mein kar do"\nBolDoc\'s reply: "Tamil mein bana deti hoon, ek minute mein phone par aa '
     'jayega."', "claims"),
    ('Caller asked: "add my PAN number"\nBolDoc\'s reply: "What is your PAN number?"', "asks"),
    ('Caller asked: "Tamil version nahi mila"\nBolDoc\'s reply: "Tamil version aapke phone par bhej diya gaya hai."',
     "claims"),
    ('Caller asked: "Tamil mein likho"\nBolDoc\'s reply: "Bilkul, main Tamil mein baat kar sakti hoon. Bataiye?"',
     "ignores"),
    ('Caller asked: "amount 5,000 kar do"\nBolDoc\'s reply: "Aapka invoice ready hai, kuch aur chahiye?"', "ignores"),
]


def _with_examples(system: str, examples: list, user: str) -> list:
    msgs = [{"role": "system", "content": system}]
    for q, a in examples:
        msgs += [{"role": "user", "content": q + "\nAnswer:"}, {"role": "assistant", "content": a}]
    return [*msgs, {"role": "user", "content": user}]


async def reply_closes_call(client: httpx.AsyncClient, api_key: Union[str, LLMEndpoint], bot_line: str,
                            caller_text: str, reply: str) -> bool:
    """BolDoc said goodbye to a caller who wants nothing more, without ending the call : the call should end after it.
    Any error is "continue": hanging up on someone who still wants something is worse than a call left open."""
    ep = api_key if isinstance(api_key, LLMEndpoint) else LLMEndpoint(
        "sarvam", "sarvam-105b", BUILTIN["sarvam"]["base_url"], api_key, BUILTIN["sarvam"]["auth"],
        dict(BUILTIN["sarvam"]["judge_extra"]))
    user = (f"BolDoc's line: \"{bot_line[-300:]}\"\nCaller's answer: \"{caller_text[-400:]}\"\n"
            f"BolDoc's reply: \"{reply[-300:]}\"\nAnswer:")
    body = {"model": ep.model, "messages": _with_examples(CLOSE_CHECK, CLOSE_EXAMPLES, user),
            "max_tokens": 3, "temperature": 0.0, **ep.extra}
    try:
        r = await client.post(ep.chat_url, json=body, headers=ep.headers(), timeout=1.5)
        r.raise_for_status()
        word = ((r.json()["choices"][0]["message"]["content"] or "").strip().lower().split() or ["continue"])[0]
    except Exception:  # see the docstring
        return False
    return word.strip(".:,!") == "end"


YES_CHECK = """A phone call with BolDoc, an assistant that writes documents for the caller. BolDoc asked the caller to
confirm something: whether to write a document, make a change, or call them back. From the caller's answer, decide
whether it is a yes to that question. Phone speech recognition garbles words (a Hindi "kar do" can come out as
"card", "haan, likh dijiye" as "Hello, write it down"): judge what they most likely said. It is a yes only if they
agree to exactly what BolDoc said back. A question, a "wait", a no, or anything that changes or replaces what BolDoc
said (another date, amount, name or wording, "make it the 28th instead") is not a yes. Reply with exactly one word:
yes, no or unclear. When unsure, answer unclear."""


async def is_yes(client: httpx.AsyncClient, api_key: Union[str, LLMEndpoint], bot_line: str,
                 caller_text: str) -> str:
    """Whether the caller's answer is a yes to BolDoc's request for the go-ahead: "yes", "no" or "unclear". Asked only
    when the word rules (turns.approves / declines) can't tell; any error is "unclear", so BolDoc asks again."""
    ep = api_key if isinstance(api_key, LLMEndpoint) else LLMEndpoint(
        "sarvam", "sarvam-105b", BUILTIN["sarvam"]["base_url"], api_key, BUILTIN["sarvam"]["auth"],
        dict(BUILTIN["sarvam"]["judge_extra"]))
    user = f"BolDoc asked: \"{bot_line[-300:]}\"\nCaller answered: \"{caller_text[-300:]}\"\nAnswer:"
    body = {"model": ep.model, "messages": _with_examples(YES_CHECK, YES_EXAMPLES, user),
            "max_tokens": 3, "temperature": 0.0, **ep.extra}
    try:
        r = await client.post(ep.chat_url, json=body, headers=ep.headers(), timeout=1.5)
        r.raise_for_status()
        word = ((r.json()["choices"][0]["message"]["content"] or "").strip().lower().split() or ["unclear"])[0]
    except Exception:  # see the docstring
        return "unclear"
    word = word.strip(".:,!")
    return word if word in ("yes", "no") else "unclear"



CHANGE_CHECK = """A phone call with BolDoc, an assistant that writes documents for the caller. A document has been
written on this call. From BolDoc's last line and the caller's words (phone speech recognition may garble them),
decide one thing: is the caller asking for that document to be changed: its content, a detail, its format, or the
language it is written in (for example "English mein likho" for a Hindi letter, "date badal do", "add my address")?
Agreeing to a change BolDoc just offered counts too. Asking for a new, different document, a question about it,
thanks or a goodbye do not. When BolDoc asked whether anything should change, an answer that names nothing to change
is not a request for one. Reply with exactly one word: change or no."""
# A sense gate : "नहीं", then "Bus" (बस), answering "sab kuch sahi hai ya kuch badalna hai?" were each taken for a
# change 1 time in 3, so BolDoc's replies were dropped as ignoring it.
CHANGE_EXAMPLES = [
    ('BolDoc\'s last line: "Your rent receipt is ready. Is everything correct, or should I change anything?"\n'
     'Caller\'s words: "nope"', "no"),
    ('BolDoc\'s last line: "Aapka bill ready hai. Sab sahi hai ya kuch badalna hai?"\nCaller\'s words: "na ji"', "no"),
    ('BolDoc\'s last line: "Aapka bill ready hai. Sab sahi hai ya kuch badalna hai?"\nCaller\'s words: "na, GST galat '
     'laga hai"', "change"),
    ('BolDoc\'s last line: "Your letter is ready. Anything to change?"\nCaller\'s words: "make it sound more polite"',
     "change"),
    ('BolDoc\'s last line: "Aapki shikayat ready hai."\nCaller\'s words: "theek hai, itna kaafi hai"', "no"),
]


async def asks_for_change(client: httpx.AsyncClient, api_key: Union[str, LLMEndpoint], bot_line: str,
                          caller_text: str) -> Optional[bool]:
    """Whether the caller is asking to change the document written on this call, by meaning (the word rules know
    only some ways of saying it). None when the check failed: the rules alone decide then."""
    ep = api_key if isinstance(api_key, LLMEndpoint) else LLMEndpoint(
        "sarvam", "sarvam-105b", BUILTIN["sarvam"]["base_url"], api_key, BUILTIN["sarvam"]["auth"],
        dict(BUILTIN["sarvam"]["judge_extra"]))
    user = f"BolDoc's last line: \"{bot_line[-300:]}\"\nCaller's words: \"{caller_text[-400:]}\"\nAnswer:"
    body = {"model": ep.model, "messages": _with_examples(CHANGE_CHECK, CHANGE_EXAMPLES, user),
            "max_tokens": 3, "temperature": 0.0, **ep.extra}
    try:
        r = await client.post(ep.chat_url, json=body, headers=ep.headers(), timeout=1.5)
        r.raise_for_status()
        word = ((r.json()["choices"][0]["message"]["content"] or "").strip().lower().split() or [""])[0]
    except Exception:
        return None
    return word.strip(".:,!") == "change"


CLAIM_CHECK = """A phone call with BolDoc, an assistant that writes documents for the caller. The caller asked for a
change to their document (its content, a detail, or the language it is written in), but BolDoc called no tool:
nothing has been changed, nothing new is being written, and nothing new is on their screen or phone. Classify
BolDoc's reply with exactly one word:
claims: it tells the caller, or lets them believe, that the change is being made, has been made, or is ready, sent
or visible (promising to do it now, giving a time for it, or saying it is or should be on their screen or phone);
asks: it says the change back and asks whether to make it, or asks for a detail the change needs;
ignores: it doesn't take up the change at all (it talks about something else, or misunderstands it)."""


async def reply_to_change(client: httpx.AsyncClient, api_key: Union[str, LLMEndpoint], caller_text: str,
                          reply: str) -> Optional[str]:
    """How BolDoc's reply, with no tool called, meets the change the caller asked for: "claims", "asks" or "ignores".
    None when the check failed: the reply plays as it would have."""
    ep = api_key if isinstance(api_key, LLMEndpoint) else LLMEndpoint(
        "sarvam", "sarvam-105b", BUILTIN["sarvam"]["base_url"], api_key, BUILTIN["sarvam"]["auth"],
        dict(BUILTIN["sarvam"]["judge_extra"]))
    user = f"Caller asked: \"{caller_text[-300:]}\"\nBolDoc's reply: \"{reply[-400:]}\""
    body = {"model": ep.model, "messages": _with_examples(CLAIM_CHECK, CLAIM_EXAMPLES, user + "\nAnswer:"),
            "max_tokens": 3, "temperature": 0.0, **ep.extra}
    try:
        r = await client.post(ep.chat_url, json=body, headers=ep.headers(), timeout=1.5)
        r.raise_for_status()
        word = ((r.json()["choices"][0]["message"]["content"] or "").strip().lower().split() or [""])[0]
    except Exception:  # see the docstring
        return None
    word = word.strip(".:,!")
    return word if word in ("claims", "asks", "ignores") else None
