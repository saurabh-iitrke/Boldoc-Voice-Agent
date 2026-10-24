"""Call report on a synthetic trace with known timings: latency breakdown, dead air, talk-over, false claims."""
from apps.control.report import build_report, render_text

T0 = 1_000_000.0
EVENTS = [
    # turn 1: normal reply. caller quiet at 3.0; reply audible at 4.0 -> 1.0 s
    {"t": 1.0, "e": "vad_start"}, {"t": 1.1, "e": "turn_start"}, {"t": 3.0, "e": "vad_stop"},
    {"t": 3.3, "e": "turn_end", "words": 6, "speech_s": 2.0, "text": "leave application chahiye"},
    {"t": 3.31, "e": "llm_start"}, {"t": 3.6, "e": "llm_first_token"}, {"t": 3.85, "e": "tts_first_audio"},
    {"t": 3.95, "e": "gate_release", "held_ms": 350}, {"t": 4.0, "e": "bot_start"},
    {"t": 4.5, "e": "llm_end", "chars": 40, "text": "Ji, kis tarikh se?"}, {"t": 6.0, "e": "bot_stop"},
    # turn 2: nothing recognised, BolDoc silent for 5 s
    {"t": 7.0, "e": "vad_start"}, {"t": 7.05, "e": "turn_start"}, {"t": 8.0, "e": "vad_stop"},
    {"t": 8.2, "e": "turn_end", "words": 0, "speech_s": 1.0, "text": ""},
    # turn 3: tool call, then a premature "done" while the caller starts talking again
    {"t": 13.0, "e": "vad_start"}, {"t": 13.1, "e": "turn_start"}, {"t": 14.0, "e": "vad_stop"},
    {"t": 14.2, "e": "turn_end", "words": 5, "speech_s": 1.0, "text": "haan PDF bana do"},
    {"t": 14.21, "e": "llm_start"}, {"t": 14.3, "e": "tool", "tool": "create_doc", "status": "accepted", "ms": 40},
    {"t": 14.35, "e": "llm_start"}, {"t": 14.5, "e": "llm_first_token"},
    {"t": 14.6, "e": "llm_end", "chars": 18, "text": "Document ready है।"}, {"t": 14.7, "e": "tts_first_audio"},
    {"t": 14.8, "e": "vad_start"}, {"t": 14.9, "e": "bot_start"}, {"t": 15.4, "e": "bot_cut", "after_s": 0.5},
    {"t": 15.5, "e": "vad_stop"},
    # thinker finishes at +20, announced, heard at +21
    {"t": 20.2, "e": "event", "type": "task.completed"}, {"t": 20.5, "e": "announce", "type": "task.completed"},
    {"t": 20.51, "e": "llm_start", "announce": "task.completed"}, {"t": 21.0, "e": "bot_start"},
    {"t": 23.0, "e": "bot_stop"},
]
TRACE = {"call_id": "c1", "started_wall": T0, "content": True, "events": EVENTS}
TASKS = [{"id": "task_000001", "kind": "create", "tier": "T2", "state": "completed", "attempts": 1,
          "committed_version": 1, "created_at": T0 + 14.3}]
CTL_EVENTS = [{"type": "task.accepted", "task_id": "task_000001", "ts": T0 + 14.3, "payload": {"via": "queued"}},
              {"type": "task.progress", "task_id": "task_000001", "ts": T0 + 14.4, "payload": {}},
              {"type": "task.completed", "task_id": "task_000001", "ts": T0 + 20.0, "payload": {}}]
DELIVERIES = [{"purpose": "initial", "channel": "sms", "recipient": "+919988776655", "version": 1,
               "status": "delivered", "created_at": T0 + 20.0, "updated_at": T0 + 26.0}]


def test_report_breaks_down_latency_and_flags_problems():
    r = build_report(TRACE, TASKS, CTL_EVENTS, DELIVERIES, session_id="sess")
    t1, t2, t3 = r["turns"]
    assert (t1["v2v_s"], t1["eot_s"], t1["ttft_s"], t1["tts_s"], t1["hold_s"]) == (1.0, 0.3, 0.29, 0.25, 0.35)
    assert t1["reply"] == "spoken" and t1["flags"] == []
    assert t2["reply"] == "none" and t2["silence_s"] == 5.1 and set(t2["flags"]) == {"dead_air", "empty_transcript"}
    assert "talked_over_caller" in t3["flags"] and "bot_cut_off" in t3["flags"] and t3["ttft_s"] == 0.15
    s = r["summary"]
    assert s["talk_over"] == 1 and s["dead_air_turns"] == 1 and s["false_claims"] == 1
    assert s["heard_done_after_s"] == [1.0] and s["v2v_p50_s"] == 0.9
    failed = {c["check"] for c in r["checks"] if not c["ok"]}
    assert failed == {"no dead air > 3s", "never talks over the caller", "no premature 'done' claims"}
    assert r["thinker"][0]["work_s"] == 5.6 and r["thinker"][0]["queue_s"] == 0.1
    assert r["delivery"][0]["status"] == "delivered"
    text = render_text(r)
    assert "FAIL  never talks over the caller" in text and "caller: haan PDF bana do" in text


def test_a_hmm_in_a_pause_is_not_dead_air():
    """The caller stopped mid-sentence; BolDoc said हम्म 1.3 s in, and they went on 2.5 s later: a merged turn."""
    events = [{"t": 1.0, "e": "vad_start"}, {"t": 1.1, "e": "turn_start"}, {"t": 3.0, "e": "vad_stop"},
              {"t": 4.2, "e": "turn_end", "words": 6, "speech_s": 2.0, "text": "मुझे अपने HR manager के लिए"},
              {"t": 4.3, "e": "ack", "silence_ms": 1300, "source": "rules"},
              {"t": 6.7, "e": "vad_start"}, {"t": 6.8, "e": "turn_start"}]
    r = build_report({**TRACE, "events": events}, [], [], [], session_id="sess")
    t = r["turns"][0]
    assert t["reply"] == "merged" and "dead_air" not in t["flags"]
    gone = build_report({**TRACE, "events": [e for e in events if e["e"] != "ack"]}, [], [], [], session_id="s")
    assert gone["turns"][0]["reply"] == "none" and gone["turns"][0]["silence_s"] == 3.8  # without it: dead air


def test_report_without_content_has_no_words():
    trace = dict(TRACE, content=False, events=[{k: v for k, v in e.items() if k != "text"} for e in EVENTS])
    r = build_report(trace, TASKS, CTL_EVENTS, DELIVERIES)
    assert "text" not in str(r["turns"]) and r["false_claims"] == []


def test_a_generation_cancelled_by_the_caller_is_not_an_empty_reply():
    from apps.control.report import build_report
    evs = [
        {"t": 1.0, "e": "turn_start"}, {"t": 2.0, "e": "vad_stop"}, {"t": 2.3, "e": "turn_end", "words": 6},
        {"t": 2.31, "e": "llm_start"},
        {"t": 2.32, "e": "turn_start", "bot_speaking": False},       # the caller carries on talking...
        {"t": 2.33, "e": "llm_end", "chars": 0},                      # ...so the reply is cancelled, unsaid
        {"t": 2.34, "e": "gate_drop", "why": "interrupted"},
        {"t": 4.0, "e": "turn_end", "words": 3}, {"t": 4.01, "e": "llm_start"},
        {"t": 4.3, "e": "llm_end", "chars": 0},                       # a real empty reply: nothing said, no tool
    ]
    r = build_report({"call_id": "c", "started_wall": 0, "events": evs}, [], [], [])
    assert r["summary"]["empty_llm_replies"] == 1


def test_a_tool_call_reported_after_the_reply_ended_is_acting():
    """Pipecat runs a reply's function calls after the reply ends: the call that follows still counts."""
    evs = [{"t": 1.0, "e": "turn_end", "words": 3}, {"t": 1.01, "e": "llm_start"},
           {"t": 1.2, "e": "llm_end", "chars": 0}, {"t": 1.21, "e": "tool_call", "tool": "edit_document"},
           {"t": 1.22, "e": "tool", "tool": "edit_doc", "status": "which_document", "ms": 0}]
    r = build_report({"call_id": "c", "started_wall": 0, "events": evs}, [], [], [])
    assert r["summary"]["empty_llm_replies"] == 0


def test_a_scenario_fails_when_its_document_never_got_written():
    from apps.control.report import expect_checks
    nothing = {"thinker": []}
    assert [c["ok"] for c in expect_checks(nothing, ["create", "edit"])] == [False, False]
    done = {"thinker": [{"kind": "create", "state": "completed"}, {"kind": "edit", "state": "completed"}]}
    assert [(c["check"], c["ok"]) for c in expect_checks(done, ["create", "edit"])] == [
        ("a document was written", True), ("the document was changed", True)]


def test_a_request_that_made_no_sense_is_asked_about_before_anything_is_written():
    """The sense scenario passes only when the request was flagged and nothing was written before the caller's next
    turn (their answer to BolDoc's question)."""
    from apps.control.report import expect_checks

    def turn(t):
        return [{"t": t, "e": "turn_start"}, {"t": t + 2, "e": "turn_end", "words": 6, "speech_s": 2.0}]

    def write(t):
        return {"t": t, "e": "tool", "tool": "create_doc", "status": "accepted", "ms": 40}

    def check(evs):
        r = build_report({"call_id": "c", "started_wall": 0, "events": evs}, [], [], [])
        return expect_checks(r, ["sense"])[0]

    flag = {"t": 3.5, "e": "sense", "verdict": "check"}
    held = {"t": 4.0, "e": "tool", "tool": "create_doc", "status": "check_first", "ms": 0}
    asked = [*turn(1.0), flag, held, *turn(9.0), write(30.0)]
    assert check(asked)["ok"] and check(asked)["check"] == "asked what was meant before writing"
    assert not check([*turn(1.0), flag, write(4.0), *turn(9.0)])["ok"]    # wrote before the caller answered
    assert not check([*turn(1.0), *turn(9.0), write(30.0)])["ok"]          # never flagged


def test_the_lang_scenario_passes_only_when_boldoc_switched_to_hindi_and_hung_up_after_its_goodbye():
    from apps.control.report import expect_checks

    def check(evs):
        r = build_report({"call_id": "c", "started_wall": 0, "events": evs}, [], [], [])
        return [(c["check"], c["ok"]) for c in expect_checks(r, ["hindi", "ends"])]

    switched = {"t": 3.0, "e": "lang_switch", "family": "hinglish"}
    played = {"t": 9.0, "e": "played", "ms": 30, "name": "r2"}
    goodbye = {"t": 9.1, "e": "goodbye_played", "how": "played"}
    assert check([switched, played, goodbye]) == [("switched to Hindi when asked", True),
                                                  ("the call ended after BolDoc's goodbye", True)]
    assert check([played]) == [("switched to Hindi when asked", False),
                               ("the call ended after BolDoc's goodbye", False)]


def _timed_turn(t, v2v, tool=False):
    """One caller turn: speech ends at t, BolDoc's reply is heard v2v seconds later (with or without a tool call)."""
    evs = [{"t": t - 1.0, "e": "vad_start"}, {"t": t - 0.9, "e": "turn_start"}, {"t": t, "e": "vad_stop"},
           {"t": t + 0.3, "e": "turn_end", "words": 4, "speech_s": 1.0}, {"t": t + 0.31, "e": "llm_start"}]
    if tool:
        evs.append({"t": t + 0.4, "e": "tool", "tool": "create_doc", "status": "accepted", "ms": 40})
    evs += [{"t": t + v2v - 0.1, "e": "tts_first_audio"}, {"t": t + v2v, "e": "bot_start"},
            {"t": t + v2v + 0.1, "e": "llm_end", "chars": 20}, {"t": t + v2v + 1.0, "e": "bot_stop"}]
    return evs


def test_latency_slos_measure_plain_replies_and_each_talker_by_its_own_bar():
    def report(role, plain, tool):
        evs = [{"t": 0.0, "e": "llm", "role": role}]
        for i, v in enumerate(plain):
            evs += _timed_turn(5 + 5 * i, v)
        evs += _timed_turn(30, tool, tool=True)
        r = build_report({"call_id": "c", "started_wall": 0, "events": evs}, [], [], [])
        return {c["check"]: c["ok"] for c in r["checks"]}, r["summary"]
    # primary: plain replies ~1.0 s pass; a 1.6 s tool reply only counts against the tail bound
    checks, s = report("primary", [1.0, 1.1], 1.6)
    assert checks["plain reply latency p50 ≤ 1.2s (primary talker)"]
    assert checks["reply latency p90 ≤ 1.8s (primary talker)"]
    assert s["v2v_plain_p50_s"] <= 1.1 and s["talker"] == "primary"
    # the overflow talker is held to its own, documented bar; the same timings would fail the primary's
    checks, _ = report("overflow", [1.4, 1.5], 2.2)
    assert checks["plain reply latency p50 ≤ 1.6s (overflow talker)"]
    assert checks["reply latency p90 ≤ 2.4s (overflow talker)"]
    checks, _ = report("primary", [1.4, 1.5], 2.2)
    assert not checks["plain reply latency p50 ≤ 1.2s (primary talker)"]
    # only a tool reply : nothing plain to measure, not a failure; the tail bound still holds
    checks, s = report("primary", [], 1.0)
    assert checks["plain reply latency p50 ≤ 1.2s (primary talker)"] and s["v2v_plain_p50_s"] is None
    assert checks["reply latency p90 ≤ 1.8s (primary talker)"]
    silent = build_report({"call_id": "c", "started_wall": 0, "events": [{"t": 0.0, "e": "llm", "role": "primary"}]},
                          [], [], [])
    assert not {c["check"]: c["ok"] for c in silent["checks"]}["plain reply latency p50 ≤ 1.2s (primary talker)"]


def test_a_claim_the_guard_replaced_was_never_heard():
    """The claim guard swaps a premature "it's done" for a progress line before text-to-speech: not a false claim."""
    spoken = build_report(TRACE, TASKS, CTL_EVENTS, DELIVERIES)
    assert spoken["false_claims"]                       # turn 3's "Document ready है।" was heard
    events = list(EVENTS)
    at = next(e["t"] for e in events if e.get("text") == "Document ready है।")
    events.append({"t": at - 0.05, "e": "claim_guard", "text": "Document ready है।"})
    guarded = build_report(dict(TRACE, events=events), TASKS, CTL_EVENTS, DELIVERIES)
    assert guarded["false_claims"] == []
    # dropped as a repeat : never heard either
    events[-1] = {"t": at - 0.05, "e": "repeat_drop", "sig": "x", "text": "Document ready है।"}
    repeated = build_report(dict(TRACE, events=events), TASKS, CTL_EVENTS, DELIVERIES)
    assert repeated["false_claims"] == []


def test_a_sentence_spoken_twice_is_counted_without_keeping_words():
    evs = [{"t": 1.0, "e": "said", "sig": "a1"}, {"t": 2.0, "e": "said", "sig": "b2"},
           {"t": 5.0, "e": "repeat_drop", "sig": "a1"}, {"t": 9.0, "e": "said", "sig": "a1"}]
    r = build_report({"call_id": "c", "started_wall": 0, "events": evs}, [], [], [])
    assert (r["summary"]["repeats_spoken"], r["summary"]["repeats_prevented"]) == (1, 1)
    assert {c["check"]: c["ok"] for c in r["checks"]}["BolDoc never repeats itself"] is False


def test_a_sentence_dropped_before_it_played_is_no_repeat_when_said_later():
    evs = [{"t": 1.0, "e": "said", "sig": "a1"}, {"t": 2.0, "e": "gate_drop", "why": "interrupted"},
           {"t": 2.0, "e": "unsaid", "sig": "a1"}, {"t": 9.0, "e": "said", "sig": "a1"}]
    r = build_report({"call_id": "c", "started_wall": 0, "events": evs}, [], [], [])
    assert r["summary"]["repeats_spoken"] == 0


def test_short_acknowledgements_said_again_are_not_repeats():
    evs = [{"t": 1.0, "e": "said", "sig": "ok", "words": 2}, {"t": 5.0, "e": "said", "sig": "ok", "words": 2}]
    r = build_report({"call_id": "c", "started_wall": 0, "events": evs}, [], [], [])
    assert r["summary"]["repeats_spoken"] == 0


def test_plivos_word_on_playback_and_instructions_read_out_are_reported():
    evs = [*EVENTS, {"t": 6.2, "e": "played", "name": "r1", "ms": 180}, {"t": 15.45, "e": "audio_cleared", "ms": 40},
           {"t": 15.5, "e": "played_after_clear", "name": "r2"}, {"t": 23.3, "e": "played", "name": "r3", "ms": 240},
           {"t": 23.31, "e": "goodbye_played", "how": "played"},
           {"t": 21.5, "e": "instruction_echo", "text": "End the call only when they want nothing more."}]
    r = build_report(dict(TRACE, events=sorted(evs, key=lambda e: e["t"])), TASKS, CTL_EVENTS, DELIVERIES)
    assert r["summary"]["playout"] == {"replies": 2, "lag_p50_ms": 180, "lag_max_ms": 240, "clears": 1,
                                       "clear_p50_ms": 40, "clear_max_ms": 40, "after_clear": 1, "goodbye": "played"}
    text = render_text(r)
    assert "playout (Plivo's word): 2 replies heard to the end" in text and "goodbye hung up on: played" in text
    assert "instructions read out (dropped) 1" in text
    assert build_report(TRACE, TASKS, CTL_EVENTS, DELIVERIES)["summary"]["playout"] is None  # a line that says nothing


def test_only_sms_plivo_took_count_toward_the_cost():
    took = dict(DELIVERIES[0], provider_ref="uuid-1")
    refused = dict(DELIVERIES[0], status="failed")              # never reached Plivo: nothing to pay
    on_screen = dict(DELIVERIES[0], channel="web", provider_ref="x")
    assert build_report(TRACE, TASKS, CTL_EVENTS, [took, refused, on_screen])["summary"]["sms_sent"] == 1


def test_a_goodbye_that_waits_for_the_caller_is_not_reply_latency():
    """Gates on 42aed44: after end_call the goodbye waited 1.4-1.9 s for the scripted caller's "bas itna hi"
    (on purpose: it plays when they pause, and the call ends after it), and the reply p90 check failed on it."""
    evs = [{"t": 0.0, "e": "llm", "role": "primary"}, *_timed_turn(5, 1.0), *_timed_turn(10, 0.9)]
    bye = _timed_turn(20, 2.4)
    bye.insert(5, {"t": 20.4, "e": "tool", "tool": "end_call", "status": "ending", "ms": 0})
    r = build_report({"call_id": "c", "started_wall": 0, "events": evs + bye}, [], [], [])
    checks = {c["check"]: c["ok"] for c in r["checks"]}
    assert checks["reply latency p90 ≤ 1.8s (primary talker)"] and r["summary"]["goodbye_held_s"] == [2.4]
    assert r["summary"]["v2v_max_s"] <= 1.0
