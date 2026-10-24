import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api, ApiError, RUNNING, type TestCalls, type TestLine, type TestRun, type TestRunRow } from "../api";
import s from "../admin.module.css";
import { fmtS } from "../charts";
import { ago, inr, length, when } from "../format";
import { Fact, message, PageHead, Problem, Seg, Skeleton, Status, useLoad } from "../ui";

const POLL_MS = 1500;

/** Run the e2e gate's scripted call from here, follow it as it happens, and read its checks. */
export function TestCallPage() {
  const [params, setParams] = useSearchParams();
  const runId = params.get("run");
  const list = useLoad(() => api.testCalls(), "test-calls");
  const [run, setRun] = useState<TestRun | null>(null);
  const [startError, setStartError] = useState<{ text: string; running?: string } | null>(null);
  const [scenario, setScenario] = useState("doc");
  const [lang, setLang] = useState("en-IN");
  const [starting, setStarting] = useState(false);

  // follow the run in the address, else the one running now
  const follow = runId ?? list.data?.running ?? null;
  useEffect(() => {
    if (!follow) { setRun(null); return; }
    let live = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const { run: r } = await api.testCall(follow);
        if (!live) return;
        setRun(r);
        if (RUNNING.includes(r.status)) timer = window.setTimeout(poll, POLL_MS);
        else list.reload();
      } catch (e) {
        if (live && e instanceof ApiError && e.status === 404) setRun(null);
        else if (live) timer = window.setTimeout(poll, POLL_MS * 2);
      }
    };
    void poll();
    return () => { live = false; window.clearTimeout(timer); };
  }, [follow]);  // list.reload is stable

  const start = async () => {
    setStarting(true);
    setStartError(null);
    try {
      const { run: r } = await api.startTestCall(scenario, lang);
      setRun(r);
      setParams({ run: r.id }, { replace: false });
      list.reload();
    } catch (e) {
      const running = e instanceof ApiError && e.code === "test_call_running" ? list.data?.running ?? undefined : undefined;
      setStartError({ text: message(e), running });
    } finally {
      setStarting(false);
    }
  };

  const busy = run !== null && RUNNING.includes(run.status);
  return (
    <>
      <PageHead title="Test call" lede={<>A scripted caller calls BolDoc the way real callers reach it, then the call's
        report checks every part of it. Use it after a deploy, or whenever something seems off.</>} />
      {list.error && <Problem error={list.error} retry={list.reload} />}
      {!list.data && !list.error && <Skeleton rows={4} />}
      {list.data && (
        <RunForm data={list.data} scenario={scenario} setScenario={setScenario} lang={lang} setLang={setLang}
          busy={busy || starting || list.data.running !== null} starting={starting} onStart={start} error={startError} />
      )}
      {run && <RunCard key={run.id} run={run} />}
      {list.data && <Recent rows={withRun(list.data.runs, run)} current={run?.id ?? null} />}
    </>
  );
}

/** The list as loaded, with the run being followed as it is now (the list itself reloads when a run ends). */
function withRun(rows: TestRunRow[], run: TestRun | null): TestRunRow[] {
  if (!run) return rows;
  const now: TestRunRow = { id: run.id, call_id: run.call_id, scenario: run.scenario, label: run.label, lang: run.lang,
    by: run.by, status: run.status, started_at: run.started_at, ended_at: run.ended_at, elapsed_s: run.elapsed_s,
    summary: run.summary };
  if (rows.some((r) => r.id === run.id)) return rows.map((r) => (r.id === run.id ? now : r));
  return RUNNING.includes(run.status) ? [now, ...rows] : rows;
}

function RunForm({ data, scenario, setScenario, lang, setLang, busy, starting, onStart, error }: {
  data: TestCalls; scenario: string; setScenario: (v: string) => void; lang: string; setLang: (v: string) => void;
  busy: boolean; starting: boolean; onStart: () => void; error: { text: string; running?: string } | null }) {
  const noLine = data.free_lines < 1;
  return (
    <section className={s.card} aria-labelledby="tc-new">
      <div className={s.cardHead}>
        <h2 id="tc-new" className={s.cardTitle}>Run a test call</h2>
        <p className={`${s.muted} ${s.small}`}>One at a time. It takes a line for about two and a half minutes, and the
          last free line is always kept for a real caller.</p>
      </div>
      <fieldset className={s.choices}>
        <legend className={s.label}>What to test</legend>
        {data.choices.map((c) => (
          <label key={c.id} className={s.choice} data-on={c.id === scenario || undefined}>
            <input type="radio" name="tc-scenario" value={c.id} checked={c.id === scenario}
              onChange={() => setScenario(c.id)} />
            <span className={s.choiceTitle}>{c.label}</span>
            <span className={s.choiceText}>{c.checks}</span>
          </label>
        ))}
      </fieldset>
      <div className={s.runRow}>
        <div className={s.field}>
          <span>BolDoc talks in</span>
          <Seg label="BolDoc talks in" value={lang} options={data.langs.map((l) => ({ value: l.id, label: l.label }))}
            onChange={setLang} />
        </div>
        <button type="button" className={s.btnInk} onClick={onStart} disabled={busy || noLine}>
          {starting ? "Starting…" : "Run test call"}
        </button>
        <p className={`${s.muted} ${s.small}`}>
          {noLine ? "Every line but the last is in use: try again when a call ends."
            : data.running ? "A test call is running: follow it below."
              : `${data.free_lines} ${data.free_lines === 1 ? "line" : "lines"} free.`}
        </p>
      </div>
      {error && (
        <p className={s.failStrip} role="alert">
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 3l9.5 16.5h-19zM12 10v4M12 17.5v.01" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
          <span>{error.text}{error.running && <> <Link to={`/test-calls?run=${encodeURIComponent(error.running)}`}>Follow it</Link>.</>}</span>
        </p>
      )}
    </section>
  );
}

const STATUS: Record<TestRun["status"], string> = { starting: "Starting", calling: "On the call", reporting: "Reading the report",
  passed: "Passed", failed: "Failed", error: "Couldn't run" };

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const sec = Math.floor(seconds % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

/** Ticks once a second while the run is on, so the elapsed time moves between polls. */
function useElapsed(run: TestRun): number {
  const [now, setNow] = useState(() => Date.now());
  const on = RUNNING.includes(run.status);
  useEffect(() => {
    if (!on) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [on]);
  if (!on) return run.elapsed_s;
  return Math.max(run.elapsed_s, (now - Date.parse(run.started_at)) / 1000);
}

function RunCard({ run }: { run: TestRun }) {
  const elapsed = useElapsed(run);
  const on = RUNNING.includes(run.status);
  const r = run.result;
  const failed = r?.checks.filter((c) => !c.ok) ?? [];
  const lang = run.lang === "hi-IN" ? "Hinglish" : "English";
  const logRef = useRef<HTMLOListElement>(null);
  const stick = useRef(on);  // keep the newest line in view while the call goes on, until the reader scrolls up
  useEffect(() => {
    const el = logRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [run]);
  const onLogScroll = () => {
    const el = logRef.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 32;
  };
  const pct = run.total ? Math.min(100, Math.round((Math.max(run.step - (on ? 1 : 0), 0) / run.total) * 100)) : 0;

  return (
    <section className={`${s.card} ${s.runCard}`} aria-labelledby="tc-run" aria-live="polite" aria-busy={on}>
      <div className={s.lastHead}>
        <div className={s.cardHead}>
          <h2 id="tc-run" className={`${s.cardTitle} ${s.titleWithDot}`}>
            {on && <span className={`${s.liveDot} ${s.pulse}`} aria-hidden="true" />}{run.label}
          </h2>
          <p className={`${s.muted} ${s.small}`}>
            {when(run.started_at)} by {run.by}, in {lang}
            {on ? <> · <span className={s.mono}>{clock(elapsed)}</span> so far</>
              : run.status === "error" ? <> · stopped after <span className={s.mono}>{clock(elapsed)}</span></> : null}
          </p>
        </div>
        <span className={s.runPill} data-status={run.status}>{STATUS[run.status]}</span>
      </div>

      {on && (
        <div className={s.runNow}>
          <div className={s.runBar} role="progressbar" aria-label="Steps done" aria-valuemin={0} aria-valuemax={run.total}
            aria-valuenow={Math.max(run.step - 1, 0)}>
            <span style={{ width: `${run.status === "reporting" ? 100 : pct}%` }} />
          </div>
          <p className={s.small}>
            <span className={s.muted}>{run.status === "reporting" ? "The call is over" : `Step ${run.step || 1} of ${run.total}`}:</span>{" "}
            <b className={s.runNowText}>{run.now}</b>
          </p>
        </div>
      )}

      {run.status === "error" && (
        <p className={s.failStrip} role="note">
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 3l9.5 16.5h-19zM12 10v4M12 17.5v.01" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
          <span><b>The test call didn't finish.</b> {run.error}</span>
        </p>
      )}

      {r && (
        <>
          {failed.length ? (
            <p className={s.failStrip} role="note">
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 3l9.5 16.5h-19zM12 10v4M12 17.5v.01" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
              <span><b>{failed.length === 1 ? "One check failed" : `${failed.length} checks failed`}:</b>{" "}
                {failed.map((c) => `${c.check}, got ${show(c.value)}`).join("; ")}.</span>
            </p>
          ) : <p><Status ok>All {r.checks.length} checks passed</Status></p>}
          <ul className={s.checkChips} aria-label="Checks">
            {r.checks.map((c) => (
              <li key={c.check} className={s.checkChip} data-bad={c.ok ? undefined : ""}>
                <span><span className="visually-hidden">{c.ok ? "Passed: " : "Failed: "}</span>{c.check}</span>
                <span className={s.checkChipValue}>{show(c.value)}</span>
              </li>
            ))}
          </ul>
          <dl className={`${s.lastFacts} ${s.runFacts}`}>
            <Fact label="Replies" value={`${fmtS(r.p50_s)} p50`} sub={`p90 ${fmtS(r.p90_s)} · slowest ${fmtS(r.max_s)}`} />
            <Fact label="Plain replies" value={`${fmtS(r.plain_p50_s)} p50`} sub="against 1.2 s" />
            <Fact label="The call" value={length(r.duration_s)}
              sub={`${r.turns ?? "–"} caller turns${r.ended_by_bolDoc ? ", BolDoc said goodbye" : ""}`} />
            <Fact label="Cost" value={r.cost !== null ? inr(r.cost) : "–"} sub="speech, voice and model" />
          </dl>
          <div className={s.runLinks}>
            <Link to={`/calls/${encodeURIComponent(run.call_id)}`} className={s.btnInk}>Open the call</Link>
            <span className={`${s.muted} ${s.small}`}>Every turn, the timeline and what was said (a test call keeps its
              words).</span>
          </div>
        </>
      )}

      <div className={s.part}>
        <h3 className={s.label}>As it happened</h3>
        {run.lines.length === 0 ? <p className={`${s.muted} ${s.small}`}>Getting the line…</p> : (
          <ol className={`${s.timeline} ${s.runLog}`} ref={logRef} onScroll={onLogScroll}>
            {run.lines.map((l, i) => <LogLine key={i} line={l} />)}
          </ol>
        )}
      </div>
    </section>
  );
}

function LogLine({ line }: { line: TestLine }) {
  const cat = line.who === "caller" ? "caller" : line.who === "bolDoc" ? "bolDoc" : line.who === "problem" ? "problem" : "note";
  const [who, said] = cat === "caller" || cat === "bolDoc" ? split(line.text) : ["", line.text];
  return (
    <li className={`${s.tlRow} ${cat === "problem" ? s.tlBad : ""}`} data-who={cat}>
      <span className={s.tlTime}>{clock(line.t)}</span>
      <span className={s.tlDot} aria-hidden="true" />
      <span className={s.tlWhat}>
        {who && <span className={s.tlLabel}>{who}</span>}
        <span className={cat === "note" || cat === "problem" ? s.tlLabel : s.runSaid} lang={cat === "bolDoc" ? "hi" : undefined}>{said}</span>
      </span>
    </li>
  );
}

/** "BolDoc, step 3: …" -> ["BolDoc, step 3", "…"] */
function split(text: string): [string, string] {
  const i = text.indexOf(": ");
  return i > 0 ? [text.slice(0, i), text.slice(i + 2)] : ["", text];
}

function show(v: unknown): string {
  if (Array.isArray(v)) return v.length ? v.join(", ") : "none";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return v === null || v === undefined ? "–" : String(v);
}

function Recent({ rows, current }: { rows: TestRunRow[]; current: string | null }) {
  return (
    <section className={s.card} aria-labelledby="tc-recent">
      <h2 id="tc-recent" className={s.cardTitle}>Recent test calls</h2>
      {rows.length === 0 ? <p className={s.empty}>No test calls yet. Run one above.</p> : (
        <div className={s.tableWrap}>
          <table className={`${s.table} ${s.stackTable}`}>
            <thead>
              <tr><th>Test</th><th>When</th><th>Result</th><th className={s.num}>Replies p50</th><th>By</th><th /></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} aria-current={r.id === current ? "true" : undefined}>
                  <td data-label="Test" className={s.lead}>
                    <Link to={`/test-calls?run=${encodeURIComponent(r.id)}`} className={s.rowLink}>{r.label}</Link>
                    {r.lang === "hi-IN" && <span className={`${s.muted} ${s.small}`}>{" · Hinglish"}</span>}
                  </td>
                  <td data-label="When" className={s.nowrap}>{ago(r.started_at)}</td>
                  <td data-label="Result" className={s.tdMid}><RunResult r={r} /></td>
                  <td data-label="Replies p50" className={s.num}>{fmtS(r.summary?.p50_s ?? null)}</td>
                  <td data-label="By" className={s.muted}>{r.by}</td>
                  <td data-label="Call" className={s.num}>
                    {r.summary ? <Link to={`/calls/${encodeURIComponent(r.call_id)}`} className={s.rowLink}>Open the call</Link>
                      : <span className={s.muted}>–</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function RunResult({ r }: { r: TestRunRow }) {
  if (RUNNING.includes(r.status)) return <span className={s.runPill} data-status={r.status}>{STATUS[r.status]}</span>;
  if (r.status === "error" || !r.summary) return <Status ok={false}>Didn't finish</Status>;
  const passed = r.summary.checks - r.summary.failed;
  return <Status ok={r.summary.failed === 0}>{passed} of {r.summary.checks} checks</Status>;
}

/** The overview's way in: the main test with one click, and how the last one went. */
export function TestCallCard() {
  const load = useLoad(() => api.testCalls(), "ov-tests");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const data = load.data;
  const last = data?.runs[0];
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const { run } = await api.startTestCall("doc", "en-IN");
      navigate(`/test-calls?run=${encodeURIComponent(run.id)}`);
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  };
  return (
    <section className={`${s.card} ${s.testCard}`} aria-labelledby="ov-test">
      <div className={s.lastHead}>
        <div className={s.cardHead}>
          <h2 id="ov-test" className={s.cardTitle}>Test call</h2>
          <p className={`${s.muted} ${s.small}`}>A scripted caller writes a letter and changes it; the call's report checks
            every part.</p>
        </div>
        <div className={s.runRow}>
          {data?.running ? (
            <Link to={`/test-calls?run=${encodeURIComponent(data.running)}`} className={s.btnInk}>Follow the test call</Link>
          ) : (
            <button type="button" className={s.btnInk} onClick={start} disabled={busy || !data || data.free_lines < 1}>
              {busy ? "Starting…" : "Run a test call"}
            </button>
          )}
          <Link to="/test-calls" className={s.more}>Other tests</Link>
        </div>
      </div>
      {error && <p className={`${s.small} ${s.error}`} role="alert">{error}</p>}
      {last && !RUNNING.includes(last.status) && (
        <p className={s.lastChecks}>
          <RunResult r={last} />
          <span className={`${s.muted} ${s.small}`}>
            {last.label}, {ago(last.started_at)}
            {last.summary?.p50_s ? <>, replies <span className={s.nowrap}>{fmtS(last.summary.p50_s)} at p50</span></> : null}
          </span>
        </p>
      )}
    </section>
  );
}
