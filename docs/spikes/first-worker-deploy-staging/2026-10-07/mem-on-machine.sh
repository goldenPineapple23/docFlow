#!/bin/sh
echo "== machine: $FLY_MACHINE_ID date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "usage_in_bytes:     $(cat /sys/fs/cgroup/memory/memory.usage_in_bytes)"
echo "max_usage_in_bytes: $(cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes)"
grep -E "^(MemTotal|MemAvailable|SwapTotal)" /proc/meminfo
echo "== ps (rss in KiB)"
ps -o pid,ppid,rss,args -u docflow 2>/dev/null | cut -c1-150 || echo "ps not available"
echo "== per process: pid ppid VmRSS VmHWM (KiB) cmd"
for d in /proc/[0-9]*; do
  p=${d#/proc/}
  [ "$(stat -c %U "$d" 2>/dev/null)" = "docflow" ] || continue
  rss=$(awk '/^VmRSS/{print $2}' "$d/status" 2>/dev/null); hwm=$(awk '/^VmHWM/{print $2}' "$d/status" 2>/dev/null); pp=$(awk '/^PPid/{print $2}' "$d/status" 2>/dev/null)
  echo "$p $pp $rss $hwm $(tr '\0' ' ' < "$d/cmdline" | cut -c1-110)"
done
