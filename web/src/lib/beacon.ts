/** Anonymous funnel counters (no cookies, no identifiers): POST /api/t {k}. Never blocks or throws. */
export type FunnelEvent =
  | "visit" | "cta_tel" | "cta_browser" | "cta_qr" | "demo_play" | "link_open" | "download" | "signin_start"
  | "signin_done" | "share_whatsapp" | "start_open" | "cta_callme";

export function track(k: FunnelEvent): void {
  if (import.meta.env.VITE_API_MODE === "mock") return;  // UI work and CI have no server to count on
  try {
    const body = JSON.stringify({ k });
    if (navigator.sendBeacon?.("/api/t", new Blob([body], { type: "application/json" }))) return;
    void fetch("/api/t", { method: "POST", body, headers: { "Content-Type": "application/json" }, keepalive: true })
      .catch(() => undefined);
  } catch {
    /* counting is never worth an error */
  }
}
