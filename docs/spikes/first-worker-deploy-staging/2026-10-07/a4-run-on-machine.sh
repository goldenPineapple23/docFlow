#!/bin/sh
# Runs on docflow-parse-staging (RUNBOOK 9.7 step 10): A4 against the real
# staging API and worker. Targets are fixed addresses, checked from outside
# the sandbox first (the control), then from inside a job.
set -u
PY=/opt/parse/venv/bin/python
echo "== machine: $FLY_MACHINE_ID image: $FLY_IMAGE_REF"
echo "== date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > /tmp/a4-targets.json <<'EOF'
{"tcp": [
  ["A4:api-private-8000", "fdaa:cb:76d5:a7b:56e:c63d:1351:2", 8000, 6],
  ["A4:worker-private-22-fly-ssh", "fdaa:cb:76d5:a7b:268:6a10:d63d:2", 22, 6]
], "dns": ["docflow-api-staging.internal", "docflow-worker-staging.internal"]}
EOF
echo "== targets:"; cat /tmp/a4-targets.json; echo
SELFTEST_SUPABASE_HOST=ovntvsmhekqefsrpjtih.supabase.co \
SELFTEST_SUPABASE_DB_HOST=aws-0-us-east-1.pooler.supabase.com \
SELFTEST_SUPERVISOR_PORT=8100 \
  $PY -m parse_service.selftest A --targets /tmp/a4-targets.json
echo "== selftest exit=$?"
echo "== date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
