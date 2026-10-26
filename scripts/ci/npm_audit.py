"""
The web dependency audit: a high or critical advisory in a runtime dependency
blocks; one only in dev dependencies warns (founder, 2026-10-23; D-219).

npm's report doesn't say which tree an advisory came through, so the check
reads two: the whole audit (`npm audit`) and the runtime audit (`npm audit
--omit=dev`). An advisory in the runtime report is a runtime finding; one in
the whole report only is dev-only.

Whether a runtime finding fails the step depends on `--blocking`:
  yes: it is an ::error, and the step fails;
  no:  it is a ::warning, and the step passes.
CI decides with scripts/ci/deps_changed.sh: blocking only when
apps/web/package.json or package-lock.json changed, so an advisory published
against an unchanged lock file never turns an unrelated pull request red. The
daily audit (.github/workflows/dependency-audit.yml) and the check before a
deploy (scripts/ci/predeploy_audit.sh) always block.

A dev-only finding is always a ::warning and never fails. There are no
exceptions: the exceptions file and its code are retired (founder,
2026-10-23). A runtime advisory is fixed, never excepted.

A report that can't be read, or that counts high or critical findings this
check can't trace to an advisory, fails in every mode: the audit never passes
on a report it doesn't understand.

Usage:
  npm audit --json > npm-audit.json || true
  npm audit --omit=dev --json > npm-audit-runtime.json || true
  python scripts/ci/npm_audit.py <suite-name> <npm-audit.json> <npm-audit-runtime.json> \
      --blocking yes|no

Results are GitHub annotations, so they can be read from the run's summary
page without opening the log.
"""

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

FAILING = ("high", "critical")
_GHSA = re.compile(r"GHSA(?:-[0-9a-z]{4}){3}")


class UnreadableReport(Exception):
    pass


@dataclass(frozen=True)
class Advisory:
    advisory: str
    package: str
    severity: str
    title: str
    url: str


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


def check(suite: str, report: object, runtime_report: object, blocking: bool) -> int:
    """`report` is the whole audit (dev dependencies included); `runtime_report`
    is `npm audit --omit=dev`, which says which findings are runtime ones."""
    try:
        found = advisories(report)
    except UnreadableReport as unreadable:
        print(f"::error title={suite}: dependency audit could not be read::{_escape(str(unreadable))}")
        return 1
    try:
        at_runtime = advisories(runtime_report)
    except UnreadableReport as unreadable:
        print(
            f"::error title={suite}: runtime dependency audit could not be read::"
            f"{_escape(str(unreadable))}"
        )
        return 1

    runtime_ids = {a.advisory for a in at_runtime}
    runtime = [a for a in at_runtime if a.severity in FAILING]
    dev_only = [a for a in found if a.severity in FAILING and a.advisory not in runtime_ids]

    for advisory in runtime:
        text = f"{advisory.advisory} {_escape(advisory.title)} {advisory.url}"
        if blocking:
            print(f"::error title={suite}: {advisory.severity} advisory in {advisory.package}::{text}")
        else:
            print(
                f"::warning title={suite}: {advisory.severity} advisory in {advisory.package} "
                f"(not blocking: no package file changed)::{text}"
            )
    for advisory in dev_only:
        print(
            f"::warning title={suite}: dev-only {advisory.severity} advisory in {advisory.package}::"
            f"{advisory.advisory} {_escape(advisory.title)} {advisory.url}"
        )

    print(
        f"{suite}: {len(found)} advisories reported, {len(runtime) + len(dev_only)} high or critical "
        f"({len(runtime)} in runtime dependencies, {len(dev_only)} dev-only); "
        f"blocking: {'yes' if blocking else 'no'}"
    )
    return 1 if blocking and runtime else 0


def main(argv: list[str]) -> int:
    if len(argv) != 6 or argv[4] != "--blocking" or argv[5] not in ("yes", "no"):
        print(__doc__, file=sys.stderr)
        return 2
    suite = argv[1]
    reports: list[object] = []
    for report_path in (Path(argv[2]), Path(argv[3])):
        try:
            reports.append(json.loads(report_path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as unreadable:
            print(
                f"::error title={suite}: dependency audit could not be read::"
                f"{report_path.name}: {_escape(type(unreadable).__name__)}"
            )
            return 1
    return check(suite, reports[0], reports[1], argv[5] == "yes")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
