import { Link, useSearchParams } from "react-router-dom";

import { api, type CallRow, type Overview } from "../api";
import s from "../admin.module.css";
import { hangupNote, languageLine } from "../callText";
import { BarList, fmtS, Stat, TrendColumns, TrendLines, type BarRow } from "../charts";
import { ago, inr, istDate, length, num, pct, refused, when } from "../format";
import { Fact, PageHead, Problem, Seg, Skeleton, Status, useLoad } from "../ui";
import { channelLabel, smsLabel } from "./Calls";
import { TestCallCard } from "./TestCall";

export const RANGES = [{ value: "today", label: "Today" }, { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" }] as const;
export const KINDS = [{ value: "real", label: "Real calls" }, { value: "test", label: "Test calls" },
  { value: "all", label: "All" }] as const;

const PLAIN_TARGET = 1.2;   // the call report's checks: a plain reply's p50 ≤ 1.2 s...
const TAIL_TARGET = 1.8;    // ...and every reply's p90 ≤ 1.8 s
const CAUGHT = new Set(["claim_guard", "repeats_prevented", "echo_drops", "tool_deferrals"]);   // a guard stopped it
const COST: Record<string, string> = { telephony: "Phone line", stt: "Speech to text", tts: "Text to speech",
  llm: "Language model", sms: "SMS" };
const SMS: Record<string, string> = { delivered: "Delivered", failed: "Failed", submitted: "Sent, no receipt yet",
  queued: "Queued", undelivered: "Not delivered" };

export function pick<T extends string>(v: string | null, options: readonly { value: T }[], fallback: T): T {
  return options.some((o) => o.value === v) ? (v as T) : fallback;
}

export function OverviewPage() {
  const [params, setParams] = useSearchParams();
  const range = pick(params.get("range"), RANGES, "7d");
  const kind = pick(params.get("kind"), KINDS, "real");
  const load = useLoad(() => api.overview(range, kind), `${range}|${kind}`);
  const set = (key: string, value: string) => setParams((p) => {
    const next = new URLSearchParams(p);
    next.set(key, value);
    return next;
  }, { replace: true });

  return (
    <>
      <PageHead title="Overview" lede={<>How fast BolDoc answers, what goes wrong and what it costs. Percentiles pool
        every reply of every call, so a long call counts for all its turns.</>}>
        <Seg label="Time range" value={range} options={RANGES} onChange={(v) => set("range", v)} />
        <Seg label="Which calls" value={kind} options={KINDS} onChange={(v) => set("kind", v)} />
      </PageHead>
      {load.error && <Problem error={load.error} retry={load.reload} />}
      {!load.data && !load.error && <><Skeleton rows={4} /><Skeleton rows={2} /></>}
      {load.data && <Body o={load.data} stale={load.loading} />}
    </>
  );
}

const sec = (v: number) => fmtS(v, 2);
const spread = (p95: number | null) => `p95 ${fmtS(p95, 2)}`;

/** The newest answered call at a glance, one click from its full analysis. */
function LastCall({ c }: { c: CallRow }) {
  const failed = c.checks_failed ?? [];
  const made = [c.docs?.create ? `${c.docs.create} new` : "", c.docs?.edit ? `${c.docs.edit} ${c.docs.edit === 1 ? "edit" : "edits"}` : ""]
    .filter(Boolean).join(", ");
  return (
    <section className={`${s.card} ${s.lastCard}`} aria-labelledby="ov-last">
      <div className={s.lastHead}>
        <div className={s.cardHead}>
          <h2 id="ov-last" className={`${s.cardTitle} ${s.titleWithDot}`}><span className={s.liveDot} aria-hidden="true" />Last call</h2>
          <p className={`${s.muted} ${s.small}`}>{when(c.at)}, {ago(c.at)}{c.test ? ", a test call" : ""}</p>
        </div>
        <Link to={`/calls/${encodeURIComponent(c.id)}`} className={s.btnInk}>Open the call</Link>
      </div>
      <dl className={s.lastFacts}>
        <Fact label="Caller" value={c.caller ?? "Withheld"} sub={channelLabel(c)} />
        <Fact label="Length" value={length(c.duration_s)} sub={c.turns !== undefined ? `${c.turns} caller turns` : undefined} />
        <Fact label="Replies" value={`${fmtS(c.v2v_p50_s)} p50`} sub={`p90 ${fmtS(c.v2v_p90_s)} · slowest ${fmtS(c.v2v_max_s)}`} />
        <Fact label="Language" value={languageLine(c.language)} />
        <Fact label="Ended" value={c.ended?.label ?? "–"} sub={hangupNote(c.ended)} />
        <Fact label="Documents" value={made || "None"} sub={c.sms?.length ? `SMS ${smsLabel(c.sms).toLowerCase()}` : undefined} />
      </dl>
      {failed.length ? (
        <p className={s.failStrip} role="note">
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M12 3l9.5 16.5h-19zM12 10v4M12 17.5v.01" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
          <span><b>{failed.length === 1 ? "One check failed" : `${failed.length} checks failed`}:</b> {failed.join("; ")}.</span>
        </p>
      ) : <p className={s.lastChecks}><Status ok>All checks passed</Status></p>}
    </section>
  );
}

function Body({ o, stale }: { o: Overview; stale: boolean }) {
  const L = o.latency;
  const per = o.range === "today" ? "hour" : "day";
  const start = istDate(o.range === "today" ? 0 : o.range === "7d" ? -6 : -29);
  const calls = (extra: Record<string, string>) => `/calls?${new URLSearchParams({ start, kind: o.kind, ...extra })}`;
  const plain = L.v2v_plain.p50;
  const stages: BarRow[] = [
    { label: "End of turn", value: L.eot.p50, note: spread(L.eot.p95) },
    { label: "LLM first token", value: L.ttft.p50, note: spread(L.ttft.p95) },
    { label: "TTS first audio", value: L.tts.p50, note: spread(L.tts.p95) },
    { label: "Held before playing", value: L.hold.p50, note: spread(L.hold.p95) },
  ];
  const whole: BarRow[] = [
    { label: "Plain reply", value: L.v2v_plain.p50, note: spread(L.v2v_plain.p95) },
    { label: "Reply with a tool", value: L.v2v_tool.p50, note: spread(L.v2v_tool.p95) },
    { label: "Greeting after pickup", value: L.ttfw.p50, note: spread(L.ttfw.p95) },
  ];
  const secMax = Math.max(1e-9, ...[...stages, ...whole].map((r) => r.value ?? 0));
  const byCount = (a: { count: number }, b: { count: number }) => b.count - a.count;
  const went = o.flags.filter((f) => !CAUGHT.has(f.key)).sort(byCount);
  const caught = o.flags.filter((f) => CAUGHT.has(f.key)).sort(byCount);
  const refusals = Object.entries(o.calls.refused).sort((a, b) => b[1] - a[1]);
  const d = o.documents;
  const done = d.create.completed + d.edit.completed;
  const tried = done + d.create.failed + d.edit.failed;
  const ch = o.calls.by_channel;

  return (
    <div className={`${s.stack} ${stale ? s.stale : ""}`} aria-busy={stale}>
      <p className={s.meta}>
        {when(o.since)} to {when(o.until)}, India time · a call's numbers settle within 5 minutes of its end
      </p>

      {o.last_call && <LastCall c={o.last_call} />}
      <TestCallCard />

      <div className={s.kpiRow}>
        <section className={s.tileSlab} aria-label="Reply time">
          <p className={s.tileLabel}>Reply time at p50, voice to voice</p>
          <p className={s.tileValue}><Unit text={fmtS(L.v2v.p50)} /></p>
          <p className={s.tileSub}>{fmtS(L.v2v.p95)} at p95, {fmtS(L.v2v.p99)} at p99, over {num(L.v2v.n)} replies</p>
          <Spark values={o.trend.map((t) => t.v2v_p50)} line={TAIL_TARGET} />
        </section>
        <section className={s.tile} aria-label="Calls answered">
          <p className={s.tileLabel}>Calls answered</p>
          <p className={s.tileValue}>{num(o.calls.admitted)}</p>
          <p className={s.tileSub}>{num(ch.phone ?? 0)} by phone, {num(ch.browser ?? 0)} in the browser</p>
        </section>
        <section className={s.tile} aria-label="Documents">
          <p className={s.tileLabel}>Documents</p>
          <p className={s.tileValue}>{num(d.create.completed)}</p>
          <p className={s.tileSub}>{num(d.edit.completed)} edits, {tried ? pct(done / tried) : "–"} succeeded</p>
        </section>
        <section className={s.tile} aria-label="Cost a call">
          <p className={s.tileLabel}>Cost a call</p>
          <p className={s.tileValue}><Rupee text={inr(o.cost.per_call_inr)} /></p>
          <p className={s.tileSub}>{inr(o.cost.per_min_inr)} a minute, {inr(o.cost.total_inr)} in all</p>
        </section>
      </div>

      <section className={s.card} aria-label="Reply time">
        <TrendLines title={`Reply time by ${per}`} buckets={o.trend.map((t) => t.bucket)}
          target={{ value: TAIL_TARGET, label: `${TAIL_TARGET} s line` }}
          series={[
            { key: "p50", label: "p50", tone: "accent", values: o.trend.map((t) => t.v2v_p50) },
            { key: "p95", label: "p95", tone: "context", values: o.trend.map((t) => t.v2v_p95) },
          ]} />
        {L.v2v.n > 0 ? (
          <ul className={s.statusRow}>
            <li>
              <Status ok={plain !== null && plain <= PLAIN_TARGET}>
                Plain replies {fmtS(plain)} at p50, against a {PLAIN_TARGET} s target
              </Status>
            </li>
            <li className={s.muted}>{pct(L.slow_share)} of replies took longer than 1.5 s</li>
          </ul>
        ) : <p className={s.muted}>No replies in this window yet.</p>}
      </section>

      <section className={s.card} aria-label="Totals">
        <div className={s.kpis}>
          <Stat label="Minutes" value={num(o.calls.minutes)}
            sub={o.calls.admitted ? `${num(o.calls.minutes / o.calls.admitted)} min a call` : "no calls"} />
          <Stat label="Callers" value={num(o.calls.unique_callers)} sub="different numbers" />
          <Stat label="Turned away" value={num(o.calls.refused_total)}
            sub={refusals[0] ? `most often: ${refused(refusals[0][0]).toLowerCase()}` : "no one"} />
          <Stat label="SMS delivered" value={num(o.sms.delivered)}
            sub={`of ${num(o.sms.submitted)} sent${o.sms.failed ? ` · ${num(o.sms.failed)} failed` : ""}`} />
          <Stat label="Total cost" value={inr(o.cost.total_inr)} sub={`${inr(o.cost.per_doc_inr)} a document`} />
        </div>
      </section>

      <div className={s.grid2}>
        <section className={s.card} aria-label="Calls">
          <TrendColumns title={`Calls answered by ${per}`} unit="calls"
            data={o.trend.map((t) => ({ bucket: t.bucket, value: t.calls }))} />
          <div className={s.part}>
            <h3 className={s.label}>Turned away</h3>
            {refusals.length ? (
              <BarList rows={refusals.map(([reason, n]) => ({ label: refused(reason), value: n }))} />
            ) : <p className={`${s.muted} ${s.small}`}>No one was turned away.</p>}
          </div>
        </section>

        <section className={s.card} aria-labelledby="ov-time">
          <h2 id="ov-time" className={s.cardTitle}>Where a reply's time goes</h2>
          <p className={`${s.muted} ${s.small}`}>Each at p50, its p95 beside it; both lists share one scale.</p>
          <div className={s.part}>
            <h3 className={s.label}>Stages</h3>
            <BarList rows={stages} format={sec} max={secMax} labelWidth="10.5rem" />
          </div>
          <div className={s.part}>
            <h3 className={s.label}>Whole reply</h3>
            <BarList rows={whole} format={sec} max={secMax} labelWidth="10.5rem" />
          </div>
        </section>
      </div>

      <section className={s.card} aria-labelledby="ov-flags">
        <h2 id="ov-flags" className={s.cardTitle}>Quality flags</h2>
        <div className={s.cols}>
          <div className={s.part}>
            <h3 className={s.label}>Went wrong · open one to see its calls</h3>
            <BarList tone="accent" rows={went.map((f) => ({ label: f.label, value: f.count,
              to: f.count ? calls({ flag: f.label }) : undefined }))} />
          </div>
          <div className={s.part}>
            <h3 className={s.label}>Caught by a guard before the caller heard it</h3>
            <BarList tone="ink" rows={caught.map((f) => ({ label: f.label, value: f.count }))} />
          </div>
        </div>
      </section>

      <div className={s.grid2}>
        <section className={s.card} aria-labelledby="ov-docs">
          <h2 id="ov-docs" className={s.cardTitle}>Documents and SMS</h2>
          <div className={s.pair}>
            <Stat label="New documents" value={num(d.create.completed)}
              sub={<>{num(d.create.failed)} failed · ready in {fmtS(d.create.time_p50_s, 1)}, p95 {fmtS(d.create.time_p95_s, 1)}</>} />
            <Stat label="Edits" value={num(d.edit.completed)}
              sub={<>{num(d.edit.failed)} failed · ready in {fmtS(d.edit.time_p50_s, 1)}, p95 {fmtS(d.edit.time_p95_s, 1)}</>} />
          </div>
          <div className={s.part}>
            <h3 className={s.label}>SMS with the link</h3>
            {o.sms.submitted ? (
              <BarList rows={Object.entries(o.sms.by_status).sort((a, b) => b[1] - a[1])
                .map(([k, n]) => ({ label: SMS[k] ?? k, value: n }))} />
            ) : <p className={`${s.muted} ${s.small}`}>No SMS sent.</p>}
          </div>
          <Link to={calls({ has_doc: "true" })} className={s.more}>Calls that made a document</Link>
        </section>

        <section className={s.card} aria-labelledby="ov-cost">
          <h2 id="ov-cost" className={s.cardTitle}>Cost</h2>
          <Stat label="Total" value={inr(o.cost.total_inr)}
            sub={`${inr(o.cost.per_call_inr)} a call · ${inr(o.cost.per_min_inr)} a minute`} />
          <BarList format={inr} rows={Object.entries(o.cost.by_component).sort((a, b) => b[1] - a[1])
            .map(([k, v]) => ({ label: COST[k] ?? k, value: v }))} />
          <p className={`${s.muted} ${s.small}`}>Phone line from Plivo's call record once it arrives, estimated until
            then. Speech, voice and model: usage at list price.</p>
        </section>
      </div>
    </div>
  );
}

/** "0.94 s" with its unit in orange; anything else as it is. */
function Unit({ text }: { text: string }) {
  const m = text.match(/^(.*\d)\s?s$/);
  return m ? <>{m[1]}<span className={s.tileUnit}> s</span></> : <>{text}</>;
}

/** "₹6.42" with the rupee sign in orange. */
function Rupee({ text }: { text: string }) {
  return text.startsWith("₹") ? <><span className={s.tileUnit}>₹</span>{text.slice(1)}</> : <>{text}</>;
}

/** The reply time's trend at a glance (the chart below has the axes): its p50 by day, and the target as a dash. */
function Spark({ values, line }: { values: (number | null)[]; line: number }) {
  const pts = values.map((v, i) => [i, v] as const).filter((p): p is readonly [number, number] => p[1] !== null);
  if (pts.length < 2) return null;
  const top = Math.max(line, ...pts.map((p) => p[1])) * 1.1;
  const x = (i: number) => (i / Math.max(1, values.length - 1)) * 220;
  const y = (v: number) => 42 - (v / top) * 40;
  return (
    <svg className={s.spark} viewBox="0 0 220 44" preserveAspectRatio="none" aria-hidden="true">
      <line x1="0" x2="220" y1={y(line)} y2={y(line)} />
      <polyline points={pts.map(([i, v]) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")} />
    </svg>
  );
}
