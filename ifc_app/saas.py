"""SaaS shell, account, organisation, and audit primitives for IFC Toolkit."""
from __future__ import annotations

import hashlib
import hmac
import base64
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")
DB_PATH = Path(os.getenv("SAAS_DB_PATH", "data/ifc_toolkit.db"))
ROLES = {"Owner", "Admin", "User", "Viewer"}


class CookieSessionMiddleware:
    """Small signed-cookie session middleware without an external session service."""
    def __init__(self, app, secret_key: str, https_only: bool = False):
        self.app, self.secret, self.https_only = app, secret_key.encode(), https_only

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        cookies = headers.get(b"cookie", b"").decode()
        token = next((part.split("=", 1)[1] for part in cookies.split("; ") if part.startswith("ifc_session=")), "")
        session: dict[str, Any] = {}
        if "." in token:
            payload, signature = token.rsplit(".", 1)
            if hmac.compare_digest(hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest(), signature):
                try:
                    session = json.loads(base64.urlsafe_b64decode(payload + "=="))
                except (ValueError, json.JSONDecodeError):
                    session = {}
        scope["session"] = session

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                payload = base64.urlsafe_b64encode(json.dumps(scope["session"]).encode()).decode().rstrip("=")
                signature = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
                cookie = f"ifc_session={payload}.{signature}; Path=/; HttpOnly; SameSite=Lax"
                if self.https_only:
                    cookie += "; Secure"
                message.setdefault("headers", []).append((b"set-cookie", cookie.encode()))
            await send(message)
        await self.app(scope, receive, send_wrapper)

PUBLIC_PAGES = {
    "/tools": ("Tools", "Practical tools for better information management.", "Start with IFC validation, then use focused utilities to improve model quality."),
    "/resources": ("Resources", "Clear guidance for dependable IFC delivery.", "Practical notes, methodologies, and checklists are being prepared for the IFC Toolkit community."),
    "/pricing": ("Simple pricing", "Start validating with confidence.", "MVP access is available by invitation while subscriptions and enterprise plans are prepared."),
    "/about": ("About IFC Toolkit", "Built to make compliance practical.", "IFC Toolkit turns complex information requirements into clear, repeatable validation workflows."),
    "/contact": ("Contact", "Talk to the IFC Toolkit team.", "Tell us about your validation workflow, deployment requirements, or enterprise readiness needs."),
}
APP_PAGES = {
    "/app/dashboard": ("Dashboard", "Your validation workspace", "Track recent validation jobs, reports, and compliance activity."),
    "/app/tools": ("Tools", "Practical validation tools", "Use focused tools to improve the quality and readiness of your information."),
    "/app/reports": ("Reports", "Validation reports", "Report metadata and configured outputs are retained; original uploads are not."),
    "/app/settings": ("Settings", "Your profile and preferences", "Account controls are ready for future MFA and SSO integration."),
    "/app/organisation": ("Organisation", "Workspace and roles", "The workspace supports Owner, Admin, User, and Viewer roles."),
    "/app/billing": ("Billing", "Subscription readiness", "Subscription billing will be enabled after the MVP access period."),
}

def now() -> str:
    return datetime.now(timezone.utc).isoformat()

def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection

def initialise() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, display_name TEXT NOT NULL, email_verified_at TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS organisations (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS organisation_members (organisation_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (organisation_id, user_id));
        CREATE TABLE IF NOT EXISTS validation_jobs (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL, user_id TEXT NOT NULL, filename TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT);
        CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, organisation_id TEXT NOT NULL, validation_job_id TEXT NOT NULL, format TEXT NOT NULL, retention_until TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit_logs (id TEXT PRIMARY KEY, organisation_id TEXT, user_id TEXT, event TEXT NOT NULL, metadata TEXT, created_at TEXT NOT NULL);
        """)

def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 240_000).hex()
    return f"{salt}:{digest}"

def password_ok(password: str, stored: str) -> bool:
    salt, expected = stored.split(":", 1)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 240_000).hex()
    return hmac.compare_digest(actual, expected)

def current_user(request: Request) -> dict[str, Any] | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    with connect() as db:
        row = db.execute("SELECT id, email, display_name FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None

def audit(event: str, user_id: str | None = None, organisation_id: str | None = None, metadata: str = "") -> None:
    with connect() as db:
        db.execute("INSERT INTO audit_logs VALUES (?, ?, ?, ?, ?, ?)", (uuid.uuid4().hex, organisation_id, user_id, event, metadata, now()))

def context(request: Request, **extra: Any) -> dict[str, Any]:
    return {"request": request, "user": current_user(request), **extra}

def protected(request: Request) -> dict[str, Any] | RedirectResponse:
    user = current_user(request)
    return user or RedirectResponse(f"/login?next={request.url.path}", status_code=303)

@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request=request, name="saas/home.html", context=context(request))

for path, page in PUBLIC_PAGES.items():
    def make_public(route_path: str, details: tuple[str, str, str]):
        @router.get(route_path, response_class=HTMLResponse)
        def public_page(request: Request):
            return templates.TemplateResponse(request=request, name="saas/public_page.html", context=context(request, title=details[0], heading=details[1], copy=details[2], route=route_path))
    make_public(path, page)

@router.get("/compliance-security", response_class=HTMLResponse)
def compliance(request: Request):
    return templates.TemplateResponse(request=request, name="saas/compliance.html", context=context(request))

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="saas/auth.html", context=context(request, mode="login", error=None))

@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    with connect() as db:
        row = db.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    if not row or not password_ok(password, row["password_hash"]):
        return templates.TemplateResponse(request=request, name="saas/auth.html", context=context(request, mode="login", error="Email or password is incorrect."), status_code=400)
    request.session["user_id"] = row["id"]
    audit("user.login", row["id"])
    return RedirectResponse("/app/dashboard", status_code=303)

@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return templates.TemplateResponse(request=request, name="saas/auth.html", context=context(request, mode="signup", error=None))

@router.post("/signup")
def signup(request: Request, display_name: str = Form(...), organisation_name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    if len(password) < 10:
        return templates.TemplateResponse(request=request, name="saas/auth.html", context=context(request, mode="signup", error="Use at least 10 characters for your password."), status_code=400)
    user_id, org_id = uuid.uuid4().hex, uuid.uuid4().hex
    try:
        with connect() as db:
            db.execute("INSERT INTO users VALUES (?, ?, ?, ?, NULL, ?)", (user_id, email.strip().lower(), password_hash(password), display_name.strip(), now()))
            db.execute("INSERT INTO organisations VALUES (?, ?, ?)", (org_id, organisation_name.strip(), now()))
            db.execute("INSERT INTO organisation_members VALUES (?, ?, 'Owner', ?)", (org_id, user_id, now()))
    except sqlite3.IntegrityError:
        return templates.TemplateResponse(request=request, name="saas/auth.html", context=context(request, mode="signup", error="An account with that email already exists."), status_code=400)
    request.session["user_id"] = user_id
    audit("user.signup", user_id, org_id)
    return RedirectResponse("/app/dashboard", status_code=303)

@router.post("/logout")
def logout(request: Request):
    user_id = request.session.get("user_id")
    audit("user.logout", user_id)
    request.session.clear()
    return RedirectResponse("/", status_code=303)

for path, page in APP_PAGES.items():
    def make_app(route_path: str, details: tuple[str, str, str]):
        @router.get(route_path, response_class=HTMLResponse)
        def app_page(request: Request):
            user = protected(request)
            if isinstance(user, RedirectResponse): return user
            return templates.TemplateResponse(request=request, name="saas/app_page.html", context=context(request, title=details[0], heading=details[1], copy=details[2], route=route_path))
    make_app(path, page)

@router.get("/app/tools/ifc-validator", response_class=HTMLResponse)
def validator(request: Request):
    user = protected(request)
    if isinstance(user, RedirectResponse): return user
    return templates.TemplateResponse(request=request, name="saas/validator.html", context=context(request))
