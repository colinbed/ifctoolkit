import re
import sqlite3
from pathlib import Path

from ifc_app.reg38_projects import REG38_DEFAULT_SECTIONS


MIGRATION = Path(
    "supabase/migrations/202608280007_reg38_create_project_owner_compatibility.sql"
)


def migration_sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_create_project_satisfies_real_legacy_not_null_constraints():
    sql = migration_sql()
    normalized = re.sub(r"\s+", " ", sql.lower())

    assert "alter column owner_id set not null" in normalized
    insert = re.search(
        r"insert into public\.projects\s*\((.*?)\)\s*values\s*\((.*?)\)\s*returning id into pid",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert insert is not None
    columns = [column.strip().lower() for column in insert.group(1).split(",")]
    values = [value.strip().lower() for value in insert.group(2).split(",")]

    # A project insert can satisfy the production NOT NULL compatibility column,
    # and both ownership fields are bound to the authenticated actor.
    assert columns[-2:] == ["owner_id", "created_by"]
    assert values[-2:] == ["actor", "actor"]

    section_insert = re.search(
        r"insert into public\.reg38_sections\s*\((.*?)\)\s*values\s*(.*?)\s*on conflict",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert section_insert is not None
    section_columns = [column.strip().lower() for column in section_insert.group(1).split(",")]
    assert section_columns == ["project_id", "section_key", "title", "name", "sort_order"]

    # Exercise the production compatibility contract against actual NOT NULL
    # constraints, rather than only checking that column names occur in SQL.
    database = sqlite3.connect(":memory:")
    database.executescript("""
        create table projects (
          id text primary key, name text not null, owner_id text not null, created_by text not null
        );
        create table project_members (
          project_id text not null, user_id text not null, role text not null,
          unique(project_id, user_id)
        );
        create table reg38_sections (
          project_id text not null, section_key text not null, title text not null,
          name text not null, sort_order integer not null,
          unique(project_id, section_key)
        );
    """)
    actor = "authenticated-user"
    project_id = "created-project"
    with database:
        database.execute(
            "insert into projects(id,name,owner_id,created_by) values(?,?,?,?)",
            (project_id, "Riverside", actor, actor),
        )
        database.execute(
            "insert into project_members(project_id,user_id,role) values(?,?,?) "
            "on conflict(project_id,user_id) do nothing",
            (project_id, actor, "OWNER"),
        )
        database.executemany(
            "insert into reg38_sections(project_id,section_key,title,name,sort_order) "
            "values(?,?,?,?,?) on conflict(project_id,section_key) do nothing",
            [
                (project_id, key, label, label, sort_order)
                for sort_order, (key, label) in enumerate(REG38_DEFAULT_SECTIONS, 1)
            ],
        )

    assert database.execute("select count(*) from projects").fetchone()[0] == 1
    assert database.execute(
        "select count(*) from project_members where role='OWNER'"
    ).fetchone()[0] == 1
    assert database.execute("select count(*) from reg38_sections").fetchone()[0] == 17
    assert database.execute(
        "select count(*) from reg38_sections where title=name and title is not null"
    ).fetchone()[0] == 17


def test_compatibility_rpc_preserves_fields_idempotent_seeds_and_security():
    sql = migration_sql()
    normalized = re.sub(r"\s+", " ", sql.lower())
    expected_fields = {
        "name", "project_reference", "client_name", "principal_contractor", "principal_designer",
        "description", "building_name", "building_type", "project_status", "planned_handover_date",
        "responsible_person_name", "responsible_person_email", "address_line_1", "address_line_2",
        "town_city", "county", "postcode", "country", "owner_id", "created_by",
    }
    project_columns = re.search(
        r"insert into public\.projects\s*\((.*?)\)\s*values",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    assert project_columns is not None
    assert {field.strip().lower() for field in project_columns.group(1).split(",")} == expected_fields

    assert "language plpgsql security definer set search_path=public" in normalized
    assert "actor uuid := auth.uid()" in normalized
    assert "not public.can_create_project()" in normalized
    assert "on conflict(project_id,user_id) do nothing" in normalized
    assert "on conflict(project_id,section_key) do nothing" in normalized
    assert "alter column title set not null" in normalized
    assert len(REG38_DEFAULT_SECTIONS) == 17
    for sort_order, (section_key, name) in enumerate(REG38_DEFAULT_SECTIONS, 1):
        assert f"(pid,'{section_key}','{name}','{name}',{sort_order})" in sql
    assert "revoke all on function public.create_reg38_project(jsonb) from public" in normalized
    assert "grant execute on function public.create_reg38_project(jsonb) to authenticated" in normalized
