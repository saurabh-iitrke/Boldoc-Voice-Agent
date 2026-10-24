# Turn-taking: when has the caller finished?

## Layers (apps/talker)
1. **Silero VAD** marks the end of speech after 0.2 s of silence.
2. **Smart Turn v3** (Pipecat's audio turn model, 8M parameters, BSD-2) decides from the sound when the turn has
   ended, with a 1.0 s silence fallback. A final transcript that ends with ।, . or ? ends the turn at once, and a
   short whole answer ends it after 0.5 s (`processors.CallerTurnStop`).
3. **The word check** decides whether BolDoc may answer yet. The reply is generated as soon as the turn ends, and the
   playback gate holds it until the caller has kept quiet long enough (`session.required_gap`).
   - Word rules first (`logic/turns.py`, `turn_judge.by_rules`): unfinished endings such as "aur", "ke liye",
     "kyunki", "मेरा"; "ek minute" asks BolDoc to wait; short answers and punctuated sentences are finished.
   - What the rules leave open goes to **LiveKit's multilingual end-of-turn model** (`eou.py`), run as its plugin
     runs it, for Hindi/Hinglish and English. Other languages keep the rules; without the model files the Sarvam
     classifier is used, as before.
   - "Item named, value missing" endings ("flight का खर्चा", "मेरा account number है", "Goa trip के खर्चे में
     flight") are decided by a word rule (`logic/turns.value_missing`): they were the model's misses.
   - An unfinished sentence holds the reply up to **3.5 s** of silence when the rules found it, or the model did on 5+
     words; the model's "unfinished" on 4 words or fewer is often an answer ("बैंक मैनेजर के लिए") and keeps the old
     2.0 s, as does the classifier. After **1.2 s** BolDoc says **"हम्म"** once: a clip in the call's own voice,
     played straight to the line as plain audio (the transport reports no "bot speaking" for it), not a reply and
     never in the model's context (`ack.py`).
   - A greeting alone ("नमस्ते.") waits 1.0 s: callers go on with the request ~0.56 s later.

## Why LiveKit's model replaced the Sarvam classifier
After a real call where a slow speaker went on 4.0 s into a pause the old 2.0 s hold had already
ended ("if speaker is slower and thinking then do not trigger end of turn if sentence is not complete. add filler
hmm").

The set: 268 caller utterances at turn ends, without end punctuation (punctuated finals never reach the word check).
208 are finished: hand-labelled utterances from our real and scripted calls plus 20 whole requests. 60 are
unfinished: 15 from calls plus 45 mid-thought cuts in our callers' shapes. Both judges got the same BolDoc line before
each utterance and the same word rules in front.

| Word check | "Unfinished" calls that were right | Real pauses caught | Finished sentences held |
|---|---|---|---|
| Rules, then Sarvam classifier (before) | 44% (54/123) | 90% | 33% (69/208) |
| Rules, then LiveKit's model | 81% (47/58) | 78% | 5% (11/208) |

- **Latency:** LiveKit's model takes 178 ms p50 and 339 ms p95 on one core of the box (t3.large). The classifier
  took 180 ms p50 and 556 ms p95 over the network. Both run in parallel with the reply.
- **Rate limit:** the model takes the classifier's requests (about half of all turns) off the shared Sarvam limit.
- **Its misses** are "item named, value missing" ("flight का खर्चा", "मेरा account number है"): BolDoc answers
  those after the normal 0.5–0.6 s gap.
- **Smart Turn v3 can't be scored on this set:** it judges audio, and we don't keep call audio. Daily publishes
  93.4% accuracy on Hindi for it (1,295 clips, 4.4% called finished too early).
  LiveKit publishes 99.4% / 96.3% (finished / unfinished right) on Hindi; on our Hinglish set it was 94% / 73%.

## What the first gates showed
- The "हम्म" worked: after "मुझे अपने HR manager के लिए" it played at 1.3 s, the caller went on at 2.8 s, and the
  half-sentence reply was dropped (gate `slow`).
- **The model was slow on calls:** 14 checks, p50 754 ms, max 1,632 ms, against 192 ms idle. Devanagari is several
  tokens a word, so BolDoc's read-back plus the caller's words filled the plugin's 128 tokens (a real prompt: 115),
  at about 6 ms a token on the box. The last 32 tokens score the same on our set, so that is all it reads now, on its
  own two threads.
- Misses fixed by rules: "Goa trip के खर्चे में flight" (p 0.57: finished) and the short answer "बैंक मैनेजर के लिए"
  held 3.5 s. The talk-over after "नमस्ते." predates this work.
- Not fixable from words: the scripted "…chhutti chahiye kyunki" came back from speech recognition as "Cookie". It is
  a test artifact too: each scripted fragment is synthesised as a sentence of its own, with final intonation.

## The model
- `livekit/turn-detector`, revision `v0.4.1-intl`.
- `onnx/model_q8.onnx` is 396 MB, fetched into the talker image by `eou_fetch.py` and pinned by sha256.
- It adds about 210 MB to the talker (380 to 588 MiB measured on the box) and is loaded at startup.
- **Licence:** the LiveKit Model License, which allows the model only together with the LiveKit Agents framework.
