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
        if "project_members?" in url: return [{"role": "EDITOR"}]
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


def test_ifc_upload_is_signed_then_creates_file_and_job_after_storage_confirmation(monkeypatch):
    auth = FakeAuth(); captured = {}
    class Response:
        status_code = 200
        headers = {}
        def json(self): return {"signedURL": "/object/upload/sign/reg38-evidence/path?token=signed"}
    def post(url, **kwargs): captured.update(url=url, **kwargs); return Response()
    monkeypatch.setattr(module.requests, "post", post)
    monkeypatch.setattr(module.requests, "head", lambda *args, **kwargs: Response())
    repo = Regulation38Repository(auth)
    prepared = repo.create_ifc_upload("token", "00000000-0000-4000-8000-000000000010", "model.ifc", 13)
    assert prepared["signed_url"].startswith("https://example.supabase.co/storage/v1/")
    result = repo.finalize_ifc_upload("token", "user-id", "00000000-0000-4000-8000-000000000010",
                                      prepared["file_id"], "model.ifc", 13, prepared["storage_path"])
    assert result["storage_path"].startswith("projects/00000000-0000-4000-8000-000000000010/models/")
    inserts = [call[2]["json"] for call in auth.calls if call[0] == "POST"]
    assert inserts[0]["original_filename"] == "model.ifc"
    assert inserts[0]["file_size"] == 13
    assert inserts[1]["status"] == "QUEUED"
    assert inserts[1]["progress_percent"] == 0


def test_storage_failure_does_not_create_processing_job(monkeypatch):
    auth = FakeAuth()
    class Missing:
        status_code = 404
        headers = {}
    monkeypatch.setattr(module.requests, "head", lambda *args, **kwargs: Missing())
    with pytest.raises(module.SupabaseAuthError, match="confirmed"):
        Regulation38Repository(auth).finalize_ifc_upload(
            "token", "user", "00000000-0000-4000-8000-000000000010",
            "00000000-0000-4000-8000-000000000011", "model.ifc", 13,
            "projects/00000000-0000-4000-8000-000000000010/models/00000000-0000-4000-8000-000000000011/model.ifc")
    assert not any("ifc_processing_jobs" in call[1] and call[0] == "POST" for call in auth.calls)


@pytest.mark.parametrize("filename", ["model.zip", "model.ifczip", "model.exe"])
def test_invalid_upload_type_is_rejected(filename):
    with pytest.raises(ValueError, match=".ifc"):
        validate_ifc(filename, 100)


def test_ifc_over_limit_is_rejected():
    with pytest.raises(ValueError, match="500 MB"):
        validate_ifc("model.ifc", module.MAX_IFC_BYTES + 1)


def test_unauthorised_ifc_upload_is_rejected():
    class ViewerAuth(FakeAuth):
        def _request_json(self, method, url, **kwargs):
            if "project_members?" in url: return [{"role": "VIEWER"}]
            if "rpc/is_platform_admin" in url: return False
            return super()._request_json(method, url, **kwargs)
    with pytest.raises(module.SupabaseAuthError, match="permission"):
        Regulation38Repository(ViewerAuth()).create_ifc_upload(
            "token", "00000000-0000-4000-8000-000000000010", "model.ifc", 100)


def test_upload_ui_has_one_clickable_dropzone_and_shared_selection_path():
    template = open("templates/saas/reg38_wizard.html", encoding="utf-8").read()
    script = open("static/reg38-ifc-upload.js", encoding="utf-8").read()
    css = open("static/saas.css", encoding="utf-8").read()
    assert 'for="ifc-file"' in template and 'class="drop-zone"' in template
    assert '["dragenter", "dragover", "dragleave", "drop"]' in script
    assert "selectFiles(input.files)" in script and "selectFiles(event.dataTransfer.files)" in script
    assert "upload-filename" in script and "formatSize(file.size)" in script
    assert 'aria-disabled="true"' in template
    assert ".wizard-card .visually-hidden-file" in css
    assert "width:1px" in css and "width:100%; height:1px" not in css


def test_wizard_migration_allows_only_unprocessed_removal_and_private_storage_path():
    sql = open("supabase/migrations/202608280003_reg38_project_wizard.sql", encoding="utf-8").read().lower()
    assert "status='queued'" in sql
    assert "not exists" in sql
    assert "split_part(object_name,'/',2)" in sql
