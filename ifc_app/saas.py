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
    get_account_profile,
    require_user,
    safe_next_url,
    session_from_auth_response,
    store_auth_session,
    user_display_name,
)
from ifc_app.entitlements import TOOL_REGISTRY, account_level, can_access_tool, has_account_level, trial_is_active, trial_summary
from ifc_app.firetrace_wizard import (
    FIRETRACE_WIZARD_STEPS,
    LEGACY_REGULATION_38_STEP_ALIASES,
    firetrace_wizard_step,
    firetrace_wizard_url,
)
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

FIRETRACE_ROUTES = {
    "home": "/app/firetrace",
    "projects": "/app/firetrace/projects",
    "new_project": "/app/firetrace/projects/new",
    "project": "/app/firetrace/projects/{project_id}",
    "setup": "/app/firetrace/projects/{project_id}/setup/{step}",
}


def _require_firetrace(request: Request) -> dict[str, Any] | HTMLResponse | RedirectResponse:
    """Central FireTrace authentication and premium-entitlement boundary."""
    user = _private_user(request)
    if isinstance(user, RedirectResponse):
        return user
    if not can_access_tool(get_account_profile(request), "regulation_38"):
        return HTMLResponse("FireTrace requires a Premium, trial or administrator account.", status_code=403)
    return user


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
    project_rows, can_create, list_error, permission_error = [], False, None, False
    repository = Regulation38Repository(get_auth_service())
    try:
        permission = repository.resolve_create_permission(token)
        can_create = permission.allowed
        if permission.check_failed:
            LOGGER.warning("can_create_project RPC failed; creation retained through verified is_platform_admin")
    except (SupabaseAuthError, AttributeError) as exc:
        permission_error = True
        detail = (exc.detail or exc.public_message) if isinstance(exc, SupabaseAuthError) else str(exc)
        LOGGER.error("Regulation 38 create-permission check failed: %s", detail)
    try:
        project_rows = repository.list_projects(token)
    except SupabaseAuthError as exc:
        LOGGER.error("Regulation 38 project list failed: %s", exc.detail or exc.public_message)
        list_error = True
        try:
            LOGGER.error("Regulation 38 schema health: %s", repository.schema_health(token))
        except Exception as health_exc:
            LOGGER.error("Regulation 38 schema health check unavailable: %s", health_exc)
    except AttributeError as exc:
        # Keeps the page usable with deployments whose auth adapter predates Data REST.
        LOGGER.error("Regulation 38 project adapter unavailable: %s", exc)
        list_error = True
    return templates.TemplateResponse(
        request=request,
        name="saas/projects.html",
        context=_dashboard_context(request, user, projects=project_rows, can_create=can_create,
            list_error=list_error, permission_error=permission_error),
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


@router.get("/app/firetrace", response_class=HTMLResponse)
def firetrace_home(request: Request):
    user = _require_firetrace(request)
    if isinstance(user, (HTMLResponse, RedirectResponse)):
        return user
    return templates.TemplateResponse(request=request, name="firetrace/dashboard.html",
                                      context=_dashboard_context(request, user, routes=FIRETRACE_ROUTES))


@router.get("/app/firetrace/projects", response_class=HTMLResponse)
def firetrace_projects(request: Request):
    user = _require_firetrace(request)
    if isinstance(user, (HTMLResponse, RedirectResponse)):
        return user
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    repo = Regulation38Repository(get_auth_service())
    rows, can_create, list_error = [], False, False
    try:
        can_create = repo.resolve_create_permission(token).allowed
        rows = repo.list_projects(token)
    except (SupabaseAuthError, AttributeError) as exc:
        LOGGER.error("FireTrace project list unavailable: %s", exc)
        list_error = True
    return templates.TemplateResponse(request=request, name="firetrace/projects.html",
        context=_dashboard_context(request, user, projects=rows, can_create=can_create, list_error=list_error))


@router.get("/app/regulation-38")
def regulation_38(request: Request):
    return RedirectResponse(FIRETRACE_ROUTES["home"], status_code=308)


def _wizard_response(request: Request, user: dict[str, Any], project: dict[str, Any] | None, step: int, **extra: Any):
    project_id = str((project or {}).get("id", ""))
    values = {"project": project or {}, "project_id": project_id, "step": step,
              "steps": tuple(label for _, label in FIRETRACE_WIZARD_STEPS),
              "wizard_steps": FIRETRACE_WIZARD_STEPS,
              "wizard_routes": {n: firetrace_wizard_url(project_id, n)
                                for n in range(1, len(FIRETRACE_WIZARD_STEPS) + 1)},
              "sections": [], "files": [], "error": None}
    values.update(extra)
    return templates.TemplateResponse(request=request, name="firetrace/setup/wizard.html",
        context=_dashboard_context(request, user, **values))


@router.get("/app/regulation-38/projects/new", response_class=HTMLResponse)
@router.get("/app/projects/new", response_class=HTMLResponse)
@router.get("/app/firetrace/projects/new", response_class=HTMLResponse)
def new_reg38_project(request: Request):
    user = _require_firetrace(request)
    if isinstance(user, (HTMLResponse, RedirectResponse)):
        return user
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        if not Regulation38Repository(get_auth_service()).resolve_create_permission(token).allowed:
            return HTMLResponse("Project creation is not permitted for this account.", status_code=403)
    except SupabaseAuthError as exc:
        return HTMLResponse(exc.public_message, status_code=exc.status_code)
    return _wizard_response(request, user, {}, 1)


@router.post("/app/regulation-38/projects/new")
@router.post("/app/projects/new")
@router.post("/app/firetrace/projects/new")
async def create_reg38_project(request: Request):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    form = await request.form()
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        repository = Regulation38Repository(get_auth_service())
        if not repository.resolve_create_permission(token).allowed:
            return HTMLResponse("Project creation is not permitted for this account.", status_code=403)
        project = ProjectCreate(**{key: (str(form.get(key) or "") or None) for key in ProjectCreate.__dataclass_fields__ if key not in {"project_status", "country"}},
                                country=str(form.get("country") or "United Kingdom"))
        project_id = repository.create_project(token, project)
        return RedirectResponse(f"/app/firetrace/projects/{project_id}/setup/scope", status_code=303)
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
        model_scan = repo.model_scan(token, project_id, str(user.get("id") or "")) if step == 4 else {}
        spatial = repo.spatial_review(token, project_id) if step == 5 else {}
        return _wizard_response(request, user, project, max(1, min(step, 9)), sections=sections, files=files,
                                model_scan=model_scan, spatial=spatial, zone_types=ZONE_TYPES)
    except SupabaseAuthError as exc:
        status = 403 if exc.status_code in {401, 403} else 404 if exc.status_code == 404 and exc.public_message == "Project not found." else 503 if exc.status_code == 503 else 502
        return HTMLResponse(exc.public_message, status_code=status)


@router.get("/app/firetrace/projects/{project_id}", response_class=HTMLResponse)
def firetrace_project_dashboard(request: Request, project_id: str):
    user = _require_firetrace(request)
    if isinstance(user, (HTMLResponse, RedirectResponse)):
        return user
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        repo = Regulation38Repository(get_auth_service())
        project = repo.get_project(token, project_id)
        if not project:
            return HTMLResponse("Project not found.", status_code=404)
        return templates.TemplateResponse(request=request, name="firetrace/project.html",
            context=_dashboard_context(request, user, project=project, project_id=project_id,
                                       setup_url=firetrace_wizard_url(project_id, 1), routes=FIRETRACE_ROUTES))
    except SupabaseAuthError as exc:
        return HTMLResponse(exc.public_message, status_code=exc.status_code)


@router.get("/app/firetrace/projects/{project_id}/{area}", response_class=HTMLResponse)
def firetrace_project_area(request: Request, project_id: str, area: str):
    areas = {
        "model": ("Design Model", "Model processing, assurance findings and replacement controls."),
        "spatial": ("Spaces", "Building, storey, space, fire-compartment and occupancy information."),
        "fire-strategy": ("Fire Strategy", "Review model-derived fire safety information and its provenance."),
        "requirements": ("Requirements", "Structured information requirements and review status."),
        "evidence": ("Evidence", "Evidence coverage, source and traceability."),
        "compliance": ("Compliance", "Regulation 38, BS 8644, ISO 19650 and project requirement lenses."),
        "export": ("Handover / Export", "Controlled FireTrace deliverables and outstanding-information reporting."),
    }
    if area not in areas:
        return HTMLResponse("FireTrace area not found.", status_code=404)
    user = _require_firetrace(request)
    if isinstance(user, (HTMLResponse, RedirectResponse)):
        return user
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        project = Regulation38Repository(get_auth_service()).get_project(token, project_id)
        if not project:
            return HTMLResponse("Project not found.", status_code=404)
        title, description = areas[area]
        return templates.TemplateResponse(request=request, name="firetrace/area.html",
            context=_dashboard_context(request, user, project=project, project_id=project_id,
                                       area=area, title=title, description=description))
    except SupabaseAuthError as exc:
        return HTMLResponse(exc.public_message, status_code=exc.status_code)


@router.get("/app/firetrace/projects/{project_id}/setup/{setup_step}", response_class=HTMLResponse)
def regulation_38_setup(request: Request, project_id: str, setup_step: str):
    step = firetrace_wizard_step(setup_step)
    if step is None:
        return HTMLResponse("Setup step not found.", status_code=404)
    return regulation_38_project(request, project_id, step)


@router.get("/app/regulation-38/projects/{project_id}/setup/{setup_step}")
def legacy_regulation_38_setup(project_id: str, setup_step: str):
    """Keep bookmarked Regulation 38 steps working via FireTrace redirects."""
    canonical_slug = LEGACY_REGULATION_38_STEP_ALIASES.get(setup_step)
    if canonical_slug is None:
        return HTMLResponse("Setup step not found.", status_code=404)
    step = firetrace_wizard_step(canonical_slug)
    return RedirectResponse(firetrace_wizard_url(project_id, step or 1), status_code=308)


@router.post("/app/projects/{project_id}/regulation-38/details")
@router.post("/app/firetrace/projects/{project_id}/details")
async def update_reg38_details(request: Request, project_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    form = await request.form(); token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    values = {key: (str(form.get(key) or "") or None) for key in ProjectCreate.__dataclass_fields__ if key != "project_status"}
    try:
        ProjectCreate(**values).payload()
        Regulation38Repository(get_auth_service()).update_project(token, project_id, values)
        return RedirectResponse(firetrace_wizard_url(project_id, 2), status_code=303)
    except (ValueError, SupabaseAuthError) as exc:
        return _wizard_response(request, user, {"id": project_id, **values}, 1, error=str(exc) if isinstance(exc, ValueError) else exc.public_message)


@router.post("/app/projects/{project_id}/regulation-38/scope")
@router.post("/app/firetrace/projects/{project_id}/scope")
async def update_reg38_scope(request: Request, project_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    form = await request.form(); token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    applicability = {key: str(form.get(f"applicability_{key}") or "TO_BE_CONFIRMED") for key, _ in REG38_DEFAULT_SECTIONS}
    try:
        Regulation38Repository(get_auth_service()).save_scope(token, project_id, str(form.get("scope_type") or "ENTIRE_BUILDING"),
            str(form.get("scope_description") or ""), str(form.get("building_reference") or ""),
            str(form.get("area_description") or ""), applicability)
        return RedirectResponse(firetrace_wizard_url(project_id, 3), status_code=303)
    except SupabaseAuthError as exc: return HTMLResponse(exc.public_message, status_code=exc.status_code)


@router.post("/app/projects/{project_id}/regulation-38/ifc/initiate")
@router.post("/app/firetrace/projects/{project_id}/ifc/initiate")
async def initiate_reg38_ifc(request: Request, project_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        payload = await request.json()
        result = Regulation38Repository(get_auth_service()).create_ifc_upload(
            token, project_id, str(payload.get("filename") or ""), int(payload.get("file_size") or 0))
        return JSONResponse(result)
    except (ValueError, SupabaseAuthError) as exc:
        return JSONResponse({"detail": str(exc) if isinstance(exc, ValueError) else exc.public_message},
                            status_code=400 if isinstance(exc, ValueError) else exc.status_code)


@router.post("/app/projects/{project_id}/regulation-38/ifc/finalize")
@router.post("/app/firetrace/projects/{project_id}/ifc/finalize")
async def finalize_reg38_ifc(request: Request, project_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        payload = await request.json()
        result = Regulation38Repository(get_auth_service()).finalize_ifc_upload(
            token, str(user["id"]), project_id, str(payload.get("file_id") or ""),
            str(payload.get("filename") or ""), int(payload.get("file_size") or 0),
            str(payload.get("storage_path") or ""))
        replace_id = str(payload.get("replace_file_id") or "")
        replace_path = str(payload.get("replace_storage_path") or "")
        if replace_id and replace_path:
            Regulation38Repository(get_auth_service()).remove_ifc(token, project_id, replace_id, replace_path)
        return JSONResponse({**result, "status": "UPLOADED", "job_status": "QUEUED"})
    except (ValueError, SupabaseAuthError) as exc:
        return JSONResponse({"detail": str(exc) if isinstance(exc, ValueError) else exc.public_message},
                            status_code=400 if isinstance(exc, ValueError) else exc.status_code)


@router.post("/app/projects/{project_id}/regulation-38/ifc/failure")
@router.post("/app/firetrace/projects/{project_id}/ifc/failure")
async def report_reg38_ifc_failure(request: Request, project_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    payload = await request.json()
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    repository = Regulation38Repository(get_auth_service())
    repository.require_project_edit(token, project_id)
    storage_path = str(payload.get("storage_path") or "")
    if storage_path:
        try:
            repository.cleanup_failed_upload(token, project_id, storage_path)
        except (ValueError, SupabaseAuthError):
            pass
    LOGGER.error(
        "ifc_direct_upload_failed project_id=%s user_id=%s filename=%s file_size=%s storage_path=%s storage_http_status=%s ifc_files_insert=not_attempted processing_job_insert=not_attempted",
        project_id, user.get("id"), str(payload.get("filename") or ""), int(payload.get("file_size") or 0),
        storage_path, int(payload.get("storage_http_status") or 0),
    )
    return JSONResponse({"received": True})


@router.post("/app/projects/{project_id}/regulation-38/ifc/{file_id}/remove")
@router.post("/app/firetrace/projects/{project_id}/ifc/{file_id}/remove")
async def remove_reg38_ifc(request: Request, project_id: str, file_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    form = await request.form(); token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    Regulation38Repository(get_auth_service()).remove_ifc(token, project_id, file_id, str(form.get("storage_path") or ""))
    return RedirectResponse(firetrace_wizard_url(project_id, 3), status_code=303)


@router.post("/app/projects/{project_id}/regulation-38/spatial-acknowledgement")
async def acknowledge_missing_spatial_data(request: Request, project_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    Regulation38Repository(get_auth_service()).acknowledge_missing_spatial_data(
        token, project_id, str(user.get("id") or ""))
    return RedirectResponse(firetrace_wizard_url(project_id, 5), status_code=303)


@router.post("/app/projects/{project_id}/regulation-38/delete")
async def delete_draft_reg38_project(request: Request, project_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    Regulation38Repository(get_auth_service()).delete_draft_project(token, project_id)
    return RedirectResponse("/app/regulation-38", status_code=303)


@router.post("/app/projects/{project_id}/regulation-38/ifc/{file_id}/retry")
@router.post("/app/firetrace/projects/{project_id}/ifc/{file_id}/retry")
async def retry_reg38_ifc(request: Request, project_id: str, file_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        Regulation38Repository(get_auth_service()).retry_model_scan(token, project_id, file_id)
        return RedirectResponse(firetrace_wizard_url(project_id, 4), status_code=303)
    except SupabaseAuthError as exc:
        return HTMLResponse(exc.public_message, status_code=exc.status_code)


@router.post("/app/projects/{project_id}/regulation-38/spaces/{space_id}")
@router.post("/app/firetrace/projects/{project_id}/spaces/{space_id}")
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
        return RedirectResponse(f"{firetrace_wizard_url(project_id, 5)}?selected=space:{space_id}", status_code=303)
    except (ValueError, SupabaseAuthError) as exc:
        return HTMLResponse(str(exc) if isinstance(exc, ValueError) else exc.public_message,
                            status_code=400 if isinstance(exc, ValueError) else exc.status_code)


@router.post("/app/projects/{project_id}/regulation-38/zones")
@router.post("/app/firetrace/projects/{project_id}/zones")
async def create_reg38_zone(request: Request, project_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    form = await request.form(); token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        zone_id = Regulation38Repository(get_auth_service()).create_zone(token, project_id, str(form.get("name") or ""),
            str(form.get("zone_type") or "USER_DEFINED"), [str(x) for x in form.getlist("space_ids")])
        return RedirectResponse(f"{firetrace_wizard_url(project_id, 5)}?selected=zone:{zone_id}", status_code=303)
    except (ValueError, SupabaseAuthError) as exc:
        return HTMLResponse(str(exc) if isinstance(exc, ValueError) else exc.public_message,
                            status_code=400 if isinstance(exc, ValueError) else exc.status_code)


@router.post("/app/projects/{project_id}/regulation-38/zones/{zone_id}")
@router.post("/app/firetrace/projects/{project_id}/zones/{zone_id}")
async def update_reg38_zone(request: Request, project_id: str, zone_id: str):
    user = _private_user(request)
    if isinstance(user, RedirectResponse): return user
    form = await request.form(); token = str((request.scope.get("auth_session") or {}).get("access_token") or "")
    try:
        Regulation38Repository(get_auth_service()).update_zone(token, project_id, zone_id, str(form.get("name") or ""),
            str(form.get("zone_type") or "USER_DEFINED"), [str(x) for x in form.getlist("space_ids")])
        return RedirectResponse(f"{firetrace_wizard_url(project_id, 5)}?selected=zone:{zone_id}", status_code=303)
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
