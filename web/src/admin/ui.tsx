/* What the admin pages share: loading data, the page head, segmented controls, a status with an icon, the
   loading and error states. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { ApiError } from "./api";
import s from "./admin.module.css";

export function message(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof TypeError) return "Can't reach the server. Check the connection and try again.";
  return "Something went wrong. Try again.";
}

export type Load<T> = { data: T | null; error: string | null; loading: boolean; reload: () => void };

/** Loads when `key` changes; the last data stays on screen (dimmed) while the next loads. */
export function useLoad<T>(fn: () => Promise<T>, key: string): Load<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const latest = useRef(fn);
  latest.current = fn;
  useEffect(() => {
    let live = true;
    setLoading(true);
    latest.current()
      .then((d) => { if (live) { setData(d); setError(null); } })
      .catch((e: unknown) => { if (live) setError(message(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [key, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

export function useTitle(title: string): void {
  useEffect(() => { document.title = `${title} · BolDoc admin`; }, [title]);
}

export function PageHead({ title, lede, children }: { title: string; lede: ReactNode; children?: ReactNode }) {
  useTitle(title);
  return (
    <div className={s.titleRow}>
      <div>
        <h1 className={s.h1}>{title}</h1>
        <p className={s.lede}>{lede}</p>
      </div>
      {children && <div className={s.filters}>{children}</div>}
    </div>
  );
}

export function Seg<T extends string>({ label, value, options, onChange }: { label: string; value: T;
  options: readonly { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className={s.seg} role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Pass or fail: an icon and words, never colour alone. */
export function Status({ ok, children }: { ok: boolean; children: ReactNode }) {
  return (
    <span className={s.status}>
      {ok ? (
        <svg className={`${s.statusIcon} ${s.ok}`} viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="6.6" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M5.2 8.2l1.9 1.9 3.8-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"
            strokeLinejoin="round" />
        </svg>
      ) : (
        <svg className={`${s.statusIcon} ${s.bad}`} viewBox="0 0 16 16" aria-hidden="true">
          <path d="M8 1.8l6.6 11.6H1.4z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
          <path d="M8 6.4v3.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          <circle cx="8" cy="11.6" r="0.9" fill="currentColor" />
        </svg>
      )}
      <span><span className="visually-hidden">{ok ? "Passed: " : "Failed: "}</span>{children}</span>
    </span>
  );
}

export function Problem({ error, retry }: { error: string; retry?: () => void }) {
  return (
    <div className={s.card} role="alert">
      <p className={s.cardTitle}>Couldn't load this</p>
      <p className={s.muted}>{error}</p>
      {retry && <button type="button" className={s.btnSm} onClick={retry}>Try again</button>}
    </div>
  );
}

/** A labelled fact: the label small, the value large, an optional line under it. */
export function Fact({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className={s.fact}>
      <dt className={s.label}>{label}</dt>
      <dd className={s.factValue}>{value}</dd>
      {sub && <dd className={`${s.muted} ${s.small}`}>{sub}</dd>}
    </div>
  );
}

/** A call's ID (Plivo's CallUUID): its first 8 characters shown, which is enough to find it ("08edee88" in the calls
 * search, /calls/08edee88, the call report); the whole ID copied on a tap. */
export function CallId({ id, full = false }: { id: string; full?: boolean }) {
  const [copied, setCopied] = useState(false);
  const text = useRef<HTMLSpanElement>(null);
  async function copy(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {  // no clipboard here: select it, for the viewer's own copy
      const sel = window.getSelection();
      if (text.current && sel) {
        const r = document.createRange();
        r.selectNodeContents(text.current);
        sel.removeAllRanges();
        sel.addRange(r);
      }
    }
  }
  return (
    <button type="button" className={s.callId} onClick={copy} aria-label={`Copy call ID ${id}`} title={id}>
      <span ref={text} className={s.mono}>{full ? id : id.slice(0, 8)}</span>
      <span className={s.copyNote} aria-live="polite">{copied ? "Copied" : "Copy"}</span>
    </button>
  );
}

/** The page's shape while its first data loads. */
export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className={s.card} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => <span key={i} className={s.skeleton} style={{ width: `${88 - i * 17}%` }} />)}
    </div>
  );
}
