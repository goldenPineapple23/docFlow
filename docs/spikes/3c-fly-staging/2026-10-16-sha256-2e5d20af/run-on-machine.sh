#!/bin/sh
# Runs on docflow-parse-staging (RUNBOOK 8.1), PR 1b-i gate, 2026-10-16.
# As the 3c runs' script, without A4 and the throwaway app's DNS name
# (founder: gate only, no A4, no throwaway app). Targets are resolved here,
# outside the sandbox, before any job runs (D-150's rule).
set -u
PY=/opt/parse/venv/bin/python
echo "== machine: $FLY_MACHINE_ID image: $FLY_IMAGE_REF"
echo "== date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
$PY - <<'EOF' > /tmp/targets.json
import json, socket
def v6(host):
    return socket.getaddrinfo(host, None, socket.AF_INET6, socket.SOCK_STREAM)[0][4][0]
tcp = [["A3:upstash", v6("fly-docflow-staging-redis.upstash.io"), 6379, 6]]
print(json.dumps({"tcp": tcp, "dns": ["fly-docflow-staging-redis.upstash.io"]}))
EOF
echo "== targets:"; cat /tmp/targets.json; echo
SELFTEST_SUPABASE_HOST=ovntvsmhekqefsrpjtih.supabase.co \
SELFTEST_SUPABASE_DB_HOST=aws-0-us-east-1.pooler.supabase.com \
SELFTEST_SUPERVISOR_PORT=8100 \
  $PY -m parse_service.selftest all --targets /tmp/targets.json
echo "== selftest exit=$?"
echo "== date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
