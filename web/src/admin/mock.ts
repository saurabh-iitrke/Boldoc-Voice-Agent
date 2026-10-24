/** Canned admin data for UI work and screenshots (`VITE_API_MODE=mock`). Sign-in: any username, any 12+ character
   password, code 246810.
*/
import type { AdminApi, AuditEntry, CallDetail, CallRow, Kind, Overview, Range, Report, TestLine, TestRun, TestRunRow,
  TestStatus, UserRow } from "./api";
import agentSchema from "./agentSchema.mock.json";
import { ApiError, RUNNING, type AgentValue, type AgentVersion, type AgentView } from "./api";
import { GATE_REPORT, GATE_TIMELINE } from "./mockCall";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
let signedIn = (() => { try { return sessionStorage.getItem("boldoc.admin.mock") === "1"; } catch { return false; } })();

function trend(range: Range) {
  if (range === "today") {
    const hours = [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19];
    const calls = [1, 2, 0, 3, 2, 4, 1, 2, 3, 5, 4];
    const p50 = [0.98, 0.95, null, 0.91, 1.04, 0.93, 0.97, 0.9, 0.96, 0.92, 0.94];
    const p95 = [1.9, 1.7, null, 1.6, 2.3, 1.8, 1.7, 1.5, 1.9, 1.7, 1.86];
    return hours.map((h, i) => ({ bucket: `${String(h).padStart(2, "0")}:00`, calls: calls[i]!, v2v_p50: p50[i] ?? null,
      v2v_p95: p95[i] ?? null }));
  }
  const days = range === "7d" ? 7 : 30;
  return Array.from({ length: days }, (_, i) => {
    const d = new Date(Date.UTC(2026, 9, 22 - (days - 1 - i)));
    const wave = Math.sin(i * 1.7) * 0.06 + Math.cos(i * 0.9) * 0.04;
    return { bucket: `${d.getUTCDate()} ${"Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ")[d.getUTCMonth()]}`,
      calls: Math.max(1, Math.round(5 + i * (range === "7d" ? 0.6 : 0.18) + Math.sin(i * 2.3) * 2.5)),
      v2v_p50: Math.round((0.95 + wave) * 100) / 100, v2v_p95: Math.round((1.85 + wave * 4) * 100) / 100 };
  });
}

function overview(range: Range, kind: Kind): Overview {
  const k = range === "today" ? 0.6 : range === "7d" ? 1 : 3.6;
  const n = (x: number) => Math.round(x * k);
  return {
    range, kind, until: "2026-10-22T14:40:00Z",
    since: { today: "2026-10-21T18:30:00Z", "7d": "2026-10-15T18:30:00Z", "30d": "2026-09-22T18:30:00Z" }[range],
    calls: { admitted: n(46), refused: { withheld: n(3), busy: n(2), trial_over: n(2), too_frequent: n(1) },
      refused_total: n(8), minutes: Math.round(118.4 * k * 10) / 10, unique_callers: n(19),
      by_channel: { phone: n(39), browser: n(7) }, with_trace: n(46) },
    latency: {
      v2v: { p50: 0.94, p95: 1.86, p99: 2.91, n: n(412) }, v2v_plain: { p50: 0.91, p95: 1.42, p99: 1.9, n: n(361) },
      v2v_tool: { p50: 1.62, p95: 3.1, p99: 6.1, n: n(51) }, eot: { p50: 0.37, p95: 0.62, p99: 1.25, n: n(498) },
      ttft: { p50: 0.14, p95: 0.27, p99: 0.41, n: n(420) }, tts: { p50: 0.41, p95: 0.6, p99: 0.71, n: n(420) },
      hold: { p50: 0.05, p95: 0.32, p99: 1.6, n: n(420) }, ttfw: { p50: 0.71, p95: 1.12, p99: 1.4, n: n(46) },
      slow_share: 0.08, turns: n(498) },
    flags: [
      { key: "talk_over", label: "talked over the caller", count: n(3) },
      { key: "bot_cut_off", label: "cut off by the caller", count: n(12) },
      { key: "dead_air_turns", label: "dead air", count: 0 },
      { key: "empty_transcripts", label: "empty transcripts", count: n(4) },
      { key: "empty_llm_replies", label: "empty replies", count: 0 },
      { key: "claim_guard", label: "claims stopped", count: n(7) },
      { key: "false_claims", label: "false 'done' claims", count: 0 },
      { key: "repeats_prevented", label: "repeats stopped", count: n(19) },
      { key: "repeats_spoken", label: "repeats heard", count: n(1) },
      { key: "dropped_replies", label: "replies dropped", count: n(38) },
      { key: "echo_drops", label: "echo dropped", count: n(2) },
      { key: "tool_deferrals", label: "tool calls put off", count: n(9) },
    ],
    documents: {
      create: { completed: n(31), failed: n(1), success_rate: 0.969, time_p50_s: 6.9, time_p95_s: 11.8 },
      edit: { completed: n(22), failed: 0, success_rate: 1, time_p50_s: 1.4, time_p95_s: 3.2 },
    },
    sms: { submitted: n(29), delivered: n(27), failed: n(1), by_status: { delivered: n(27), failed: n(1), submitted: n(1) } },
    cost: { total_inr: Math.round(273.6 * k * 100) / 100,
      by_component: { telephony: 61.2 * k, stt: 59.2 * k, tts: 103.5 * k, llm: 44.5 * k, sms: 5.2 * k },
      per_call_inr: 5.83, per_min_inr: 2.27, per_doc_inr: 8.66 },
    trend: trend(range),
    last_call: kind === "test" ? null : CALLS[0],
  };
}

const CALLS: CallRow[] = [
  { id: "9f3c2a71-4be0-4c55-9d2e-7a1f0b6c3d84", at: "2026-10-22T15:12:40Z", channel: "phone",
    caller: "+91 99••• ••655", decision: "admit", reason: null, duration_s: 144, plan: "credits", via: null, test: false,
    turns: 20, v2v_p50_s: 1.285, v2v_p90_s: 2.098, v2v_max_s: 2.228, cost_inr: 7.12, docs: { create: 1, edit: 1 },
    sms: [], flags: [], checks_failed: ["reply latency p90 ≤ 1.8s (primary talker)"],
    ended: { how: "end_call", label: "The caller was done: BolDoc said goodbye", hangup_source: "Caller",
      hangup_cause: "NORMAL_CLEARING" },
    language: { start: null, switches: [{ t: 3.5, family: "hinglish" }] }, called: "+918035449833", first_call: false },
  { id: "acb4f015-c160-4af0-808f-60c21e9cd164", at: "2026-10-22T14:22:13Z", channel: "phone", caller: "+91 94••• ••670",
    decision: "admit", reason: null, duration_s: 134, plan: "credits", via: null, test: false, turns: 15,
    v2v_p50_s: 0.94, v2v_p90_s: 1.38, v2v_max_s: 1.75, cost_inr: 5.76, docs: { create: 1, edit: 0 },
    sms: ["delivered"], flags: ["talked over the caller", "cut off by the caller"],
    checks_failed: ["never talks over the caller"] },
  { id: "491172bf-216a-4769-9168-2ecb27336e6b", at: "2026-10-22T14:03:00Z", channel: "browser",
    caller: "+91 94••• ••670", decision: "admit", reason: null, duration_s: 42, plan: "credits", via: null, test: false,
    turns: 3, v2v_p50_s: 1.88, v2v_p90_s: 1.92, v2v_max_s: 1.92, cost_inr: 1.73, docs: { create: 0, edit: 0 }, sms: [],
    flags: ["cut off by the caller"], checks_failed: ["plain reply latency p50 ≤ 1.2s (primary talker)"] },
  { id: "c8b71801-39ce-48c1-9934-3130fe002395", at: "2026-10-22T12:03:16Z", channel: "phone", caller: "+91 99••• ••655",
    decision: "admit", reason: null, duration_s: 44, plan: "credits", via: null, test: false, turns: 4,
    v2v_p50_s: 1.02, v2v_p90_s: 1.4, v2v_max_s: 1.4, cost_inr: 1.9, docs: { create: 0, edit: 0 }, sms: [], flags: [],
    checks_failed: [] },
  { id: "6a57ef99-238d-4da4-951a-557e69494839", at: "2026-10-22T10:01:06Z", channel: "phone", caller: "+91 99••• ••655",
    decision: "admit", reason: null, duration_s: 168, plan: "credits", via: "call_me", test: false, turns: 18,
    v2v_p50_s: 0.91, v2v_p90_s: 1.62, v2v_max_s: 2.4, cost_inr: 7.12, docs: { create: 1, edit: 2 },
    sms: ["delivered", "delivered"], flags: [], checks_failed: [] },
  { id: "b8f2c1aa-0e7c-4c9b-a1a2-0f1e2d3c4b5a", at: "2026-10-22T09:41:10Z", channel: "phone", caller: null,
    decision: "refuse", reason: "withheld", duration_s: null, plan: "anonymous", via: null, test: false },
  { id: "c3fafcee-acc7-45b8-b463-1c442626ef29", at: "2026-10-22T07:29:07Z", channel: "phone", caller: "+91 99••• ••655",
    decision: "admit", reason: null, duration_s: 49, plan: "credits", via: null, test: false, turns: 6,
    v2v_p50_s: 0.97, v2v_p90_s: 1.21, v2v_max_s: 1.21, cost_inr: 2.05, docs: { create: 0, edit: 0 }, sms: [],
    flags: ["empty transcripts"], checks_failed: [] },
  { id: "b1c85494-a0f9-4569-93f6-3a2b506d7735", at: "2026-10-22T07:23:36Z", channel: "phone", caller: "+91 99••• ••655",
    decision: "admit", reason: null, duration_s: 134, plan: "trial", via: null, test: false, turns: 12,
    v2v_p50_s: 1.08, v2v_p90_s: 2.1, v2v_max_s: 3.4, cost_inr: 5.3, docs: { create: 1, edit: 1 },
    sms: ["delivered", "delivered"], flags: ["dead air"], checks_failed: ["reply latency p90 ≤ 1.8s (primary talker)",
      "no dead air > 3s"] },
  { id: "d41e7a02-5f63-4b2c-8e1d-6a7b8c9d0e1f", at: "2026-10-21T18:12:44Z", channel: "phone", caller: "+91 98••• ••022",
    decision: "refuse", reason: "too_frequent", duration_s: null, plan: "trial", via: null, test: false },
  { id: "279e5391-91e2-4d3f-9adf-9e5899bec34d", at: "2026-10-21T16:00:47Z", channel: "browser",
    caller: "+91 98••• ••022", decision: "admit", reason: null, duration_s: 31, plan: "credits", via: null, test: false,
    turns: 3, v2v_p50_s: 0.88, v2v_p90_s: 0.95, v2v_max_s: 0.95, cost_inr: 1.31, docs: { create: 0, edit: 0 }, sms: [],
    flags: [], checks_failed: [] },
];

const REPORT: Report = {
  session_id: "acb4f015-c160-4af0-808f-60c21e9cd164", content: true,
  summary: { duration_s: 129.3, caller_turns: 15, replied: 11, v2v_p50_s: 0.94, v2v_p90_s: 1.38, v2v_max_s: 1.749,
    eot_p50_s: 0.361, ttft_p50_s: 0.135, tts_p50_s: 0.408, hold_p50_s: 0.203, ttfw_s: 0.614, talk_over: 1,
    bot_cut_off: 3, claim_guard: 0, repeats_prevented: 6, dropped_replies: 3 },
  checks: [
    { check: "plain reply latency p50 ≤ 1.2s (primary talker)", ok: true, value: 0.919 },
    { check: "reply latency p90 ≤ 1.8s (primary talker)", ok: true, value: 1.38 },
    { check: "no dead air > 3s", ok: true, value: 0 },
    { check: "never talks over the caller", ok: false, value: 1 },
    { check: "no premature 'done' claims", ok: true, value: 0 },
    { check: "every LLM reply speaks or acts", ok: true, value: 0 },
    { check: "BolDoc never repeats itself", ok: true, value: 0 },
    { check: "thinker tasks succeed", ok: true, value: 0 },
    { check: "SMS not failed", ok: true, value: ["delivered"] },
  ],
  turns: [
    { turn: 1, words: 6, speech_s: 1.9, eot_s: 0.36, reply: "merged", flags: [], verdict: "incomplete",
      text: "Mujhe HR ko letter likhna hai", reply_text: "Achha, HR ko letter. Kis liye hai, chhutti chahiye ya koi complaint?" },
    { turn: 2, words: 6, speech_s: 4.0, eot_s: 0.37, reply: "spoken", flags: [], v2v_s: 0.918, ttft_s: 0.135,
      tts_s: 0.408, hold_s: 0.244, text: "मुझे bank में letter लिखनी है।",
      reply_text: "Achha, HR ko letter. HR ke liye letter kya hai? Aap kya kehna chahte hain?" },
    { turn: 3, words: 2, speech_s: 0.6, eot_s: 0.35, reply: "merged", flags: [], text: "1 minute",
      reply_text: "Theek hai, main wait kar rahi hoon." },
    { turn: 4, words: 1, speech_s: 1.2, eot_s: 1.24, reply: "spoken", flags: ["talked_over_caller", "slow"],
      v2v_s: 1.749, ttft_s: 0.121, tts_s: 0.377, hold_s: 0.04, text: "मुझे",
      reply_text: "Theek hai, main wait kar rahi hoon. Jab aap ready hon, toh bata dijiye." },
    { turn: 5, words: 6, speech_s: 1.8, eot_s: 0.44, reply: "spoken", flags: [], v2v_s: 0.943, ttft_s: 0.141,
      tts_s: 0.351, hold_s: 0.169, text: "LIC agent को letter लिखना है।",
      reply_text: "Achha, LIC agent ko letter. Kya kaam hai usme? Policy surrender karna hai, loan lena hai, ya koi claim?" },
    { turn: 9, words: 5, speech_s: 1.8, eot_s: 0.44, reply: "spoken", flags: [], verdict: "wait", v2v_s: 1.047,
      ttft_s: 0.165, tts_s: 0.427, hold_s: 0.164, text: "policy number nahi pata mujhe",
      reply_text: "Ramesh ji, toh: LIC agent ko loan application, aapke naam se. Likh doon?" },
    { turn: 10, words: 4, speech_s: 0.6, eot_s: 0.34, reply: "spoken", flags: [], v2v_s: 1.38, ttft_s: 0.145,
      tts_s: 0.382, hold_s: 0.041, tools: ["create_doc:accepted(32ms)"], text: "is the name sir",
      reply_text: "Main abhi likh rahi hoon. Lagbhag pandrah second mein ho jayega." },
  ],
  thinker: [{ task: "a91f3c", kind: "create", tier: "T2", state: "completed", attempts: 1, version: 1, queue_s: 0.15,
    work_s: 5.75, total_s: 5.9 }],
  delivery: [{ purpose: "initial", channel: "sms", to: "…5670", version: 1, status: "delivered", last_update_s: 5.1 }],
  cost: { total_inr: 5.94, inr: { telephony: 1.32, stt: 1.117, tts: 2.57, llm: 0.754, sms: 0.18 }, per_min_inr: 2.66,
    telephony_source: "cdr" },
};

const USERS: UserRow[] = [
  { id: 7, phone: "+91 94••• ••670", status: "active", plan: "credits", linked: 1, calls: 9, minutes: 21.4,
    documents: 6, credits: 2150, created_at: "2026-10-22T05:10:00Z", verified_at: "2026-10-22T05:12:00Z" },
  { id: 4, phone: "+91 99••• ••655", status: "active", plan: "credits", linked: 0, calls: 31, minutes: 74.9,
    documents: 18, credits: 1880, created_at: "2026-10-21T09:00:00Z", verified_at: "2026-10-21T09:04:00Z" },
  { id: 12, phone: "+91 98••• ••022", status: "trial", plan: "trial", linked: 0, calls: 2, minutes: 3.1, documents: 1,
    credits: 0, created_at: "2026-10-21T16:00:00Z", verified_at: null },
  { id: 13, phone: "+91 70••• ••118", status: "trial", plan: "trial", linked: 0, calls: 1, minutes: 0.4, documents: 0,
    credits: 0, created_at: "2026-10-22T11:30:00Z", verified_at: null },
];

const AUDIT: AuditEntry[] = [
  { actor: "saurabh", action: "view_transcript", target: "acb4f015-c160-4af0-808f-60c21e9cd164", detail: {},
    ip: "49.36.12.8", at: "2026-10-22T14:40:12Z" },
  { actor: "saurabh", action: "reveal_number", target: "user:7", detail: {}, ip: "49.36.12.8",
    at: "2026-10-22T14:38:01Z" },
  { actor: "saurabh", action: "login", target: null, detail: {}, ip: "49.36.12.8", at: "2026-10-22T14:35:44Z" },
  { actor: "saurabh", action: "login_failed", target: null, detail: { why: "code" }, ip: "49.36.12.8",
    at: "2026-10-22T14:35:20Z" },
  { actor: "cli", action: "create_admin", target: "saurabh", detail: {}, ip: null, at: "2026-10-22T14:30:02Z" },
];

/** The newest call is a real gate call with its whole timeline; the others carry the shorter canned report. */
function detail(id: string): CallDetail {
  const call = CALLS.find((c) => c.id === id) ?? CALLS[1]!;
  if (call.decision !== "admit") return { call, report: null };
  if (call.id === CALLS[0]!.id) {
    return { call, report: { ...GATE_REPORT, session_id: call.id },
      timeline: { events: GATE_TIMELINE, total: GATE_TIMELINE.length, truncated: false },
      documents: [{ id: "doc_7f21", title: "Leave Application", versions: 2, formats: ["pdf", "docx"], lang: "en-IN",
        this_call: true, created_at: "2026-10-22T15:13:34Z", updated_at: "2026-10-22T15:14:18Z" }] };
  }
  return { call, report: { ...REPORT, session_id: call.id } };
}

// The agent's settings: the real schema (exported from apps/control/agent_config.py), two versions published
const AGENT = agentSchema as unknown as { groups: AgentView["groups"]; schema: AgentView["schema"];
  defaults: Record<string, AgentValue> };
const AGENT_HISTORY: AgentVersion[] = [
  { version: 2, note: "A little more time for callers who think aloud", published_by: "saurabh",
    published_at: "2026-10-24T03:40:00Z", restored_from: null, changes: { unfinished_hold_s: [3.5, 4] } },
  { version: 1, note: "Noise cancellation off: it clipped words on browser calls", published_by: "saurabh",
    published_at: "2026-10-24T01:05:00Z", restored_from: null, changes: { noise_cancellation: [true, false] } },
];
const agentLive = () => {
  const settings = { ...AGENT.defaults };
  for (const v of [...AGENT_HISTORY].reverse()) for (const [k, [, after]] of Object.entries(v.changes)) settings[k] = after;
  const top = AGENT_HISTORY[0];
  return { version: top?.version ?? 0, settings, note: top?.note, published_by: top?.published_by,
    published_at: top?.published_at };
};
const agentView = (): AgentView => ({ groups: AGENT.groups, schema: AGENT.schema, live: agentLive(),
  history: AGENT_HISTORY.map((v) => ({ ...v })) });
function agentPublish(base: number, changes: Record<string, AgentValue>, note: string, restoredFrom: number | null) {
  const live = agentLive();
  if (base !== live.version) {
    throw new ApiError(409, "Someone published a newer version meanwhile. Reload to see it.", "published_meanwhile",
      { live_version: live.version });
  }
  if (!note.trim()) throw new ApiError(400, "Some values aren't allowed.", "invalid_settings",
    { errors: { note: "say what this change is for" } });
  const changed: Record<string, [AgentValue, AgentValue]> = {};
  for (const [k, v] of Object.entries(changes)) if (live.settings[k] !== v) changed[k] = [live.settings[k]!, v];
  AGENT_HISTORY.unshift({ version: live.version + 1, note: note.trim(), published_by: "saurabh",
    published_at: new Date().toISOString(), restored_from: restoredFrom, changes: changed });
  return agentView();
}

export const mockApi: AdminApi = {
  me: async () => { await wait(120); return signedIn ? { username: "saurabh" } : null; },
  login: async (username, password, code) => {
    await wait(500);
    const digits = code.replace(/\D/g, "");
    if (password.length < 12 || (digits !== "" && digits !== "246810")) {  // the code is optional
      throw new ApiError(401, "Wrong username, password or code.", "bad_login");
    }
    signedIn = true;
    try { sessionStorage.setItem("boldoc.admin.mock", "1"); } catch { /* private mode */ }
    return { username: username.trim().toLowerCase() || "saurabh" };
  },
  sendSmsCode: async (_username, password) => {
    await wait(400);
    if (password.length < 12) throw new ApiError(401, "Wrong username or password.", "bad_login");
    return { sent_to: "…6655", resend_after_s: 30 };
  },
  logout: async () => { signedIn = false; try { sessionStorage.removeItem("boldoc.admin.mock"); } catch { /* */ } },
  overview: async (range, kind) => { await wait(250); return overview(range, kind); },
  calls: async (f) => {
    await wait(200);
    const digits = (f.q ?? "").replace(/\D/g, "");
    const rows = CALLS.filter((c) => (!f.channel || c.channel === f.channel)
      && (!digits || (c.caller ?? "").replace(/\D/g, "").includes(digits.slice(-3)))
      && (!f.has_doc || (c.docs && c.docs.create + c.docs.edit > 0))
      && (!f.min_v2v || (c.v2v_max_s ?? 0) >= Number(f.min_v2v))
      && (!f.flag || (c.flags ?? []).includes(f.flag)));
    return { calls: rows, flags: ["talked over the caller", "cut off by the caller", "dead air", "empty transcripts",
      "empty replies", "false 'done' claims", "repeats heard", "replies dropped"] };
  },
  call: async (id) => { await wait(200); return detail(id); },
  latest: async (kind) => { await wait(200); return kind === "test" ? null : detail(CALLS[0]!.id); },
  revealCaller: async (id) => {
    await wait(300);
    // made up, shaped like the call's mask (the hidden digits are zeros)
    const m = /^\+91 (\d{2})••• ••(\d{3})$/.exec(CALLS.find((c) => c.id === id)?.caller ?? "");
    return { number: m ? `+91${m[1]}00000${m[2]}` : "+919000000467" };
  },
  users: async (q) => {
    await wait(150);
    const d = q.replace(/\D/g, "");
    return { users: USERS.filter((u) => !d || u.phone.replace(/\D/g, "").includes(d.slice(-3))) };
  },
  reveal: async (id) => {
    await wait(300);
    // made up, shaped like each user's mask (the hidden digits are zeros)
    const numbers: Record<number, string[]> = { 7: ["+919400000232", "+917000000981"], 4: ["+919000000467"],
      12: ["+919800000022"], 13: ["+917000000118"] };
    return { numbers: numbers[id] ?? [] };
  },
  audit: async () => { await wait(150); return { entries: AUDIT }; },
  testCalls: async () => {
    await wait(150);
    const active = MOCK_RUNS.find((r) => RUNNING.includes(runAt(r).status));
    return { runs: MOCK_RUNS.map((r) => row(runAt(r))), running: active?.id ?? null, free_lines: active ? 1 : 2,
      choices: CHOICES, langs: [{ id: "en-IN", label: "English" }, { id: "hi-IN", label: "Hinglish" }] };
  },
  startTestCall: async (scenario, lang) => {
    await wait(300);
    if (MOCK_RUNS.some((r) => RUNNING.includes(runAt(r).status))) {
      throw new ApiError(409, "A test call is already running.", "test_call_running");
    }
    const choice = CHOICES.find((c) => c.id === scenario) ?? CHOICES[0]!;
    const id = `test-${Math.floor(Date.now() / 1000)}`;
    const run: MockRun = { id, call_id: id, scenario: choice.id, label: choice.label, lang, by: "saurabh",
      started: Date.now(), ...PLAYS[choice.id]! };
    MOCK_RUNS.unshift(run);
    return { run: runAt(run) };
  },
  agent: async () => { await wait(150); return agentView(); },
  publishAgent: async (base, changes, note) => { await wait(300); return agentPublish(base, changes, note, null); },
  restoreAgent: async (version, note) => {
    await wait(300);
    const at = AGENT_HISTORY.find((v) => v.version === version);
    if (!at) throw new ApiError(404, "No such version.", "not_found");
    const settings = { ...AGENT.defaults };
    for (const v of [...AGENT_HISTORY].reverse().filter((h) => h.version <= version)) {
      for (const [k, [, after]] of Object.entries(v.changes)) settings[k] = after;
    }
    return agentPublish(agentLive().version, settings, note, version);
  },
  testCall: async (id) => {
    await wait(120);
    const run = MOCK_RUNS.find((r) => r.id === id);
    if (!run) throw new ApiError(404, "No such test call.", "not_found");
    return { run: runAt(run) };
  },
};

/* Test calls, played out in real time from a real doc gate : the doc test takes 2 min 15 s here as on
      the server (the screenshot script moves the page's clock on instead of waiting).
*/
type ScriptLine = { at: number; step?: number; now?: string; who: TestLine["who"]; text: string };
type MockResult = { checks: { check: string; ok: boolean; value: unknown }[]; p50_s: number; p90_s: number;
  plain_p50_s: number; max_s: number; turns: number; duration_s: number; cost: number; ended_by_bolDoc: boolean };
type MockRun = { id: string; call_id: string; scenario: string; label: string; lang: string; by: string;
  started: number; script: ScriptLine[]; final: MockResult; total: number };

const CHOICES = [
  { id: "doc", label: "Write a letter, then change it",
    checks: "Dictation, the read-back, writing the PDF and Word, one change by voice, the SMS, the goodbye." },
  { id: "interrupt", label: "Talk over BolDoc", checks: "BolDoc stops when the caller cuts in, and answers what was said." },
  { id: "pause", label: "Pause mid-sentence", checks: "BolDoc waits while the caller thinks, then writes an Excel sheet." },
  { id: "sense", label: "Ask for a letter to a cow",
    checks: "BolDoc asks what's really needed before it writes, then writes that." },
];

const say = (at: number, step: number, text: string): ScriptLine => ({ at, step, now: text, who: "caller",
  text: `Caller: ${text}` });
const quiet = (at: number, step: number, seconds: number): ScriptLine => ({ at, step,
  now: `The caller stays quiet for ${seconds} s`, who: "note", text: `The caller stays quiet for ${seconds} s` });
const note = (at: number, text: string): ScriptLine => ({ at, who: "note", text });
const bolDoc = (at: number, step: number | null, text: string): ScriptLine => ({ at, who: "bolDoc",
  text: `${step === null ? "BolDoc" : `BolDoc, step ${step}`}: ${text}` });
/** BolDoc's reply to a step as the runner logs it: how long it took (when the caller timed it), then its words. */
const reply = (at: number, step: number, latency: number | null, text: string): ScriptLine[] => [
  ...(latency === null ? [] : [note(at, `BolDoc answered in ${latency.toFixed(2)} s`)]), bolDoc(at, step, text)];
const opening = (greeted: number, seconds: number): ScriptLine[] => [note(0.2, "The line is open; calling BolDoc"),
  note(0.6, "Connected to the talker"), note(greeted, `BolDoc greeted (${seconds.toFixed(1)} s)`),
  bolDoc(greeted, null, "Hello! I'm Bol Doc, your AI assistant. Just tell me what you need, and I'll write it.")];

const DOC_SCRIPT: ScriptLine[] = [
  ...opening(7.1, 5.2),
  say(7.2, 1, "Namaste. Mujhe apne HR manager ke liye ek leave application likhni hai."),
  ...reply(18.2, 1, 1.11, "ज़रूर। छुट्टी किन तारीखों में चाहिए, और वजह क्या है?"),
  say(18.3, 2, "Pachchees se sattaais October tak chhutti chahiye, meri behen ki shaadi hai Jaipur mein. Mera naam "
    + "Saurabh Kumar hai, main Acme Technologies mein Senior Engineer hoon."),
  ...reply(38.9, 2, 2.02, "समझ गई: पच्चीस से सत्ताईस अक्टूबर, जयपुर में बहन की शादी। सौरभ कुमार, Senior Engineer, Acme "
    + "Technologies. Letter किस भाषा में चाहिए?"),
  say(39.0, 3, "Haan, sab sahi hai. English mein likhiye, PDF aur Word dono chahiye."),
  ...reply(52.4, 3, 1.16, "तो HR manager को leave application, पच्चीस से सत्ताईस अक्टूबर, English में, PDF और Word दोनों। लिख दूँ?"),
  say(52.5, 4, "Haan, likh dijiye."),
  ...reply(60.3, 4, 1.87, "ठीक है, मैं अभी लिख रही हूँ। बस पंद्रह सेकंड लगेंगे।"),
  quiet(60.4, 5, 20),
  ...reply(81.0, 5, null, "सौरभ जी, आपकी Leave Application तैयार है, PDF और Word दोनों; link SMS पर भेज दिया है। कोई बदलाव चाहिए?"),
  say(81.1, 6, "Ek change kar dijiye. Chhutti chhabbees se sattaais October tak kar dijiye."),
  ...reply(91.6, 6, 1.19, "तो पच्चीस की जगह छब्बीस October कर दूँ?"),
  say(91.7, 7, "Haan, kar dijiye."),
  bolDoc(99.9, 7, "माफ़ कीजिए, ठीक से सुनाई नहीं दिया। पच्चीस की जगह छब्बीस October कर दूँ?"),
  note(99.9, "BolDoc asked again"), say(100.0, 7, "हाँ जी, कर दीजिए।"),
  ...reply(108.4, 7, 1.21, "ठीक है, मैं अभी change कर रही हूँ, बस कुछ सेकंड लगेंगे।"),
  quiet(108.5, 8, 12),
  ...reply(121.1, 8, null, "हो गया, अब छुट्टी छब्बीस से सत्ताईस October तक है। और कुछ चाहिए?"),
  say(121.2, 9, "Nahi, bas. Thank you."),
  ...reply(129.6, 9, 0.98, "ठीक है सौरभ जी, सब तैयार है। फिर मिलते हैं, take care."),
  quiet(129.7, 10, 12), note(131.9, "BolDoc ended the call"),
];
const INTERRUPT_SCRIPT: ScriptLine[] = [
  ...opening(6.9, 5.0),
  say(7.0, 1, "Namaste. Mujhe ek letter likhna hai."),
  ...reply(14.6, 1, 0.91, "ज़रूर। Letter किसके लिए है, और किस बारे में?"),
  say(14.7, 2, "Bank manager ke liye."),
  ...reply(24.3, 2, 0.88, "ठीक है, bank manager के लिए। किस बारे में लिखना है?"),
  say(24.4, 3, "Account band karwana hai."),
  ...reply(31.8, 3, 0.95, "समझ गई, account बंद करवाने की request. आपका नाम और bank का नाम बताइए।"),
  say(31.9, 4, "Mera naam Saurabh Kumar hai. Account SBI mein hai."),
  ...reply(42.5, 4, 0.9, "ठीक है सौरभ जी, SBI के branch manager को account बंद करने की application. लिख दूँ?"),
  quiet(42.6, 5, 20),
  ...reply(63.0, 5, null, "सौरभ जी, क्या मैं letter लिख दूँ?"),
  note(63.1, "The script is done"),
];
const PAUSE_SCRIPT: ScriptLine[] = [
  ...opening(6.9, 5.0),
  say(7.0, 1, "Namaste. Mujhe Goa trip ke kharche ki Excel sheet banani hai."),
  ...reply(16.2, 1, 0.93, "ज़रूर। कौन-कौन से खर्चे लिखने हैं?"),
  say(16.3, 2, "Goa trip ke kharche mein flight … atharah hazaar paanch sau rupaye."),
  ...reply(27.9, 2, 1.02, "Flight, अठारह हज़ार पाँच सौ रुपये। और?"),
  say(28.0, 3, "Hotel teen raat ka, chaar hazaar do sau per night. Aur chaar log ja rahe hain."),
  ...reply(41.2, 3, 0.97, "Hotel, तीन रात, चार हज़ार दो सौ per night, चार लोग। और कुछ जोड़ना है?"),
  say(41.3, 4, "Bas itna hi. Excel mein bana dijiye."),
  ...reply(49.0, 4, 0.89, "ठीक है, Excel sheet बना रही हूँ। बस कुछ सेकंड।"),
  quiet(49.1, 5, 25),
  ...reply(75.2, 5, null, "आपकी Goa trip की Excel sheet तैयार है; link SMS पर भेज दिया है। और कुछ चाहिए?"),
  note(75.3, "The script is done"),
];
const REPORTING_S = 3.4;
const check = (name: string, value: unknown, ok = true) => ({ check: name, ok, value });
const DOC_RESULT: MockResult = {
  checks: [check("plain reply latency p50 ≤ 1.2s (primary talker)", 0.821),
    check("reply latency p90 ≤ 1.8s (primary talker)", 1.958, false), check("no dead air > 3s", 0),
    check("never talks over the caller", 0), check("no premature 'done' claims", 0),
    check("every LLM reply speaks or acts", 0), check("BolDoc never repeats itself", 0), check("thinker tasks succeed", 0),
    check("SMS not failed", []), check("a document was written", 1), check("the document was changed", 1)],
  p50_s: 1.19, p90_s: 1.96, plain_p50_s: 0.82, max_s: 2.02, turns: 10, duration_s: 132.2, cost: 6.42, ended_by_bolDoc: true,
};
const SHORT_RESULT: MockResult = {
  checks: DOC_RESULT.checks.slice(0, 9).map((c) => ({ ...c, ok: true, value: c.check.startsWith("reply latency") ? 0.97 : c.value })),
  p50_s: 0.9, p90_s: 0.97, plain_p50_s: 0.9, max_s: 0.95, turns: 4, duration_s: 63.4, cost: 3.1, ended_by_bolDoc: false,
};
const PLAYS: Record<string, Pick<MockRun, "script" | "final" | "total">> = {
  doc: { script: DOC_SCRIPT, final: DOC_RESULT, total: 10 },
  interrupt: { script: INTERRUPT_SCRIPT, final: SHORT_RESULT, total: 5 },
  pause: { script: PAUSE_SCRIPT, final: { ...SHORT_RESULT, p50_s: 0.95, max_s: 1.02, duration_s: 75.6, cost: 3.6 }, total: 5 },
};

/** A mock run as the API would show it now (from its start time and script). */
function runAt(r: MockRun): TestRun {
  const t = (Date.now() - r.started) / 1000;
  const lines = r.script.filter((l) => l.at <= t);
  const last = r.script[r.script.length - 1]!.at;
  const stepLine = [...lines].reverse().find((l) => l.step !== undefined);
  const done = t >= last + REPORTING_S;
  const failed = r.final.checks.filter((c) => !c.ok);
  const status: TestStatus = t < 0.2 ? "starting" : t < last ? "calling" : !done ? "reporting" : failed.length ? "failed" : "passed";
  const out: TestLine[] = lines.map((l) => ({ t: Math.round(l.at * 10) / 10, who: l.who, text: l.text }));
  if (done) out.push({ t: Math.round((last + REPORTING_S) * 10) / 10, who: failed.length ? "problem" : "note",
    text: `${r.final.checks.length - failed.length} of ${r.final.checks.length} checks passed` });
  return { id: r.id, call_id: r.call_id, scenario: r.scenario, label: r.label, lang: r.lang, by: r.by, status,
    started_at: new Date(r.started).toISOString(), ended_at: done ? new Date(r.started + (last + REPORTING_S) * 1000).toISOString() : null,
    elapsed_s: Math.round(Math.min(t, last + REPORTING_S) * 10) / 10, step: stepLine?.step ?? 0, total: r.total,
    now: done ? `${r.final.checks.length - failed.length} of ${r.final.checks.length} checks passed`
      : status === "reporting" ? "Reading the call's report" : stepLine?.now ?? "Getting the line",
    lines: out, summary: done ? { checks: r.final.checks.length, failed: failed.length, p50_s: r.final.p50_s } : null,
    result: done ? r.final : null, error: null };
}

const row = (r: TestRun): TestRunRow => ({ id: r.id, call_id: r.call_id, scenario: r.scenario, label: r.label,
  lang: r.lang, by: r.by, status: r.status, started_at: r.started_at, ended_at: r.ended_at, elapsed_s: r.elapsed_s,
  summary: r.summary });

const HOUR = 3600_000;
const MOCK_RUNS: MockRun[] = [
  { id: "test-1792689425", call_id: "test-1792689425", scenario: "doc", label: CHOICES[0]!.label, lang: "en-IN",
    by: "saurabh", started: Date.now() - 2 * HOUR, ...PLAYS.doc!,
    final: { ...DOC_RESULT, checks: DOC_RESULT.checks.map((c) => c.check === "the document was changed"
      ? { ...c, ok: false, value: 0 } : c.check.startsWith("reply latency") ? { ...c, ok: true, value: 1.64 } : c),
      p50_s: 1.24, p90_s: 1.64 } },
  { id: "test-1792684991", call_id: "test-1792684991", scenario: "interrupt", label: CHOICES[1]!.label, lang: "en-IN",
    by: "saurabh", started: Date.now() - 5 * HOUR, ...PLAYS.interrupt! },
  { id: "test-1792683764", call_id: "test-1792683764", scenario: "doc", label: CHOICES[0]!.label, lang: "hi-IN",
    by: "saurabh", started: Date.now() - 6 * HOUR, ...PLAYS.doc!,
    final: { ...DOC_RESULT, checks: DOC_RESULT.checks.map((c) => ({ ...c, ok: true,
      value: c.check.startsWith("reply latency") ? 1.43 : c.value })), p50_s: 1.19, p90_s: 1.43 } },
];
