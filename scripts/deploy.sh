#!/usr/bin/env bash
# Sync git-tracked files (working-tree versions; never secrets, never .venv) to the EC2 box.
# Portable: tar over ssh (macOS ships openrsync, which lacks the options we'd need).
# Usage: scripts/deploy.sh          (env: DEPLOY_HOST, DEPLOY_KEY, DEPLOY_DIR override defaults)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
HOST=${DEPLOY_HOST:?set DEPLOY_HOST, e.g. ubuntu@your-server}
KEY=${DEPLOY_KEY:-$HOME/.ssh/id_ed25519}
DEST=${DEPLOY_DIR:-app}

# COPYFILE_DISABLE stops macOS tar adding ._* AppleDouble files.
git ls-files -z | COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata --null -T - -czf - \
  | ssh -i "$KEY" -o ConnectTimeout=15 "$HOST" "mkdir -p '$DEST' && tar -xzf - -C '$DEST'"
echo "synced $(git ls-files | wc -l | tr -d ' ') tracked files to $HOST:~/$DEST ($(git rev-parse --short HEAD))"
