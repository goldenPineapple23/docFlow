"""
The image check for one app (founder, 2026-10-28; D-225 item 6): builds what
the app's Dockerfile installs, on its own, and checks that it is whole.

Usage:
  python scripts/ci/image_check.py <api|worker> [--repo <folder>]

The steps are read from apps/<app>/Dockerfile itself, not copied here: its
COPY lines into /opt/<app>/, its `python3 -m venv /opt/<app>/venv` and its two
`/opt/<app>/venv/bin/pip install ...` lines. They run unchanged in a new
folder under $RUNNER_TEMP (the system temp folder when that is unset), with
only /opt/<app> mapped to it; `python3` is the Python running this script.
A pip line, a venv line or a COPY it doesn't know refuses the run (exit 2),
so the check can't drift from the Dockerfile.

Then, failing (exit 1) on any of these:
  - `pip check` finds a broken requirement;
  - a package named in apps/<app>/requirements-dev.txt is installed;
  - the CMD's entry module, or any module of `app` or `docflow_core`,
    fails to import, run from the image's WORKDIR;
  - the imports loaded a dev tool.

What it can't see: the images' own Python (Debian's) and pip, and imports
made inside a function (D-225 items 5 and 11). --repo is for the recorded
negative controls: a scratch copy of the repository with a changed lock.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
APPS = ("api", "worker")
DEV_TOOL_MODULES = ("pytest", "_pytest", "mypy", "ruff", "coverage", "hypothesis", "pygments")

# Runs inside the new venv, from the image's WORKDIR. Every module of `app`
# and `docflow_core` is imported; one that fails, or exits, is listed.
WALK = r"""
import importlib, json, pkgutil, sys

entry, dev_tools = sys.argv[1], json.loads(sys.argv[2])
failed, tried = [], set()


def attempt(name):
    if name in tried:
        return
    tried.add(name)
    try:
        importlib.import_module(name)
    except BaseException as error:
        failed.append(f"{name}: {type(error).__name__}: {error}")


attempt(entry)
for top in ("app", "docflow_core"):
    attempt(top)
    package = sys.modules.get(top)
    if package is None:
        continue
    for info in pkgutil.walk_packages(package.__path__, top + ".", onerror=lambda name: None):
        attempt(info.name)
loaded = sorted(set(sys.modules) & set(dev_tools))
print(json.dumps({"modules": len(tried), "failed": failed, "dev_tools": loaded}))
"""


class UnknownDockerfile(Exception):
    pass


@dataclass(frozen=True)
class Plan:
    copies: tuple[tuple[str, str], ...]  # (path in the repository, path in the image)
    venv: str
    installs: tuple[tuple[str, ...], ...]
    workdir: str
    entry: str


def instructions(dockerfile: str) -> list[tuple[str, str]]:
    """(keyword, the rest) for each instruction, continuations joined and
    comment lines dropped, as Docker reads them."""
    found: list[tuple[str, str]] = []
    pending = ""
    for line in dockerfile.splitlines():
        if line.strip().startswith("#"):
            continue
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1] + " "
            continue
        whole = (pending + line).strip()
        pending = ""
        if whole:
            keyword, _, rest = whole.partition(" ")
            found.append((keyword.upper(), rest.strip()))
    return found


def read_plan(app: str, dockerfile: str) -> Plan:
    root = f"/opt/{app}"
    pip = f"{root}/venv/bin/pip"
    copies: list[tuple[str, str]] = []
    venvs: list[str] = []
    installs: list[tuple[str, ...]] = []
    workdir = entry = ""
    for keyword, rest in instructions(dockerfile):
        if keyword == "COPY":
            parts = shlex.split(rest)
            if len(parts) != 2 or parts[0].startswith("--") or not parts[1].startswith(root + "/"):
                raise UnknownDockerfile(f"a COPY this check doesn't know: COPY {rest}")
            copies.append((parts[0], parts[1]))
        elif keyword == "RUN":
            for segment in (part.strip() for part in rest.split("&&")):
                if "pip" not in segment and f"{root}/venv" not in segment:
                    continue
                tokens = tuple(shlex.split(segment))
                if tokens == ("python3", "-m", "venv", f"{root}/venv"):
                    venvs.append(tokens[-1])
                elif tokens[:2] == (pip, "install"):
                    installs.append(tokens)
                else:
                    raise UnknownDockerfile(f"a pip or venv line this check doesn't know: {segment}")
        elif keyword == "WORKDIR":
            workdir = rest
        elif keyword == "CMD":
            entry = _entry_module(json.loads(rest))
    if len(venvs) != 1 or len(installs) != 2:
        raise UnknownDockerfile(
            f"expected one `python3 -m venv {root}/venv` and two `{pip} install` lines, "
            f"found {len(venvs)} and {len(installs)}"
        )
    if workdir != root or not entry:
        raise UnknownDockerfile(f"expected WORKDIR {root} and a CMD naming the entry module")
    return Plan(tuple(copies), venvs[0], tuple(installs), workdir, entry)


def _entry_module(cmd: list[str]) -> str:
    if cmd[:1] == ["uvicorn"] and len(cmd) > 1:
        return cmd[1].split(":")[0]
    if cmd[:2] == ["python", "-m"] and len(cmd) > 2:
        return cmd[2]
    raise UnknownDockerfile(f"a CMD this check can't read an entry module from: {cmd}")


def map_path(token: str, root: str, base: Path) -> str:
    """An image path under `root`, as a path under `base`; the venv's own bin
    folder as this platform's; any other token unchanged."""
    bin_folder = f"{root}/venv/bin/"
    if token.startswith(bin_folder):
        return str(venv_bin(base) / token[len(bin_folder) :])
    if token == root or token.startswith(root + "/"):
        return str(base.joinpath(*token[len(root) :].strip("/").split("/")))
    return token


def venv_bin(base: Path) -> Path:
    return base / "venv" / ("Scripts" if os.name == "nt" else "bin")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_names(text: str) -> set[str]:
    """The packages a requirements file pins (`name==version` lines)."""
    return {
        canonical(line.split("==")[0])
        for line in (raw.strip().lstrip("﻿") for raw in text.splitlines())
        if "==" in line and not line.startswith("#")
    }


def freeze_names(freeze: str) -> set[str]:
    return {canonical(re.split(r"\s*(?:==|@)\s*", line)[0]) for line in freeze.splitlines() if line.strip()}


def run(
    command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    shown_command = [("<the import walk>" if part == WALK else part) for part in command]
    print(f"$ {shlex.join(shown_command)}", flush=True)
    started = time.monotonic()
    done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    lines = (done.stdout + done.stderr).rstrip().splitlines()
    shown = lines if done.returncode or len(lines) <= 6 else [f"... ({len(lines) - 3} lines)", *lines[-3:]]
    for line in shown:
        print(f"  {line}")
    print(f"  [exit {done.returncode}, {time.monotonic() - started:.1f} s]", flush=True)
    return done


def check(app: str, repo: Path) -> int:
    root = f"/opt/{app}"
    title = f"{app}: image check"
    try:
        plan = read_plan(app, (repo / "apps" / app / "Dockerfile").read_text(encoding="utf-8"))
    except UnknownDockerfile as error:
        print(f"::error title={title}::{error}. Update scripts/ci/image_check.py with the Dockerfile.")
        return 2
    started = time.monotonic()
    base = Path(tempfile.mkdtemp(prefix=f"image-{app}-", dir=os.environ.get("RUNNER_TEMP") or None))
    print(f"Building {root} in {base}")
    for source, target in plan.copies:
        here, there = repo / source, Path(map_path(target, root, base))
        there.parent.mkdir(parents=True, exist_ok=True)
        if here.is_dir():
            shutil.copytree(here, there, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(here, there)

    python = str(venv_bin(base) / ("python.exe" if os.name == "nt" else "python"))
    failures: list[str] = []
    if run([sys.executable, "-m", "venv", map_path(plan.venv, root, base)]).returncode:
        failures.append("the venv could not be made")
    for install in () if failures else plan.installs:
        if run([map_path(token, root, base) for token in install]).returncode:
            failures.append(f"`{shlex.join(install)}` failed")
            break
    if failures:
        return report(title, failures, started)

    if run([python, "-m", "pip", "check"]).returncode:
        failures.append("`pip check` found a broken requirement (its output is above)")
    freeze = run([python, "-m", "pip", "freeze"]).stdout
    print(freeze.rstrip())
    dev_file = repo / "apps" / app / "requirements-dev.txt"
    dev = requirement_names(dev_file.read_text(encoding="utf-8")) & freeze_names(freeze)
    if dev:
        failures.append(f"packages from requirements-dev.txt are installed: {', '.join(sorted(dev))}")

    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    walked = run(
        [python, "-c", WALK, plan.entry, json.dumps(DEV_TOOL_MODULES)],
        cwd=Path(map_path(plan.workdir, root, base)),
        env=env,
    )
    try:
        result = json.loads(walked.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        failures.append("the import walk printed no result (its output is above)")
    else:
        print(f"modules imported: {result['modules']}; failed: {len(result['failed'])}; "
              f"dev tools loaded: {result['dev_tools']}")
        failures += [f"import failed: {line}" for line in result["failed"]]
        if result["dev_tools"]:
            failures.append(f"the imports loaded dev tools: {', '.join(result['dev_tools'])}")
        if not failures:
            print(f"::notice title={title}::the Dockerfile's install alone: pip check clean, no dev package, "
                  f"{result['modules']} modules imported ({time.monotonic() - started:.0f} s)")
            return 0
    return report(title, failures, started)


def report(title: str, failures: list[str], started: float) -> int:
    for failure in failures:
        print(f"::error title={title}::{failure}")
    print(f"{len(failures)} failure(s) in {time.monotonic() - started:.0f} s")
    return 1


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 4) or argv[1] not in APPS or (len(argv) == 4 and argv[2] != "--repo"):
        print(__doc__, file=sys.stderr)
        return 2
    return check(argv[1], Path(argv[3]).resolve() if len(argv) == 4 else REPO)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
