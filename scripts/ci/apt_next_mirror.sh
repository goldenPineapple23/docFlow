#!/usr/bin/env bash
# Prints an apt mirror list without its first-priority mirror (D-247 item 8).
# The list is apt's mirror-method format: one mirror URI a line, then
# tab-separated fields such as `priority:N`. The first-priority mirror is the
# line with the lowest `priority:N`, or the first mirror line when no line has
# one. A list with one mirror or none is printed unchanged. Blank and `#`
# lines are kept. What it did goes to stderr. It reads only the file it is
# given and writes nothing: the caller decides what to do with the output.
#
#   scripts/ci/apt_next_mirror.sh /etc/apt/apt-mirrors.txt
set -euo pipefail

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "apt_next_mirror.sh: give one mirror list file" >&2
  exit 2
fi

awk '
  { line[NR] = $0 }
  /^[[:space:]]*(#|$)/ { next }
  {
    mirrors++
    if (first_line == "") first_line = NR
    if (match($0, /priority:[0-9]+/)) {
      p = substr($0, RSTART + 9, RLENGTH - 9) + 0
      if (best_line == "" || p < best_p) { best_line = NR; best_p = p }
    }
  }
  END {
    if (mirrors <= 1) {
      print "only one mirror listed; nothing removed" > "/dev/stderr"
      for (i = 1; i <= NR; i++) print line[i]
      exit 0
    }
    drop = (best_line != "") ? best_line : first_line
    print "removed the first-priority mirror: " line[drop] > "/dev/stderr"
    for (i = 1; i <= NR; i++) if (i != drop) print line[i]
  }
' "$1"
