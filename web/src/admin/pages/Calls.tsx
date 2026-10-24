import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";

import { api, type CallFilters, type CallRow } from "../api";
import s from "../admin.module.css";
import { fmtS } from "../charts";
import { inr, istDate, mmss, refused, when } from "../format";
import { CallId, PageHead, Problem, Seg, Skeleton, useLoad } from "../ui";
import { KINDS, pick } from "./Overview";

const SLOW = [{ value: "", label: "Any" }, { value: "1.5", label: "Over 1.5 s" }, { value: "2", label: "Over 2 s" },
  { value: "3", label: "Over 3 s" }];

export function channelLabel(c: Pick<CallRow, "channel" | "via">): string {
  const base = c.channel === "browser" ? "Browser" : c.channel === "test" ? "Test line" : "Phone";
  return c.via === "call_me" ? `${base} · call me` : base;
}

export function smsLabel(statuses: string[] | undefined): string {
  if (!statuses?.length) return "–";
  const n: Record<string, number> = {};
  for (const st of statuses) n[st] = (n[st] ?? 0) + 1;
  return Object.entries(n).map(([st, k]) => `${k} ${st}`).join(", ");
}

/** Every call of the window, newest first; the filters live in the address, so a filtered view can be shared. */
export function CallsPage() {
  const [params, setParams] = useSearchParams();
  const { search } = useLocation();
  const f: CallFilters = {
    start: params.get("start") ?? istDate(-6), end: params.get("end") ?? istDate(0),
    kind: pick(params.get("kind"), KINDS, "real"), channel: params.get("channel") ?? "", q: params.get("q") ?? "",
    min_v2v: params.get("min_v2v") ?? "", has_doc: params.get("has_doc") === "true", flag: params.get("flag") ?? "",
  };
  const load = useLoad(() => api.calls(f), JSON.stringify(f));
  const set = (key: string, value: string) => setParams((p) => {
    const next = new URLSearchParams(p);
    if (value) next.set(key, value);
    else next.delete(key);
    return next;
  }, { replace: true });

  // the number box: typed freely, sent once typing pauses; it follows the address (back, forward, clear)
  const [q, setQ] = useState(f.q ?? "");
  const timer = useRef<number | undefined>(undefined);
  const typeQ = (v: string) => {
    setQ(v);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => set("q", v.replace(/\D/g, "")), 350);
  };
  const addressQ = params.get("q") ?? "";
  useEffect(() => { setQ(addressQ); }, [addressQ]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const filtered = ["channel", "q", "min_v2v", "has_doc", "flag"].some((k) => params.get(k));
  const rows = load.data?.calls ?? [];
  const answered = rows.filter((c) => c.decision === "admit").length;

  return (
    <>
      <PageHead title="Calls" lede="Every call in the window, newest first. Open one for its turns, checks and cost." />
      <div className={s.filterBar} role="search" aria-label="Filter calls">
        <label className={s.field}>
          <span>From</span>
          <input className={s.input} type="date" value={f.start} max={f.end} onChange={(e) => set("start", e.target.value)} />
        </label>
        <label className={s.field}>
          <span>To</span>
          <input className={s.input} type="date" value={f.end} min={f.start} max={istDate(0)}
            onChange={(e) => set("end", e.target.value)} />
        </label>
        <div className={`${s.field} ${s.segField}`}>
          <span>Calls</span>
          <Seg label="Which calls" value={f.kind ?? "real"} options={KINDS} onChange={(v) => set("kind", v)} />
        </div>
        <label className={s.field}>
          <span>Channel</span>
          <select className={s.select} value={f.channel} onChange={(e) => set("channel", e.target.value)}>
            <option value="">Any</option>
            <option value="phone">Phone</option>
            <option value="browser">Browser</option>
          </select>
        </label>
        <label className={s.field}>
          <span>Number or call ID</span>
          <input className={`${s.input} ${s.idSearch}`} type="search" placeholder="6655 or 08edee88" value={q}
            onChange={(e) => typeQ(e.target.value)} />
        </label>
        <label className={s.field}>
          <span>Slowest reply</span>
          <select className={s.select} value={f.min_v2v} onChange={(e) => set("min_v2v", e.target.value)}>
            {SLOW.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        <label className={s.field}>
          <span>Flag</span>
          <select className={s.select} value={f.flag} onChange={(e) => set("flag", e.target.value)}>
            <option value="">Any</option>
            {(load.data?.flags ?? (f.flag ? [f.flag] : [])).map((x) => <option key={x} value={x}>{x}</option>)}
          </select>
        </label>
        <label className={s.check}>
          <input type="checkbox" checked={!!f.has_doc} onChange={(e) => set("has_doc", e.target.checked ? "true" : "")} />
          Made a document
        </label>
        {filtered && (
          <button type="button" className={s.btnSm} onClick={() => {
            window.clearTimeout(timer.current);
            setQ("");
            setParams((p) => {
              const next = new URLSearchParams();
              for (const k of ["start", "end", "kind"]) { const v = p.get(k); if (v) next.set(k, v); }
              return next;
            }, { replace: true });
          }}>Clear filters</button>
        )}
      </div>

      {load.error && <Problem error={load.error} retry={load.reload} />}
      {!load.data && !load.error && <Skeleton rows={6} />}
      {load.data && (
        <section className={`${s.card} ${load.loading ? s.stale : ""}`} aria-label="Calls" aria-busy={load.loading}>
          <p className={s.label}>
            {rows.length} {rows.length === 1 ? "call" : "calls"} · {answered} answered, {rows.length - answered} turned away
            {rows.length >= 200 ? " · the newest 200" : ""}
          </p>
          {rows.length ? (
            <div className={s.tableWrap}>
              <table className={`${s.table} ${s.stackTable}`}>
                <thead>
                  <tr>
                    <th scope="col">When</th><th scope="col">Call ID</th><th scope="col">Caller</th>
                    <th scope="col">Channel</th>
                    <th scope="col" className={s.num}>Length</th><th scope="col" className={s.num}>Turns</th>
                    <th scope="col" className={s.num}>Reply p50</th><th scope="col" className={s.num}>p90</th>
                    <th scope="col" className={s.num}>Slowest</th><th scope="col">Documents</th><th scope="col">SMS</th>
                    <th scope="col" className={s.num}>Cost</th><th scope="col">Issues</th>
                  </tr>
                </thead>
                <tbody>{rows.map((c) => <Row key={c.id} c={c} from={search} />)}</tbody>
              </table>
            </div>
          ) : <p className={s.empty}>No calls match these filters.</p>}
        </section>
      )}
    </>
  );
}

function Row({ c, from }: { c: CallRow; from: string }) {
  const docs = c.docs ? [c.docs.create ? `${c.docs.create} new` : "", c.docs.edit ? `${c.docs.edit} edited` : ""]
    .filter(Boolean).join(", ") : "";
  const failed = c.checks_failed?.length ?? 0;
  return (
    <tr>
      <td data-label="When" className={s.lead}>
        <Link className={s.rowLink} to={`/calls/${c.id}`} state={{ from }}>{when(c.at)}</Link>
      </td>
      <td data-label="Call ID" className={s.nowrap}><CallId id={c.id} /></td>
      <td data-label="Caller" className={s.nowrap}>{c.caller ?? "Withheld"}</td>
      <td data-label="Channel" className={s.nowrap}>
        {channelLabel(c)}{c.test && c.channel !== "test" ? <span className={s.chip}>test</span> : null}
      </td>
      <td data-label="Length" className={s.num}>{mmss(c.duration_s)}</td>
      <td data-label="Turns" className={s.num}>{c.turns ?? "–"}</td>
      <td data-label="Reply p50" className={s.num}>{fmtS(c.v2v_p50_s)}</td>
      <td data-label="p90" className={`${s.num} ${s.hidePhone}`}>{fmtS(c.v2v_p90_s)}</td>
      <td data-label="Slowest" className={s.num}>{fmtS(c.v2v_max_s)}</td>
      <td data-label="Documents" className={s.nowrap}>{docs || "–"}</td>
      <td data-label="SMS" className={s.nowrap}>{smsLabel(c.sms)}</td>
      <td data-label="Cost" className={s.num}>{inr(c.cost_inr)}</td>
      <td data-label="Issues" className={s.wide}>
        {c.decision !== "admit" ? (
          <span className={s.chips}><span className={s.chip}>Turned away: {refused(c.reason).toLowerCase()}</span></span>
        ) : (c.flags?.length || failed) ? (
          <span className={s.chips}>
            {(c.flags ?? []).map((x) => <span key={x} className={`${s.chip} ${s.chipBad}`}>{x}</span>)}
            {failed ? <span className={`${s.chip} ${s.chipBad}`}>{failed} {failed === 1 ? "check" : "checks"} failed</span> : null}
          </span>
        ) : <span className={s.muted}>None</span>}
      </td>
    </tr>
  );
}
