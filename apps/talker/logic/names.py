"""The caller's own name, when they say it plainly ("mera naam Saurabh Kumar hai", "मेरा नाम है सौरभ", "my name is
Saurabh"): BolDoc uses it for the rest of the call, and the next call's welcome greets them by it. Any other way of
giving it ("Aapka naam?" "Saurabh") is the writer's check's to recognise, by meaning (thinker/intake.py caller_name)."""
from __future__ import annotations

import re
from typing import Optional

_TOKEN = r"[^\s,।.?!;:]+"
_PATTERNS = [
    re.compile(rf"(?:mera|meraa|मेरा)\s+(?:naam|nam|नाम)\s+(?:hai\s+|है\s+)?(?P<n>{_TOKEN}(?:\s+{_TOKEN})?)", re.I),
    re.compile(r"\bmy\s+name\s+is\s+(?P<n>[A-Za-z]+(?:\s+[A-Za-z]+)?)", re.I),
    re.compile(rf"(?:\bmain|मैं)\s+(?P<n>{_TOKEN}(?:\s+{_TOKEN})?)\s+(?:bol|बोल)\s+(?:raha|rahi|रहा|रही)", re.I),
]
# Words that follow "mera naam" without being a name ("mera naam kya likhna hai", "mera naam hai ki ...").
_NOT_NAME = {"hai", "है", "ji", "जी", "ka", "का", "ki", "की", "ke", "के", "bhi", "भी", "kya", "क्या", "aur", "और",
             "to", "तो", "toh", "ye", "यह", "yeh", "wo", "वो", "likh", "likhna", "likho", "likhiye", "लिख", "लिखना",
             "daal", "daalo", "dalna", "डाल", "se", "से", "mein", "में", "par", "पर", "is", "the", "a", "and", "not"}


def spoken_name(text: str) -> Optional[str]:
    """The name the caller says is theirs, at most first and last name; None if this isn't one."""
    for pattern in _PATTERNS:
        m = pattern.search(text or "")
        if not m:
            continue
        words = m.group("n").split()
        if not words or words[0].lower() in _NOT_NAME:
            return None
        words = [w for w in words[:2] if w.lower() not in _NOT_NAME]
        if not words or any(len(w) < 2 or any(ch.isdigit() for ch in w) for w in words):
            return None
        return " ".join(w.capitalize() if w.isascii() else w for w in words)
    return None


def first_name(name: Optional[str]) -> Optional[str]:
    return name.split()[0] if name else None
