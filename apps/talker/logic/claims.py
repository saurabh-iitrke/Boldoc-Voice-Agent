"""Claim guard: "never say it's done before it is" enforced in code, not only in the prompt.

While the thinker is still working, a spoken sentence that claims completion ("kar diya hai", "ready है",
"I've updated it") is replaced before it reaches TTS. Future and progressive forms ("ready ho jayega",
"kar rahi hoon", "will send") are honest and pass. Sentences are split on Indic and Latin terminators so the
check runs before audio, at no extra latency (TTS synthesises sentence by sentence anyway).
"""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from typing import List, Tuple

_SENTENCE_END = re.compile(r"(?<=[।॥.!?])\s+|(?<=[।॥])")

_CLAIM = re.compile(
    r"(कर\s*दि(या|ये|ए)|कर\s*दी|बदल\s*दि(या|ये)|बदल\s*दी|हो\s*गय[ाी]|हो\s*गई|तैयार\s*है|बन\s*गय[ाी]|बन\s*गई|"
    r"भेज\s*दि(या|ये)|भेज\s*दी|लिख\s*दि(या|ये)|लिख\s*दी|बना\s*दि(या|ये)|बना\s*दी|जोड़\s*दि(या|ये)|हटा\s*दि(या|ये)|"
    r"अपडेट\s*(हो\s*गय|कर\s*दि)|ready\s*है|updated?\s*है"
    r"|\b(kar\s*(diya|di|diye)|badal\s*(diya|di)|ho\s*gaya|ho\s*gayi|ho\s*gai|taiyaa?r\s*hai|ban\s*gaya|"
    r"bhej\s*(diya|di)|likh\s*(diya|di)|bana\s*(diya|di)|ready\s*hai|is\s*ready|has\s*been\s*(sent|updated|changed|"
    r"created|written)|i(\s*have|'ve)\s*(changed|updated|sent|made|written|created|added)|it'?s\s*done|all\s*done)\b)",
    re.I)

_FUTURE_OR_ONGOING = re.compile(
    r"(जाएग[ाी]|जायेग[ाी]|जाएँग|रही\s*हूँ|रही\s*हूं|रहा\s*है|रही\s*है|देती\s*हूँ|देती\s*हूं|दूँगी|दूंगी|करूँगी|करूंगी|"
    r"\b(jayega|jayegi|jaayega|rahi\s*hoon|rahi\s*hun|raha\s*hai|rahi\s*hai|deti\s*hoon|dungi|karungi|will|going\s*to|"
    r"once|when|as\s*soon\s*as|after)\b|\?)", re.I)

PROGRESS_LINE = "बस कुछ second, मैं अभी उस पर काम कर रही हूँ।"
# said instead of a premature "it's done", in the call's language (an English call never hears the Hindi line)
PROGRESS_LINES = {"en-IN": "Just a few seconds, I'm working on it now.", "hinglish": PROGRESS_LINE}


def progress_line(family: str) -> str:
    return PROGRESS_LINES["en-IN" if family == "en-IN" else "hinglish"]


def split_sentences(buffer: str) -> Tuple[List[str], str]:
    """Complete sentences in `buffer`, plus the unfinished remainder."""
    parts = _SENTENCE_END.split(buffer)
    if not parts:
        return [], ""
    *done, rest = parts
    return [p for p in done if p and p.strip()], rest


_CLAUSE = re.compile(r"[,;،]|\s(?:aur|और|and|lekin|लेकिन|but)\s")


def is_completion_claim(sentence: str) -> bool:
    """Judged clause by clause: "main abhi bana rahi hoon, ho gaya hai" still claims "it's done"."""
    return any(_CLAIM.search(c) and not _FUTURE_OR_ONGOING.search(c) for c in _CLAUSE.split(sentence) if c.strip())


# What a model sometimes writes instead of calling a tool: Qwen3-235B on Bedrock streamed
# 'create游戏副本\n{' as reply text in 2 of 12 benchmark replays. BolDoc never says Chinese, Japanese or
# Korean characters, tool names, braces or our [STATE] markers, so a sentence with any of them is not speech
# and must never reach text-to-speech.
_NOT_SPEECH = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]|create_document|edit_document|end_call|"
                         r"[{}]|\[STATE")


def is_not_speech(sentence: str) -> bool:
    return bool(_NOT_SPEECH.search(sentence))


# Repetition. The prompt asks for neither; this is the net under it.
_SELF_INTRO = re.compile(r"(\bmain\b|मैं)\s*boldoc\s*(hoon|hun|हूँ|हूं)|\bi(?:'m| am) boldoc\b|\bthis is boldoc\b", re.I)


def normalized(sentence: str) -> str:
    """For comparing what BolDoc says: lower case, words only (any script), single spaces."""
    return re.sub(r"[\W_]+", " ", sentence.lower()).strip()


def signature(sentence: str) -> str:
    """A short fingerprint of a sentence: the call report counts repeats without keeping any words."""
    return hashlib.sha1(normalized(sentence).encode()).hexdigest()[:10]


def is_self_intro(sentence: str) -> bool:
    return bool(_SELF_INTRO.search(sentence))


def repeats(sentence: str, said: List[str], threshold: float = 0.85) -> bool:
    """Has BolDoc already said this sentence (or nearly: a word or two different)? Short ones ("Ji.", "Theek hai.",
    "Kya likhna hai?") may come again."""
    n = normalized(sentence)
    if len(n.split()) < 4:
        return False
    if any(n == s or SequenceMatcher(None, n, s).ratio() >= threshold for s in said):
        return True
    # the same facts in other words ("Saurabh ji, ek mahine ki vacation leave, foreign trip ke liye..." read back
    # three times on a real call): a long sentence whose words are mostly in one already said
    words = set(n.split())
    return len(words) >= 6 and any(
        len(prior := set(s.split())) >= 6 and len(words & prior) / min(len(words), len(prior)) >= 0.8 for s in said)


# What BolDoc is told is never what it says. The words an instruction gives BolDoc to say are in quotes ("Likh doon?")
# and stay sayable; short sentences ("It is NOT ready yet.") are left out, since BolDoc may well say them itself.
_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”")
ECHO_MIN_WORDS = 6


def instruction_sentences(text: str) -> List[str]:
    """An instruction's own sentences, without what it quotes for BolDoc to say: what echoes compares with."""
    found = (normalized(s) for s in split_sentences(_QUOTED.sub(" ", text) + " ")[0])
    return [s for s in found if len(s.split()) >= ECHO_MIN_WORDS]


def echoes(sentence: str, instructions: List[str], threshold: float = 0.85) -> bool:
    """Does this sentence read out an instruction BolDoc was given: one of its sentences nearly word for word, or a
    long piece of one?"""
    n = normalized(sentence)
    if len(n.split()) < ECHO_MIN_WORDS:
        return False
    return any(n in s or SequenceMatcher(None, n, s).ratio() >= threshold for s in instructions)


# How BolDoc's name is said: Hindi voices read "BolDoc" oddly, so they get it in Devanagari, hyphenated: "बोल-डॉक", the
# user's pick of five spellings heard in the call voice. Other voices read "Bol Doc".
_BRAND = re.compile(r"\bBol\s?Doc\b", re.I)


def speakable(text: str, hindi_voice: bool = True) -> str:
    return _BRAND.sub("बोल-डॉक" if hindi_voice else "Bol Doc", text)


# One thing at a time: "Aapka bank kaunsa hai aur account number kya hai?" asks two things, and on a phone the caller
# answers one. The prompt says so; the model still joins them about one reply in three.
_WH = (r"kya|kaun|kaunsa|kaunsi|kaunse|kitne|kitna|kitni|kab|kahan|kaise|kis|kiske|kiska|kiski|kisko|kyun|kyon|"
       r"what|which|who|when|where|how|why|क्या|कौन|कौनसा|कौनसी|कितने|कितना|कितनी|कब|कहाँ|कैसे|किस|किसके|किसका|किसको|क्यों")
_ASKS = re.compile(rf"(?:^|\s)(?:{_WH})(?=$|[\s?,])", re.I)
_JOIN = re.compile(r"\s+(?:aur|and|और)\s+", re.I)


def one_question(sentence: str) -> str:
    """Keep the first of two asks joined by "aur": "Aapka bank kaunsa hai aur account number kya hai?" becomes
    "Aapka bank kaunsa hai?". A question with one ask ("naam aur pata dono daalun?") stays as it is."""
    s = sentence.rstrip()
    if not s.endswith("?"):
        return sentence
    for m in _JOIN.finditer(s):
        first, second = s[:m.start()], s[m.end():]
        if _ASKS.search(first) and _ASKS.search(second):
            return first.rstrip(" ,") + "?"
    return sentence


def chained_question(sentence: str) -> bool:
    """A second question tacked on with "Aur …?" after the reply already asked one."""
    s = sentence.strip()
    return s.endswith("?") and bool(re.match(r"(?:aur|and|और)\b", s, re.I)) and bool(_ASKS.search(s))


# A change the caller asked for ("छुट्टी 26 से 27 October तक कर दीजिए"): BolDoc once answered "kar di hai" without
# calling edit_document, so nothing changed.
_CHANGE = re.compile(r"kar\s*(?:dijiye|do|dena|de|dein)\b|badal|change|update|hata|nikal|jod|add|daal|ki\s+jagah|"
                     r"instead|replace|correct|sahi\s+kar|कर\s*(?:दीजिए|दीजिये|दो|देना|दें)|बदल|हटा|निकाल|जोड़|डाल|की\s+जगह|"
                     r"सही\s+कर", re.I)


# A change word with a no in it ("nahi add karna", "mat badliye", "don't change anything") may be no request at all:
# it's the change check's to judge, by meaning.
_NEGATED = re.compile(r"\b(?:nahi|nahin|nhi|mat|no|not|don'?t|dont|never)\b|नहीं|नही|मत|ਨਹੀਂ|ਨਹੀ|ਮਤ|না", re.I)


def is_change_request(text: str) -> bool:
    """The caller's words plainly ask for a change ("date 26 kar do", "add my address"). A negated one is the change
    check's (session._start_change_check) to judge."""
    t = text or ""
    return bool(_CHANGE.search(t)) and not _NEGATED.search(t)


# "I'm writing it now" with nothing being written: after a create_document put off while the caller kept talking, BolDoc
# said "aapki leave application likh rahi hoon" for the rest of the call and no document was ever written. Present
# progressive only: "main likh deti hoon" (I'll write it) is an intent.
_PROGRESS = re.compile(r"likh\s*rahi\s*h(?:oo|u)n|bana\s*rahi\s*h(?:oo|u)n|(?:update|change)\s*kar\s*rahi|badal\s*rahi|"
                       r"लिख\s*रही|बना\s*रही|बदल\s*रही|"
                       r"\b(?:i'?m|i am)\s+(?:writing|making|preparing|updating|changing)\b",
                       re.I)


def is_progress_claim(sentence: str) -> bool:
    return bool(_PROGRESS.search(sentence or ""))


# The link goes by SMS to the number that called (and on screen for a browser call), never by email or WhatsApp. Reading
# an email address back is fine, and so is saying it won't come that way ("email par nahi, SMS par aayega").
_CHANNEL = re.compile(r"e-?mail|ईमेल|ई-मेल|whats\s?app|व्हाट्सए?ऐ?प", re.I)
_SENDS = re.compile(r"\blink\b|लिंक|\bbhej|भेज|\bmil\s*(?:jaye|ja|ega|egi|enge)|मिल\s*जा|मिलेग|\bmilega|\bmilegi|"
                    r"\baa\s*ja|आ\s*जा|\baaye?ga|आएग|\bsend|\breceive|\bget\b|\bdeliver|"
                    r"e-?mail\s+(?:kar|you|it)|ईमेल\s+कर", re.I)
_NOT = re.compile(r"\b(?:nahi|nahin|nhi|not|can'?t|cannot|won'?t|no|sirf|only)\b|नहीं|सिर्फ|केवल", re.I)


def is_wrong_channel(sentence: str) -> bool:
    """Promises the link or document by email or WhatsApp (BolDoc sends it by SMS)."""
    s = sentence or ""
    return bool(_CHANNEL.search(s) and _SENDS.search(s) and not _NOT.search(s))
