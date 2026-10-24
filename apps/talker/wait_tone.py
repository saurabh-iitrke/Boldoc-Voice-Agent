"""The soft sound a caller hears while waiting on the line for a long document: two quiet notes every few seconds, so
the line never sounds dead between BolDoc's progress lines.

Made here, not recorded: 8 kHz 16-bit mono, as the call's audio goes out. It is played straight to the line like the
"हम्म" (processors.PlaybackGate.play_clip): never in the model's context, and the transport doesn't count it as BolDoc
speaking, so a caller who talks over it simply starts a turn. Off with WAIT_TONE=0.
"""
from __future__ import annotations

import os

import numpy as np

RATE = 8000
EVERY_S = 4.0       # one motif this often while the caller waits
QUIET_S = 6.0       # ...once BolDoc and the caller have both been quiet this long
PEAK = 0.12         # of full scale (about -18 dBFS): well under speech, a background sound
NOTES = (523.25, 659.25)  # C5 then E5: a soft rising "working on it", not a ring or a beep
NOTE_S, GAP_S, DECAY_S = 0.16, 0.10, 0.06


def enabled() -> bool:
    return os.environ.get("WAIT_TONE", "1") != "0"


def _note(freq: float) -> np.ndarray:
    t = np.arange(int(NOTE_S * RATE)) / RATE
    attack = np.minimum(1.0, t / 0.006)               # 6 ms rise: no click on the line
    tone = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * 2 * freq * t)  # a little body, like a mallet
    return tone * attack * np.exp(-t / DECAY_S)


def motif() -> bytes:
    """One motif: two notes and a short tail of silence (about 0.5 s)."""
    gap = np.zeros(int(GAP_S * RATE))
    x = np.concatenate([_note(NOTES[0]), gap, _note(NOTES[1]), np.zeros(int(0.08 * RATE))])
    x = x / np.max(np.abs(x)) * PEAK
    return (x * 32767).astype(np.int16).tobytes()


CLIP = motif()
