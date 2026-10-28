"""
The Python dependency audit for one app (founder, 2026-10-23; D-219): reads
pip-audit's JSON reports and turns them into GitHub annotations.

Usage:
  pip-audit -r <requirements file> --no-deps --disable-pip --strict \
      --progress-spinner off -f json -o <report.json> || true
  python scripts/ci/pip_audit.py <app> --blocking yes|no runtime=<report.json> [dev=<report.json>]

runtime: the requirements an app ships: its lock, which its image installs
whole (for core, the worker's lock, which is what core's job installs). A
known vulnerability there:
  --blocking yes: an ::error, and the step fails;
  --blocking no:  a ::warning, and the step passes.
CI decides with scripts/ci/deps_changed.sh: blocking only when that app's
requirements files changed. The daily audit and the check before a deploy
always block.

dev: the app's requirements-dev.txt (for core, the worker's), which is never
in an image (D-225). A known vulnerability there is always a ::warning.

For Python, "blocks" means any known vulnerability of any severity, with no
exceptions mechanism (founder, 2026-10-23): pip-audit reports no severity.

In every mode, a report that is missing or unreadable, or that lists a
package pip-audit couldn't audit, is an ::error and fails: the audit never
passes on a report it doesn't understand. pip-audit's own exit code is
dropped (`|| true`); this script decides.
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path


class UnreadableReport(Exception):
    pass


@dataclass(frozen=True)
class Finding:
    package: str
    version: str
    vuln_id: str
    aliases: tuple[str, ...]
    fix_versions: tuple[str, ...]

    def text(self) -> str:
        aliases = f" ({', '.join(self.aliases)})" if self.aliases else ""
        fixed = f"fixed in {', '.join(self.fix_versions)}" if self.fix_versions else "no fixed version yet"
        return f"{self.package} {self.version}: {self.vuln_id}{aliases}, {fixed}"


def findings(report: object) -> tuple[int, list[Finding]]:
    """The number of packages audited, and every known vulnerability."""
    if isinstance(report, UnreadableReport):
        raise report
    if not isinstance(report, dict) or not isinstance(report.get("dependencies"), list):
        raise UnreadableReport("the report has no `dependencies` list")
    found: list[Finding] = []
    for dep in report["dependencies"]:
        if not isinstance(dep, dict) or not dep.get("name"):
            raise UnreadableReport("a dependency entry has no name")
        if "skip_reason" in dep:
            raise UnreadableReport(f"{dep['name']} was not audited: {dep['skip_reason']}")
        vulns = dep.get("vulns")
        if not dep.get("version") or not isinstance(vulns, list):
            raise UnreadableReport(f"{dep['name']} has no version or no `vulns` list")
        for vuln in vulns:
            if not isinstance(vuln, dict) or not vuln.get("id"):
                raise UnreadableReport(f"{dep['name']} lists a vulnerability with no id")
            found.append(
                Finding(
                    str(dep["name"]),
                    str(dep["version"]),
                    str(vuln["id"]),
                    tuple(str(a) for a in vuln.get("aliases") or []),
                    tuple(str(v) for v in vuln.get("fix_versions") or []),
                )
            )
    return len(report["dependencies"]), found


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", " ").replace("\n", " ")


def check(app: str, reports: dict[str, object], blocking: bool) -> int:
    """`reports` maps "runtime" (required) and "dev" (optional) to pip-audit's
    parsed JSON, or to an UnreadableReport when the file couldn't be read."""
    failed = False
    for kind in ("runtime", "dev"):
        if kind not in reports:
            continue
        label = app if kind == "runtime" else f"{app} (dev tools)"
        try:
            audited, found = findings(reports[kind])
        except UnreadableReport as unreadable:
            print(f"::error title={label}: dependency audit could not be read::{_escape(str(unreadable))}")
            failed = True
            continue
        for finding in found:
            if kind == "dev":
                title = f"{app}: known vulnerability in a dev tool (warning only)"
                print(f"::warning title={title}::{_escape(finding.text())}")
            elif blocking:
                title = f"{app}: known vulnerability in a runtime dependency"
                print(f"::error title={title}::{_escape(finding.text())}")
                failed = True
            else:
                title = (
                    f"{app}: known vulnerability in a runtime dependency "
                    "(not blocking: no requirements file changed)"
                )
                print(f"::warning title={title}::{_escape(finding.text())}")
        packages = len({f.package for f in found})
        if kind == "dev":
            mode = "warning only"
        elif blocking:
            mode = "blocking: yes"
        else:
            mode = "blocking: no (requirements files unchanged)"
        print(
            f"::notice title={label}: dependency audit::{audited} packages audited, "
            f"{packages} with a known vulnerability; {mode}"
        )
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    usage_ok = len(argv) >= 5 and argv[2] == "--blocking" and argv[3] in ("yes", "no")
    paths: dict[str, Path] = {}
    for arg in argv[4:] if usage_ok else []:
        kind, sep, path = arg.partition("=")
        if not sep or kind not in ("runtime", "dev") or kind in paths or not path:
            usage_ok = False
            break
        paths[kind] = Path(path)
    if not usage_ok or "runtime" not in paths:
        print(__doc__, file=sys.stderr)
        return 2
    app = argv[1]
    reports: dict[str, object] = {}
    for kind, path in paths.items():
        try:
            reports[kind] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as unreadable:
            reports[kind] = UnreadableReport(f"{path.name}: {type(unreadable).__name__}")
    return check(app, reports, argv[3] == "yes")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
