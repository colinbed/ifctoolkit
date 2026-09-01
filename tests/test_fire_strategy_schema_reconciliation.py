import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
RECONCILIATION = MIGRATIONS / "202609011300_fire_strategy_schema_reconciliation.sql"


def test_migration_versions_are_unique_except_for_the_documented_production_collision():
    versions = {}
    for migration in MIGRATIONS.glob("*.sql"):
        versions.setdefault(migration.name.split("_", 1)[0], []).append(migration.name)
    collisions = {version: sorted(names) for version, names in versions.items() if len(names) > 1}
    assert collisions == {"202609010001": [
        "202609010001_fire_strategy_review.sql",
        "202609010001_reg38_ifc_lifecycle.sql",
    ]}


def test_worker_project_spaces_select_is_covered_by_schema_health():
    worker = (ROOT / "backend" / "reg38_ifc_worker.py").read_text(encoding="utf-8")
    block = worker.split('existing = self._request("GET", "rest/v1/project_spaces?', 1)[1].split(
        "working =", 1)[0]
    match = re.search(r"&select=(.*?)\"\) or", block, re.S)
    assert match, "worker project_spaces select contract was not found"
    contract = re.sub(r'"\s*\n\s*"', "", match.group(1))
    selected = {field.strip() for field in contract.split(",")}
    sql = RECONCILIATION.read_text(encoding="utf-8").lower()
    for field in selected:
        assert field == "id" or f"('project_spaces','{field}')" in sql


def test_reconciliation_contains_fire_strategy_operational_contract():
    sql = RECONCILIATION.read_text(encoding="utf-8").lower()
    for contract in (
        "add column if not exists working_fields_edited boolean not null default false",
        "create table if not exists public.fire_strategy_reviews",
        "unique (project_id, model_id, ifc_global_id)",
        "fire_strategy_reviews_set_updated_at",
        "fire_strategy_reviews_select",
        "fire_strategy_reviews_write",
        "grant select, insert, update, delete",
        "notify pgrst, 'reload schema'",
    ):
        assert contract in sql
