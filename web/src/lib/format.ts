import type { Cell, ColumnType } from "./types";

/** '1234567' -> '12,34,567' (Indian grouping: last 3 digits, then pairs). */
export function groupIndian(digits: string): string {
  if (digits.length <= 3) return digits;
  const head = digits.slice(0, -3);
  const tail = digits.slice(-3);
  return head.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + tail;
}

export function formatIndian(value: number, decimals = 0, currency = false): string {
  const sign = value < 0 ? "-" : "";
  const fixed = Math.abs(value).toFixed(decimals);
  const [int = "0", frac] = fixed.split(".");
  return `${sign}${currency ? "₹" : ""}${groupIndian(int)}${frac ? "." + frac : ""}`;
}

/** Credits as money (1 credit = ₹1): 2082 -> "₹2,082", 123456 -> "₹1,23,456"; signed: "+₹150", "−₹12". */
export function rupees(amount: number, signed = false): string {
  const v = formatIndian(Math.abs(amount), 0, true);
  return amount < 0 ? `−${v}` : signed ? `+${v}` : v;
}

export function parseNumber(v: Cell): number | null {
  if (typeof v === "number") return v;
  if (typeof v !== "string") return null;
  const m = v.trim().match(/^[₹\s]*(-)?([\d,]+(?:\.\d+)?)\s*(lakh|lakhs|lac|crore|crores|cr|k|%)?$/i);
  if (!m) return null;
  const mult: Record<string, number> = { lakh: 1e5, lakhs: 1e5, lac: 1e5, crore: 1e7, crores: 1e7, cr: 1e7, k: 1e3,
    "%": 1 };
  const n = Number((m[2] ?? "").replace(/,/g, "")) * (mult[(m[3] ?? "").toLowerCase()] ?? 1);
  return m[1] ? -n : n;
}

export function formatCell(v: Cell, type: ColumnType): string {
  if (v === null || v === undefined) return "";
  if (type === "number" || type === "currency" || type === "percent") {
    const n = parseNumber(v);
    if (n === null) return String(v);
    if (type === "currency") return formatIndian(n, 2, true);
    if (type === "percent") return `${n}%`;
    return formatIndian(n, Number.isInteger(n) ? 0 : 2);
  }
  return String(v);
}

/** '+919988776655' -> '99887 76655' (how Indians read numbers aloud: 5-5). */
export function displayMobile(e164: string): string {
  const d = e164.replace(/\D/g, "").slice(-10);
  return d.length === 10 ? `${d.slice(0, 5)} ${d.slice(5)}` : e164;
}

export function maskMobile(e164: string): string {
  return `••${e164.replace(/\D/g, "").slice(-4)}`;
}

export function clock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

const dtf = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
export function shortDateTime(iso: string): string {
  return dtf.format(new Date(iso));
}

export function relativeDay(iso: string, now = new Date()): string {
  const d = new Date(iso);
  const days = Math.round((startOfDay(now) - startOfDay(d)) / 86_400_000);
  const time = d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
  if (days === 0) return `Today, ${time}`;
  if (days === 1) return `Yesterday, ${time}`;
  return shortDateTime(iso);
}

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export const FORMAT_LABEL: Record<string, string> = {
  pdf: "PDF", docx: "Word", xlsx: "Excel", pptx: "PowerPoint", csv: "CSV", md: "Markdown", txt: "Text",
  html: "Web page",
};
