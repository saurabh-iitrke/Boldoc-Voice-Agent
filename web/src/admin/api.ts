/** The admin console's API (/admin/api on the admin host). `VITE_API_MODE=mock` serves canned data for UI work. */
import { mockApi } from "./mock";

export type Spread = { p50: number | null; p95: number | null; p99: number | null; n: number };
export type Range = "today" | "7d" | "30d";
export type Kind = "real" | "test" | "all";

export type Overview = {
  range: Range; kind: Kind; since: string; until: string;
  calls: { admitted: number; refused: Record<string, number>; refused_total: number; minutes: number;
    unique_callers: number; by_channel: Record<string, number>; with_trace: number };
  latency: { v2v: Spread; v2v_plain: Spread; v2v_tool: Spread; eot: Spread; ttft: Spread; tts: Spread; hold: Spread;
    ttfw: Spread; slow_share: number | null; turns: number };
  flags: { key: string; label: string; count: number }[];
  documents: Record<"create" | "edit", { completed: number; failed: number; success_rate: number | null;
    time_p50_s: number | null; time_p95_s: number | null }>;
  sms: { submitted: number; delivered: number; failed: number; by_status: Record<string, number> };
  cost: { total_inr: number; by_component: Record<string, number>; per_call_inr: number | null;
    per_min_inr: number | null; per_doc_inr: number | null };
  trend: { bucket: string; calls: number; v2v_p50: number | null; v2v_p95: number | null }[];
  last_call?: CallRow | null;
};

/** How a call ended: BolDoc's side from the trace, else Plivo's hang-up record. */
export type Ending = { how: "refused" | "time_limit" | "idle_goodbye" | "end_call" | "hangup" | "stream_closed";
  label: string; hangup_source: string | null; hangup_cause: string | null };
/** The language picked on the website (when recorded) and every switch the talker made, seconds from the start. */
export type Languages = { start: string | null; switches: { t: number; family: string }[] };

export type CallRow = {
  id: string; at: string; channel: "phone" | "browser" | "test"; caller: string | null; decision: string;
  reason: string | null; duration_s: number | null; plan: string | null; via: string | null; test: boolean;
  turns?: number; v2v_p50_s?: number | null; v2v_p90_s?: number | null; v2v_max_s?: number | null;
  cost_inr?: number; docs?: { create: number; edit: number }; sms?: string[]; flags?: string[];
  checks_failed?: string[]; ended?: Ending; language?: Languages; called?: string | null; first_call?: boolean;
};

export type Turn = {
  turn: number; t?: number; words: number; speech_s: number | null; eot_s: number; reply: string; flags: string[];
  verdict?: string; v2v_s?: number; llm_wait_s?: number; ttft_s?: number; tts_s?: number; hold_s?: number;
  tools?: string[]; deferred?: string[]; silence_s?: number; text?: string; reply_text?: string;
};

export type Summary = Record<string, unknown> & { duration_s: number; caller_turns: number; replied: number;
  no_reply?: number; merged_fragments?: number; v2v_p50_s: number | null; v2v_p90_s: number | null;
  v2v_max_s: number | null; v2v_plain_p50_s?: number | null; eot_p50_s: number | null; ttft_p50_s: number | null;
  tts_p50_s: number | null; hold_p50_s: number | null; ttfw_s?: number | null; dead_air_max_s?: number;
  llm_calls?: number; llm_prompt_tokens?: number; llm_completion_tokens?: number; tts_chars?: number;
  judge_calls?: number; talker_model?: string | null; errors?: Record<string, number> };

export type Report = {
  session_id: string; content: boolean; call_id?: string;
  summary: Summary;
  checks: { check: string; ok: boolean; value: unknown }[];
  turns: Turn[];
  thinker: { task: string; kind: string; tier: string | null; state: string; attempts: number; version: number | null;
    queue_s?: number; work_s?: number; total_s?: number; error?: string;
    research?: { searches: number; pages: number; cited: number; refused: number; inr: number } }[];
  delivery: { purpose: string; channel: string; to: string; version: number; status: string; last_update_s?: number;
    error?: string }[];
  false_claims?: string[];
  cost?: { total_inr: number; inr: Record<string, number>; per_min_inr: number | null; telephony_source: string;
    as_of?: string };
};

/** One event of the talker's trace: `t` seconds from the start of the call, `e` its kind, then its own fields. */
export type TraceEvent = { t: number; e: string; [field: string]: unknown };

export type CallDoc = { id: string; title: string; versions: number; formats: string[]; lang: string;
  this_call: boolean; created_at: string; updated_at: string };

export type CallDetail = { call: CallRow; report: Report | null;
  timeline?: { events: TraceEvent[]; total: number; truncated: boolean }; documents?: CallDoc[] };

export type UserRow = { id: number; phone: string; status: string; plan: string; linked: number; calls: number;
  minutes: number; documents: number; credits: number; created_at: string; verified_at: string | null };

export type AuditEntry = { actor: string; action: string; target: string | null; detail: Record<string, unknown>;
  ip: string | null; at: string };

export type CallFilters = { start?: string; end?: string; kind?: Kind; channel?: string; q?: string;
  min_v2v?: string; has_doc?: boolean; flag?: string };

/** A test call (the e2e gate's scripted call, run from the console): who said what as it happened, then the checks. */
export type TestLine = { t: number; who: "caller" | "bolDoc" | "note" | "problem"; text: string };
export type TestStatus = "starting" | "calling" | "reporting" | "passed" | "failed" | "error";
export type TestRun = {
  id: string; call_id: string; scenario: string; label: string; lang: string; by: string; status: TestStatus;
  started_at: string; ended_at: string | null; elapsed_s: number; step: number; total: number; now: string;
  lines: TestLine[]; summary: { checks: number; failed: number; p50_s: number | null } | null;
  result: { checks: { check: string; ok: boolean; value: unknown }[]; p50_s: number | null; p90_s: number | null;
    plain_p50_s: number | null; max_s: number | null; turns: number | null; duration_s: number | null;
    cost: number | null; ended_by_bolDoc: boolean } | null;
  error: string | null;
};
export type TestRunRow = Pick<TestRun, "id" | "call_id" | "scenario" | "label" | "lang" | "by" | "status"
  | "started_at" | "ended_at" | "elapsed_s" | "summary">;
export type TestCalls = { runs: TestRunRow[]; running: string | null; free_lines: number;
  choices: { id: string; label: string; checks: string }[]; langs: { id: string; label: string }[] };
export const RUNNING: readonly TestStatus[] = ["starting", "calling", "reporting"];

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string,
              public body: Record<string, unknown> = {}) {  // the server's details: a publish's errors by setting
    super(message);
  }
}

/** The agent's settings: the editable whitelist, what's live, and every published version. */
export type AgentSetting = {
  key: string; group: "call" | "timing" | "prices"; label: string; help: string;
  kind: "bool" | "int" | "float" | "choice"; default: AgentValue; min: number | null; max: number | null;
  step: number | null; unit: string; choices: { value: string; label: string }[]; applies: "talker" | "control";
};
export type AgentValue = boolean | number | string;
export type AgentVersion = { version: number; note: string; published_by: string; published_at: string;
  restored_from: number | null; changes: Record<string, [AgentValue, AgentValue]> };
export type AgentView = {
  groups: { id: string; label: string; when: string }[];
  schema: AgentSetting[];
  live: { version: number; settings: Record<string, AgentValue>; note?: string; published_by?: string;
    published_at?: string };
  history: AgentVersion[];
};

export type SignInMethod = "totp" | "sms";
export type SmsSent = { sent_to: string; resend_after_s: number };

export interface AdminApi {
  me(): Promise<{ username: string } | null>;
  /** `method`: the authenticator app's code (totp) or the code sent by SMS to the admin's own mobile (sms). */
  login(username: string, password: string, code: string, method?: SignInMethod): Promise<{ username: string }>;
  /** The right username and password send a code by SMS to the admin's own mobile. */
  sendSmsCode(username: string, password: string): Promise<SmsSent>;
  logout(): Promise<void>;
  overview(range: Range, kind: Kind): Promise<Overview>;
  calls(filters: CallFilters): Promise<{ calls: CallRow[]; flags: string[] }>;
  call(id: string): Promise<CallDetail>;
  /** The newest answered call, in full (null when there are none yet). */
  latest(kind: Kind): Promise<CallDetail | null>;
  revealCaller(id: string): Promise<{ number: string }>;
  users(q: string): Promise<{ users: UserRow[] }>;
  reveal(id: number): Promise<{ numbers: string[] }>;
  audit(): Promise<{ entries: AuditEntry[] }>;
  /** Recent test calls, the one running, the free lines and what a run can be. */
  testCalls(): Promise<TestCalls>;
  /** Starts a test call (409 test_call_running, 503 no_free_line when it can't). */
  startTestCall(scenario: string, lang: string): Promise<{ run: TestRun }>;
  testCall(id: string): Promise<{ run: TestRun }>;
  agent(): Promise<AgentView>;
  /** A new version: the live one plus these changes (400 invalid_settings, 409 published_meanwhile). */
  publishAgent(baseVersion: number, changes: Record<string, AgentValue>, note: string): Promise<AgentView>;
  /** An earlier version's settings, published again as a new version. */
  restoreAgent(version: number, note: string): Promise<AgentView>;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  headers.set("X-BolDoc-Admin", "1");  // the server's CSRF check: a cross-site form can't set it
  const res = await fetch(`/admin/api${path}`, { credentials: "same-origin", ...init, headers });
  const body = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== "/me" && path !== "/login") window.dispatchEvent(new Event("admin:signed-out"));
  if (!res.ok) throw new ApiError(res.status, body.message ?? `Request failed (${res.status})`, body.code, body);
  return body as T;
}

const query = (params: Record<string, string | boolean | undefined>) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "" && v !== false) q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
};

const liveApi: AdminApi = {
  me: async () => {
    try {
      return await request<{ username: string }>("/me");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return null;
      throw e;
    }
  },
  login: (username, password, code, method = "totp") => request("/login", { method: "POST",
    body: JSON.stringify({ username, password, code, method }) }),
  sendSmsCode: (username, password) => request("/login/sms", { method: "POST",
    body: JSON.stringify({ username, password }) }),
  logout: async () => { await request("/logout", { method: "POST" }); },
  overview: (range, kind) => request(`/overview${query({ range, kind })}`),
  calls: (f) => request(`/calls${query({ ...f })}`),
  call: (id) => request(`/calls/${encodeURIComponent(id)}`),
  latest: async (kind) => {
    try {
      return await request<CallDetail>(`/calls/latest${query({ kind })}`);
    } catch (e) {
      if (e instanceof ApiError && e.code === "no_calls") return null;
      throw e;
    }
  },
  revealCaller: (id) => request(`/calls/${encodeURIComponent(id)}/reveal`, { method: "POST" }),
  users: (q) => request(`/users${query({ q })}`),
  reveal: (id) => request(`/users/${id}/reveal`, { method: "POST" }),
  audit: () => request("/audit"),
  testCalls: () => request("/test-calls"),
  startTestCall: (scenario, lang) => request("/test-calls", { method: "POST",
    body: JSON.stringify({ scenario, lang: lang === "en-IN" ? null : lang }) }),
  testCall: (id) => request(`/test-calls/${encodeURIComponent(id)}`),
  agent: () => request("/agent"),
  publishAgent: (baseVersion, changes, note) => request("/agent/publish", { method: "POST",
    body: JSON.stringify({ base_version: baseVersion, changes, note }) }),
  restoreAgent: (version, note) => request("/agent/restore", { method: "POST", body: JSON.stringify({ version, note }) }),
};

export const MOCK = import.meta.env.VITE_API_MODE === "mock";
export const api: AdminApi = MOCK ? mockApi : liveApi;
