"""Acoustic end-to-end test: a fake Plivo caller talks to the talker over the real stream protocol.

The scripted caller itself (scenarios, voices, re-asks, the step loop) is apps/control/testcall/caller.py, shared
with the admin console's "Test call"; this is its command line for scripts/e2e_gate.sh, which parses the output.

Usage: python tests/talker_e2e/fake_caller.py --key-file sarvam_key.txt --ws ws://localhost:8090/plivo/stream?k=testkey&test=1
       [--scenario basic|doc|interrupt|pause|sense|tamil] [--call-id test-…]
A scripted call is anonymous (no number, no SMS) unless it was admitted first with a caller number:
  docker compose exec control python -m apps.control.calls admit <call-id> 919xxxxxxxxx --test
(scripts/e2e_gate.sh does this when ADMIT_FROM is set; a real Indian mobile then gets a real SMS).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # run as a script from anywhere in the repo

from apps.control.testcall.caller import (
    SCENARIOS,
    ScriptedCall,
    asked_again,  # noqa: F401  (re-exported: tests and tools import it from here)
    transcribe,
)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--ws", required=True)
    ap.add_argument("--out", default="/tmp/boldoc-e2e")
    ap.add_argument("--scenario", default="basic", choices=sorted(SCENARIOS))
    ap.add_argument("--call-id", default="", help="the call's id (admit it first to give it a caller number)")
    a = ap.parse_args()
    key = Path(a.key_file).read_text().strip().splitlines()[0]
    out = Path(a.out)
    out.mkdir(exist_ok=True)
    call_id = a.call_id or f"test-{int(time.time())}"

    def on_event(kind: str, data: dict) -> None:
        if kind == "greeting":
            print(f"greeting: {data['seconds']:.1f}s of audio")

    call = ScriptedCall(key, a.ws, a.scenario, call_id, on_event=on_event, out_dir=out)
    result = await call.run()

    print(f"\ncall {call_id}\n{'step':<70} v2v latency")
    if result.ended_by_bolDoc:
        print("(BolDoc ended the call)")
    for step in result.steps:
        lat = step.latency
        shown = f"{lat * 1000:.0f} ms" if lat is not None else ("NO REPLY" if not step.audio else "-")
        print(f"{step.label[:68]:<70} {shown}")
    print("\nwhat BolDoc said (transcribed):")
    print(f"  [greeting] {await transcribe(key, result.greeting)}")
    for i, step in enumerate(result.steps, 1):
        print(f"  [step {i}] {await transcribe(key, step.audio) if step.audio else '-'}")
    if result.clears:
        print(f"\nbarge-in clears sent: {result.clears}")


if __name__ == "__main__":
    asyncio.run(main())
