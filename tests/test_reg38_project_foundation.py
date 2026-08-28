import re
from datetime import date
from pathlib import Path

import pytest

from ifc_app.reg38_projects import ProjectCreate, REG38_DEFAULT_SECTIONS, Regulation38Repository
from ifc_app.supabase_auth import SupabaseAuthError

MIGRATION = Path("supabase/migrations/202608280002_reg38_project_foundation.sql")


def sql():
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_has_requested_layered_tables_and_unique_ifc_identity():
    text = sql().lower()
    expected = {
        "projects", "project_members", "buildings", "building_storeys", "ifc_files", "ifc_processing_jobs",
        "ifc_objects", "ifc_object_properties", "ifc_object_relationships", "project_spaces", "project_zones",
        "project_zone_members", "project_grids", "project_grid_axes", "fire_requirements", "fire_object_reviews",
        "project_plans", "plan_objects", "reg38_sections", "reg38_requirements", "reg38_evidence",
    }
    created = set(re.findall(r"create table public\.([a-z0-9_]+)", text))
    assert expected <= created
    assert "unique(ifc_file_id, ifc_global_id)" in text
    assert "no duplicate reg38_projects table" in text


def test_all_project_tables_enable_rls_and_source_data_has_no_update_policy():
    text = sql().lower()
    rls_array = re.search(r"-- rls:.*?array\[(.*?)\]", text, re.S).group(1)
    for table in ("projects", "project_members", "ifc_objects", "project_spaces", "fire_requirements", "reg38_evidence"):
        assert f"'{table}'" in rls_array
    assert "ifc_objects_update" not in text
    assert "ifc_object_properties_update" not in text
    assert "ifc_files_update" not in text
    assert "created_by=(select auth.uid()) and public.can_create_project()" in text
    assert "members cannot change or remove their own project role" in text
    assert "uploaded_by=(select auth.uid())" in text
    assert "protect_reg38_actor_fields" in text


def test_cross_project_guards_and_storage_are_membership_scoped():
    text = sql().lower()
    assert "enforce_reg38_project_consistency" in text
    assert "zone and space belong to different projects" in text
    assert "evidence requirement belongs to another project" in text
    assert "public.is_project_member(public.storage_project_id(name))" in text
    assert "public.can_edit_project(public.storage_project_id(name))" in text


def test_seed_contains_canonical_17_sections_in_order():
    text = sql()
    assert len(REG38_DEFAULT_SECTIONS) == 17
    positions = [text.index(f"'{key}','{name}',{index}") for index, (key, name) in enumerate(REG38_DEFAULT_SECTIONS, 1)]
    assert positions == sorted(positions)
    assert "automatically demonstrate regulatory compliance" in text


def test_project_create_validation_and_date_serialisation():
    with pytest.raises(ValueError, match="name"):
        ProjectCreate(name="  ").payload()
    payload = ProjectCreate(name="  Riverside  ", project_reference="RIV-1", planned_handover_date=date(2027, 4, 1)).payload()
    assert payload["name"] == "Riverside"
    assert payload["planned_handover_date"] == "2027-04-01"


def test_repository_uses_authenticated_rpc_and_rls_for_listing(monkeypatch):
    calls = []

    class FakeAuth:
        settings = type("Settings", (), {"project_url": "https://example.supabase.co"})()

        def _request_json(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if "create_reg38_project" in url:
                return "project-id"
            return [{"role": "OWNER", "projects": {"id": "project-id", "name": "Riverside", "project_reference": "RIV-1", "project_status": "DRAFT"}}]

    repository = Regulation38Repository(FakeAuth())
    assert repository.create_project("user-token", ProjectCreate(name="Riverside", project_reference="RIV-1")) == "project-id"
    projects = repository.list_projects("user-token")
    assert projects[0].role == "OWNER"
    assert all(call[2]["access_token"] == "user-token" for call in calls)
    assert calls[0][1].endswith("/rest/v1/rpc/create_reg38_project")


def test_permission_rpc_failure_is_not_a_denial_and_verified_super_admin_survives():
    class AdminAuth:
        settings = type("Settings", (), {"project_url": "https://example.supabase.co"})()
        def _request_json(self, method, url, **kwargs):
            if url.endswith("/rpc/can_create_project"):
                raise SupabaseAuthError("Permission unavailable", status_code=503, detail="function missing")
            if url.endswith("/rpc/is_platform_admin"):
                return True
            raise AssertionError(url)

    permission = Regulation38Repository(AdminAuth()).resolve_create_permission("token")
    assert permission.allowed is True
    assert permission.check_failed is True
    assert permission.source == "is_platform_admin"


def test_permission_rpc_failure_without_admin_fallback_raises():
    class MemberAuth:
        settings = type("Settings", (), {"project_url": "https://example.supabase.co"})()
        def _request_json(self, method, url, **kwargs):
            if url.endswith("/rpc/can_create_project"):
                raise SupabaseAuthError("Permission unavailable", status_code=503, detail="timeout")
            return False

    with pytest.raises(SupabaseAuthError, match="timeout"):
        Regulation38Repository(MemberAuth()).resolve_create_permission("token")


def test_security_migration_separates_roles_and_checks_schema():
    text = Path("supabase/migrations/202608280006_reg38_admin_permissions.sql").read_text(encoding="utf-8").lower()
    assert "security_role = 'super_admin'" in text
    assert "security_role = 'admin' and can_create_projects" in text
    can_create_body = text.split("create or replace function public.can_create_project()", 1)[1].split("$$;", 1)[0]
    assert "account_level" not in can_create_body
    for required in ("projects", "project_members", "reg38_sections", "reg38_project_scope", "ifc_files", "ifc_processing_jobs"):
        assert f"('{required}'" in text
