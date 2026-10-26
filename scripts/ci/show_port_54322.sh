#!/usr/bin/env bash
# What holds port 54322 (the local Supabase stack's database port) on this
# runner, printed around "Start the local Supabase stack" (D-199 flake 3;
# D-218). Read-only: it prints and changes nothing.
#
#   scripts/ci/show_port_54322.sh before|after
#
# before: what is listening on 54322, and any container publishing it.
# after:  (only after a failed start) what is listening on 54322, and every
#         container, running or stopped. On a fresh runner the likeliest
#         holder is supabase start's own half-started db container from a
#         retry. If supabase start removed its containers when it failed,
#         this prints nothing, and that is itself a finding.
#
# It never fails the job: every command ends in `|| true`, and each step
# that calls it has continue-on-error: true.
set -uo pipefail

WHEN="${1:-}"
if [ "$WHEN" != "before" ] && [ "$WHEN" != "after" ]; then
  echo "usage: show_port_54322.sh before|after" >&2
  exit 2
fi
FORMAT='{{.ID}} {{.Image}} {{.Names}} {{.Status}} {{.Ports}}'

echo "Listening on 54322 (nothing printed below = the port is free):"
sudo ss -ltnp 'sport = :54322' || true

if [ "$WHEN" = "before" ]; then
  echo "Containers publishing 54322:"
  docker ps -a --filter publish=54322 --format "$FORMAT" || true
else
  echo "Every container, running or stopped:"
  docker ps -a --format "$FORMAT" || true
fi
