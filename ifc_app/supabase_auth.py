"""Supabase Auth client and signed browser-session helpers for IFC Toolkit."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode, urlparse

import requests
from fastapi import Request


LOGGER = logging.getLogger("ifc_app.auth")
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
ENV_LOCAL_PATH = REPOSITORY_ROOT / ".env.local"
REQUIRED_AUTH_ENV = (
    "AUTH_SECRET",
    "APP_URL",
    "SUPABASE_URL",
    "SUPABASE_PUBLISHABLE_KEY",
)
SESSION_COOKIE_NAME = "ifc_session"


def load_env_local(path: Path = ENV_LOCAL_PATH) -> None:
    """Load .env.local without replacing deployment-provided environment values."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_local()


@dataclass(frozen=True)
class AuthSettings:
    app_url: str
    supabase_url: str
    publishable_key: str
    request_timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "AuthSettings":
        return cls(
            app_url=os.getenv("APP_URL", "http://127.0.0.1:8000").rstrip("/"),
            supabase_url=os.getenv("SUPABASE_URL", "").rstrip("/"),
            publishable_key=os.getenv("SUPABASE_PUBLISHABLE_KEY", ""),
            request_timeout_seconds=float(os.getenv("SUPABASE_AUTH_TIMEOUT_SECONDS", "10")),
        )

    @property
    def auth_url(self) -> str:
        if self.supabase_url.endswith("/auth/v1"):
            return self.supabase_url
        return f"{self.supabase_url}/auth/v1"

    @property
    def project_url(self) -> str:
        return self.supabase_url.removesuffix("/auth/v1")

    @property
    def configured(self) -> bool:
        return bool(self.supabase_url and self.publishable_key)


def missing_auth_environment() -> list[str]:
    return [name for name in REQUIRED_AUTH_ENV if not os.getenv(name)]


def validate_auth_environment(logger: logging.Logger = LOGGER) -> list[str]:
    """Report missing auth configuration without logging any secret values."""
    missing = missing_auth_environment()
    if missing:
        logger.error(
            "AUTH_CONFIGURATION_ERROR: missing required environment variables: %s. "
            "Set them in the deployment environment or repository-root .env.local.",
            ", ".join(missing),
        )
    else:
        logger.info("Supabase Auth configuration detected for APP_URL=%s", os.getenv("APP_URL"))
    return missing


class SupabaseAuthError(RuntimeError):
    def __init__(self, public_message: str, *, status_code: int = 400, detail: str = ""):
        super().__init__(detail or public_message)
        self.public_message = public_message
        self.status_code = status_code
        self.detail = detail


class SupabaseAuthService:
    """Small server-side client using Supabase's Auth and Data REST endpoints."""

    def __init__(self, settings: AuthSettings | None = None):
        self.settings = settings or AuthSettings.from_env()

    def _ensure_configured(self) -> None:
        if not self.settings.configured:
            raise SupabaseAuthError(
                "Authentication is not configured on this server yet.",
                status_code=503,
                detail="SUPABASE_URL or SUPABASE_PUBLISHABLE_KEY is missing",
            )

    def _headers(self, access_token: str | None = None) -> dict[str, str]:
        self._ensure_configured()
        headers = {
            "apikey": self.settings.publishable_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        return headers

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        access_token: str | None = None,
        public_error: str,
        **kwargs: Any,
    ) -> Any:
        try:
            response = requests.request(
                method,
                url,
                headers=self._headers(access_token),
                timeout=self.settings.request_timeout_seconds,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise SupabaseAuthError(
                "Authentication is temporarily unavailable. Please try again.",
                status_code=503,
                detail=str(exc),
            ) from exc
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            payload = {}
        if not 200 <= response.status_code < 300:
            detail = ""
            if isinstance(payload, Mapping):
                detail = str(
                    payload.get("msg")
                    or payload.get("message")
                    or payload.get("error_description")
                    or payload.get("error")
                    or ""
                )
            LOGGER.warning("Supabase Auth request failed with HTTP %s: %s", response.status_code, detail)
            raise SupabaseAuthError(public_error, status_code=response.status_code, detail=detail)
        return payload

    def sign_in(self, email: str, password: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"{self.settings.auth_url}/token?grant_type=password",
            json={"email": email, "password": password},
            public_error="Email or password is incorrect.",
        )

    def sign_up(self, name: str, email: str, password: str) -> dict[str, Any]:
        redirect_to = f"{self.settings.app_url}/auth/callback"
        metadata = {"name": name, "full_name": name, "display_name": name}
        query = urlencode({"redirect_to": redirect_to})
        return self._request_json(
            "POST",
            f"{self.settings.auth_url}/signup?{query}",
            json={"email": email, "password": password, "data": metadata},
            public_error="We could not create that account. Check the details and try again.",
        )

    def send_password_reset(self, email: str) -> None:
        redirect_to = f"{self.settings.app_url}/reset-password"
        query = urlencode({"redirect_to": redirect_to})
        self._request_json(
            "POST",
            f"{self.settings.auth_url}/recover?{query}",
            json={"email": email},
            public_error="We could not send a reset email. Please try again.",
        )

    def verify_token_hash(self, token_hash: str, token_type: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"{self.settings.auth_url}/verify",
            json={"token_hash": token_hash, "type": token_type},
            public_error="That authentication link is invalid or has expired.",
        )

    def exchange_code(self, code: str, code_verifier: str = "") -> dict[str, Any]:
        body = {"auth_code": code}
        if code_verifier:
            body["code_verifier"] = code_verifier
        return self._request_json(
            "POST",
            f"{self.settings.auth_url}/token?grant_type=pkce",
            json=body,
            public_error="That authentication link is invalid or has expired.",
        )

    def get_user(self, access_token: str) -> dict[str, Any]:
        payload = self._request_json(
            "GET",
            f"{self.settings.auth_url}/user",
            access_token=access_token,
            public_error="Your session has expired. Please log in again.",
        )
        if isinstance(payload, Mapping) and isinstance(payload.get("user"), Mapping):
            return dict(payload["user"])
        return dict(payload)

    def refresh_session(self, refresh_token: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"{self.settings.auth_url}/token?grant_type=refresh_token",
            json={"refresh_token": refresh_token},
            public_error="Your session has expired. Please log in again.",
        )

    def validate_session(self, session: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        access_token = str(session.get("access_token") or "")
        refresh_token = str(session.get("refresh_token") or "")
        if not access_token:
            raise SupabaseAuthError("Please log in to continue.", status_code=401)
        try:
            return self.get_user(access_token), dict(session)
        except SupabaseAuthError as exc:
            if exc.status_code not in {400, 401, 403} or not refresh_token:
                raise
        refreshed = self.refresh_session(refresh_token)
        user = refreshed.get("user") if isinstance(refreshed, Mapping) else None
        if not isinstance(user, Mapping):
            user = self.get_user(str(refreshed.get("access_token") or ""))
        return dict(user), session_from_auth_response(refreshed)

    def sign_out(self, access_token: str) -> None:
        if not access_token:
            return
        try:
            self._request_json(
                "POST",
                f"{self.settings.auth_url}/logout",
                access_token=access_token,
                public_error="You have been logged out locally.",
            )
        except SupabaseAuthError:
            LOGGER.info("Supabase sign-out failed; the local browser session was still cleared")

    def update_password(self, access_token: str, password: str) -> dict[str, Any]:
        return self._request_json(
            "PUT",
            f"{self.settings.auth_url}/user",
            access_token=access_token,
            json={"password": password},
            public_error="We could not update your password. Request a new reset link and try again.",
        )

    def update_name(self, access_token: str, name: str) -> dict[str, Any]:
        metadata = {"name": name, "full_name": name, "display_name": name}
        user = self._request_json(
            "PUT",
            f"{self.settings.auth_url}/user",
            access_token=access_token,
            json={"data": metadata},
            public_error="We could not update your profile. Please try again.",
        )
        self._update_profiles_row_if_available(access_token, user, name)
        return dict(user)

    def get_profile(self, access_token: str, user_id: str) -> dict[str, Any] | None:
        if not user_id:
            return None
        try:
            payload = self._request_json(
                "GET",
                f"{self.settings.project_url}/rest/v1/profiles",
                access_token=access_token,
                params={"id": f"eq.{user_id}", "select": "*", "limit": "1"},
                public_error="Profile details are temporarily unavailable.",
            )
        except SupabaseAuthError as exc:
            if exc.status_code in {400, 404}:
                return None
            raise
        if isinstance(payload, list) and payload and isinstance(payload[0], Mapping):
            return dict(payload[0])
        return None

    def _update_profiles_row_if_available(self, access_token: str, user: Mapping[str, Any], name: str) -> None:
        user_id = str(user.get("id") or "")
        try:
            profile = self.get_profile(access_token, user_id)
            if not profile:
                return
            column = next((item for item in ("full_name", "name", "display_name") if item in profile), None)
            if not column:
                return
            self._request_json(
                "PATCH",
                f"{self.settings.project_url}/rest/v1/profiles",
                access_token=access_token,
                params={"id": f"eq.{user_id}"},
                json={column: name},
                public_error="Your Auth profile was updated, but the public profile could not be saved.",
            )
        except SupabaseAuthError as exc:
            LOGGER.warning("Optional public.profiles update was not completed: %s", exc.detail or exc)


def session_from_auth_response(payload: Mapping[str, Any]) -> dict[str, Any]:
    session = payload.get("session") if isinstance(payload.get("session"), Mapping) else payload
    access_token = str(session.get("access_token") or "")
    refresh_token = str(session.get("refresh_token") or "")
    if not access_token:
        return {}
    result: dict[str, Any] = {"access_token": access_token, "refresh_token": refresh_token}
    for key in ("expires_at", "expires_in", "token_type"):
        if session.get(key) is not None:
            result[key] = session[key]
    return result


def user_display_name(user: Mapping[str, Any] | None, profile: Mapping[str, Any] | None = None) -> str:
    for source in (profile or {}, (user or {}).get("user_metadata") or {}, user or {}):
        if not isinstance(source, Mapping):
            continue
        for key in ("full_name", "name", "display_name"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def safe_next_url(value: str | None, default: str = "/app") -> str:
    if not value:
        return default
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):
        return default
    return value


def _urlsafe_b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class AuthSessionMiddleware:
    """Signed, HttpOnly cookie storage for Supabase access and refresh tokens."""

    def __init__(
        self,
        app: Any,
        secret_key: str | bytes | None,
        *,
        https_only: bool = False,
        max_age: int = 60 * 60 * 24 * 7,
    ):
        self.app = app
        self.secret = (
            secret_key.encode("utf-8") if isinstance(secret_key, str) and secret_key else secret_key
        ) or secrets.token_bytes(32)
        self.https_only = https_only
        self.max_age = max_age

    def encode(self, session: Mapping[str, Any]) -> str:
        payload = base64.urlsafe_b64encode(
            json.dumps(dict(session), separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).decode("ascii").rstrip("=")
        signature = hmac.new(self.secret, payload.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def decode(self, token: str) -> dict[str, Any]:
        if "." not in token:
            return {}
        payload, signature = token.rsplit(".", 1)
        expected = hmac.new(self.secret, payload.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return {}
        try:
            value = json.loads(_urlsafe_b64decode(payload))
        except (ValueError, TypeError, json.JSONDecodeError):
            return {}
        return dict(value) if isinstance(value, Mapping) else {}

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        cookie_header = dict(scope.get("headers") or []).get(b"cookie", b"").decode("latin-1")
        token = ""
        for part in cookie_header.split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name == SESSION_COOKIE_NAME:
                token = value
                break
        scope["auth_session"] = self.decode(token)
        scope["auth_session_dirty"] = False

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start" and scope.get("auth_session_dirty"):
                session = scope.get("auth_session") or {}
                if session:
                    cookie = (
                        f"{SESSION_COOKIE_NAME}={self.encode(session)}; Path=/; HttpOnly; "
                        f"SameSite=Lax; Max-Age={self.max_age}"
                    )
                else:
                    cookie = (
                        f"{SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; "
                        "Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
                    )
                if self.https_only:
                    cookie += "; Secure"
                message.setdefault("headers", []).append((b"set-cookie", cookie.encode("latin-1")))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def store_auth_session(request: Request, session: Mapping[str, Any]) -> None:
    request.scope["auth_session"] = dict(session)
    request.scope["auth_session_dirty"] = True
    request.scope.pop("auth_user", None)
    request.scope.pop("auth_user_resolved", None)


def clear_auth_session(request: Request) -> None:
    store_auth_session(request, {})


def get_auth_service() -> SupabaseAuthService:
    return SupabaseAuthService()


def get_current_user(request: Request) -> dict[str, Any] | None:
    if request.scope.get("auth_user_resolved"):
        user = request.scope.get("auth_user")
        return dict(user) if isinstance(user, Mapping) else None
    request.scope["auth_user_resolved"] = True
    session = request.scope.get("auth_session") or {}
    if not session.get("access_token"):
        request.scope["auth_user"] = None
        return None
    try:
        user, validated_session = get_auth_service().validate_session(session)
    except SupabaseAuthError as exc:
        LOGGER.info("Supabase browser session rejected: %s", exc.detail or exc.public_message)
        clear_auth_session(request)
        request.scope["auth_user_resolved"] = True
        request.scope["auth_user"] = None
        return None
    if dict(validated_session) != dict(session):
        store_auth_session(request, validated_session)
    request.scope["auth_user_resolved"] = True
    request.scope["auth_user"] = dict(user)
    return dict(user)


def require_user(request: Request) -> dict[str, Any] | None:
    """Return the validated Supabase user, or None when authentication is required."""
    return get_current_user(request)

