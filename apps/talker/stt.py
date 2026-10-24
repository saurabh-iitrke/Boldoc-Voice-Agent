"""Sarvam realtime STT for phone calls: every language, and no clipped first words.

* Language: `language_code=auto` (saaras:v3-realtime).
  transcripts for Hindi, Hinglish, English, Tamil and Telugu (with `hi-IN` fixed, Tamil and Telugu came back as
  Devanagari gibberish). The model's `language` label was unreliable (Tamil labelled en-IN), so the talker
  decides the language from the transcript's script (see logic/language.py), not from the label.
* Pre-roll: with manual endpointing our VAD confirms speech ~0.2 s after it starts, and Sarvam ignores audio
  sent before `speech_start` ("PDF और Word" came back as "NDF और VAT"; short answers like "teen" vanished on a
  On `speech_start` we re-send the last 0.6 s, which restores the first word without duplicating it
"""
from __future__ import annotations

from collections import deque

from pipecat.frames.frames import Frame, VADUserStartedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.sarvam.stt import SarvamRealtimeSTTService

PREROLL_S = 0.6
DOMAIN_PROMPT = ("Phone call to BolDoc, an assistant that writes documents. Callers mention formats (PDF, Word, "
                 "Excel, PowerPoint), letters, applications, invoices, GST, amounts in rupees, dates and names.")


class PrerollSarvamSTT(SarvamRealtimeSTTService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._recent: deque = deque()
        self._recent_bytes = 0

    def _remember(self, chunk: bytes) -> None:
        self._recent.append(chunk)
        self._recent_bytes += len(chunk)
        limit = int(self.sample_rate * 2 * PREROLL_S) if self.sample_rate else 19200
        while self._recent and self._recent_bytes - len(self._recent[0]) >= limit:
            self._recent_bytes -= len(self._recent.popleft())

    async def _send_audio_chunk(self, chunk: bytes):
        await super()._send_audio_chunk(chunk)
        if not self._manual_turn_open:
            self._remember(chunk)  # what the server will ignore until speech_start: keep it for the re-send

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)  # sends speech_start under manual endpointing
        if isinstance(frame, VADUserStartedSpeakingFrame) and self._recent:
            preroll = b"".join(self._recent)
            self._recent.clear()
            self._recent_bytes = 0
            chunk = self._chunk_size_bytes()
            for i in range(0, len(preroll), chunk):
                await super()._send_audio_chunk(preroll[i:i + chunk])
