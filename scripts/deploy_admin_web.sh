#!/usr/bin/env bash
# Build the admin console and publish it to the EC2 box: ~/app/site-admin/releases/<id>, then an atomic switch of the
# ~/app/site-admin/current symlink (Caddy serves /srv/adminroot/current on the ADMIN_HOSTS names). Always the live API:
# the console has no demo mode in production. Run it once before the first `docker compose up -d caddy` with the
# site-admin mount, so the directory exists and belongs to the deploy user (Docker would create it as root).
# Usage: scripts/deploy_admin_web.sh
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
HOST=${DEPLOY_HOST:?set DEPLOY_HOST, e.g. ubuntu@your-server}
KEY=${DEPLOY_KEY:-$HOME/.ssh/id_ed25519}
ID="$(date +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"

(cd web && VITE_API_MODE=live npx vite build --config vite.admin.config.ts --outDir dist-admin --emptyOutDir >/dev/null)
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -czf - -C web/dist-admin . \
  | ssh -i "$KEY" -o ConnectTimeout=15 "$HOST" "set -e; mkdir -p app/site-admin/releases/$ID \
      && tar -xzf - -C app/site-admin/releases/$ID \
      && cd app/site-admin && ln -sfn releases/$ID current.tmp && mv -T current.tmp current \
      && ls -1dt releases/* | tail -n +4 | xargs -r rm -rf"
rm -rf web/dist-admin
echo "admin console published as $ID"
