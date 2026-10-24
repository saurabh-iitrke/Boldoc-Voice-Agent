"""Fetch LiveKit's end-of-turn model files into the talker image (run once at build time), checked against pinned
hashes so a changed upload never reaches a call. Usage: python eou_fetch.py [OUT_DIR]"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

BASE = "https://huggingface.co/livekit/turn-detector/resolve/v0.4.1-intl/"
FILES = {  # path in the repo -> sha256
    "onnx/model_q8.onnx": "bd2c30776882138a1d95a07faddc13756fe1a35bef6323505f1124fca349bc9c",
    "tokenizer.json": "9c5ae00e602b8860cbd784ba82a8aa14e8feecec692e7076590d014d7b7fdafa",
    "languages.json": "05a16e11e4282173331dbf140624048e581a727ba45c361bed35e3014ad68187",
    "LICENSE": "4f5b74db4fd26299a166688de8a3bde046fd7d9cc13ecab64532c24310bc18d7",
}


def main(out: Path) -> None:
    for name, sha in FILES.items():
        dst = out / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(BASE + name, dst)
        h = hashlib.sha256()
        with open(dst, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != sha:
            sys.exit(f"{name}: sha256 {h.hexdigest()} != pinned {sha}")
        print(f"{name}: ok")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/eou"))
