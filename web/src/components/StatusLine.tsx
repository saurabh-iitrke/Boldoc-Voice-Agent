import { useEffect, useState } from "react";

import { clock } from "../lib/format";
import type { CallState } from "../lib/types";
import s from "./StatusLine.module.css";

/** The call itself, in a word: what BolDoc is doing lives in its own card (components/CallOrb.tsx). */
function headline(call: CallState, muted: boolean): string {
  switch (call) {
    case "idle": case "connecting": return "Connecting";
    case "ringing": return "Ringing";
    case "reconnecting": return "Reconnecting";
    case "ended": return "Call ended";
    case "failed": return "Call didn't connect";
    case "connected": return muted ? "On a call, muted" : "On a call";
  }
}

/** The header's pill: a dot that pulses while the line is open, the state, and the running time. */
export function StatusLine({ call, muted, connectedAt, endedAt, demo }: {
  call: CallState; muted: boolean; connectedAt?: number; endedAt?: number; demo: boolean;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (call !== "connected" && call !== "reconnecting") return;
    const t = window.setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [call]);

  const live = call === "connected" || call === "reconnecting";
  const elapsed = connectedAt ? ((endedAt ?? now) - connectedAt) / 1000 : 0;

  return (
    <div className={s.pill}>
      <span className={`${s.dot} ${live ? s.live : ""} ${call === "reconnecting" ? s.warn : ""}`} aria-hidden="true" />
      {/* only the state is announced: the running time would be read out every second */}
      <span className={s.state} role="status">{headline(call, muted)}</span>
      {connectedAt && <span className={s.time}>{clock(elapsed)}</span>}
      {demo && <span className={s.demo}>Recorded call</span>}
    </div>
  );
}
