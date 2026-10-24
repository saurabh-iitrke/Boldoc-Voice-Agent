"""BolDoc's "हम्म" while a caller pauses mid-sentence: the listening sound people make on the phone, in the call's own
voice. It is synthesised once per voice, trimmed, softened and played straight to the line (processors.PlaybackGate):
not a reply, never in the model's context, and the caller's turn stays open.
"""
from __future__ import annotations

import asyncio
import base64
import io
import wave
from typing import Dict, Optional, Tuple

import httpx
import numpy as np
from loguru import logger

TTS_URL = "https://api.sarvam.ai/text-to-speech"
TEXT = "हम्म"
RATE = 8000
GAIN = 0.6         # quieter than speech: a listening sound, not a turn
FRAME = RATE // 100  # 10 ms
_clips: Dict[Tuple[str, str], bytes] = {}
_locks: Dict[Tuple[str, str], asyncio.Lock] = {}


def shape(pcm: bytes) -> bytes:
    """Trim the silence around the sound, soften it and fade its ends (no click on the line)."""
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n = len(x) // FRAME
    if n == 0:
        return b""
    rms = np.sqrt(np.mean(x[:n * FRAME].reshape(n, FRAME) ** 2, axis=1))
    voiced = np.flatnonzero(rms > 300)
    if voiced.size == 0:
        return b""
    a, b = max(0, voiced[0] - 2) * FRAME, min(n, voiced[-1] + 3) * FRAME
    y = x[a:b] * GAIN
    fade = min(len(y) // 4, 2 * FRAME)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        y[:fade] *= ramp
        y[-fade:] *= ramp[::-1]
    return np.clip(y, -32768, 32767).astype(np.int16).tobytes()


def _pcm(wav_b64: str) -> bytes:
    with wave.open(io.BytesIO(base64.b64decode(wav_b64))) as w:
        if w.getframerate() != RATE or w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(f"unexpected audio {w.getframerate()} Hz x{w.getnchannels()}")
        return w.readframes(w.getnframes())


async def clip(api_key: str, model: str, speaker: str, language: str) -> Optional[bytes]:
    """The voice's "हम्म" (8 kHz 16-bit mono), fetched once per process; None if it can't be made."""
    key = (model, speaker)
    if key in _clips:
        return _clips[key]
    async with _locks.setdefault(key, asyncio.Lock()):
        if key not in _clips:
            body = {"text": TEXT, "target_language_code": language, "model": model, "speaker": speaker,
                    "speech_sample_rate": RATE}
            try:
                async with httpx.AsyncClient(timeout=10.0) as c:
                    r = await c.post(TTS_URL, headers={"api-subscription-key": api_key}, json=body)
                    r.raise_for_status()
                pcm = shape(_pcm(r.json()["audios"][0]))
                if not pcm:
                    raise ValueError("no sound")
            except Exception as e:  # no "हम्म" on this voice: the hold just stays silent, as before
                logger.warning(f"ack: no listening sound for {speaker} ({type(e).__name__}: {e})")
                return None
            _clips[key] = pcm
    return _clips[key]
