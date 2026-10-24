"""Turn-taking helpers: dictation endpointing, exit cues, backchannels."""
from __future__ import annotations

import re
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Tuple

_WORD = re.compile(r"[\wऀ-෿]+", re.UNICODE)


def _norm_tokens(text: str) -> List[str]:
    text = unicodedata.normalize("NFC", text.lower())
    return _WORD.findall(text)


BACKCHANNELS = {"haan", "haa", "han", "ha", "hmm", "hm", "ok", "okay", "accha", "achha", "acha", "ji", "haanji",
                "right", "yes", "yeah", "theek", "thik", "sahi", "uh", "huh", "mm", "हाँ", "हां", "जी", "अच्छा", "ठीक",
                "हम्म", "ओके", "सही", "हाँजी", "हांजी",
                "ਹਾਂ", "ਹਾਂਜੀ", "ਜੀ", "ਠੀਕ"}  # Saaras sometimes writes a Hindi "haan" in Gurmukhi
FILLERS = {"hai", "है", "ਹੈ", "na", "ना", "ji", "जी"}  # "theek hai", "haan na": still just an acknowledgement


GREETINGS = {"namaste", "namaskar", "namaskaar", "hello", "helo", "hi", "hey", "pranam", "नमस्ते", "नमस्कार", "हेलो",
             "हैलो", "हलो", "प्रणाम", "vanakkam", "வணக்கம்"}


def is_greeting(transcript: str) -> bool:
    """'नमस्ते', 'Hello hello', 'namaste ji': a greeting alone, which callers follow at once with what they want."""
    toks = _norm_tokens(transcript)
    greeting = [t for t in toks if t in GREETINGS]
    return 0 < len(toks) <= 3 and bool(greeting) and all(t in GREETINGS or t in FILLERS for t in toks)


# An item named, its amount or number still to come: "flight का खर्चा", "मेरा account number है", "Goa trip के खर्चे में
# flight". LiveKit's model calls these finished, so the words decide them. A question ("खर्चा कितना है?") never matches.
_FIELD = (r"kharcha|kharche|kharch|खर्चा|खर्चे|खर्च|amount|number|नंबर|नम्बर|total|टोटल|price|प्राइस|rate|रेट|कीमत|"
          r"kimat|keemat|fees?|फीस|salary|सैलरी|तनख्वाह|rent|किराया|kiraya|cost|कॉस्ट|budget|बजट|naam|नाम")
_VALUE_MISSING = re.compile(
    rf"(?:^|\s)(?:{_FIELD})(?:\s+(?:hai|है|hoga|होगा|tha|था|rahega|रहेगा))?\s*[,.।]?\s*$|"
    r"(?:kharche|खर्चे|expenses?|kharch|खर्च)\b[^\d.।?]{0,40}?\s(?:mein|में|me)\s+[^\d\s]+(?:\s+[^\d\s]+)?\s*[,.।]?\s*$",
    re.I)


# "Mujhe call mat karna", "dobara phone mat kijiye", "please don't call me", "कॉल मत करना": no more callbacks.
# Devanagari has no \b: its vowel signs aren't word characters.
_NO_CALLS = re.compile(r"\b(?:call|phone)\b.{0,25}?\b(?:mat|na|nahi|mt)\b.{0,15}?\b(?:kar|kijiye|kijie)|"
                       r"\b(?:don'?t|do not|never|stop)\s+(?:call|calling|phone|ringing)\b|"
                       r"(?:कॉल|फ़ोन|फोन).{0,25}?(?:मत|ना|नहीं).{0,15}?(?:कर|कीजि)", re.I)


def wants_no_calls(transcript: str) -> bool:
    return bool(_NO_CALLS.search(transcript or ""))


def value_missing(transcript: str) -> bool:
    return bool(_VALUE_MISSING.search((transcript or "").strip()))


def is_backchannel(transcript: str) -> bool:
    """'haan', 'ok ji', 'accha theek hai' while the bot talks: acknowledgement, not an interruption."""
    toks = _norm_tokens(transcript)
    return 0 < len(toks) <= 3 and all(t in BACKCHANNELS or t in FILLERS for t in toks)


def real_words(transcript: str) -> int:
    """Words that aren't acknowledgements: 'haan theek hai' -> 0, 'Excel mein' -> 2."""
    return sum(1 for t in _norm_tokens(transcript) if t not in BACKCHANNELS and t not in FILLERS)


EXIT_CUES = ("bas itna hi", "bas itna", "bas yahi", "bas", "ho gaya", "hogaya", "khatam", "that's it", "thats it",
             "that's all", "thats all", "that is all", "done", "finished", "end of note", "बस", "हो गया", "खत्म")
# BolDoc's own offer of more help ("Koi aur document banwana hai?"): the talker doesn't re-ask it as an unanswered
# question. Whether the caller is done is decided from the call's state and the meaning of what they say (the turn
# judge), never from a list of words.
_OFFERS_MORE = re.compile(r"\b(?:koi aur|kuch aur|aur kuch|aur koi|anything else|something else|anything more|"
                          r"aur madad)\b|कोई और|कुछ और|और कुछ|और कोई", re.I)


def offers_more(bot_line: str) -> bool:
    """BolDoc's line ends asking whether the caller needs anything else."""
    return bool(_OFFERS_MORE.search(bot_line or "")) and (bot_line or "").rstrip().endswith("?")


# Writing or changing a document needs the caller's yes to BolDoc's read-back, in code as well as in the prompt.
_ASKED_TO_WRITE = re.compile(r"likh\s*d(?:oo|u)n|likh\s*dein|likh\s*du\b|likhun|bana\s*d(?:oo|u)n|bana\s*dein|"
                             r"shall i (?:write|create|make)|should i (?:write|create|make|go ahead)|"
                             r"लिख\s*दूँ|लिख\s*दूं|लिख\s*दें|बना\s*दूँ|बना\s*दूं", re.I)
_ASKED_TO_CHANGE = re.compile(r"kar\s*d(?:oo|u)n|kar\s*dein|badal\s*d(?:oo|u)n|badal\s*dein|hata\s*d(?:oo|u)n|"
                              r"jod\s*d(?:oo|u)n|shall i (?:change|update|make|replace|add|remove)|"
                              r"should i (?:change|update|make|replace|add|remove)|कर\s*दूँ|कर\s*दूं|बदल\s*दूँ|"
                              r"बदल\s*दूं|हटा\s*दूँ|जोड़\s*दूँ", re.I)
# Writing or making words count as a yes only as a request ("likh dijiye", "लिख दो").
_YES_WORDS = re.compile(r"\b(?:haan|haa|han|ha|haanji|hanji|ji|jee|yes|yeah|yep|ok|okay|theek|thik|sahi|bilkul|"
                        r"correct|right|sure|chalega|chaliye|likh\s+(?:do|dijiye|dijie|dein|de)|likho|likhiye|likhdo|"
                        r"bana\s+(?:do|dijiye|dijie|dein|de)|banao|banaiye|kar\s+(?:do|dijiye|dijie|dein|de)|karo|"
                        r"kariye|kijiye|dijiye|go ahead|proceed)\b|हाँ|हां|जी|ठीक|सही|बिल्कुल|लिख\s*(?:दीजिए|दीजिये|"
                        r"दो|दें)|लिखिए|लिखिये|लिखो|बना\s*(?:दीजिए|दीजिये|दो|दें)|बनाइए|बनाओ|कीजिए|दीजिए|करो|करिए|"
                        r"ਹਾਂ", re.I)
_NOT_YES = re.compile(r"\?|\b(?:nahi|nahin|nhi|no|not|don'?t|wait|ruk|ruko|rukiye|minute|what|kya|kaun|kaise|kab|"
                      r"kitna|why|kyun|which|who|change|badal|galat|wrong|but|lekin|magar)\b|नहीं|रुक|क्या|कौन|कैसे|"
                      r"बदल|गलत|लेकिन|मगर|मिनट", re.I)


# A choice ("English mein likhun ya Hindi mein?") asks which, not whether.
_CHOICE = re.compile(r"\b(?:ya|or)\b|(?<![ऀ-ॿ])या(?![ऀ-ॿ])", re.I)
# ...but "Likh doon, haan ya nahi?" asks whether (3 of the 12 choices in 339 write-asks on stored calls)
_YES_OR_NO = re.compile(r"\b(?:haan|haa|ha)\s+ya\s+(?:nahi|nahin|na)\b|\byes\s+or\s+no\b|(?:हाँ|हां)\s+या\s+(?:नहीं|ना)",
                        re.I)


def _asks(bot_line: str, ask: re.Pattern) -> bool:
    """A sentence of BolDoc's line asks this, and isn't an offer of more or a choice: "Kuch aur change karna hai ya koi
    aur document likh doon?" reads nothing back, and its "haan, likh dijiye" wrote the finished letter again."""
    return any(ask.search(s) and not _OFFERS_MORE.search(s) and not _CHOICE.search(_YES_OR_NO.sub("", s))
               for s in re.split(r"(?<=[.?!।])\s+", bot_line or ""))


def asked_to_write(bot_line: str) -> bool:
    """BolDoc's last line read the document back and asked whether to write it ("…Likh doon?")."""
    line = (bot_line or "").rstrip()
    return line.endswith("?") and _asks(line, _ASKED_TO_WRITE)


def asked_to_change(bot_line: str, also_write: bool = True) -> bool:
    """BolDoc's last line said a change back and asked whether to make it ("…26 October kar doon?"); with also_write,
    a read-back ending "Likh doon?" counts too."""
    line = (bot_line or "").rstrip()
    return line.endswith("?") and (_asks(line, _ASKED_TO_CHANGE) or (also_write and _asks(line, _ASKED_TO_WRITE)))


# BolDoc asking whether to end the call now (state_block.PHRASES["end_call"], asked when nothing is left on the call)
_ASKED_TO_END = re.compile(r"\b(?:end|cut|disconnect)\s+(?:the|this)\s+call\b|\bcall\s+(?:end|khatam|band|cut)\s+kar\s*"
                           r"(?:doon|dun|du|dein)\b|कॉल\s*(?:end|एंड|खत्म|बंद)\s*कर\s*(?:दूँ|दूं|दें)", re.I)


def asked_to_end(bot_line: str) -> bool:
    """BolDoc's last line asked whether to end the call now ("Call end kar doon? Link SMS pe aa jayega.")."""
    return bool(re.search(r"\?", bot_line or "")) and _asks(bot_line, _ASKED_TO_END)


def approved(bot_line: str, caller_text: str) -> Optional[str]:
    """What the caller just said yes to: "change" (BolDoc said a change back), "write" (it read a new document back),
    or None."""
    if not approves(caller_text) or not (bot_line or "").rstrip().endswith("?"):
        return None
    if _asks(bot_line, _ASKED_TO_CHANGE):
        return "change"
    return "write" if _asks(bot_line, _ASKED_TO_WRITE) else None


# BolDoc asking whether it may call back with the writer's questions (long documents)
_ASKED_CALLBACK = re.compile(r"call kar (?:sakti|sakta|sakte) h|call back|isi number (?:se|par|pe) call|"
                             r"may i call you|can i call you|कॉल कर (?:सकती|सकता) ह", re.I)
_DECLINES = re.compile(r"\b(?:nahi|nahin|nhi|no|mat|don'?t|not)\b|नहीं|मत", re.I)
# "Leave it": a no, though it ends in a word that's a yes elsewhere ("rehne dijiye", "call mat kijiye").
_LET_IT_BE = re.compile(r"\brehne\s+(?:do|dijiye|dijie|dein|de)\b|रहने\s*(?:दो|दीजिए|दीजिये|दें)|"
                        r"\bmat\s+(?:karo|kijiye|kijie|kariye|karna|badlo|badliye|badalna|likho|likhiye)\b|"
                        r"मत\s*(?:करो|कीजिए|कीजिये|करिए|करना|बदलो|बदलिए|बदलना|लिखो|लिखिए)|\bleave it\b|"
                        r"\bkeep it as it is\b|\bno need\b|\bzaroorat nahi|ज़रूरत नहीं|जरूरत नहीं", re.I)


# Two names the writer's check gave a document: the same request? Its kind (the first of these words) and its other
# words, ignoring the ones that don't change what it is ("demo", "short"), and with school and college words as one.
_DOC_KINDS = {"project", "essay", "assignment", "homework", "thesis", "dissertation", "synopsis", "presentation",
              "deck", "report", "application", "letter", "complaint", "certificate", "receipt", "invoice", "bill",
              "notice", "resume", "proposal", "plan", "minutes", "paper", "sheet", "tracker", "budget", "agreement",
              "affidavit", "statement", "slip", "file"}
_SAME_ANYWAY = {"a", "an", "the", "for", "of", "to", "my", "on", "about", "in", "and", "with", "from", "by", "new",
                "document", "ka", "ki", "ke", "ek", "mera", "meri", "mere", "demo", "practice", "sample", "dummy",
                "test", "only", "just", "simple", "short", "long", "big", "small", "full", "complete", "detailed"}
_ACADEMIC = {"academic", "college", "school", "university", "semester", "class", "final", "year", "btech", "mtech",
             "mba", "bca", "mca", "bsc", "msc", "graduation", "degree", "1st", "2nd", "3rd", "4th", "major", "minor",
             "guide", "marks", "student", "submission"}


def _doc_kind(name: str) -> Tuple[Optional[str], set]:
    kind, rest = None, set()
    for t in _norm_tokens(name or ""):
        if t in _SAME_ANYWAY:
            continue
        base = t[:-1] if t.endswith("s") and t[:-1] in _DOC_KINDS else t
        if base in _DOC_KINDS:
            kind = kind or base
        else:
            rest.add("academic" if base in _ACADEMIC else base)
    return kind, rest


def same_request(a: str, b: str) -> bool:
    """"demo project report" is "4th year college project report" asked again; "project report for a bank loan" and
    "leave application to the college" are other documents."""
    ka, ra = _doc_kind(a)
    kb, rb = _doc_kind(b)
    if ka and kb and ka != kb:
        return False
    return not ra or not rb or bool(ra & rb)


def asked_callback(bot_line: str) -> bool:
    """BolDoc's last line asked whether it may call the caller back."""
    return bool(_ASKED_CALLBACK.search(bot_line or "")) and (bot_line or "").rstrip().endswith("?")


_FROM_WEB = re.compile(r"(?:internet|इंटरनेट|online|ऑनलाइन|\bweb|google|गूगल)\s*(?:se\b|से)|from (?:the )?(?:internet|web)|"
                       r"search (?:it |this )?online|look (?:it|this|these|things) up", re.I)
_WEB_USE = re.compile(r"\bjod|जोड़|\bdaal|डाल|\badd|dekh|देख|search|सर्च|nikal|निकाल|include|research|jaankari|"
                      r"जानकारी|details|aankde|आंकड़|figures|\blo\b|लो\b|lijiye|लीजिए", re.I)


def asks_for_research(caller_text: str) -> Optional[bool]:
    """The caller, unasked, wants facts from the web in the document ("Internet se jaankari bhi jod dijiye": True)
    or doesn't ("internet se kuch mat daalna": False); None when they said nothing about it. "Cafe mein internet
    hoga" is about their business, not a request: only "internet se" (from the internet) counts."""
    t = caller_text or ""
    if not _FROM_WEB.search(t):
        return None
    if _LET_IT_BE.search(t) or (_DECLINES.search(t) and not _YES_WORDS.search(t)):
        return False
    return True if (_WEB_USE.search(t) or _YES_WORDS.search(t)) else None


def declines(caller_text: str) -> bool:
    """A clear no ("nahi", "mat karna", "no", "rehne dijiye")."""
    t = caller_text or ""
    return bool(_LET_IT_BE.search(t)) or (bool(_DECLINES.search(t)) and not _YES_WORDS.search(t))


# The words a bare yes is made of ("haan, theek hai, likh dijiye", "Yes, please do it", "sab sahi hai, bilkul"). An
# answer with anything more in it is the yes-check's to judge, by meaning.
_BARE_YES = BACKCHANNELS | FILLERS | {
    "yep", "yup", "sure", "fine", "good", "great", "perfect", "correct", "absolutely", "definitely", "exactly",
    "alright", "please", "plz", "pls", "go", "ahead", "proceed", "do", "it", "write", "down", "make", "now", "that",
    "this", "is", "all", "everything", "jee", "hanji", "hain", "bilkul", "chalega", "chaliye", "likh", "likho",
    "likhiye", "likhdo", "dijiye", "dijie", "dein", "de", "bana", "banao", "banaiye", "banado", "kar", "karo",
    "kariye", "kijiye", "kardo", "toh", "to", "ab", "abhi", "sab", "kuch", "sir", "madam", "mam", "maam", "aap",
    "wahi", "vahi", "yahi", "same", "वही", "यही",  # "haan wahi": yes, that one
    "हैं", "बिल्कुल", "बिलकुल", "लिख", "लिखो", "लिखिए", "लिखिये", "दीजिए", "दीजिये", "दो", "दें", "बना", "बनाओ",
    "बनाइए", "कीजिए", "करो", "करिए", "कर", "अब", "तो", "सब", "कुछ", "आप", "प्लीज़", "प्लीज", "चलेगा", "चलिए"}


def bare_yes(text: str) -> bool:
    """Nothing but a yes: every word is one a go-ahead is made of."""
    tokens = [tok.strip("।॥") for tok in _norm_tokens(text or "")]  # a Devanagari full stop rides on its word
    tokens = [tok for tok in tokens if tok]
    return bool(tokens) and all(tok in _BARE_YES for tok in tokens)


def approves(caller_text: str) -> bool:
    """A clear yes and nothing more ("haan", "theek hai, likh dijiye", "yes go ahead"); a question, a correction, a
    "wait", anything unclear, or a yes that goes on to say something ("Yes, and the name is...") is not one. Those, and
    other ways of agreeing ("write it down", "sounds good"), are the yes-check's to judge, by meaning
    (session._start_yes_check)."""
    t = caller_text or ""
    return (bool(_YES_WORDS.search(t)) and not _NOT_YES.search(t) and not _LET_IT_BE.search(t) and bare_yes(t))


_QUESTION_START = ("what", "how", "when", "where", "why", "who", "which", "can you", "could you", "will you",
                   "kya", "kaise", "kab", "kahan", "kyun", "kitna", "kaun", "क्या", "कैसे", "कब", "कितना")


def is_exit_cue(transcript: str) -> bool:
    t = " ".join(_norm_tokens(transcript.replace("'", "")))
    t_apos = " ".join(transcript.lower().split())
    return any(t.endswith(cue.replace("'", "")) or t_apos.endswith(cue) for cue in EXIT_CUES)


def is_question_to_agent(transcript: str) -> bool:
    t = transcript.strip().lower()
    return t.endswith("?") or any(t.startswith(q + " ") for q in _QUESTION_START)


@dataclass
class DictationEndpointer:
    """Adaptive silence threshold for dictation: caller's p90 pause + margin, clamped to [min_s, max_s]."""
    min_s: float = 2.0
    max_s: float = 4.0
    margin_s: float = 0.5
    default_s: float = 2.5
    ack_after_s: float = 1.2
    ack_cooldown_s: float = 6.0
    end_silence_s: float = 5.0
    _pauses: Deque[float] = field(default_factory=lambda: deque(maxlen=30))
    _last_ack_at: float = -1e9

    def observe_pause(self, pause_s: float) -> None:
        if 0.15 <= pause_s <= 10.0:
            self._pauses.append(pause_s)

    @property
    def threshold_s(self) -> float:
        if len(self._pauses) < 5:
            return self.default_s
        p = sorted(self._pauses)
        p90 = p[min(len(p) - 1, round(0.9 * (len(p) - 1)))]
        return min(self.max_s, max(self.min_s, p90 + self.margin_s))

    def on_silence(self, silence_s: float, now: float, last_text: str) -> Optional[str]:
        """Called while the caller is silent mid-dictation. Returns an action or None:
        'exit' (cue said), 'ask_more' (long silence -> "anything more?"), 'segment' (commit chunk), 'ack' (hmm/ji).
        """
        if last_text and is_exit_cue(last_text):
            return "exit"
        if last_text and is_question_to_agent(last_text):
            return "exit"
        if silence_s >= self.end_silence_s:
            return "ask_more"
        if silence_s >= self.threshold_s:
            return "segment"
        if silence_s >= self.ack_after_s and now - self._last_ack_at >= self.ack_cooldown_s:
            self._last_ack_at = now
            return "ack"
        return None


def reply_gap_s(speech_s: float) -> float:
    """Silence required before a reply may play, by how long the caller spoke since BolDoc last spoke (VAD time, not
    transcript words: STT can drop words)."""
    if speech_s <= 0.8:
        return 0.5    # "haan", "PDF", "theek hai": answer quickly (between-sentence pauses run to ~0.5 s)
    if speech_s <= 4.0:
        return 0.6
    return 0.9       # dictating: let them breathe


# ------------------------------------------------------------------ has the caller finished? (words, not sound)
_OPEN_END = re.compile(
    r"(?:^|\s)(aur|ki|ke|ka|ko|se|mein|me|main|par|pe|liye|ya|lekin|magar|toh|to|jaise|matlab|yaani|kyunki|"
    r"and|or|but|for|to|of|with|the|a|an|because|like|so|"
    r"और|कि|के|का|की|को|से|में|पर|लिए|या|लेकिन|मगर|तो|जैसे|मतलब|यानी|क्योंकि|मैं|"
    # a sentence's opener said alone: "मुझे" on a real call, and BolDoc talked over the rest
    r"mujhe|mujhko|hamein|humein|mera|meri|mere|hamara|hamari|i|my|we|मुझे|मुझको|हमें|मेरा|मेरी|मेरे|हमारा|हमारी)"
    r"\s*[,.।]?\s*$", re.I)
_WAIT = re.compile(r"(ek|1)\s*(minute|min|second|sec)|ruk(o|iye|ie|jaiye|na)|wait|hold\s*on|"
                   r"let\s*me\s*(check|see|think)|soch(ne|ta|ti)|एक\s*(मिनट|सेकंड)|रुक(ो|िए|िये|ना)|ज़रा\s*रुक|"
                   r"सोच(ने|ता|ती)", re.I)
_CLOSED = re.compile(
    r"(\?\s*$|^(kya|kaise|kab|kitna|kitne|kyun|kaun|kahan|can|could|would|will|what|how|when|which|who|why|is|are|do|"
    r"does)\b|^(क्या|कैसे|कब|कितना|कितने|क्यों|कौन|कहाँ)\s|"
    r"(chahiye|dijiye|dijie|kijiye|kariye|karo|bana\s*do|kar\s*do|bhej\s*do|please|thanks|thank\s*you|"
    r"चाहिए|दीजिए|दीजिये|कीजिए|करिए|करो|बना\s*दो|कर\s*दो|भेज\s*दो|धन्यवाद|शुक्रिया|हाँ|हां|नहीं|जी)\s*[.।!]?\s*$)", re.I)


def turn_shape(text: str) -> Optional[str]:
    """'open' (stopped mid-thought), 'wait' (asked to wait), 'closed' (a question or request), or None (ask the
    classifier). Word rules first: they are instant and never wrong in the obvious cases."""
    t = (text or "").strip()
    if not t:
        return None
    tail = " ".join(t.split()[-6:])
    if _WAIT.search(tail):
        return "wait"
    if _OPEN_END.search(t):
        return "open"
    if _CLOSED.search(t):
        return "closed"
    return None


def _word_key(word: str) -> str:
    return unicodedata.normalize("NFC", word).strip(".,!?।|\"'").lower()


def collapse_repeats(text: str, run: int = 3) -> str:
    """Speech-to-text sometimes loops on noise: "Phone phone phone…", 86 words from 0.9 s of speech. A word written
    `run` or more times in a row is kept once; "bahut bahut" stays as said."""
    words, out, i = text.split(), [], 0
    while i < len(words):
        j, key = i, _word_key(words[i])
        while key and j + 1 < len(words) and _word_key(words[j + 1]) == key:
            j += 1
        out.extend(words[i:i + 1] if j - i + 1 >= run else words[i:j + 1])
        i = j + 1
    return " ".join(out)
