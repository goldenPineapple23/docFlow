"""
scripts/sweep_test_tenants.py's guard (D-224; founder, 2026-10-27): it
sweeps only docflow-staging, it reads its two flags from the process
environment and never from `.env`, and nothing from apps/api loads before
the guard has passed. No database, no Storage: the guard is checked with
prepared values, and `main` is stopped before it could connect.

The purge's own checks (the name, the age, the marks, the backup) need a
database and are in apps/api/tests/test_sweep_test_tenants.py.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

from docflow_core.config import Settings

_SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "sweep_test_tenants.py"
REF = "ovntvsmhekqefsrpjtih"
POOLER = f"postgresql://docflow_admin.{REF}:not-a-password@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
DIRECT = f"postgresql://docflow_admin:not-a-password@db.{REF}.supabase.co:5432/postgres"
STORAGE = f"https://{REF}.storage.supabase.co/storage/v1/s3"
FLAGS = {"DOCFLOW_STAGING_RUN": "1", "DOCFLOW_ENV": "staging"}


def _load():
    spec = importlib.util.spec_from_file_location("sweep_test_tenants_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sweep = _load()


def test_loading_the_script_imports_nothing_from_the_api():
    """The founder's condition: the purge is imported inside the delete path,
    after the guard, so core's CI job needs no API and no API code runs first."""
    before = set(sys.modules)
    _load()
    loaded = set(sys.modules) - before
    assert not [name for name in sys.modules if "tenant_cleanup" in name]
    api = pathlib.Path(__file__).resolve().parents[3] / "apps" / "api"
    files = {name: getattr(sys.modules[name], "__file__", None) for name in loaded}
    from_api = [name for name, file in files.items() if file and api in pathlib.Path(file).resolve().parents]
    assert not from_api, f"loading the script imported API modules: {from_api}"


@pytest.mark.parametrize("url", [POOLER, DIRECT])
def test_staging_with_both_flags_passes(url):
    assert sweep.check_environment(FLAGS, url, STORAGE) == []


@pytest.mark.parametrize(
    "environ, expected",
    [
        ({}, ["DOCFLOW_STAGING_RUN=1", "DOCFLOW_ENV=staging"]),
        ({"DOCFLOW_ENV": "staging"}, ["DOCFLOW_STAGING_RUN=1"]),
        ({"DOCFLOW_STAGING_RUN": "true", "DOCFLOW_ENV": "staging"}, ["DOCFLOW_STAGING_RUN=1"]),
        # Unset, although the settings' own default and staging's .env say "staging".
        ({"DOCFLOW_STAGING_RUN": "1"}, ["DOCFLOW_ENV=staging"]),
        ({"DOCFLOW_STAGING_RUN": "1", "DOCFLOW_ENV": "production"}, ["DOCFLOW_ENV=staging"]),
    ],
)
def test_each_missing_or_wrong_flag_is_refused(environ, expected):
    problems = sweep.check_environment(environ, POOLER, STORAGE)
    assert len(problems) == len(expected)
    for problem, flag in zip(problems, expected):
        assert flag in problem


@pytest.mark.parametrize(
    "url",
    [
        "",
        "postgresql://nobody:nothing@db.invalid:5432/none",
        "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
        "postgresql://postgres:postgres@localhost:54322/postgres",
        # Another Supabase project, through the same pooler and directly.
        "postgresql://docflow_admin.abcdefghijklmnopqrst:x@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
        "postgresql://docflow_admin:x@db.abcdefghijklmnopqrst.supabase.co:5432/postgres",
        # The ref somewhere it doesn't name the project.
        f"postgresql://docflow_admin:x@{REF}-copy.example.test:5432/postgres",
        f"postgresql://{REF}:x@localhost:5432/postgres",
        "not a url at all",
    ],
)
def test_any_database_but_stagings_is_refused(url):
    problems = sweep.check_environment(FLAGS, url, STORAGE)
    assert len(problems) == 1 and "ADMIN_DATABASE_URL" in problems[0]


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "http://127.0.0.1:54321/storage/v1/s3",
        "https://abcdefghijklmnopqrst.storage.supabase.co/storage/v1/s3",
        f"https://storage.example.test/{REF}/s3",
    ],
)
def test_any_storage_but_stagings_is_refused(endpoint):
    problems = sweep.check_environment(FLAGS, POOLER, endpoint)
    assert len(problems) == 1 and "STORAGE_S3_ENDPOINT" in problems[0]


@pytest.fixture()
def no_api(monkeypatch):
    """Fails the test if main gets past the guard to the purge."""

    def refuse():
        raise AssertionError("main loaded the purge although the guard refused")

    monkeypatch.setattr(sweep, "_load_purge", refuse)


def test_main_refuses_before_anything_loads(monkeypatch, no_api, capsys):
    for flag in FLAGS:
        monkeypatch.delenv(flag, raising=False)
    monkeypatch.setattr(
        sweep, "_settings", lambda: Settings(admin_database_url=POOLER, storage_s3_endpoint=STORAGE)
    )
    assert sweep.main(["list"]) == sweep.EXIT_REFUSED_ENVIRONMENT
    assert "DOCFLOW_STAGING_RUN=1" in capsys.readouterr().err


def test_the_flags_in_a_dot_env_file_do_not_count(monkeypatch, no_api, tmp_path, capsys):
    """The settings read `.env`; the guard's flags never come from there."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DOCFLOW_STAGING_RUN=1\nDOCFLOW_ENV=staging\n"
        f"ADMIN_DATABASE_URL={POOLER}\nSTORAGE_S3_ENDPOINT={STORAGE}\n"
        "STORAGE_S3_REGION=us-east-1\nSTORAGE_S3_ACCESS_KEY_ID=x\nSTORAGE_S3_SECRET_ACCESS_KEY=x\n",
        encoding="utf-8",
    )
    for name in (*FLAGS, "ADMIN_DATABASE_URL", "STORAGE_S3_ENDPOINT", "STORAGE_S3_REGION",
                 "STORAGE_S3_ACCESS_KEY_ID", "STORAGE_S3_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sweep, "_settings", lambda: Settings(_env_file=str(env_file)))
    assert sweep.main(["delete", "00000000-0000-0000-0000-000000000000", "--name", "Acme Test X"]) == 2
    err = capsys.readouterr().err
    assert "DOCFLOW_STAGING_RUN=1" in err and "DOCFLOW_ENV=staging" in err
    assert "ADMIN_DATABASE_URL" not in err and "STORAGE_S3_ENDPOINT" not in err  # the file's URLs were read


def test_storage_must_be_fully_configured(monkeypatch, no_api, capsys):
    for flag, value in FLAGS.items():
        monkeypatch.setenv(flag, value)
    monkeypatch.setattr(
        sweep,
        "_settings",
        lambda: Settings(
            admin_database_url=POOLER, storage_s3_endpoint=STORAGE, storage_s3_region="",
            storage_s3_access_key_id="", storage_s3_secret_access_key="",
        ),
    )
    assert sweep.main(["list"]) == sweep.EXIT_REFUSED_ENVIRONMENT
    assert "STORAGE_S3_REGION" in capsys.readouterr().err
