#!/usr/bin/env bash
# The one way to run scripts/record_mail_fixtures.py (B1 slice 4 commit 7;
# D-246; live calls on the founder's word only). From the repo root, in Git
# Bash on the founder's PC:
#
#   bash scripts/record_mail_fixtures.sh
#   bash scripts/record_mail_fixtures.sh --relabel   (the second run)
#
# It reads one line of .env, ANTHROPIC_API_KEY, and nothing else from it,
# then runs the script in an emptied environment (env -i) holding only that
# key and what Windows needs to start Python and open a TLS connection
# (PATH, SYSTEMROOT, TEMP, TMP, USERPROFILE, HOME). No database, Supabase,
# Storage or Stripe setting reaches the script; it refuses to start if one
# does, and its Settings never read .env. The key is never printed.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${REPO}/apps/worker/.venv/Scripts/python.exe"
line="$(grep -E '^ANTHROPIC_API_KEY=' "${REPO}/.env" | head -1 || true)"
key="${line#ANTHROPIC_API_KEY=}"
key="${key%$'\r'}"
key="${key%\"}"
key="${key#\"}"
if [ -z "${key}" ]; then
  echo "refused: no ANTHROPIC_API_KEY line in .env" >&2
  exit 1
fi

exec env -i \
  PATH="${PATH}" \
  SYSTEMROOT="${SYSTEMROOT:-C:\\Windows}" \
  TEMP="${TEMP:-}" TMP="${TMP:-}" USERPROFILE="${USERPROFILE:-}" HOME="${HOME:-}" \
  PYTHONIOENCODING=utf-8 \
  ANTHROPIC_API_KEY="${key}" \
  "${PYTHON}" "${REPO}/scripts/record_mail_fixtures.py" "$@"
