"""
The image check (scripts/ci/image_check.py; founder, 2026-10-28; D-225 item 6):
it reads the API's and the worker's install steps from their Dockerfiles, so
these tests hold it to the real Dockerfiles, and make it refuse one it doesn't
know. Building the venv and importing every module run in CI's api and
worker jobs; their negative controls are recorded in D-225 item 6.
"""

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


def _image_check():
    spec = importlib.util.spec_from_file_location("image_check", REPO / "scripts" / "ci" / "image_check.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dockerfile(app: str) -> str:
    return (REPO / "apps" / app / "Dockerfile").read_text(encoding="utf-8")


@pytest.mark.parametrize(("app", "entry"), [("api", "app.main"), ("worker", "app.run_workers")])
def test_the_plan_is_the_dockerfiles_own_install(app, entry):
    root = f"/opt/{app}"
    plan = _image_check().read_plan(app, _dockerfile(app))
    assert plan.copies == (
        (f"apps/{app}/requirements.lock.txt", f"{root}/requirements.lock.txt"),
        ("packages/core/pyproject.toml", f"{root}/src/core/pyproject.toml"),
        ("packages/core/docflow_core", f"{root}/src/core/docflow_core"),
        (f"apps/{app}/app", f"{root}/app"),
    )
    assert plan.venv == f"{root}/venv"
    pip = (f"{root}/venv/bin/pip", "install", "--no-cache-dir", "--no-deps")
    assert plan.installs == ((*pip, "-r", f"{root}/requirements.lock.txt"), (*pip, f"{root}/src/core"))
    assert plan.workdir == root
    assert plan.entry == entry


@pytest.mark.parametrize(
    "added",
    [
        "RUN /opt/api/venv/bin/pip install --no-cache-dir pytest",
        "RUN pip install --no-cache-dir pytest",
        'RUN /opt/api/venv/bin/python -c "import app.main"',
        "RUN python3 -m venv --system-site-packages /opt/api/venv",
        "COPY --chown=docflow apps/api/app /opt/api/app",
        "COPY apps/api/tests /srv/tests",
    ],
)
def test_a_line_it_does_not_know_is_refused(added):
    image_check = _image_check()
    dockerfile = _dockerfile("api").replace("WORKDIR /opt/api", f"{added}\nWORKDIR /opt/api")
    with pytest.raises(image_check.UnknownDockerfile):
        image_check.read_plan("api", dockerfile)


def test_a_cmd_with_no_entry_module_is_refused():
    image_check = _image_check()
    cmd = 'CMD ["python", "-m", "app.run_workers"]'
    dockerfile = _dockerfile("worker").replace(cmd, 'CMD ["celery", "worker"]')
    with pytest.raises(image_check.UnknownDockerfile):
        image_check.read_plan("worker", dockerfile)


def test_an_unknown_dockerfile_stops_the_run_before_anything_is_built(tmp_path, capsys):
    (tmp_path / "apps" / "api").mkdir(parents=True)
    (tmp_path / "apps" / "api" / "Dockerfile").write_text(
        _dockerfile("api") + "RUN /opt/api/venv/bin/pip install --no-cache-dir pytest\n", encoding="utf-8"
    )
    assert _image_check().check("api", tmp_path) == 2
    assert "::error title=api: image check::" in capsys.readouterr().out


def test_image_paths_map_into_the_build_folder_and_nothing_else_moves(tmp_path):
    image_check = _image_check()
    assert image_check.map_path("/opt/api/requirements.lock.txt", "/opt/api", tmp_path) == str(
        tmp_path / "requirements.lock.txt"
    )
    assert image_check.map_path("/opt/api/src/core", "/opt/api", tmp_path) == str(tmp_path / "src" / "core")
    assert image_check.map_path("/opt/api/venv/bin/pip", "/opt/api", tmp_path) == str(
        image_check.venv_bin(tmp_path) / "pip"
    )
    assert image_check.map_path("/opt/apiary/x", "/opt/api", tmp_path) == "/opt/apiary/x"
    assert image_check.map_path("--no-deps", "/opt/api", tmp_path) == "--no-deps"


def test_freeze_and_requirement_names_compare_as_pip_spells_them():
    image_check = _image_check()
    freeze = "RapidFuzz==3.14.6\ndocflow-core @ file:///tmp/core\nPyJWT==2.15.0\n"
    assert image_check.freeze_names(freeze) == {
        "rapidfuzz",
        "docflow-core",
        "pyjwt",
    }
    assert image_check.requirement_names("﻿# a comment\nmypy_extensions==1.1.0\nhttpx>=0.27\n") == {
        "mypy-extensions"
    }


@pytest.mark.parametrize("app", ["api", "worker"])
def test_no_package_is_in_both_an_apps_lock_and_its_dev_file(app):
    """What the check's dev-package rule catches in the built venv, read from
    the files: the lock is all the image installs (D-225)."""
    image_check = _image_check()
    folder = REPO / "apps" / app
    lock = image_check.requirement_names((folder / "requirements.lock.txt").read_text(encoding="utf-8"))
    dev = image_check.requirement_names((folder / "requirements-dev.txt").read_text(encoding="utf-8"))
    assert dev and not lock & dev
