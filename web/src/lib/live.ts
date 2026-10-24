import { useCallback, useEffect, useReducer, useRef } from "react";

import { demoScript } from "./demoData";
import type { AgentState, CallState, Delivery, Doc, LiveEvent, Speaker } from "./types";

export type Caption = { id: string; speaker: Speaker; text: string; final: boolean; lang?: string };
export type VersionEntry = { version: number; changes: string[]; at: number; changedBlockIds: string[] };
/** One document of the call: every version seen of it, and its SMS. */
export type DocTrack = { docs: Record<number, Doc>; latest?: number; versions: VersionEntry[]; deliveries: Delivery[] };
const NO_TRACK: DocTrack = { docs: {}, versions: [], deliveries: [] };

export type LiveState = {
  call: CallState;
  callReason?: string;
  connectedAt?: number;
  endedAt?: number;
  agent: AgentState;
  captions: Caption[];
  // A call can make several documents.
  documents: Record<string, DocTrack>;  // each document of the call, by id
  order: string[];                      // their ids, in the order they were started
  current?: string;                     // the one on screen
  writingNew: boolean;                  // the running task starts a new document (not a new version)
  // the shown document's track, kept in step with `current` (what the page and FairCopy read)
  docs: Record<number, Doc>;
  latest?: number;
  versions: VersionEntry[];
  stage?: string;                  // thinker progress, e.g. "Writing the body"
  question?: string;               // open thinker question being asked
  deliveries: Delivery[];
  userSpeaking: boolean;           // an interim caption from the caller is in progress
};

export const initialLive: LiveState = { call: "idle", agent: "idle", captions: [], documents: {}, order: [],
  writingNew: false, docs: {}, versions: [], deliveries: [], userSpeaking: false };

/** The page shows `current`: its track's fields copied to the top of the state. */
function showing(s: LiveState, current?: string): LiveState {
  const t = (current && s.documents[current]) || NO_TRACK;
  return { ...s, current, docs: t.docs, latest: t.latest, versions: t.versions, deliveries: t.deliveries };
}

export function liveReducer(s: LiveState, ev: LiveEvent | { kind: "reset" } | { kind: "show"; doc_id: string },
  now = Date.now()): LiveState {
  switch (ev.kind) {
    case "reset":
      return initialLive;
    case "show":  // the caller picked another of the call's documents
      return s.documents[ev.doc_id] ? showing(s, ev.doc_id) : s;
    case "call":
      return { ...s, call: ev.state, callReason: ev.reason,
        connectedAt: ev.state === "connected" ? (s.connectedAt ?? now) : s.connectedAt,
        endedAt: ev.state === "ended" || ev.state === "failed" ? now : s.endedAt,
        agent: ev.state === "ended" ? "idle" : s.agent, userSpeaking: false };
    case "caption": {
      const idx = s.captions.findIndex((c) => c.id === ev.id);
      const cap: Caption = { id: ev.id, speaker: ev.speaker, text: ev.text, final: ev.final, lang: ev.lang };
      const captions = idx === -1 ? [...s.captions, cap] : s.captions.map((c, i) => (i === idx ? cap : c));
      return { ...s, captions, userSpeaking: ev.speaker === "you" ? !ev.final : s.userSpeaking };
    }
    case "agent":
      return { ...s, agent: ev.state, stage: ev.state === "writing" ? s.stage : s.stage };
    case "task": {
      if (ev.type === "progress") return { ...s, stage: ev.stage };
      if (ev.type === "question") return { ...s, question: ev.text };
      if (ev.type === "failed" || ev.type === "cancelled") return { ...s, stage: undefined, writingNew: false };
      // accepted: a document of this call being changed, or a new one (shown as its own, still empty)
      const isNew = !!ev.doc_id && !s.documents[ev.doc_id];
      const started = { ...s, stage: "Starting", writingNew: isNew };
      return ev.doc_id ? showing({ ...started, documents: isNew ? { ...s.documents, [ev.doc_id]: NO_TRACK }
        : s.documents, order: isNew ? [...s.order, ev.doc_id] : s.order }, ev.doc_id) : started;
    }
    case "doc": {
      const v = ev.doc.version;
      const t = s.documents[ev.doc.id] ?? NO_TRACK;
      const track: DocTrack = { ...t, docs: { ...t.docs, [v]: ev.doc }, latest: Math.max(t.latest ?? 0, v),
        versions: [...t.versions.filter((x) => x.version !== v),
          { version: v, changes: ev.changes, at: now, changedBlockIds: ev.changed_block_ids }]
          .sort((a, b) => a.version - b.version) };
      return showing({ ...s, documents: { ...s.documents, [ev.doc.id]: track },
        order: s.order.includes(ev.doc.id) ? s.order : [...s.order, ev.doc.id], stage: undefined, writingNew: false,
        agent: s.agent === "writing" ? "speaking" : s.agent }, ev.doc.id);
    }
    case "delivery": {
      const t = s.documents[ev.doc_id] ?? NO_TRACK;
      const rest = t.deliveries.filter((d) => !(d.version === ev.delivery.version && d.channel === ev.delivery.channel));
      const track = { ...t, deliveries: [...rest, ev.delivery].sort((a, b) => a.version - b.version) };
      return showing({ ...s, documents: { ...s.documents, [ev.doc_id]: track } }, s.current ?? ev.doc_id);
    }
  }
}

type Source = { type: "demo"; speed?: number } | { type: "ws"; url: string } | null;

/** Feeds LiveEvents from the demo timeline or the live WebSocket into the reducer. */
export function useLiveSession(source: Source) {
  const [state, dispatch] = useReducer(
    (s: LiveState, ev: LiveEvent | { kind: "reset" } | { kind: "show"; doc_id: string }) => liveReducer(s, ev),
    initialLive);
  const timers = useRef<number[]>([]);
  const [runId, restart] = useReducer((n: number) => n + 1, 0);

  const push = useCallback((ev: LiveEvent | { kind: "show"; doc_id: string }) => dispatch(ev), []);

  useEffect(() => {
    dispatch({ kind: "reset" });
    if (!source) return;
    if (source.type === "demo") {
      const speed = source.speed ?? 1;
      timers.current = demoScript.map(({ at, ev }) => window.setTimeout(() => dispatch(ev), at / speed));
      return () => timers.current.forEach((t) => clearTimeout(t));
    }
    let ws: WebSocket | null = null;
    let closed = false;
    let backoff = 500;
    let lastSeq = 0;      // durable events carry a seq: a reconnect asks only for what it missed
    let notFound = 0;
    const connect = () => {
      const url = lastSeq ? `${source.url}${source.url.includes("?") ? "&" : "?"}after=${lastSeq}` : source.url;
      ws = new WebSocket(url);
      ws.onmessage = (m) => {
        try {
          const ev = JSON.parse(m.data) as LiveEvent & { seq?: number };
          if (typeof ev.seq === "number") lastSeq = Math.max(lastSeq, ev.seq);
          dispatch(ev);
        } catch { /* ignore malformed frame */ }
      };
      ws.onopen = () => { backoff = 500; };
      ws.onclose = (ev) => {
        // 4401: not signed in as this call's caller. 4404: the call wasn't admitted (yet): try a few more times.
        if (closed || ev.code === 4401 || (ev.code === 4404 && ++notFound > 3)) return;
        window.setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 8000);
      };
    };
    connect();
    return () => { closed = true; ws?.close(); };
    // runId restarts the session; source identity is intentionally reduced to its primitive parts
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source?.type, source && "url" in source ? source.url : source?.speed, runId]);

  const stop = useCallback(() => { timers.current.forEach((t) => clearTimeout(t)); timers.current = []; }, []);

  return { state, push, restart, stop };
}
