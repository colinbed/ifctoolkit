from types import SimpleNamespace

import pytest

import ifc_app.reg38_projects as module
from ifc_app.reg38_projects import ProjectCreate, Regulation38Repository, validate_ifc


class FakeAuth:
    settings = SimpleNamespace(project_url="https://example.supabase.co", request_timeout_seconds=10)
    def __init__(self): self.calls = []
    def _headers(self, token): return {"apikey": "key", "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    def _request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if "rpc/can_create_project" in url: return True
        if "rpc/create_reg38_project" in url: return "00000000-0000-4000-8000-000000000010"
        return []


def test_new_project_requires_identity_and_is_created_as_draft():
    with pytest.raises(ValueError, match="reference"):
        ProjectCreate(name="Riverside").payload()
    auth = FakeAuth(); repo = Regulation38Repository(auth)
    repo.create_project("token", ProjectCreate(name="Riverside", project_reference="RIV-01"))
    payload = auth.calls[-1][2]["json"]["project_data"]
    assert payload["project_status"] == "DRAFT"
    assert payload["country"] == "United Kingdom"


def test_project_listing_is_membership_rls_scoped():
    auth = FakeAuth(); Regulation38Repository(auth).list_projects("member-token")
    assert "/project_members?" in auth.calls[-1][1]
    assert auth.calls[-1][2]["access_token"] == "member-token"


def test_ifc_upload_preserves_bytes_and_creates_file_and_job(monkeypatch):
    auth = FakeAuth(); captured = {}
    class Response: status_code = 200
    def post(url, **kwargs): captured.update(url=url, **kwargs); return Response()
    monkeypatch.setattr(module.requests, "post", post)
    result = Regulation38Repository(auth).upload_ifc("token", "user-id", "00000000-0000-4000-8000-000000000010", "model.ifc", b"ISO-10303-21;")
    assert captured["data"] == b"ISO-10303-21;"
    assert result["storage_path"].startswith("projects/00000000-0000-4000-8000-000000000010/models/")
    inserts = [call[2]["json"] for call in auth.calls if call[0] == "POST"]
    assert inserts[0]["original_filename"] == "model.ifc"
    assert inserts[0]["file_size"] == 13
    assert inserts[1]["status"] == "QUEUED"


@pytest.mark.parametrize("filename", ["model.zip", "model.ifczip", "model.exe"])
def test_invalid_upload_type_is_rejected(filename):
    with pytest.raises(ValueError, match=".ifc"):
        validate_ifc(filename, 100)


def test_wizard_migration_allows_only_unprocessed_removal_and_private_storage_path():
    sql = open("supabase/migrations/202608280003_reg38_project_wizard.sql", encoding="utf-8").read().lower()
    assert "status='queued'" in sql
    assert "not exists" in sql
    assert "split_part(object_name,'/',2)" in sql
