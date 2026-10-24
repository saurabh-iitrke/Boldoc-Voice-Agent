#!/usr/bin/env bash
# A real "Talk in browser" call, run on the server in Playwright's Linux image: Chrome with a fake microphone playing
# a scripted caller (tests/talker_e2e/caller_wav.py), signed in as an allowlisted test number (never sent an SMS) with a
# session made on the server (accounts.cli session), so no OTPs are spent.
# On macOS, headless Chrome can't open even a fake microphone (getUserMedia never settles), so the call runs on Linux.
# Usage: scripts/browser_call_on_box.sh <caller.wav> <local out dir> [seconds]
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
WAV=${1:?caller wav}; OUT=${2:?out dir}; SECONDS_BUDGET=${3:-150}
HOST=${DEPLOY_HOST:?set DEPLOY_HOST, e.g. ubuntu@your-server}; KEY=${DEPLOY_KEY:-$HOME/.ssh/id_ed25519}
SITE=${PUBLIC_HOST:-boldoc.in}; PHONE=${BOLDOC_TEST_PHONE:-+919999900001}
PW=1.64.1
SSH=(ssh -i "$KEY" -o ConnectTimeout=15 "$HOST")
"${SSH[@]}" 'mkdir -p ~/app/tmp/browsercall && rm -rf ~/app/tmp/browsercall/out'
scp -q -i "$KEY" web/scripts/browser_call.mjs "$WAV" "$HOST:app/tmp/browsercall/"
"${SSH[@]}" bash -s "$SITE" "$PHONE" "$PW" "$(basename "$WAV")" "$SECONDS_BUDGET" <<'REMOTE'
set -euo pipefail
SITE=$1; PHONE=$2; PW=$3; WAV=$4; BUDGET=$5
cd ~/app/tmp/browsercall
umask 077
ENVF=$(mktemp); trap 'rm -f "$ENVF"' EXIT
# A session for the allowlisted test number, made on the server (no OTP spent). < /dev/null: this script arrives on
# stdin, and docker compose exec would swallow the rest of it.
TOKEN=$(cd ~/app/infra && docker compose exec -T control python -m apps.control.accounts.cli session "$PHONE" \
  < /dev/null 2>/dev/null | tail -1)
[ ${#TOKEN} -ge 40 ] || { echo "no session for $PHONE: $TOKEN" >&2; exit 1; }
echo "BOLDOC_COOKIE=$TOKEN" > "$ENVF"
docker run --rm --network host --env-file "$ENVF" --user "$(id -u):$(id -g)" -e HOME=/tmp < /dev/null \
  -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
  -v "$PWD:/work" -w /work "mcr.microsoft.com/playwright:v$PW-noble" \
  sh -c "npm init -y > /dev/null && npm i --no-audit --no-fund playwright@$PW > /dev/null && \
         node browser_call.mjs https://$SITE /work/$WAV /work/out $BUDGET"
REMOTE
mkdir -p "$OUT"
scp -q -r -i "$KEY" "$HOST:app/tmp/browsercall/out/*" "$OUT/"
echo "results in $OUT"
