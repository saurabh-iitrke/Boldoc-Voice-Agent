/** How the admin console words what a call did: its languages, how it ended, and each event of the talker's trace. */
import type { Ending, Languages, TraceEvent } from "./api";

const LANG: Record<string, string> = {
  "en-IN": "English", hinglish: "Hinglish", "hi-IN": "Hindi", "ta-IN": "Tamil", "te-IN": "Telugu", "kn-IN": "Kannada",
  "ml-IN": "Malayalam", "mr-IN": "Marathi", "bn-IN": "Bengali", "gu-IN": "Gujarati", "pa-IN": "Punjabi",
  "od-IN": "Odia", "or-IN": "Odia",
};
export const langName = (code: unknown) => (typeof code === "string" && code ? LANG[code] ?? code : "–");

/** Seconds from the start of the call: "1:07.4". */
export function at(t: number): string {
  const m = Math.floor(t / 60);
  return `${m}:${(t - m * 60).toFixed(1).padStart(4, "0")}`;
}

/** "Tamil, picked on the website" / "Default, then Hinglish from 0:03.5" (the start is recorded only when picked). */
export function languageLine(l: Languages | undefined): string {
  if (!l) return "–";
  const start = l.start ? langName(l.start) : "Default";
  if (!l.switches.length) return l.start ? `${start}, picked on the website` : "Default, no switch";
  return [start, ...l.switches.map((s) => `${langName(s.family)} from ${at(s.t)}`)].join(", then ");
}

/** Plivo's own word for it, under BolDoc's: "Plivo: Caller, normal clearing". */
export function hangupNote(e: Ending | undefined): string | undefined {
  if (!e || (!e.hangup_source && !e.hangup_cause)) return undefined;
  const cause = e.hangup_cause ? e.hangup_cause.toLowerCase().replace(/_/g, " ") : null;
  return `Plivo: ${[e.hangup_source, cause].filter(Boolean).join(", ")}`;
}

export type Cat = "turns" | "replies" | "guards" | "tools" | "call";
export const CATS: { key: Cat; label: string }[] = [
  { key: "turns", label: "Caller" }, { key: "replies", label: "Replies" }, { key: "guards", label: "Guards and drops" },
  { key: "tools", label: "Tools" }, { key: "call", label: "Call and writer" },
];

export type Described = { cat: Cat; label: string; detail?: string; text?: string; bad?: boolean };

const words = (v: unknown) => (typeof v === "string" ? v.replace(/_/g, " ") : v === undefined || v === null ? "" : String(v));
const ms = (v: unknown) => (typeof v === "number" ? `${Math.round(v)} ms` : "");
const s1 = (v: unknown) => (typeof v === "number" ? `${v.toFixed(1)} s` : "");
const join = (...parts: unknown[]) => parts.filter((p) => p !== "" && p !== undefined && p !== null).join(" · ");
const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);
const TOOL: Record<string, string> = { create_doc: "create_document", edit_doc: "edit_document" };

/** One trace event as a line of the timeline: which group, what happened, the fields that matter, any words. */
export function describe(e: TraceEvent): Described {
  const text = str(e.text);
  switch (e.e) {
    case "turn_start": return { cat: "turns", label: "Caller starts speaking" };
    case "turn_end": return { cat: "turns", label: "Caller's turn",
      detail: join(`${e.words ?? 0} ${e.words === 1 ? "word" : "words"}`, s1(e.speech_s)), text };
    case "turn_verdict": return { cat: "turns", label: "Turn judged", detail: join(words(e.verdict), words(e.source), ms(e.ms)) };
    case "llm_start": return { cat: "replies", label: "Model asked" };
    case "llm_first_token": return { cat: "replies", label: "First word from the model" };
    case "llm_end": return { cat: "replies", label: e.chars ? "Model replied" : "Model replied with no words",
      detail: join(e.chars ? `${e.chars} characters` : "", Array.isArray(e.tools) ? `tools: ${e.tools.join(", ")}` : ""), text };
    case "tts_first_audio": return { cat: "replies", label: "First audio from the voice" };
    case "gate_release": return { cat: "replies", label: "Reply let through", detail: join(words(e.why), ms(e.held_ms) && `held ${ms(e.held_ms)}`) };
    case "bot_start": return { cat: "replies", label: "BolDoc starts speaking" };
    case "bot_stop": return { cat: "replies", label: "BolDoc stops" };
    case "bot_cut": return { cat: "replies", label: "BolDoc cut off by the caller", bad: true };
    case "gate_drop": return { cat: "guards", label: "Reply dropped before it played", detail: words(e.why), bad: true };
    case "unsaid": return { cat: "guards", label: "A sentence never heard, forgotten" };
    case "claim_guard": return { cat: "guards", label: "Claim stopped", detail: words(e.why) || "premature done", text, bad: true };
    case "repeat_drop": return { cat: "guards", label: "Repeat stopped", text, bad: true };
    case "not_speech": return { cat: "guards", label: "Not speech, dropped", text, bad: true };
    case "second_question": return { cat: "guards", label: "Second question held back", text };
    case "echo_drop": return { cat: "guards", label: "BolDoc's own echo dropped", text };
    case "script_fixed": return { cat: "guards", label: "Script fixed", detail: words(e.side) };
    case "said_again": return { cat: "guards", label: "Asked the caller to say it again", bad: true };
    case "tool_call": return { cat: "tools", label: "Tool called", detail: words(e.tool) };
    case "tool": return { cat: "tools", label: `${TOOL[String(e.tool)] ?? words(e.tool)}`,
      detail: join(words(e.status), ms(e.ms)), bad: e.status === "not_done" || e.status === "unavailable" };
    case "tool_deferred": return { cat: "tools", label: "Tool put off (the caller was still speaking)", detail: words(e.tool) };
    case "announce": return { cat: "call", label: "BolDoc announced", detail: words(e.type) };
    case "notice": case "time_limit": return { cat: "call", label: "Notice", detail: words(e.stage) };
    case "event": return { cat: "call", label: "Writer", detail: join(words(e.type), e.version ? `version ${e.version}` : "") };
    case "caller_done_check": return { cat: "call", label: "Is the caller done?",
      detail: join(e.done ? "done" : "wants more", e.job_done ? "work finished" : "work going on", ms(e.ms)) };
    case "lang_switch": return { cat: "call", label: `Language: ${langName(e.family)}` };
    case "voice_switch": return { cat: "call", label: `Voice: ${langName(e.family)}` };
    case "session": return { cat: "call", label: "Session opened",
      detail: join(e.sms ? "SMS number known" : "no SMS number", e.max_call_s ? `up to ${Math.round(Number(e.max_call_s) / 60)} min` : "") };
    case "intake": return { cat: "call", label: "Writer's checklist",
      detail: join(e.allowed === false ? "not allowed" : "", `${e.facts ?? 0} facts`, `${e.missing ?? 0} missing`, ms(e.ms)) };
    case "llm": return { cat: "call", label: "Talker model", detail: join(words(e.model), words(e.role)) };
    case "refused": return { cat: "call", label: "Not admitted", bad: true };
    case "error": return { cat: "call", label: "Error", detail: join(words(e.stage), words(e.processor), e.fatal ? "fatal" : ""), bad: true };
    default: {
      const rest = Object.entries(e).filter(([k]) => k !== "t" && k !== "e" && k !== "text")
        .map(([k, v]) => `${k} ${typeof v === "object" ? JSON.stringify(v) : String(v)}`);
      return { cat: "call", label: words(e.e), detail: rest.join(" · "), text };
    }
  }
}
