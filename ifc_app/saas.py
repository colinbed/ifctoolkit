"""Supabase-backed authentication and private application routes."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ifc_app.supabase_auth import (
    SupabaseAuthError,
    clear_auth_session,
    get_auth_service,
    get_current_user,
    get_account_profile,
    require_user,
    safe_next_url,
    session_from_auth_response,
    store_auth_session,
    user_display_name,
)
from ifc_app.entitlements import TOOL_REGISTRY, account_level, can_access_tool, has_account_level, trial_is_active, trial_summary
from ifc_app.reg38_projects import REG38_DEFAULT_SECTIONS, ZONE_TYPES, ProjectCreate, Regulation38Repository


LOGGER = logging.getLogger("ifc_app.auth.routes")
router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["auth_user"] = get_current_user
templates.env.globals["user_display_name"] = user_display_name
templates.env.globals["account_profile"] = get_account_profile
templates.env.globals["has_account_level"] = has_account_level
templates.env.globals["trial_is_active"] = trial_is_active


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
    profile = get_account_profile(request)
    return {"request": request, "user": user, "profile": profile, "plan": account_level(profile),
            "trial": trial_summary(profile), **extra}


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
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    project_rows, can_create, error = [], False, None
    try:
        repository = Regulation38Repository(get_auth_service())
        project_rows = repository.list_projects(token)
        can_create = repository.can_create_project(token)
    except SupabaseAuthError as exc:
        LOGGER.error("Regulation 38 project list failed: %s", exc.detail or exc.public_message)
        error = "Projects could not be loaded."
    except AttributeError:
        # Keeps the page usable with deployments whose auth adapter predates Data REST.
        error = "Project data is not available from this deployment yet."
    return templates.TemplateResponse(
        request=request,
        name="saas/projects.html",
        context=_dashboard_context(request, user, projects=project_rows, can_create=can_create, error=error),
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
            profile=profile or {},
            plan=account_level(profile),
            trial=trial_summary(profile),
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
def regulation_38(request: Request):
    return projects(request)


WIZARD_STEPS = ("Project Details", "Regulation 38 Scope", "Upload IFC", "Model Scan", "Review Spaces & Zones",
                "Review Fire Construction", "Generate Plans", "Configure Information Requirements", "Summary")


def _wizard_response(request: Request, user: dict[str, Any], project: dict[str, Any] | None, step: int, **extra: Any):
    values = {"project": project or {}, "project_id": (project or {}).get("id", ""), "step": step,
              "steps": WIZARD_STEPS, "sections": [], "files": [], "error": None}
    values.update(extra)
    return templates.TemplateResponse(request=request, name="saas/reg38_wizard.html",
        context=_dashboard_context(request, user, **values))


@router.get("/app/regulation-38/projects/new", response_class=HTMLResponse)
@router.get("/app/projects/new", response_class=HTMLResponse)
def new_reg38_project(request: Request):
    user = _private_user(request)
    if isinstance(user, RedirectResponse):
        return user
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        if not Regulation38Repository(get_auth_service()).can_create_project(token):
            return HTMLResponse("Project creation is not permitted for this account.", status_code=403)
    except SupabaseAuthError as exc:
        return HTMLResponse(exc.public_message, status_code=exc.status_code)
    return _wizard_response(request, user, {}, 1)


@router.post("/app/regulation-38/projects/new")
@router.post("/app/projects/new")
async def create_reg38_project(request: Request):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    form = await request.form()
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        repository = Regulation38Repository(get_auth_service())
        if not repository.can_create_project(token):
            return HTMLResponse("Project creation is not permitted for this account.", status_code=403)
        project = ProjectCreate(**{key: (str(form.get(key) or "") or None) for key in ProjectCreate.__dataclass_fields__ if key not in {"project_status", "country"}},
                                country=str(form.get("country") or "United Kingdom"))
        project_id = repository.create_project(token, project)
        return RedirectResponse(f"/app/regulation-38/projects/{project_id}/setup/scope", status_code=303)
    except (ValueError, SupabaseAuthError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else exc.public_message
        return _wizard_response(request, user, dict(form), 1, error=message)


@router.get("/app/projects/{project_id}/regulation-38", response_class=HTMLResponse)
def regulation_38_project(request: Request, project_id: str, step: int = 1):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        repo = Regulation38Repository(get_auth_service())
        project = repo.get_project(token, project_id)
        if not project: return HTMLResponse("Project not found.", status_code=404)
        sections = repo.get_sections(token, project_id) if step == 2 else []
        files = repo.list_ifc_files(token, project_id) if step == 3 else []
        spatial = repo.spatial_review(token, project_id) if step == 5 else {}
        return _wizard_response(request, user, project, max(1, min(step, 9)), sections=sections, files=files,
                                spatial=spatial, zone_types=ZONE_TYPES)
    except SupabaseAuthError as exc:
        return HTMLResponse(exc.public_message, status_code=403 if exc.status_code in {401, 403} else exc.status_code)


@router.get("/app/regulation-38/projects/{project_id}/setup/{setup_step}", response_class=HTMLResponse)
def regulation_38_setup(request: Request, project_id: str, setup_step: str):
    step_map = {"details": 1, "scope": 2, "upload-ifc": 3, "model-scan": 4,
                "spaces-zones": 5, "fire-construction": 6, "plans": 7,
                "information-requirements": 8, "summary": 9}
    if setup_step not in step_map:
        return HTMLResponse("Setup step not found.", status_code=404)
    return regulation_38_project(request, project_id, step_map[setup_step])


@router.post("/app/projects/{project_id}/regulation-38/details")
async def update_reg38_details(request: Request, project_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    form = await request.form(); token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    values = {key: (str(form.get(key) or "") or None) for key in ProjectCreate.__dataclass_fields__ if key != "project_status"}
    try:
        ProjectCreate(**values).payload()
        Regulation38Repository(get_auth_service()).update_project(token, project_id, values)
        return RedirectResponse(f"/app/projects/{project_id}/regulation-38?step=2", status_code=303)
    except (ValueError, SupabaseAuthError) as exc:
        return _wizard_response(request, user, {"id": project_id, **values}, 1, error=str(exc) if isinstance(exc, ValueError) else exc.public_message)


@router.post("/app/projects/{project_id}/regulation-38/scope")
async def update_reg38_scope(request: Request, project_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    form = await request.form(); token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    applicability = {key: str(form.get(f"applicability_{key}") or "TO_BE_CONFIRMED") for key, _ in REG38_DEFAULT_SECTIONS}
    try:
        Regulation38Repository(get_auth_service()).save_scope(token, project_id, str(form.get("scope_type") or "ENTIRE_BUILDING"),
            str(form.get("scope_description") or ""), str(form.get("building_reference") or ""),
            str(form.get("area_description") or ""), applicability)
        return RedirectResponse(f"/app/regulation-38/projects/{project_id}/setup/upload-ifc", status_code=303)
    except SupabaseAuthError as exc: return HTMLResponse(exc.public_message, status_code=exc.status_code)


@router.post("/app/projects/{project_id}/regulation-38/ifc")
async def upload_reg38_ifc(request: Request, project_id: str, ifc_file: UploadFile):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    content = await ifc_file.read(); token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        Regulation38Repository(get_auth_service()).upload_ifc(token, str(user["id"]), project_id, ifc_file.filename or "", content)
        return RedirectResponse(f"/app/projects/{project_id}/regulation-38?step=3", status_code=303)
    except (ValueError, SupabaseAuthError) as exc:
        repo = Regulation38Repository(get_auth_service()); project = repo.get_project(token, project_id)
        return _wizard_response(request, user, project, 3, files=repo.list_ifc_files(token, project_id), error=str(exc) if isinstance(exc, ValueError) else exc.public_message)


@router.post("/app/projects/{project_id}/regulation-38/ifc/{file_id}/remove")
async def remove_reg38_ifc(request: Request, project_id: str, file_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    form = await request.form(); token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    Regulation38Repository(get_auth_service()).remove_ifc(token, file_id, str(form.get("storage_path") or ""))
    return RedirectResponse(f"/app/projects/{project_id}/regulation-38?step=3", status_code=303)


@router.post("/app/projects/{project_id}/regulation-38/spaces/{space_id}")
async def update_reg38_space(request: Request, project_id: str, space_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    form = await request.form(); token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        Regulation38Repository(get_auth_service()).update_space(token, project_id, space_id, {
            "space_number": str(form.get("space_number") or ""), "name": str(form.get("name") or ""),
            "description": str(form.get("description") or ""), "occupancy_type": str(form.get("occupancy_type") or ""),
            "occupancy_capacity": form.get("occupancy_capacity"), "high_risk": form.get("high_risk") == "yes",
            "included_in_reg38": form.get("included_in_reg38") == "yes"})
        return RedirectResponse(f"/app/projects/{project_id}/regulation-38?step=5&selected=space:{space_id}", status_code=303)
    except (ValueError, SupabaseAuthError) as exc:
        return HTMLResponse(str(exc) if isinstance(exc, ValueError) else exc.public_message,
                            status_code=400 if isinstance(exc, ValueError) else exc.status_code)


@router.post("/app/projects/{project_id}/regulation-38/zones")
async def create_reg38_zone(request: Request, project_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    form = await request.form(); token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        zone_id = Regulation38Repository(get_auth_service()).create_zone(token, project_id, str(form.get("name") or ""),
            str(form.get("zone_type") or "USER_DEFINED"), [str(x) for x in form.getlist("space_ids")])
        return RedirectResponse(f"/app/projects/{project_id}/regulation-38?step=5&selected=zone:{zone_id}", status_code=303)
    except (ValueError, SupabaseAuthError) as exc:
        return HTMLResponse(str(exc) if isinstance(exc, ValueError) else exc.public_message,
                            status_code=400 if isinstance(exc, ValueError) else exc.status_code)


@router.post("/app/projects/{project_id}/regulation-38/zones/{zone_id}")
async def update_reg38_zone(request: Request, project_id: str, zone_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    form = await request.form(); token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        Regulation38Repository(get_auth_service()).update_zone(token, project_id, zone_id, str(form.get("name") or ""),
            str(form.get("zone_type") or "USER_DEFINED"), [str(x) for x in form.getlist("space_ids")])
        return RedirectResponse(f"/app/projects/{project_id}/regulation-38?step=5&selected=zone:{zone_id}", status_code=303)
    except (ValueError, SupabaseAuthError) as exc:
        return HTMLResponse(str(exc) if isinstance(exc, ValueError) else exc.public_message,
                            status_code=400 if isinstance(exc, ValueError) else exc.status_code)


@router.get("/app/tools", response_class=HTMLResponse)
def app_tools(request: Request):
    user = _private_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        request=request,
        name="saas/tools.html",
        context=_dashboard_context(
            request, user,
            tools=[{**tool, "id": tool_id, "available": can_access_tool(get_account_profile(request), tool_id)} for tool_id, tool in TOOL_REGISTRY.items() if tool["access"] != "hidden"],
        ),
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
