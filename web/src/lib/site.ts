/** Facts the public site states. The phone number is also served by /api/config; this is the fallback. */
export const DIAL_IN_E164 = "+918035449833";
export const DIAL_IN_DISPLAY = "+91 80 3544 9833";
export const LANGUAGES: { code: string; name: string; native: string }[] = [
  { code: "hi-IN", name: "Hindi", native: "हिन्दी" },
  { code: "en-IN", name: "English", native: "English" },
  { code: "bn-IN", name: "Bengali", native: "বাংলা" },
  { code: "mr-IN", name: "Marathi", native: "मराठी" },
  { code: "te-IN", name: "Telugu", native: "తెలుగు" },
  { code: "ta-IN", name: "Tamil", native: "தமிழ்" },
  { code: "gu-IN", name: "Gujarati", native: "ગુજરાતી" },
  { code: "kn-IN", name: "Kannada", native: "ಕನ್ನಡ" },
  { code: "ml-IN", name: "Malayalam", native: "മലയാളം" },
  { code: "od-IN", name: "Odia", native: "ଓଡ଼ିଆ" },
];
/** apps/control/accounts/pricing.py: a number's first calls, no sign-up; signing up adds credits. */
export const TRIAL = { calls: 3, minutes: 5, docsPerCall: 2, signupCredits: 2000 };
export const PRICES = { minute: 4, document: 10, edit: 2, bigDocument: 60 };
/** How many documents the sign-up money pays for, each with a 3-minute call (accounts/pricing.py docs_estimate). */
export const SIGNUP_DOCS = Math.floor(TRIAL.signupCredits / (PRICES.document + 3 * PRICES.minute));

/** Requests as callers say them: what BolDoc is for, at a glance (the landing, the console's "Try saying"). */
// `becomes`: the same, as the console says it (a sentence, no separators)
export const TRY_SAYING: { say: string; what: string; becomes: string; lang: string }[] = [
  { say: "3 din ki leave application, 25 se 27 October, behen ki shaadi hai", what: "Leave letter · PDF and Word",
    becomes: "A leave letter, PDF and Word", lang: "hi-Latn" },
  { say: "₹12,000 ka invoice, GST 18%, Sharma Traders ke naam", what: "GST invoice · PDF and Excel",
    becomes: "A GST invoice, PDF and Excel", lang: "hi-Latn" },
  { say: "Goa trip ka kharcha: hotel 9,000, flight 6,400, khana 3,200", what: "Expense sheet · Excel",
    becomes: "An expense sheet in Excel", lang: "hi-Latn" },
  { say: "सोसाइटी मीटिंग की सूचना, रविवार शाम 6 बजे, क्लब हाउस में", what: "Notice in Hindi · PDF",
    becomes: "A notice in Hindi, PDF", lang: "hi" },
];

/** A big document: scoped on the call, written after it, and sent by SMS; for signed-up callers. Its formats
 * and size are the catalogue's project report (apps/thinker/catalogue.py). */
export const BIG_SAYING = {
  say: "Meri bakery ke liye 10 lakh ke bank loan ki project report chahiye, 3 saal ke projections ke saath",
  becomes: "A 12-page project report in PDF, Word and Excel, written after the call",
  lang: "hi-Latn",
};
