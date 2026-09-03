import asyncio
import json
import os
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

import app as app_module
import ifc_app.saas as saas
import ifc_app.supabase_auth as supabase_auth
from ifc_app.firetrace_wizard import FIRETRACE_WIZARD_STEPS, FireTraceProgress, firetrace_wizard_url


@dataclass
class ASGIResponse:
    status_code: int
    headers: list[tuple[str, str]]
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def header(self, name: str, default: str = "") -> str:
        wanted = name.lower()
        return next((value for key, value in self.headers if key == wanted), default)


def request(method: str, target: str, *, body: bytes = b"", headers: dict[str, str] | None = None) -> ASGIResponse:
    parsed = urlsplit(target)
    response_messages = []

    async def run_request():
        request_sent = False

        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            response_messages.append(message)

        raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
        if body and not any(key == b"content-length" for key, _ in raw_headers):
            raw_headers.append((b"content-length", str(len(body)).encode()))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method.upper(),
            "scheme": "http",
            "path": parsed.path,
            "raw_path": parsed.path.encode(),
            "query_string": parsed.query.encode(),
            "root_path": "",
            "headers": raw_headers,
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
        await app_module.app(scope, receive, send)

    asyncio.run(run_request())
    start = next(message for message in response_messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"") for message in response_messages if message["type"] == "http.response.body"
    )
    response_headers = [(key.decode().lower(), value.decode()) for key, value in start.get("headers", [])]
    return ASGIResponse(start["status"], response_headers, response_body)


def test_public_auth_pages_and_anonymous_navigation_render():
    homepage = request("GET", "/")
    assert homepage.status_code == 200
    assert ">Log in<" in homepage.text
    assert ">Create account<" in homepage.text

    login = request("GET", "/login")
    assert login.status_code == 200
    assert login.text.count('name="email"') == 1
    assert login.text.count('name="password"') == 1

    signup = request("GET", "/signup")
    assert signup.status_code == 200
    assert signup.text.count('name="email"') == 1
    assert signup.text.count('name="password"') == 1

    forgot = request("GET", "/forgot-password")
    assert forgot.status_code == 200
    assert 'name="password"' not in forgot.text
    assert request("GET", "/reset-password").status_code == 200
    assert request("GET", "/logout").status_code == 405


def test_private_routes_redirect_anonymous_users_to_login():
    for path in ("/app", "/app/projects", "/app/account", "/app/regulation-38"):
        response = request("GET", path)
        assert response.status_code == 303
        assert response.header("location") == f"/login?next={path}"


def test_env_local_does_not_override_deployment_environment(monkeypatch, tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "APP_URL=http://from-file.example\nSUPABASE_URL=https://local-ref.supabase.co\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_URL", "https://deployment.example")
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    supabase_auth.load_env_local(env_file)

    assert os.environ["APP_URL"] == "https://deployment.example"
    assert os.environ["SUPABASE_URL"] == "https://local-ref.supabase.co"


def test_password_reset_uses_app_url_reset_page(monkeypatch):
    settings = supabase_auth.AuthSettings(
        app_url="https://ifctoolkit.co.uk",
        supabase_url="https://project-ref.supabase.co",
        publishable_key="publishable-key",
    )
    service = supabase_auth.SupabaseAuthService(settings)
    captured = {}

    def fake_request_json(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return {}

    monkeypatch.setattr(service, "_request_json", fake_request_json)

    service.send_password_reset("member@example.com")

    request_url = urlsplit(captured["url"])
    assert captured["method"] == "POST"
    assert request_url.path == "/auth/v1/recover"
    assert parse_qs(request_url.query)["redirect_to"] == ["https://ifctoolkit.co.uk/reset-password"]
    assert captured["json"] == {"email": "member@example.com"}


class RecoverySupabaseAuth:
    user = {"id": "recovery-user", "email": "member@example.com", "user_metadata": {}}

    def validate_session(self, session):
        if session.get("access_token") != "valid-access" or session.get("refresh_token") != "valid-refresh":
            raise supabase_auth.SupabaseAuthError("Session expired.", status_code=401)
        return self.user, dict(session)


def test_expired_access_token_refreshes_before_user_lookup(monkeypatch):
    settings = supabase_auth.AuthSettings(
        supabase_url="https://example.supabase.co",
        publishable_key="public-key",
        app_url="https://ifctoolkit.example",
    )
    service = supabase_auth.SupabaseAuthService(settings)
    calls = []

    def fake_refresh(refresh_token):
        calls.append(("refresh", refresh_token))
        return {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_at": 4102444800,
            "user": {"id": "user-1"},
        }

    monkeypatch.setattr(service, "refresh_session", fake_refresh)
    monkeypatch.setattr(service, "get_user", lambda token: (_ for _ in ()).throw(AssertionError("expired token was used")))

    user, refreshed = service.validate_session(
        {"access_token": "expired-access", "refresh_token": "valid-refresh", "expires_at": 1}
    )

    assert calls == [("refresh", "valid-refresh")]
    assert user == {"id": "user-1"}
    assert refreshed["access_token"] == "new-access"


def test_recovery_session_handoff_enables_reset_form(monkeypatch):
    fake = RecoverySupabaseAuth()
    monkeypatch.setattr(saas, "get_auth_service", lambda: fake)
    monkeypatch.setattr(supabase_auth, "get_auth_service", lambda: fake)
    payload = {
        "access_token": "valid-access",
        "refresh_token": "valid-refresh",
        "token_type": "bearer",
        "expires_in": "3600",
        "expires_at": "1800000000",
        "type": "recovery",
    }

    handoff = request(
        "POST",
        "/auth/session",
        body=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )

    assert handoff.status_code == 200
    assert handoff.text == '{"redirect":"/reset-password"}'
    session_cookie = handoff.header("set-cookie").split(";", 1)[0]
    reset_page = request("GET", "/reset-password", headers={"cookie": session_cookie})
    assert reset_page.status_code == 200
    assert 'name="password" required minlength="8" autocomplete="new-password" disabled' not in reset_page.text
    assert '<button class="button" type="submit" disabled>' not in reset_page.text


def test_invalid_recovery_session_fails_without_cookie(monkeypatch):
    fake = RecoverySupabaseAuth()
    monkeypatch.setattr(saas, "get_auth_service", lambda: fake)
    payload = {"access_token": "expired", "refresh_token": "expired", "type": "recovery"}

    handoff = request(
        "POST",
        "/auth/session",
        body=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )

    assert handoff.status_code == 401
    assert not handoff.header("set-cookie")


def test_reset_page_hands_off_recovery_hash_and_removes_tokens_from_url():
    reset_page = request("GET", "/reset-password")

    assert 'hash.get("access_token")' in reset_page.text
    assert 'hash.get("refresh_token")' in reset_page.text
    assert 'hash.get("type") !== "recovery"' in reset_page.text
    assert 'fetch("/auth/session"' in reset_page.text
    assert 'history.replaceState(null, "", "/reset-password")' in reset_page.text
    assert reset_page.text.index('history.replaceState(null, "", "/reset-password")') < reset_page.text.index(
        'fetch("/auth/session"'
    )
    assert 'window.location.replace("/reset-password")' in reset_page.text
    assert "Your password reset link is invalid or has expired. Request a new password reset link." in reset_page.text
    assert "Open the latest reset link from your email." in reset_page.text
    assert '<button class="button" type="submit" disabled>' in reset_page.text


class FakeSupabaseAuth:
    user = {
        "id": "00000000-0000-4000-8000-000000000001",
        "email": "member@example.com",
        "user_metadata": {"name": "Test Member"},
    }

    def sign_in(self, email: str, password: str):
        assert email == "member@example.com"
        assert password == "correct horse battery staple"
        return {
            "access_token": "mock-access-token",
            "refresh_token": "mock-refresh-token",
            "expires_in": 3600,
            "user": self.user,
        }

    def validate_session(self, session):
        assert session["access_token"] == "mock-access-token"
        return self.user, dict(session)

    def sign_out(self, access_token: str):
        assert access_token == "mock-access-token"

    def get_profile(self, access_token: str, user_id: str):
        assert access_token == "mock-access-token"
        assert user_id == self.user["id"]
        return {"id": user_id, "full_name": "Test Member", "account_level": "standard",
                "subscription_status": "trial", "trial_started_at": "2026-08-28T00:00:00Z",
                "trial_ends_at": "2099-11-26T00:00:00Z"}


def test_mocked_supabase_login_private_navigation_and_logout(monkeypatch):
    fake = FakeSupabaseAuth()
    monkeypatch.setattr(saas, "get_auth_service", lambda: fake)
    monkeypatch.setattr(supabase_auth, "get_auth_service", lambda: fake)

    form = urlencode(
        {
            "email": "member@example.com",
            "password": "correct horse battery staple",
            "next": "/app",
        }
    ).encode()
    login = request(
        "POST",
        "/login",
        body=form,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert login.status_code == 303
    assert login.header("location") == "/app"
    session_cookie = login.header("set-cookie").split(";", 1)[0]
    assert session_cookie.startswith("ifc_session=")

    private_app = request("GET", "/app", headers={"cookie": session_cookie})
    assert private_app.status_code == 200
    assert "Projects" in private_app.text
    assert "Regulation 38" in private_app.text
    assert ">Account<" in private_app.text
    assert ">Log out<" in private_app.text
    assert "Create account" not in private_app.text

    account = request("GET", "/account", headers={"cookie": session_cookie})
    assert account.status_code == 200
    assert 'value="Test Member"' in account.text
    assert 'value="member@example.com"' in account.text

    regulation_38 = request("GET", "/app/regulation-38", headers={"cookie": session_cookie})
    assert regulation_38.status_code == 308
    assert regulation_38.header("location") == "/app/firetrace"
    firetrace = request("GET", "/app/firetrace", headers={"cookie": session_cookie})
    assert firetrace.status_code == 200
    assert "Fire Information Compliance Platform" in firetrace.text
    assert "does not replace competent-person judgement" in firetrace.text

    logout = request("POST", "/logout", headers={"cookie": session_cookie})
    assert logout.status_code == 303
    assert logout.header("location") == "/"
    assert "Max-Age=0" in logout.header("set-cookie")

    cleared_cookie = logout.header("set-cookie").split(";", 1)[0]
    after_logout = request("GET", "/app", headers={"cookie": cleared_cookie})
    assert after_logout.status_code == 303
    assert after_logout.header("location") == "/login?next=/app"


class Regulation38AdminAuth(FakeSupabaseAuth):
    settings = type("Settings", (), {"project_url": "https://example.supabase.co"})()
    list_fails = False
    projects = []

    def __init__(self):
        super().__init__()
        self.created_project_data = None

    def _request_json(self, method, url, **kwargs):
        if url.endswith("/rpc/can_create_project"):
            return True
        if "project_members?" in url:
            if self.list_fails:
                raise supabase_auth.SupabaseAuthError("Projects could not be loaded.", status_code=400, detail="projects.building_name does not exist")
            return self.projects
        if url.endswith("/rpc/reg38_schema_health"):
            return {"valid": False, "missing": ["projects.building_name"]}
        if url.endswith("/rpc/create_reg38_project"):
            self.created_project_data = kwargs["json"]["project_data"]
            return "00000000-0000-4000-8000-000000000038"
        raise AssertionError(url)


def _admin_cookie(monkeypatch, fake):
    monkeypatch.setattr(saas, "get_auth_service", lambda: fake)
    monkeypatch.setattr(supabase_auth, "get_auth_service", lambda: fake)
    body = urlencode({"email": "member@example.com", "password": "correct horse battery staple", "next": "/app"}).encode()
    response = request("POST", "/login", body=body, headers={"content-type": "application/x-www-form-urlencoded"})
    return response.header("set-cookie").split(";", 1)[0]


def test_admin_landing_keeps_create_action_when_project_list_fails(monkeypatch, caplog):
    fake = Regulation38AdminAuth()
    fake.list_fails = True
    cookie = _admin_cookie(monkeypatch, fake)
    response = request("GET", "/app/firetrace/projects", headers={"cookie": cookie})
    assert response.status_code == 200
    assert '>New Project</a>' in response.text
    assert "Projects could not be loaded" in response.text
    assert "Please try again." in response.text
    assert "No FireTrace projects yet" not in response.text
    assert "projects.building_name" in caplog.text


def test_admin_zero_projects_has_create_cta_and_direct_wizard_is_independent(monkeypatch):
    fake = Regulation38AdminAuth()
    cookie = _admin_cookie(monkeypatch, fake)
    landing = request("GET", "/app/firetrace/projects", headers={"cookie": cookie})
    assert "No FireTrace projects yet" in landing.text
    assert '>New FireTrace Project</a>' in landing.text
    assert "FIRETRACE PROJECTS" in landing.text
    direct = request("GET", "/app/regulation-38/projects/new", headers={"cookie": cookie})
    assert direct.status_code == 200
    assert "Project Scope" in direct.text


def test_application_new_project_post_calls_rpc_and_redirects_to_scope(monkeypatch):
    fake = Regulation38AdminAuth()
    cookie = _admin_cookie(monkeypatch, fake)
    body = urlencode({"name": "FireTrace House", "project_reference": "FT-038"}).encode()
    response = request("POST", "/app/projects/new", body=body, headers={
        "cookie": cookie, "content-type": "application/x-www-form-urlencoded",
    })
    assert response.status_code == 303
    assert response.header("location") == (
        "/app/firetrace/projects/00000000-0000-4000-8000-000000000038/setup/scope"
    )
    assert fake.created_project_data["name"] == "FireTrace House"
    assert fake.created_project_data["project_reference"] == "FT-038"


def test_every_canonical_firetrace_wizard_route_renders_and_navigation_exists(monkeypatch):
    """Exercise routing and rendering for every step, including the former NameError path."""
    fake = Regulation38AdminAuth()
    cookie = _admin_cookie(monkeypatch, fake)
    project = {"id": "project-id", "name": "Draft FireTrace", "project_reference": "FT-1",
               "project_status": "DRAFT", "spatial_ifc_unavailable": True}
    repository = saas.Regulation38Repository
    monkeypatch.setattr(repository, "get_project", lambda self, token, project_id: project)
    monkeypatch.setattr(repository, "get_sections", lambda self, token, project_id: [])
    monkeypatch.setattr(repository, "list_ifc_files", lambda self, token, project_id: [])
    monkeypatch.setattr(repository, "model_scan", lambda self, token, project_id, user_id: {
        "file": None, "job": None, "warnings": []})
    monkeypatch.setattr(repository, "spatial_review", lambda self, token, project_id: {
        "spaces": [], "zones": [], "grids": [], "members": [], "can_admin": True})
    monkeypatch.setattr(repository, "fire_strategy", lambda self, token, project_id, user_id: {
        "ready": False, "error": "Model Scan data is missing."})
    monkeypatch.setattr(repository, "firetrace_progress", lambda self, token, project:
                        FireTraceProgress("scope", frozenset({"details"}), frozenset({"details", "scope"})))
    monkeypatch.setattr(repository, "get_scope", lambda self, token, project_id: None)

    for index, (slug, _) in enumerate(FIRETRACE_WIZARD_STEPS, 1):
        response = request("GET", firetrace_wizard_url("project-id", index), headers={"cookie": cookie})
        assert response.status_code == 200, slug
        if index > 1:
            assert firetrace_wizard_url("project-id", index - 1) in response.text
        if index < len(FIRETRACE_WIZARD_STEPS) and index not in {1, 2, 3, 4, 5, 6}:
            assert firetrace_wizard_url("project-id", index + 1) in response.text

    # A backend read failure on Step 6 is a recoverable workspace state, not a
    # gateway error, and the user retains a route back to Spatial Review.
    def failed_strategy(self, token, project_id, user_id):
        raise supabase_auth.SupabaseAuthError("Projects could not be loaded.", status_code=400,
                                              detail="production schema mismatch")
    monkeypatch.setattr(repository, "fire_strategy", failed_strategy)
    response = request("GET", firetrace_wizard_url("project-id", 6), headers={"cookie": cookie})
    assert response.status_code == 200
    assert "setup progress has been preserved" in response.text
    assert firetrace_wizard_url("project-id", 5) in response.text


def test_dashboard_continue_setup_and_legacy_wizard_routes_are_canonical(monkeypatch):
    fake = Regulation38AdminAuth()
    cookie = _admin_cookie(monkeypatch, fake)
    project = {"id": "project-id", "name": "Draft FireTrace", "project_reference": "FT-1",
               "project_status": "DRAFT"}
    monkeypatch.setattr(saas.Regulation38Repository, "get_project",
                        lambda self, token, project_id: project)
    monkeypatch.setattr(saas.Regulation38Repository, "firetrace_progress", lambda self, token, project:
                        FireTraceProgress("model", frozenset({"details", "scope"}),
                                          frozenset({"details", "scope", "model"})))

    dashboard = request("GET", "/app/firetrace/projects/project-id", headers={"cookie": cookie})
    assert dashboard.status_code == 200
    assert f'href="{firetrace_wizard_url("project-id", 3)}">Continue setup' in dashboard.text
    for old_slug, (new_slug, _) in zip(
        ("details", "scope", "upload-ifc", "model-scan", "spaces-zones", "fire-construction",
         "plans", "information-requirements", "summary"),
        FIRETRACE_WIZARD_STEPS,
    ):
        legacy = request("GET", f"/app/regulation-38/projects/project-id/setup/{old_slug}",
                         headers={"cookie": cookie})
        assert legacy.status_code == 308
        assert legacy.header("location") == f"/app/firetrace/projects/project-id/setup/{new_slug}"


def test_member_cannot_see_create_or_open_direct_wizard(monkeypatch):
    fake = Regulation38AdminAuth()
    original = fake._request_json
    fake._request_json = lambda method, url, **kwargs: False if url.endswith("/rpc/can_create_project") else original(method, url, **kwargs)
    cookie = _admin_cookie(monkeypatch, fake)
    landing = request("GET", "/app/firetrace/projects", headers={"cookie": cookie})
    assert "+ New Project" not in landing.text and "+ Create Project" not in landing.text
    assert "No FireTrace projects yet" in landing.text
    assert request("GET", "/app/regulation-38/projects/new", headers={"cookie": cookie}).status_code == 403


def test_admin_user_lookup_uses_correct_endpoint_and_logs_safe_404(monkeypatch, caplog):
    settings = supabase_auth.AuthSettings(
        app_url="https://app.example", supabase_url="https://project.supabase.co",
        publishable_key="publishable",
    )
    service = supabase_auth.SupabaseAuthService(settings)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-secret")
    seen = {}

    class Response:
        status_code = 404
        content = b'{"code":"user_not_found","message":"User not found"}'
        text = content.decode()
        def json(self): return {"code": "user_not_found", "message": "User not found"}

    monkeypatch.setattr(supabase_auth.requests, "request", lambda method, url, **kwargs: (seen.update(method=method, url=url, kwargs=kwargs) or Response()))
    user_id = "10000000-0000-4000-8000-000000000001"
    with caplog.at_level("WARNING"), pytest.raises(supabase_auth.SupabaseAuthError):
        service.get_user_by_id(user_id)
    assert seen["method"] == "GET"
    assert seen["url"] == f"https://project.supabase.co/auth/v1/admin/users/{user_id}"
    assert f"endpoint=/auth/v1/admin/users/{user_id}" in caplog.text
    assert f"user_id={user_id}" in caplog.text
    assert "credential=service_role status=404" in caplog.text
    assert "User not found" in caplog.text
    assert "service-secret" not in caplog.text and "Authorization" not in caplog.text


def test_postgrest_failure_uses_data_log_label(monkeypatch, caplog):
    settings = supabase_auth.AuthSettings(
        app_url="https://app.example", supabase_url="https://project.supabase.co",
        publishable_key="publishable",
    )
    service = supabase_auth.SupabaseAuthService(settings)

    class Response:
        status_code = 404
        content = b'{"code":"PGRST205","message":"table missing"}'
        text = content.decode()
        def json(self): return {"code": "PGRST205", "message": "table missing"}

    monkeypatch.setattr(supabase_auth.requests, "request", lambda *args, **kwargs: Response())
    with caplog.at_level("WARNING"), pytest.raises(supabase_auth.SupabaseAuthError):
        service._request_json(
            "GET", "https://project.supabase.co/rest/v1/model_scan_warnings",
            access_token="user-token", public_error="Projects could not be loaded.",
        )
    assert "supabase_data_request_failed" in caplog.text
    assert "endpoint=/rest/v1/model_scan_warnings" in caplog.text
    assert "PGRST205" in caplog.text
    assert "supabase_auth_request_failed" not in caplog.text


def test_finalized_ifc_model_scan_get_returns_200_without_admin_user_lookup(monkeypatch):
    class ModelScanRouteAuth(FakeSupabaseAuth):
        settings = type("Settings", (), {"project_url": "https://example.supabase.co"})()
        def __init__(self): self.urls = []
        def _request_json(self, method, url, **kwargs):
            self.urls.append(url)
            if "projects?id=" in url:
                return [{"id": "project-id", "name": "Valid project", "created_by": self.user["id"]}]
            if "project_members?" in url:
                return [{"id": "member-id", "user_id": self.user["id"], "role": "OWNER"}]
            if "ifc_files?" in url:
                return [{"id": "model-id", "original_filename": "model.ifc", "file_size": 42,
                         "status": "PROCESSED", "storage_path": "projects/project-id/models/model-id/original/model.ifc",
                         "ifc_processing_jobs": [{"id": "job-id", "status": "SUCCEEDED", "progress_percent": 100}]}]
            if "ifc_processing_jobs?" in url:
                return [{"id": "job-id", "status": "SUCCEEDED", "progress_percent": 100}]
            if "model_scan_warnings?" in url: return []
            raise AssertionError(url)

    fake = ModelScanRouteAuth()
    cookie = _admin_cookie(monkeypatch, fake)
    monkeypatch.setattr(saas.Regulation38Repository, "get_scope", lambda self, token, project_id: None)
    monkeypatch.setattr(saas.Regulation38Repository, "get_sections", lambda self, token, project_id: [])
    response = request("GET", "/app/firetrace/projects/project-id/setup/model-scan",
                       headers={"cookie": cookie})
    assert response.status_code == 200
    assert "Model Scan" in response.text
    assert not any("/auth/v1/admin/" in url for url in fake.urls)
