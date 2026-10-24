#!/usr/bin/env bash
# Fails if staged changes contain anything that looks like a credential, or if a secret file is staged.
# Used by .git/hooks/pre-commit and scripts/check.sh.
set -euo pipefail

staged_files=$(git diff --cached --name-only --diff-filter=ACMR)
[ -z "$staged_files" ] && exit 0

fail=0
for f in $staged_files; do
  case "$f" in
    *plivo_auth.txt|*git_classic.txt|*_key.txt|*_auth.txt|*token*.txt|*.pem|*.key|*.env|.env*)
      [ "$f" = ".env.example" ] || [[ "$f" == *"/.env.example" ]] || { echo "BLOCKED: secret file staged: $f"; fail=1; } ;;
  esac
done

# Patterns: GitHub tokens, AWS keys, Plivo Auth IDs, private keys, generic "token/secret = <long value>".
patterns='(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16}|\bMA[A-Z0-9]{18}\b|\bSA[A-Z0-9]{18}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----|(api[_-]?key|auth[_-]?token|secret)["'"'"' ]*[:=]["'"'"' ]*[A-Za-z0-9_\-]{24,})'
if git diff --cached -U0 --diff-filter=ACMR | grep -E '^\+' | grep -v '^+++' | grep -nE "$patterns" >/dev/null; then
  echo "BLOCKED: staged diff contains credential-like strings:"
  git diff --cached -U0 --diff-filter=ACMR | grep -E '^\+' | grep -v '^+++' | grep -nE "$patterns" | sed -E 's/([A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]{10,}/\1…REDACTED/g' | head -20
  fail=1
fi

[ "$fail" -eq 0 ] && echo "secret scan: clean"
exit "$fail"
