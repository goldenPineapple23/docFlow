"""
M10 (D-239): the model IDs and their prices come from config, and reading
config changes nothing that runs (D-235 item 4).

The two literals below are typed on purpose: they are the strings that ran
before M10, and the no-behaviour-change rule is that the defaults equal them.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from docflow_core import extraction
from docflow_core.config import (
    EXTRACTION_MODEL_DEFAULT,
    MODEL_PRICES,
    ROUTING_MODEL_DEFAULT,
    Settings,
    get_settings,
    model_ids_line,
    settings_refusal,
)

BEFORE_M10_EXTRACTION = "claude-sonnet-5"
BEFORE_M10_ROUTING = "claude-haiku-4-5"

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = REPO_ROOT / "packages" / "core" / "docflow_core" / "config.py"


def _settings(**values: str) -> Settings:
    """Settings from these values alone: no .env, and the model variables
    of this process's environment ignored by passing both explicitly."""
    values.setdefault("docflow_extraction_model", EXTRACTION_MODEL_DEFAULT)
    values.setdefault("docflow_routing_model", ROUTING_MODEL_DEFAULT)
    return Settings(_env_file=None, **values)


# ── The defaults are what ran before ────────────────────────────────────────


def test_the_defaults_are_the_strings_that_ran_before_m10():
    fields = Settings.model_fields
    assert fields["docflow_extraction_model"].default == BEFORE_M10_EXTRACTION
    assert fields["docflow_routing_model"].default == BEFORE_M10_ROUTING


def test_the_defaults_load_clean(monkeypatch):
    monkeypatch.delenv("DOCFLOW_EXTRACTION_MODEL", raising=False)
    monkeypatch.delenv("DOCFLOW_ROUTING_MODEL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.docflow_extraction_model == BEFORE_M10_EXTRACTION
    assert settings.docflow_routing_model == BEFORE_M10_ROUTING


def test_the_extraction_module_calls_the_models_that_ran_before():
    assert extraction.EXTRACTION_MODEL == BEFORE_M10_EXTRACTION
    assert extraction.ROUTING_MODEL == BEFORE_M10_ROUTING


# ── The price table ──────────────────────────────────────────────────────────


def test_the_price_table_holds_exactly_the_two_models_at_their_prices_and_roles():
    assert {
        model: (price.input_usd_per_million, price.output_usd_per_million, set(price.roles))
        for model, price in MODEL_PRICES.items()
    } == {
        BEFORE_M10_EXTRACTION: (Decimal("2.00"), Decimal("10.00"), {"extraction"}),
        BEFORE_M10_ROUTING: (Decimal("1.00"), Decimal("5.00"), {"routing"}),
    }


def test_sonnets_costs_are_pinned_at_fixed_token_counts():
    """Founder's condition for no staging suites (D-239): both models' cost
    functions pinned. Haiku's routing cost is also pinned at
    test_example_prompting.py (0.0012)."""
    assert extraction.extraction_input_cost(1_000_000) == Decimal("2.00")
    assert extraction.extraction_input_cost(1_234) == Decimal("0.002468")
    assert extraction._cost(1_000_000, 1_000_000) == Decimal("12.00")
    assert extraction._cost(1_234, 567) == Decimal("0.008138")


def test_haikus_costs_are_pinned_at_fixed_token_counts():
    assert extraction.routing_input_cost(1_000_000) == Decimal("1.00")
    assert extraction.routing_input_cost(1_234) == Decimal("0.001234")


# ── The check when Settings loads ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "env_name", "model"),
    [
        ("docflow_extraction_model", "DOCFLOW_EXTRACTION_MODEL", "claude-unknown-1"),
        ("docflow_routing_model", "DOCFLOW_ROUTING_MODEL", "claude-unknown-1"),
        # The value the local .env held until 2026-11-12: not in the table.
        ("docflow_routing_model", "DOCFLOW_ROUTING_MODEL", "claude-haiku-4-5-20251001"),
    ],
)
def test_an_unknown_model_stops_settings_loading(field, env_name, model):
    with pytest.raises(ValidationError) as caught:
        _settings(**{field: model})
    message = caught.value.errors()[0]["msg"]
    assert f"{env_name}={model!r} is not in MODEL_PRICES" in message
    assert "\n" not in message


@pytest.mark.parametrize(
    ("field", "env_name", "model", "role"),
    [
        # Section 3: the cheaper routing model never does the final extraction.
        ("docflow_extraction_model", "DOCFLOW_EXTRACTION_MODEL", BEFORE_M10_ROUTING, "extraction"),
        ("docflow_routing_model", "DOCFLOW_ROUTING_MODEL", BEFORE_M10_EXTRACTION, "routing"),
    ],
)
def test_a_model_set_for_a_role_its_entry_does_not_allow_stops_settings_loading(field, env_name, model, role):
    with pytest.raises(ValidationError) as caught:
        _settings(**{field: model})
    message = caught.value.errors()[0]["msg"]
    assert f"{env_name}={model!r} may not fill the {role} role" in message
    assert "\n" not in message


def test_the_check_runs_from_the_environment_too(monkeypatch):
    """Scripts and tests load Settings the same way the apps do."""
    monkeypatch.setenv("DOCFLOW_ROUTING_MODEL", BEFORE_M10_EXTRACTION)
    get_settings.cache_clear()
    try:
        refusal = settings_refusal()
    finally:
        monkeypatch.delenv("DOCFLOW_ROUTING_MODEL")
        get_settings.cache_clear()
    assert refusal == (
        f"DOCFLOW_ROUTING_MODEL={BEFORE_M10_EXTRACTION!r} may not fill the routing role "
        "(MODEL_PRICES allows it only for extraction; D-239)"
    )


def test_a_settings_error_never_quotes_the_settings_it_was_given():
    """hide_input_in_errors: a refused Settings would otherwise print what it
    was given, secrets among them, into a traceback."""
    with pytest.raises(ValidationError) as caught:
        _settings(docflow_routing_model="claude-unknown-1", stripe_secret_key="sk_test_must_not_appear")
    assert "sk_test_must_not_appear" not in str(caught.value)


def test_settings_ignores_unknown_variables_such_as_a_leftover_session_secret(monkeypatch):
    """SESSION_SECRET was removed (D-239); a leftover one must not stop loading."""
    monkeypatch.setenv("SESSION_SECRET", "left-over-value")
    settings = Settings(_env_file=None)
    assert not hasattr(settings, "session_secret")


def test_the_start_line_names_both_models():
    assert model_ids_line(_settings()) == (
        f"model_ids: extraction={BEFORE_M10_EXTRACTION} routing={BEFORE_M10_ROUTING}"
    )


# ── The guard: model IDs live only in config.py ─────────────────────────────

MODEL_ID = re.compile(r"claude-(?:sonnet|haiku|opus|fable|instant|\d)")
GUARDED = (
    "packages/core/docflow_core",
    "apps/api/app",
    "apps/worker/app",
    "apps/parse/parse_service",
    "scripts",
    "apps/web/src",
    ".github",
)
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".yml", ".yaml", ".toml", ".json", ".sh", ".txt"}


def model_id_literals(roots: list[Path], base: Path = REPO_ROOT) -> list[str]:
    found = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in SUFFIXES or path == CONFIG:
                continue
            if any(part in {"node_modules", ".next", "__pycache__", ".venv"} for part in path.parts):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if MODEL_ID.search(line):
                    found.append(f"{path.relative_to(base)}:{number}: {line.strip()}")
    return found


def test_no_model_id_literal_outside_config():
    """D-239: a model ID anywhere else would run beside the setting, unpriced
    and unchecked. Tests and docs may name models; product and tooling code
    may not."""
    assert model_id_literals([REPO_ROOT / root for root in GUARDED]) == []
    assert MODEL_ID.search(CONFIG.read_text(encoding="utf-8"))  # the guard can see one


def test_the_guard_finds_a_planted_literal(tmp_path):
    (tmp_path / "planted.py").write_text('MODEL = "claude-haiku-4-5"\n', encoding="utf-8")
    (tmp_path / "clean.ts").write_text("const model = settings.model;\n", encoding="utf-8")
    assert model_id_literals([tmp_path], base=tmp_path) == ['planted.py:1: MODEL = "claude-haiku-4-5"']
