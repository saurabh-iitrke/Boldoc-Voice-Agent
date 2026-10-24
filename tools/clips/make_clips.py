"""Short phone clips for calls BolDoc can't take (busy, already on a call, time up), in BolDoc's own voice.

Played by Plivo (<Play>) from the website's /clips/ path, so a refused caller hears the same "Simran" voice as a
normal call, not a generic text-to-speech voice. Re-run after changing a line; the files are committed.

Usage: .venv/bin/python tools/clips/make_clips.py --key-file sarvam_key.txt
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from apps.talker.logic.claims import speakable  # BolDoc said as calls say it: "बोल-डॉक"

OUT = Path(__file__).resolve().parents[2] / "web" / "public" / "clips"
VOICE = {"model": "bulbul:v4-flash", "speaker": "simran_enhi_customer", "target_language_code": "hi-IN"}
CLIPS = {
    "busy": "Namaste, main BolDoc hoon. Abhi saari lines busy hain. Kripya do-teen minute baad phir se call kijiye. "
            "Dhanyavaad!",
    "already-on-call": "Namaste, main BolDoc hoon. Aap pehle se ek call par mujhse baat kar rahe hain. Us call ko "
                       "khatam karke phir se call kijiye.",
    "refused": "Namaste, main BolDoc hoon. Maaf kijiye, yeh call abhi connect nahi ho sakti. Kripya thodi der baad "
               "phir koshish kijiye.",
    "time-up": "Is call ka samay poora ho gaya hai. Aapka document save hai. Dhanyavaad, phir milte hain!",
    "trial-over": "Namaste, main BolDoc hoon. Aapke teen free calls poore ho gaye hain. Aage ke liye BolDoc ki "
                  "website par isi number se sign in kijiye, aapko do hazaar rupaye free milenge. Link aapko SMS "
                  "par bheja "
                  "hai. Dhanyavaad!",
    "no-credits": "Namaste, main BolDoc hoon. Aapke account ka balance khatam ho gaya hai. BolDoc ki website par "
                  "apna balance dekh sakte hain. Dhanyavaad!",
    "verified": "Dhanyavaad! Aapka number verify ho gaya hai. Ab BolDoc ki website par wapas jaiye.",
    "withheld": "Namaste, main BolDoc hoon. Aapka number nahi dikh raha hai. Kripya apna number dikhate hue phir se "
                "call kijiye. Dhanyavaad!",
    "too-frequent": "Namaste, main BolDoc hoon. Is number se abhi kai baar call ho chuki hai. Kripya aadhe ghante "
                    "baad phir se call kijiye. Dhanyavaad!",
    "daily-limit": "Namaste, main BolDoc hoon. Is number ki aaj ki call limit poori ho gayi hai. Kripya kal phir se "
                   "call kijiye. Dhanyavaad!",
    "trial-budget": "Namaste, main BolDoc hoon. Aaj ke free calls abhi poore ho gaye hain. BolDoc ki website par sign "
                    "in karke call kar sakte hain, ya kal phir koshish kijiye. Dhanyavaad!",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--only", nargs="*", help="clip names to (re)make")
    a = ap.parse_args()
    key = Path(a.key_file).read_text().strip().splitlines()[0]
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=30) as c:
        for name, text in CLIPS.items():
            if a.only and name not in a.only:
                continue
            r = c.post("https://api.sarvam.ai/text-to-speech", headers={"api-subscription-key": key},
                       json={"text": speakable(text), **VOICE, "speech_sample_rate": 8000})
            if r.status_code != 200:
                print(f"{name}: HTTP {r.status_code} {r.text[:200]}")
                return 1
            wav = base64.b64decode(r.json()["audios"][0])
            (OUT / f"{name}.wav").write_bytes(wav)
            print(f"{name}.wav  {len(wav) // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
