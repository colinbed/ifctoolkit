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
        content = b'"job-id"'
        def json(self): return {"signedURL": "/object/upload/sign/project-files/path?token=signed"}
    def post(url, **kwargs): captured.update(url=url, **kwargs); return Response()
    monkeypatch.setattr(module.requests, "post", post)
    monkeypatch.setattr(module.requests, "head", lambda *args, **kwargs: Response())
    class DataResponse:
        status_code = 200
        content = b"yes"
        text = ""
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload
    def data_request(method, url, **kwargs):
        if "rpc/finalize_ifc_upload" in url:
            auth.calls.append((method, url, kwargs))
            return DataResponse("job-id")
        if "ifc_files?" in url:
            return DataResponse([{"id": kwargs.get("json", {}).get("target_file"), "status": "UPLOADED",
                                  "storage_path": prepared["storage_path"]}])
        return DataResponse([{"id": "00000000-0000-4000-8000-000000000010"}])
    monkeypatch.setattr(module.requests, "request", data_request)
    repo = Regulation38Repository(auth)
    prepared = repo.create_ifc_upload("token", "00000000-0000-4000-8000-000000000010", "model.ifc", 13)
    assert prepared["signed_url"].startswith("https://example.supabase.co/storage/v1/")
    result = repo.finalize_ifc_upload("token", "user-id", "00000000-0000-4000-8000-000000000010",
                                      prepared["file_id"], "model.ifc", 13, prepared["storage_path"])
    assert result["storage_path"].startswith("projects/00000000-0000-4000-8000-000000000010/models/")
    finalize = next(call[2]["json"] for call in auth.calls if "rpc/finalize_ifc_upload" in call[1])
    assert finalize["original_name"] == "model.ifc"
    assert finalize["object_size"] == 13
    assert "/original/model.ifc" in finalize["object_path"]
    assert captured["url"].startswith("https://example.supabase.co/storage/v1/object/upload/sign/project-files/projects/")
    assert captured["json"] == {}
    assert "project-files/project-files" not in captured["url"]


def test_valid_45_mb_ifc_is_sent_for_signing(monkeypatch):
    class Response:
        status_code = 200
        def json(self): return {"url": "/object/upload/sign/project-files/path?token=signed"}
    called = {}
    monkeypatch.setattr(module.requests, "post", lambda url, **kwargs: (called.update(url=url) or Response()))
    result = Regulation38Repository(FakeAuth()).create_ifc_upload(
        "token", "00000000-0000-4000-8000-000000000010", "../building model.ifc", 45 * 1024 * 1024)
    assert called["url"].endswith("/original/building%20model.ifc")
    assert result["storage_path"].endswith("/original/building model.ifc")


@pytest.mark.parametrize("status,body", [
    (400, {"statusCode": "400", "error": "Bad Request", "message": "Bucket not found"}),
    (401, {"statusCode": "401", "error": "Unauthorized", "message": "Invalid JWT"}),
    (403, {"statusCode": "403", "error": "Forbidden", "message": "new row violates row-level security policy"}),
])
def test_signed_upload_errors_are_logged_safely_and_return_reference(monkeypatch, caplog, status, body):
    class Response:
        status_code = status
        text = ""
        def json(self): return body
    monkeypatch.setattr(module.requests, "post", lambda *args, **kwargs: Response())
    with caplog.at_level("ERROR"), pytest.raises(module.SupabaseAuthError) as caught:
        Regulation38Repository(FakeAuth()).create_ifc_upload(
            "secret-user-jwt", "00000000-0000-4000-8000-000000000010", "model.ifc", 100)
    assert caught.value.status_code == 502
    assert caught.value.public_message.startswith("Storage could not prepare this upload. Reference: ")
    assert f"storage_http_status={status}" in caplog.text
    assert body["message"] in caplog.text
    assert "secret-user-jwt" not in caplog.text and "Bearer" not in caplog.text and "apikey" not in caplog.text


def test_storage_bucket_health_reports_existing_and_missing_bucket(monkeypatch, caplog):
    class Response:
        text = ""
        def __init__(self, status): self.status_code = status
        def json(self): return ({"id": "project-files"} if self.status_code == 200 else
                                {"statusCode": "404", "error": "not_found", "message": "Bucket not found"})
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-secret")
    seen = {}
    monkeypatch.setattr(module.requests, "get", lambda *args, **kwargs: (seen.update(kwargs) or Response(200)))
    assert module.check_reg38_storage_bucket(FakeAuth()) is True
    assert seen["headers"]["Authorization"] == "Bearer service-secret"
    monkeypatch.setattr(module.requests, "get", lambda *args, **kwargs: Response(404))
    with caplog.at_level("ERROR"):
        assert module.check_reg38_storage_bucket(FakeAuth()) is False
    assert "Bucket not found" in caplog.text


def test_storage_failure_does_not_create_processing_job(monkeypatch):
    auth = FakeAuth()
    class Missing:
        status_code = 404
        headers = {}
    monkeypatch.setattr(module.requests, "head", lambda *args, **kwargs: Missing())
    with pytest.raises(module.SupabaseAuthError, match="project could not be updated"):
        Regulation38Repository(auth).finalize_ifc_upload(
            "token", "user", "00000000-0000-4000-8000-000000000010",
            "00000000-0000-4000-8000-000000000011", "model.ifc", 13,
            "projects/00000000-0000-4000-8000-000000000010/models/00000000-0000-4000-8000-000000000011/original/model.ifc")
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


def test_project_files_migration_is_private_project_scoped_and_atomic():
    sql = open("supabase/migrations/202608280008_project_files_and_model_scan.sql", encoding="utf-8").read().lower()
    assert "'project-files', 'project-files', false, 524288000" in sql
    assert "public.is_project_member(public.storage_project_id(name))" in sql
    assert "public.can_edit_project(public.storage_project_id(name))" in sql
    assert "models/%s/original/%s" in sql
    assert "insert into public.ifc_files" in sql and "insert into public.ifc_processing_jobs" in sql


def test_completion_recovery_is_idempotent_and_audits_schema():
    sql = open("supabase/migrations/202608300001_reg38_ifc_completion_recovery.sql", encoding="utf-8").read().lower()
    assert "on conflict (id) do update" in sql
    assert "status in ('queued','running')" in sql
    for name in ("projects", "project_members", "ifc_files", "ifc_processing_jobs",
                 "create_reg38_project", "save_reg38_scope", "finalize_ifc_upload"):
        assert name in sql


def test_upload_ui_retries_finalisation_without_reupload_or_cleanup():
    script = open("static/reg38-ifc-upload.js", encoding="utf-8").read()
    assert "IFC uploaded, but the project could not be updated." in script
    assert 'closest(".retry-finalize")' in script
    assert "finalizeUpload().catch" in script
    assert "prepared && !pendingFinalization" in script
