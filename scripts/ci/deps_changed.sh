#!/usr/bin/env bash
# Did any of the named dependency files change in this run's commits? Writes
# blocking=yes|no and changed=<files> to $GITHUB_OUTPUT (stdout outside CI)
# for the dependency audit that follows (founder, 2026-10-23; D-219). A known
# vulnerability in a runtime dependency blocks only when that app's
# dependency files changed, so an advisory published against an unchanged
# file never turns an unrelated pull request red. The daily audit
# (.github/workflows/dependency-audit.yml) always blocks.
#
#   scripts/ci/deps_changed.sh <path>...
#
# Paths are relative to the repository root. Reads EVENT
# (github.event_name), BEFORE (github.event.before) and SHA (github.sha)
# from the environment:
#   pull_request: the checkout is the merge commit, and its first parent is
#                 the base branch (checkout needs fetch-depth: 2);
#   push:         BEFORE..SHA, after fetching BEFORE (main included).
# Whenever it can't tell, it blocks: any other event (the weekly debian
# snapshot's call included), an all-zero or unfetchable BEFORE, any git error.
set -uo pipefail

OUT="${GITHUB_OUTPUT:-/dev/stdout}"

decide() {  # yes|no, why, changed files
  echo "blocking=$1" >> "$OUT"
  echo "changed=$3" >> "$OUT"
  echo "dependency files: blocking=$1 ($2)" >&2
  exit 0
}

if [ "$#" -eq 0 ]; then
  echo "usage: deps_changed.sh <path>..." >&2
  exit 2
fi
ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || decide yes "not in a git repository" ""
cd "$ROOT" || decide yes "can't enter $ROOT" ""

case "${EVENT:-}" in
  pull_request)
    BASE="HEAD^1"
    TIP="HEAD"
    ;;
  push)
    if [ -z "${BEFORE:-}" ] || [ -z "${BEFORE//0/}" ]; then
      decide yes "a push with no previous commit" ""
    fi
    if ! git cat-file -e "${BEFORE}^{commit}" 2>/dev/null \
      && ! git fetch --quiet --no-tags --depth=1 origin "$BEFORE" 2>/dev/null; then
      decide yes "the previous commit $BEFORE can't be fetched" ""
    fi
    BASE="$BEFORE"
    TIP="${SHA:-HEAD}"
    ;;
  *)
    decide yes "the event '${EVENT:-}' always blocks" ""
    ;;
esac

CHANGED=$(git diff --name-only "$BASE" "$TIP" -- "$@" 2>/dev/null) \
  || decide yes "git can't compare $BASE and $TIP" ""
CHANGED=$(printf '%s' "$CHANGED" | tr '\n' ' ')
if [ -n "$CHANGED" ]; then
  decide yes "changed: $CHANGED" "$CHANGED"
fi
decide no "none of them changed" ""
