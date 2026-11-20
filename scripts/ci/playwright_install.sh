#!/usr/bin/env bash
# Runs one Playwright install command with a time limit and one retry
# (D-238; D-247). The web jobs call it twice, as two steps: the system
# packages (`playwright install-deps`, apt as root) and the browser
# (`playwright install`, no root).
#
#   PW_TITLE="Playwright system packages" PW_APT_RECOVERY=1 \
#     scripts/ci/playwright_install.sh \
#     sudo env "PATH=$PATH" HOME=/root timeout --kill-after=10 __inner_limit__ \
#     ./node_modules/.bin/playwright install-deps chromium
#
# The arguments are the command, run as given. At most two attempts.
# Attempt 1 is stopped after PW_ATTEMPT1_SECONDS (each job's own figure;
# PW_ATTEMPT_SECONDS when unset), attempt 2 after PW_ATTEMPT_SECONDS (300).
# The packages command carries its own root `timeout`, 10 s shorter:
# Playwright then runs apt-get as root with no sudo of its own, so that
# timeout's kill of its process group reaches apt-get. On #580 a sudo
# started inside the install put apt-get out of reach of the non-root
# `timeout`, and the retry failed on the dpkg lock that apt-get still held
# (D-247). An argument that is exactly `__inner_limit__` is replaced, for each
# attempt, by that attempt's limit less 10 s, so the root `timeout` follows
# the attempt's limit. The limit an attempt prints, and the one a stopped
# attempt is said to have reached, is the one that fires first: the root
# `timeout`'s when the command carries it. (#595's web job said "stopped at
# its 300 s limit" after 290 s: the root `timeout`'s fixed 290 had fired.)
#
# PW_APT_RECOVERY=1 (the packages step): each attempt first prints the
# runner's apt mirror list (/etc/apt/apt-mirrors.txt). After a failed attempt,
# any apt-get or dpkg that wasn't running before it is killed by process ID;
# then the retry waits up to PW_LOCK_WAIT_SECONDS (120) for the dpkg lock to
# be free and finishes any unpack an interrupted dpkg left
# (`dpkg --configure -a`). When attempt 1 stopped at its limit, attempt 2 runs
# without the list's first-priority mirror (scripts/ci/apt_next_mirror.sh;
# nothing is removed when only one mirror is listed). The archives attempt 1
# downloaded stay in apt's cache. #582's web job spent both attempts
# downloading from one slow mirror (D-247 item 8).
#
# PW_PROVE_STALL=1 is the proof run of D-247 only, never a normal run: the
# first attempt goes through a local proxy that accepts and never answers,
# with apt's timeouts above the attempt's limit, so it stops at its limit;
# then a background process holds the dpkg lock for 40 s as the second
# begins.
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
ATTEMPT1_SECONDS="${PW_ATTEMPT1_SECONDS:-${ATTEMPT_SECONDS}}"
INNER_TOKEN="__inner_limit__"
INNER_MARGIN=10
WAIT_SECONDS="${PW_WAIT_SECONDS:-0}"
LOCK_WAIT_SECONDS="${PW_LOCK_WAIT_SECONDS:-120}"
APT_RECOVERY="${PW_APT_RECOVERY:-0}"
PROVE_STALL="${PW_PROVE_STALL:-0}"
DPKG_LOCK=/var/lib/dpkg/lock-frontend
PROVE_CONF=/etc/apt/apt.conf.d/99zz-ci-prove-stall
PROVE_PORT=18080
PROVE_LISTENER=""
MIRRORS=/etc/apt/apt-mirrors.txt
NEXT_MIRROR="$(cd "$(dirname "$0")" && pwd)/apt_next_mirror.sh"

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

show_mirrors() {
  # $1: the attempt number.
  if [ -f "${MIRRORS}" ]; then
    echo "${TITLE}: the mirror list for attempt $1 (${MIRRORS}):"
    cat "${MIRRORS}"
  else
    echo "${TITLE}: no ${MIRRORS} on this runner"
  fi
}

drop_first_mirror() {
  local next
  if [ ! -f "${MIRRORS}" ]; then
    echo "${TITLE}: no ${MIRRORS}; nothing removed"
    return
  fi
  if ! next=$(bash "${NEXT_MIRROR}" "${MIRRORS}"); then
    echo "${TITLE}: the mirror list could not be read; nothing removed"
    return
  fi
  if [ "${next}" = "$(cat "${MIRRORS}")" ]; then
    return
  fi
  printf '%s\n' "${next}" | sudo tee "${MIRRORS}" >/dev/null
  echo "${TITLE}: attempt 1 stopped at its limit; attempt 2 runs without its first-priority mirror"
}

prove_stall_start() {
  python3 -c "
import socket
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', ${PROVE_PORT}))
s.listen(64)
held = []
while True:
    held.append(s.accept())
" &
  PROVE_LISTENER=$!
  sleep 1
  printf '%s\n' "Acquire::http::Proxy \"http://127.0.0.1:${PROVE_PORT}\";" \
    "Acquire::https::Proxy \"http://127.0.0.1:${PROVE_PORT}\";" \
    'Acquire::Retries "0";' 'Acquire::http::Timeout "600";' 'Acquire::https::Timeout "600";' \
    | sudo tee "${PROVE_CONF}" >/dev/null
  echo "::notice title=${TITLE} (proof)::attempt 1 goes through a proxy that never answers, $(now)"
}

prove_stall_lock() {
  if [ -n "${PROVE_LISTENER}" ]; then
    kill "${PROVE_LISTENER}" 2>/dev/null || true
  fi
  sudo rm -f "${PROVE_CONF}"
  sudo python3 -c "import fcntl, time; f = open('${DPKG_LOCK}', 'w'); fcntl.lockf(f, fcntl.LOCK_EX); time.sleep(40)" &
  sleep 2
  echo "::notice title=${TITLE} (proof)::the dpkg lock is held for 40 s from $(now)"
}

for seconds in "${ATTEMPT1_SECONDS}" "${ATTEMPT_SECONDS}"; do
  if ! [[ "${seconds}" =~ ^[0-9]+$ ]] || [ "${seconds}" -le "${INNER_MARGIN}" ]; then
    echo "::error title=${TITLE}::an attempt limit must be a whole number of seconds above ${INNER_MARGIN}"
    exit 2
  fi
done

for attempt in 1 2; do
  limit="${ATTEMPT_SECONDS}"
  if [ "${attempt}" -eq 1 ]; then
    limit="${ATTEMPT1_SECONDS}"
  fi
  inner=$((limit - INNER_MARGIN))
  command=()
  has_inner=0
  for arg in "$@"; do
    if [ "${arg}" = "${INNER_TOKEN}" ]; then
      command+=("${inner}")
      has_inner=1
    else
      command+=("${arg}")
    fi
  done
  stop_at="${limit}"
  if [ "${has_inner}" -eq 1 ]; then
    stop_at="${inner}"
  fi
  before=""
  if [ "${APT_RECOVERY}" = "1" ]; then
    before=$(apt_pids)
    show_mirrors "${attempt}"
  fi
  if [ "${PROVE_STALL}" = "1" ] && [ "${attempt}" -eq 1 ]; then
    prove_stall_start
  fi
  started=$(date +%s)
  echo "${TITLE}, attempt ${attempt} of 2 (at most ${stop_at} s), $(now): ${command[*]}"
  timeout --kill-after=10 "${limit}" "${command[@]}"
  status=$?
  took=$(( $(date +%s) - started ))
  if [ "${status}" -eq 0 ]; then
    echo "${TITLE}, attempt ${attempt} of 2: done in ${took} s, $(now)"
    exit 0
  fi
  if [ "${status}" -eq 124 ] || [ "${status}" -eq 137 ]; then
    # The root `timeout` (stop_at) fires first; the outer one, at `limit`,
    # is the backstop, and only it lets an attempt run that long.
    if [ "${has_inner}" -eq 1 ] && [ "${took}" -lt "${limit}" ]; then
      why="stopped at its ${inner} s limit"
    else
      why="stopped at its ${limit} s limit"
    fi
  else
    why="exit ${status}"
  fi
  echo "::warning title=${TITLE}::attempt ${attempt} of 2 failed (${why}) after ${took} s, $(now)"
  if [ "${attempt}" -lt 2 ]; then
    if [ "${APT_RECOVERY}" = "1" ] && { [ "${status}" -eq 124 ] || [ "${status}" -eq 137 ]; }; then
      drop_first_mirror
    fi
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

echo "::error title=${TITLE}::both attempts failed (attempt 1 at most ${ATTEMPT1_SECONDS} s, attempt 2 at most ${ATTEMPT_SECONDS} s); the cause is in the attempts' output above"
exit 1
