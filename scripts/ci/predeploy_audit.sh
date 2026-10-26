#!/usr/bin/env bash
# The dependency audit before a deploy (founder, 2026-10-23; D-219; RUNBOOK
# 11): one app's runtime dependencies at the checked-out commit, always
# blocking. Exits 0 only when nothing known is found.
#
#   scripts/ci/predeploy_audit.sh api|worker|parse|web
#
# A Python app needs pip-audit 2.10.1 on PATH (the api venv has it); web
# needs npm. It reaches only PyPI's vulnerability API (pip-audit) or the npm
# registry (web), and reads the lock file only: nothing is installed.
set -uo pipefail

APP="${1:-}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$(command -v python || command -v python3)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# A path the programs below can open: on Windows (Git Bash) they are native
# programs and need a Windows path; elsewhere it is unchanged.
native() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s\n' "$1"; fi
}

case "$APP" in
  api|worker|parse)
    pip-audit -r "$(native "$ROOT/apps/$APP/requirements.lock.txt")" --no-deps --disable-pip --strict \
      --progress-spinner off -f json -o "$(native "$TMP/runtime.json")" || true
    "$PY" "$(native "$ROOT/scripts/ci/pip_audit.py")" "$APP" --blocking yes \
      "runtime=$(native "$TMP/runtime.json")"
    ;;
  web)
    (
      cd "$ROOT/apps/web" || exit 1
      npm audit --json > "$TMP/full.json" || true
      npm audit --omit=dev --json > "$TMP/runtime.json" || true
    )
    "$PY" "$(native "$ROOT/scripts/ci/npm_audit.py")" web "$(native "$TMP/full.json")" \
      "$(native "$TMP/runtime.json")" --blocking yes
    ;;
  *)
    echo "usage: predeploy_audit.sh api|worker|parse|web" >&2
    exit 2
    ;;
esac
