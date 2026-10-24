"""Which language is the caller speaking? Decided from the transcript's script, not the STT label.

Script is unambiguous for every Indic script except Devanagari (Hindi vs Marathi), which a few frequent Marathi words
separate well enough.

Hindi and English are one family on a phone call in India (Hinglish): a caller switching between them is not a language
switch, and BolDoc answers in the mix they use. Moving away from Hindi/English needs a real utterance in the other
language (five words, or three confirmed by the next turn). Coming back is immediate.

A caller who names the language they want ("I am Hindi speaker", "ਹਿੰਦੀ ਵਿੱਚ ਬੋਲੋ") gets it, until they name another,
whatever script speech-to-text writes their words in (it sometimes writes a Hindi speaker's words in Gurmukhi, "ਹਾਂ"). A
browser call went Punjabi and stayed there after "I am Hindi speaker". How they ask is theirs: "Hindi please", "in
Hindi", "Hindi mein kijiye" are requests as much as "Hindi mein baat kijiye". The words decide the clear cases here, at
once; any other way of asking is the talker model's to recognise, with its set_language tool. A language named for the
document ("Hindi mein likhna", or the answer to "English mein likhun ya Hindi mein?") or turned down ("Hindi nahi aati",
"I don't speak Hindi") is not a request.

A caller's language is remembered for their next call, by phone, "Call me" or the browser : the call starts in it, and
follows them as usual if they speak another.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .scripts import TO_DEVANAGARI as _TO_DEVANAGARI

SCRIPTS = [  # (code, first, last)
    ("hi-IN", 0x0900, 0x097F), ("bn-IN", 0x0980, 0x09FF), ("pa-IN", 0x0A00, 0x0A7F), ("gu-IN", 0x0A80, 0x0AFF),
    ("od-IN", 0x0B00, 0x0B7F), ("ta-IN", 0x0B80, 0x0BFF), ("te-IN", 0x0C00, 0x0C7F), ("kn-IN", 0x0C80, 0x0CFF),
    ("ml-IN", 0x0D00, 0x0D7F),
]
_MARATHI = re.compile(r"(^|\s)(आहे|आहेत|आणि|मला|मी|नाही|काय|तुम्ही|पाहिजे|करा|होते|आम्ही|माझे|माझा|माझी)(?=\s|$|[।,.?!])")
NAMES = {"hi-IN": "Hindi", "en-IN": "English", "bn-IN": "Bengali", "pa-IN": "Punjabi", "gu-IN": "Gujarati",
         "od-IN": "Odia", "ta-IN": "Tamil", "te-IN": "Telugu", "kn-IN": "Kannada", "ml-IN": "Malayalam",
         "mr-IN": "Marathi"}
HINGLISH = {"hi-IN", "en-IN"}
# Languages BolDoc doesn't talk in. Punjabi : Saaras writes a Hindi speaker's words in Gurmukhi often enough ("ਨਹੀਂ add
# ਕਰਨਾ") that calls went Punjabi, answered in Punjabi, and the next call started in it. Gurmukhi is heard as Hindi;
# documents can still be written in Punjabi when asked.
UNSPOKEN = {"pa-IN"}
# Hindi's most common words: a transcript in another script that reads as Hindi once in Devanagari is Hindi
_HINDI_WORDS = {"है", "हैं", "नहीं", "नही", "मुझे", "मेरा", "मेरी", "मेरे", "करना", "करो", "कीजिए", "दीजिए", "का", "की",
                "के", "में", "से", "को", "और", "भी", "यह", "वह", "क्या", "कैसे", "अभी", "चाहिए", "हाँ", "हां", "जी",
                "लिख", "दो", "आप", "हम", "तो", "था", "थी", "हो", "गया", "गई", "रहा", "रही", "ठीक", "कर", "दिया", "बस"}


def reads_as_hindi(devanagari: str) -> bool:
    """Two of Hindi's most common words: a Hindi speaker's words, whatever script speech-to-text wrote them in."""
    return sum(1 for w in devanagari.split() if w.strip("।,.?!") in _HINDI_WORDS) >= 2
_LANG_WORDS = {"hi-IN": r"hindi|हिंदी|हिन्दी|ਹਿੰਦੀ|হিন্দি", "en-IN": r"english|इंग्लिश|अंग्रेज़ी|अंग्रेजी|ਇੰਗਲਿਸ਼|ਅੰਗਰੇਜ਼ੀ",
               "pa-IN": r"punjabi|पंजाबी|ਪੰਜਾਬੀ", "ta-IN": r"tamil|तमिल|தமிழ்", "te-IN": r"telugu|तेलुगु|తెలుగు",
               "kn-IN": r"kannada|कन्नड़|ಕನ್ನಡ", "ml-IN": r"malayalam|मलयालम|മലയാളം", "mr-IN": r"marathi|मराठी",
               "bn-IN": r"bengali|bangla|बंगाली|বাংলা", "gu-IN": r"gujarati|गुजराती|ગુજરાતી",
               "od-IN": r"odia|oriya|ओड़िया|ଓଡ଼ିଆ"}
# talking, not writing: "Hindi mein baat kijiye" names the conversation's language; "letter English mein likhna
# hai" names the document's (create_document's doc_lang)
_TALK = re.compile(r"speaker|speak|talk|\bbol|baat|samajh|samjh|understand|aati|aata|language|bhasha|reply|"
                   r"respond|answer|converse|switch|continue|chalega|chalegi|comfortable|prefer|भाषा|बोल|बात|समझ|आती|आता|चलेगा|"
                   r"स्पीकर|ਬੋਲ|ਗੱਲ|ਸਮਝ|ਆਉਂਦੀ|ਸਪੀਕਰ", re.I)
_WRITE = re.compile(r"likh|write|letter|document|application|\bpdf\b|\bword\b|excel|essay|notice|report|invoice|"
                    r"\bbill\b|resume|\bcv\b|\bppt\b|presentation|slides?\b|sheet|article|assignment|project|"
                    r"लिख|पत्र|ਲਿਖ", re.I)
# Turned down, in the same clause: "Hindi nahi aati", "English is not good", "Hindi mein mat bolo", "English kam aati
# hai", "I don't speak Hindi". A clause ends at punctuation or "but"/"lekin": "I can't speak English, Hindi please" is
# Hindi. Any "not" earlier in the clause counts, so an unclear one ("I don't understand English well so Hindi") is left
# to the talker model rather than guessed here.
_CLAUSE_END = re.compile(r"[,.;!?।]|\b(?:but|lekin|magar|so|isliye|kyunki|because)\b", re.I)
_NEG = re.compile(r"\b(?:not|no|don'?t|do not|doesn'?t|can'?t|cannot|never|nahi|nahin|nhi|mat|na|isn'?t|kam|weak|"
                  r"poor|kamzor|kamjor)\b|नहीं|मत|कम|ਨਹੀਂ|(?:^|\s)न(?:\s|$)", re.I)


def _turned_down(text: str, start: int, end: int) -> bool:
    before = text[:start]
    cut = list(_CLAUSE_END.finditer(before))
    before = before[cut[-1].end():] if cut else before
    after = text[end:]
    stop = _CLAUSE_END.search(after)
    after = " ".join((after[:stop.start()] if stop else after).split()[:3])
    return bool(_NEG.search(before) or _NEG.search(after))


# What a request to switch is made of besides the language: "Hindi please", "in Hindi", "Hindi mein kijiye",
# "can we continue in English?", "हिंदी में", "Hindi please, not English" (each language's "not" is weighed on its
# own). Anything else said with it ("my son has a Hindi exam") makes it not one.
_REQUEST_TEXT = """not no nahi nahin nhi mat na don't dont नहीं मत
call conversation chat rest of remaining onwards baaki baki aage ab कॉल बाकी आगे अब
please pls plz can could would will shall you we us me i i'd id like to in into the a an it this do does let's lets
let just only now ok okay yes yeah sir madam maam ma'am mam from here on with go language lang
better fine is be are speak talk reply answer respond converse switch change continue use using want prefer
mein me main mai mei se hi bhi ji haan han ha hanji achha acha theek thik hai hain ho karo kariye karein kijiye kijie
karna kar kare karenge sakte sakti sakta dijiye de dena chalega chalegi chahiye bas sirf aap aapse hum humse mujhe
mujhse humein yaar bhai zara jara thoda baat bolo boliye bol bolna boliyega samjhao samjhaiye bataiye batao bata
bhasha mein में मे मैं से ही भी जी हाँ हां ठीक है हैं हो करो करिए कीजिए करें कर सकते सकती दीजिए दो चलेगा चलेगी
चाहिए बस सिर्फ आप आपसे हम मुझे मुझसे प्लीज़ प्लीज बात बोलो बोलिए बोलिये भाषा समझाइए बताइए ਵਿੱਚ ਵਿਚ ਜੀ ਹਾਂ ਬੋਲੋ
ਗੱਲ ਕਰੋ"""
_REQUEST_WORDS = set(_REQUEST_TEXT.split())
_LANG_TOKEN = re.compile("|".join(f"(?:{p})" for p in _LANG_WORDS.values()), re.I)


def _named(text: str) -> List[Tuple[str, int, int]]:
    """Each language named in the text, with where: (code, start, end)."""
    return sorted(((code, m.start(), m.end()) for code, pattern in _LANG_WORDS.items()
                   for m in re.finditer(pattern, text, re.I)), key=lambda x: x[1])


def asked_about_document_language(bot_line: str) -> bool:
    """BolDoc's last line asked which language the DOCUMENT should be in ("English mein likhun ya Hindi mein?")."""
    return bool(bot_line) and bool(_WRITE.search(bot_line)) and bool(
        _LANG_TOKEN.search(bot_line) or re.search(r"language|bhasha|भाषा", bot_line, re.I))


def _only_a_request(text: str) -> bool:
    # Indic letters carry combining marks (not \w): a word is anything between spaces and punctuation
    words = [w for w in re.findall(r"[^\s,.;:!?।\"()\-]+", text.lower()) if not _LANG_TOKEN.fullmatch(w)]
    return all(w in _REQUEST_WORDS for w in words)


def asked_for(text: str, bot_line: str = "") -> Optional[str]:
    """The language a caller asks BolDoc to talk in, or None. Of several named, the last one not turned down:
    "Punjabi nahi aati, Hindi mein boliye" is Hindi. A request either says it's about talking ("Hindi mein baat
    kijiye", "I am Hindi speaker") or is nothing but the request ("Hindi please", "in English"); a language named for
    the document, or as the answer to BolDoc asking the document's language, isn't one."""
    named = _named(text)
    if not named:
        return None
    if not _TALK.search(text):
        if _WRITE.search(text) or asked_about_document_language(bot_line) or not _only_a_request(text):
            return None
    elif _WRITE.search(text) and not re.search(r"baat|speak|talk|\bbol|बात|बोल|ਗੱਲ|ਬੋਲ", text, re.I):
        return None  # "Hindi mein likhna, English nahi aati" is about the document
    found: Optional[str] = None
    for code, start, end in named:
        if not _turned_down(text, start, end) and code not in UNSPOKEN:  # not one BolDoc talks in: the model says so
            found = code
    return found


def mostly_hindi(text: str) -> bool:
    """Real Hindi in the words, not one Devanagari word in an English sentence: two Hindi words, or as many Hindi
    letters as English ones ("नमस्ते", "PDF चाहिए", "मुझे leave application चाहिए")."""
    hindi_words = sum(1 for w in text.split() if any(0x0900 <= ord(c) <= 0x097F for c in w))
    hindi = sum(1 for c in text if 0x0900 <= ord(c) <= 0x097F)
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    return hindi_words >= 2 or hindi >= latin


def detect(text: str) -> Optional[str]:
    """Dominant language of one transcript, or None if there is nothing to go on."""
    counts = {}
    latin = 0
    for ch in text:
        o = ord(ch)
        if ch.isascii():
            latin += ch.isalpha()
            continue
        for code, lo, hi in SCRIPTS:
            if lo <= o <= hi:
                counts[code] = counts.get(code, 0) + 1
                break
    if counts:
        code = max(counts, key=counts.get)
        if code == "hi-IN":
            return "mr-IN" if _MARATHI.search(text) else "hi-IN"
        if latin > counts[code]:
            return "en-IN"  # mostly Latin with a stray word in another script ("October இல்ல"): not that language
        return code
    return "en-IN" if latin else None


def family_of(code: str) -> str:
    """The voice family for a language: Hindi is BolDoc's Hindi/English (Hinglish) voice."""
    return "hinglish" if code == "hi-IN" else code


@dataclass
class LanguageTracker:
    """The conversation language, with hysteresis. Starts in English, or in the language picked on the website, kept
    until the caller names another. Any Hindi from the caller moves it to Hindi/Hinglish at once.

    `family` drives the voice: "hinglish" (Hindi, Hinglish, occasional English), "en-IN" once a caller has spoken only
    English for two real turns, or another language code.
    """
    current: str = "en-IN"
    english_turns: int = 0
    family: str = "en-IN"
    pending: Optional[str] = None  # another language heard once in a short phrase: the next turn decides
    chosen: Optional[str] = None   # the language the caller asked for: kept until they name another
    remembered: bool = False       # started in the language of their last call, and they haven't spoken since
    words_heard: int = 0           # the caller's words so far: a call they said nothing in teaches nothing

    @classmethod
    def starting(cls, picked: Optional[str], remembered: Optional[str] = None,
                 default: Optional[str] = None) -> LanguageTracker:
        """A call's tracker: the language picked on the website for this call (kept until they name another), else
        the language of their last call (followed as usual if they speak another), else the default for a new caller
        (the agent's settings: English, or Hindi and Hinglish), followed as usual too."""
        picked = "hi-IN" if picked in UNSPOKEN else picked
        remembered = "hi-IN" if remembered in UNSPOKEN else remembered  # a call that drifted into it: Hindi
        if picked and picked != "en-IN":
            return cls(current=picked, chosen=picked, family=family_of(picked))
        if not picked and remembered and remembered != "en-IN" and remembered in NAMES:
            return cls(current=remembered, family=family_of(remembered), remembered=True)
        if not picked and not remembered and default == "hinglish":
            return cls(current="hi-IN", family="hinglish")
        return cls()

    def choose(self, code: str) -> Optional[str]:
        """The caller asked to talk in `code` (their words, or the talker model's set_language): kept until they
        name another. Returns the new voice family if it changed, else None."""
        code = "hi-IN" if code in UNSPOKEN else code
        self.chosen = self.current = code
        self.english_turns, self.pending, self.remembered = 0, None, False
        family = family_of(code)
        if family == self.family:
            return None
        self.family = family
        return family

    def call_language(self) -> Optional[str]:
        """The language to start their next call in: what this call ended in, once they have said something (a
        call they only listened to, or answered in a word, changes nothing)."""
        if self.words_heard < 2 and not self.chosen:
            return None
        return "hi-IN" if self.family == "hinglish" else self.family

    def observe(self, text: str, bot_line: str = "") -> Optional[str]:
        """Feed one final transcript (and BolDoc's line before it, which says whether a language named is the
        document's); returns the new voice family if it changed, else None."""
        self.words_heard += len(text.split())
        asked = asked_for(text, bot_line)
        if asked:
            return self.choose(asked)
        if self.chosen:
            return None  # they said which language they want; a transcript in another script doesn't change it
        lang = detect(text)
        if lang is None:
            return None
        if lang in UNSPOKEN or (lang != "hi-IN" and self.current in HINGLISH and lang in _TO_DEVANAGARI
                                and reads_as_hindi(_TO_DEVANAGARI[lang](text))):
            # Gurmukhi is a Hindi speaker's words (BolDoc doesn't talk in Punjabi), and so is Hindi that
            # speech-to-text wrote in Bengali letters: weighed as the Devanagari it stands for
            text, lang = _TO_DEVANAGARI[lang](text), "hi-IN"
        if lang == "hi-IN" and self.family == "en-IN" and not mostly_hindi(text):
            lang = "en-IN"
        words = len(text.split())
        if words >= 2:
            self.remembered = False  # they're talking now: what they speak decides, as on any call
        if self.current not in HINGLISH and lang in HINGLISH and words >= (2 if lang == "hi-IN" else 4):
            # Hindi, or a real English sentence ("What did you say?"): back to Hindi/English at once. Most callers
            # speak it, and a switch away is as likely a misheard phrase as a real change of language.
            self.current, self.english_turns, self.pending = lang, 0, None
            family = "hinglish"
        elif lang in HINGLISH and self.current in HINGLISH:
            self.pending = None
            self.current = lang
            # Hinglish is one conversation; only a caller who keeps speaking English gets the English voice.
            self.english_turns = self.english_turns + 1 if (lang == "en-IN" and words >= 4) else (
                0 if lang == "hi-IN" else self.english_turns)
            family = "en-IN" if self.english_turns >= 2 else ("hinglish" if lang == "hi-IN" else self.family)
        elif lang == "en-IN":
            # English words are normal inside other Indian languages ("PDF venum"): only sustained English
            # (two turns of 5+ words) moves a Tamil/Telugu/... conversation to English.
            self.english_turns = self.english_turns + 1 if words >= 5 else 0
            if self.english_turns < 2:
                return None
            self.current, family = "en-IN", "en-IN"
        elif words < 3:
            return None  # a word or two ("ok", a name, a misheard word) is not a switch
        elif self.current in HINGLISH and words < 5 and self.pending != lang:
            self.pending = lang  # heard once, briefly: switch only if the next turn is in it too
            return None
        else:
            self.current, self.english_turns, self.pending = lang, 0, None
            family = "hinglish" if lang in HINGLISH else lang
        if family == self.family:
            return None
        self.family = family
        return family

    def prompt_line(self) -> Optional[str]:
        if self.remembered and self.family == "hinglish":  # not said yet on this call: the system prompt says English
            return ("caller's language: Hindi/English (Hinglish), as on their last call. Reply in Hindi/Hinglish the "
                    "way they mix it; switch if they speak another language.")
        if self.chosen in HINGLISH:  # they said so: whatever script their words come in, and whatever came before
            if self.family == "en-IN":
                return ("caller's language: English (they asked for it). Reply in English only, with no Hindi words; "
                        "a question these notes suggest in Hinglish is asked in English.")
            return ("caller's language: Hindi/English (Hinglish), they asked for it. Reply in Hindi/Hinglish the way "
                    "they mix it, even if earlier replies or a transcript line were in another language or script.")
        if self.current in HINGLISH:
            if self.family == "en-IN":  # the start of every call, until they speak another language
                return ("caller's language: English (calls start in English). Reply in English only, with no Hindi "
                        "words; a question these notes suggest in Hinglish is asked in English. Switch as soon as "
                        "they speak another language; if they ask for one, call set_language.")
            return None  # the system prompt already covers Hindi/Hinglish
        name = NAMES.get(self.current, self.current)
        return (f"caller's language: {name} ({self.current}). Reply in {name}, in its own script, short and "
                f"natural; keep English words people use in {name} (PDF, Word) as they are.")
