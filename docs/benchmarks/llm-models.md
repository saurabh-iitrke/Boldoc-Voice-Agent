# LLM benchmark: Sarvam vs Amazon Bedrock (Mumbai)

Run with `tools/llm_bench/bench.py` from a laptop in India to the Mumbai endpoints (so absolute times include a
home-broadband hop; the server, also in Mumbai, measures less). Same prompts and tools as production.

## Talker (live call): 5 scenarios × 3 runs = 15 turns per model

Scenarios: a Hinglish request, confirming details (must call `create_document` with PDF + Word), a Tamil caller
(must answer in Tamil script), a voice edit (must call `edit_document` with the new date), an English request.
"First word" is when the first word of the answer (or the tool call) arrives: what text-to-speech waits for.

| Model | Where | First word p50 / p90 | Right tool + args | Right script | Words/reply |
|---|---|---|---|---|---|
| **sarvam-105b** (reasoning off) | Sarvam, India | **0.17 / 0.21 s** | 15/15 | 15/15 | 12 |
| qwen.qwen3-235b-a22b-2507 | Bedrock ap-south-1 | 0.55 / 0.66 s | 15/15 | 15/15 | 14 |
| mistral.mistral-large-3-675b | Bedrock ap-south-1 | 0.55 / 0.79 s | 15/15 | 15/15 | 13 |
| openai.gpt-oss-120b (reasoning low) | Bedrock ap-south-1 | 0.51 / 0.60 s | 14/15 | 15/15 | 25 |
| mistral.ministral-3-14b | Bedrock ap-south-1 | 0.34 / 0.49 s | 13/15 | 14/15 | 29 |
| deepseek.v3.2 | Bedrock ap-south-1 | 0.47 / 0.57 s | 11/15 | 14/15 | 42 |
| zai.glm-4.7-flash | Bedrock ap-south-1 | 0.46 / 0.55 s | 10/15 | 15/15 | 21 |
| moonshotai.kimi-k2.5 | Bedrock ap-south-1 | 0.55 / 1.85 s | 10/15 | 15/15 | 35 |
| qwen.qwen3-next-80b-a3b | Bedrock ap-south-1 | 0.58 / 0.84 s | 9/15 | 15/15 | 26 |
| google.gemma-3-27b-it | Bedrock ap-south-1 | no output with tools | 9/15 | 0/15 | 0 |

Sarvam also sounded the most natural: feminine first-person forms as the persona requires, colloquial Tamil
("உதவி பண்றேன்"), no lists or blank lines. Qwen3-235B and Mistral Large 3 were correct but more formal and
sometimes split replies into paragraphs (fine for text, odd for speech).

## Thinker (document draft): the real call's transcript -> document JSON, production settings

| Model | Valid drafts | Time p50 | First-try valid JSON | Notes |
|---|---|---|---|---|
| sarvam-105b (reasoning off) | 5/5 | 2.7 s | 3/5 (2 needed the repair retry, +~2 s) | "Dear HR Manager" |
| qwen.qwen3-235b-a22b-2507 | 3/3 | 2.8 s | 3/3 | "Respected Sir/Madam" |
| mistral.mistral-large-3-675b | 3/3 | 2.1 s | 3/3 | |
| openai.gpt-oss-120b | 3/3 | 1.4 s | 3/3 | left placeholders: "Dear [HR Manager Name]" |
| deepseek.v3.2 | 3/3 | 3.2 s | 3/3 | |

## Price (per million tokens, input / output; AWS price list for ap-south-1, 16 Oct 2026; ₹88/$)

| Model | $ | ₹ |
|---|---|---|
| sarvam-105b | | 15 / 60 |
| gpt-oss-120b | 0.18 / 0.71 | 16 / 62 |
| Qwen3-235B-A22B-2507 | 0.26 / 1.04 | 23 / 92 |
| Mistral Large 3 | 0.59 / 1.76 | 52 / 155 |
| DeepSeek V3.2 | 0.74 / 2.22 | 65 / 195 |

A ~2-minute call uses ~30K input / 0.6K output talker tokens: ₹0.48 on Sarvam, ₹0.73 on Qwen3-235B. A draft
(~1.2K in / 0.4K out, plus the review call) costs well under ₹0.2 on any of them.

## Residency

In-region Bedrock models (`ON_DEMAND` in ap-south-1, reached at `bedrock-mantle.ap-south-1.api.aws`) run in
Mumbai. Claude Haiku 4.5, Claude Sonnet 5 and Kimi K3 have India-only cross-region profiles (`in.*`: Mumbai +
Hyderabad); newer Claude/GPT models are global profiles (prompts may leave India).

## Conclusion

- **Talker: keep Sarvam.** A third of the first-word latency of anything on Bedrock, perfect tool use, the most
  natural Indic speech. Its limit (40 requests/min on the starter plan; 60/120 on Pro/Business, self-serve) caps
  concurrent calls at about 3.
- **Overflow: Bedrock Qwen3-235B-2507 (Mumbai)** for calls beyond Sarvam's capacity, instead of "busy":
  15/15 correct, ~0.35 s slower first word, similar cost, 10,000 requests/min.
- **Thinker: Qwen3-235B-2507 or Mistral Large 3 on Bedrock** would remove the repair retries and free Sarvam's
  rate limit for live calls; same speed, negligible cost.
- Switching is configuration only (`protocol/llm.py`): `LLM_PROVIDERS` with the Bedrock base URL and key file,
  then `THINKER_LLM=bedrock:qwen.qwen3-235b-a22b-2507`.
