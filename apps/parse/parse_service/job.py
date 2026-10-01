"""
The unprivileged parse job (Stage 3c, item 3, step 7).

With isolation on, before it reads a byte of its input (founder's item 1):
1. sets PR_SET_NO_NEW_PRIVS itself (setpriv already did; this does not rely
   on it);
2. loads the seccomp filter;
3. checks /proc/self/status shows `NoNewPrivs: 1` and `Seccomp: 2`.
If any step fails it exits with status 70 without reading the file: the
supervisor answers that as the service failing to isolate.

Then: one header line of JSON on stdin ({"filename": ..., "args": ...}),
the file's bytes until EOF, and one JSON answer on stdout. The filename is
built by the supervisor from the extension alone; the sender's own name
never reaches this process.

Exit statuses: 0 with an answer; 70 when this second hardening fails; 71
out of memory inside Python (the per-process backstop). Since Q13 (founder,
2026-10-01) neither decides an outcome: isolation is confirmed by
sandbox_init's "hardened" message before this process exists, so any
non-zero exit here is a parser failure, logged by name. A job killed by the
supervisor or by the kernel has no status of its own; the supervisor names
the cause from its own records.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys

EXIT_ISOLATION_FAILED = 70
EXIT_MEMORY = 71
PR_SET_NO_NEW_PRIVS = 38
HEADER_MAX_BYTES = 64 * 1024


def _status_fields() -> dict[str, str]:
    out = {}
    with open("/proc/self/status") as handle:
        for line in handle:
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip()
    return out


def harden() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS)")
    from parse_service import seccomp_filter

    seccomp_filter.load()
    status = _status_fields()
    if status.get("NoNewPrivs") != "1" or status.get("Seccomp") != "2":
        raise RuntimeError(
            f"hardening not in effect: NoNewPrivs={status.get('NoNewPrivs')} Seccomp={status.get('Seccomp')}"
        )


def _answer(answer: dict) -> None:
    out = sys.stdout.buffer
    out.write(json.dumps(answer, ensure_ascii=False).encode("utf-8"))
    out.flush()


def main(argv: list[str]) -> int:
    kind = argv[1]
    if os.environ.get("PARSE_ISOLATION", "on") == "on":
        try:
            harden()
        except Exception as exc:  # noqa: BLE001 -- never read the file un-hardened
            print(f"job hardening failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return EXIT_ISOLATION_FAILED

    header = json.loads(sys.stdin.buffer.readline(HEADER_MAX_BYTES) or b"{}")
    try:
        if kind.startswith("selftest:"):
            from parse_service import selftest_programs

            _answer(selftest_programs.run(kind.split(":", 1)[1], header.get("args") or {}))
            return 0
        content = sys.stdin.buffer.read()
        filename = str(header.get("filename") or "upload")
        if kind == "document":
            from parse_service.parsing import documents

            _answer(documents.parse(content, filename))
        elif kind == "table":
            from parse_service.parsing import tables

            _answer(tables.parse(content, filename))
        else:
            print(f"unknown job kind {kind!r}", file=sys.stderr)
            return 2
    except MemoryError:
        return EXIT_MEMORY
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
