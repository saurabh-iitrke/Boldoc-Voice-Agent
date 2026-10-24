import { useEffect, useMemo, useState } from "react";

import { api, ApiError, type AgentSetting, type AgentValue, type AgentVersion, type AgentView } from "../api";
import s from "../admin.module.css";
import a from "./agent.module.css";
import { when } from "../format";
import { message, PageHead, Problem, Skeleton, useLoad, useTitle } from "../ui";

/** What a setting's value reads as: On/Off, a choice's name, or a number with its unit. */
function show(st: AgentSetting, v: AgentValue | undefined): string {
  if (v === undefined) return "–";
  if (st.kind === "bool") return v ? "On" : "Off";
  if (st.kind === "choice") return st.choices.find((c) => c.value === v)?.label ?? String(v);
  if (st.unit === "₹") return `₹${v}`;
  return st.unit ? `${v} ${st.unit}` : String(v);
}

/** The agent's settings: what may change without a deploy, each range-checked, every publish a version
 * with a note, any version restorable. A publish applies from the next call; prices from the next charge. */
export function AgentPage() {
  useTitle("Agent");
  const load = useLoad(() => api.agent(), "agent");
  const [view, setView] = useState<AgentView | null>(null);
  const [draft, setDraft] = useState<Record<string, AgentValue>>({});
  const [invalid, setInvalid] = useState<Record<string, string>>({});
  const [reviewing, setReviewing] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  useEffect(() => { if (load.data) setView(load.data); }, [load.data]);

  const byKey = useMemo(() => Object.fromEntries((view?.schema ?? []).map((st) => [st.key, st])), [view]);
  const live = view?.live.settings ?? {};
  const changed = Object.keys(draft);

  function set(key: string, value: AgentValue) {
    setDone(null);
    setDraft((d) => {
      const next = { ...d };
      if (value === live[key]) delete next[key];
      else next[key] = value;
      return next;
    });
  }
  function bad(key: string, why: string | null) {
    setInvalid((e) => {
      const next = { ...e };
      if (why) next[key] = why;
      else delete next[key];
      return next;
    });
  }
  function published(v: AgentView, what: string) {
    setView(v);
    setDraft({});
    setInvalid({});
    setReviewing(false);
    setDone(what);
  }

  if (load.error && !view) return <Problem error={load.error} retry={load.reload} />;
  if (!view) return <><Head /><Skeleton rows={8} /></>;

  return (
    <>
      <Head />
      <Live view={view} />
      {done && <p className={a.done} role="status">{done}</p>}
      {view.groups.map((g) => (
        <section key={g.id} className={s.card} aria-labelledby={`g-${g.id}`}>
          <div className={a.groupHead}>
            <h2 id={`g-${g.id}`} className={s.cardTitle}>{g.label}</h2>
            <span className={s.label}>{g.when}</span>
          </div>
          <ul className={a.rows}>
            {view.schema.filter((st) => st.group === g.id).map((st) => (
              <Row key={st.key} st={st} live={live[st.key]!} value={draft[st.key] ?? live[st.key]!}
                   changed={st.key in draft} error={invalid[st.key]} onChange={(v) => set(st.key, v)}
                   onInvalid={(why) => bad(st.key, why)} />
            ))}
          </ul>
        </section>
      ))}
      <History view={view} onRestored={(v, n) => published(v, `Version ${n} restored as version ${v.live.version}. `
        + "It applies from the next call.")} />
      {changed.length > 0 && !reviewing && <div className={a.barRoom} aria-hidden="true" />}
      {changed.length > 0 && !reviewing && (
        <div className={a.bar} role="region" aria-label="Unpublished changes">
          <span><strong>{changed.length} {changed.length === 1 ? "change" : "changes"}</strong> not published</span>
          <span className={a.barActions}>
            <button type="button" className={s.btnSm} onClick={() => { setDraft({}); setInvalid({}); }}>Discard</button>
            <button type="button" className={a.ink} disabled={Object.keys(invalid).length > 0}
                    onClick={() => setReviewing(true)}>Review and publish</button>
          </span>
        </div>
      )}
      {reviewing && (
        <Review view={view} draft={draft} byKey={byKey} onCancel={() => setReviewing(false)}
                onPublished={(v) => published(v, `Version ${v.live.version} is live. It applies from the next call`
                  + (Object.keys(draft).some((k) => byKey[k]?.group === "prices") ? "; prices from the next charge."
                    : "."))}
                onStale={(v) => { setView(v); setReviewing(false); }} />
      )}
    </>
  );
}

function Head() {
  return (
    <PageHead title="Agent" lede="What BolDoc does on calls and what it charges. Change a setting, review it, publish:
      it applies from the next call, prices from the next charge. Every version can be restored." />
  );
}

function Live({ view }: { view: AgentView }) {
  const l = view.live;
  return (
    <section className={`${s.card} ${a.live}`} aria-label="What's live">
      <span className={a.liveDot} aria-hidden="true" />
      <div className={a.liveText}>
        <p className={a.liveTitle}>{l.version ? `Live: version ${l.version}` : "Live: today's defaults"}</p>
        <p className={s.muted}>
          {l.version ? <>Published by {l.published_by}, {when(l.published_at!)}. “{l.note}”</>
            : "Nothing has been published from here yet: every setting is the value BolDoc started with."}
        </p>
      </div>
    </section>
  );
}

function Row({ st, live, value, changed, error, onChange, onInvalid }: {
  st: AgentSetting; live: AgentValue; value: AgentValue; changed: boolean; error?: string;
  onChange: (v: AgentValue) => void; onInvalid: (why: string | null) => void;
}) {
  const id = `set-${st.key}`;
  return (
    <li className={`${a.row} ${changed ? a.rowChanged : ""}`}>
      <div className={a.what}>
        <label htmlFor={id} className={a.name}>{st.label}</label>
        {st.help && <p className={a.help}>{st.help}</p>}
      </div>
      <div className={a.control}>
        {st.kind === "bool" ? (
          <button id={id} type="button" role="switch" aria-checked={value === true} className={a.switch}
                  onClick={() => onChange(!value)}>
            <span className={a.knob} aria-hidden="true" />
            <span className={a.switchText}>{value ? "On" : "Off"}</span>
          </button>
        ) : st.kind === "choice" ? (
          <select id={id} className={`${s.select} ${a.select}`} value={String(value)}
                  onChange={(e) => onChange(e.target.value)}>
            {st.choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
        ) : (
          <NumberField id={id} st={st} value={value as number} error={error} onChange={onChange} onInvalid={onInvalid} />
        )}
        <p className={a.meta}>
          {changed ? <>Was {show(st, live)} · <button type="button" className={s.linkBtn}
            onClick={() => { onInvalid(null); onChange(live); }}>Undo</button></>
            : <>Default {show(st, st.default)}{value !== st.default && <> · <button type="button" className={s.linkBtn}
              onClick={() => onChange(st.default)}>Reset to default</button></>}</>}
        </p>
      </div>
    </li>
  );
}

function NumberField({ id, st, value, error, onChange, onInvalid }: {
  id: string; st: AgentSetting; value: number; error?: string; onChange: (v: AgentValue) => void;
  onInvalid: (why: string | null) => void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => { setText(String(value)); }, [value]);
  const range = `${st.unit === "₹" ? "₹" : ""}${st.min}–${st.unit === "₹" ? "₹" : ""}${st.max}`
    + (st.unit && st.unit !== "₹" ? ` ${st.unit}` : "");
  function typed(t: string) {
    setText(t);
    const n = Number(t);
    const why = t.trim() === "" || Number.isNaN(n) ? "a number"
      : st.kind === "int" && !Number.isInteger(n) ? "a whole number"
        : (st.min !== null && n < st.min) || (st.max !== null && n > st.max) ? `between ${range}` : null;
    onInvalid(why ? `Must be ${why}.` : null);
    if (!why) onChange(n);
  }
  return (
    <span className={a.number}>
      <span className={a.numberBox}>
        {st.unit === "₹" && <span className={a.unit}>₹</span>}
        <input id={id} className={`${s.input} ${a.input}`} inputMode={st.kind === "int" ? "numeric" : "decimal"}
               value={text} onChange={(e) => typed(e.target.value)} aria-invalid={!!error}
               aria-describedby={`${id}-range`} />
        {st.unit && st.unit !== "₹" && <span className={a.unit}>{st.unit}</span>}
      </span>
      <span id={`${id}-range`} className={error ? `${a.range} ${s.error}` : a.range}>{error ?? range}</span>
    </span>
  );
}

function Review({ view, draft, byKey, onCancel, onPublished, onStale }: {
  view: AgentView; draft: Record<string, AgentValue>; byKey: Record<string, AgentSetting>;
  onCancel: () => void; onPublished: (v: AgentView) => void; onStale: (v: AgentView) => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const prices = Object.keys(draft).some((k) => byKey[k]?.group === "prices");

  async function publish() {
    setBusy(true);
    setError(null);
    try {
      onPublished(await api.publishAgent(view.live.version, draft, note));
    } catch (e) {
      if (e instanceof ApiError && e.code === "published_meanwhile") {
        setError("Someone published a newer version meanwhile. It's loaded now: check your changes again.");
        onStale(await api.agent());
      } else if (e instanceof ApiError && e.body.errors) {
        const errors = e.body.errors as Record<string, string>;
        setError(Object.entries(errors).map(([k, v]) => `${byKey[k]?.label ?? (k === "note" ? "Note" : k)}: ${v}`)
          .join(". "));
      } else setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={`${s.card} ${a.review}`} aria-labelledby="review-h">
      <h2 id="review-h" className={s.cardTitle}>Review version {view.live.version + 1}</h2>
      <div className={s.tableWrap}>
        <table className={s.table}>
          <thead><tr><th scope="col">Setting</th><th scope="col">Live</th><th scope="col">New</th></tr></thead>
          <tbody>
            {Object.entries(draft).map(([k, v]) => (
              <tr key={k}><td>{byKey[k]?.label ?? k}</td><td>{show(byKey[k]!, view.live.settings[k])}</td>
                <td><strong>{show(byKey[k]!, v)}</strong></td></tr>
            ))}
          </tbody>
        </table>
      </div>
      {prices && <p className={a.notice}>Callers are charged the new prices from their next charge. Nothing already
        charged changes.</p>}
      <label className={s.field}>
        <span>What is this change for? (kept with the version)</span>
        <textarea className={`${s.input} ${a.note}`} rows={2} value={note} onChange={(e) => setNote(e.target.value)}
                  placeholder="e.g. Callers who think aloud were cut off" />
      </label>
      {error && <p className={s.error} role="alert">{error}</p>}
      <div className={a.reviewActions}>
        <button type="button" className={s.btnSm} onClick={onCancel} disabled={busy}>Back to editing</button>
        <button type="button" className={a.ink} onClick={() => void publish()} disabled={busy || !note.trim()}>
          {busy ? "Publishing…" : `Publish version ${view.live.version + 1}`}
        </button>
      </div>
    </section>
  );
}

function History({ view, onRestored }: { view: AgentView; onRestored: (v: AgentView, from: number) => void }) {
  const byKey = Object.fromEntries(view.schema.map((st) => [st.key, st]));
  return (
    <section className={s.card} aria-labelledby="hist-h">
      <h2 id="hist-h" className={s.cardTitle}>Versions</h2>
      {view.history.length === 0 ? <p className={s.empty}>Nothing published yet.</p> : (
        <ol className={a.versions}>
          {view.history.map((v) => (
            <Version key={v.version} v={v} byKey={byKey} live={v.version === view.live.version}
                     onRestored={(nv) => onRestored(nv, v.version)} />
          ))}
        </ol>
      )}
    </section>
  );
}

function Version({ v, byKey, live, onRestored }: {
  v: AgentVersion; byKey: Record<string, AgentSetting>; live: boolean; onRestored: (v: AgentView) => void;
}) {
  const [restoring, setRestoring] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function restore() {
    setBusy(true);
    setError(null);
    try { onRestored(await api.restoreAgent(v.version, note)); } catch (e) { setError(message(e)); } finally {
      setBusy(false);
    }
  }
  return (
    <li className={a.version}>
      <div className={a.versionHead}>
        <strong>Version {v.version}</strong>{live && <span className={a.livePill}>Live</span>}
        <span className={s.label}>{v.published_by}, {when(v.published_at)}</span>
        {!live && !restoring && (
          <button type="button" className={`${s.btnSm} ${a.restoreBtn}`} onClick={() => setRestoring(true)}>Restore</button>
        )}
      </div>
      <p>“{v.note}”{v.restored_from ? ` (restored version ${v.restored_from})` : ""}</p>
      <ul className={a.changes}>
        {Object.entries(v.changes).map(([k, [before, after]]) => (
          <li key={k}>{byKey[k]?.label ?? k}: {byKey[k] ? show(byKey[k]!, before) : String(before)} → {byKey[k]
            ? show(byKey[k]!, after) : String(after)}</li>
        ))}
      </ul>
      {restoring && (
        <div className={a.restore}>
          <input className={s.input} value={note} onChange={(e) => setNote(e.target.value)}
                 placeholder="Why restore it?" aria-label={`Why restore version ${v.version}?`} />
          <button type="button" className={a.ink} disabled={busy || !note.trim()} onClick={() => void restore()}>
            {busy ? "Restoring…" : `Restore version ${v.version}`}</button>
          <button type="button" className={s.btnSm} onClick={() => setRestoring(false)} disabled={busy}>Cancel</button>
          {error && <p className={s.error} role="alert">{error}</p>}
        </div>
      )}
    </li>
  );
}
