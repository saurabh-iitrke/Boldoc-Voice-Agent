import type { AgentState, CallState } from "../lib/types";
import s from "./CallOrb.module.css";
import { DocIcon, MicOffIcon } from "./icons";

export type OrbState = "wait" | "speaking" | "listening" | "muted" | "thinking" | "writing" | "ended";

export function orbState(call: CallState, agent: AgentState, muted: boolean): OrbState {
  if (call === "ended" || call === "failed") return "ended";
  if (call !== "connected") return "wait";
  if (agent === "speaking" || agent === "thinking" || agent === "writing") return agent;
  return muted ? "muted" : "listening";
}

/** BolDoc on the call, as one shape: white bars on orange while it speaks; the caller's own voice (the --level the
 * page's voice meter writes, lib/audioLevel.ts) while it listens; dots while it thinks; a turning ring while it writes. */
export function CallOrb({ state, size = "lg" }: { state: OrbState; size?: "lg" | "sm" }) {
  return (
    <span className={`${s.orb} ${s[size]}`} data-state={state} aria-hidden="true">
      <span className={s.halo} />
      <span className={s.disc}>
        {(state === "speaking" || state === "listening") && (
          <span className={s.bars}>{[0, 1, 2, 3, 4].map((i) => <span key={i} className={s.bar} />)}</span>
        )}
        {state === "muted" && <MicOffIcon size={size === "lg" ? 30 : 20} />}
        {state === "thinking" && (
          <span className={s.dots}>{[0, 1, 2].map((i) => <span key={i} className={s.dotT} />)}</span>
        )}
        {state === "writing" && <DocIcon size={size === "lg" ? 30 : 20} />}
      </span>
    </span>
  );
}
