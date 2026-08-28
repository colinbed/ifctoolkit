import asyncio
import json
import os
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

import app as app_module
import ifc_app.saas as saas
import ifc_app.supabase_auth as supabase_auth


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
        return {"id": user_id, "full_name": "Test Member"}


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
    assert regulation_38.status_code == 200
    assert "Fire Safety Information" in regulation_38.text
    assert "does not automatically demonstrate legal or regulatory compliance" in regulation_38.text

    logout = request("POST", "/logout", headers={"cookie": session_cookie})
    assert logout.status_code == 303
    assert logout.header("location") == "/"
    assert "Max-Age=0" in logout.header("set-cookie")

    cleared_cookie = logout.header("set-cookie").split(";", 1)[0]
    after_logout = request("GET", "/app", headers={"cookie": cleared_cookie})
    assert after_logout.status_code == 303
    assert after_logout.header("location") == "/login?next=/app"
