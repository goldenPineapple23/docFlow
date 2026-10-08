#!/bin/bash
# Drill 3 (RUNBOOK 9.7 step 12.3): kill the documents worker's Celery main
# process once, then read what the machine, the log, /healthz and the alerts show.
set -u
N="$1"
REPO="/c/Users/NK/Agentic Workflow Port/DocFlow/IDP Engine"
cat > /tmp/kill-documents.sh <<'EOF'
for d in /proc/[0-9]*; do
  p=${d#/proc/}
  cmd=$(tr '\0' ' ' < $d/cmdline 2>/dev/null)
  case "$cmd" in *"-Q interactive,bulk"*) ;; *) continue ;; esac
  pp=$(awk '/^PPid/{print $2}' $d/status)
  case "$(tr '\0' ' ' < /proc/$pp/cmdline 2>/dev/null)" in *run_workers*) ;; *) continue ;; esac
  echo "killing documents main pid $p (parent $pp) at $(date -u +%H:%M:%S.%3NZ) on $FLY_MACHINE_ID"
  kill -9 $p
  exit 0
done
echo "documents main process not found"; exit 3
EOF
B64=$(base64 -w0 /tmp/kill-documents.sh)
LIVE="$(dirname "$0")/drill3_live_kill$N.log"
fly logs --app docflow-worker-staging > "$LIVE" 2>&1 &
LOGPID=$!
sleep 4
echo "== kill $N issued $(date -u +%H:%M:%SZ)"
fly ssh console --app docflow-worker-staging --process-group worker -C "sh -c 'echo $B64 | base64 -d | sh'" 2>&1 | grep -v Connecting
sleep 45
echo "== machine, $(date -u +%H:%M:%SZ)"
fly machine list --app docflow-worker-staging --json | "$REPO/apps/api/.venv/Scripts/python.exe" -c "
import json,sys,datetime
for m in json.load(sys.stdin):
    ev = [(e['type'], e['status'], datetime.datetime.fromtimestamp(e['timestamp']/1000, datetime.UTC).strftime('%H:%M:%S')) for e in m.get('events', [])[:4]]
    print(' ', m['id'], m['config']['metadata']['fly_process_group'], m['state'], ev)"
kill $LOGPID 2>/dev/null
echo "== log (live capture, $(wc -l < "$LIVE") lines)"
cat "$LIVE" | sed 's/\x1b\[[0-9;]*m//g' | grep -E "run_workers|exited with|Main child exited|restart|OOM|oom| ready\." | tail -12 | cut -c1-190
fly machine start 8e7d7eb762d918 --app docflow-api-staging >/dev/null 2>&1
for i in $(seq 1 40); do H=$(curl -s -m 4 http://127.0.0.1:18000/healthz) && [ -n "$H" ] && break; sleep 1; done
echo "== healthz $(date -u +%H:%M:%SZ): $H"
cd "$REPO" && apps/api/.venv/Scripts/python.exe -c "
from docflow_core.db import platform_session, use_own_login
from sqlalchemy import text
use_own_login('admin')
with platform_session() as s:
    print('== worker_restarting alerts, last 2 h:', [(r[0], r[1].strftime('%H:%M:%S'), r[2], r[3]) for r in s.execute(text(\"select a.severity, a.created_at, o.status, a.payload::text from founder_alerts a left join email_outbox o on o.id = a.email_outbox_id where a.type = 'worker_restarting' and a.created_at > now() - interval '2 hours' order by a.created_at\"))])
"
