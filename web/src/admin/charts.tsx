/* Small, quiet charts in plain SVG (no chart library): thin marks, hairline grids, direct labels, a hover/focus layer,
      and a table view for every chart. Colour follows the "emphasis" form: one series in the accent (--voice), context in
      secondary ink. Validated for the light card and the dark card.
*/
import { useEffect, useId, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent,
  type ReactNode } from "react";
import { Link } from "react-router-dom";

import s from "./charts.module.css";

export function fmtS(v: number | null | undefined, digits = 2): string {
  return v === null || v === undefined ? "–" : `${v.toFixed(digits)} s`;
}

export function compact(n: number): string {
  if (n >= 100000) return `${(n / 1000).toFixed(0)}K`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(Math.round(n * 10) / 10);
}

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(600);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => { if (e) setW(Math.max(260, Math.round(e.contentRect.width))); });
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function ticks(max: number, count = 4): number[] {
  if (max <= 0) return [0];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw) ?? raw;
  return Array.from({ length: Math.floor(max / step) + 2 }, (_, i) => Math.round(i * step * 1000) / 1000)
    .filter((_t, i, a) => i === 0 || a[i - 1]! < max);
}

/** The single number a view leads with (one per view). */
export function Hero({ label, value, children }: { label: string; value: string; children?: ReactNode }) {
  return (
    <div className={s.hero}>
      <p className={s.label}>{label}</p>
      <p className={s.heroValue}>{value}</p>
      {children && <div className={s.heroSub}>{children}</div>}
    </div>
  );
}

export function Stat({ label, value, sub }: { label: string; value: string; sub?: ReactNode }) {
  return (
    <div className={s.stat}>
      <p className={s.label}>{label}</p>
      <p className={s.statValue}>{value}</p>
      {sub && <p className={s.sub}>{sub}</p>}
    </div>
  );
}

const PAD = { top: 18, right: 64, bottom: 26, left: 40 };

type Tip = { i: number } | null;

function useTip(n: number) {
  const [tip, setTip] = useState<Tip>(null);
  const onKey = (e: KeyboardEvent) => {
    if (!n) return;
    if (e.key === "ArrowRight") setTip((t) => ({ i: Math.min(n - 1, (t?.i ?? -1) + 1) }));
    else if (e.key === "ArrowLeft") setTip((t) => ({ i: Math.max(0, (t?.i ?? n) - 1) }));
    else if (e.key === "Escape") setTip(null);
    else return;
    e.preventDefault();
  };
  return { tip, setTip, onKey };
}

/** Calls per bucket: columns in secondary ink, the latest bucket in the accent. */
export function TrendColumns({ data, title, unit }: { data: { bucket: string; value: number }[]; title: string;
  unit: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const { tip, setTip, onKey } = useTip(data.length);
  const height = 190;
  const inner = { w: width - PAD.left - 16, h: height - PAD.top - PAD.bottom };
  const max = Math.max(1, ...data.map((d) => d.value));
  const ys = ticks(max);
  const top = ys[ys.length - 1] ?? max;
  const band = inner.w / Math.max(1, data.length);
  const barW = Math.min(24, Math.max(4, band - 6));
  const y = (v: number) => PAD.top + inner.h - (v / top) * inner.h;
  const every = Math.ceil(data.length / Math.max(1, Math.floor(inner.w / 56)));
  const move = (e: PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientX - box.left - PAD.left) / band);
    setTip(i >= 0 && i < data.length ? { i } : null);
  };
  const hovered = tip ? data[tip.i] : undefined;
  return (
    <figure className={s.figure} ref={ref}>
      <figcaption className={s.caption}>{title}</figcaption>
      <div className={s.plot}>
        <svg width={width} height={height} role="img" aria-label={`${title}, as a column chart; the table below has the values`}
          tabIndex={0} onKeyDown={onKey} onPointerMove={move} onPointerLeave={() => setTip(null)} onBlur={() => setTip(null)}>
          {ys.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={PAD.left + inner.w} y1={y(t)} y2={y(t)} className={s.grid} />
              <text x={PAD.left - 8} y={y(t)} className={s.tick} textAnchor="end" dominantBaseline="middle">{compact(t)}</text>
            </g>
          ))}
          {data.map((d, i) => {
            const x = PAD.left + i * band + (band - barW) / 2;
            const h = Math.max(d.value > 0 ? 2 : 0, (d.value / top) * inner.h);
            const r = Math.min(4, barW / 2, h);
            const yTop = PAD.top + inner.h - h;
            return (
              <g key={d.bucket}>
                <path className={i === data.length - 1 ? s.barNow : s.bar} opacity={tip && tip.i !== i ? 0.55 : 1}
                  d={`M${x},${yTop + h} V${yTop + r} Q${x},${yTop} ${x + r},${yTop} H${x + barW - r} ` +
                     `Q${x + barW},${yTop} ${x + barW},${yTop + r} V${yTop + h} Z`} />
                {i % every === 0 || i === data.length - 1 ? (
                  <text x={x + barW / 2} y={height - 8} className={s.tick} textAnchor="middle">{d.bucket}</text>
                ) : null}
              </g>
            );
          })}
          {data.length > 0 && (() => {
            const last = data[data.length - 1]!;
            const x = PAD.left + (data.length - 1) * band + band / 2;
            return <text x={x} y={y(last.value) - 7} className={s.endLabel} textAnchor="middle">{compact(last.value)}</text>;
          })()}
        </svg>
        {hovered && tip && (
          <div className={s.tip} style={{ left: Math.min(width - 150, Math.max(0, PAD.left + tip.i * band + band / 2 - 70)) }}>
            <strong>{compact(hovered.value)} {unit}</strong>
            <span>{hovered.bucket}</span>
          </div>
        )}
      </div>
      <TableView heads={["", unit]} rows={data.map((d) => [d.bucket, compact(d.value)])} />
    </figure>
  );
}

type Series = { key: string; label: string; tone: "accent" | "context"; values: (number | null)[] };

/** Reply time per bucket: two lines (the accent for the typical reply, secondary ink for the tail), a target line. */
export function TrendLines({ buckets, series, title, target }: { buckets: string[]; series: Series[]; title: string;
  target?: { value: number; label: string } }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const { tip, setTip, onKey } = useTip(buckets.length);
  const id = useId();
  const height = 210;
  const inner = { w: width - PAD.left - PAD.right, h: height - PAD.top - PAD.bottom };
  const all = series.flatMap((x) => x.values.filter((v): v is number => v !== null));
  const max = Math.max(target?.value ?? 0, ...all, 0.5);
  const ys = ticks(max * 1.08);
  const top = ys[ys.length - 1] ?? max;
  const step = buckets.length > 1 ? inner.w / (buckets.length - 1) : 0;
  const x = (i: number) => PAD.left + (buckets.length > 1 ? i * step : inner.w / 2);
  const y = (v: number) => PAD.top + inner.h - (v / top) * inner.h;
  const every = Math.ceil(buckets.length / Math.max(1, Math.floor(inner.w / 56)));
  const path = (vals: (number | null)[]) => {
    let d = "";
    let pen = false;
    vals.forEach((v, i) => {
      if (v === null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
      pen = true;
    });
    return d.trim();
  };
  const move = (e: PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - box.left;
    const i = buckets.length > 1 ? Math.round((px - PAD.left) / step) : 0;
    setTip(i >= 0 && i < buckets.length ? { i } : null);
  };
  const lastIdx = (vals: (number | null)[]) => {
    for (let i = vals.length - 1; i >= 0; i--) if (vals[i] !== null) return i;
    return -1;
  };
  // end labels: keep them apart when the two lines end close together (a short leader line to each end)
  const ends = series.map((sr) => ({ sr, i: lastIdx(sr.values) })).filter((e) => e.i >= 0)
    .map((e) => ({ ...e, yv: y(e.sr.values[e.i]!) })).sort((a, b) => a.yv - b.yv);
  for (let k = 1; k < ends.length; k++) {
    if (ends[k]!.yv - ends[k - 1]!.yv < 16) ends[k]!.yv = ends[k - 1]!.yv + 16;
  }
  return (
    <figure className={s.figure} ref={ref}>
      <figcaption className={s.caption}>{title}</figcaption>
      <ul className={s.legend} aria-label="Legend">
        {series.map((sr) => (
          <li key={sr.key}><span className={`${s.key} ${sr.tone === "accent" ? s.keyAccent : s.keyContext}`} />{sr.label}</li>
        ))}
        {target && <li><span className={`${s.key} ${s.keyTarget}`} />{target.label}</li>}
      </ul>
      <div className={s.plot}>
        <svg width={width} height={height} role="img" tabIndex={0} onKeyDown={onKey} onPointerMove={move}
          onPointerLeave={() => setTip(null)} onBlur={() => setTip(null)}
          aria-label={`${title}, as a line chart; the table below has the values`} aria-describedby={id}>
          {ys.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={PAD.left + inner.w} y1={y(t)} y2={y(t)} className={s.grid} />
              <text x={PAD.left - 8} y={y(t)} className={s.tick} textAnchor="end" dominantBaseline="middle">{t} s</text>
            </g>
          ))}
          {target && <line x1={PAD.left} x2={PAD.left + inner.w} y1={y(target.value)} y2={y(target.value)}
            className={s.target} />}
          {buckets.map((b, i) => (i % every === 0 || i === buckets.length - 1) && (
            <text key={b} x={x(i)} y={height - 8} className={s.tick} textAnchor="middle">{b}</text>
          ))}
          {tip && <line x1={x(tip.i)} x2={x(tip.i)} y1={PAD.top} y2={PAD.top + inner.h} className={s.crosshair} />}
          {series.map((sr) => (
            <path key={sr.key} d={path(sr.values)} className={sr.tone === "accent" ? s.lineAccent : s.lineContext} />
          ))}
          {ends.map(({ sr, i, yv }) => (
            <g key={sr.key}>
              <circle cx={x(i)} cy={y(sr.values[i]!)} r={4} className={sr.tone === "accent" ? s.dotAccent : s.dotContext} />
              {Math.abs(yv - y(sr.values[i]!)) > 2 && (
                <line x1={x(i) + 6} x2={x(i) + 10} y1={y(sr.values[i]!)} y2={yv} className={s.leader} />
              )}
              <text x={x(i) + 12} y={yv} className={s.endLabel} dominantBaseline="middle">
                {sr.label} {fmtS(sr.values[i], 2)}
              </text>
            </g>
          ))}
          {tip && series.map((sr) => sr.values[tip.i] !== null && (
            <circle key={sr.key} cx={x(tip.i)} cy={y(sr.values[tip.i]!)} r={4}
              className={sr.tone === "accent" ? s.dotAccent : s.dotContext} />
          ))}
        </svg>
        {tip && (
          <div className={s.tip} style={{ left: Math.min(width - 170, Math.max(0, x(tip.i) - 80)) }}>
            {series.map((sr) => (
              <span key={sr.key} className={s.tipRow}>
                <span className={`${s.key} ${sr.tone === "accent" ? s.keyAccent : s.keyContext}`} />
                <strong>{fmtS(sr.values[tip.i])}</strong> {sr.label}
              </span>
            ))}
            <span>{buckets[tip.i]}</span>
          </div>
        )}
      </div>
      <p id={id} className="visually-hidden">Use the left and right arrow keys to read each point.</p>
      <TableView heads={["", ...series.map((sr) => sr.label)]}
        rows={buckets.map((b, i) => [b, ...series.map((sr) => fmtS(sr.values[i]))])} />
    </figure>
  );
}

export type BarRow = { label: string; value: number | null; note?: string; to?: string };

/** Magnitudes side by side: one hue, the value at the tip of each bar (so every value is on the page). Lists that
 * sit together pass one `max` and one `labelWidth`, so their bars share a scale and a baseline. A row with `to` links
 * to the calls behind it. */
export function BarList({ rows, format = compact, max, labelWidth, tone }: { rows: BarRow[];
  format?: (v: number) => string; max?: number; labelWidth?: string; tone?: "accent" | "ink" }) {
  const top = max ?? Math.max(1e-9, ...rows.map((r) => r.value ?? 0));
  // the longest bar leaves room for its value (and note) at its tip, inside the card
  return (
    <ul className={`${s.barList} ${rows.some((r) => r.note) ? s.noted : ""} ${tone === "accent" ? s.toneAccent
      : tone === "ink" ? s.toneInk : ""}`}
      style={labelWidth ? { "--label": labelWidth } as CSSProperties : undefined}>
      {rows.map((r) => {
        const v = r.value ?? 0;
        return (
          <li key={r.label} className={v === 0 ? s.zero : undefined}>
            {r.to ? <Link to={r.to} className={s.barLabel}>{r.label}</Link> : <span className={s.barLabel}>{r.label}</span>}
            <span className={s.barTrack}>
              {v > 0 && <span className={s.barFill} style={{ "--f": Math.max(0.015, v / top) } as CSSProperties} />}
              <span className={s.barValue}>
                {r.value === null ? "–" : format(r.value)}{r.note ? <span className={s.barNote}> {r.note}</span> : null}
              </span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export function TableView({ heads, rows }: { heads: string[]; rows: string[][] }) {
  return (
    <details className={s.table}>
      <summary>Show as a table</summary>
      <table>
        <thead><tr>{heads.map((h, i) => <th key={i} scope="col">{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => j === 0 ? <th key={j} scope="row">{c}</th> : <td key={j}>{c}</td>)}</tr>)}</tbody>
      </table>
    </details>
  );
}
