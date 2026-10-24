"""A caller's side of a call as a WAV, for a browser's fake microphone (Chrome --use-file-for-fake-audio-capture).

The scripted lines are spoken by Sarvam TTS (cached, like the fake phone caller's), with pauses that leave BolDoc
room to greet and answer; a long silence at the end keeps Chrome from looping the request during the test.

  .venv312/bin/python tests/talker_e2e/caller_wav.py --key-file sarvam_key.txt --out /tmp/caller.wav [--scenario doc]
"""
from __future__ import annotations

import argparse
import asyncio
import audioop
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from apps.control.testcall.caller import SCENARIOS, synth  # the scripted caller, shared

RATE = 48000          # Chrome's capture rate: no resampling surprises
GREETING_S = 7.0      # BolDoc greets first (about 5.5 s)
AFTER_LINE_S = 7.0    # room for BolDoc's reply before the next line
TAIL_S = 120.0


def pcm(mulaw_8k: bytes) -> bytes:
    lin = audioop.ulaw2lin(mulaw_8k, 2)
    return audioop.ratecv(lin, 2, 1, 8000, RATE, None)[0]


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(RATE * seconds)


async def build(key: str, scenario: str) -> bytes:
    out = [silence(GREETING_S)]
    for step, arg in SCENARIOS[scenario]:
        if step == "say":
            out += [pcm(await synth(key, arg)), silence(AFTER_LINE_S)]
        elif step == "listen":
            out.append(silence(float(arg)))
    out.append(silence(TAIL_S))
    return b"".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scenario", default="doc")
    a = ap.parse_args()
    key = Path(a.key_file).read_text().strip()
    audio = asyncio.run(build(key, a.scenario))
    with wave.open(a.out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio)
    print(f"{a.out}: {len(audio) / 2 / RATE:.0f} s")


if __name__ == "__main__":
    main()
