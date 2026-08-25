"""Supabase-backed authentication and private application routes."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ifc_app.supabase_auth import (
    SupabaseAuthError,
    clear_auth_session,
    get_auth_service,
    get_current_user,
    require_user,
    safe_next_url,
    session_from_auth_response,
    store_auth_session,
    user_display_name,
)


LOGGER = logging.getLogger("ifc_app.auth.routes")
router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["auth_user"] = get_current_user
templates.env.globals["user_display_name"] = user_display_name


REGULATION_38_SECTIONS = (
    "Overview",
    "Project / Building Information",
    "Fire Safety Information",
    "Passive Fire Protection",
    "Active Fire Systems",
    "Fire Doors",
    "Compartmentation",
    "Fire Stopping / Penetrations",
    "Drawings & Models",
    "Inspection / Commissioning Information",
    "Handover Documents",
    "Outstanding Information",
    "Completeness Review",
)


def context(request: Request, **extra: Any) -> dict[str, Any]:
    return {"request": request, "user": get_current_user(request), **extra}


def _login_redirect(request: Request) -> RedirectResponse:
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(f"/login?next={quote(target, safe='/')}", status_code=303)


def _private_user(request: Request) -> dict[str, Any] | RedirectResponse:
    return require_user(request) or _login_redirect(request)


def _auth_page(
    request: Request,
    *,
    mode: str,
    error: str | None = None,
    message: str | None = None,
    status_code: int = 200,
    next_url: str = "",
    can_reset: bool = False,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="saas/auth.html",
        context=context(
            request,
            mode=mode,
            error=error,
            message=message,
            next_url=safe_next_url(next_url, default="/app") if next_url else "/app",
            can_reset=can_reset,
        ),
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = ""):
    if get_current_user(request):
        return RedirectResponse(safe_next_url(next), status_code=303)
    return _auth_page(request, mode="login", next_url=next)


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
):
    destination = safe_next_url(next)
    try:
        result = get_auth_service().sign_in(email.strip().lower(), password)
        session = session_from_auth_response(result)
        if not session:
            raise SupabaseAuthError("Email or password is incorrect.")
        store_auth_session(request, session)
    except SupabaseAuthError as exc:
        LOGGER.info("Supabase login rejected: %s", exc.detail or exc.public_message)
        return _auth_page(
            request,
            mode="login",
            error=exc.public_message,
            status_code=exc.status_code if exc.status_code >= 500 else 400,
            next_url=destination,
        )
    return RedirectResponse(destination, status_code=303)


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, next: str = ""):
    if get_current_user(request):
        return RedirectResponse(safe_next_url(next), status_code=303)
    return _auth_page(request, mode="signup", next_url=next)


@router.post("/signup")
def signup(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    next: str = Form(""),
):
    destination = safe_next_url(next)
    clean_name = name.strip()
    if not clean_name:
        return _auth_page(request, mode="signup", error="Enter your name.", status_code=400, next_url=destination)
    if password != confirm_password:
        return _auth_page(request, mode="signup", error="Passwords do not match.", status_code=400, next_url=destination)
    if len(password) < 8:
        return _auth_page(
            request,
            mode="signup",
            error="Use at least 8 characters for your password.",
            status_code=400,
            next_url=destination,
        )
    try:
        result = get_auth_service().sign_up(clean_name, email.strip().lower(), password)
        session = session_from_auth_response(result)
    except SupabaseAuthError as exc:
        LOGGER.info("Supabase signup rejected: %s", exc.detail or exc.public_message)
        return _auth_page(
            request,
            mode="signup",
            error=exc.public_message,
            status_code=exc.status_code if exc.status_code >= 500 else 400,
            next_url=destination,
        )
    if session:
        store_auth_session(request, session)
        return RedirectResponse(destination, status_code=303)
    return _auth_page(
        request,
        mode="signup",
        message="Account created. Check your email to confirm your address, then log in.",
        next_url=destination,
    )


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return _auth_page(request, mode="forgot")


@router.post("/forgot-password", response_class=HTMLResponse)
def forgot_password(request: Request, email: str = Form(...)):
    try:
        get_auth_service().send_password_reset(email.strip().lower())
    except SupabaseAuthError as exc:
        LOGGER.info("Supabase password reset request failed: %s", exc.detail or exc.public_message)
        return _auth_page(
            request,
            mode="forgot",
            error=exc.public_message,
            status_code=exc.status_code if exc.status_code >= 500 else 400,
        )
    return _auth_page(
        request,
        mode="forgot",
        message="If an account exists for that email, a password reset link is on its way.",
    )


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request):
    return _auth_page(request, mode="reset", can_reset=bool(get_current_user(request)))


@router.post("/reset-password", response_class=HTMLResponse)
def reset_password(
    request: Request,
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    user = require_user(request)
    if not user:
        return _auth_page(
            request,
            mode="reset",
            error="Open the latest password reset link from your email before setting a new password.",
            status_code=401,
            can_reset=False,
        )
    if password != confirm_password:
        return _auth_page(request, mode="reset", error="Passwords do not match.", status_code=400, can_reset=True)
    if len(password) < 8:
        return _auth_page(
            request,
            mode="reset",
            error="Use at least 8 characters for your password.",
            status_code=400,
            can_reset=True,
        )
    access_token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        get_auth_service().update_password(access_token, password)
    except SupabaseAuthError as exc:
        return _auth_page(
            request,
            mode="reset",
            error=exc.public_message,
            status_code=exc.status_code if exc.status_code >= 500 else 400,
            can_reset=True,
        )
    return _auth_page(request, mode="reset", message="Password updated. You can continue to the application.", can_reset=True)


@router.get("/auth/callback", response_class=HTMLResponse)
def auth_callback(request: Request, token_hash: str = "", type: str = "", code: str = ""):
    if token_hash and type:
        try:
            result = get_auth_service().verify_token_hash(token_hash, type)
            session = session_from_auth_response(result)
            if session:
                store_auth_session(request, session)
            destination = "/reset-password" if type == "recovery" else "/app"
            return RedirectResponse(destination, status_code=303)
        except SupabaseAuthError as exc:
            return templates.TemplateResponse(
                request=request,
                name="saas/callback.html",
                context=context(request, error=exc.public_message),
                status_code=400,
            )
    if code:
        try:
            verifier = str((request.scope.get("auth_session") or {}).get("code_verifier") or "")
            result = get_auth_service().exchange_code(code, verifier)
            session = session_from_auth_response(result)
            if session:
                store_auth_session(request, session)
                return RedirectResponse("/app", status_code=303)
        except SupabaseAuthError as exc:
            return templates.TemplateResponse(
                request=request,
                name="saas/callback.html",
                context=context(request, error=exc.public_message),
                status_code=400,
            )
    return templates.TemplateResponse(
        request=request,
        name="saas/callback.html",
        context=context(request, error=None),
    )


@router.post("/auth/session")
async def establish_callback_session(request: Request):
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"detail": "Invalid callback payload."}, status_code=400)
    session = session_from_auth_response(payload if isinstance(payload, dict) else {})
    if not session:
        return JSONResponse({"detail": "Authentication tokens were not provided."}, status_code=400)
    try:
        _, session = get_auth_service().validate_session(session)
    except SupabaseAuthError as exc:
        return JSONResponse({"detail": exc.public_message}, status_code=401)
    store_auth_session(request, session)
    destination = "/reset-password" if payload.get("type") == "recovery" else "/app"
    return JSONResponse({"redirect": destination})


@router.post("/logout")
def logout(request: Request):
    session = request.scope.get("auth_session") or {}
    get_auth_service().sign_out(str(session.get("access_token") or ""))
    clear_auth_session(request)
    return RedirectResponse("/", status_code=303)


def _dashboard_context(request: Request, user: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"request": request, "user": user, **extra}


@router.get("/app", response_class=HTMLResponse)
@router.get("/app/dashboard", response_class=HTMLResponse)
def app_home(request: Request):
    user = _private_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        request=request,
        name="saas/dashboard.html",
        context=_dashboard_context(request, user),
    )


@router.get("/app/projects", response_class=HTMLResponse)
def projects(request: Request):
    user = _private_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        request=request,
        name="saas/projects.html",
        context=_dashboard_context(request, user),
    )


@router.get("/account", response_class=HTMLResponse)
@router.get("/app/account", response_class=HTMLResponse)
def account(request: Request):
    user = _private_user(request)
    if isinstance(user, RedirectResponse):
        return user
    session = request.scope.get("auth_session") or {}
    profile = None
    try:
        profile = get_auth_service().get_profile(str(session.get("access_token") or ""), str(user.get("id") or ""))
    except SupabaseAuthError as exc:
        LOGGER.info("Optional profile read failed: %s", exc.detail or exc.public_message)
    return templates.TemplateResponse(
        request=request,
        name="saas/account.html",
        context=_dashboard_context(
            request,
            user,
            profile=profile,
            display_name=user_display_name(user, profile),
            error=None,
            message=None,
        ),
    )


@router.post("/account", response_class=HTMLResponse)
@router.post("/app/account", response_class=HTMLResponse)
def update_account(request: Request, name: str = Form(...)):
    user = _private_user(request)
    if isinstance(user, RedirectResponse):
        return user
    clean_name = name.strip()
    if not clean_name:
        return templates.TemplateResponse(
            request=request,
            name="saas/account.html",
            context=_dashboard_context(
                request, user, profile=None, display_name="", error="Enter your name.", message=None
            ),
            status_code=400,
        )
    access_token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        updated_user = get_auth_service().update_name(access_token, clean_name)
    except SupabaseAuthError as exc:
        return templates.TemplateResponse(
            request=request,
            name="saas/account.html",
            context=_dashboard_context(
                request,
                user,
                profile=None,
                display_name=clean_name,
                error=exc.public_message,
                message=None,
            ),
            status_code=exc.status_code if exc.status_code >= 500 else 400,
        )
    request.scope["auth_user"] = updated_user
    request.scope["auth_user_resolved"] = True
    return templates.TemplateResponse(
        request=request,
        name="saas/account.html",
        context=_dashboard_context(
            request,
            updated_user,
            profile=None,
            display_name=user_display_name(updated_user),
            error=None,
            message="Profile updated.",
        ),
    )


@router.get("/app/regulation-38", response_class=HTMLResponse)
@router.get("/app/projects/{project_id}/regulation-38", response_class=HTMLResponse)
def regulation_38(request: Request, project_id: str = ""):
    user = _private_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        request=request,
        name="saas/regulation_38.html",
        context=_dashboard_context(
            request,
            user,
            project_id=project_id,
            sections=REGULATION_38_SECTIONS,
        ),
    )


@router.get("/app/tools", response_class=HTMLResponse)
def app_tools(request: Request):
    user = _private_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        request=request,
        name="saas/tools.html",
        context=_dashboard_context(request, user),
    )


@router.get("/app/tools/ifc-validator", response_class=HTMLResponse)
def validator(request: Request):
    user = _private_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        request=request,
        name="saas/validator.html",
        context=_dashboard_context(request, user),
    )


@router.get("/app/reports", response_class=HTMLResponse)
def reports(request: Request):
    user = _private_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        request=request,
        name="saas/app_page.html",
        context=_dashboard_context(
            request,
            user,
            title="Reports",
            heading="Reports",
            copy="Validation reports and session outputs remain available through the existing IFC tools.",
            route="/app/reports",
        ),
    )

