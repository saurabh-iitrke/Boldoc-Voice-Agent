"""Pick TTS voices by measurement: synthesise BolDoc's own lines with candidate voices at phone quality
(8 kHz through μ-law), time the synthesis, and score intelligibility by transcribing the result with Sarvam's
realtime STT (character error rate after normalisation). Writes WAVs + results.json for the listening page.

Usage: python -I tools/voices/voice_eval.py <sarvam key file> <out dir>       (never prints the key)
"""
from __future__ import annotations

import asyncio
import audioop
import base64
import io
import json
import re
import sys
import time
import unicodedata
import wave
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode

import httpx
import websockets

LINES = {
    "hinglish": ("hi-IN", "ठीक है, मैं आपकी leave application अभी लिख रही हूँ। PDF और Word दोनों SMS पर भेज दूँगी।"),
    "en": ("en-IN", "Sure, I'm writing your leave application now. You'll get the PDF and Word files by SMS."),
    "ta": ("ta-IN", "சரி, உங்கள் விடுப்பு கடிதத்தை இப்போது எழுதுகிறேன். PDF மற்றும் Word இரண்டும் SMS-ல் வரும்."),
    "te": ("te-IN", "సరే, మీ సెలవు లేఖను ఇప్పుడే రాస్తున్నాను. PDF మరియు Word రెండూ SMS ద్వారా వస్తాయి."),
    "kn": ("kn-IN", "ಸರಿ, ನಿಮ್ಮ ರಜೆ ಅರ್ಜಿಯನ್ನು ಈಗ ಬರೆಯುತ್ತಿದ್ದೇನೆ. PDF ಮತ್ತು Word ಎರಡೂ SMS ಮೂಲಕ ಬರುತ್ತವೆ."),
    "mr": ("mr-IN", "ठीक आहे, मी तुमचा रजेचा अर्ज आता लिहित आहे. PDF आणि Word दोन्ही SMS वर येतील."),
    "bn": ("bn-IN", "ঠিক আছে, আমি এখন আপনার ছুটির আবেদন লিখছি। PDF আর Word দুটোই SMS-এ আসবে।"),
    "gu": ("gu-IN", "સારું, હું હમણાં તમારી રજાની અરજી લખું છું. PDF અને Word બંને SMS પર આવશે."),
    "pa": ("pa-IN", "ਠੀਕ ਹੈ, ਮੈਂ ਹੁਣੇ ਤੁਹਾਡੀ ਛੁੱਟੀ ਦੀ ਅਰਜ਼ੀ ਲਿਖ ਰਹੀ ਹਾਂ। PDF ਅਤੇ Word ਦੋਵੇਂ SMS ਰਾਹੀਂ ਆਉਣਗੇ।"),
    "ml": ("ml-IN", "ശരി, നിങ്ങളുടെ അവധി അപേക്ഷ ഇപ്പോൾ എഴുതുകയാണ്. PDF ഉം Word ഉം SMS വഴി വരും."),
    "od": ("od-IN", "ଠିକ ଅଛି, ମୁଁ ଆପଣଙ୍କ ଛୁଟି ଆବେଦନ ଏବେ ଲେଖୁଛି। PDF ଏବଂ Word ଦୁଇଟି SMS ରେ ଆସିବ।"),
}
V4, V3 = "bulbul:v4-flash", "bulbul:v3"
CANDIDATES = {  # (model, speaker): female-sounding conversational/customer personas first
    "hinglish": [(V4, "kavya_hi_conversational"), (V4, "aparna_hi_customer"), (V4, "aarti_hi_customer"),
                 (V4, "simran_hi_conversation"), (V4, "simran_enhi_customer"), (V4, "ishita_enhi_customer"),
                 (V4, "shalini_enhi_customer")],
    "en": [(V4, "aparna_en_companion"), (V4, "sanchita_en_companion"), (V4, "simran_en_customer"),
           (V4, "ishita_en_customer"), (V4, "simran_enhi_customer")],
    "ta": [(V4, "aravind_ta_ads"), (V4, "gokul_ta_narration"), (V3, "simran"), (V3, "priya"), (V3, "kavya")],
    "te": [(V4, "kavitha_te_conversation"), (V4, "pooja_te_conversation"), (V3, "simran")],
    "kn": [(V4, "chaitra_kn_conversation"), (V4, "chetan_kn_conversation"), (V3, "simran")],
    "mr": [(V4, "ishita_mr_conversational"), (V4, "nilesh_mr_conversation"), (V3, "simran")],
    "bn": [(V4, "roopa_bn_conversational"), (V4, "arnab_bn_conversation"), (V3, "simran")],
    "gu": [(V4, "pooja_gu_conversational"), (V4, "pooja_gu_customer"), (V3, "simran")],
    "pa": [(V4, "anand_pa_conversation"), (V4, "harpreet_pa_narration"), (V3, "simran")],
    "ml": [(V3, "simran"), (V3, "priya"), (V3, "kavya")],
    "od": [(V3, "simran"), (V3, "priya"), (V3, "kavya")],
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s.lower())
    return re.sub(r"[\W_]+", "", s)


def cer(ref: str, hyp: str) -> float:
    a, b = norm(ref), norm(hyp)
    if not a:
        return 1.0
    return round(1 - SequenceMatcher(None, a, b).ratio(), 3)


def phone(wav_b64: str) -> bytes:
    with wave.open(io.BytesIO(base64.b64decode(wav_b64))) as w:
        pcm, rate = w.readframes(w.getnframes()), w.getframerate()
    if rate != 8000:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, 8000, None)
    return audioop.ulaw2lin(audioop.lin2ulaw(pcm, 2), 2)  # what a caller hears: 8 kHz μ-law


async def tts(c: httpx.AsyncClient, key: str, model: str, speaker: str, lang: str, text: str):
    body = {"text": text, "target_language_code": lang, "model": model, "speaker": speaker,
            "speech_sample_rate": 8000}
    t0 = time.perf_counter()
    r = await c.post("https://api.sarvam.ai/text-to-speech", headers={"api-subscription-key": key}, json=body)
    dt = time.perf_counter() - t0
    if r.status_code != 200:
        return None, dt, f"HTTP {r.status_code}: {r.text[:120]}"
    return phone(r.json()["audios"][0]), dt, None


async def stt(key: str, pcm8: bytes) -> str:
    pcm16, _ = audioop.ratecv(pcm8, 2, 1, 8000, 16000, None)
    q = {"language_code": "auto", "stream_type": "fast", "endpointing": "manual", "encoding": "linear16",
         "sample_rate": 16000, "model": "saaras:v3-realtime", "mode": "codemix"}
    finals = []
    async with websockets.connect("wss://api.sarvam.ai/speech-to-text-realtime/ws?" + urlencode(q),
                                  additional_headers={"api-subscription-key": key}, max_size=None) as ws:
        async def recv():
            async for raw in ws:
                m = json.loads(raw)
                if m.get("event") == "transcript.final":
                    finals.append(m.get("text", ""))
        task = asyncio.create_task(recv())
        await ws.send(json.dumps({"event": "speech_start"}))
        for i in range(0, len(pcm16), 6400):
            await ws.send(json.dumps({"event": "audio_input", "audio": base64.b64encode(pcm16[i:i + 6400]).decode()}))
        await ws.send(json.dumps({"event": "speech_end"}))
        t0 = time.time()
        while not finals and time.time() - t0 < 8:
            await asyncio.sleep(0.05)
        task.cancel()
    return " ".join(finals)


def to_wav(pcm8: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(pcm8)
    return buf.getvalue()


async def main(key_file: str, out_dir: str) -> None:
    key = Path(key_file).read_text().strip().splitlines()[0]
    out = Path(out_dir)
    (out / "wav").mkdir(parents=True, exist_ok=True)
    results = []
    async with httpx.AsyncClient(timeout=60) as c:
        for family, voices in CANDIDATES.items():
            lang, text = LINES[family]
            for model, speaker in voices:
                pcm, dt, err = await tts(c, key, model, speaker, lang, text)
                row = {"family": family, "lang": lang, "model": model, "speaker": speaker, "text": text,
                       "synth_s": round(dt, 2)}
                if err:
                    row["error"] = err
                else:
                    heard = await stt(key, pcm)
                    name = f"{family}-{speaker}-{model.split(':')[1]}.wav"
                    (out / "wav" / name).write_bytes(to_wav(pcm))
                    row.update({"heard": heard, "cer": cer(text, heard), "wav": f"wav/{name}",
                                "seconds": round(len(pcm) / 16000, 2)})
                results.append(row)
                print(f"{family:<9} {model:<16} {speaker:<26} synth {row['synth_s']:>5}s "
                      + (f"CER {row['cer']:.2f}" if "cer" in row else row.get("error", "")))
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
