#!/usr/bin/env bash
# The parse service's checks against its real image (Stage 3c test table:
# E1, E4, the A/S/B self-tests, and starting the service for the HTTP tests).
#
#   scripts/ci/parse_image.sh <image> e1|e4|selftest|serve
#
# Each step reports its own evidence (RUNBOOK 1.6) and exits non-zero on a
# failure. The token is a throwaway CI value, never a real secret.
set -uo pipefail

IMAGE="$1"
STEP="$2"
TOKEN="${PARSE_SERVICE_TOKEN:-ci-parse-token-not-a-secret}"

# Starts a container that must refuse to start; asserts it exits on its own,
# says why, and never says it is listening.
must_refuse() {
  local label="$1"; shift
  local cid status logs
  cid=$(docker run -d -e DOCFLOW_ENV=production -e PARSE_SERVICE_TOKEN="$TOKEN" "$@" "$IMAGE")
  status=$(timeout 180 docker wait "$cid" 2>/dev/null || echo "still-running")
  logs=$(docker logs "$cid" 2>&1)
  docker rm -f "$cid" >/dev/null 2>&1
  echo "----- $label: container log -----"
  echo "$logs"
  echo "----- $label: exit status $status -----"
  if [ "$status" = "still-running" ] || [ "$status" = "0" ]; then
    echo "::error::$label FAIL: the service did not exit on its own (status $status)"
    return 1
  fi
  if ! grep -q "refused to start" <<<"$logs"; then
    echo "::error::$label FAIL: no refusal in the log"
    return 1
  fi
  if grep -q "listening on port" <<<"$logs"; then
    echo "::error::$label FAIL: it opened its port"
    return 1
  fi
  echo "$label PASS: refused to start and never opened its port (exit $status)"
}

case "$STEP" in
  e1)
    # Production mode without the privileges a Fly VM's root has: no
    # namespaces or cgroups can be created, so it must not serve.
    must_refuse "E1 (unprivileged container)"
    ;;
  e4)
    # Every privilege, but the cgroup filesystem read-only: no job cgroup can
    # be created, so there is no working memory cap.
    must_refuse "E4 (cgroup filesystem read-only)" --privileged -v /sys/fs/cgroup:/sys/fs/cgroup:ro
    ;;
  selftest)
    docker run --rm --privileged "$IMAGE" python -m parse_service.selftest all | tee parse-selftest.txt
    exit "${PIPESTATUS[0]}"
    ;;
  serve)
    docker rm -f docflow-parse >/dev/null 2>&1
    docker run -d --name docflow-parse --privileged -p 127.0.0.1:8100:8100 \
      -e DOCFLOW_ENV=production -e PARSE_SERVICE_TOKEN="$TOKEN" "$IMAGE" >/dev/null
    for _ in $(seq 1 120); do
      if curl -fsS http://127.0.0.1:8100/health >/dev/null 2>&1; then
        echo "----- E3: the startup log -----"
        docker logs docflow-parse 2>&1
        if ! docker logs docflow-parse 2>&1 | grep -q "canary: PASS"; then
          echo "::error::E3 FAIL: healthy without a canary PASS line"
          exit 1
        fi
        echo "the parse service is up (production mode, isolation on)"
        exit 0
      fi
      sleep 1
    done
    echo "::error::the parse service did not become healthy"
    docker logs docflow-parse 2>&1
    exit 1
    ;;
  *)
    echo "unknown step $STEP"
    exit 2
    ;;
esac
