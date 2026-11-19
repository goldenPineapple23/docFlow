#!/usr/bin/env bash
# Runs one Playwright install command with a time limit and one retry
# (D-238; D-247). The web jobs call it twice, as two steps: the system
# packages (`playwright install-deps`, apt as root) and the browser
# (`playwright install`, no root).
#
#   PW_TITLE="Playwright system packages" PW_APT_RECOVERY=1 \
#     scripts/ci/playwright_install.sh \
#     sudo env "PATH=$PATH" HOME=/root timeout --kill-after=10 290 \
#     ./node_modules/.bin/playwright install-deps chromium
#
# The arguments are the command, run as given. At most two attempts, each
# stopped after PW_ATTEMPT_SECONDS (300). The packages command carries its
# own root `timeout`, a little shorter: Playwright then runs apt-get as root
# with no sudo of its own, so that timeout's kill of its process group
# reaches apt-get. On #580 a sudo started inside the install put apt-get
# out of reach of the non-root `timeout`, and the retry failed on the dpkg
# lock that apt-get still held (D-247).
#
# PW_APT_RECOVERY=1 (the packages step): after a failed attempt, any apt-get
# or dpkg that wasn't running before it is killed by process ID; then the
# retry waits up to PW_LOCK_WAIT_SECONDS (120) for the dpkg lock to be free
# and finishes any unpack an interrupted dpkg left (`dpkg --configure -a`).
#
# PW_PROVE_STALL=1 is the proof run of D-247 only, never a normal run: the
# first attempt goes through an unroutable proxy with low apt timeouts, and
# a background process holds the dpkg lock for 40 s as the second begins.
#
# Each attempt says its number and time; a failed attempt is a warning
# annotation, and two failed attempts end with an error annotation and
# exit 1. No third-party action is used.
set -uo pipefail

if [ "$#" -eq 0 ]; then
  echo "::error title=Playwright install::no install command given"
  exit 2
fi

TITLE="${PW_TITLE:-Playwright install}"
ATTEMPT_SECONDS="${PW_ATTEMPT_SECONDS:-300}"
WAIT_SECONDS="${PW_WAIT_SECONDS:-0}"
LOCK_WAIT_SECONDS="${PW_LOCK_WAIT_SECONDS:-120}"
APT_RECOVERY="${PW_APT_RECOVERY:-0}"
PROVE_STALL="${PW_PROVE_STALL:-0}"
DPKG_LOCK=/var/lib/dpkg/lock-frontend
PROVE_CONF=/etc/apt/apt.conf.d/99ci-prove-stall

now() { date -u +%H:%M:%SZ; }

apt_pids() { pgrep -x 'apt-get|apt|dpkg|http|https' 2>/dev/null | sort; }

recover_apt() {
  # $1: the apt and dpkg process IDs that were running before the attempt.
  local leftover
  leftover=$(comm -13 <(printf '%s\n' "$1") <(apt_pids) | tr '\n' ' ')
  if [ -n "${leftover// /}" ]; then
    echo "${TITLE}: killing what the failed attempt left running: ${leftover}"
    # shellcheck disable=SC2086
    sudo kill -KILL ${leftover} 2>/dev/null || true
  fi
  local waited=0
  while sudo fuser "${DPKG_LOCK}" >/dev/null 2>&1; do
    if [ "${waited}" -ge "${LOCK_WAIT_SECONDS}" ]; then
      echo "${TITLE}: the dpkg lock is still held after ${waited} s; trying anyway"
      break
    fi
    sleep 2
    waited=$((waited + 2))
  done
  echo "${TITLE}: dpkg lock free after ${waited} s, $(now)"
  sudo dpkg --configure -a || true
}

prove_stall_start() {
  printf '%s\n' 'Acquire::http::Proxy "http://10.255.255.1:9";' 'Acquire::https::Proxy "http://10.255.255.1:9";' \
    'Acquire::Retries "1";' 'Acquire::http::Timeout "5";' 'Acquire::https::Timeout "5";' \
    | sudo tee "${PROVE_CONF}" >/dev/null
  echo "::notice title=${TITLE} (proof)::attempt 1 goes through an unroutable proxy, $(now)"
}

prove_stall_lock() {
  sudo rm -f "${PROVE_CONF}"
  sudo python3 -c "import fcntl, time; f = open('${DPKG_LOCK}', 'w'); fcntl.lockf(f, fcntl.LOCK_EX); time.sleep(40)" &
  sleep 2
  echo "::notice title=${TITLE} (proof)::the dpkg lock is held for 40 s from $(now)"
}

for attempt in 1 2; do
  before=""
  if [ "${APT_RECOVERY}" = "1" ]; then
    before=$(apt_pids)
  fi
  if [ "${PROVE_STALL}" = "1" ] && [ "${attempt}" -eq 1 ]; then
    prove_stall_start
  fi
  started=$(date +%s)
  echo "${TITLE}, attempt ${attempt} of 2 (at most ${ATTEMPT_SECONDS} s), $(now): $*"
  timeout --kill-after=10 "${ATTEMPT_SECONDS}" "$@"
  status=$?
  took=$(( $(date +%s) - started ))
  if [ "${status}" -eq 0 ]; then
    echo "${TITLE}, attempt ${attempt} of 2: done in ${took} s, $(now)"
    exit 0
  fi
  if [ "${status}" -eq 124 ] || [ "${status}" -eq 137 ]; then
    why="stopped at its ${ATTEMPT_SECONDS} s limit"
  else
    why="exit ${status}"
  fi
  echo "::warning title=${TITLE}::attempt ${attempt} of 2 failed (${why}) after ${took} s, $(now)"
  if [ "${attempt}" -lt 2 ]; then
    if [ "${PROVE_STALL}" = "1" ]; then
      prove_stall_lock
    fi
    if [ "${APT_RECOVERY}" = "1" ]; then
      recover_apt "${before}"
    fi
    if [ "${WAIT_SECONDS}" -gt 0 ]; then
      sleep "${WAIT_SECONDS}"
    fi
  fi
done

echo "::error title=${TITLE}::both attempts failed (each at most ${ATTEMPT_SECONDS} s); the cause is in the attempts' output above"
exit 1
