#!/usr/bin/env bash
# Quality gate run before every commit/push: lint, tests, secret scan.
# Usage: scripts/check.sh            (uses .venv if present)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
PY=${PY:-.venv/bin/python}
RUFF=${RUFF:-.venv/bin/ruff}

echo "== ruff"
"$RUFF" check .

echo "== pytest"
set +e
"$PY" -m pytest -q
rc=$?
set -e
if [ "$rc" -ne 0 ] && [ "$rc" -ne 5 ]; then exit "$rc"; fi  # 5 = no tests collected

if [ -d web/node_modules ]; then
  echo "== web typecheck"
  (cd web && npx tsc -b)
fi

echo "== shell syntax"
for f in $(git ls-files '*.sh') scripts/*.sh infra/*.sh; do [ -f "$f" ] && bash -n "$f"; done

echo "== secret scan (staged)"
scripts/secret_scan.sh
echo "ALL CHECKS PASSED"
