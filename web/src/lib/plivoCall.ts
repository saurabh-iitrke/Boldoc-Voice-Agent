import { useCallback, useEffect, useRef, useState } from "react";

import { MIC } from "./mic";
import type { CallState } from "./types";

type PlivoClient = {
  on: (ev: string, cb: (...args: unknown[]) => void) => void;
  loginWithAccessToken: (token: string) => boolean;
  call: (dest: string, headers: Record<string, string>) => boolean;
  hangup: () => boolean;
  mute: () => boolean;
  unmute: () => boolean;
  sendDtmf: (digit: string) => void;
  logout: () => boolean;
  startNoiseReduction?: () => Promise<boolean>;
};

export type CallProblem = "microphone" | "login" | "network" | "rejected" | "unknown";

const LOGIN_TIMEOUT_MS = 15000;

/** The SDK's own log level: "ALL" when localStorage boldoc.debug is "plivo" (diagnosing a browser call). */
function sdkDebug(): string {
  try { return localStorage.getItem("boldoc.debug") === "plivo" ? "ALL" : "ERROR"; } catch { return "ERROR"; }
}

/** Thin wrapper over the Plivo Browser SDK: JWT login (minted by our server), one call, mute, keypad. */
export function usePlivoCall(onState: (s: CallState, reason?: string) => void) {
  const client = useRef<PlivoClient | null>(null);
  const [muted, setMuted] = useState(false);
  const [problem, setProblem] = useState<CallProblem | null>(null);
  const cb = useRef(onState);
  cb.current = onState;

  const start = useCallback(async (token: string, destination: string, headers: Record<string, string>) => {
    setProblem(null);
    cb.current("connecting");
    const { default: Plivo } = await import("plivo-browser-sdk");
    const sdk = new Plivo({ debug: sdkDebug(), permOnClick: true, enableTracking: true, audioConstraints: MIC } as never);
    const c = sdk.client as unknown as PlivoClient;
    client.current = c;
    // Never leave the caller on "Connecting": a login that neither succeeds nor fails in time is a failure.
    let loggedIn = false;
    const loginFailed = (why: string) => {
      if (loggedIn) return;
      window.clearTimeout(timer);
      console.error(`[BolDoc call] login failed: ${why}`);
      setProblem("login");
      cb.current("failed", "login");
    };
    const timer = window.setTimeout(() => loginFailed("no answer from Plivo"), LOGIN_TIMEOUT_MS);
    // one call per start: a second "onLogin" (the SDK logging in again) must not place another call with the
    // same single-use token, which control refuses as unauthenticated
    let placed = false;
    c.on("onLogin", () => {
      loggedIn = true; window.clearTimeout(timer);
      if (placed) { console.warn("[BolDoc call] logged in again; the call is already placed"); return; }
      placed = true;
      c.call(destination, headers);
    });
    c.on("onLoginFailed", (reason) => loginFailed(String(reason ?? "rejected")));
    c.on("onMediaPermission", (evt) => {
      const status = (evt as { status?: string })?.status;
      if (status === "failure") { setProblem("microphone"); cb.current("failed", "microphone"); }
    });
    c.on("onCallRemoteRinging", () => cb.current("ringing"));
    c.on("onCallAnswered", () => {
      cb.current("connected");
      void c.startNoiseReduction?.().catch(() => undefined);
    });
    c.on("onCallTerminated", () => cb.current("ended"));
    c.on("onCallFailed", (reason) => {
      const r = String(reason ?? "");
      console.error(`[BolDoc call] call failed: ${r}`);
      setProblem(/network|timeout/i.test(r) ? "network" : /reject|busy|decline/i.test(r) ? "rejected" : "unknown");
      cb.current("failed", r);
    });
    c.on("onConnectionChange", (info) => {
      const state = (info as { state?: string })?.state ?? "";
      if (/disconnect|reconnect/i.test(state)) cb.current("reconnecting");
    });
    if (!c.loginWithAccessToken(token)) loginFailed("the access token was not accepted");
  }, []);

  const hangup = useCallback(() => {
    client.current?.hangup();
    client.current?.logout();
  }, []);

  const toggleMute = useCallback(() => {
    setMuted((m) => {
      if (m) client.current?.unmute(); else client.current?.mute();
      return !m;
    });
  }, []);

  const dtmf = useCallback((digit: string) => client.current?.sendDtmf(digit), []);

  useEffect(() => () => { client.current?.hangup(); client.current?.logout(); }, []);

  return { start, hangup, toggleMute, dtmf, muted, problem };
}
