#!/usr/bin/env bash
# What holds the local Supabase stack's ports on this runner, printed around
# "Start the local Supabase stack" (D-199 flake 3; D-218). Since D-231 it
# covers the stack's whole block, 54320 to 54327 (flakes 10 and 11 were the
# mail catcher's port 54324), and names the runner image. Read-only: it
# prints and changes nothing.
#
#   scripts/ci/show_port_54322.sh before|after
#
# before: the runner image, every socket on 54320 to 54327 with its process,
#         and any container publishing 54322.
# after:  (only after a failed start) the same sockets, and every container,
#         running or stopped. On a fresh runner the likeliest holder is
#         supabase start's own half-started container from a retry. If
#         supabase start removed its containers when it failed, and nothing
#         else holds the port, this prints nothing, and that is itself a
#         finding.
#
# Listeners are printed first, then every socket in any state: a connection
# whose automatically assigned local port is one of these isn't a listener.
# sudo, because `ss -p` names only the runner user's own processes, and
# Docker's port proxy runs as root.
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
PORTS='( sport >= :54320 and sport <= :54327 )'

if [ "$WHEN" = "before" ]; then
  echo "Runner image: ${ImageOS:-unknown} ${ImageVersion:-unknown}"
fi

echo "Listening on 54320 to 54327, TCP and UDP, with the owning process (only the column header below = none):"
sudo ss -ltunp "$PORTS" || true
echo "Every socket on 54320 to 54327 in any state, with its process (only the column header below = none):"
sudo ss -tuanp "$PORTS" || true

if [ "$WHEN" = "before" ]; then
  echo "Containers publishing 54322:"
  docker ps -a --filter publish=54322 --format "$FORMAT" || true
else
  echo "Every container, running or stopped:"
  docker ps -a --format "$FORMAT" || true
fi
