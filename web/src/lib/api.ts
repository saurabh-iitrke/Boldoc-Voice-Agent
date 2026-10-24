import { demoDetails, demoSummaries } from "./demoData";
import type {
  AccountNumber, AccountNumbers, AppConfig, CallToken, Credits, DocDetail, DocQuestion, DocSummary, FileItem, Me,
  Profile, VerifyByCall,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string,
              public data: Record<string, unknown> = {}) {
    super(message);
  }
}

export interface Api {
  config(): Promise<AppConfig>;
  me(): Promise<Me | null>;
  requestOtp(phoneE164: string): Promise<{ resend_after_s: number }>;
  verifyOtp(phoneE164: string, code: string): Promise<Me>;
  verifyByCall(phoneE164: string): Promise<VerifyByCall>;
  verifyByCallStatus(nonce: string): Promise<{ status: "pending" } | { status: "verified"; me: Me }>;
  signOut(): Promise<void>;
  credits(): Promise<Credits>;
  profile(): Promise<Profile>;
  saveProfile(p: Partial<Profile>): Promise<Profile>;
  numbers(): Promise<AccountNumbers>;
  sendNumberCode(phoneE164: string): Promise<{ resend_after_s: number }>;
  addNumber(phoneE164: string, code: string): Promise<AccountNumbers>;
  removeNumber(phoneE164: string): Promise<AccountNumbers>;
  documents(): Promise<DocSummary[]>;
  document(id: string): Promise<DocDetail>;
  publicDocument(token: string): Promise<DocDetail>;
  /** The first tap on an SMS link: this browser keeps it (the link then opens only here, or for its signed-in owner). */
  openLink(token: string): Promise<DocDetail>;
  /** Stop a document's links and make a new one (the owner, from the console). */
  newLink(docId: string): Promise<{ link: string; stopped: number }>;
  /** "Send to my phone": the link by SMS to the number the account signs in with. */
  sendToPhone(docId: string): Promise<{ status: string; sent_to: string }>;
  /** Answers to a long document's questions, typed instead of said on a call. */
  answerQuestions(docId: string, answers: Record<string, string>): Promise<{ questions: DocQuestion[] }>;
  /** "Call me": BolDoc rings one of the account's numbers; the AI answers when it's picked up, talking in `lang`
   * (a LANGUAGES code; the server's default without it). */
  callMe(phoneE164: string, lang?: string): Promise<{ id: string; calling: string; ring_s: number }>;
  callMeStatus(id: string): Promise<{ status: "calling" | "answered" | "refused" | "failed" | "ended" }>;
  /** A browser call, BolDoc talking in `lang` (as for callMe). */
  browserToken(lang?: string): Promise<CallToken>;
  /** Files for BolDoc: a long document's (docId), or the ones waiting for the owner's next document. */
  files(docId?: string): Promise<FileItem[]>;
  addFile(file: File, docId?: string): Promise<FileItem>;
  removeFile(id: string): Promise<void>;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  headers.set("X-BolDoc-Client", "web");  // the server's CSRF check: a cross-site form can't set it
  const res = await fetch(path, { credentials: "include", ...init, headers });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== "/api/me") window.dispatchEvent(new Event("boldoc:signed-out"));
  if (!res.ok) throw new ApiError(res.status, body.message ?? `Request failed (${res.status})`, body.code, body);
  return body as T;
}

const liveApi: Api = {
  config: () => request("/api/config"),
  me: async () => {
    try {
      return (await request<Me | undefined>("/api/me")) ?? null;  // 204 when signed out
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return null;
      throw e;
    }
  },
  requestOtp: (phone) => request("/api/auth/otp", { method: "POST", body: JSON.stringify({ phone }) }),
  verifyOtp: (phone, code) => request("/api/auth/verify", { method: "POST", body: JSON.stringify({ phone, code }) }),
  verifyByCall: (phone) => request("/api/auth/verify-by-call", { method: "POST", body: JSON.stringify({ phone }) }),
  verifyByCallStatus: (nonce) => request(`/api/auth/verify-by-call/${encodeURIComponent(nonce)}`),
  signOut: () => request("/api/auth/logout", { method: "POST" }),
  credits: () => request("/api/credits"),
  profile: () => request("/api/profile"),
  saveProfile: (p) => request("/api/profile", { method: "PUT", body: JSON.stringify(p) }),
  numbers: () => request("/api/numbers"),
  sendNumberCode: (phone) => request("/api/numbers/code", { method: "POST", body: JSON.stringify({ phone }) }),
  addNumber: (phone, code) => request("/api/numbers", { method: "POST", body: JSON.stringify({ phone, code }) }),
  removeNumber: (phone) => request(`/api/numbers/${encodeURIComponent(phone)}`, { method: "DELETE" }),
  documents: () => request("/api/documents"),
  document: (id) => request(`/api/documents/${encodeURIComponent(id)}`),
  publicDocument: (token) => request(`/api/d/${encodeURIComponent(token)}`),
  openLink: (token) => request(`/api/d/${encodeURIComponent(token)}/open`, { method: "POST" }),
  newLink: (id) => request(`/api/documents/${encodeURIComponent(id)}/link`, { method: "POST" }),
  sendToPhone: (id) => request(`/api/documents/${encodeURIComponent(id)}/send`, { method: "POST" }),
  answerQuestions: (id, answers) => request(`/api/documents/${encodeURIComponent(id)}/answers`,
    { method: "POST", body: JSON.stringify({ answers }) }),
  callMe: (phone, lang) => request("/api/calls/call-me", { method: "POST", body: JSON.stringify({ phone, lang }) }),
  callMeStatus: (id) => request(`/api/calls/call-me/${encodeURIComponent(id)}`),
  browserToken: (lang) => request("/api/calls/browser-token",
    { method: "POST", ...(lang ? { body: JSON.stringify({ lang }) } : {}) }),
  files: (docId) => request(`/api/files${docId ? `?doc_id=${encodeURIComponent(docId)}` : ""}`),
  // the file is the request's body; its name goes in a header (no multipart to build or parse)
  addFile: (file, docId) => request(`/api/files${docId ? `?doc_id=${encodeURIComponent(docId)}` : ""}`, {
    method: "POST", body: file,
    headers: { "Content-Type": file.type || "application/octet-stream", "X-File-Name": encodeURIComponent(file.name) },
  }),
  removeFile: async (id) => { await request(`/api/files/${encodeURIComponent(id)}`, { method: "DELETE" }); },
};

// ------------------------------------------------------------------------------------------- mock
// For UI work without the backend: `VITE_API_MODE=mock npm run dev`. OTP is 246810.
const MOCK_KEY = "boldoc.mock.me";
const MOCK_DIAL_IN = "+918000000000";  // a placeholder for the signed-in line; the real one comes from /api/me
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
// files: two waiting for the owner's next document, one read and one still being read; one for the report being written
let mockFiles: FileItem[] = [
  { id: "mat_1", name: "Cafe equipment quotation.pdf", kind: "pdf", size: 482_133, status: "ready", pages: 2,
    read_by: "text", doc_id: null, added_at: "2026-10-23T16:20:00+05:30" },
  { id: "mat_2", name: "Photo of shop rent agreement.jpg", kind: "image", size: 1_906_221, status: "reading",
    doc_id: null, added_at: "2026-10-23T16:21:00+05:30" },
  { id: "mat_3", name: "Sales April to September.xlsx", kind: "xlsx", size: 18_440, status: "ready", pages: 1,
    read_by: "text", doc_id: "doc_dpr", added_at: "2026-10-23T16:41:00+05:30" },
];
const mockMask = (e164: string) => `+91 ${e164.slice(3, 5)}••• ••${e164.slice(-3)}`;
let mockNumbers: AccountNumber[] | null = null;
let mockCallStarted = 0;
function mockAccountNumbers(): AccountNumbers {
  if (!mockNumbers) {
    let primary = "+919876543210";
    try { primary = (JSON.parse(localStorage.getItem(MOCK_KEY) ?? "null") as Me | null)?.phone_e164 ?? primary; }
    catch { /* private mode */ }
    mockNumbers = [{ phone_e164: primary, phone_masked: mockMask(primary), primary: true }];
  }
  return { numbers: mockNumbers, max_added: 3 };
}
const MOCK_TOKEN: CallToken = { token: "mock", destination: "boldoc", session: "mock-session", max_call_s: 1800 };

const mockApi: Api = {
  config: async () => ({ product: "BolDoc", dial_in_e164: "+918035449833", dial_in_display: "080 3544 9833",
    mode: "mock" }),
  me: async () => {
    try {
      const raw = localStorage.getItem(MOCK_KEY);
      return raw ? { dial_in: MOCK_DIAL_IN, ...(JSON.parse(raw) as Me) } : null;
    } catch {
      return null;
    }
  },
  requestOtp: async (phone) => {
    await wait(700);
    if (!/^\+91[6-9]\d{9}$/.test(phone)) throw new ApiError(422, "Enter a 10-digit Indian mobile number.");
    return { resend_after_s: 30 };
  },
  verifyOtp: async (phone, code) => {
    await wait(600);
    if (code !== "246810") throw new ApiError(400, "That code isn't right. 4 tries left.", "wrong_code");
    const me: Me = { id: 1, phone_e164: phone, phone_masked: `+91 ${phone.slice(3, 5)}••• ••${phone.slice(-3)}`,
      status: "active", name: null, email: null, credits: { balance: 150, docs_estimate: 6 }, dial_in: MOCK_DIAL_IN };
    try { localStorage.setItem(MOCK_KEY, JSON.stringify(me)); } catch { /* private mode: session only */ }
    return me;
  },
  verifyByCall: async () => ({ call: "+918035449833", expires_in_s: 120, nonce: "mock" }),
  verifyByCallStatus: async () => ({ status: "pending" }),
  signOut: async () => { try { localStorage.removeItem(MOCK_KEY); } catch { /* ignore */ } },
  credits: async () => ({ balance: 128, docs_estimate: 5, prices: { minute: 4, document: 10, edit: 2 }, ledger: [
    { id: 3, kind: "call_minutes", amount: -12, balance_after: 128, reason: "3 min call", at: "2026-10-22T09:12:00Z" },
    { id: 2, kind: "doc_create", amount: -10, balance_after: 140, reason: null, at: "2026-10-22T09:11:00Z" },
    { id: 1, kind: "signup_bonus", amount: 2000, balance_after: 2000, reason: "sign-up gift",
      at: "2026-10-22T09:00:00Z" }] }),
  profile: async () => ({ name: "Saurabh Kumar", designation: null, organisation: null, address: null,
    default_lang: null, ui_lang: null, retention_days: 30, email: null }),
  saveProfile: async (p) => { await wait(300); return { name: null, designation: null, organisation: null, address: null,
    default_lang: null, ui_lang: null, retention_days: 30, ...p }; },
  numbers: async () => { await wait(200); return mockAccountNumbers(); },
  sendNumberCode: async (phone) => {
    await wait(600);
    const { numbers, max_added } = mockAccountNumbers();
    if (!/^\+91[6-9]\d{9}$/.test(phone)) throw new ApiError(422, "Enter a 10-digit Indian mobile number.");
    if (numbers[0]?.phone_e164 === phone) throw new ApiError(409, "That's the number you sign in with.", "own_number");
    if (numbers.length - 1 >= max_added) throw new ApiError(409, `You can add up to ${max_added} numbers.`, "too_many");
    return { resend_after_s: 30 };
  },
  addNumber: async (phone, code) => {
    await wait(500);
    if (code !== "246810") throw new ApiError(400, "That code isn't right. 4 tries left.", "wrong_code");
    const cur = mockAccountNumbers();
    if (!cur.numbers.some((n) => n.phone_e164 === phone)) {
      mockNumbers = [...cur.numbers, { phone_e164: phone, phone_masked: mockMask(phone), primary: false,
        added_at: new Date().toISOString() }];
    }
    return mockAccountNumbers();
  },
  removeNumber: async (phone) => {
    await wait(300);
    mockNumbers = mockAccountNumbers().numbers.filter((n) => n.primary || n.phone_e164 !== phone);
    return mockAccountNumbers();
  },
  documents: async () => { await wait(300); return demoSummaries; },
  document: async (id) => {
    await wait(250);
    const d = demoDetails[id];
    if (!d) throw new ApiError(404, "This document doesn't exist or has been deleted.");
    return d;
  },
  publicDocument: async (token) => {
    await wait(250);
    // preview tokens for the two states of a link that isn't open on this browser
    if (token === "unclaimed") throw new ApiError(403, "Open it on this phone or computer.", "link_unclaimed");
    if (token === "locked") throw new ApiError(403, "This link is already open on another phone or computer.",
      "link_locked", { sent_to: "••••• 6655" });
    const d = demoDetails[token] ?? (token === "demo" ? demoDetails.doc_leave : undefined);
    if (!d) throw new ApiError(404, "This link has expired or doesn't exist.");
    return { ...d, link: `https://boldoc.in/d/${token}` };
  },
  openLink: async () => { await wait(400); return { ...demoDetails.doc_leave!, link: "https://boldoc.in/d/unclaimed" }; },
  newLink: async () => { await wait(400); return { link: "https://boldoc.in/d/Qm9sRG9jTmV3TGluazAx", stopped: 1 }; },
  sendToPhone: async () => { await wait(500); return { status: "accepted", sent_to: "••••• 6655" }; },
  answerQuestions: async (id, answers) => {
    await wait(500);
    const asked = demoDetails[id]?.questions ?? [];
    return { questions: asked.map((q) => ({ ...q, answer: q.answer ?? answers[q.question] ?? null })) };
  },
  callMe: async (phone) => {
    await wait(600);
    mockCallStarted = Date.now();
    return { id: "mock-call", calling: mockMask(phone), ring_s: 40 };
  },
  callMeStatus: async () => {  // mock: rings 6 s, then a 20 s call
    const since = Date.now() - mockCallStarted;
    return { status: since > 26000 ? "ended" : since > 6000 ? "answered" : "calling" };
  },
  // A mock call has no Plivo: the call page plays the recorded call in its place (src/pages/Call.tsx), whatever the
  // language asked for. localStorage boldoc.mock.call = "no_credits" shows the refusal instead (screenshots).
  browserToken: async () => {
    await wait(400);
    let refuse = false;
    try { refuse = localStorage.getItem("boldoc.mock.call") === "no_credits"; } catch { /* private mode */ }
    if (refuse) throw new ApiError(402, "Your balance has run out. Add money to call BolDoc again.", "no_credits");
    return MOCK_TOKEN;
  },
  files: async (docId) => { await wait(200); return mockFiles.filter((f) => (f.doc_id ?? undefined) === docId); },
  addFile: async (file, docId) => {
    await wait(500);
    const ext = file.name.toLowerCase().split(".").pop() ?? "";
    if (!["pdf", "docx", "xlsx", "pptx", "csv", "txt", "jpg", "jpeg", "png"].includes(ext)) {
      throw new ApiError(415, "BolDoc reads PDF, Word, Excel, PowerPoint, CSV and text files, and photos (JPG, PNG).",
        "file_type");
    }
    const item: FileItem = { id: `mat_${Date.now()}`, name: file.name, size: file.size, status: "reading",
      kind: ext === "jpg" || ext === "jpeg" || ext === "png" ? "image" : ext, doc_id: docId ?? null,
      added_at: new Date().toISOString() };
    mockFiles.push(item);
    // read a moment later, as the server does in the background
    setTimeout(() => Object.assign(item, { status: "ready", pages: ext === "pdf" ? 3 : null,
      read_by: item.kind === "image" ? "vision" : "text" }), 1800);
    return { ...item };
  },
  removeFile: async (id) => { await wait(250); mockFiles = mockFiles.filter((f) => f.id !== id); },
};

// live: everything from the server. mock: everything canned (UI work, OTP 246810).
export const API_MODE: "live" | "mock" = import.meta.env.VITE_API_MODE === "mock" ? "mock" : "live";

export const api: Api = API_MODE === "mock" ? mockApi : liveApi;

/** Whether a document view is backed by the real server (so downloads exist). */
export function isLiveDocument(): boolean {
  return API_MODE === "live";
}
