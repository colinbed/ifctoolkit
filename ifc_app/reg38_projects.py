"""RLS-scoped persistence and private Storage operations for Regulation 38."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
import json
import logging
import os
from time import perf_counter
from typing import Any, Mapping
from urllib.parse import quote, urlparse
from uuid import uuid4

import requests

from ifc_app.supabase_auth import SupabaseAuthError, SupabaseAuthService
from ifc_app.firetrace_wizard import FireTraceProgress, get_firetrace_resume_step

REG38_DEFAULT_SECTIONS: tuple[tuple[str, str], ...] = (
    ("PROJECT_BUILDING_INFORMATION", "Project & Building Information"), ("FIRE_SAFETY_STRATEGY", "Fire Safety Strategy"),
    ("SPATIAL_OCCUPANCY", "Spatial & Occupancy"), ("ESCAPE_EVACUATION", "Escape & Evacuation"),
    ("COMPARTMENTATION", "Compartmentation"), ("FIRE_DOORS_OPENINGS", "Fire Doors & Openings"),
    ("FIRE_STOPPING_PENETRATIONS", "Fire Stopping / Penetrations"), ("DETECTION_ALARM", "Detection & Alarm"),
    ("EMERGENCY_LIGHTING_SIGNAGE", "Emergency Lighting & Signage"), ("SUPPRESSION_FIREFIGHTING", "Suppression & Firefighting"),
    ("SMOKE_CONTROL", "Smoke Control"), ("ELECTRICAL_CRITICAL_SYSTEMS", "Electrical / Critical Systems"),
    ("FIRE_RESCUE_FACILITIES", "Fire & Rescue Facilities"), ("SPECIFICATIONS_OM", "Specifications & O&M"),
    ("TESTING_COMMISSIONING", "Testing & Commissioning"), ("DRAWINGS_MODELS", "Drawings & Models"), ("HANDOVER", "Handover"),
)
MAX_IFC_BYTES = 500 * 1024 * 1024
LOGGER = logging.getLogger("ifc_app.reg38.upload")
ZONE_TYPES = ("FIRE_COMPARTMENT", "SMOKE_ZONE", "ALARM_ZONE", "SPRINKLER_ZONE", "EVACUATION_ZONE",
              "OCCUPANCY_ZONE", "REFUGE", "HIGH_RISK", "USER_DEFINED")
FIRE_STRATEGY_CATEGORIES = ("COMPARTMENTATION", "FIRE_RESISTING_CONSTRUCTION", "FIRE_DOORS_SHUTTERS",
    "ESCAPE_ROUTES", "DETECTION_ALARM", "EMERGENCY_LIGHTING", "SMOKE_CONTROL", "FIRE_SUPPRESSION",
    "FIREFIGHTING_FACILITIES", "SIGNAGE", "FIRE_SERVICE_ACCESS", "OTHER")
FIRE_RELEVANT_ENTITIES = {"IfcDoor", "IfcWall", "IfcWallStandardCase", "IfcSlab", "IfcWindow", "IfcCurtainWall",
    "IfcStair", "IfcRamp", "IfcRailing", "IfcCovering", "IfcDamper", "IfcFan", "IfcAlarm", "IfcSensor",
    "IfcLightFixture", "IfcFireSuppressionTerminal", "IfcDistributionElement", "IfcSpace", "IfcZone",
    "IfcSpatialZone", "IfcSystem", "IfcGroup"}
FIRE_STRATEGY_PAGE_SIZE = 500
FIRE_STRATEGY_ID_BATCH_SIZE = 100


def check_reg38_storage_bucket(auth: SupabaseAuthService | None = None) -> bool:
    """Check Storage configuration at startup without exposing credential values."""
    repository = Regulation38Repository(auth)
    settings = repository.auth.settings
    url = f"{settings.project_url}/storage/v1/bucket/{quote(repository.bucket, safe='')}"
    # Bucket administration is deliberately not exposed to the publishable key.
    # Supabase can mask that authorization failure as "Bucket not found", even
    # while an authenticated user can upload through an RLS-authorized URL.
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not service_key:
        LOGGER.error("reg38_storage_health_failed storage_host=%s storage_bucket=%s stage=credential error=SUPABASE_SERVICE_ROLE_KEY_missing",
                     urlparse(url).hostname, repository.bucket)
        return False
    try:
        response = requests.get(
            url,
            headers={"apikey": service_key, "Authorization": f"Bearer {service_key}", "Accept": "application/json"},
            timeout=settings.request_timeout_seconds,
        )
    except requests.RequestException as exc:
        LOGGER.warning("reg38_storage_health_failed storage_host=%s storage_bucket=%s error=%s",
                       urlparse(url).hostname, repository.bucket, type(exc).__name__)
        return False
    if 200 <= response.status_code < 300:
        LOGGER.info("reg38_storage_health_ok storage_host=%s storage_bucket=%s",
                    urlparse(url).hostname, repository.bucket)
        return True
    LOGGER.error("reg38_storage_health_failed storage_host=%s storage_bucket=%s storage_http_status=%s storage_response=%s",
                 urlparse(url).hostname, repository.bucket, response.status_code,
                 repository._safe_storage_response(response))
    return False


@dataclass(frozen=True)
class ProjectCreate:
    name: str
    project_reference: str | None = None
    client_name: str | None = None
    principal_contractor: str | None = None
    principal_designer: str | None = None
    description: str | None = None
    building_name: str | None = None
    building_type: str | None = None
    project_status: str = "DRAFT"
    planned_handover_date: date | str | None = None
    responsible_person_name: str | None = None
    responsible_person_email: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    town_city: str | None = None
    county: str | None = None
    postcode: str | None = None
    country: str = "United Kingdom"

    def payload(self) -> dict[str, Any]:
        if not self.name.strip():
            raise ValueError("Project name is required")
        if not (self.project_reference or "").strip():
            raise ValueError("Project reference is required")
        payload = asdict(self)
        payload["name"] = self.name.strip()
        payload["project_reference"] = str(self.project_reference).strip()
        if isinstance(self.planned_handover_date, date):
            payload["planned_handover_date"] = self.planned_handover_date.isoformat()
        return payload


@dataclass(frozen=True)
class ProjectSummary:
    id: str
    name: str
    project_reference: str | None
    project_status: str
    role: str
    building_name: str | None = None
    building_type: str | None = None
    planned_handover_date: str | None = None
    archived_at: str | None = None
    awaiting_review: bool = False


@dataclass(frozen=True)
class CreateProjectPermission:
    """A permission result which cannot confuse an RPC outage with a denial."""
    allowed: bool
    source: str
    check_failed: bool = False


def validate_ifc(filename: str, size: int) -> None:
    if Path(filename).suffix.lower() != ".ifc":
        raise ValueError("Select an IFC file with the .ifc extension.")
    if size <= 0:
        raise ValueError("The selected IFC file is empty.")
    if size > MAX_IFC_BYTES:
        raise ValueError("The IFC file exceeds the 500 MB upload limit.")


class Regulation38Repository:
    DEFAULT_BUCKET = "project-files"

    def __init__(self, auth: SupabaseAuthService | None = None):
        self.auth = auth or SupabaseAuthService()

    @property
    def bucket(self) -> str:
        """Return the bucket id without ever accepting it as part of an object key."""
        return os.getenv("REG38_STORAGE_BUCKET", self.DEFAULT_BUCKET).strip() or self.DEFAULT_BUCKET

    @staticmethod
    def _safe_storage_response(response: requests.Response) -> str:
        """Extract bounded Storage diagnostics without reflecting credentials or tokens."""
        try:
            payload = response.json()
        except (ValueError, AttributeError):
            return str(getattr(response, "text", ""))[:1000].replace("\n", " ")
        if isinstance(payload, Mapping):
            safe = {key: payload[key] for key in ("statusCode", "error", "message") if key in payload}
            return json.dumps(safe or {"response": "Storage returned an unrecognised JSON error"}, separators=(",", ":"))[:1000]
        return json.dumps(payload, separators=(",", ":"))[:1000]

    def _data_request(self, method: str, path: str, access_token: str, **kwargs: Any) -> Any:
        return self.auth._request_json(method, f"{self.auth.settings.project_url}/rest/v1/{path}", access_token=access_token,
            public_error="Projects could not be loaded.", **kwargs)

    def _paged_data_request(self, path: str, token: str, page_size: int = FIRE_STRATEGY_PAGE_SIZE) -> list[dict[str, Any]]:
        """Read a bounded PostgREST result set without relying on the server row cap."""
        rows: list[dict[str, Any]] = []
        offset = 0
        separator = "&" if "?" in path else "?"
        while True:
            page = self._data_request("GET", f"{path}{separator}limit={page_size}&offset={offset}", token)
            page = page if isinstance(page, list) else []
            rows.extend(row for row in page if isinstance(row, dict))
            if len(page) < page_size:
                break
            offset += page_size
        return rows

    @staticmethod
    def _safe_data_response(response: requests.Response) -> str:
        """Return bounded PostgREST diagnostics, excluding headers and credentials."""
        try:
            payload = response.json() if getattr(response, "content", None) else {}
        except ValueError:
            return response.text[:1000].replace("\n", " ")
        if isinstance(payload, Mapping):
            payload = {key: payload[key] for key in ("code", "message", "details", "hint") if key in payload}
        return json.dumps(payload, separators=(",", ":"))[:1000]

    def _completion_data_request(self, method: str, path: str, token: str, reference: str,
                                 project_id: str, file_id: str, stage: str, **kwargs: Any) -> Any:
        """PostgREST request with completion-specific, safe diagnostics."""
        caller_headers = kwargs.pop("headers", None) or {}
        request_headers = {**self.auth._headers(token), **dict(caller_headers)}
        request_timeout = kwargs.pop("timeout", self.auth.settings.request_timeout_seconds)
        try:
            response = requests.request(method, f"{self.auth.settings.project_url}/rest/v1/{path}",
                                        headers=request_headers, timeout=request_timeout, **kwargs)
        except requests.RequestException as exc:
            LOGGER.error("ifc_upload_complete_failed stage=%s reference=%s project_id=%s model_id=%s storage_bucket=%s database_http_status=unavailable response=%s",
                         stage, reference, project_id, file_id, self.bucket, type(exc).__name__)
            raise SupabaseAuthError(f"IFC uploaded, but the project could not be updated. Reference: {reference}",
                                    status_code=503, detail=type(exc).__name__) from exc
        safe_body = self._safe_data_response(response)
        if not 200 <= response.status_code < 300:
            LOGGER.error("ifc_upload_complete_failed stage=%s reference=%s project_id=%s model_id=%s storage_bucket=%s database_http_status=%s response=%s",
                         stage, reference, project_id, file_id, self.bucket, response.status_code, safe_body)
            raise SupabaseAuthError(f"IFC uploaded, but the project could not be updated. Reference: {reference}",
                                    status_code=502, detail=safe_body)
        try:
            return response.json() if getattr(response, "content", None) else None
        except ValueError as exc:
            raise SupabaseAuthError(f"IFC uploaded, but the project could not be updated. Reference: {reference}",
                                    status_code=502, detail="Invalid Supabase JSON response") from exc

    def can_create_project(self, token: str) -> bool:
        return self._data_request("POST", "rpc/can_create_project", token, json={}) is True

    def is_platform_admin(self, token: str) -> bool:
        return self._data_request("POST", "rpc/is_platform_admin", token, json={}) is True

    def resolve_create_permission(self, token: str) -> CreateProjectPermission:
        """Check permission, retaining capability only through a verified admin fallback."""
        try:
            return CreateProjectPermission(self.can_create_project(token), "can_create_project")
        except SupabaseAuthError as permission_error:
            try:
                if self.is_platform_admin(token):
                    return CreateProjectPermission(True, "is_platform_admin", check_failed=True)
            except SupabaseAuthError:
                pass
            raise permission_error

    def create_project(self, token: str, project: ProjectCreate) -> str:
        result = self._data_request("POST", "rpc/create_reg38_project", token, json={"project_data": project.payload()})
        if not isinstance(result, str) or not result:
            raise SupabaseAuthError("The project could not be created.", status_code=502)
        return result

    def list_projects(self, token: str) -> list[ProjectSummary]:
        select = "role,projects(id,name,building_name,project_reference,project_status,building_type,planned_handover_date,archived_at,reg38_sections(completion_status))"
        rows = self._data_request("GET", f"project_members?select={select}&order=created_at.desc", token)
        output = []
        for row in rows if isinstance(rows, list) else []:
            project = row.get("projects") if isinstance(row, Mapping) else None
            if isinstance(project, Mapping):
                sections = project.get("reg38_sections") or []
                output.append(ProjectSummary(str(project["id"]), str(project["name"]), project.get("project_reference"),
                    str(project["project_status"]), str(row["role"]), project.get("building_name"), project.get("building_type"),
                    project.get("planned_handover_date"), project.get("archived_at"),
                    any(section.get("completion_status") == "REVIEW_REQUIRED" for section in sections if isinstance(section, Mapping))))
        return output

    def schema_health(self, token: str) -> dict[str, Any]:
        result = self._data_request("POST", "rpc/reg38_schema_health", token, json={})
        return dict(result) if isinstance(result, Mapping) else {"valid": False, "unexpected_response": True}

    def get_project(self, token: str, project_id: str) -> dict[str, Any] | None:
        rows = self._data_request("GET", f"projects?id=eq.{quote(project_id)}&select=*", token)
        return dict(rows[0]) if isinstance(rows, list) and rows else None

    def update_project(self, token: str, project_id: str, values: Mapping[str, Any]) -> None:
        self._data_request("PATCH", f"projects?id=eq.{quote(project_id)}", token, json=dict(values))

    def get_sections(self, token: str, project_id: str) -> list[dict[str, Any]]:
        rows = self._data_request("GET", f"reg38_sections?project_id=eq.{quote(project_id)}&select=id,section_key,name,enabled,applicability_status,completion_status,sort_order&order=sort_order", token)
        return [dict(row) for row in rows] if isinstance(rows, list) else []

    def get_scope(self, token: str, project_id: str) -> dict[str, Any] | None:
        rows = self._data_request("GET", f"reg38_project_scope?project_id=eq.{quote(project_id)}&select=*&limit=1", token)
        return dict(rows[0]) if isinstance(rows, list) and rows else None

    def firetrace_progress(self, token: str, project: Mapping[str, Any]) -> FireTraceProgress:
        """Load the authoritative progression snapshot used by every entry point."""
        project_id = str(project["id"])
        scope = self.get_scope(token, project_id)
        sections = self.get_sections(token, project_id)
        model = self.get_current_ifc_file(token, project_id)
        job = None
        if model:
            rows = self._data_request("GET", f"ifc_processing_jobs?ifc_file_id=eq.{quote(str(model['id']))}&select=*&order=created_at.desc,id.desc&limit=1", token)
            job = dict(rows[0]) if isinstance(rows, list) and rows else None
        return get_firetrace_resume_step(project, scope, model, job, sections)

    def save_scope(self, token: str, project_id: str, scope_type: str, scope_description: str,
                   building_reference: str, area_description: str, applicability: Mapping[str, str]) -> None:
        self._data_request("POST", "rpc/save_reg38_scope", token, json={"target_project_id": project_id,
            "scope_data": {"scope_type": scope_type, "scope_description": scope_description,
                           "building_reference": building_reference, "area_description": area_description}})
        sections = self.get_sections(token, project_id)
        for section in sections:
            self._data_request("PATCH", f"reg38_sections?id=eq.{quote(str(section['id']))}", token,
                json={"applicability_status": applicability.get(section["section_key"], "TO_BE_CONFIRMED"), "enabled": True})

    def list_ifc_files(self, token: str, project_id: str) -> list[dict[str, Any]]:
        query = "select=id,original_filename,file_size,status,storage_path,ifc_schema,created_at,ifc_processing_jobs(id,status,progress_percent,statistics,completed_at,created_at)&order=created_at.desc,id.desc"
        rows = self._data_request("GET", f"ifc_files?project_id=eq.{quote(project_id)}&{query}", token)
        return [dict(row) for row in rows] if isinstance(rows, list) else []

    def get_current_ifc_file(self, token: str, project_id: str) -> dict[str, Any] | None:
        """Resolve the current model using the established newest-upload semantics.

        IFC replacement retains history, so ``created_at`` (then ``id`` for a
        deterministic tie break) is the sole model selector.  There is no
        ``ifc_files.is_current`` column in the canonical schema.
        """
        files = self.list_ifc_files(token, project_id)
        return files[0] if files else None

    def create_ifc_upload(self, token: str, project_id: str, filename: str, size: int) -> dict[str, str]:
        safe_name = Path(filename).name
        validate_ifc(safe_name, size)
        self.require_project_edit(token, project_id)
        file_id = str(uuid4())
        storage_path = f"projects/{project_id}/models/{file_id}/original/{safe_name}"
        url = f"{self.auth.settings.project_url}/storage/v1/object/upload/sign/{quote(self.bucket, safe='')}/{quote(storage_path, safe='/')}"
        try:
            # This endpoint accepts an empty JSON object. Upsert is enabled only through
            # x-upsert:true; sending an `upsert` JSON property is not part of its API.
            response = requests.post(url, headers=self.auth._headers(token), json={},
                                     timeout=self.auth.settings.request_timeout_seconds)
        except requests.RequestException as exc:
            LOGGER.exception("ifc_upload_sign_failed project_id=%s filename=%s file_size=%s storage_path=%s",
                             project_id, safe_name, size, storage_path)
            raise SupabaseAuthError("The IFC upload is temporarily unavailable.", status_code=503, detail=str(exc)) from exc
        if not 200 <= response.status_code < 300:
            reference = str(uuid4())
            LOGGER.error("ifc_upload_sign_failed reference=%s project_id=%s filename=%s file_size=%s storage_host=%s storage_bucket=%s storage_path=%s storage_method=POST storage_endpoint=/storage/v1/object/upload/sign/{bucket}/{path} storage_request={} storage_headers=Accept:application/json,Content-Type:application/json storage_http_status=%s storage_response=%s",
                         reference, project_id, safe_name, size, urlparse(url).hostname, self.bucket,
                         storage_path, response.status_code, self._safe_storage_response(response))
            raise SupabaseAuthError(f"Storage could not prepare this upload. Reference: {reference}", status_code=502)
        payload = response.json()
        signed_url = payload.get("signedURL") or payload.get("signedUrl") or payload.get("url")
        if not signed_url:
            raise SupabaseAuthError("The IFC upload could not be prepared.", status_code=502)
        if signed_url.startswith("/"):
            signed_url = f"{self.auth.settings.project_url}/storage/v1{signed_url}"
        return {"file_id": file_id, "storage_path": storage_path, "signed_url": signed_url}

    def finalize_ifc_upload(self, token: str, user_id: str, project_id: str, file_id: str,
                            filename: str, size: int, storage_path: str) -> dict[str, str]:
        reference = str(uuid4())
        safe_name = Path(filename).name
        stage = "started"
        LOGGER.info("ifc_upload_complete_started reference=%s project_id=%s model_id=%s storage_bucket=%s storage_path=%s user_id=%s",
                    reference, project_id, file_id, self.bucket, storage_path, user_id)
        validate_ifc(safe_name, size)
        try:
            self.require_project_edit(token, project_id)
        except Exception:
            LOGGER.exception("ifc_upload_complete_failed stage=authorization reference=%s project_id=%s model_id=%s storage_bucket=%s storage_path=%s user_id=%s",
                             reference, project_id, file_id, self.bucket, storage_path, user_id)
            raise SupabaseAuthError(f"IFC uploaded, but the project could not be updated. Reference: {reference}", status_code=403)
        expected_path = f"projects/{project_id}/models/{file_id}/original/{safe_name}"
        if storage_path != expected_path:
            raise ValueError("The IFC upload details are invalid.")
        object_url = f"{self.auth.settings.project_url}/storage/v1/object/{quote(self.bucket, safe='')}/{quote(storage_path, safe='/')}"
        try:
            stored = requests.head(object_url, headers=self.auth._headers(token),
                                   timeout=self.auth.settings.request_timeout_seconds)
        except requests.RequestException as exc:
            LOGGER.exception("ifc_upload_complete_failed stage=storage_verify reference=%s project_id=%s model_id=%s user_id=%s storage_bucket=%s storage_path=%s storage_http_status=unavailable response=%s",
                             reference, project_id, file_id, user_id, self.bucket, storage_path, type(exc).__name__)
            raise SupabaseAuthError(f"IFC uploaded, but the project could not be updated. Reference: {reference}", status_code=503) from exc
        if not 200 <= stored.status_code < 300:
            LOGGER.error("ifc_upload_complete_failed stage=storage_verify reference=%s project_id=%s model_id=%s user_id=%s storage_bucket=%s storage_path=%s storage_http_status=%s response=%s",
                         reference, project_id, file_id, user_id, self.bucket, storage_path, stored.status_code,
                         self._safe_storage_response(stored))
            raise SupabaseAuthError(f"IFC uploaded, but the project could not be updated. Reference: {reference}", status_code=502)
        stored_size = stored.headers.get("content-length")
        if stored_size and int(stored_size) != size:
            LOGGER.error("ifc_upload_complete_failed stage=storage_size reference=%s project_id=%s model_id=%s user_id=%s storage_bucket=%s storage_path=%s storage_http_status=%s declared_size=%s stored_size=%s",
                         reference, project_id, file_id, user_id, self.bucket, storage_path, stored.status_code, size, stored_size)
            raise SupabaseAuthError(f"IFC uploaded, but the project could not be updated. Reference: {reference}", status_code=400)
        LOGGER.info("ifc_upload_complete_storage_verified reference=%s project_id=%s model_id=%s storage_bucket=%s storage_path=%s user_id=%s storage_http_status=%s",
                    reference, project_id, file_id, self.bucket, storage_path, user_id, stored.status_code)
        job_id = str(uuid4())
        try:
            stage = "model_update"
            rpc_result = self._completion_data_request("POST", "rpc/finalize_ifc_upload", token, reference, project_id, file_id, stage, json={
                "target_project": project_id, "target_file": file_id, "target_job": job_id,
                "object_path": storage_path, "original_name": safe_name, "object_size": size})
            if isinstance(rpc_result, str):
                job_id = rpc_result
            LOGGER.info("ifc_upload_complete_model_updated reference=%s project_id=%s model_id=%s storage_bucket=%s storage_path=%s user_id=%s database_http_status=200",
                        reference, project_id, file_id, self.bucket, storage_path, user_id)
            stage = "project_load"
            project_rows = self._completion_data_request("GET", f"projects?id=eq.{quote(project_id)}&select=id", token,
                                                         reference, project_id, file_id, stage)
            model_rows = self._completion_data_request("GET", f"ifc_files?id=eq.{quote(file_id)}&project_id=eq.{quote(project_id)}&select=id,status,storage_path", token,
                                                       reference, project_id, file_id, stage)
            if not project_rows or not model_rows or model_rows[0].get("storage_path") != storage_path:
                raise SupabaseAuthError(f"IFC uploaded, but the project could not be updated. Reference: {reference}", status_code=502)
            LOGGER.info("ifc_upload_complete_project_loaded reference=%s project_id=%s model_id=%s storage_bucket=%s storage_path=%s user_id=%s database_http_status=200",
                        reference, project_id, file_id, self.bucket, storage_path, user_id)
        except SupabaseAuthError:
            raise
        LOGGER.info("ifc_upload_complete_success reference=%s project_id=%s model_id=%s storage_bucket=%s storage_path=%s user_id=%s",
                    reference, project_id, file_id, self.bucket, storage_path, user_id)
        return {"file_id": file_id, "job_id": job_id, "storage_path": storage_path, "reference": reference}

    def remove_ifc(self, token: str, project_id: str, file_id: str, storage_path: str) -> None:
        self.require_project_edit(token, project_id)
        expected_prefix = f"projects/{project_id}/models/{file_id}/"
        if not storage_path.startswith(expected_prefix):
            raise ValueError("The IFC file path is invalid.")
        # One transaction removes every row derived from this model while retaining
        # manual scope/configuration. Storage is necessarily handled separately.
        self._data_request("POST", "rpc/remove_reg38_ifc_model", token,
                           json={"target_project": project_id, "target_file": file_id})
        url = f"{self.auth.settings.project_url}/storage/v1/object/{quote(self.bucket, safe='')}/{quote(storage_path, safe='/')}"
        requests.delete(url, headers=self.auth._headers(token), timeout=self.auth.settings.request_timeout_seconds)

    def acknowledge_missing_spatial_data(self, token: str, project_id: str, user_id: str) -> None:
        self.require_project_edit(token, project_id)
        self._data_request("POST", "rpc/acknowledge_reg38_missing_spatial_data", token,
                           json={"target_project": project_id, "target_user": user_id})

    def delete_draft_project(self, token: str, project_id: str) -> None:
        """Delete private objects first, then transactionally remove a DRAFT project."""
        if self.project_role(token, project_id) not in {"OWNER", "ADMIN"} and not self.is_platform_admin(token):
            raise SupabaseAuthError("Only a project owner or administrator can delete a draft project.", status_code=403)
        prefix = f"projects/{project_id}/"
        list_url = f"{self.auth.settings.project_url}/storage/v1/object/list/{quote(self.bucket, safe='')}"
        response = requests.post(list_url, headers=self.auth._headers(token),
                                 json={"prefix": prefix, "limit": 1000, "offset": 0},
                                 timeout=self.auth.settings.request_timeout_seconds)
        if not 200 <= response.status_code < 300:
            raise SupabaseAuthError("Project files could not be cleaned up.", status_code=502)
        objects = response.json() if getattr(response, "content", None) else []
        paths = [prefix + str(item["name"]) for item in objects if isinstance(item, Mapping) and item.get("name")]
        if paths:
            delete_url = f"{self.auth.settings.project_url}/storage/v1/object/{quote(self.bucket, safe='')}"
            deleted = requests.delete(delete_url, headers=self.auth._headers(token), json={"prefixes": paths},
                                      timeout=self.auth.settings.request_timeout_seconds)
            if not 200 <= deleted.status_code < 300:
                raise SupabaseAuthError("Project files could not be cleaned up.", status_code=502)
        self._data_request("POST", "rpc/delete_draft_reg38_project", token,
                           json={"target_project": project_id})

    def cleanup_failed_upload(self, token: str, project_id: str, storage_path: str) -> None:
        """Remove an uploaded object that never reached atomic finalization."""
        self.require_project_edit(token, project_id)
        if not storage_path.startswith(f"projects/{project_id}/models/") or "/original/" not in storage_path:
            raise ValueError("The IFC file path is invalid.")
        url = f"{self.auth.settings.project_url}/storage/v1/object/{quote(self.bucket, safe='')}/{quote(storage_path, safe='/')}"
        requests.delete(url, headers=self.auth._headers(token), timeout=self.auth.settings.request_timeout_seconds)

    def project_role(self, token: str, project_id: str) -> str | None:
        rows = self._data_request("GET", f"project_members?project_id=eq.{quote(project_id)}&select=role", token)
        role = rows[0].get("role") if isinstance(rows, list) and rows else None
        return str(role) if role is not None else None

    def model_scan(self, token: str, project_id: str, authenticated_user_id: str = "") -> dict[str, Any]:
        """Load scan prerequisites using RLS membership, without an Admin Auth lookup."""
        reference = str(uuid4())
        project_rows = self._data_request(
            "GET", f"projects?id=eq.{quote(project_id)}&select=id,created_by", token)
        if not project_rows:
            LOGGER.info("reg38_model_scan_project_missing reference=%s project_id=%s authenticated_user_id=%s",
                        reference, project_id, authenticated_user_id)
            raise SupabaseAuthError("Project not found.", status_code=404)
        project = dict(project_rows[0])
        members = self._data_request(
            "GET", f"project_members?project_id=eq.{quote(project_id)}&select=id,user_id,role", token)
        member_user_ids = [str(row.get("user_id") or "") for row in members if isinstance(row, Mapping)]
        file = self.get_current_ifc_file(token, project_id)
        if not file:
            LOGGER.info(
                "reg38_model_scan_prerequisites reference=%s project_id=%s authenticated_user_id=%s "
                "created_by=%s member_user_ids=%s project=present finalized_ifc_file=missing processing_job=missing",
                reference, project_id, authenticated_user_id, project.get("created_by"), member_user_ids,
            )
            return {"job": None, "warnings": []}
        jobs = self._data_request("GET", f"ifc_processing_jobs?ifc_file_id=eq.{quote(str(file['id']))}&select=*&order=created_at.desc,id.desc&limit=1", token)
        # Aggregate counts live in job.statistics. Bound preview rows so a model
        # with thousands of findings never creates a multi-megabyte wizard page.
        warnings = self._data_request("GET", f"model_scan_warnings?ifc_file_id=eq.{quote(str(file['id']))}&select=id,warning_code,title,severity&order=created_at&limit=100", token)
        job = dict(jobs[0]) if isinstance(jobs, list) and jobs else None
        LOGGER.info(
            "reg38_model_scan_prerequisites reference=%s project_id=%s authenticated_user_id=%s created_by=%s "
            "member_user_ids=%s project=present finalized_ifc_file=present model_id=%s model_status=%s "
            "storage_path=%s processing_job=%s job_status=%s",
            reference, project_id, authenticated_user_id, project.get("created_by"), member_user_ids,
            file.get("id"), file.get("status"), file.get("storage_path"), "present" if job else "missing",
            (job or {}).get("status"),
        )
        return {"file": file, "job": job,
                "warnings": warnings if isinstance(warnings, list) else []}

    def retry_model_scan(self, token: str, project_id: str, file_id: str) -> str:
        self.require_project_admin(token, project_id)
        value = self._data_request("POST", "rpc/retry_reg38_ifc_job", token, json={"target_file": file_id})
        return str(value)

    def rerun_model_scan(self, token: str, project_id: str, file_id: str, user_id: str) -> str:
        """Queue a fresh job for a completed file without replacing its source object."""
        self.require_project_admin(token, project_id)
        scan = self.model_scan(token, project_id, user_id)
        current_file, previous_job = scan.get("file"), scan.get("job")
        if not current_file or str(current_file.get("id")) != file_id:
            raise SupabaseAuthError("The current IFC model could not be found.", status_code=404)
        if str((previous_job or {}).get("status") or "").upper() not in {"COMPLETED", "SUCCEEDED"}:
            raise SupabaseAuthError("Only a completed Model Scan can be re-run.", status_code=409)
        value = self._data_request("POST", "rpc/rerun_reg38_ifc_job", token, json={"target_file": file_id})
        new_job_id = str(value)
        LOGGER.info(
            "event=model_scan_rerun_requested project_id=%s file_id=%s previous_job_id=%s new_job_id=%s user_id=%s",
            project_id, file_id, previous_job.get("id"), new_job_id, user_id,
        )
        return new_job_id

    def require_project_admin(self, token: str, project_id: str) -> None:
        if self.project_role(token, project_id) not in {"OWNER", "ADMIN"}:
            raise SupabaseAuthError("Only a project owner or administrator can review spaces and zones.", status_code=403)

    def require_project_edit(self, token: str, project_id: str) -> None:
        if self.project_role(token, project_id) not in {"OWNER", "ADMIN", "EDITOR"} and not self.is_platform_admin(token):
            raise SupabaseAuthError("You do not have permission to edit this project.", status_code=403)

    def spatial_review(self, token: str, project_id: str) -> dict[str, Any]:
        """Return source and working spatial data separately; source tables are read-only."""
        pid = quote(project_id)
        spaces = self._data_request("GET", f"project_spaces?project_id=eq.{pid}&select=*,building_storeys(id,name,elevation),ifc_objects(ifc_entity,name,long_name,description,source_data)&order=name", token)
        zones = self._data_request("GET", f"project_zones?project_id=eq.{pid}&select=*&order=name", token)
        grids = self._data_request("GET", f"project_grids?project_id=eq.{pid}&select=*,project_grid_axes(*)&order=name", token)
        members = self._data_request("GET", f"project_zone_members?zone_id=in.({','.join(str(z['id']) for z in zones)})&select=id,zone_id,space_id,source", token) if zones else []
        spaces = spaces if isinstance(spaces, list) else []
        def geometry_needs_backfill(space: Mapping[str, Any]) -> bool:
            geometry = space.get("source_geometry")
            return (not isinstance(geometry, Mapping) or geometry.get("reason") in
                    {"BACKFILL_REQUIRED", "NOT_PROCESSED", "GEOMETRY_ENGINE_FAILURE", "TRANSIENT_ERROR"})
        geometries = [space.get("source_geometry") if isinstance(space.get("source_geometry"), Mapping) else {}
                      for space in spaces]
        summary = {
            "spaces": len(spaces),
            "missing_direct": sum(g.get("geometry_method") != "DIRECT_REPRESENTATION" for g in geometries),
            "direct": sum(g.get("geometry_method") == "DIRECT_REPRESENTATION" for g in geometries),
            "boundary": sum(g.get("geometry_method") == "SPACE_BOUNDARY" for g in geometries),
            "elements": sum(g.get("geometry_method") == "BOUNDING_ELEMENTS" for g in geometries),
            "unresolved": sum(g.get("type") != "Polygon" for g in geometries),
        }
        return {"spaces": spaces, "zones": zones if isinstance(zones, list) else [],
                "grids": grids if isinstance(grids, list) else [], "members": members if isinstance(members, list) else [],
                "can_admin": self.project_role(token, project_id) in {"OWNER", "ADMIN"},
                "geometry_summary": summary,
                "geometry_backfill_required": any(geometry_needs_backfill(space) for space in spaces)}

    def spatial_storey_plan(self, token: str, project_id: str, storey_id: str) -> dict[str, Any]:
        """Return persisted lightweight geometry for one authorised storey."""
        if self.project_role(token, project_id) is None and not self.is_platform_admin(token):
            raise SupabaseAuthError("You cannot access this project.", status_code=403)
        rows = self._data_request("GET", f"project_spaces?project_id=eq.{quote(project_id)}&storey_id=eq.{quote(storey_id)}"
                                  "&select=id,storey_id,space_number,name,description,source_geometry&order=space_number", token)
        spaces = rows if isinstance(rows, list) else []
        return {"project_id": project_id, "storey_id": storey_id, "spaces": spaces,
                "geometry_status": "available" if any((s.get("source_geometry") or {}).get("coordinates") for s in spaces) else "unavailable"}

    def fire_strategy(self, token: str, project_id: str, user_id: str = "") -> dict[str, Any]:
        """Return persisted scan data and idempotently seed review suggestions."""
        started = perf_counter()
        if self.project_role(token, project_id) is None and not self.is_platform_admin(token):
            raise SupabaseAuthError("You cannot access this project.", status_code=403)
        pid = quote(project_id)
        model = self.get_current_ifc_file(token, project_id)
        if not model:
            return {"ready": False, "error": "Model Scan data is missing. Complete Model Scan before reviewing Fire Strategy."}
        mid = quote(str(model["id"]))
        LOGGER.info("event=fire_strategy_load_started project_id=%s model_id=%s", project_id, model["id"])
        jobs = self._data_request("GET", f"ifc_processing_jobs?ifc_file_id=eq.{mid}&select=status&order=created_at.desc&limit=1", token)
        status = str((jobs[0] if isinstance(jobs, list) and jobs else {}).get("status") or model.get("status") or "").upper()
        if status not in {"COMPLETED", "SUCCEEDED"}:
            return {"ready": False, "error": "Model processing must complete successfully before Fire Strategy review."}
        property_started = perf_counter()
        properties = self._paged_data_request(
            f"ifc_object_properties?is_fire_relevant=eq.true&select=ifc_object_id,property_set,property_name,property_value_text,source_scope,ifc_objects!inner(project_id,ifc_file_id)"
            f"&ifc_objects.project_id=eq.{pid}&ifc_objects.ifc_file_id=eq.{mid}&order=ifc_object_id,id", token)
        LOGGER.info("event=fire_strategy_properties_loaded project_id=%s model_id=%s property_count=%s duration_ms=%s",
                    project_id, model["id"], len(properties), round((perf_counter() - property_started) * 1000))
        object_started = perf_counter()
        entity_filter = ",".join(sorted(FIRE_RELEVANT_ENTITIES))
        object_select = "id,ifc_global_id,ifc_entity,name,long_name,description,object_type,predefined_type,storey_id,building_storeys(id,name)"
        objects = self._paged_data_request(
            f"ifc_objects?project_id=eq.{pid}&ifc_file_id=eq.{mid}&ifc_entity=in.({entity_filter})&select={object_select}&order=ifc_entity,name,id", token)
        known_ids = {str(obj.get("id")) for obj in objects}
        property_object_ids = list(dict.fromkeys(str(prop["ifc_object_id"]) for prop in properties if prop.get("ifc_object_id")))
        for offset in range(0, len(property_object_ids), FIRE_STRATEGY_ID_BATCH_SIZE):
            missing = [value for value in property_object_ids[offset:offset + FIRE_STRATEGY_ID_BATCH_SIZE] if value not in known_ids]
            if not missing:
                continue
            batch = self._data_request("GET", f"ifc_objects?project_id=eq.{pid}&ifc_file_id=eq.{mid}"
                                       f"&id=in.({','.join(map(quote, missing))})&select={object_select}&order=ifc_entity,name,id", token)
            for obj in batch if isinstance(batch, list) else []:
                if str(obj.get("id")) not in known_ids:
                    objects.append(obj); known_ids.add(str(obj.get("id")))
        spaces = self._data_request(
            "GET", f"project_spaces?project_id=eq.{pid}&select=id,storey_id,space_number,name,source_geometry,working_geometry&order=space_number", token)
        spaces = spaces if isinstance(spaces, list) else []
        review_started = perf_counter()
        existing = self._paged_data_request(
            f"fire_strategy_reviews?project_id=eq.{pid}&model_id=eq.{mid}&select=*&order=id", token)
        LOGGER.info("event=fire_strategy_reviews_loaded project_id=%s model_id=%s review_count=%s duration_ms=%s",
                    project_id, model["id"], len(existing), round((perf_counter() - review_started) * 1000))
        # A persisted manual review remains visible even if it is not an entity or
        # property candidate under the current canonical classifier.
        reviewed_ids = list(dict.fromkeys(str(row["ifc_object_id"]) for row in existing
                                           if row.get("ifc_object_id") and str(row["ifc_object_id"]) not in known_ids))
        for offset in range(0, len(reviewed_ids), FIRE_STRATEGY_ID_BATCH_SIZE):
            batch_ids = reviewed_ids[offset:offset + FIRE_STRATEGY_ID_BATCH_SIZE]
            batch = self._data_request("GET", f"ifc_objects?project_id=eq.{pid}&ifc_file_id=eq.{mid}"
                                       f"&id=in.({','.join(map(quote, batch_ids))})&select={object_select}&order=ifc_entity,name,id", token)
            for obj in batch if isinstance(batch, list) else []:
                if str(obj.get("id")) not in known_ids:
                    objects.append(obj); known_ids.add(str(obj.get("id")))
        LOGGER.info("event=fire_strategy_candidates_loaded project_id=%s model_id=%s objects_count=%s duration_ms=%s",
                    project_id, model["id"], len(objects), round((perf_counter() - object_started) * 1000))
        existing_guids = {str(row.get("ifc_global_id")) for row in existing}
        seeds = []
        properties_by_object: dict[str, list[dict[str, Any]]] = {}
        for prop in properties:
            properties_by_object.setdefault(str(prop.get("ifc_object_id")), []).append(prop)
        for obj in objects:
            reasons = []
            if obj.get("ifc_entity") in FIRE_RELEVANT_ENTITIES:
                reasons.append(f"IFC entity {obj['ifc_entity']} is potentially fire relevant")
            for prop in properties_by_object.get(str(obj.get("id")), []):
                reasons.append(f"{prop.get('property_set') or 'IFC'}.{prop.get('property_name')} is populated")
            gid = str(obj.get("ifc_global_id") or "")
            if reasons and gid and gid not in existing_guids:
                seed = {"project_id": project_id, "model_id": model["id"], "ifc_object_id": obj["id"],
                    "ifc_global_id": gid, "entity_type": obj.get("ifc_entity"), "automatically_suggested": True,
                    "suggestion_reason": "Suggested because " + "; ".join(dict.fromkeys(reasons)),
                    "original_values": {key: obj.get(key) for key in ("name", "long_name", "description", "predefined_type", "object_type", "storey_id")}}
                seeds.append(seed)
        if seeds:
            self._data_request("POST", "fire_strategy_reviews?on_conflict=project_id,model_id,ifc_global_id", token,
                               json=seeds, headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
            existing = self._paged_data_request(
                f"fire_strategy_reviews?project_id=eq.{pid}&model_id=eq.{mid}&select=*&order=id", token)
        object_by_guid = {str(obj.get("ifc_global_id")): obj for obj in objects}
        reviews = [{**row, "object": object_by_guid.get(str(row.get("ifc_global_id")))} for row in existing]
        summary = self._fire_strategy_summary(reviews)
        LOGGER.info("event=fire_strategy_load_completed project_id=%s model_id=%s duration_ms=%s",
                    project_id, model["id"], round((perf_counter() - started) * 1000))
        return {"ready": True, "model": model, "objects": objects, "spaces": spaces,
                "reviews": reviews, "summary": summary,
                "can_edit": self.project_role(token, project_id) in {"OWNER", "ADMIN", "EDITOR"},
                "categories": FIRE_STRATEGY_CATEGORIES}

    def fire_strategy_object(self, token: str, project_id: str, object_id: str) -> dict[str, Any]:
        """Load one candidate's relevant property provenance on demand."""
        if self.project_role(token, project_id) is None and not self.is_platform_admin(token):
            raise SupabaseAuthError("You cannot access this project.", status_code=403)
        rows = self._data_request("GET", f"ifc_objects?project_id=eq.{quote(project_id)}&id=eq.{quote(object_id)}"
                                  "&select=id,ifc_global_id,ifc_entity,name,long_name,description,object_type,predefined_type,storey_id,building_storeys(id,name)&limit=1", token)
        if not isinstance(rows, list) or not rows:
            raise SupabaseAuthError("IFC object not found.", status_code=404)
        properties = self._paged_data_request(
            f"ifc_object_properties?ifc_object_id=eq.{quote(object_id)}&is_fire_relevant=eq.true"
            "&select=property_set,property_name,property_value_text,source_scope&order=property_set,property_name", token)
        return {**rows[0], "ifc_object_properties": properties}

    @staticmethod
    def _fire_strategy_summary(reviews: list[Mapping[str, Any]]) -> dict[str, Any]:
        active = [r for r in reviews if not r.get("orphaned")]
        missing_category = sum(r.get("relevance") == "IN_SCOPE" and not (r.get("categories") or []) for r in active)
        missing_evidence = sum(r.get("relevance") == "IN_SCOPE" and not str(r.get("evidence_required") or "").strip()
                               and not r.get("no_evidence_required") for r in active)
        unreviewed = sum(r.get("automatically_suggested") and r.get("relevance") == "NOT_ASSESSED" for r in active)
        return {"total_suggestions": sum(bool(r.get("automatically_suggested")) for r in active),
            "reviewed": len(active) - unreviewed, "in_scope": sum(r.get("relevance") == "IN_SCOPE" for r in active),
            "out_of_scope": sum(r.get("relevance") == "OUT_OF_SCOPE" for r in active),
            "review_required": sum(r.get("relevance") == "REVIEW_REQUIRED" for r in active),
            "missing_category": missing_category, "missing_evidence": missing_evidence,
            "complete": not (unreviewed or missing_category or missing_evidence)}

    def update_fire_strategy(self, token: str, project_id: str, review_ids: list[str], values: Mapping[str, Any], user_id: str) -> None:
        self.require_project_edit(token, project_id)
        if not review_ids: raise ValueError("Select at least one review record.")
        allowed = {"relevance", "categories", "requirement_reference", "required_fire_performance", "evidence_required",
                   "no_evidence_required", "review_notes", "responsible_organisation", "review_status"}
        payload = {key: values[key] for key in allowed if key in values}
        payload["reviewed_by"] = user_id or None
        ids = ",".join(quote(value) for value in review_ids)
        self._data_request("PATCH", f"fire_strategy_reviews?project_id=eq.{quote(project_id)}&id=in.({ids})", token, json=payload)

    def update_space(self, token: str, project_id: str, space_id: str, values: Mapping[str, Any]) -> None:
        self.require_project_admin(token, project_id)
        allowed = {"space_number", "name", "description", "occupancy_type", "occupancy_capacity", "high_risk", "included_in_reg38", "working_geometry"}
        payload = {key: values[key] for key in allowed if key in values}
        payload["working_fields_edited"] = True
        if "working_geometry" in payload:
            geometry = payload["working_geometry"]
            if not isinstance(geometry, Mapping) or geometry.get("type") != "Polygon" or len(geometry.get("coordinates") or []) < 4:
                raise ValueError("Working geometry must be a closed polygon.")
            ring = geometry["coordinates"]
            if ring[0] != ring[-1]:
                raise ValueError("Working geometry must be closed.")
            payload["working_geometry"] = {**geometry, "geometry_method": "MANUAL", "source": "USER", "confidence": "MANUAL"}
        capacity = payload.get("occupancy_capacity")
        if capacity not in (None, ""):
            payload["occupancy_capacity"] = int(capacity)
            if payload["occupancy_capacity"] < 0: raise ValueError("Occupancy capacity cannot be negative.")
        else: payload["occupancy_capacity"] = None
        self._data_request("PATCH", f"project_spaces?id=eq.{quote(space_id)}&project_id=eq.{quote(project_id)}", token, json=payload)

    def create_zone(self, token: str, project_id: str, name: str, zone_type: str, space_ids: list[str]) -> str:
        self.require_project_admin(token, project_id)
        if not name.strip(): raise ValueError("Zone name is required.")
        if zone_type not in ZONE_TYPES: raise ValueError("Select a valid zone type.")
        if not space_ids: raise ValueError("Select at least one space.")
        spaces = self._data_request("GET", f"project_spaces?project_id=eq.{quote(project_id)}&id=in.({','.join(quote(x) for x in space_ids)})&select=id,building_id,storey_id", token)
        if not isinstance(spaces, list) or {str(x['id']) for x in spaces} != set(space_ids):
            raise ValueError("One or more selected spaces do not belong to this project.")
        zone_id = str(uuid4()); first = spaces[0]
        self._data_request("POST", "project_zones", token, json={"id": zone_id, "project_id": project_id,
            "building_id": first.get("building_id"), "storey_id": first.get("storey_id") if len({x.get('storey_id') for x in spaces}) == 1 else None,
            "source_kind": "MANUAL", "name": name.strip(), "zone_type": zone_type})
        self._data_request("POST", "project_zone_members", token, json=[{"zone_id": zone_id, "space_id": sid, "source": "MANUAL"} for sid in dict.fromkeys(space_ids)])
        return zone_id

    def update_zone(self, token: str, project_id: str, zone_id: str, name: str, zone_type: str, space_ids: list[str]) -> None:
        self.require_project_admin(token, project_id)
        if not name.strip(): raise ValueError("Zone name is required.")
        if zone_type not in ZONE_TYPES: raise ValueError("Select a valid zone type.")
        valid = self._data_request("GET", f"project_spaces?project_id=eq.{quote(project_id)}&id=in.({','.join(quote(x) for x in space_ids)})&select=id", token) if space_ids else []
        if {str(x["id"]) for x in valid} != set(space_ids): raise ValueError("One or more selected spaces do not belong to this project.")
        self._data_request("PATCH", f"project_zones?id=eq.{quote(zone_id)}&project_id=eq.{quote(project_id)}", token, json={"name": name.strip(), "zone_type": zone_type})
        existing = self._data_request("GET", f"project_zone_members?zone_id=eq.{quote(zone_id)}&select=space_id", token)
        old, new = {str(x["space_id"]) for x in existing}, set(space_ids)
        for sid in old - new:
            self._data_request("DELETE", f"project_zone_members?zone_id=eq.{quote(zone_id)}&space_id=eq.{quote(sid)}", token)
        additions = [{"zone_id": zone_id, "space_id": sid, "source": "MANUAL"} for sid in new - old]
        if additions: self._data_request("POST", "project_zone_members", token, json=additions)
