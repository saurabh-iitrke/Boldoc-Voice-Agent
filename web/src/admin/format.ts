/** How the admin console writes times, money and counts: India time, rupees with Indian grouping. */
const TZ = "Asia/Kolkata";

const WHEN = new Intl.DateTimeFormat("en-IN", { timeZone: TZ, day: "numeric", month: "short", hour: "2-digit",
  minute: "2-digit", hourCycle: "h23" });
const DAY = new Intl.DateTimeFormat("en-IN", { timeZone: TZ, day: "numeric", month: "short", year: "numeric" });
const CLOCK = new Intl.DateTimeFormat("en-IN", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const RUPEES = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2,
  maximumFractionDigits: 2 });
const PCT = new Intl.NumberFormat("en-IN", { style: "percent", maximumFractionDigits: 1 });
const NUM = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });

type Maybe<T> = T | null | undefined;

export const when = (iso: Maybe<string>) => (iso ? WHEN.format(new Date(iso)) : "–");
export const day = (iso: Maybe<string>) => (iso ? DAY.format(new Date(iso)) : "–");
export const clock = (d: Date) => CLOCK.format(d);
export const inr = (v: Maybe<number>) => (v === null || v === undefined ? "–" : RUPEES.format(v));
export const pct = (v: Maybe<number>) => (v === null || v === undefined ? "–" : PCT.format(v));
export const num = (v: Maybe<number>) => (v === null || v === undefined ? "–" : NUM.format(v));

/** "just now", "12 min ago", "3 h ago", "2 days ago". */
export function ago(iso: Maybe<string>, now = Date.now()): string {
  if (!iso) return "–";
  const s = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const d = Math.round(s / 86400);
  return `${d} ${d === 1 ? "day" : "days"} ago`;
}

/** "2:14" in a table, "2 min 14 s" in prose. */
export function mmss(s: Maybe<number>): string {
  if (s === null || s === undefined) return "–";
  const t = Math.round(s);
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
}

export function length(s: Maybe<number>): string {
  if (s === null || s === undefined) return "–";
  const t = Math.round(s);
  return t < 60 ? `${t} s` : `${Math.floor(t / 60)} min ${t % 60} s`;
}

/** The calendar day in India, `offset` days from today, as the API takes it (YYYY-MM-DD). */
export function istDate(offset = 0): string {
  return new Date(Date.now() + 5.5 * 3600e3 + offset * 86400e3).toISOString().slice(0, 10);
}

/** +919876543210 → +91 98765 43210 */
export function phone(e164: string): string {
  const m = /^\+91(\d{5})(\d{5})$/.exec(e164);
  return m ? `+91 ${m[1]} ${m[2]}` : e164;
}

/** Why admission turned a call away (apps/control/calls.py and accounts/users.py), in words. */
export const REFUSED: Record<string, string> = {
  withheld: "Number withheld", busy: "All lines busy", trial_over: "Trial used up", no_credits: "Balance used up",
  too_frequent: "Calling too often", daily_limit: "Daily limit reached", trial_budget: "Day's trial budget spent",
  already_on_call: "Already on a call", suspended: "Account suspended", browser_unauthenticated: "Browser, not signed in",
  time_up: "Call time used up", not_admitted: "Rejoined a refused call", verified: "Number check call",
  call_me_expired: "Call-me link expired", other: "Other",
};

export const refused = (reason: Maybe<string>) => (reason ? REFUSED[reason] ?? reason.replace(/_/g, " ") : "Other");
