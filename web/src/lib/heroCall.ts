/** The landing page's example call: lines and documents from a real BolDoc call. Lines are trimmed where marked with
 * "…". Times are this call's measurements.
 */
import type { Doc } from "./types";

export type HeroLine = { at: number; who: "you" | "boldoc"; text: string; lang?: "hi-IN" };

export const HERO_STATS = { reply_s: 1.2, draft_s: 7.4, change_s: 1.7 };
export const HERO_LENGTH = "1:52";

export const HERO_LINES: HeroLine[] = [
  { at: 0.0, who: "you", lang: "hi-IN",
    text: "मुझे अपने HR manager के लिए एक leave application लिखनी है। 25 से 27 October तक छुट्टी चाहिए।" },
  { at: 2.6, who: "boldoc", text: "Main aapke liye leave application likh deti hoon. Aapka naam kya hai aur kaunsi chutti chahiye?" },
  { at: 5.0, who: "you", lang: "hi-IN",
    text: "मेरी बहन की शादी है जयपुर में। मेरा नाम सौरभ कुमार है, मैं एक्मा टेक्नोलॉजीज़ में सीनियर इंजीनियर हूँ।" },
  { at: 7.8, who: "boldoc", text: "Samajh gayi, Saurabh ji. Main 25 se 27 October ki … leave application likh rahi hoon." },
  { at: 9.6, who: "you", lang: "hi-IN", text: "English में लिखिए। PDF और Word दोनों चाहिए।" },
  { at: 11.0, who: "boldoc", text: "Main abhi likh rahi hoon, lagbhag pandrah second lagenge." },
  { at: 14.0, who: "boldoc", text: "Leave application ready hai. Kya ismein kuch badalna hai?" },
  { at: 16.4, who: "you", lang: "hi-IN", text: "छुट्टी 26 से 27 October तक कर दीजिए।" },
  { at: 18.2, who: "boldoc", text: "Main dates 26 se 27 October kar rahi hoon, bas thodi der mein ho jayega." },
  { at: 20.0, who: "boldoc", text: "Leave Application version 2 update ho gayi hai." },
];
export const HERO_V1_AT = 14.0;   // the document appears with "ready"
export const HERO_V2_AT = 20.0;   // ...and changes with "version 2"
export const HERO_END = 22.5;

const para = (id: string, text: string) => ({ type: "paragraph" as const, id, runs: [{ text }] });
const base = {
  id: "doc_hero", title: "Leave Application", lang: "en-IN", script: "Latn", doc_type: "application", assumptions: [],
};
export const HERO_V1: Doc = {
  ...base, version: 1,
  meta: { date: "22 October 2026", to: "HR Manager, Exma Technologies",
          subject: "Leave Application for 25 to 27 October 2026" },
  blocks: [
    para("b1", "Dear HR Manager,"),
    para("b2", "I am writing to formally request a three-day leave from 25 October to 27 October 2026 to attend my sister's wedding in Jaipur."),
    para("b3", "I will resume my duties on 28 October 2026. I have ensured that all my current tasks are up to date and will hand over any pending work to my team before I leave."),
    { type: "signature", id: "b4", closing: "Yours sincerely,", name: "Saurabh Kumar", designation: "Senior Engineer, Exma Technologies" },
  ],
};
export const HERO_V2: Doc = {
  ...HERO_V1, version: 2,
  meta: { ...HERO_V1.meta, subject: "Leave Application for 26 to 27 October 2026" },
  blocks: [
    HERO_V1.blocks[0]!,
    para("b2", "I am writing to formally request a two-day leave from 26 October to 27 October 2026 to attend my sister's wedding in Jaipur."),
    HERO_V1.blocks[2]!, HERO_V1.blocks[3]!,
  ],
};
