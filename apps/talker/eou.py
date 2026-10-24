"""Has the caller finished, or only paused mid-thought? LiveKit's multilingual end-of-turn model
(livekit/turn-detector, revision v0.4.1-intl), run the way LiveKit's own plugin runs it: BolDoc's last line and the
caller's words, normalised (NFKC, lower case, no punctuation but ' and -), in the model's chat template and cut just
before the caller's end-of-turn token; the model's last output is the probability that the caller has finished.
Below the language's tuned threshold (languages.json) they haven't.

It covers Hindi and English among BolDoc's languages; other languages keep the word rules. The files are fetched into
the talker image at build time (eou_fetch.py, pinned by hash); without them the Sarvam classifier is used, as before.
Inference runs on a worker thread with one ONNX thread: the box's two vCPUs also carry every live call's audio.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from loguru import logger

MODEL_DIR = Path(os.environ.get("EOU_MODEL_DIR", "/opt/eou"))
REVISION = "v0.4.1-intl"
# The plugin keeps 128 tokens; Devanagari is several tokens a word, so a read-back plus the caller's words filled them
# and a check took 0.75-1.6 s on the box during calls. The last 32 tokens scored the same on our 268 utterances (P 73%,
# R 75%, finished held 8%) at a quarter of the work.
MAX_TOKENS = 32
FAMILY_LANG = {"hinglish": "hi", "en-IN": "en"}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text.lower())
    text = "".join(ch for ch in text if not (unicodedata.category(ch).startswith("P") and ch not in "'-"))
    return re.sub(r"\s+", " ", text).strip()


def prompt(bot_line: str, caller_text: str) -> str:
    """The model's chat template (<|im_start|>role\\ncontent<|im_end|>) over BolDoc's line and the caller's words,
    cut before the caller's <|im_end|>: the model scores whether that token comes next."""
    turns = [("assistant", normalise(bot_line)), ("user", normalise(caller_text))]
    convo = "".join(f"<|im_start|>{role}\n{text}<|im_end|>\n" for role, text in turns if text)
    return convo[:convo.rfind("<|im_end|>")]


class EndOfTurn:
    def __init__(self, model_dir: Path = MODEL_DIR):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._np = np
        self._tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.thresholds = {k: v["threshold"] for k, v in json.loads((model_dir / "languages.json").read_text()).items()}
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._session = ort.InferenceSession(str(model_dir / "onnx" / "model_q8.onnx"),
                                             providers=["CPUExecutionProvider"], sess_options=opts)
        self.probability("Ji, boliye.", "mujhe ek letter likhna hai")  # first run allocates: not on a call

    def probability(self, bot_line: str, caller_text: str) -> float:
        ids = self._tok.encode(prompt(bot_line, caller_text), add_special_tokens=False).ids[-MAX_TOKENS:]
        out = self._session.run(None, {"input_ids": self._np.array([ids], dtype=self._np.int64)})
        return float(out[0].flatten()[-1])

    def unfinished(self, family: str, bot_line: str, caller_text: str) -> Optional[tuple]:
        """(unfinished?, probability), or None for a language the model doesn't cover."""
        lang = FAMILY_LANG.get(family)
        if lang is None or lang not in self.thresholds:
            return None
        p = self.probability(bot_line, caller_text)
        return p < self.thresholds[lang], p


_model: Optional[EndOfTurn] = None
_failed = False
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="eou")  # never queued behind other blocking work


def load() -> Optional[EndOfTurn]:
    """Load the process's model (once, at startup: it takes a second or two), or None when its files aren't there."""
    global _model, _failed
    if _model is None and not _failed:
        try:
            _model = EndOfTurn()
            logger.info(f"end-of-turn model loaded ({REVISION})")
        except Exception as e:  # missing files or packages: the classifier carries on
            _failed = True
            logger.warning(f"end-of-turn model unavailable ({type(e).__name__}: {e}); using the classifier")
    return _model


def ready() -> bool:
    """Loaded at startup (server.py): a call never waits for the model to load."""
    return _model is not None


async def unfinished(family: str, bot_line: str, caller_text: str) -> Optional[tuple]:
    if _model is None:
        return None
    return await asyncio.get_running_loop().run_in_executor(_executor, _model.unfinished, family, bot_line,
                                                            caller_text)
