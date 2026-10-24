// Mirrors packages/docengine/model.py and protocol/messages.py (the backend is the source of truth).

export type Run = { text: string; bold?: boolean; italic?: boolean; underline?: boolean };
export type ColumnType = "text" | "number" | "currency" | "percent" | "date";
export type Cell = string | number | null;

export type Block =
  | { type: "heading"; id: string; level: 1 | 2 | 3; text: string }
  | { type: "paragraph"; id: string; runs: Run[] }
  | { type: "list"; id: string; ordered: boolean; items: string[] }
  | { type: "table"; id: string; caption?: string | null; columns: { name: string; type: ColumnType }[];
      rows: Cell[][]; total_row: boolean }
  | { type: "kv"; id: string; pairs: string[][] }
  | { type: "signature"; id: string; closing: string; name: string; designation?: string | null }
  | { type: "page_break"; id: string }
  | { type: "slide"; id: string; title: string; bullets: string[]; notes?: string | null };

export type Doc = {
  id: string;
  title: string;
  lang: string;
  script: string;
  doc_type: string;
  meta: Record<string, string>;
  blocks: Block[];
  version: number;
  assumptions: string[];
};

export type Format = "pdf" | "docx" | "xlsx" | "pptx" | "csv" | "md" | "txt" | "html";

export type Delivery = {
  channel: "sms" | "web" | "email" | "whatsapp";
  recipient_masked: string;
  status: "submitted" | "delivered" | "failed";
  version: number;
  at: string; // ISO time
};

export type VersionInfo = { version: number; at: string; changes: string[] };

export type DocSummary = {
  id: string;
  title: string;
  head_version: number;
  formats: Format[];
  updated_at: string;
  created_at?: string;
  doc_lang?: string;
  delivery?: Delivery;
  /** one line of what it says (its subject, or the first line of the head version); not sent by the server yet */
  hint?: string | null;
};

/** A long document's writer asking the owner something (answered on a call, a callback, or here). */
export type DocQuestion = { question: string; answer?: string | null };

/** A file the owner added for BolDoc to use in a long document (or their next one): only its text is kept. */
export type FileItem = {
  id: string; name: string; kind: string; size: number;
  status: "reading" | "ready" | "failed";
  pages?: number | null;
  read_by?: "text" | "vision" | null;
  error?: string | null;
  doc_id?: string | null;
  added_at: string;
};

/** A long document still being written: how far the writer is, and what it will cover. */
export type Writing = { state: string; stage?: string | null; waiting: boolean; outline: string[]; since: string };

export type DocDetail = { doc: Doc; versions: VersionInfo[]; formats: Format[]; delivery?: Delivery;
  link?: string; expires_at?: string; questions?: DocQuestion[]; writing?: Writing;
  /** written (or being written) by the long-document writer: files can be added for it */
  long?: boolean };

export type Me = {
  id?: number;
  phone_e164: string;
  phone_masked: string;
  status?: "trial" | "active" | "suspended";
  name?: string | null;
  email?: string | null;
  credits?: { balance: number; docs_estimate: number };
  trial?: { calls_left: number };
  /** the number a signed-in account calls (E.164); the public trial number when absent */
  dial_in?: string | null;
  /** the language their last call ended in (phone, "Call me" or the browser): BolDoc starts the next one in it */
  call_lang?: string | null;
};
export type LedgerRow = { id: number; kind: string; amount: number; balance_after: number; reason: string | null;
  doc_title?: string | null; at: string };
export type Credits = { balance: number; docs_estimate: number; prices: { minute: number; document: number; edit: number };
  ledger: LedgerRow[] };
/** Settings → "Your numbers": the sign-in number first, then the ones added to the account. */
export type AccountNumber = { phone_e164: string; phone_masked: string; primary: boolean; added_at?: string };
export type AccountNumbers = { numbers: AccountNumber[]; max_added: number };

export type Profile = {
  name: string | null; designation: string | null; organisation: string | null; address: string | null;
  default_lang: string | null; ui_lang: string | null; retention_days: number | null; email?: string | null;
};
export type VerifyByCall = { call: string; expires_in_s: number; nonce: string };
/** A browser call's Plivo token and the one-time session that says whose call it is. */
export type CallToken = { token: string; destination: string; session: string; max_call_s: number };
export type AppConfig = { product: string; dial_in_e164: string; dial_in_display: string; mode: "live" | "mock" };

// ---------------------------------------------------------------------------------- live call events
export type Speaker = "you" | "agent";
export type AgentState = "listening" | "thinking" | "speaking" | "writing" | "idle";
export type CallState = "idle" | "connecting" | "ringing" | "connected" | "reconnecting" | "ended" | "failed";

export type LiveEvent =
  | { kind: "call"; state: CallState; reason?: string }
  | { kind: "caption"; id: string; speaker: Speaker; text: string; final: boolean; lang?: string }
  | { kind: "agent"; state: AgentState }
  | { kind: "task"; type: "accepted" | "progress" | "question" | "failed" | "cancelled"; task_id: string;
      doc_id?: string; stage?: string; text?: string }
  | { kind: "doc"; doc: Doc; changes: string[]; changed_block_ids: string[] }
  | { kind: "delivery"; delivery: Delivery; doc_id: string };
