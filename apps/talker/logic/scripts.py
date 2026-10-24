"""Gurmukhi (and Bengali) to Devanagari, letter for letter: speech-to-text sometimes writes a Hindi speaker's words in
Gurmukhi ("ਮੈਂ ਹਿੰਦੀ ਸਪੀਕਰ ਹਾਂ"), and the talker model, seeing Punjabi letters (and then its own Punjabi replies),
answered in Punjabi even after "I am Hindi speaker". While the caller's language is Hindi/English, the model gets
such a transcript back in Devanagari ("मैं हिंदी सपीकर हां"), and a reply that still comes out in Gurmukhi is spoken,
and kept in the history, in Devanagari.

The two Unicode blocks share one layout (Devanagari = Gurmukhi - 0x100) except a few Gurmukhi-only signs.

Bengali the same way (Devanagari = Bengali - 0x80, but for a few Bengali-only signs).
"""
from __future__ import annotations

_GURMUKHI = range(0x0A00, 0x0A80)
_TIPPI, _ADDAK, _IRI, _URA = "ੰ", "ੱ", "ੲ", "ੳ"
_VIRAMA = "्"
# a vowel sign on the Gurmukhi vowel carriers ੲ / ੳ is an independent vowel in Devanagari
_CARRIED = {(_IRI, "ਿ"): "इ", (_IRI, "ੀ"): "ई", (_IRI, "ੇ"): "ए",
            (_URA, "ੁ"): "उ", (_URA, "ੂ"): "ऊ", (_URA, "ੋ"): "ओ"}


def gurmukhi_to_devanagari(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if (ch, nxt) in _CARRIED:
            out.append(_CARRIED[(ch, nxt)])
            i += 2
            continue
        if ch == _TIPPI:
            out.append("ं")  # anusvara
        elif ch == _ADDAK:  # doubles the next consonant: ਗੱਲ -> गल्ल
            if nxt and ord(nxt) in _GURMUKHI:
                doubled = chr(ord(nxt) - 0x100)
                out.append(doubled + _VIRAMA)
        elif ch in (_IRI, _URA):
            out.append("अ" if ch == _IRI else "उ")
        elif ord(ch) in _GURMUKHI:
            out.append(chr(ord(ch) - 0x100))
        else:
            out.append(ch)
        i += 1
    return "".join(out)


_BENGALI = range(0x0980, 0x09F0)  # letters, signs and digits; the currency and fraction signs after them stay
_BENGALI_ONLY = {"ৎ": "त्", "ৰ": "र", "ৱ": "व"}  # khanda ta; Assamese ra and wa


def bengali_to_devanagari(text: str) -> str:
    return "".join(_BENGALI_ONLY.get(ch) or (chr(ord(ch) - 0x80) if ord(ch) in _BENGALI else ch) for ch in text)


TO_DEVANAGARI = {"pa-IN": gurmukhi_to_devanagari, "bn-IN": bengali_to_devanagari}
