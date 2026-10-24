#!/usr/bin/env bash
# Installs a pre-commit hook: secret scan + ruff on staged Python files.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
cat > .git/hooks/pre-commit <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
scripts/secret_scan.sh
py=$(git diff --cached --name-only --diff-filter=ACMR -- '*.py')
if [ -n "$py" ]; then .venv/bin/ruff check $py; fi
HOOK
chmod +x .git/hooks/pre-commit
echo "pre-commit hook installed"
