from pathlib import Path


MIGRATION = Path("supabase/migrations/202609010002_reg38_section_status_default.sql")


def test_status_defaults_keep_legacy_and_canonical_representations_distinct():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "alter column status set default 'Not Started'" in sql
    assert "alter column completion_status set default 'NOT_STARTED'" in sql
    assert "update public.reg38_sections" not in sql.lower()
    for value in ("Not Started", "In Progress", "Ready for Review", "Complete", "Not Applicable"):
        assert value in sql


def test_schema_health_detects_status_default_constraint_and_data_drift():
    sql = MIGRATION.read_text(encoding="utf-8")
    for diagnostic in (
        "default:reg38_sections.status",
        "default:reg38_sections.completion_status",
        "constraint:reg38_sections.status",
        "data:reg38_sections.status",
    ):
        assert diagnostic in sql
    assert "pg_get_constraintdef(c.oid) not like '%Not Started%'" in sql
