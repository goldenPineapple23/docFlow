#!/usr/bin/env bash
# The API's real image answers on IPv4 and on IPv6 (D-227).
#
#   scripts/ci/api_image_listen.sh <image>
#
# Fly's edge dials a machine on IPv4 and `fly proxy` on its private IPv6
# address. A published port is IPv4 into the container, so it stands in for
# the edge here; IPv6 is read from inside the container, which Docker gives
# none by default (hence the sysctl). The control starts the same image with
# the command that failed on Fly on 2026-11-01, and it must fail here too, or
# this step can't see the fault. Each check reports its own evidence
# (RUNBOOK 1.6). No database is reachable, which /healthz answers anyway.
set -uo pipefail

IMAGE="$1"
PORT=18000
CONTROL_PORT=18001
NAMES=(docflow-api-listen docflow-api-listen-control)

cleanup() {
  docker rm -f "${NAMES[@]}" >/dev/null 2>&1
}
trap cleanup EXIT

start() {
  local name="$1" port="$2"; shift 2
  docker run -d --name "$name" --sysctl net.ipv6.conf.all.disable_ipv6=0 \
    -p "127.0.0.1:$port:8000" "$IMAGE" "$@" >/dev/null
}

ipv6_status() {
  docker exec "$1" python -c "
import http.client
try:
    c = http.client.HTTPConnection('::1', 8000, timeout=10)
    c.request('GET', '/healthz')
    print(c.getresponse().status)
except OSError:
    print(0)
" 2>/dev/null
}

ipv4_status() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 15 "http://127.0.0.1:$1/healthz"
}

# Waits for the server inside the container, on IPv6, which both starts have.
wait_for() {
  local name="$1"
  for _ in $(seq 1 60); do
    [ "$(ipv6_status "$name")" = "200" ] && return 0
    sleep 1
  done
  return 1
}

fail() {
  echo "::error title=api image listen::$1"
  for name in "${NAMES[@]}"; do
    echo "----- $name: container log -----"
    docker logs "$name" 2>&1 | tail -40
  done
  exit 1
}

start "${NAMES[0]}" "$PORT" || fail "the image's own command would not start"
wait_for "${NAMES[0]}" || fail "no 200 on IPv6 inside the container within 60 s"
status=$(ipv4_status "$PORT")
echo "the image's command: IPv6 200, IPv4 $status"
[ "$status" = "200" ] || fail "the image's command answers on IPv6 but not IPv4 (status $status)"

start "${NAMES[1]}" "$CONTROL_PORT" uvicorn app.main:app --host :: --port 8000 \
  || fail "the control would not start"
wait_for "${NAMES[1]}" || fail "the control: no 200 on IPv6 inside the container within 60 s"
status=$(ipv4_status "$CONTROL_PORT")
echo "the control, uvicorn --host ::: IPv6 200, IPv4 $status"
[ "$status" != "200" ] || fail "the control answered on IPv4, so this step can't see the fault"

echo "::notice title=api image listen::IPv4 and IPv6 answer 200; the IPv6-only control is refused on IPv4"
