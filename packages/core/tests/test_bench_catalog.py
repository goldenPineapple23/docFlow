"""
The catalog sizing job's parts that need no database (Stage 4; D-199, D-200):
the fake catalogs are the same on every run, the fastener families really do
hold thousands of near-identical siblings, the job refuses any database that
is not on its own machine, and the trigram index statements have one home.

The half that needs a database runs in CI's own job
(.github/workflows/catalog-sizing.yml), on a small mix, whenever these files
change. It never runs against docflow-staging.
"""

import hashlib
import importlib.util
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BENCH = REPO / "scripts" / "bench"
WORKFLOW = REPO / ".github" / "workflows" / "catalog-sizing.yml"
MIGRATIONS = REPO / "supabase" / "migrations"

STAGE_4_MIX = "250000,100000x2,50000x7"
SCALE_GATE_MIX = "250000,100000x4,50000x45"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, BENCH / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # a dataclass looks its own module up by name
    spec.loader.exec_module(module)
    return module


gen = _load("catalog_gen")
sizing = _load("catalog_sizing")


def _fingerprint(items) -> str:
    digest = hashlib.sha256()
    for item in items:
        digest.update(repr((item.sku, item.description, item.unit_of_measure, item.barcode,
                            item.external_id, sorted(item.raw.items()))).encode())  # fmt: skip
    return digest.hexdigest()


def test_the_same_seed_gives_the_same_catalog_and_another_tenant_a_different_one():
    first = _fingerprint(gen.catalog(7, 1, 2000))
    assert first == _fingerprint(gen.catalog(7, 1, 2000))
    assert first != _fingerprint(gen.catalog(7, 2, 2000))
    assert first != _fingerprint(gen.catalog(8, 1, 2000))


@pytest.mark.parametrize("count", [1, 199, 200, 3000, 50_000])
def test_a_catalog_has_exactly_the_items_asked_for_and_no_sku_twice(count):
    items = list(gen.catalog(7, 3, count))
    assert len(items) == count
    assert len({item.sku for item in items}) == count
    # The SKU index's own key (letters and digits only) is different for each, too.
    assert len({sizing.sku_key(item.sku) for item in items}) == count


def test_the_largest_stage_4_tenant_fits_one_catalog():
    assert max(gen.parse_mix(STAGE_4_MIX)) <= gen.MAX_ITEMS_PER_TENANT
    with pytest.raises(ValueError, match="more than one tenant's catalog can hold"):
        list(gen.catalog(7, 1, gen.MAX_ITEMS_PER_TENANT + gen.GENERAL_COMBINATIONS + 10_000))


def test_thousands_of_fastener_items_have_a_sibling_one_token_apart():
    """Founder, 2026-10-11: fastener-style families with thousands of
    siblings one token apart ("1/4-20 x 1 HHCS GR5 ZP")."""
    items = list(gen.catalog(7, 1, 20_000))  # tenant 1 writes fasteners abbreviated
    fasteners = [tuple(item.description.split()) for item in items if " x " in item.description]
    assert len(fasteners) > 15_000
    masked: Counter[tuple] = Counter()
    for tokens in fasteners:
        for i in range(len(tokens)):
            masked[tokens[:i] + ("*",) + tokens[i + 1 :]] += 1
    with_a_sibling = sum(
        1
        for tokens in fasteners
        if any(masked[tokens[:i] + ("*",) + tokens[i + 1 :]] > 1 for i in range(len(tokens)))
    )
    assert with_a_sibling > 5_000


def test_a_tenants_house_style_is_abbreviated_or_spelled_out():
    abbreviated = next(i for i in gen.catalog(7, 1, 500) if " x " in i.description)
    spelled_out = next(i for i in gen.catalog(7, 2, 500) if " x " in i.description)
    assert re.search(r" (HHCS|SHCS|FHCS|BHCS|CB|LAG|PPMS|FPMS|HWTS|SSS|HFB|TR) ", abbreviated.description)
    assert re.search(r"(Screw|Bolt|Rod) ", spelled_out.description)


def test_some_descriptions_repeat_under_another_sku_and_the_hard_pairs_are_there():
    items = list(gen.catalog(7, 1, 20_000))
    by_description = Counter(item.description for item in items)
    assert sum(1 for n in by_description.values() if n > 1) >= 20_000 // gen.DUPLICATE_DESCRIPTION_EVERY - 5
    general = {item.description for item in items if item.description.startswith("Acme Test ")}
    pairs = sum(1 for d in general if " 5lb " in d and d.replace(" 5lb ", " 2lb ") in general)
    assert pairs > 10  # D-066: the same product in "5lb" and in "2lb"


def test_every_bench_tenant_is_named_as_test_data():
    assert gen.tenant_name(1, 250_000) == "Acme Test Bench 01 (250000 items)"


def test_the_mix_is_one_catalog_size_per_tenant():
    assert gen.parse_mix("250000") == [250_000]
    stage_4 = gen.parse_mix(STAGE_4_MIX)
    assert stage_4 == [250_000, 100_000, 100_000] + [50_000] * 7
    assert sum(stage_4) == 800_000
    scale_gate = gen.parse_mix(SCALE_GATE_MIX)
    assert len(scale_gate) == 50 and sum(scale_gate) == 2_900_000


@pytest.mark.parametrize("bad", ["", "abc", "250000x", "0", "100x0", "-5", "250000;drop", "9999999"])
def test_a_mix_that_cannot_be_read_is_refused(bad):
    with pytest.raises(ValueError):
        gen.parse_mix(bad)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
        "postgresql://postgres:postgres@localhost:54322/postgres",
        "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres",
    ],
)
def test_a_database_on_this_machine_is_accepted(url):
    sizing.refuse_unless_local(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://docflow_admin.abc:pw@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
        "postgresql://postgres:pw@db.example.test:5432/postgres",
        "postgresql://postgres:pw@127.0.0.1.example.test:5432/postgres",
        "not a url",
    ],
)
def test_any_other_database_is_refused_before_anything_is_done(url):
    with pytest.raises(sizing.NotALocalDatabase) as refused:
        sizing.refuse_unless_local(url)
    assert "pw" not in str(refused.value)


def test_a_refused_database_exits_without_connecting(capsys):
    code = sizing.main(["--mix", "10", "--database-url", "postgresql://u:pw@db.example.test:5432/postgres"])
    assert code == 2
    assert "only runs against a database on this machine" in capsys.readouterr().out


def test_order_lines_are_the_same_on_every_run_and_lowercased():
    rows = [(item.sku, item.description) for item in gen.catalog(7, 1, 60)]
    first = sizing.order_lines(rows, 7)
    assert first == sizing.order_lines(rows, 7)
    assert all(description == description.lower() and key == key.lower() for description, key in first)
    # Every third line is asked as written.
    assert first[0] == (rows[0][1].lower(), sizing.sku_key(rows[0][0]))


def _progress_points(mix: str) -> list[int]:
    """The row counts at which the load's tenant lines become an annotation."""
    sizes = gen.parse_mix(mix)
    points, loaded = [], 0
    for size in sizes:
        if sizing.progress_due(loaded, loaded + size, sum(sizes)):
            points.append(loaded + size)
        loaded += size
    return points


@pytest.mark.parametrize(
    ("mix", "points"),
    [
        ("250000", [250_000]),
        ("3000,1000x2", [3000, 4000, 5000]),
        (STAGE_4_MIX, [250_000, 450_000, 600_000, 800_000]),
        (SCALE_GATE_MIX, [250_000, 750_000, 1_450_000, 2_200_000, 2_900_000]),
    ],
)
def test_the_load_reports_after_the_first_tenant_and_at_each_quarter(mix, points):
    assert _progress_points(mix) == points


@pytest.mark.parametrize("mix", ["1", "250000", "1x50", "50000x50", STAGE_4_MIX, SCALE_GATE_MIX])
def test_a_run_never_prints_more_notices_than_github_keeps_from_one_step(mix):
    """GitHub keeps ten notices from one step and drops the rest without a
    word: a dropped one would be a figure nobody reads."""
    results = 4  # inputs, sizes as loaded, the candidate query, the rebuild
    points = _progress_points(mix)
    assert len(points) + results <= 10
    assert points[-1] == sum(gen.parse_mix(mix))  # the last tenant always reports


def test_a_tenants_line_gives_its_rows_its_seconds_and_how_far_the_load_is():
    line = sizing.tenant_line(3, 10, 100_000, 80.0, 450_000, 800_000, 312.4)
    assert line == (
        "tenant 3 of 10: 100000 rows in 80 s (1250 rows a second); "
        "450000 of 800000 rows loaded, 312 s since the load began"
    )


def test_the_trigram_index_statements_have_one_home():
    """scripts/bench/trigram_indexes.sql is where they are written. A
    migration that creates a trigram index (0037 will) must carry these
    statements word for word, and no other trigram index."""
    home = sizing.statements((BENCH / "trigram_indexes.sql").read_text(encoding="utf-8"))
    creates = [s for s in home if s.startswith("create index")]
    assert [re.search(r"create index (\w+)", s).group(1) for s in creates] == list(sizing.TRIGRAM_INDEXES)
    assert all("where deleted_at is null" in s and "(tenant_id, " in s for s in creates)

    for migration in sorted(MIGRATIONS.glob("*.sql")):
        found = sizing.statements(migration.read_text(encoding="utf-8"))
        for statement in found:
            if "gist_trgm_ops" in statement or "gin_trgm_ops" in statement:
                assert statement in creates, (
                    f"{migration.name} creates a trigram index that is not one of the statements in "
                    "scripts/bench/trigram_indexes.sql; copy them from there word for word"
                )


def test_the_queries_order_by_the_indexed_expressions():
    home = (BENCH / "trigram_indexes.sql").read_text(encoding="utf-8")
    for query in (sizing.DESCRIPTION_QUERY, sizing.SKU_QUERY):
        expression = re.search(r"ORDER BY (.+) <->", query).group(1)
        assert expression in home
        assert "tenant_id = %s AND deleted_at IS NULL" in query


def test_the_workflow_is_started_by_hand_and_reads_no_secret():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow and "mix:" in workflow
    assert "secrets." not in workflow
    assert "127.0.0.1" in workflow and "supabase.com" not in workflow
    # The inputs are passed as environment values, never pasted into the shell line.
    run_line = next(line for line in workflow.splitlines() if "catalog_sizing.py" in line and "run:" in line)
    assert "${{" not in run_line
    assert "runs-on: ubuntu-24.04" in workflow
    # Under GitHub's own 360-minute stop, so the job ends on its own terms.
    assert "timeout-minutes: 350" in workflow
