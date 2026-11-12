#!/usr/bin/env bash
# Installs Playwright's browsers, with a time limit and one retry (D-238).
#
#   scripts/ci/playwright_install.sh npx playwright install --with-deps chromium webkit
#
# The arguments are the install command, run as given. At most two attempts,
# each stopped after PW_ATTEMPT_SECONDS (300), with PW_WAIT_SECONDS (30)
# between them. On #95 the runner couldn't resolve azure.archive.ubuntu.com
# inside `--with-deps`, and the web job's install ran 1 h 41 min with no
# limit; the same run's second attempt failed after 20 min. `--with-deps`
# stays: without it a missing system library would surface later as a
# browser that won't launch, which is harder to read than a DNS failure.
#
# `timeout` signals its whole process group, so apt-get under npx stops too.
# Each attempt says its number; a failed attempt is a warning annotation, and
# two failed attempts end with an error annotation and exit 1. No third-party
# action is used.
set -uo pipefail

if [ "$#" -eq 0 ]; then
  echo "::error title=Playwright install::no install command given"
  exit 2
fi

ATTEMPT_SECONDS="${PW_ATTEMPT_SECONDS:-300}"
WAIT_SECONDS="${PW_WAIT_SECONDS:-30}"

for attempt in 1 2; do
  echo "Playwright install, attempt ${attempt} of 2 (at most ${ATTEMPT_SECONDS} s), $(date -u +%H:%M:%SZ): $*"
  timeout --kill-after=10 "${ATTEMPT_SECONDS}" "$@"
  status=$?
  if [ "${status}" -eq 0 ]; then
    echo "Playwright install, attempt ${attempt} of 2: done, $(date -u +%H:%M:%SZ)"
    exit 0
  fi
  if [ "${status}" -eq 124 ] || [ "${status}" -eq 137 ]; then
    why="stopped at its ${ATTEMPT_SECONDS} s limit"
  else
    why="exit ${status}"
  fi
  echo "::warning title=Playwright install::attempt ${attempt} of 2 failed (${why}), $(date -u +%H:%M:%SZ)"
  if [ "${attempt}" -lt 2 ]; then
    echo "Waiting ${WAIT_SECONDS} s before attempt 2."
    sleep "${WAIT_SECONDS}"
  fi
done

echo "::error title=Playwright install::both attempts failed (each at most ${ATTEMPT_SECONDS} s, ${WAIT_SECONDS} s apart); the cause is in the attempts' output above"
exit 1
