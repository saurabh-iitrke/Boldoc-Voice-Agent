import { useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";

import { api, type CallDetail, type CallRow, type Report, type Turn } from "../api";
import s from "../admin.module.css";
import { at, CATS, describe, hangupNote, languageLine, type Cat } from "../callText";
import { BarList, fmtS } from "../charts";
import { ago, day, inr, length, num, refused, when } from "../format";
import { CallId, Fact, message, PageHead, Problem, Seg, Skeleton, Status, useLoad, useTitle } from "../ui";
import { channelLabel } from "./Calls";
import { KINDS, pick } from "./Overview";

const COUNTERS: Record<string, string> = {
  talk_over: "talked over the caller", bot_cut_off: "cut off by the caller", dead_air_turns: "dead air",
  empty_transcripts: "empty transcripts", empty_llm_replies: "empty replies", claim_guard: "claims stopped",
  false_claims: "false 'done' claims", repeats_prevented: "repeats stopped", repeats_spoken: "repeats heard",
  dropped_replies: "replies dropped", echo_drops: "echo dropped", tool_deferrals: "tool calls put off",
};
const COST: Record<string, string> = { telephony: "Phone line", stt: "Speech to text", tts: "Text to speech",
  llm: "Language model", sms: "SMS" };
const FORMAT: Record<string, string> = { pdf: "PDF", docx: "Word", xlsx: "Excel", pptx: "PowerPoint", csv: "CSV",
  md: "Markdown", txt: "Text", html: "HTML" };

/** One call, everything to analyse it: who, when and how it ended; every check; each turn's words (where kept) and
 * timing; the talker's own timeline of events; the documents and SMS it made; what it cost. `/calls/latest` is the
 * newest answered call. */
export function CallPage() {
  const { id = "" } = useParams();
  const latest = id === "latest";
  const [params, setParams] = useSearchParams();
  const kind = pick(params.get("kind"), KINDS, "real");
  const from = (useLocation().state as { from?: string } | null)?.from ?? "";
  const load = useLoad<CallDetail | null>(() => (latest ? api.latest(kind) : api.call(id)),
    latest ? `latest|${kind}` : id);
  const back = <Link to={`/calls${from}`} className={s.back}>← All calls</Link>;
  const kinds = latest && (
    <Seg label="Which calls" value={kind} options={KINDS} onChange={(v) => setParams({ kind: v }, { replace: true })} />
  );

  if (load.error) return <>{back}<Untitled latest={latest} /><Problem error={load.error} retry={load.reload} /></>;
  if (load.data === null && !load.loading) {
    return (
      <>
        <PageHead title="Last call" lede="The newest answered call, in full.">{kinds}</PageHead>
        <section className={s.card}><p className={s.muted}>No answered {kind === "all" ? "" : `${kind} `}calls yet.</p></section>
      </>
    );
  }
  if (!load.data) return <>{back}<Untitled latest={latest} /><Skeleton rows={3} /><Skeleton rows={6} /></>;
  const { call, report } = load.data;
  return (
    <>
      {!latest && back}
      <PageHead title={latest ? "Last call" : call.at ? `Call at ${when(call.at)}` : "Call"}
        lede={<span className={s.ids}>
          {latest && call.at && <span>{when(call.at)} · {ago(call.at)}</span>}
          <span>{channelLabel(call)}</span>
          {call.plan && <span className={s.chip}>{call.plan}</span>}
          {call.test && <span className={s.chip}>test</span>}
          <CallId id={call.id} full />
        </span>}>
        {kinds}
      </PageHead>
      <div className={`${s.stack} ${load.loading ? s.stale : ""}`} aria-busy={load.loading}>
        {report ? <Detail d={load.data} r={report} /> : <NoTrace call={call} />}
      </div>
    </>
  );
}

function Untitled({ latest }: { latest: boolean }) {
  useTitle(latest ? "Last call" : "Call");
  return <h1 className={s.h1}>{latest ? "Last call" : "Call"}</h1>;
}

function NoTrace({ call }: { call: CallRow }) {
  const turnedAway = call.decision && call.decision !== "admit";
  return (
    <section className={s.card}>
      <dl className={s.facts}>
        <Fact label="Caller" value={call.caller ?? "Withheld"} />
        <Fact label="Decision" value={turnedAway ? "Turned away" : "Answered"} sub={turnedAway ? refused(call.reason) : undefined} />
        <Fact label="Length" value={length(call.duration_s)} />
      </dl>
      <p className={s.muted}>
        {turnedAway ? "No trace: the call was turned away before BolDoc picked up."
          : "No trace for this call: it never streamed audio, or its trace didn't reach the control plane."}
      </p>
    </section>
  );
}

function checkValue(check: string, v: unknown): string {
  if (v === null || v === undefined) return "–";
  if (typeof v === "number") return /latency/.test(check) ? fmtS(v) : String(v);
  if (Array.isArray(v)) return v.length ? v.join(", ") : "none";
  return String(v);
}

function Detail({ d, r }: { d: CallDetail; r: Report }) {
  const S = r.summary;
  const counters = Object.entries(COUNTERS).map(([k, label]) => ({ label, n: Number(S[k] ?? 0) })).filter((c) => c.n > 0);
  const failed = r.checks.filter((c) => !c.ok).length;
  const stages = [
    { label: "End of turn", value: S.eot_p50_s }, { label: "LLM first token", value: S.ttft_p50_s },
    { label: "TTS first audio", value: S.tts_p50_s }, { label: "Held before playing", value: S.hold_p50_s },
  ];
  return (
    <>
      <CallFacts call={d.call} r={r} />
      <Numbers call={d.call} r={r} />

      <div className={s.grid2}>
        <section className={s.card} aria-labelledby="call-checks">
          <h2 id="call-checks" className={s.cardTitle}>
            Checks · {failed ? `${failed} failed` : "all passed"}
          </h2>
          <ul className={s.checkChips}>
            {r.checks.map((c) => (
              <li key={c.check} className={s.checkChip} data-bad={c.ok ? undefined : ""}>
                <span>{c.check}{c.ok ? "" : <span className="visually-hidden"> (failed)</span>}</span>
                <span className={s.checkChipValue}>{checkValue(c.check, c.value)}</span>
              </li>
            ))}
          </ul>
          {counters.length > 0 && (
            <div className={s.part}>
              <h3 className={s.label}>Counted in this call</h3>
              <span className={s.chips}>
                {counters.map((c) => <span key={c.label} className={s.chip}>{c.label} · {c.n}</span>)}
              </span>
            </div>
          )}
          {(r.false_claims?.length ?? 0) > 0 && (
            <div className={s.part}>
              <h3 className={s.label}>False "done" claims the caller heard</h3>
              <ul className={s.quotes}>{r.false_claims!.map((q, i) => <li key={i}>{q}</li>)}</ul>
            </div>
          )}
        </section>

        <section className={s.card} aria-labelledby="call-time">
          <h2 id="call-time" className={s.cardTitle}>Where the time went</h2>
          <p className={`${s.muted} ${s.small}`}>Each stage at p50 over this call's replies.</p>
          <BarList rows={stages} format={(v) => fmtS(v)} />
          {r.cost && (
            <div className={s.part}>
              <h3 className={s.label}>Cost · {inr(r.cost.total_inr)}</h3>
              <BarList format={inr} rows={Object.entries(r.cost.inr).sort((a, b) => b[1] - a[1])
                .map(([k, v]) => ({ label: COST[k] ?? k, value: v }))} />
              <p className={`${s.muted} ${s.small}`}>
                Phone line {r.cost.telephony_source === "cdr" ? "from Plivo's call record" : "estimated (no call record yet)"}.
              </p>
            </div>
          )}
        </section>
      </div>

      <Turns r={r} />
      <Timeline d={d} content={r.content} />

      <div className={s.grid2}>
        <section className={s.card} aria-labelledby="call-docs">
          <h2 id="call-docs" className={s.cardTitle}>Documents</h2>
          {d.documents && d.documents.length > 0 && (
            <ul className={s.rows}>
              {d.documents.map((doc) => (
                <li key={doc.id}>
                  <span className={s.docTitle}>{doc.title}</span>
                  <span className={s.rowNote}>
                    {doc.versions} {doc.versions === 1 ? "version" : "versions"} · {doc.formats.map((f) => FORMAT[f] ?? f).join(", ") || "no files"}
                    {" · "}{doc.this_call ? "written on this call" : `changed on this call, first written ${day(doc.created_at)}`}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <div className={d.documents?.length ? s.part : undefined}>
            {d.documents?.length ? <h3 className={s.label}>The writer's work</h3> : null}
            {r.thinker.length ? (
              <ul className={s.rows}>
                {r.thinker.map((t) => (
                  <li key={t.task}>
                    <Status ok={t.state === "completed"}>
                      {t.kind === "create" ? "New document" : t.kind === "edit" ? "Edit" : t.kind}
                      {t.version ? ` · version ${t.version}` : ""} · {t.state}
                    </Status>
                    <span className={s.rowNote}>
                      {t.tier ?? "–"} · {t.attempts} {t.attempts === 1 ? "attempt" : "attempts"} · queued {fmtS(t.queue_s, 2)},
                      worked {fmtS(t.work_s, 1)}, ready in {fmtS(t.total_s, 1)}{t.error ? ` · ${t.error}` : ""}
                    </span>
                    {t.research ? (
                      <span className={s.rowNote}>
                        Web research: {t.research.searches} {t.research.searches === 1 ? "search" : "searches"},{" "}
                        {t.research.pages} {t.research.pages === 1 ? "page" : "pages"} read, {t.research.cited} cited
                        {t.research.refused ? `, ${t.research.refused} refused for the caller's privacy` : ""} ·{" "}
                        ₹{t.research.inr.toFixed(2)}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : <p className={`${s.muted} ${s.small}`}>No document work in this call.</p>}
          </div>
        </section>

        <section className={`${s.card} ${s.slabCard}`} aria-labelledby="call-sms">
          <h2 id="call-sms" className={s.cardTitle}>SMS</h2>
          {r.delivery.length ? (
            <ul className={s.rows}>
              {r.delivery.map((x, i) => (
                <li key={i}>
                  <Status ok={x.status === "delivered"}>
                    {x.purpose === "initial" ? "The link" : x.purpose} to {x.to} · {x.status}
                  </Status>
                  <span className={s.rowNote}>
                    {x.channel.toUpperCase()} · version {x.version}
                    {x.last_update_s !== undefined ? ` · receipt ${fmtS(x.last_update_s, 1)} after sending` : ""}
                    {x.error ? ` · ${x.error}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          ) : <p className={`${s.muted} ${s.small}`}>No SMS for this call{d.call.channel === "browser" ? ": a browser call shows the document on screen" : ""}.</p>}
        </section>
      </div>
    </>
  );
}

/** Who, when, on which line, in what language, how it ended, what it cost. */
function CallFacts({ call, r }: { call: CallRow; r: Report }) {
  const [shown, setShown] = useState<{ number?: string; error?: string; busy?: boolean }>({});
  const reveal = async () => {
    setShown({ busy: true });
    try {
      setShown({ number: (await api.revealCaller(call.id)).number });
    } catch (e) {
      setShown({ error: message(e) });
    }
  };
  const S = r.summary;
  return (
    <section className={s.card} aria-labelledby="call-about">
      <h2 id="call-about" className="visually-hidden">The call</h2>
      <dl className={s.callFacts}>
        <Fact label="When" value={call.at ? when(call.at) : "–"} sub={call.at ? `${day(call.at)}, India time` : undefined} />
        <Fact label="Length" value={length(call.duration_s ?? S.duration_s)} sub={`${S.caller_turns} caller turns`} />
        <Fact label="Line" value={channelLabel(call)}
          sub={[call.called ? `to ${call.called}` : "", call.first_call ? "their first call" : ""].filter(Boolean).join(" · ") || undefined} />
        <Fact label="Caller" value={shown.number ?? call.caller ?? "Withheld"}
          sub={call.caller && !shown.number ? (
            <button type="button" className={s.linkBtn} onClick={() => void reveal()} disabled={shown.busy}>
              {shown.busy ? "Showing…" : "Show the full number"}
            </button>
          ) : shown.number ? "Shown just now (logged)" : undefined} />
        <Fact label="Language" value={languageLine(call.language)} />
        <Fact label="Ended" value={call.ended?.label ?? "–"} sub={hangupNote(call.ended)} />
        <Fact label="Cost" value={inr(r.cost?.total_inr)} sub={r.cost ? `${inr(r.cost.per_min_inr)} a minute` : undefined} />
      </dl>
      {shown.error && <p className={s.error} role="alert">{shown.error}</p>}
    </section>
  );
}

/** The call's summary numbers: replies, latency, what the guards caught, what the models used. */
function Numbers({ call, r }: { call: CallRow; r: Report }) {
  const S = r.summary;
  const n = (k: string) => Number(S[k] ?? 0);
  const errors = Object.entries((S.errors as Record<string, number> | undefined) ?? {});
  const tokens = (k: string) => num(n(k) / 1000) + "K";
  const minutes = (call.duration_s ?? S.duration_s ?? 0) / 60;
  const rows: { label: string; value: string; bad?: boolean }[] = [
    { label: "Replies", value: `${S.replied} of ${S.caller_turns} turns` },
    { label: "Merged into the next turn", value: String(S.merged_fragments ?? 0) },
    { label: "No reply", value: String(S.no_reply ?? 0), bad: Number(S.no_reply ?? 0) > 0 },
    { label: "Reply p50 · plain", value: `${fmtS(S.v2v_p50_s)} · ${fmtS(S.v2v_plain_p50_s)}` },
    { label: "Reply p90 · slowest", value: `${fmtS(S.v2v_p90_s)} · ${fmtS(S.v2v_max_s)}` },
    { label: "Greeting after pickup", value: fmtS(S.ttfw_s) },
    { label: "Longest silence", value: n("dead_air_max_s") ? fmtS(n("dead_air_max_s"), 1) : "none over 3 s",
      bad: n("dead_air_max_s") > 0 },
    { label: "Talked over · cut off", value: `${n("talk_over")} · ${n("bot_cut_off")}`, bad: n("talk_over") > 0 },
    { label: "Empty transcripts · replies", value: `${n("empty_transcripts")} · ${n("empty_llm_replies")}` },
    { label: "Claims stopped · repeats stopped", value: `${n("claim_guard")} · ${n("repeats_prevented")}` },
    { label: "Echo dropped · tools put off", value: `${n("echo_drops")} · ${n("tool_deferrals")}` },
    { label: "Replies dropped unheard", value: String(n("dropped_replies")) },
    { label: "Language model", value: `${n("llm_calls")} calls · ${tokens("llm_prompt_tokens")} in · ${num(n("llm_completion_tokens"))} out` },
    { label: "Voice", value: `${num(n("tts_chars"))} characters` },
    { label: "Speech to text", value: `${num(minutes)} min` },
    { label: "Turn judge", value: `${n("judge_calls")} calls` },
    { label: "Errors", value: errors.length ? errors.map(([k, v]) => `${k} ${v}`).join(", ") : "none", bad: errors.length > 0 },
  ];
  return (
    <section className={s.card} aria-labelledby="call-numbers">
      <h2 id="call-numbers" className={s.cardTitle}>In numbers</h2>
      <dl className={s.numbers}>
        {rows.map((x) => (
          <div key={x.label} className={x.bad ? s.numBad : undefined}>
            <dt>{x.label}</dt>
            <dd>{x.value}</dd>
          </div>
        ))}
      </dl>
      {S.talker_model ? <p className={`${s.muted} ${s.small}`}>Talker: {String(S.talker_model)}.</p> : null}
    </section>
  );
}

const REPLY: Record<string, string> = { merged: "merged with the next turn", none: "no reply",
  announce_only: "news, not an answer" };

function Turns({ r }: { r: Report }) {
  return (
    <section className={s.card} aria-labelledby="call-turns">
      <div className={s.cardHead}>
        <h2 id="call-turns" className={s.cardTitle}>Turns</h2>
        <p className={`${s.muted} ${s.small}`}>
          {r.content ? "The words were kept for this call (a test call or a debug number). Opening them is logged."
            : "Timings only: this call's words were not kept."}
        </p>
      </div>
      <div className={s.tableWrap}>
        <table className={`${s.table} ${s.stackTable}`}>
          <thead>
            <tr>
              <th scope="col">Turn</th><th scope="col">{r.content ? "What was said" : "Caller"}</th>
              <th scope="col" className={s.num}>Speech</th><th scope="col" className={s.num}>End of turn</th>
              <th scope="col" className={s.num}>LLM</th><th scope="col" className={s.num}>TTS</th>
              <th scope="col" className={s.num}>Hold</th><th scope="col" className={s.num}>Reply</th>
            </tr>
          </thead>
          <tbody>{r.turns.map((t) => <TurnRow key={t.turn} t={t} content={r.content} />)}</tbody>
        </table>
      </div>
    </section>
  );
}

function TurnRow({ t, content }: { t: Turn; content: boolean }) {
  const unheard = (t.reply_text ?? "").startsWith("[unheard] ");
  const replies = (t.reply_text ?? "").replace(/^\[unheard\] /, "").split(" | ").filter(Boolean);
  const notes = [
    ...(t.verdict && t.verdict !== "complete"
      ? [`verdict: ${t.verdict}${t.verdict === "incomplete" && t.v2v_s !== undefined ? " (not timed)" : ""}`] : []),
    ...(t.reply !== "spoken" ? [REPLY[t.reply] ?? t.reply] : []),
    ...t.flags.map((f) => f.replace(/_/g, " ")),
  ];
  const bad = new Set(t.flags.map((f) => f.replace(/_/g, " ")));
  return (
    <tr>
      <td data-label="Turn" className={`${s.lead} ${s.turnNo}`}>{t.turn}</td>
      <td data-label={content ? "What was said" : "Caller"} className={s.wide}>
        {content ? (
          <div className={s.said}>
            <p><b>Caller</b> {t.text || <span className={s.muted}>(nothing heard)</span>}</p>
            {replies.map((x, i) => (
              <p key={i} className={unheard ? s.unheard : undefined}>
                <b>{i === 0 ? "BolDoc" : "then"}</b> {x}
              </p>
            ))}
          </div>
        ) : (
          <span>{t.words} {t.words === 1 ? "word" : "words"}</span>
        )}
        {(notes.length > 0 || unheard || (t.tools?.length ?? 0) > 0 || (t.deferred?.length ?? 0) > 0) && (
          <span className={s.chips}>
            {unheard && <span className={`${s.chip} ${s.chipBad}`}>never heard</span>}
            {notes.map((x) => <span key={x} className={`${s.chip} ${bad.has(x) ? s.chipBad : ""}`}>{x}</span>)}
            {(t.tools ?? []).map((x) => <span key={x} className={s.chip}>{x}</span>)}
            {(t.deferred ?? []).map((x) => <span key={`d-${x}`} className={s.chip}>{x}: put off</span>)}
            {t.silence_s !== undefined && <span className={`${s.chip} ${s.chipBad}`}>silent {fmtS(t.silence_s, 1)}</span>}
          </span>
        )}
      </td>
      <td data-label="Speech" className={s.num}>{t.speech_s !== null && t.speech_s !== undefined ? fmtS(t.speech_s, 1) : "–"}</td>
      <td data-label="End of turn" className={s.num}>{fmtS(t.eot_s)}</td>
      <td data-label="LLM" className={s.num}>{t.ttft_s !== undefined ? fmtS(t.ttft_s) : "–"}</td>
      <td data-label="TTS" className={s.num}>{t.tts_s !== undefined ? fmtS(t.tts_s) : "–"}</td>
      <td data-label="Hold" className={s.num}>{t.hold_s !== undefined ? fmtS(t.hold_s) : "–"}</td>
      <td data-label="Reply" className={`${s.num} ${t.v2v_s !== undefined && t.v2v_s > 1.5 ? s.slow : ""}`}>
        {t.v2v_s !== undefined ? fmtS(t.v2v_s) : "–"}
      </td>
    </tr>
  );
}

/** The talker's trace in order, each event in words, filtered by group. */
function Timeline({ d, content }: { d: CallDetail; content: boolean }) {
  const [hidden, setHidden] = useState<Set<Cat>>(() => new Set());
  const tl = d.timeline;
  if (!tl) {
    return (
      <section className={s.card} aria-labelledby="call-timeline">
        <h2 id="call-timeline" className={s.cardTitle}>Timeline</h2>
        <p className={`${s.muted} ${s.small}`}>No timeline for this call.</p>
      </section>
    );
  }
  const rows = tl.events.map((e) => ({ e, x: describe(e) }));
  const counts: Partial<Record<Cat, number>> = {};
  for (const r of rows) counts[r.x.cat] = (counts[r.x.cat] ?? 0) + 1;
  const shown = rows.filter((r) => !hidden.has(r.x.cat));
  const flip = (c: Cat) => setHidden((h) => {
    const next = new Set(h);
    if (next.has(c)) next.delete(c);
    else next.add(c);
    return next;
  });
  return (
    <section className={s.card} aria-labelledby="call-timeline">
      <div className={s.cardHead}>
        <h2 id="call-timeline" className={s.cardTitle}>Timeline</h2>
        <p className={`${s.muted} ${s.small}`}>
          What the talker traced, in order, from the start of the call
          {tl.truncated ? ` (the first ${num(tl.events.length)} of ${num(tl.total)} events)` : ""}.
          {content ? "" : " The words were not kept, so events show what happened, not what was said."}
        </p>
      </div>
      <div className={s.chips} role="group" aria-label="Show these events">
        {CATS.map((c) => (
          <button key={c.key} type="button" className={s.toggle} aria-pressed={!hidden.has(c.key)} onClick={() => flip(c.key)}>
            {c.label} <span className={s.toggleCount}>{counts[c.key] ?? 0}</span>
          </button>
        ))}
      </div>
      {shown.length ? (
        <ol className={s.timeline} aria-label="Events">
          {shown.map(({ e, x }, i) => (
            <li key={i} className={`${s.tlRow} ${x.bad ? s.tlBad : ""}`} data-cat={x.cat}>
              <span className={s.tlTime}>{at(e.t)}</span>
              <span className={s.tlDot} aria-hidden="true" />
              <span className={s.tlWhat}>
                <span className={s.tlLabel}>{x.label}</span>
                {x.detail && <span className={s.tlDetail}>{x.detail}</span>}
                {x.text && <span className={s.tlText}>{x.text}</span>}
              </span>
            </li>
          ))}
        </ol>
      ) : <p className={`${s.muted} ${s.small}`}>Every group is hidden: turn one back on above.</p>}
    </section>
  );
}
