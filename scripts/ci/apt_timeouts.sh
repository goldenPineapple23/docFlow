#!/usr/bin/env bash
# apt's own limits for this runner (D-247), written before anything installs
# with apt. Every apt-get on the runner reads them, Playwright's included:
#   - Acquire::Retries 3, and 30 s Acquire::http/https::Timeout: apt gives up
#     on a mirror that stops answering instead of waiting for the step's
#     limit. They apply per file, so the step's limit stays the real bound.
#   - DPkg::Lock::Timeout 180: an apt-get that finds the dpkg lock held waits
#     for it instead of failing at once (#580's second attempt failed in 4 s
#     on a lock held by the first attempt's apt-get).
set -euo pipefail

CONF=/etc/apt/apt.conf.d/99ci-timeouts
printf '%s\n' \
  'Acquire::Retries "3";' \
  'Acquire::http::Timeout "30";' \
  'Acquire::https::Timeout "30";' \
  'DPkg::Lock::Timeout "180";' \
  | sudo tee "${CONF}" >/dev/null
echo "apt limits written to ${CONF}:"
cat "${CONF}"
