/** A real gate call for the admin's mock call page: its report as the API returns it, and a timeline rebuilt from its
   turns (the API sends the trace's own events).
*/
import type { Report, TraceEvent } from "./api";

export const GATE_REPORT: Report = {
 "session_id": "test-1792684826",
 "content": true,
 "summary": {
  "duration_s": 143.8,
  "caller_turns": 20,
  "replied": 6,
  "no_reply": 0,
  "merged_fragments": 13,
  "v2v_p50_s": 1.285,
  "v2v_p90_s": 2.098,
  "v2v_max_s": 2.228,
  "v2v_plain_p50_s": 1.004,
  "talker": "primary",
  "v2v_mean_s": 1.626,
  "held_mid_thought": 1,
  "held_reply_s": [
   2.017
  ],
  "eot_p50_s": 0.369,
  "ttft_p50_s": 0.138,
  "tts_p50_s": 0.522,
  "hold_p50_s": 0.041,
  "talk_over": 0,
  "bot_cut_off": 0,
  "dead_air_turns": 0,
  "dead_air_max_s": 0,
  "empty_transcripts": 0,
  "empty_llm_replies": 0,
  "echo_drops": 0,
  "claim_guard": 0,
  "false_claims": 0,
  "tool_deferrals": 1,
  "dropped_replies": 12,
  "heard_done_after_s": [
   2.57,
   5.2
  ],
  "repeats_spoken": 0,
  "repeats_prevented": 7,
  "llm_calls": 26,
  "llm_by_model": {
   "sarvam-105b": [
    93203,
    927
   ]
  },
  "talker_model": "sarvam:sarvam-105b",
  "llm_prompt_tokens": 93203,
  "llm_completion_tokens": 927,
  "tts_chars": 1638,
  "judge_calls": 4,
  "errors": {},
  "ttfw_s": 1.569
 },
 "checks": [
  {
   "check": "plain reply latency p50 ≤ 1.2s (primary talker)",
   "ok": true,
   "value": 1.004
  },
  {
   "check": "reply latency p90 ≤ 1.8s (primary talker)",
   "ok": false,
   "value": 2.098
  },
  {
   "check": "no dead air > 3s",
   "ok": true,
   "value": 0
  },
  {
   "check": "never talks over the caller",
   "ok": true,
   "value": 0
  },
  {
   "check": "no premature 'done' claims",
   "ok": true,
   "value": 0
  },
  {
   "check": "every LLM reply speaks or acts",
   "ok": true,
   "value": 0
  },
  {
   "check": "BolDoc never repeats itself",
   "ok": true,
   "value": 0
  },
  {
   "check": "thinker tasks succeed",
   "ok": true,
   "value": 0
  },
  {
   "check": "SMS not failed",
   "ok": true,
   "value": []
  }
 ],
 "turns": [
  {
   "turn": 1,
   "t": 12.285,
   "words": 1,
   "speech_s": 0.38,
   "eot_s": 0.363,
   "reply": "merged",
   "flags": [],
   "text": "नमस्ते",
   "reply_text": "Namaste! Bataiye, aaj main kya likh doon? Koi application, letter, ya bill?"
  },
  {
   "turn": 2,
   "t": 14.645,
   "words": 6,
   "speech_s": 2.37,
   "eot_s": 0.451,
   "reply": "merged",
   "flags": [],
   "text": "मुझे अपने HR manager के लिए",
   "reply_text": "Namaste! HR manager ke liye leave application chahiye. Reason kya hai, medical ya koi function?"
  },
  {
   "turn": 3,
   "t": 16.146,
   "words": 5,
   "speech_s": 3.69,
   "eot_s": 0.345,
   "reply": "spoken",
   "flags": [],
   "text": "एक leave application लिखनी है।",
   "v2v_s": 1.004,
   "llm_wait_s": 0.014,
   "ttft_s": 0.138,
   "tts_s": -0.133,
   "hold_s": 0.203,
   "reply_text": "Namaste! HR manager ko leave application ke liye, aap kya likhna chahte hain? Kripya vishay bataiye."
  },
  {
   "turn": 4,
   "t": 23.196,
   "words": 5,
   "speech_s": 1.9,
   "eot_s": 0.347,
   "reply": "merged",
   "flags": [],
   "text": "25 से 27 October तक",
   "verdict": "incomplete",
   "reply_text": "Achha, HR manager ko chhutti application. Toh dates toh aapne bata di hain, 25 se 27 October. Reason kya likhun chhutti ka? Medical emergency ya koi aur kaam?"
  },
  {
   "turn": 5,
   "t": 24.069,
   "words": 2,
   "speech_s": 2.46,
   "eot_s": 0.378,
   "reply": "merged",
   "flags": [],
   "text": "छोटी चाहिए",
   "reply_text": "[unheard] Achha, 25 se 27 October ki chhutti. Chhutti ke kya karan hai? Jaise medical, family function, ya kuch aur?"
  },
  {
   "turn": 6,
   "t": 29.885,
   "words": 19,
   "speech_s": 7.97,
   "eot_s": 0.409,
   "reply": "spoken",
   "flags": [
    "slow"
   ],
   "text": "मेरी बहन की शादी है जयपुर में मेरा नाम सौरभ कुमार है मैं एक्मा टेक्नोलॉजीज़ में सीनियर इंजीनियर हूँ",
   "verdict": "incomplete",
   "v2v_s": 2.017,
   "llm_wait_s": 0.007,
   "ttft_s": 0.135,
   "tts_s": 0.631,
   "hold_s": 1.591,
   "reply_text": "Achha, 25 से 27 October, sister ki shaadi, HR manager ko. Reason toh bata diya, Saurabh ji. Kya aapka naam Saurabh Kumar hi hai jo document mein daalna hai?"
  },
  {
   "turn": 7,
   "t": 44.763,
   "words": 1,
   "speech_s": 0.27,
   "eot_s": 0.346,
   "reply": "merged",
   "flags": [],
   "text": "Hum",
   "reply_text": "Toh: 25 से 27 October ki chhutti, behen ki shaadi, aapke naam se. Likh doon?"
  },
  {
   "turn": 8,
   "t": 45.832,
   "words": 3,
   "speech_s": 0.82,
   "eot_s": 0.375,
   "reply": "merged",
   "flags": [],
   "text": "सब सही है।"
  },
  {
   "turn": 9,
   "t": 46.996,
   "words": 3,
   "speech_s": 1.69,
   "eot_s": 0.389,
   "reply": "merged",
   "flags": [],
   "text": "English में लिखिए",
   "verdict": "incomplete",
   "reply_text": "Toh: 25 se 27 October ki chhutti, behen ki shaadi, HR manager ko, Saurabh Kumar, Exma Technologies. Likh doon?"
  },
  {
   "turn": 10,
   "t": 47.807,
   "words": 1,
   "speech_s": 2.17,
   "eot_s": 0.35,
   "reply": "merged",
   "flags": [],
   "text": "PDF",
   "verdict": "incomplete"
  },
  {
   "turn": 11,
   "t": 48.545,
   "words": 1,
   "speech_s": 2.45,
   "eot_s": 0.346,
   "reply": "merged",
   "flags": [],
   "text": "or",
   "deferred": [
    "create_document"
   ]
  },
  {
   "turn": 12,
   "t": 50.196,
   "words": 4,
   "speech_s": 3.69,
   "eot_s": 0.374,
   "reply": "spoken",
   "flags": [
    "slow"
   ],
   "text": "I need both words.",
   "v2v_s": 2.098,
   "llm_wait_s": 0.816,
   "ttft_s": 0.138,
   "tts_s": 0.768,
   "hold_s": 0.041,
   "tools": [
    "create_doc:confirm_first(0ms)"
   ],
   "reply_text": "Saurabh ji, main abhi likh deti hoon. Yeh application HR Manager ko jaayegi, 25 se 27 October ki chhutti ke liye, behen ki shaadi Jaipur mein. Saurabh Kumar, Exma Technologies, Senior Engineer ke naam se. Likh doon?"
  },
  {
   "turn": 13,
   "t": 61.757,
   "words": 3,
   "speech_s": 0.67,
   "eot_s": 0.702,
   "reply": "spoken",
   "flags": [
    "slow"
   ],
   "text": "All लिख दीजिए",
   "v2v_s": 2.228,
   "llm_wait_s": 0.802,
   "ttft_s": 0.199,
   "tts_s": 0.522,
   "hold_s": 0.04,
   "tools": [
    "create_doc:accepted(36ms)"
   ],
   "reply_text": "Main likh deti hoon, bas 15 second lagenge. Aapko iski PDF aur Word dono mein copy mil jayegi. Kuch aur add karna hai isme? | Leave Application ready hai, Saurabh ji. Kuch aur chahiye? | Saurabh ji, leave application ready hai. Koi aur document chahiye?"
  },
  {
   "turn": 14,
   "t": 99.117,
   "words": 3,
   "speech_s": 0.86,
   "eot_s": 0.369,
   "reply": "merged",
   "flags": [],
   "text": "Exchange कर दीजिए।",
   "reply_text": "Solarb ji, jo aapne abhi leave application banwayi hai, uski PDF aur Word dono formats ready hain. Kya isme koi badlav karna hai, ya koi aur document chahiye?"
  },
  {
   "turn": 15,
   "t": 102.513,
   "words": 8,
   "speech_s": 3.8,
   "eot_s": 0.36,
   "reply": "spoken",
   "flags": [
    "slow"
   ],
   "text": "छुट्टी 26 से 27 October तक कर दीजिए।",
   "v2v_s": 1.871,
   "llm_wait_s": 0.864,
   "ttft_s": 0.144,
   "tts_s": 0.501,
   "hold_s": 0.04,
   "tools": [
    "edit_doc:accepted(58ms)"
   ],
   "reply_text": "Abhi change kar rahi hoon, bas 5 second lagenge. 26 se 27 October kar doon? | Leave Application version 2 ready hai, 26 se 27 October ke saath. Aur kuch chahiye?"
  },
  {
   "turn": 16,
   "t": 118.223,
   "words": 1,
   "speech_s": 0.28,
   "eot_s": 0.358,
   "reply": "merged",
   "flags": [],
   "text": "How",
   "reply_text": "Leave Application ready hai, Saurabh ji. Kuch aur chahiye?"
  },
  {
   "turn": 17,
   "t": 119.262,
   "words": 2,
   "speech_s": 0.85,
   "eot_s": 0.356,
   "reply": "spoken",
   "flags": [],
   "text": "Car दीजिए।",
   "v2v_s": 1.27,
   "llm_wait_s": 0.01,
   "ttft_s": 0.374,
   "tts_s": 0.525,
   "hold_s": 0.204,
   "reply_text": "Saurabh ji, yeh toh Leave Application hi hai. Aapka document ready hai. Kuch aur chahiye? | Saurabh ji, kuch aur chahiye?"
  },
  {
   "turn": 18,
   "t": 139.851,
   "words": 1,
   "speech_s": 0.18,
   "eot_s": 0.341,
   "reply": "merged",
   "flags": [],
   "text": "No",
   "tools": [
    "end_call:ending(0ms)"
   ]
  },
  {
   "turn": 19,
   "t": 140.587,
   "words": 1,
   "speech_s": 0.46,
   "eot_s": 0.417,
   "reply": "merged",
   "flags": [],
   "text": "Bus",
   "reply_text": "[unheard] Namaste, Saurabh ji. Aapki leave application taiyaar hai. Apna khayal rakhiyega."
  },
  {
   "turn": 20,
   "t": 141.543,
   "words": 2,
   "speech_s": 1.05,
   "eot_s": 0.409,
   "reply": "spoken",
   "flags": [],
   "text": "Thank you.",
   "v2v_s": 1.285,
   "llm_wait_s": 0.332,
   "ttft_s": 0.117,
   "tts_s": 0.424,
   "hold_s": 0.041,
   "tools": [
    "end_call:ending(0ms)"
   ],
   "reply_text": "Namaste Saurabh ji, aapki leave application taiyaar hai. Apna khayal rakhiyega."
  }
 ],
 "thinker": [
  {
   "task": "598065",
   "kind": "create",
   "tier": "T2",
   "state": "completed",
   "attempts": 1,
   "version": 1,
   "queue_s": 0.18,
   "work_s": 7.06,
   "total_s": 7.24
  },
  {
   "task": "5daa20",
   "kind": "edit",
   "tier": "T1",
   "state": "completed",
   "attempts": 1,
   "version": 2,
   "queue_s": 0.21,
   "work_s": 1.21,
   "total_s": 1.42
  }
 ],
 "delivery": [],
 "false_claims": [],
 "cost": {
  "inr": {
   "telephony": 1.188,
   "stt": 1.198,
   "tts": 3.276,
   "llm": 1.454
  },
  "total_inr": 7.12,
  "per_min_inr": 2.97,
  "telephony_source": "estimate",
  "as_of": "2026-10-22"
 }
};

export const GATE_TIMELINE: TraceEvent[] = [
 {
  "t": 0.0,
  "e": "session",
  "epoch": 1,
  "sms": true,
  "max_call_s": 1800
 },
 {
  "t": 0.02,
  "e": "llm",
  "model": "sarvam-105b",
  "role": "primary"
 },
 {
  "t": 1.57,
  "e": "bot_start"
 },
 {
  "t": 5.9,
  "e": "bot_stop"
 },
 {
  "t": 11.542,
  "e": "turn_start"
 },
 {
  "t": 11.824,
  "e": "turn_start"
 },
 {
  "t": 11.874,
  "e": "gate_drop",
  "why": "interrupted"
 },
 {
  "t": 11.874,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 12.111,
  "e": "turn_start"
 },
 {
  "t": 12.161,
  "e": "gate_drop",
  "why": "interrupted"
 },
 {
  "t": 12.161,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 12.161,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 12.285,
  "e": "turn_end",
  "words": 1,
  "speech_s": 0.38,
  "text": "नमस्ते"
 },
 {
  "t": 12.289,
  "e": "lang_switch",
  "family": "hinglish"
 },
 {
  "t": 12.295,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 12.305,
  "e": "llm_start"
 },
 {
  "t": 12.585,
  "e": "voice_switch",
  "family": "hinglish"
 },
 {
  "t": 12.695,
  "e": "llm_end",
  "chars": 75,
  "text": "Namaste! Bataiye, aaj main kya likh doon? Koi application, letter, ya bill?"
 },
 {
  "t": 14.645,
  "e": "turn_end",
  "words": 6,
  "speech_s": 2.37,
  "text": "मुझे अपने HR manager के लिए"
 },
 {
  "t": 14.655,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 14.665,
  "e": "llm_start"
 },
 {
  "t": 15.055,
  "e": "llm_end",
  "chars": 95,
  "text": "Namaste! HR manager ke liye leave application chahiye. Reason kya hai, medical ya koi function?"
 },
 {
  "t": 16.146,
  "e": "turn_end",
  "words": 5,
  "speech_s": 3.69,
  "text": "एक leave application लिखनी है।"
 },
 {
  "t": 16.156,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 16.16,
  "e": "llm_start"
 },
 {
  "t": 16.548,
  "e": "llm_end",
  "chars": 100,
  "text": "Namaste! HR manager ko leave application ke liye, aap kya likhna chahte hain? Kripya vishay bataiye."
 },
 {
  "t": 16.785,
  "e": "gate_release",
  "held_ms": 203,
  "why": "silence≥0.60s"
 },
 {
  "t": 16.805,
  "e": "bot_start"
 },
 {
  "t": 20.949,
  "e": "turn_start"
 },
 {
  "t": 21.231,
  "e": "turn_start"
 },
 {
  "t": 21.281,
  "e": "gate_drop",
  "why": "interrupted"
 },
 {
  "t": 21.281,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 21.281,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 21.506,
  "e": "turn_start"
 },
 {
  "t": 21.556,
  "e": "gate_drop",
  "why": "interrupted"
 },
 {
  "t": 21.556,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 21.556,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 23.196,
  "e": "turn_end",
  "words": 5,
  "speech_s": 1.9,
  "text": "25 से 27 October तक"
 },
 {
  "t": 23.206,
  "e": "turn_verdict",
  "verdict": "incomplete",
  "source": "classifier"
 },
 {
  "t": 23.216,
  "e": "llm_start"
 },
 {
  "t": 23.606,
  "e": "llm_end",
  "chars": 158,
  "text": "Achha, HR manager ko chhutti application. Toh dates toh aapne bata di hain, 25 se 27 October. Reason kya likhun chhutti ka? Medical emergency ya koi aur kaam?"
 },
 {
  "t": 23.948,
  "e": "bot_stop"
 },
 {
  "t": 24.069,
  "e": "turn_end",
  "words": 2,
  "speech_s": 2.46,
  "text": "छोटी चाहिए"
 },
 {
  "t": 24.079,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 24.089,
  "e": "llm_start"
 },
 {
  "t": 24.479,
  "e": "llm_end",
  "chars": 106,
  "text": "Achha, 25 se 27 October ki chhutti. Chhutti ke kya karan hai? Jaise medical, family function, ya kuch aur?"
 },
 {
  "t": 29.885,
  "e": "turn_end",
  "words": 19,
  "speech_s": 7.97,
  "text": "मेरी बहन की शादी है जयपुर में मेरा नाम सौरभ कुमार है मैं एक्मा टेक्नोलॉजीज़ में सीनियर इंजीनियर हूँ"
 },
 {
  "t": 29.892,
  "e": "llm_start"
 },
 {
  "t": 29.895,
  "e": "turn_verdict",
  "verdict": "incomplete",
  "source": "classifier"
 },
 {
  "t": 30.277,
  "e": "llm_end",
  "chars": 156,
  "text": "Achha, 25 से 27 October, sister ki shaadi, HR manager ko. Reason toh bata diya, Saurabh ji. Kya aapka naam Saurabh Kumar hi hai jo document mein daalna hai?"
 },
 {
  "t": 31.473,
  "e": "gate_release",
  "held_ms": 1591,
  "why": "silence≥0.60s"
 },
 {
  "t": 31.493,
  "e": "bot_start"
 },
 {
  "t": 42.636,
  "e": "bot_stop"
 },
 {
  "t": 44.147,
  "e": "turn_start"
 },
 {
  "t": 44.637,
  "e": "turn_start"
 },
 {
  "t": 44.687,
  "e": "gate_drop",
  "why": "interrupted"
 },
 {
  "t": 44.687,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 44.687,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 44.763,
  "e": "turn_end",
  "words": 1,
  "speech_s": 0.27,
  "text": "Hum"
 },
 {
  "t": 44.773,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 44.783,
  "e": "llm_start"
 },
 {
  "t": 44.917,
  "e": "turn_start"
 },
 {
  "t": 44.967,
  "e": "gate_drop",
  "why": "interrupted"
 },
 {
  "t": 44.967,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 45.173,
  "e": "llm_end",
  "chars": 76,
  "text": "Toh: 25 से 27 October ki chhutti, behen ki shaadi, aapke naam se. Likh doon?"
 },
 {
  "t": 45.287,
  "e": "turn_start"
 },
 {
  "t": 45.337,
  "e": "gate_drop",
  "why": "interrupted"
 },
 {
  "t": 45.337,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 45.337,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 45.749,
  "e": "turn_start"
 },
 {
  "t": 45.799,
  "e": "gate_drop",
  "why": "interrupted"
 },
 {
  "t": 45.799,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 45.832,
  "e": "turn_end",
  "words": 3,
  "speech_s": 0.82,
  "text": "सब सही है।"
 },
 {
  "t": 45.842,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 45.852,
  "e": "llm_start"
 },
 {
  "t": 46.132,
  "e": "turn_start"
 },
 {
  "t": 46.182,
  "e": "gate_drop",
  "why": "interrupted"
 },
 {
  "t": 46.182,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 46.242,
  "e": "llm_end",
  "chars": 0
 },
 {
  "t": 46.996,
  "e": "turn_end",
  "words": 3,
  "speech_s": 1.69,
  "text": "English में लिखिए"
 },
 {
  "t": 47.006,
  "e": "turn_verdict",
  "verdict": "incomplete",
  "source": "classifier"
 },
 {
  "t": 47.016,
  "e": "llm_start"
 },
 {
  "t": 47.406,
  "e": "llm_end",
  "chars": 110,
  "text": "Toh: 25 se 27 October ki chhutti, behen ki shaadi, HR manager ko, Saurabh Kumar, Exma Technologies. Likh doon?"
 },
 {
  "t": 47.807,
  "e": "turn_end",
  "words": 1,
  "speech_s": 2.17,
  "text": "PDF"
 },
 {
  "t": 47.817,
  "e": "turn_verdict",
  "verdict": "incomplete",
  "source": "classifier"
 },
 {
  "t": 47.827,
  "e": "llm_start"
 },
 {
  "t": 48.217,
  "e": "llm_end",
  "chars": 0
 },
 {
  "t": 48.545,
  "e": "turn_end",
  "words": 1,
  "speech_s": 2.45,
  "text": "or"
 },
 {
  "t": 48.555,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 48.565,
  "e": "llm_start"
 },
 {
  "t": 48.955,
  "e": "llm_end",
  "chars": 0
 },
 {
  "t": 50.196,
  "e": "turn_end",
  "words": 4,
  "speech_s": 3.69,
  "text": "I need both words."
 },
 {
  "t": 50.206,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 51.012,
  "e": "llm_start"
 },
 {
  "t": 51.15,
  "e": "tool_call",
  "tool": "create_document"
 },
 {
  "t": 51.18,
  "e": "tool",
  "tool": "create_doc",
  "status": "confirm_first",
  "ms": 0
 },
 {
  "t": 51.4,
  "e": "llm_end",
  "chars": 215,
  "text": "Saurabh ji, main abhi likh deti hoon. Yeh application HR Manager ko jaayegi, 25 se 27 October ki chhutti ke liye, behen ki shaadi Jaipur mein. Saurabh Kumar, Exma Technologies, Senior Engineer ke naam se. Likh doon?"
 },
 {
  "t": 51.9,
  "e": "gate_release",
  "held_ms": 41,
  "why": "silence≥0.60s"
 },
 {
  "t": 51.92,
  "e": "bot_start"
 },
 {
  "t": 53.62,
  "e": "event",
  "type": "task.completed",
  "seq": 4,
  "version": 1
 },
 {
  "t": 53.9,
  "e": "announce",
  "type": "task.completed",
  "seq": 4
 },
 {
  "t": 55.4,
  "e": "caller_done_check",
  "done": false,
  "job_done": true,
  "ms": 171
 },
 {
  "t": 60.385,
  "e": "turn_start"
 },
 {
  "t": 61.757,
  "e": "turn_end",
  "words": 3,
  "speech_s": 0.67,
  "text": "All लिख दीजिए"
 },
 {
  "t": 61.767,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 62.559,
  "e": "llm_start"
 },
 {
  "t": 62.758,
  "e": "tool_call",
  "tool": "create_document"
 },
 {
  "t": 62.788,
  "e": "tool",
  "tool": "create_doc",
  "status": "accepted",
  "ms": 36
 },
 {
  "t": 63.008,
  "e": "llm_end",
  "chars": 123,
  "text": "Main likh deti hoon, bas 15 second lagenge. Aapko iski PDF aur Word dono mein copy mil jayegi. Kuch aur add karna hai isme?"
 },
 {
  "t": 63.263,
  "e": "gate_release",
  "held_ms": 40,
  "why": "silence≥0.60s"
 },
 {
  "t": 63.283,
  "e": "bot_start"
 },
 {
  "t": 63.958,
  "e": "repeat_drop",
  "sig": "a41c3e9b20",
  "text": "Leave Application ready hai, Saurabh ji. Kuch aur chahiye?"
 },
 {
  "t": 67.277,
  "e": "bot_stop"
 },
 {
  "t": 72.069,
  "e": "bot_stop"
 },
 {
  "t": 84.2,
  "e": "tool_deferred",
  "tool": "edit_document"
 },
 {
  "t": 97.1,
  "e": "event",
  "type": "task.completed",
  "seq": 9,
  "version": 2
 },
 {
  "t": 97.3,
  "e": "announce",
  "type": "task.completed",
  "seq": 9
 },
 {
  "t": 97.888,
  "e": "turn_start"
 },
 {
  "t": 98.353,
  "e": "turn_start"
 },
 {
  "t": 98.403,
  "e": "gate_drop",
  "why": "interrupted"
 },
 {
  "t": 98.403,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 98.403,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 99.117,
  "e": "turn_end",
  "words": 3,
  "speech_s": 0.86,
  "text": "Exchange कर दीजिए।"
 },
 {
  "t": 99.127,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 99.137,
  "e": "llm_start"
 },
 {
  "t": 99.527,
  "e": "llm_end",
  "chars": 158,
  "text": "Solarb ji, jo aapne abhi leave application banwayi hai, uski PDF aur Word dono formats ready hain. Kya isme koi badlav karna hai, ya koi aur document chahiye?"
 },
 {
  "t": 102.513,
  "e": "turn_end",
  "words": 8,
  "speech_s": 3.8,
  "text": "छुट्टी 26 से 27 October तक कर दीजिए।"
 },
 {
  "t": 102.523,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 103.377,
  "e": "llm_start"
 },
 {
  "t": 103.521,
  "e": "tool_call",
  "tool": "edit_document"
 },
 {
  "t": 103.551,
  "e": "tool",
  "tool": "edit_doc",
  "status": "accepted",
  "ms": 58
 },
 {
  "t": 103.771,
  "e": "llm_end",
  "chars": 75,
  "text": "Abhi change kar rahi hoon, bas 5 second lagenge. 26 se 27 October kar doon?"
 },
 {
  "t": 104.004,
  "e": "gate_release",
  "held_ms": 40,
  "why": "silence≥0.60s"
 },
 {
  "t": 104.024,
  "e": "bot_start"
 },
 {
  "t": 104.721,
  "e": "repeat_drop",
  "sig": "a41c3e9b20",
  "text": "Leave Application version 2 ready hai, 26 se 27 October ke saath. Aur kuch chahiye?"
 },
 {
  "t": 109.381,
  "e": "bot_stop"
 },
 {
  "t": 117.585,
  "e": "turn_start"
 },
 {
  "t": 118.056,
  "e": "turn_start"
 },
 {
  "t": 118.106,
  "e": "gate_drop",
  "why": "interrupted"
 },
 {
  "t": 118.106,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 118.106,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 118.223,
  "e": "turn_end",
  "words": 1,
  "speech_s": 0.28,
  "text": "How"
 },
 {
  "t": 118.233,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 118.243,
  "e": "llm_start"
 },
 {
  "t": 118.633,
  "e": "llm_end",
  "chars": 58,
  "text": "Leave Application ready hai, Saurabh ji. Kuch aur chahiye?"
 },
 {
  "t": 119.262,
  "e": "turn_end",
  "words": 2,
  "speech_s": 0.85,
  "text": "Car दीजिए।"
 },
 {
  "t": 119.272,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 119.272,
  "e": "llm_start"
 },
 {
  "t": 119.896,
  "e": "llm_end",
  "chars": 89,
  "text": "Saurabh ji, yeh toh Leave Application hi hai. Aapka document ready hai. Kuch aur chahiye?"
 },
 {
  "t": 120.156,
  "e": "gate_release",
  "held_ms": 204,
  "why": "silence≥0.60s"
 },
 {
  "t": 120.176,
  "e": "bot_start"
 },
 {
  "t": 120.846,
  "e": "repeat_drop",
  "sig": "a41c3e9b20",
  "text": "Saurabh ji, kuch aur chahiye?"
 },
 {
  "t": 126.533,
  "e": "bot_stop"
 },
 {
  "t": 132.6,
  "e": "caller_done_check",
  "done": true,
  "job_done": true,
  "ms": 183
 },
 {
  "t": 139.33,
  "e": "turn_start"
 },
 {
  "t": 139.71,
  "e": "turn_start"
 },
 {
  "t": 139.76,
  "e": "gate_drop",
  "why": "interrupted"
 },
 {
  "t": 139.76,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 139.851,
  "e": "turn_end",
  "words": 1,
  "speech_s": 0.18,
  "text": "No"
 },
 {
  "t": 139.861,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 139.871,
  "e": "llm_start"
 },
 {
  "t": 140.011,
  "e": "tool_call",
  "tool": "end_call"
 },
 {
  "t": 140.041,
  "e": "tool",
  "tool": "end_call",
  "status": "ending",
  "ms": 0
 },
 {
  "t": 140.084,
  "e": "turn_start"
 },
 {
  "t": 140.134,
  "e": "gate_drop",
  "why": "interrupted"
 },
 {
  "t": 140.134,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 140.134,
  "e": "unsaid",
  "sig": "5b1f0c9e7a"
 },
 {
  "t": 140.261,
  "e": "llm_end",
  "chars": 0
 },
 {
  "t": 140.587,
  "e": "turn_end",
  "words": 1,
  "speech_s": 0.46,
  "text": "Bus"
 },
 {
  "t": 140.597,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 140.607,
  "e": "llm_start"
 },
 {
  "t": 140.997,
  "e": "llm_end",
  "chars": 80,
  "text": "Namaste, Saurabh ji. Aapki leave application taiyaar hai. Apna khayal rakhiyega."
 },
 {
  "t": 141.543,
  "e": "turn_end",
  "words": 2,
  "speech_s": 1.05,
  "text": "Thank you."
 },
 {
  "t": 141.553,
  "e": "turn_verdict",
  "verdict": "complete",
  "source": "rules"
 },
 {
  "t": 141.875,
  "e": "llm_start"
 },
 {
  "t": 141.992,
  "e": "tool_call",
  "tool": "end_call"
 },
 {
  "t": 142.022,
  "e": "tool",
  "tool": "end_call",
  "status": "ending",
  "ms": 0
 },
 {
  "t": 142.242,
  "e": "llm_end",
  "chars": 79,
  "text": "Namaste Saurabh ji, aapki leave application taiyaar hai. Apna khayal rakhiyega."
 },
 {
  "t": 142.399,
  "e": "gate_release",
  "held_ms": 41,
  "why": "silence≥0.60s"
 },
 {
  "t": 142.419,
  "e": "bot_start"
 },
 {
  "t": 148.062,
  "e": "bot_stop"
 }
];
