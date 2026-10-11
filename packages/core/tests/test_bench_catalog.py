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
variants = _load("index_variants")


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


# ── The index variants (scripts/bench/index_variants.py) ────────────────────


def test_the_baseline_variant_is_the_one_home_word_for_word_and_names_are_distinct():
    every = variants.variants()
    assert [v.name for v in every] == ["baseline", "siglen64", "siglen128", "siglen256", "gin", "no_tenant"]
    home = sizing.statements((BENCH / "trigram_indexes.sql").read_text(encoding="utf-8"))
    assert list(every[0].creates.values()) == [s for s in home if s.startswith("create index")]
    names = [variants.index_name(s) for v in every for s in v.creates.values()]
    assert len(set(names)) == len(names) == 12
    for variant in every[1:]:
        for field, statement in variant.creates.items():
            assert variants.index_name(statement).startswith("bench_")
            assert statement.endswith("where deleted_at is null")
            assert variants.EXPRESSIONS[field] in statement


def test_each_variant_changes_one_thing_from_the_baseline():
    by_name = {v.name: v for v in variants.variants()}
    assert "gist_trgm_ops(siglen=128))" in by_name["siglen128"].creates["description"]
    assert "using gist (tenant_id, " in by_name["siglen128"].creates["SKU"]
    assert "using gin (tenant_id, " in by_name["gin"].creates["description"]
    assert by_name["gin"].thresholds == (0.3, 0.5, 0.7)
    control = by_name["no_tenant"]
    assert "tenant_id" not in control.creates["description"] and control.needs_tenants == 2
    assert variants.KS == (50, 100, 200)


def test_a_run_of_every_variant_fits_the_notices_github_keeps():
    assert len(variants.variants()) + 2 <= variants.NOTICES_PER_STEP == 10


def test_the_variants_asked_for_are_all_or_named():
    assert variants.chosen("all") == variants.variants()
    assert [v.name for v in variants.chosen("gin, baseline")] == ["gin", "baseline"]
    for bad in ("", "siglen999", "baseline;drop"):
        with pytest.raises(ValueError, match="can't read the variants"):
            variants.chosen(bad)


def test_every_variant_query_stays_in_its_tenant_and_binds_its_values():
    tenant = "t-id"
    for build in (variants.nearest, variants.above_threshold):
        for with_similarity in (False, True):
            for expression in variants.EXPRESSIONS.values():
                sql, marks = build(expression, 50, with_similarity)
                assert "tenant_id = %s AND deleted_at IS NULL" in sql and sql.endswith("LIMIT 50")
                # One parameter for each placeholder, in the order the text needs them.
                assert sql.replace("%%", "").count("%s") == len(marks)
                bound = variants.bind(marks, tenant, "the line")
                assert bound.count(tenant) == 1 and bound[marks.index("t")] == tenant
    sql, marks = variants.above_threshold("lower(description)", 50)
    assert "lower(description) %% %s::text" in sql  # the driver turns %% into the one % operator
    sql, marks = variants.count_above_threshold("lower(description)")
    assert sql.replace("%%", "").count("%s") == len(marks) == 2


def test_no_bench_line_carries_its_items_exact_sku_and_all_nine_kinds_come_in_turn():
    rows = [(item.sku, item.description) for item in gen.catalog(7, 1, 90)]
    lines = variants.bench_lines(rows, 7)
    assert lines == variants.bench_lines(rows, 7)
    assert all(line.sku != sizing.sku_key(sku) for line, (sku, _) in zip(lines, rows))
    assert Counter(line.sku_kind for line in lines) == dict.fromkeys(variants.SKU_KINDS, 30)
    assert all(line.sku is None for line in lines if line.sku_kind == "no SKU")
    assert lines[0].description == rows[0][1].lower()  # every third description is as written
    assert all(line.description == line.description.lower() for line in lines)


def test_the_guard_reads_the_kth_and_the_4kth_candidate():
    assert variants.guard([0.9] * 49, 50) == (False, False)  # fewer than K came back: nothing was cut
    assert variants.guard([0.9] * 50 + [0.4] * 150, 50) == (True, False)
    assert variants.guard([0.9] * 200, 50) == (True, True)
    assert variants.guard([0.9] * 49 + [0.49] * 151, 50) == (False, False)


def test_parity_is_todays_code_over_the_fetched_candidates():
    from uuid import uuid4

    from docflow_core import matching

    made = gen.catalog(7, 1, 400)
    items = [matching.CatalogItem(uuid4(), i.sku, i.description, i.unit_of_measure) for i in made]
    catalog = matching.Catalog(items)
    line = variants.bench_lines([(items[0].sku, items[0].description)], 7)[0]
    reference = variants.resolve(matching, line, catalog)
    assert reference.candidates and reference.candidates[0].item_id == items[0].item_id

    everything = variants.from_candidates(matching, line, catalog, [i.item_id for i in items])
    assert variants.outcome(everything) == variants.outcome(reference)
    assert variants.recorded(everything) == variants.recorded(reference)

    # A fetch that misses the best item is a difference the job counts.
    without_it = variants.from_candidates(matching, line, catalog, [i.item_id for i in items[1:]])
    assert variants.recorded(without_it) != variants.recorded(reference)
    # An id the catalog does not hold, or one fetched by both lists, changes nothing.
    doubled = [i.item_id for i in items] + [items[0].item_id, uuid4()]
    again = variants.from_candidates(matching, line, catalog, doubled)
    assert variants.recorded(again) == variants.recorded(reference)


def _bench_catalog_and_lines(count: int, per_family: int):
    from uuid import uuid4

    from docflow_core import matching

    made = list(gen.catalog(7, 1, count))
    catalog = matching.Catalog(
        [matching.CatalogItem(uuid4(), i.sku, i.description, i.unit_of_measure) for i in made]
    )
    rows = [(i.sku, i.description) for i in made]
    general = [row for row in rows if row[1].startswith(variants.GENERAL_PREFIX)]
    fasteners = [row for row in rows if not row[1].startswith(variants.GENERAL_PREFIX)]
    lines = {
        "fastener": variants.bench_lines(fasteners[:per_family], 7),
        "general": variants.bench_lines(general[:per_family], 7),
    }
    return matching, catalog, lines


@pytest.mark.parametrize("keep", [500, 100, 3])
def test_the_faster_full_scan_returns_what_todays_full_scan_returns(monkeypatch, keep):
    """Every line, both families, all nine kinds: the same applied match and
    the same five candidates. With a small `FAST_KEEP` the cut falls among
    tied siblings, which is the case that has to widen and look again."""
    monkeypatch.setattr(variants, "FAST_KEEP", keep)
    matching, catalog, lines = _bench_catalog_and_lines(1500, 9)
    fast = variants.FastScan(matching, catalog)
    for family in variants.FAMILIES:
        assert len(lines[family]) == 9
        for line in lines[family]:
            today = variants.resolve(matching, line, catalog)
            faster = fast.resolve(line)
            assert variants.outcome(faster) == variants.outcome(today), (family, line)
            assert variants.recorded(faster) == variants.recorded(today), (family, line)
    assert (fast.widened > 0) == (keep == 3)


def test_the_faster_full_scan_is_measured_against_todays_on_the_same_lines():
    matching, catalog, lines = _bench_catalog_and_lines(600, 9)
    reference = {
        family: [variants.resolve(matching, line, catalog) for line in lines[family][:3]]
        for family in variants.FAMILIES
    }
    found = variants.measure_fast_scan(matching, catalog, lines, reference)
    assert [len(found["ms"][family]) for family in variants.FAMILIES] == [9, 9]
    assert len(found["report"]) == 3
    assert "keys built in" in found["report"][0] and "MB" in found["report"][0]
    assert found["report"][2].count("applied match differs on 0, the five candidates differ on 0") == 2


def test_the_variants_refuse_any_database_not_on_this_machine(capsys):
    code = variants.main(["--mix", "10", "--database-url", "postgresql://u:pw@db.example.test:5432/postgres"])
    assert code == 2
    assert "only runs against a database on this machine" in capsys.readouterr().out


def test_the_workflow_is_started_by_hand_and_reads_no_secret():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow and "mix:" in workflow
    assert "secrets." not in workflow
    assert "127.0.0.1" in workflow and "supabase.com" not in workflow
    # The inputs are passed as environment values, never pasted into the shell line.
    for script in ("catalog_sizing.py", "index_variants.py"):
        run_line = next(line for line in workflow.splitlines() if script in line and "run:" in line)
        assert "${{" not in run_line
    assert "variants:" in workflow and "if: ${{ !inputs.variants }}" in workflow
    assert "runs-on: ubuntu-24.04" in workflow
    # Under GitHub's own 360-minute stop, so the job ends on its own terms.
    assert "timeout-minutes: 350" in workflow
