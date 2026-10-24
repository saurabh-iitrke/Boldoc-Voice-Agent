"""Test calls from the admin console.

A run is the e2e gate's scripted call, made from control itself: it admits a test call with the DID as caller (as
scripts/e2e_gate.sh does), streams the scripted caller to the talker over the compose network, follows the call step by
step (what the caller said, what BolDoc answered, transcribed), then builds the call's report from its stored trace with
the gate's checks (apps/control/report.py, plus what the scenario must produce).

Guards: one run at a time; never the last free line; a hard time limit; every run in the audit log. Runs are kept in
Redis for a week.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from .. import report as reports
from .caller import EXPECT, SCENARIOS, CallResult, ScriptedCall, transcribe

# What each run checks, in plain words (the admin picks by these)
CHOICES: Dict[str, Dict[str, str]] = {
    "doc": {"label": "Write a letter, then change it",
            "checks": "Dictation, the read-back, writing the PDF and Word, one change by voice, the SMS, the goodbye."},
    "interrupt": {"label": "Talk over BolDoc",
                  "checks": "BolDoc stops when the caller cuts in, and answers what was said."},
    "pause": {"label": "Pause mid-sentence",
              "checks": "BolDoc waits while the caller thinks, then writes an Excel sheet."},
    "slow": {"label": "Think mid-sentence",
             "checks": "BolDoc waits through 3-second pauses in unfinished sentences (saying हम्म), never cuts in, "
                       "then writes the letter."},
    "sense": {"label": "Ask for a letter to a cow",
              "checks": "BolDoc asks what's really needed before it writes, then writes that."},
    "lang": {"label": "Ask for Hindi, then want nothing",
             "checks": "BolDoc switches to its Hindi voice on \"Hindi please\", says goodbye when nothing is needed, "
                       "and the call ends."},
}
LANGS = {"en-IN": "English", "hi-IN": "Hinglish"}
RUN_TTL_S = 7 * 86400
KEY = "boldoc:testcall:{}"
RECENT = "boldoc:testcalls"
LOCK = "boldoc:testcall:lock"
MAX_LINES = 120


class RunRefused(Exception):
    def __init__(self, status: int, code: str, message: str, running: Optional[str] = None):
        super().__init__(message)
        self.status, self.code, self.message, self.running = status, code, message, running


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TestCalls:
    """Starts, follows and keeps the admin's test calls. `call_factory` makes the scripted call (tests fake it)."""

    def __init__(self, *, pool, bus, calls, sarvam_key: str, stream_url: str, stream_key: str, caller: str,
                 cap: int, timeout_s: float = 420.0, report_wait_s: float = 30.0,
                 call_factory: Optional[Callable[..., Any]] = None, audit: Optional[Callable[..., Any]] = None):
        self.pool, self.bus, self.calls = pool, bus, calls
        self.sarvam_key, self.stream_url, self.stream_key = sarvam_key, stream_url, stream_key
        self.caller, self.cap = caller.lstrip("+"), cap
        self.timeout_s, self.report_wait_s = timeout_s, report_wait_s
        self.call_factory = call_factory or ScriptedCall
        self.audit = audit
        self._tasks: set = set()

    # ------------------------------------------------------------------ state (Redis)
    @property
    def _r(self):
        return getattr(self.bus, "r", None)

    async def _save(self, run: Dict[str, Any]) -> None:
        await self._r.set(KEY.format(run["id"]), json.dumps(run, ensure_ascii=False), ex=RUN_TTL_S)

    async def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        if self._r is None:
            return None
        raw = await self._r.get(KEY.format(run_id))
        return self._shown(json.loads(raw)) if raw else None

    async def recent(self, n: int = 20) -> List[Dict[str, Any]]:
        if self._r is None:
            return []
        ids = await self._r.lrange(RECENT, 0, n - 1)
        out = []
        for run_id in ids:
            raw = await self._r.get(KEY.format(run_id))
            if raw:
                run = self._shown(json.loads(raw))
                out.append({k: run[k] for k in ("id", "call_id", "scenario", "label", "lang", "by", "status",
                                                "started_at", "ended_at", "elapsed_s", "summary")})
        return out

    async def running(self) -> Optional[str]:
        return await self._r.get(LOCK) if self._r is not None else None

    @staticmethod
    def _shown(run: Dict[str, Any]) -> Dict[str, Any]:
        end = run.get("ended_at")
        started = datetime.fromisoformat(run["started_at"])
        stop = datetime.fromisoformat(end) if end else datetime.now(timezone.utc)
        run["elapsed_s"] = round((stop - started).total_seconds(), 1)
        return run

    async def free_lines(self) -> int:
        """Lines free for a test call: the cap minus calls on now, minus one kept for a real caller."""
        return self.cap - len(await self.bus.active_calls()) - 1

    # ------------------------------------------------------------------ start
    async def start(self, who: str, scenario: str, lang: Optional[str] = None) -> Dict[str, Any]:
        if scenario not in CHOICES or scenario not in SCENARIOS:
            raise RunRefused(422, "bad_request", f"Pick one of: {', '.join(CHOICES)}.")
        if lang is not None and lang not in LANGS:
            raise RunRefused(422, "bad_request", "The language is en-IN or hi-IN.")
        if self._r is None or not self.stream_key or not self.sarvam_key:
            raise RunRefused(503, "test_calls_unavailable", "Test calls need Redis, the stream key and the Sarvam "
                                                            "key on the server.")
        if await self.free_lines() < 1:
            raise RunRefused(503, "no_free_line", "Every line but the last is in use. Real callers come first: try "
                                                  "again when a call ends.")
        call_id = f"test-{int(time.time())}"
        if not await self._r.set(LOCK, call_id, nx=True, ex=int(self.timeout_s) + 120):
            running = await self._r.get(LOCK)
            raise RunRefused(409, "test_call_running", "A test call is already running.", running=running)
        steps = SCENARIOS[scenario]
        run = {"id": call_id, "call_id": call_id, "scenario": scenario, "label": CHOICES[scenario]["label"],
               "lang": lang or "en-IN", "by": who, "status": "starting", "started_at": _now(), "ended_at": None,
               "step": 0, "total": len(steps), "now": "Getting the line", "lines": [], "summary": None,
               "result": None, "error": None}
        await self._save(run)
        await self._r.lpush(RECENT, call_id)
        await self._r.ltrim(RECENT, 0, 49)
        await self._r.expire(RECENT, RUN_TTL_S)
        if self.audit is not None:
            await self.audit(who, "test_call", call_id, {"scenario": scenario, "lang": run["lang"]})
        task = asyncio.get_running_loop().create_task(self._run(run))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return self._shown(dict(run))

    # ------------------------------------------------------------------ the run
    def _line(self, run: Dict[str, Any], who: str, text: str) -> Dict[str, Any]:
        started = datetime.fromisoformat(run["started_at"])
        line = {"t": round((datetime.now(timezone.utc) - started).total_seconds(), 1), "who": who, "text": text}
        run["lines"] = (run["lines"] + [line])[-MAX_LINES:]
        return line

    async def _run(self, run: Dict[str, Any]) -> None:
        try:
            await asyncio.wait_for(self._call(run), self.timeout_s)
        except asyncio.TimeoutError:
            run.update(status="error", error=f"The test call didn't finish within {int(self.timeout_s // 60)} "
                                             "minutes.")
        except Exception as e:  # never leave a run "running": say what broke (never the stream key in the log)
            said = str(e).replace(self.stream_key, "<stream key>") if self.stream_key else str(e)
            logger.warning(f"test call {run['id']} failed: {type(e).__name__}: {said}")
            run.update(status="error", error=f"The test call couldn't run ({type(e).__name__}).")
        finally:
            run["ended_at"] = _now()
            if run["status"] not in ("passed", "failed", "error"):
                run["status"] = "error"
            await self._save(run)
            if await self._r.get(LOCK) == run["id"]:
                await self._r.delete(LOCK)
            if self.audit is not None:
                failed = [c["check"] for c in (run.get("result") or {}).get("checks", []) if not c["ok"]]
                await self.audit(run["by"], "test_call_finished", run["id"],
                                 {"scenario": run["scenario"], "result": run["status"], "failed": failed,
                                  "error": run.get("error")})

    async def _call(self, run: Dict[str, Any]) -> None:
        adm = await self.calls.admit(run["call_id"], self.caller, test=True)
        if adm.decision != "admit":
            run.update(status="error", error=f"BolDoc didn't take the call ({adm.reason or 'refused'}).")
            return
        run.update(status="calling", now="Calling BolDoc")
        self._line(run, "note", "The line is open; calling BolDoc")
        await self._save(run)
        url = (f"{self.stream_url}?k={self.stream_key}&test=1&llm=primary&intake=on"
               + (f"&lang={run['lang']}" if run["lang"] != "en-IN" else ""))
        heard: List[asyncio.Task] = []

        def hear(step: Optional[int], audio: Optional[bytes]) -> None:
            """BolDoc's words in the log where it said them: "…" now, the transcript when it's back (the caller
            has gone on to the next step by then)."""
            if not audio:
                return
            label = "BolDoc" if step is None else f"BolDoc, step {step}"
            line = self._line(run, "bolDoc", f"{label}: …")

            async def go() -> None:
                try:
                    text = await transcribe(self.sarvam_key, audio) or "(nothing heard)"
                except Exception as e:  # speech-to-text down: the call and its checks still count
                    text = f"(couldn't transcribe: {type(e).__name__})"
                line["text"] = f"{label}: {text}"
                await self._save(run)
            heard.append(asyncio.get_running_loop().create_task(go()))

        async def on_event(kind: str, data: Dict[str, Any]) -> None:
            if kind == "connected":
                self._line(run, "note", "Connected to the talker")
            elif kind == "greeting":
                self._line(run, "note", f"BolDoc greeted ({data['seconds']:.1f} s)")
                hear(None, data.get("audio"))
            elif kind == "step":
                run.update(step=data["i"], now=data["text"])
                who = "note" if data["kind"] == "listen" else "caller"
                self._line(run, who, data["text"] if who == "note" else f"Caller: {data['text']}")
            elif kind == "replied":
                lat = data["latency"]
                if lat is not None:
                    self._line(run, "note", f"BolDoc answered in {lat:.2f} s")
                hear(data["i"], data["audio"])
            elif kind == "asked_again":  # what it asked (transcribed already), and the yes said once more
                self._line(run, "bolDoc", f"BolDoc, step {data['i']}: {data['heard'] or '(nothing heard)'}")
                self._line(run, "note", "BolDoc asked something the script has no line for" if data.get("other")
                           else "BolDoc asked again")
                self._line(run, "caller", f"Caller: {data['again']}")
                run["now"] = data["again"]
            elif kind == "ended":
                self._line(run, "note", "BolDoc ended the call" if data["by_bolDoc"] else "The script is done")
            await self._save(run)

        call = self.call_factory(self.sarvam_key, url, run["scenario"], run["call_id"], on_event=on_event)
        result: CallResult = await call.run()
        run.update(status="reporting", now="Reading the call's report")
        await self._save(run)
        if heard:
            await asyncio.gather(*heard, return_exceptions=True)
        report = await self._report(run["call_id"])
        if report is None:
            run.update(status="error", error="The call's report didn't arrive (the talker sends it when the call "
                                             "ends).")
            return
        report["checks"] += reports.expect_checks(report, list(EXPECT.get(run["scenario"], ())))
        s = report["summary"]
        checks = [{"check": c["check"], "ok": bool(c["ok"]), "value": c.get("value")} for c in report["checks"]]
        run["result"] = {"checks": checks, "p50_s": s.get("v2v_p50_s"), "p90_s": s.get("v2v_p90_s"),
                         "plain_p50_s": s.get("v2v_plain_p50_s"), "max_s": s.get("v2v_max_s"),
                         "turns": s.get("caller_turns"), "duration_s": s.get("duration_s"),
                         "cost": (report.get("cost") or {}).get("total_inr"),
                         "ended_by_bolDoc": result.ended_by_bolDoc}
        failed = [c["check"] for c in checks if not c["ok"]]
        run["summary"] = {"checks": len(checks), "failed": len(failed), "p50_s": s.get("v2v_p50_s")}
        run.update(status="failed" if failed else "passed",
                   now=f"{len(checks) - len(failed)} of {len(checks)} checks passed")
        self._line(run, "problem" if failed else "note", run["now"])

    async def _report(self, call_id: str) -> Optional[Dict[str, Any]]:
        """The report, once the talker's trace has arrived (it sends it as the call ends)."""
        deadline = time.monotonic() + self.report_wait_s
        while True:
            async with self.pool.acquire() as con:
                rep = await reports.load_from(con, call_id)
            if rep is not None or time.monotonic() >= deadline:
                return rep
            await asyncio.sleep(1.0)
