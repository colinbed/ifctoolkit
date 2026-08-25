import asyncio
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

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

    assert request("GET", "/login").status_code == 200
    assert request("GET", "/signup").status_code == 200
    assert request("GET", "/forgot-password").status_code == 200
    assert request("GET", "/reset-password").status_code == 200


def test_private_routes_redirect_anonymous_users_to_login():
    for path in ("/app", "/app/projects", "/app/account", "/app/regulation-38"):
        response = request("GET", path)
        assert response.status_code == 303
        assert response.header("location") == f"/login?next={path}"


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

    logout = request("POST", "/logout", headers={"cookie": session_cookie})
    assert logout.status_code == 303
    assert logout.header("location") == "/"
    assert "Max-Age=0" in logout.header("set-cookie")

    cleared_cookie = logout.header("set-cookie").split(";", 1)[0]
    after_logout = request("GET", "/app", headers={"cookie": cleared_cookie})
    assert after_logout.status_code == 303
    assert after_logout.header("location") == "/login?next=/app"

