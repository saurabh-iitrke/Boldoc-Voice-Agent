import type { Doc, DocDetail, DocSummary, LiveEvent } from "./types";

const P = (id: string, text: string, bold = false) => ({ type: "paragraph" as const, id, runs: [{ text, bold }] });

export const leaveV1: Doc = {
  id: "doc_leave",
  title: "Leave Application",
  lang: "en-IN",
  script: "Latn",
  doc_type: "application",
  version: 1,
  assumptions: [],
  meta: {
    date: "21 October 2026",
    to: "The HR Manager, Acme Technologies Pvt Ltd, Bengaluru",
    subject: "Leave from 25 to 27 October 2026",
  },
  blocks: [
    P("b_greet", "Respected Sir/Madam,"),
    P("b_body", "I would like to request leave from 25 to 27 October 2026 to attend my sister's wedding in Jaipur. I will be travelling with my family and will return to work on 28 October."),
    { type: "list", id: "b_handover", ordered: false, items: [
      "My ongoing work will be handed over to Priya Sharma by 24 October.",
      "All pending reviews will be completed before I leave.",
    ] },
    P("b_thanks", "Thank you for considering my request."),
    { type: "signature", id: "b_sig", closing: "Yours sincerely,", name: "Saurabh Kumar", designation: "Senior Engineer" },
  ],
};

export const leaveV2: Doc = {
  ...leaveV1,
  version: 2,
  blocks: [
    P("b_greet", "Respected Sir/Madam,"),
    P("b_body", "I respectfully request leave from 25 to 27 October 2026 to attend the wedding of my sister in Jaipur. I shall resume my duties on 28 October 2026."),
    { type: "list", id: "b_handover", ordered: false, items: [
      "My ongoing work will be handed over to Ms Priya Sharma by 24 October.",
      "All pending reviews will be completed before my leave begins.",
    ] },
    P("b_reach", "For any urgent matter, I will remain reachable on my mobile, 99887 76655."),
    P("b_thanks", "I would be grateful if my request is approved."),
    { type: "signature", id: "b_sig", closing: "Yours sincerely,", name: "Saurabh Kumar", designation: "Senior Engineer" },
  ],
};

export const expenses: Doc = {
  id: "doc_trip",
  title: "Jaipur Trip Expenses",
  lang: "en-IN",
  script: "Latn",
  doc_type: "table",
  version: 1,
  assumptions: ["Gift amount noted as ₹6,000 — confirm the final figure."],
  meta: { date: "20 October 2026" },
  blocks: [
    { type: "table", id: "b_tbl", caption: "Expenses", total_row: true,
      columns: [{ name: "Item", type: "text" }, { name: "Paid on", type: "date" }, { name: "Amount", type: "currency" }],
      rows: [["Hotel, 3 nights", "25-10-2026", 12500], ["Train tickets", "24-10-2026", 2340],
        ["Airport cab", "25-10-2026", 1800], ["Wedding gift", "26-10-2026", 6000]] },
    { type: "kv", id: "b_kv", pairs: [["Paid by", "Saurabh"], ["Mode", "UPI"]] },
  ],
};

export const notice: Doc = {
  id: "doc_notice",
  title: "सोसाइटी बैठक की सूचना",
  lang: "hi-IN",
  script: "Deva",
  doc_type: "notes",
  version: 3,
  assumptions: [],
  meta: { date: "19 अक्टूबर 2026" },
  blocks: [
    P("n1", "सभी निवासियों को सूचित किया जाता है कि रविवार, 26 अक्टूबर को शाम 6 बजे क्लब हाउस में मासिक बैठक होगी।"),
    { type: "list", id: "n2", ordered: true, items: ["पानी की टंकी की सफ़ाई", "पार्किंग के नए नियम", "दिवाली की तैयारी"] },
    P("n3", "आपकी उपस्थिति अपेक्षित है।"),
    { type: "signature", id: "n4", closing: "धन्यवाद,", name: "सचिव, ग्रीन पार्क सोसाइटी" },
  ],
};

const sent = (status: "submitted" | "delivered", version: number, at: string) =>
  ({ channel: "sms" as const, recipient_masked: "••6655", status, version, at });

export const demoSummaries: DocSummary[] = [
  // a long document being written: no version yet, its writer waiting on an answer
  { id: "doc_dpr", title: "Project report for a MUDRA loan", head_version: 0, formats: ["pdf", "docx"],
    updated_at: "2026-10-23T16:40:00+05:30", hint: "Kumar Tea and Snacks, Jaipur: from a tea stall to a 20-seat café." },
  { id: "doc_leave", title: leaveV2.title, head_version: 2, formats: ["pdf", "docx"],
    updated_at: "2026-10-21T21:42:00+05:30", delivery: sent("delivered", 2, "2026-10-21T21:42:30+05:30"),
    hint: "To the HR Manager: leave from 25 to 27 October 2026 for my sister's wedding in Jaipur." },
  { id: "doc_trip", title: expenses.title, head_version: 1, formats: ["xlsx", "pdf"],
    updated_at: "2026-10-20T19:05:00+05:30", delivery: sent("delivered", 1, "2026-10-20T19:05:20+05:30"),
    hint: "Hotel, train, cab and the wedding gift for the Jaipur trip: total ₹22,640, paid by UPI." },
  { id: "doc_notice", title: notice.title, head_version: 3, formats: ["pdf"],
    updated_at: "2026-10-19T10:12:00+05:30", delivery: sent("delivered", 3, "2026-10-19T10:12:40+05:30"),
    hint: "रविवार शाम 6 बजे क्लब हाउस में सभी सदस्यों की बैठक: बजट, मरम्मत और त्योहार।" },
];

export const demoDetails: Record<string, DocDetail> = {
  doc_leave: { doc: leaveV2, formats: ["pdf", "docx"], delivery: sent("delivered", 2, "2026-10-21T21:42:30+05:30"),
    expires_at: "2026-10-28T21:42:00+05:30", versions: [
      { version: 1, at: "2026-10-21T21:40:10+05:30", changes: ["First draft"] },
      { version: 2, at: "2026-10-21T21:41:55+05:30", changes: ["Made the tone more formal",
        "Added that you're reachable on your mobile for urgent matters"] }] },
  doc_trip: { doc: expenses, formats: ["xlsx", "pdf"], delivery: sent("delivered", 1, "2026-10-20T19:05:20+05:30"),
    expires_at: "2026-10-27T19:05:00+05:30", versions: [{ version: 1, at: "2026-10-20T19:05:00+05:30",
      changes: ["First draft"] }] },
  doc_dpr: {
    doc: { id: "doc_dpr", title: "Project report for a MUDRA loan", lang: "en-IN", script: "Latn", doc_type: "report",
      meta: {}, blocks: [], version: 0, assumptions: [] },
    versions: [], formats: [], long: true,
    // a writer waiting on answers: one given on a callback, two still open
    questions: [{ question: "Kis bank se loan lena hai?", answer: "Punjab National Bank" },
      { question: "Interior ka total kharcha kitna hai?", answer: null },
      { question: "Abhi mahine ka munafa kitna hai?", answer: null }],
    writing: { state: "running", stage: "section 4 of 9", waiting: true, since: "2026-10-23T16:40:00+05:30",
      outline: ["Executive summary", "Promoter profile", "The business and the project", "Market and demand",
        "Cost of the project and means of finance", "Three-year projections", "Repayment schedule",
        "Risks and how they are handled", "Declaration"] } },
  doc_notice: { doc: notice, formats: ["pdf"], delivery: sent("delivered", 3, "2026-10-19T10:12:40+05:30"),
    expires_at: "2026-10-26T10:12:00+05:30", versions: [
      { version: 1, at: "2026-10-19T10:05:00+05:30", changes: ["First draft"] },
      { version: 2, at: "2026-10-19T10:09:00+05:30", changes: ["Added Diwali preparations to the agenda"] },
      { version: 3, at: "2026-10-19T10:12:00+05:30", changes: ["Changed the time to 6 pm"] }] },
};

/** A real-feeling call, as timed events (ms from start). Same event shapes the live agent emits. */
export const demoScript: { at: number; ev: LiveEvent }[] = (() => {
  const t: { at: number; ev: LiveEvent }[] = [];
  let at = 0;
  const add = (delay: number, ev: LiveEvent) => { at += delay; t.push({ at, ev }); };
  const say = (delay: number, id: string, speaker: "you" | "agent", text: string, lang?: string) => {
    if (speaker === "you") {
      const words = text.split(" ");
      const step = Math.max(1, Math.floor(words.length / 3));
      for (let i = step; i < words.length; i += step) add(delay / 4, { kind: "caption", id, speaker, final: false,
        text: words.slice(0, i).join(" "), lang });
      add(delay / 4, { kind: "caption", id, speaker, final: true, text, lang });
    } else {
      add(delay, { kind: "agent", state: "speaking" });
      add(150, { kind: "caption", id, speaker, final: true, text });
    }
  };
  add(0, { kind: "call", state: "connecting" });
  add(1400, { kind: "call", state: "connected" });
  say(500, "c1", "agent", "Namaste! I'm BolDoc. Tell me what you'd like written: a letter, an application, notes or a table, in any language.");
  add(3800, { kind: "agent", state: "listening" });
  say(3600, "c2", "you", "मुझे अपने मैनेजर के लिए leave application लिखनी है, 25 से 27 October तक, बहन की शादी है जयपुर में।", "hi-IN");
  add(700, { kind: "agent", state: "thinking" });
  say(600, "c3", "agent", "Got it: a leave application for 25 to 27 October, for your sister's wedding in Jaipur. Should I address it to the HR manager at Acme Technologies?");
  add(4200, { kind: "agent", state: "listening" });
  say(2400, "c4", "you", "Haan, HR manager ko. Aur Priya ko handover kar dunga.", "hi-IN");
  add(600, { kind: "task", type: "accepted", task_id: "t1" });
  say(500, "c5", "agent", "Thanks. I'm writing a one-page letter now; it takes about fifteen seconds. PDF, Word, or both?");
  add(300, { kind: "agent", state: "writing" });
  add(600, { kind: "task", type: "progress", task_id: "t1", stage: "Outlining the letter" });
  say(2600, "c6", "you", "Dono bhej do.", "hi-IN");
  say(500, "c7", "agent", "Both, then.");
  add(400, { kind: "agent", state: "writing" });
  add(1800, { kind: "task", type: "progress", task_id: "t1", stage: "Writing the body" });
  add(3200, { kind: "doc", doc: leaveV1, changes: ["First draft"], changed_block_ids: [] });
  say(300, "c8", "agent", "Your leave application is ready. I'm texting the link to the number ending 6655.");
  add(900, { kind: "delivery", doc_id: "doc_leave", delivery: { channel: "sms", recipient_masked: "••6655",
    status: "submitted", version: 1, at: new Date().toISOString() } });
  add(2600, { kind: "delivery", doc_id: "doc_leave", delivery: { channel: "sms", recipient_masked: "••6655",
    status: "delivered", version: 1, at: new Date().toISOString() } });
  say(200, "c9", "agent", "It shows as delivered. Did the message arrive?");
  add(2200, { kind: "agent", state: "listening" });
  say(4200, "c10", "you", "Haan aa gaya. Bas thoda formal kar do, aur likh do ki urgent kaam ke liye phone par available rahunga.", "hi-IN");
  add(500, { kind: "task", type: "accepted", task_id: "t2" });
  say(400, "c11", "agent", "Sure, making it more formal and adding that you're reachable on your phone.");
  add(300, { kind: "agent", state: "writing" });
  add(900, { kind: "task", type: "progress", task_id: "t2", stage: "Updating two paragraphs" });
  add(2400, { kind: "doc", doc: leaveV2, changes: ["Made the tone more formal",
    "Added that you're reachable on your mobile for urgent matters"],
    changed_block_ids: ["b_body", "b_reach", "b_thanks", "b_handover"] });
  say(300, "c12", "agent", "Done. Version 2 is more formal and mentions your phone for urgent matters. The same link now opens version 2.");
  add(4200, { kind: "agent", state: "listening" });
  say(2000, "c13", "you", "Perfect, thank you.");
  say(500, "c14", "agent", "You're welcome. When we hang up I'll send one final message with the latest version. Goodbye!");
  add(3200, { kind: "call", state: "ended" });
  add(800, { kind: "delivery", doc_id: "doc_leave", delivery: { channel: "sms", recipient_masked: "••6655",
    status: "delivered", version: 2, at: new Date().toISOString() } });
  return t;
})();
