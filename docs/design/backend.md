# Backend (control plane) — design

## Services (one EC2 box in ap-south-1, Docker Compose)
| Service | Role |
|---|---|
| `caddy` | TLS, static web app, `/api/*` + `/plivo/*` → `control` |
| `control` | FastAPI: auth, documents, links, calls, live WebSocket, Plivo webhooks |
| `postgres` | Source of truth (users, sessions, documents, versions, tasks, deliveries, events) |
| `redis` | Live-event fan-out (pub/sub) and rate limits — a projection, rebuilt from Postgres |
| `temporal` + `worker` | Durable thinker/delivery workflows (phase B4) |
| `talker` | Pipecat voice bot on the Plivo audio stream (M1, needs the Sarvam key) |

## Reusing the tested orchestrator on Postgres
The orchestrator core (`apps/control/core/orchestrator.py`) is pure logic over a `MemoryStore`, property-tested
with 1,000 random interleavings. Production keeps that logic unchanged:

1. `BEGIN`; lock the conversation session row (`SELECT … FOR UPDATE`).
2. Load the session aggregate (its documents, versions, tasks, questions, deliveries) into a `MemoryStore`.
3. Run the orchestrator operation in memory.
4. Upsert the aggregate and append new events to the `events` outbox in the same transaction; `COMMIT`.
5. After commit, publish the new events to Redis `live:<session>`.

Locking the session row serialises every mutation of that session, which is exactly the single-writer
assumption the property tests verify. Workers claim tasks across sessions with `FOR UPDATE SKIP LOCKED`.

## Live events
The browser connects to `/api/live?session=<id>&after=<seq>`. The server replays `events` with `seq > after`
from Postgres, then streams new ones from Redis. A dropped WebSocket reconnects with its last `seq`, so no event
is lost or duplicated (Postgres is the source of truth; Redis only speeds delivery).

## Identity
- Website users sign in with phone + OTP (SMS through Plivo). OTP codes are stored as HMAC-SHA256 with a server
  pepper; 5 attempts, 5-minute expiry, 30-second resend cooldown, per-phone and per-IP limits.
- Sessions: random 256-bit token in an `HttpOnly; Secure; SameSite=Lax` cookie; only its SHA-256 is stored.
- Documents are owned by a phone number, so a PSTN call from your verified mobile shows up on the website.
- Browser calls: per-user Plivo endpoint (created lazily); the server mints a 5-minute Plivo JWT
  (`POST /JWT/Token/`, `sub` = endpoint username). The call carries `X-PH-Session`, which the answer webhook
  maps back to the user's conversation session.
- "Call me" dials only the signed-in user's own verified number; the click is stored as consent.

## Webhook security
Every `/plivo/*` request is checked with Plivo's V3 signature (`plivo.utils.signature_v3.validate_v3_signature`)
before any state changes. Unsigned or invalid requests get 403.

## Files
Documents are rendered on demand by DocEngine (validated before serving) and cached on disk keyed by
(doc, version, format). S3 in ap-south-1 replaces the disk cache when AWS credentials are configured.
