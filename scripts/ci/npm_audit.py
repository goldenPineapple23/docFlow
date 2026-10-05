"""
Fail CI on any high or critical npm advisory that has no exception on file.

`npm audit --audit-level=high` is all or nothing: one advisory with no fix
fails the job until it is switched off, and switching it off hides the next
one. This check reads npm's own report and fails on every high or critical
advisory except the ones listed, one by one, in .github/audit-exceptions.txt
with the reason the founder accepted and a review date. Dev dependencies stay
in the audit.

Usage:
  npm audit --json > npm-audit.json || true
  python scripts/ci/npm_audit.py <suite-name> <npm-audit.json> [<audit-exceptions.txt>]

Each non-comment line of the exceptions file is
  <suite-name> <advisory id> review=<YYYY-MM-DD>  # reason
e.g.
  web GHSA-xxxx-xxxx-xxxx review=2026-11-05  # why this may pass

A report that can't be read, or that counts high or critical findings this
check can't trace to an advisory, fails: the audit never passes on a report it
doesn't understand. Stale exceptions (listed, but no longer reported) are
announced, not failed; delete them when seen. An exception on or past its
review date warns on every run.

Results are GitHub annotations, so they can be read from the run's summary
page without opening the log.
"""

import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DEFAULT_EXCEPTIONS = Path(__file__).resolve().parents[2] / ".github" / "audit-exceptions.txt"

FAILING = ("high", "critical")
_GHSA = re.compile(r"GHSA(?:-[0-9a-z]{4}){3}")
_LINE = re.compile(r"^(\S+)\s+(\S+)\s+review=(\d{4}-\d{2}-\d{2})$")


class UnreadableReport(Exception):
    pass


@dataclass(frozen=True)
class AuditException:
    advisory: str
    review: date
    reason: str


@dataclass(frozen=True)
class Advisory:
    advisory: str
    package: str
    severity: str
    title: str
    url: str


def read_exceptions(path: Path, suite: str) -> dict[str, AuditException]:
    found: dict[str, AuditException] = {}
    if not path.exists():
        return found
    for raw in path.read_text(encoding="utf-8").splitlines():
        entry, _, reason = raw.partition("#")
        entry = entry.strip()
        if not entry:
            continue
        match = _LINE.match(entry)
        if match is None or not reason.strip():
            raise SystemExit(f"malformed audit exception (needs an id, review=<date> and a reason): {raw!r}")
        if match.group(1) == suite:
            found[match.group(2)] = AuditException(
                match.group(2), date.fromisoformat(match.group(3)), reason.strip()
            )
    return found


def advisories(report: object) -> list[Advisory]:
    """Every advisory npm reports, once each. In npm's report a package's
    `via` holds either an advisory (an object) or the name of the package it
    inherits one from (a string), so the objects are the whole set."""
    if not isinstance(report, dict) or "error" in report:
        raise UnreadableReport("npm audit returned an error, not a report")
    packages = report.get("vulnerabilities")
    counts = (report.get("metadata") or {}).get("vulnerabilities")
    if not isinstance(packages, dict) or not isinstance(counts, dict):
        raise UnreadableReport("the report has no `vulnerabilities` or no `metadata.vulnerabilities`")
    found: dict[str, Advisory] = {}
    for package in packages.values():
        for via in (package or {}).get("via") or []:
            if not isinstance(via, dict):
                continue
            url = str(via.get("url") or "")
            match = _GHSA.search(url)
            advisory = match.group(0) if match else (url or f"npm:{via.get('source')}")
            found[advisory] = Advisory(
                advisory,
                str(via.get("name") or ""),
                str(via.get("severity") or "").lower(),
                str(via.get("title") or ""),
                url,
            )
    counted = sum(int(counts.get(level) or 0) for level in FAILING)
    if counted and not any(a.severity in FAILING for a in found.values()):
        raise UnreadableReport(
            f"the report counts {counted} high or critical findings but lists no such advisory"
        )
    return sorted(found.values(), key=lambda a: a.advisory)


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", " ").replace("\n", " ")


def check(suite: str, report: object, exceptions: dict[str, AuditException], today: date) -> int:
    try:
        found = advisories(report)
    except UnreadableReport as unreadable:
        print(f"::error title={suite}: dependency audit could not be read::{_escape(str(unreadable))}")
        return 1

    failing = [a for a in found if a.severity in FAILING]
    refused = [a for a in failing if a.advisory not in exceptions]
    passed = [a for a in failing if a.advisory in exceptions]
    reported = {a.advisory for a in found}

    for advisory in passed:
        allowed = exceptions[advisory.advisory]
        print(
            f"::notice title={suite}: audit exception in use::{advisory.advisory} "
            f"({advisory.package}, {advisory.severity}) passes by exception: "
            f"{_escape(allowed.reason)}. Review by {allowed.review.isoformat()}."
        )
        if today >= allowed.review:
            print(
                f"::warning title={suite}: audit exception due for review::{advisory.advisory} "
                f"({advisory.package}) was due for review on {allowed.review.isoformat()}. Remove it if a "
                "fix exists, or give it a new date and reason in .github/audit-exceptions.txt."
            )
    for stale in sorted(set(exceptions) - reported):
        print(
            f"::notice title={suite}: stale audit exception::{stale} is listed in "
            ".github/audit-exceptions.txt but npm no longer reports it. Delete the line."
        )
    for advisory in refused:
        print(
            f"::error title={suite}: {advisory.severity} advisory in {advisory.package}::"
            f"{advisory.advisory} {_escape(advisory.title)} {advisory.url}"
        )

    print(
        f"{suite}: {len(found)} advisories reported, {len(failing)} high or critical, "
        f"{len(passed)} passing by exception, {len(refused)} refused"
    )
    return 1 if refused else 0


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(__doc__, file=sys.stderr)
        return 2
    suite, report_path = argv[1], Path(argv[2])
    exceptions = read_exceptions(Path(argv[3]) if len(argv) == 4 else DEFAULT_EXCEPTIONS, suite)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as unreadable:
        print(
            f"::error title={suite}: dependency audit could not be read::"
            f"{report_path.name}: {_escape(type(unreadable).__name__)}"
        )
        return 1
    return check(suite, report, exceptions, date.today())


if __name__ == "__main__":
    sys.exit(main(sys.argv))
