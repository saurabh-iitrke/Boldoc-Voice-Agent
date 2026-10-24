import { useCallback, useEffect, useRef, useState } from "react";

import { track } from "../../lib/beacon";
import {
  HERO_END, HERO_LENGTH, HERO_LINES, HERO_STATS, HERO_V1, HERO_V1_AT, HERO_V2, HERO_V2_AT,
} from "../../lib/heroCall";
import type { Block, Doc } from "../../lib/types";
import s from "./CallDemo.module.css";

type Version = 1 | 2;
const CALL_SECONDS = 112;  // the real call's length (HERO_LENGTH), mapped onto the replay

/** Word-level diff of two strings: runs of unchanged, removed and added words. */
function diffWords(a: string, b: string): { kind: "same" | "del" | "ins"; text: string }[] {
  const x = a.split(/(\s+)/), y = b.split(/(\s+)/);
  const m = x.length, n = y.length;
  const lcs: number[][] = Array.from({ length: m + 1 }, () => new Array<number>(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      lcs[i]![j] = x[i] === y[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!);
  const out: { kind: "same" | "del" | "ins"; text: string }[] = [];
  const push = (kind: "same" | "del" | "ins", text: string) => {
    const last = out[out.length - 1];
    if (last && last.kind === kind) last.text += text; else out.push({ kind, text });
  };
  let i = 0, j = 0;
  while (i < m && j < n) {
    if (x[i] === y[j]) { push("same", x[i]!); i++; j++; }
    else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) push("del", x[i++]!);
    else push("ins", y[j++]!);
  }
  while (i < m) push("del", x[i++]!);
  while (j < n) push("ins", y[j++]!);
  return out;
}

function text(block: Block | undefined): string {
  return block && block.type === "paragraph" ? block.runs.map((r) => r.text).join("") : "";
}

function Changed({ before, after, show }: { before: string; after: string; show: boolean }) {
  if (!show || before === after) return <>{after}</>;
  return (
    <>
      {diffWords(before, after).map((p, k) =>
        p.kind === "same" ? <span key={k}>{p.text}</span>
          : p.kind === "del" ? (p.text.trim() ? <del key={k}>{p.text}</del> : null)
            : <ins key={k}>{p.text}</ins>)}
    </>
  );
}

function Page({ doc, prev, writing }: { doc: Doc | null; prev: Doc | null; writing: boolean }) {
  if (!doc) {
    return (
      <div className={s.blank} aria-live="polite">
        <span className={s.nib} aria-hidden="true" />
        {writing ? "Writing the first draft…" : "The document appears here as BolDoc writes it."}
      </div>
    );
  }
  const showDiff = prev !== null;
  return (
    <>
      <p className={s.docTitle}>{doc.title}</p>
      <dl className={s.docMeta}>
        <dt>Date</dt><dd>{doc.meta.date}</dd>
        <dt>To</dt><dd>{doc.meta.to}</dd>
        <dt>Subject</dt>
        <dd className={s.subject}>
          <Changed before={prev?.meta.subject ?? ""} after={doc.meta.subject ?? ""} show={showDiff} />
        </dd>
      </dl>
      <p className={s.docPara}>{text(doc.blocks[0])}</p>
      <p className={s.docPara}>
        <Changed before={text(prev?.blocks[1])} after={text(doc.blocks[1])} show={showDiff} />
      </p>
      <p className={`${s.docPara} ${s.fade}`}>{text(doc.blocks[2])}</p>
    </>
  );
}

export function CallDemo() {
  const [t, setT] = useState<number>(HERO_END);        // playback position; HERO_END = the finished call
  const [playing, setPlaying] = useState(false);
  const [pinned, setPinned] = useState<Version | null>(null);  // a version the visitor picked
  const raf = useRef<number | null>(null);
  const start = useRef(0);
  const listRef = useRef<HTMLOListElement>(null);

  const stop = useCallback(() => {
    if (raf.current !== null) cancelAnimationFrame(raf.current);
    raf.current = null;
    setPlaying(false);
  }, []);

  const play = () => {
    track("demo_play");
    setPinned(null);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setT(HERO_END);
      return;
    }
    stop();
    setT(0);
    setPlaying(true);
    start.current = performance.now();
    const tick = (now: number) => {
      const pos = (now - start.current) / 1000;
      if (pos >= HERO_END) { setT(HERO_END); stop(); return; }
      setT(pos);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
  };
  useEffect(() => stop, [stop]);

  const shown = HERO_LINES.filter((l) => l.at <= t);
  useEffect(() => {  // keep the latest line in view while it plays
    const el = listRef.current;
    if (el && playing) el.scrollTop = el.scrollHeight;
  }, [shown.length, playing]);

  const live: Version | 0 = t >= HERO_V2_AT ? 2 : t >= HERO_V1_AT ? 1 : 0;
  const version = pinned ?? live;
  const doc = version === 2 ? HERO_V2 : version === 1 ? HERO_V1 : null;
  const prev = version === 2 ? HERO_V1 : null;
  const clock = (x: number) => `${Math.floor(x / 60)}:${String(Math.floor(x % 60)).padStart(2, "0")}`;
  const done = !playing && t >= HERO_END;

  return (
    <figure className={s.demo}>
      <div className={s.card}>
        <div className={s.head}>
          <span>A real call: a leave application in Hinglish, then one change by voice</span>
          <span className={s.clock}>{clock(Math.min(t / HERO_END, 1) * CALL_SECONDS)} / {HERO_LENGTH}</span>
        </div>
        <div className={s.panes}>
          <ol ref={listRef} className={s.said} aria-label="What was said on the call" data-playing={playing}>
            {(done ? HERO_LINES.slice(-4) : shown).map((l) => (
              <li key={l.at} className={s.line} data-who={l.who}>
                <span className={s.who}>{l.who === "you" ? <><i aria-hidden="true" />you</> : "boldoc"}</span>
                <span lang={l.lang ?? "en-IN"} className={l.lang ? s.indic : undefined}>
                  {l.text.includes("26 से 27") ? <>छुट्टी <b>26 से 27 October</b> तक कर दीजिए।</> : l.text}
                </span>
              </li>
            ))}
          </ol>
          <article className={s.page} lang="en-IN" aria-label="The document BolDoc wrote">
            <Page doc={doc} prev={prev} writing={playing && t >= 11 && t < HERO_V1_AT} />
            <p className={s.pageFoot}>
              <span>PDF and Word</span>
              <span>{doc ? `Version ${doc.version}, link by SMS` : " "}</span>
            </p>
          </article>
        </div>
        <div className={s.controls}>
          <button type="button" className={s.play} onClick={playing ? stop : play}
                  aria-label={playing ? "Pause the call" : "Play the call"}>
            {playing
              ? <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="5" width="4" height="14" rx="1" /><rect x="13.5" y="5" width="4" height="14" rx="1" /></svg>
              : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z" /></svg>}
          </button>
          <div role="group" aria-label="Which version of the document" className={s.switch}
               data-v={version === 1 ? 1 : 2}>
            {([1, 2] as const).map((v) => (
              <button key={v} type="button" aria-pressed={version === v} disabled={live === 0 && !done}
                      onClick={() => { stop(); setT(HERO_END); setPinned(v); }}>
                {v === 2 && <i aria-hidden="true" />}version {v}
              </button>
            ))}
          </div>
          <output className={s.stats} aria-label="Measured on this call">
            <span>reply <b>{HERO_STATS.reply_s.toFixed(1)}</b> s</span>
            <span>draft <b>{HERO_STATS.draft_s.toFixed(1)}</b> s</span>
            <span>change <b>{HERO_STATS.change_s.toFixed(1)}</b> s</span>
          </output>
        </div>
      </div>
    </figure>
  );
}
