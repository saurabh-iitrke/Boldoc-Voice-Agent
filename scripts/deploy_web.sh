#!/usr/bin/env bash
# Build the web app and publish it to the EC2 box: ~/app/site/releases/<id>, then an atomic switch of the
# ~/app/site/current symlink (Caddy serves /srv/webroot/current). A directory swap would not work: a bind mount
# keeps pointing at the old directory.
# Usage: scripts/deploy_web.sh [mock|live]  (mock = demo data; live = the real API)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
MODE=${1:-mock}
case "$MODE" in mock|live) ;; *) echo "usage: $0 [mock|live]" >&2; exit 2 ;; esac
HOST=${DEPLOY_HOST:?set DEPLOY_HOST, e.g. ubuntu@your-server}
KEY=${DEPLOY_KEY:-$HOME/.ssh/id_ed25519}
ID="$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)-$MODE"

(cd web && VITE_API_MODE=$MODE npx vite build --outDir dist-deploy --emptyOutDir >/dev/null)
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -czf - -C web/dist-deploy . \
  | ssh -i "$KEY" -o ConnectTimeout=15 "$HOST" "set -e; mkdir -p app/site/releases/$ID && tar -xzf - -C app/site/releases/$ID \
      && cd app/site && ln -sfn releases/$ID current.tmp && mv -T current.tmp current \
      && ls -1dt releases/* | tail -n +4 | xargs -r rm -rf"
rm -rf web/dist-deploy
echo "web ($MODE) published as $ID"
