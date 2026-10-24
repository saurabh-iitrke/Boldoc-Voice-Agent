"""LiveKit's end-of-turn model, run as its plugin runs it (apps/talker/eou.py), and BolDoc's हम्म clip (ack.py)."""
import asyncio

import pytest

np = pytest.importorskip("numpy")  # the talker's environment (Python 3.12); the 3.9 run covers control only

from apps.talker import ack, eou  # noqa: E402


def test_prompt_is_the_plugins_chat_template_cut_before_the_callers_end_token():
    p = eou.prompt("Ji, boliye: kya likhwana hai?", "मुझे Goa trip के खर्चे की, Excel sheet…")
    assert p == ("<|im_start|>assistant\nji boliye kya likhwana hai<|im_end|>\n"
                 "<|im_start|>user\nमुझे goa trip के खर्चे की excel sheet")


def test_normalise_keeps_apostrophes_and_hyphens_only():
    assert eou.normalise("  It's   a 20-seat CAFÉ!!  ") == "it's a 20-seat café"
    assert eou.normalise("ﬁle") == "file"  # NFKC


def test_model_covers_hindi_and_english_only():
    assert eou.FAMILY_LANG == {"hinglish": "hi", "en-IN": "en"}


def test_without_the_model_files_the_classifier_carries_on(monkeypatch):
    monkeypatch.setattr(eou, "_model", None)
    assert not eou.ready()
    assert asyncio.run(eou.unfinished("hinglish", "Ji?", "मुझे")) is None


def test_hmm_clip_is_trimmed_softened_and_faded():
    rate = 8000
    t = np.arange(int(0.3 * rate)) / rate
    tone = (8000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
    silence = np.zeros(int(0.2 * rate), dtype=np.int16)
    out = np.frombuffer(ack.shape(np.concatenate([silence, tone, silence]).tobytes()), dtype=np.int16)
    assert 0.3 <= len(out) / rate <= 0.36           # the sound, not the silence around it
    assert np.abs(out).max() <= 8000 * ack.GAIN + 1  # quieter than speech
    assert abs(int(out[0])) < 100 and abs(int(out[-1])) < 100  # no click at either end
    assert ack.shape(silence.tobytes()) == b""
