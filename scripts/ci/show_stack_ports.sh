#!/usr/bin/env bash
# What holds the local Supabase stack's ports on this runner, printed around
# "Start the local Supabase stack" (D-199 flake 3; D-218; D-231). Read-only:
# it prints and changes nothing.
#
# The stack's ports are 25320 to 25329 (supabase/config.toml; D-243). They
# were the CLI's defaults, 54320 to 54329, until an outgoing connection's
# automatically assigned local port took one before the stack could bind it
# (flakes 10, 11 and 14). Nothing on the runner is assigned a port in the new
# block automatically, so a holder named here is a program that asked for
# that port itself.
#
#   scripts/ci/show_stack_ports.sh before|after
#
# before: the runner image, every socket on 25320 to 25329 with its process,
#         and any container publishing one of them.
# after:  (only after a failed start) the same sockets, and every container,
#         running or stopped. On a fresh runner the likeliest holder is
#         supabase start's own half-started container from a retry. If
#         supabase start removed its containers when it failed, and nothing
#         else holds the port, this prints nothing, and that is itself a
#         finding.
#
# Listeners are printed first, then every socket in any state: a connection
# whose local port is one of these isn't a listener.
# sudo, because `ss -p` names only the runner user's own processes, and
# Docker's port proxy runs as root.
#
# It never fails the job: every command ends in `|| true`, and each step
# that calls it has continue-on-error: true.
set -uo pipefail

WHEN="${1:-}"
if [ "$WHEN" != "before" ] && [ "$WHEN" != "after" ]; then
  echo "usage: show_stack_ports.sh before|after" >&2
  exit 2
fi
FORMAT='{{.ID}} {{.Image}} {{.Names}} {{.Status}} {{.Ports}}'
PORTS='( sport >= :25320 and sport <= :25329 )'

if [ "$WHEN" = "before" ]; then
  echo "Runner image: ${ImageOS:-unknown} ${ImageVersion:-unknown}"
  echo "Automatic port range (the stack's ports must be outside it):"
  sysctl net.ipv4.ip_local_port_range || true
fi

echo "Listening on 25320 to 25329, TCP and UDP, with the owning process (only the column header below = none):"
sudo ss -ltunp "$PORTS" || true
echo "Every socket on 25320 to 25329 in any state, with its process (only the column header below = none):"
sudo ss -tuanp "$PORTS" || true

if [ "$WHEN" = "before" ]; then
  echo "Containers publishing a port in 25320 to 25329:"
  docker ps -a --filter publish=25320-25329 --format "$FORMAT" || true
else
  echo "Every container, running or stopped:"
  docker ps -a --format "$FORMAT" || true
fi
