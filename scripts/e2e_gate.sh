#!/usr/bin/env bash
# Regression gate after a deploy: a scripted fake call against the live server, then the call report with
# pass/fail checks (reply latency SLOs, no dead air, no talking over the caller, no premature "done", thinker
# and SMS healthy). Exit code 1 if any check fails.
# Usage: scripts/e2e_gate.sh [scenario]      (scenarios: tests/talker_e2e/fake_caller.py; default: doc)
#        ADMIT_FROM=919xxxxxxxxx scripts/e2e_gate.sh doc   admit the call with that caller number first (real SMS)
#        GATE_LLM=overflow scripts/e2e_gate.sh doc          talk to the overflow LLM (busy-time talker) instead
#        GATE_INTAKE=off scripts/e2e_gate.sh doc            without the writer's checklist (to compare)
#        GATE_ISOLATION=off scripts/e2e_gate.sh doc         without voice isolation (to compare)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
HOST=${DEPLOY_HOST:?set DEPLOY_HOST, e.g. ubuntu@your-server}
KEY=${DEPLOY_KEY:-$HOME/.ssh/id_ed25519}
PUBLIC=${PUBLIC_HOST:-boldoc.in}
SCENARIO=${1:-doc}
case "$SCENARIO" in doc) EXPECT=create,edit ;; sense) EXPECT=sense,create ;; slow) EXPECT=create ;; lang) EXPECT=hindi,ends ;; *) EXPECT= ;; esac  # what the scripted call must produce
OUT=$(mktemp -d)
SK=$(ssh -i "$KEY" -o ConnectTimeout=15 "$HOST" 'grep ^STREAM_KEY= app/infra/.env | cut -d= -f2-')
CALL_ID="test-$(date +%s)"
if [ -n "${ADMIT_FROM:-}" ]; then  # the stream trusts only control's call record for the caller's number
  ssh -i "$KEY" -o ConnectTimeout=15 "$HOST" \
    "cd app/infra && docker compose exec -T control python -m apps.control.calls admit $CALL_ID $ADMIT_FROM --test"
fi
.venv312/bin/python tests/talker_e2e/fake_caller.py --key-file sarvam_key.txt --scenario "$SCENARIO" --out "$OUT" \
  --call-id "$CALL_ID" --ws "wss://$PUBLIC/plivo/stream?k=$SK&test=1&llm=${GATE_LLM:-primary}&intake=${GATE_INTAKE:-on}&isolation=${GATE_ISOLATION:-on}" 2>&1 \
  | grep -v -i "deprecat\|audioop" > "$OUT/caller.txt" || true  # a failed call is reported below, with its error
CALL=$(awk '/^call test-/{print $2}' "$OUT/caller.txt")
[ -n "$CALL" ] || { tail -5 "$OUT/caller.txt"; echo "fake call did not complete"; exit 2; }
sleep 4  # the talker ships its trace when the call ends
ssh -i "$KEY" -o ConnectTimeout=15 "$HOST" \
  "cd app/infra && docker compose exec -T control python -m apps.control.report $CALL --check --expect=$EXPECT"
