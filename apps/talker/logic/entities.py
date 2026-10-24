"""Spoken-entity normalisation for Indian callers (English + Hinglish).

STT often returns digits/emails the way people *say* them: "double nine eight one…", "nau aath",
"saurabh at the rate gmail dot com", "dhai lakh". These helpers turn that into canonical values so
the talker can read them back for confirmation (never act on an unconfirmed entity).
"""
from __future__ import annotations

import re
from typing import List, Optional

DIGIT_WORDS = {
    # English
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "to": "2", "too": "2", "three": "3", "four": "4",
    "for": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    # Hindi / Hinglish (romanised, as STT emits in code-mixed mode)
    "shunya": "0", "sunya": "0", "ek": "1", "do": "2", "teen": "3", "tin": "3", "char": "4", "chaar": "4",
    "paanch": "5", "panch": "5", "chhe": "6", "che": "6", "chhah": "6", "saat": "7", "sat": "7", "aath": "8",
    "ath": "8", "nau": "9", "no": "9",
    # Devanagari
    "शून्य": "0", "एक": "1", "दो": "2", "तीन": "3", "चार": "4", "पांच": "5", "पाँच": "5", "छह": "6", "छः": "6",
    "सात": "7", "आठ": "8", "नौ": "9",
}
REPEATERS = {"double": 2, "dabal": 2, "triple": 3, "tripal": 3, "डबल": 2, "ट्रिपल": 3}
# Words that are digits only inside a digit run ("to", "for", "no", "do", "o" are ordinary words otherwise).
AMBIGUOUS = {"to", "too", "for", "no", "do", "o", "oh", "sat", "che", "tin", "ath"}

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_TOKEN = re.compile(r"[\wऀ-ॿ]+|[^\s\w]", re.UNICODE)


def spoken_digits(text: str) -> str:
    """Extract the longest run of spoken/written digits: 'nine eight double one 2 3' -> '981123'."""
    tokens = _TOKEN.findall(text.lower().translate(_DEVANAGARI_DIGITS))
    best, run, pending_repeat = "", "", 1
    for i, tok in enumerate(tokens):
        if tok in REPEATERS:
            pending_repeat = REPEATERS[tok]
            continue
        digit: Optional[str] = None
        if tok.isdigit():
            digit = tok
        elif tok in DIGIT_WORDS:
            neighbours = tokens[max(0, i - 1):i] + tokens[i + 1:i + 2]
            in_run = bool(run) or pending_repeat > 1 or any(
                n.isdigit() or (n in DIGIT_WORDS and n not in AMBIGUOUS) or n in REPEATERS for n in neighbours)
            if tok not in AMBIGUOUS or in_run:
                digit = DIGIT_WORDS[tok]
        if digit is not None:
            run += digit * pending_repeat if len(digit) == 1 else digit
            pending_repeat = 1
        elif tok in {"-", ",", "."} and run:
            continue  # "98123-45678" / pauses rendered as punctuation
        else:
            best, run, pending_repeat = max(best, run, key=len), "", 1
    return max(best, run, key=len)


def indian_mobile(text: str) -> Optional[str]:
    """Return +91XXXXXXXXXX if the utterance contains a valid Indian mobile number, else None."""
    digits = spoken_digits(text)
    for prefix in ("0091", "91", "0"):
        if digits.startswith(prefix) and len(digits) == len(prefix) + 10:
            digits = digits[len(prefix):]
            break
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None


def read_back_mobile(e164: str) -> str:
    """'+919812345678' -> '98123 45678' (5-5 grouping is how Indians say numbers)."""
    d = e164[-10:]
    return f"{d[:5]} {d[5:]}"


_EMAIL_PHRASES = [  # longest first; each becomes a standalone symbol token
    ("at the rate of", "@"), ("at the rate", "@"), ("at sign", "@"), ("at", "@"),
    ("full stop", "."), ("dot", "."), ("period", "."), ("point", "."),
    ("under score", "_"), ("underscore", "_"), ("hyphen", "-"), ("dash", "-"), ("minus", "-"),
]
_EMAIL_RE = re.compile(r"^[a-z0-9][a-z0-9._%+-]*@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")
_CONNECTORS = {".", "_", "-"}


def _is_word(tok: str) -> bool:
    return bool(tok) and tok not in _CONNECTORS and tok != "@"


def spoken_email(text: str) -> Optional[str]:
    """'saurabh dot k at the rate gmail dot com' -> 'saurabh.k@gmail.com' (None if not a plausible email).

    Token-based so filler words before the address ("my email is ...") never leak into it.
    """
    low = f" {text.lower().strip()} "
    for tok in low.split():  # an address already written out wins
        cand = tok.strip(",;:!?()\"'")
        if _EMAIL_RE.match(cand):
            return cand
    for phrase, sym in _EMAIL_PHRASES:
        low = re.sub(rf"(?<=\s){re.escape(phrase)}(?=\s)", f" {sym} ", low)
    toks = low.split()
    for i in range(len(toks) - 1, -1, -1):  # prefer the last '@' (the address usually ends the utterance)
        if toks[i] != "@":
            continue
        # local part: last word, plus words linked by spoken connectors, plus spelled-out single letters
        j = i - 1
        if j < 0 or not _is_word(toks[j]):
            continue
        local = [toks[j]]
        if toks[j].isdigit() and j - 1 >= 0 and _is_word(toks[j - 1]) and not toks[j - 1].isdigit():
            local.insert(0, toks[j - 1])  # spoken digits attach to the preceding word: "sharma 12"
            j -= 1
        while j - 2 >= 0 and toks[j - 1] in _CONNECTORS and _is_word(toks[j - 2]):
            local = [toks[j - 2], toks[j - 1], *local]
            j -= 2
        if len(local[0]) == 1:
            while j - 1 >= 0 and len(toks[j - 1]) == 1 and toks[j - 1].isalnum():
                local.insert(0, toks[j - 1])
                j -= 1
        # domain: words linked by '.' (or '-')
        k = i + 1
        if k >= len(toks) or not _is_word(toks[k]):
            continue
        domain = [toks[k]]
        while k + 2 < len(toks) and toks[k + 1] in {".", "-"} and _is_word(toks[k + 2]):
            domain += [toks[k + 1], toks[k + 2]]
            k += 2
        cand = "".join(local) + "@" + "".join(domain)
        if _EMAIL_RE.match(cand):
            return cand
    return None


def spell_for_readback(value: str) -> List[str]:
    """Characters as they should be read back: letters with phonetic hints, symbols named."""
    phonetic = {"a": "A for Apple", "b": "B for Bombay", "c": "C for Chennai", "d": "D for Delhi",
                "e": "E for Elephant", "f": "F for Fish", "g": "G for Goa", "h": "H for Hyderabad",
                "i": "I for India", "j": "J for Jaipur", "k": "K for Kolkata", "l": "L for Lucknow",
                "m": "M for Mumbai", "n": "N for Nagpur", "o": "O for Orange", "p": "P for Pune",
                "q": "Q for Queen", "r": "R for Rajkot", "s": "S for Surat", "t": "T for Tiger",
                "u": "U for Udaipur", "v": "V for Varanasi", "w": "W for Water", "x": "X for X-ray",
                "y": "Y for Yellow", "z": "Z for Zebra"}
    names = {"@": "at the rate", ".": "dot", "_": "underscore", "-": "hyphen"}
    return [phonetic.get(ch, names.get(ch, ch)) for ch in value.lower()]


_HINDI_FRACTIONS = {"sawa": 1.25, "saade": None, "dedh": 1.5, "dhai": 2.5, "paune": None}
_SCALE = {"lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "crore": 10_000_000, "crores": 10_000_000,
          "cr": 10_000_000, "hazar": 1_000, "hazaar": 1_000, "thousand": 1_000, "k": 1_000}


def spoken_amount(text: str) -> Optional[float]:
    """'dhai lakh' -> 250000, 'saade teen lakh' -> 350000, '2.5 crore' -> 25000000, '15 hazar' -> 15000."""
    toks = text.lower().replace(",", "").split()
    for i, tok in enumerate(toks):
        if tok in _SCALE and i > 0:
            prev = toks[i - 1]
            base: Optional[float] = None
            if re.fullmatch(r"\d+(\.\d+)?", prev):
                base = float(prev)
            elif prev in DIGIT_WORDS and DIGIT_WORDS[prev].isdigit():
                base = float(DIGIT_WORDS[prev])
            elif _HINDI_FRACTIONS.get(prev):
                base = _HINDI_FRACTIONS[prev]
            if base is not None and i >= 2:
                if toks[i - 2] == "saade":
                    base += 0.5
                elif toks[i - 2] == "paune":
                    base -= 0.25
                elif toks[i - 2] == "sawa":
                    base += 0.25
            if base is not None:
                return base * _SCALE[tok]
    return None
