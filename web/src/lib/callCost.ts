import type { LiveState } from "./live";
import { PRICES } from "./site";
import type { Credits, LedgerRow } from "./types";

/** What a browser call costs: how many of each charge, what each line comes to, and the total. */
export type CallCost = { minutes: number; newDocs: number; changes: number;
  minuteAmount: number; docAmount: number; changeAmount: number; total: number };

/** The running estimate, from what this page saw (prices in lib/site.ts, = apps/control/accounts/pricing.py): every
 * started minute, a new document when this call wrote version 1, and each version after it. The account's ledger is the
 * record, and billedCost takes over once the call is settled.
 */
export function callCost(st: Pick<LiveState, "connectedAt" | "endedAt" | "documents">, now = Date.now()): CallCost {
  const minutes = st.connectedAt ? Math.max(1, Math.ceil(((st.endedAt ?? now) - st.connectedAt) / 60000)) : 0;
  const tracks = Object.values(st.documents);  // every document the call made or changed
  const newDocs = tracks.filter((t) => t.versions.some((v) => v.version === 1)).length;
  const changes = tracks.reduce((n, t) => n + t.versions.filter((v) => v.version > 1).length, 0);
  return lines(minutes, newDocs, changes, minutes * PRICES.minute, newDocs * PRICES.document, changes * PRICES.edit);
}

/** What the account was charged for the call: its ledger rows newer than `sinceId`, the newest row before the call
 * began (a user has one call at a time, and the console's own actions are free). Undefined until the minutes are
 * settled, which happens when Plivo reports the call's length, a few seconds after it ends. The page's estimate can
 * be a minute off at a boundary, and counts an undo, which is free. */
export function billedCost(credits: Credits, sinceId: number): CallCost | undefined {
  const rows = credits.ledger.filter((r) => r.id > sinceId);
  const settled = rows.find((r) => r.kind === "call_minutes");   // the ledger comes newest first
  if (!settled) return undefined;
  const docs = rows.filter((r) => r.kind === "doc_create");
  const edits = rows.filter((r) => r.kind === "doc_edit");
  const paid = (rs: LedgerRow[]) => rs.reduce((n, r) => n - r.amount, 0);
  return lines(Math.round(-settled.amount / credits.prices.minute), docs.length, edits.length,
    -settled.amount, paid(docs), paid(edits));
}

function lines(minutes: number, newDocs: number, changes: number, minuteAmount: number, docAmount: number,
  changeAmount: number): CallCost {
  return { minutes, newDocs, changes, minuteAmount, docAmount, changeAmount,
    total: minuteAmount + docAmount + changeAmount };
}
