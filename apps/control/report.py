"""Call report: was this call any good? Built from the talker's trace + the thinker's tasks + SMS records.

Per caller turn: voice-to-voice latency (caller stops talking -> BolDoc's voice starts) and where the time went:
end-of-turn detection, LLM first token, TTS first audio, playback hold, playout. Flags: dead air, no reply, talking
over the caller, cut-offs, empty transcripts, empty LLM replies, premature "done" claims.
Thinker: queue/work time per task, tier, retries. Delivery: SMS submitted / delivered. Announcements: how long
after the thinker finished the caller heard about it.
SLOs: p50 ≤ 1.2 s on PSTN, p95 ≤ 1.8 s, no silence > 3 s, zero talk-over, zero false claims.

CLI (on the box): docker compose exec control python -m apps.control.report [latest|<session id>] [--json] [--check]
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import statistics
import sys
from typing import Any, Dict, List, Optional

V2V_P50_S, V2V_P95_S, DEAD_AIR_S, SLOW_TURN_S = 1.2, 1.8, 3.0, 1.5
# Reply latency SLOs by talker, seconds: (p50 of plain replies, p90 of all replies). A reply that runs a tool waits for
# one more LLM round trip by design, so the typical exchange (p50) is measured on plain replies and the tail (p90) on
# every reply. The overflow talker (busy times) is a slower model by choice.
SLO = {"primary": (V2V_P50_S, V2V_P95_S), "overflow": (1.6, 2.4)}


def _first(evs: List[dict], name: str, start: int, end: int) -> Optional[int]:
    return next((j for j in range(start, min(end, len(evs))) if evs[j]["e"] == name), None)


def _last(evs: List[dict], name: str, start: int, end: int) -> Optional[int]:
    return next((j for j in range(min(end, len(evs)) - 1, start - 1, -1) if evs[j]["e"] == name), None)


def _pct(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, round(q * (len(v) - 1)))], 3)


SHORT_ID = re.compile(r"^(?:test-|t-)?[0-9a-f][0-9a-f-]{5,}$")  # 6+ characters of a call id


def _is_claim(text: str) -> bool:
    try:
        from apps.talker.logic.claims import is_completion_claim
    except ImportError:  # control image without talker logic: claim checks are skipped, not guessed
        return False
    return is_completion_claim(text)


def analyse_turns(evs: List[dict]) -> List[dict]:
    """Caller turns and BolDoc's reply to each, from the talker's trace (events sorted by t)."""
    turn_ends = [i for i, e in enumerate(evs) if e["e"] == "turn_end"]
    turns = []
    vad_open, last_stop = False, None
    vad_state_at: Dict[int, tuple] = {}
    for i, e in enumerate(evs):
        if e["e"] == "vad_start":
            vad_open = True
        elif e["e"] == "vad_stop":
            vad_open, last_stop = False, e["t"]
        vad_state_at[i] = (vad_open, last_stop)
    for n, i in enumerate(turn_ends):
        e = evs[i]
        nxt = next((j for j in range(i + 1, len(evs)) if evs[j]["e"] == "turn_start"), len(evs))
        speech_end = vad_state_at[i][1] if vad_state_at[i][1] is not None else e["t"]
        bi = _first(evs, "bot_start", i + 1, nxt)
        li = _last(evs, "llm_start", i + 1, bi) if bi is not None else _first(evs, "llm_start", i + 1, nxt)
        bot = evs[bi] if bi is not None else None
        llm = evs[li] if li is not None else None
        gap_end = evs[nxt]["t"] if nxt < len(evs) else (evs[-1]["t"] if evs else speech_end)
        # BolDoc's हम्म in a pause mid-sentence breaks the silence the caller hears
        marks = [speech_end, *(x["t"] for x in evs[i + 1:nxt] if x["e"] == "ack"), gap_end]
        longest_silence = max(b - a for a, b in zip(marks, marks[1:]))
        reply = "spoken" if bot else ("merged" if nxt < len(evs) and longest_silence < DEAD_AIR_S else "none")
        if bot and llm and llm.get("announce"):
            reply = "announce_only"  # the next thing BolDoc said was news, not an answer to this turn
        turn: Dict[str, Any] = {"turn": n + 1, "t": e["t"], "words": e.get("words", 0), "speech_s": e.get("speech_s"),
                                "eot_s": round(e["t"] - speech_end, 3), "reply": reply, "flags": []}
        if e.get("text") is not None:
            turn["text"] = e["text"]
        vi = _first(evs, "turn_verdict", i, nxt)
        if vi is not None:
            turn["verdict"] = evs[vi].get("verdict")
        if bot and reply == "spoken":
            fi = _first(evs, "llm_first_token", li, bi + 1) if li is not None else None
            ai = _first(evs, "tts_first_audio", li, bi + 1) if li is not None else None
            gi = _last(evs, "gate_release", li if li is not None else i, bi + 1)
            first, audio, gate = (evs[x] if x is not None else None for x in (fi, ai, gi))
            turn["v2v_s"] = round(bot["t"] - speech_end, 3)
            if llm is not None:
                turn["llm_wait_s"] = round(llm["t"] - e["t"], 3)
                if first is not None:
                    turn["ttft_s"] = round(first["t"] - llm["t"], 3)
                    if audio is not None:
                        turn["tts_s"] = round(audio["t"] - first["t"], 3)
            if gate is not None:
                turn["hold_s"] = round(gate.get("held_ms", 0) / 1000, 3)
            if vad_state_at[bi][0]:
                turn["flags"].append("talked_over_caller")
            if turn["v2v_s"] > SLOW_TURN_S:
                turn["flags"].append("slow")
        tools = [f"{t['tool']}:{t.get('status')}({t.get('ms')}ms)" for t in evs[i + 1:nxt] if t["e"] == "tool"]
        deferred = [t["tool"] for t in evs[i + 1:nxt] if t["e"] == "tool_deferred"]
        if tools:
            turn["tools"] = tools
        if deferred:
            turn["deferred"] = deferred
        texts = [t.get("text", "") for t in evs[i + 1:nxt] if t["e"] == "llm_end" and t.get("text")]
        if texts:
            unheard = any(t["e"] == "gate_drop" for t in evs[i + 1:nxt]) and reply != "spoken"
            turn["reply_text"] = ("[unheard] " if unheard else "") + " | ".join(texts)
        if turn["words"] == 0:
            turn["flags"].append("empty_transcript")
        if reply == "none":
            turn["silence_s"] = round(longest_silence, 1)
            if turn["silence_s"] > DEAD_AIR_S:
                turn["flags"].append("dead_air")
        if any(t["e"] == "bot_cut" for t in evs[i + 1:nxt]):
            turn["flags"].append("bot_cut_off")
        turns.append(turn)
    return turns


def thinker_rows(tasks: List[dict], events: List[dict]) -> List[dict]:
    rows = []
    for t in sorted(tasks, key=lambda x: x.get("created_at", 0)):
        mine = [e for e in events if e.get("task_id") == t["id"]]
        acc = next((e["ts"] for e in mine if e["type"] == "task.accepted"), t.get("created_at"))
        started = next((e["ts"] for e in mine if e["type"] == "task.progress"), None)
        done = next((e for e in mine if e["type"] in ("task.completed", "task.failed", "task.cancelled")), None)
        row = {"task": t["id"][-6:], "kind": t["kind"], "tier": t.get("tier"), "state": t["state"],
               "attempts": t.get("attempts", 0), "version": t.get("committed_version")}
        if started and acc:
            row["queue_s"] = round(started - acc, 2)
        if done and started:
            row["work_s"] = round(done["ts"] - started, 2)
        if done and acc:
            row["total_s"] = round(done["ts"] - acc, 2)
        if t.get("error"):
            row["error"] = t["error"]
        res = (t.get("big") or {}).get("research") or {}
        if res.get("searches") or res.get("reads"):  # a long document's web research
            from .rates import rate_card  # lazily, as call_cost below: the report also runs as a script
            card = rate_card()
            n = len(res.get("searches") or [])
            row["research"] = {"searches": n, "pages": res.get("reads", 0), "cited": res.get("cited", 0),
                               "refused": res.get("refused", 0),
                               "inr": round(n * card["web_search_usd"] * card["usd_inr"], 2)}
        rows.append(row)
    return rows


def delivery_rows(deliveries: List[dict], events: List[dict]) -> List[dict]:
    rows = []
    for d in sorted(deliveries, key=lambda x: x.get("created_at", 0)):
        row = {"purpose": d["purpose"], "channel": d["channel"], "to": "…" + str(d["recipient"])[-4:],
               "version": d["version"], "status": d["status"]}
        if d.get("updated_at") and d.get("created_at"):
            row["last_update_s"] = round(d["updated_at"] - d["created_at"], 1)
        if d.get("error"):
            row["error"] = d["error"]
        rows.append(row)
    return rows


def usage_totals(evs: List[dict]) -> Dict[str, Any]:
    """LLM tokens and TTS characters (Pipecat usage metrics, in the trace as `usage` events) and errors by stage."""
    llm = [e for e in evs if e["e"] == "usage" and e.get("kind") == "llm"]
    tts = [e for e in evs if e["e"] == "usage" and e.get("kind") == "tts"]
    errors: Dict[str, int] = {}
    for e in evs:
        if e["e"] == "error":
            errors[e.get("stage", "other")] = errors.get(e.get("stage", "other"), 0) + 1
    by_model: Dict[str, list] = {}
    for e in llm:
        m = by_model.setdefault(str(e.get("model") or "unknown"), [0, 0])
        m[0] += int(e.get("prompt") or 0)
        m[1] += int(e.get("completion") or 0)
    model = next((e.get("model") for e in evs if e["e"] == "llm"), None)
    return {"llm_calls": len(llm), "llm_by_model": by_model, "talker_model": model,
            "llm_prompt_tokens": sum(int(e.get("prompt") or 0) for e in llm),
            "llm_completion_tokens": sum(int(e.get("completion") or 0) for e in llm),
            "tts_chars": sum(int(e.get("chars") or 0) for e in tts),
            "judge_calls": sum(1 for e in evs if e["e"] == "turn_verdict" and e.get("source") == "classifier"),
            "errors": errors}


def _repeats_spoken(evs: List[dict]) -> int:
    """Sentences BolDoc said twice in the call, by fingerprint (the talker traces one per sentence it speaks; no words
    are kept). Short ones ("ठीक है।", "Ji, boliye.") may come again, as in the claim guard. The guard drops repeats
    before speech, so anything counted here got past it."""
    seen, repeated = set(), 0
    for e in evs:
        if e["e"] == "said" and e.get("sig") and int(e.get("words") or 4) >= 4:
            repeated += e["sig"] in seen
            seen.add(e["sig"])
        elif e["e"] == "unsaid":  # its reply was dropped before it played: the caller never heard it
            seen.discard(e.get("sig"))
    return repeated


def playout_summary(evs: List[dict]) -> Optional[dict]:
    """Plivo's own word on playback: how long after its checkpoint went out each reply finished playing (the network
    and Plivo's queue, which "BolDoc stopped speaking" doesn't count), and how long Plivo took to empty its queue after
    a clear. None on a line that confirmed nothing."""
    played = [e["ms"] for e in evs if e["e"] == "played"]
    cleared = [e["ms"] for e in evs if e["e"] == "audio_cleared"]
    if not played and not cleared:
        return None
    return {"replies": len(played), "lag_p50_ms": _pct(played, 0.5), "lag_max_ms": max(played, default=None),
            "clears": len(cleared), "clear_p50_ms": _pct(cleared, 0.5), "clear_max_ms": max(cleared, default=None),
            "after_clear": sum(1 for e in evs if e["e"] == "played_after_clear"),
            "goodbye": next((e.get("how") for e in evs if e["e"] == "goodbye_played"), None)}


def build_report(trace: dict, tasks: List[dict], events: List[dict], deliveries: List[dict],
                 session_id: str = "", record: Optional[dict] = None) -> dict:
    evs = sorted(trace.get("events", []), key=lambda e: e["t"])
    t0 = trace.get("started_wall", 0.0)
    turns = analyse_turns(evs)
    # Latency SLOs cover turns judged finished; a held reply after "stopped mid-thought" waits on purpose, and so does
    # the goodbye after end_call, which waits for the caller to finish before it plays and the call ends (reported on
    # its own: goodbye_held_s).
    def goodbye(t: dict) -> bool:
        return any(str(x).startswith("end_call") for x in t.get("tools") or [])
    timed = [t for t in turns if "v2v_s" in t and t.get("verdict") != "incomplete" and not goodbye(t)]
    goodbyes = [t["v2v_s"] for t in turns if "v2v_s" in t and goodbye(t)]
    v2v = [t["v2v_s"] for t in timed]
    plain = [t["v2v_s"] for t in timed if not t.get("tools")]
    talker = next((e.get("role") for e in evs if e["e"] == "llm"), None) or "primary"
    held = [t["v2v_s"] for t in turns if "v2v_s" in t and t.get("verdict") == "incomplete"]
    count = lambda name: sum(1 for e in evs if e["e"] == name)  # noqa: E731

    # Thinker finished -> when did the caller hear it?
    heard = []
    for e in (x for x in events if x["type"] == "task.completed"):
        ann = next((a for a in evs if a["e"] == "announce" and t0 + a["t"] >= e["ts"] - 0.5), None)
        if ann is not None:
            spoke = next((b for b in evs if b["e"] == "bot_start" and b["t"] >= ann["t"]), None)
            if spoke is not None:
                heard.append(round(t0 + spoke["t"] - e["ts"], 2))

    # Premature completion claims (needs the words: test/debug calls only)
    false_claims = []
    windows = []
    for task_ev in (x for x in events if x["type"] == "task.accepted" and x.get("payload", {}).get("via") != "inbox"):
        done = next((x["ts"] for x in events if x.get("task_id") == task_ev.get("task_id")
                     and x["type"] in ("task.completed", "task.failed", "task.cancelled")), float("inf"))
        windows.append((task_ev["ts"], done))
    # What the caller heard: a sentence the claim guard replaced before text-to-speech never reached them. So did a
    # sentence dropped as a repeat or as a read-out instruction.
    guarded = [(x["t"], x.get("text") or "") for x in evs
               if x["e"] in ("claim_guard", "repeat_drop", "instruction_echo")]
    for e in evs:
        if e["e"] != "llm_end" or not e.get("text"):
            continue
        audible = e["text"]
        for t, sentence in guarded:
            if sentence and 0 <= e["t"] - t <= 10:
                audible = audible.replace(sentence, "")
        if _is_claim(audible):
            at = t0 + e["t"]
            if any(a <= at < b for a, b in windows):
                false_claims.append(e["text"][:120])

    # A reply with no words and no tool call. Not counted: the silent follow-up to a (deferred) tool call, and a
    # generation cancelled because the caller started speaking again (correct behaviour, not an empty answer).
    empty_llm = 0
    for idx, e in enumerate(evs):
        if e["e"] != "llm_end" or e.get("chars") or e.get("tools"):
            continue
        near = [x for x in evs if abs(x["t"] - e["t"]) <= 1.5 and x["e"] in ("tool", "tool_deferred", "tool_call")]
        started = _last(evs, "llm_start", 0, idx)
        cancelled = (started is not None and any(evs[j]["e"] == "turn_start" for j in range(started, idx))) or any(
            x["e"] == "gate_drop" and x.get("why") == "interrupted" and 0 <= x["t"] - e["t"] <= 0.3
            for x in evs[idx + 1:idx + 4])
        if not near and not cancelled:
            empty_llm += 1
    talk_over = sum(1 for t in turns if "talked_over_caller" in t["flags"])
    dead_air = [t["silence_s"] for t in turns if "dead_air" in t["flags"]]
    summary = {
        "duration_s": round(evs[-1]["t"], 1) if evs else 0, "caller_turns": len(turns),
        "replied": len(v2v), "no_reply": sum(1 for t in turns if t["reply"] == "none"),
        "merged_fragments": sum(1 for t in turns if t["reply"] == "merged"),
        "v2v_p50_s": _pct(v2v, 0.5), "v2v_p90_s": _pct(v2v, 0.9), "v2v_max_s": max(v2v) if v2v else None,
        "v2v_plain_p50_s": _pct(plain, 0.5), "talker": talker,
        "v2v_mean_s": round(statistics.mean(v2v), 3) if v2v else None,
        "held_mid_thought": len(held), "held_reply_s": held, "goodbye_held_s": goodbyes,
        "eot_p50_s": _pct([t["eot_s"] for t in turns], 0.5),
        "ttft_p50_s": _pct([t["ttft_s"] for t in turns if "ttft_s" in t], 0.5),
        "tts_p50_s": _pct([t["tts_s"] for t in turns if "tts_s" in t], 0.5),
        "hold_p50_s": _pct([t["hold_s"] for t in turns if "hold_s" in t], 0.5),
        "talk_over": talk_over, "bot_cut_off": count("bot_cut"), "acks": count("ack"), "dead_air_turns": len(dead_air),
        "dead_air_max_s": max(dead_air) if dead_air else 0, "empty_transcripts": sum(
            1 for t in turns if t["words"] == 0), "empty_llm_replies": empty_llm, "echo_drops": count("echo_drop"),
        "claim_guard": count("claim_guard"), "false_claims": len(false_claims), "tool_deferrals": count(
            "tool_deferred"), "dropped_replies": count("gate_drop"), "heard_done_after_s": heard,
        "repeats_spoken": _repeats_spoken(evs), "repeats_prevented": count("repeat_drop"),
        "instruction_echoes": count("instruction_echo"), "playout": playout_summary(evs),
        "sense": sense_summary(evs), **usage_totals(evs),
        "lang_switches": [e.get("family") for e in evs if e["e"] == "lang_switch"],
        "goodbye_check": next((e.get("ends") for e in evs if e["e"] == "goodbye_check"), None),
        "sms_sent": sum(1 for d in deliveries if d.get("channel") == "sms" and d.get("provider_ref")),  # Plivo took it
    }
    # Time to first word, from the moment Plivo asked us to answer (admission) to BolDoc's first audio.
    first_bot = next((e for e in evs if e["e"] == "bot_start"), None)
    if record is not None and record.get("admitted_at") is not None and first_bot is not None and t0:
        admitted = record["admitted_at"].timestamp() if hasattr(record["admitted_at"], "timestamp") \
            else float(record["admitted_at"])
        summary["ttfw_s"] = round(t0 + first_bot["t"] - admitted, 3)
    p50_max, p90_max = SLO.get(talker, SLO["primary"])
    checks = [
        # A call whose replies all made a tool call or said goodbye has no plain reply to measure : not applicable, not
        # a failure. A call with no replies at all still fails.
        {"check": f"plain reply latency p50 ≤ {p50_max}s ({talker} talker)",
         "ok": summary["v2v_plain_p50_s"] <= p50_max if plain else v2v != [],
         "value": summary["v2v_plain_p50_s"] if plain else "n/a: no plain reply"},
        {"check": f"reply latency p90 ≤ {p90_max}s ({talker} talker)",
         "ok": v2v != [] and summary["v2v_p90_s"] <= p90_max, "value": summary["v2v_p90_s"]},
        {"check": f"no dead air > {DEAD_AIR_S:.0f}s", "ok": not dead_air, "value": summary["dead_air_max_s"]},
        {"check": "never talks over the caller", "ok": talk_over == 0, "value": talk_over},
        {"check": "no premature 'done' claims", "ok": not false_claims, "value": len(false_claims)},
        {"check": "every LLM reply speaks or acts", "ok": empty_llm == 0, "value": empty_llm},
        {"check": "BolDoc never repeats itself", "ok": summary["repeats_spoken"] == 0,
         "value": summary["repeats_spoken"]},
        {"check": "thinker tasks succeed", "ok": not any(t["state"] == "failed" for t in tasks),
         "value": sum(1 for t in tasks if t["state"] == "failed")},
        {"check": "SMS not failed", "ok": not any(d["status"] == "failed" for d in deliveries),
         "value": [d["status"] for d in deliveries]},
    ]
    return {"session_id": session_id, "call_id": trace.get("call_id"), "content": trace.get("content", False),
            "summary": summary, "checks": checks, "turns": turns, "thinker": thinker_rows(tasks, events),
            "delivery": delivery_rows(deliveries, events), "false_claims": false_claims}


def render_text(r: dict) -> str:
    s = r["summary"]
    out = [f"CALL {r['session_id']}  {s['duration_s']}s  turns={s['caller_turns']} replied={s['replied']} "
           f"merged={s['merged_fragments']} no_reply={s['no_reply']} held_mid_thought={s['held_mid_thought']} "
           f"{s['held_reply_s']}",
           f"reply latency: p50 {s['v2v_p50_s']}s (plain {s['v2v_plain_p50_s']}s)  p90 {s['v2v_p90_s']}s  "
           f"max {s['v2v_max_s']}s   "
           f"(end-of-turn p50 {s['eot_p50_s']}s, LLM first token {s['ttft_p50_s']}s, TTS {s['tts_p50_s']}s, "
           f"hold {s['hold_p50_s']}s)",
           "checks:"]
    out += [f"  {'PASS' if c['ok'] else 'FAIL'}  {c['check']}  ({c['value']})" for c in r["checks"]]
    out.append(f"other: cut-offs {s['bot_cut_off']}, empty transcripts {s['empty_transcripts']}, echo drops "
               f"{s['echo_drops']}, claim guard {s['claim_guard']}, deferred tools {s['tool_deferrals']}, dropped "
               f"replies {s['dropped_replies']}, repeats prevented {s.get('repeats_prevented', 0)}, instructions "
               f"read out (dropped) {s.get('instruction_echoes', 0)}, हम्म in pauses {s.get('acks', 0)}, heard 'done' "
               f"after {s['heard_done_after_s']}s")
    out.append(f"usage: LLM {s.get('llm_calls', 0)} calls, {s.get('llm_prompt_tokens', 0)} in / "
               f"{s.get('llm_completion_tokens', 0)} out tokens; TTS {s.get('tts_chars', 0)} chars; turn judge "
               f"{s.get('judge_calls', 0)} calls; errors {s.get('errors') or 'none'}"
               + (f"; first word {s['ttfw_s']}s after answer" if s.get("ttfw_s") is not None else ""))
    po = s.get("playout")
    if po:
        out.append(f"playout (Plivo's word): {po['replies']} replies heard to the end, {po['lag_p50_ms']} ms p50 (max "
                   f"{po['lag_max_ms']}) after their last audio went out; {po['clears']} clears confirmed in "
                   f"{po['clear_p50_ms']} ms p50 (max {po['clear_max_ms']})"
                   + (f"; {po['after_clear']} checkpoints answered after a clear" if po["after_clear"] else "")
                   + (f"; goodbye hung up on: {po['goodbye']}" if po["goodbye"] else ""))
    if r.get("cost"):
        c = r["cost"]
        out.append(f"cost: ₹{c['total_inr']} (" + ", ".join(f"{k} ₹{v}" for k, v in c["inr"].items())
                   + f"; telephony from {c['telephony_source']}; prices as of {c['as_of']})")
    out.append("turns:  #  words speech  eot   llm1st  tts   hold  v2v   reply    verdict    flags / tools")
    for t in r["turns"]:
        out.append(f"  {t['turn']:>3} {t['words']:>5} {t.get('speech_s') or 0:>5.1f}s {t['eot_s']:>5.2f} "
                   f"{t.get('ttft_s', '-')!s:>6} {t.get('tts_s', '-')!s:>5} {t.get('hold_s', '-')!s:>5} "
                   f"{t.get('v2v_s', '-')!s:>5} {t['reply']:<8} {t.get('verdict', '-'):<10} {','.join(t['flags'])} "
                   f"{' '.join(t.get('tools', []))}")
        if r["content"] and t.get("text") is not None:
            out.append(f"         caller: {t['text'][:110]}")
        if r["content"] and t.get("reply_text"):
            out.append(f"         BolDoc: {t['reply_text'][:110]}")
    out.append("thinker:")
    out += [f"  {x['kind']:<6} {x.get('tier')} v{x.get('version')} {x['state']:<9} queue {x.get('queue_s', '-')}s "
            f"work {x.get('work_s', '-')}s total {x.get('total_s', '-')}s attempts {x['attempts']}"
            + (f" error {x['error']}" if x.get("error") else "") for x in r["thinker"]] or ["  (none)"]
    out.append("delivery:")
    out += [f"  {d['purpose']:<8} {d['channel']} {d['to']} v{d['version']} {d['status']}"
            + (f" after {d.get('last_update_s')}s" if d.get("last_update_s") is not None else "")
            + (f" ({d['error']})" if d.get("error") else "") for d in r["delivery"]] or ["  (none)"]
    if r["false_claims"]:
        out.append("false claims: " + " | ".join(r["false_claims"]))
    return "\n".join(out)


async def load_inputs(con, sid: str) -> tuple:
    """The thinker's tasks, the session's durable events and its deliveries, as stored."""
    tasks = [json.loads(r["data"]) for r in await con.fetch(
        "select data from entities where kind='task' and session_id=$1", sid)]
    events = [json.loads(r["data"]) for r in await con.fetch(
        "select data from events where session_id=$1 order by seq", sid)]
    deliveries = [json.loads(r["data"]) for r in await con.fetch(
        "select data from entities where kind='delivery' and session_id=$1", sid)]
    return tasks, events, deliveries


async def load_from(con, target: str) -> Optional[dict]:
    """A call's report from its stored trace, as it stands now (the thinker's tasks and SMS included); None until
    the talker has sent the trace. `target`: a session or call id, or "latest"."""
    from .rates import call_cost

    if target == "latest":
        row = await con.fetchrow("select session_id, trace from call_traces order by created_at desc limit 1")
    else:
        row = await con.fetchrow("select session_id, trace from call_traces where session_id=$1 or "
                                 "trace->>'call_id'=$1 order by created_at desc limit 1", target)
        if row is None and SHORT_ID.match(target):  # "08edee88": the first characters of a CallUUID, as shown
            rows = await con.fetch("select session_id, trace from call_traces where session_id like $1 || '%' "
                                   "order by created_at desc limit 2", target)
            row = rows[0] if len(rows) == 1 else None
    if row is None:
        return None
    sid = row["session_id"]
    tasks, events, deliveries = await load_inputs(con, sid)
    has_records = await con.fetchval("select to_regclass('call_records') is not null")
    rec = await con.fetchrow("select * from call_records where call_uuid=$1", sid) if has_records else None
    trace = row["trace"] if isinstance(row["trace"], dict) else json.loads(row["trace"])
    report = build_report(trace, tasks, events, deliveries, session_id=sid, record=dict(rec) if rec else None)
    report["cost"] = call_cost(report["summary"], dict(rec) if rec else None)
    return report


async def load(dsn: str, target: str) -> Optional[dict]:
    import asyncpg

    con = await asyncpg.connect(dsn)
    try:
        return await load_from(con, target)
    finally:
        await con.close()


EXPECTED = {"create": "a document was written", "edit": "the document was changed"}


def sense_summary(evs: List[dict]) -> Dict[str, Any]:
    """The sense check on this call: did the request get flagged as something nobody would send (a letter to a cow),
    and was anything written before the caller's next turn, their answer to BolDoc's question?"""
    flagged = next((e["t"] for e in evs if e["e"] == "sense"), None)
    wrote = next((e["t"] for e in evs if e["e"] == "tool" and e.get("tool") == "create_doc"
                  and e.get("status") in ("accepted", "duplicate")), None)
    answered = next((e["t"] for e in evs if flagged is not None and e["e"] == "turn_start" and e["t"] > flagged),
                    None)
    return {"flagged": flagged is not None,
            "wrote_before_answer": wrote is not None and (answered is None or wrote < answered)}


def expect_checks(report: Dict[str, Any], kinds: List[str]) -> List[Dict[str, Any]]:
    """What a scripted call must have produced (a scenario's purpose): e.g. the doc scenario writes and edits one.
    Without it, a call where nothing was written passes "thinker tasks succeed" (none failed: none ran)."""
    out = []
    for kind in kinds:
        if kind == "hindi":  # asked to talk in Hindi: BolDoc switched to its Hindi/English voice
            switches = (report.get("summary") or {}).get("lang_switches") or []
            out.append({"check": "switched to Hindi when asked", "ok": "hinglish" in switches, "value": switches})
            continue
        if kind == "ends":  # the caller wanted nothing more: BolDoc's goodbye played, then it hung up
            how = ((report.get("summary") or {}).get("playout") or {}).get("goodbye")
            out.append({"check": "the call ended after BolDoc's goodbye", "ok": how is not None, "value": how})
            continue
        if kind == "sense":  # the request made no sense as said: BolDoc asked what was meant, and wrote nothing first
            sense = (report.get("summary") or {}).get("sense") or {}
            out.append({"check": "asked what was meant before writing",
                        "ok": bool(sense.get("flagged")) and not sense.get("wrote_before_answer"), "value": sense})
            continue
        done = sum(1 for r in report["thinker"] if r["kind"] == kind and r["state"] == "completed")
        out.append({"check": EXPECTED.get(kind, f"{kind} completed"), "ok": done > 0, "value": done})
    return out


def main(argv: List[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    report = asyncio.run(load(os.environ["DATABASE_URL"], args[0] if args else "latest"))
    if report is None:
        print("no call trace found")
        return 2
    expect = next((a.split("=", 1)[1] for a in argv if a.startswith("--expect=")), "")
    report["checks"] += expect_checks(report, [k for k in expect.split(",") if k])
    print(json.dumps(report, ensure_ascii=False, indent=1) if "--json" in argv else render_text(report))
    return 1 if "--check" in argv and not all(c["ok"] for c in report["checks"]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
