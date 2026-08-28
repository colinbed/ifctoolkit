"""RLS-scoped persistence and private Storage operations for Regulation 38."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote
from uuid import uuid4

import requests

from ifc_app.supabase_auth import SupabaseAuthError, SupabaseAuthService

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


@dataclass(frozen=True)
class ProjectCreate:
    name: str
    project_reference: str | None = None
    client_name: str | None = None
    principal_contractor: str | None = None
    principal_designer: str | None = None
    description: str | None = None
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
    building_type: str | None = None
    planned_handover_date: str | None = None


def validate_ifc(filename: str, size: int) -> None:
    if Path(filename).suffix.lower() != ".ifc":
        raise ValueError("Select an IFC file with the .ifc extension.")
    if size <= 0:
        raise ValueError("The selected IFC file is empty.")
    if size > MAX_IFC_BYTES:
        raise ValueError("The IFC file exceeds the 500 MB upload limit.")


class Regulation38Repository:
    BUCKET = "reg38-evidence"

    def __init__(self, auth: SupabaseAuthService | None = None):
        self.auth = auth or SupabaseAuthService()

    def _data_request(self, method: str, path: str, access_token: str, **kwargs: Any) -> Any:
        return self.auth._request_json(method, f"{self.auth.settings.project_url}/rest/v1/{path}", access_token=access_token,
            public_error="The Regulation 38 project service is temporarily unavailable.", **kwargs)

    def can_create_project(self, token: str) -> bool:
        return self._data_request("POST", "rpc/can_create_project", token, json={}) is True

    def create_project(self, token: str, project: ProjectCreate) -> str:
        result = self._data_request("POST", "rpc/create_reg38_project", token, json={"project_data": project.payload()})
        if not isinstance(result, str) or not result:
            raise SupabaseAuthError("The project could not be created.", status_code=502)
        return result

    def list_projects(self, token: str) -> list[ProjectSummary]:
        select = "role,projects(id,name,project_reference,project_status,building_type,planned_handover_date)"
        rows = self._data_request("GET", f"project_members?select={select}&order=created_at.desc", token)
        output = []
        for row in rows if isinstance(rows, list) else []:
            project = row.get("projects") if isinstance(row, Mapping) else None
            if isinstance(project, Mapping):
                output.append(ProjectSummary(str(project["id"]), str(project["name"]), project.get("project_reference"),
                    str(project["project_status"]), str(row["role"]), project.get("building_type"), project.get("planned_handover_date")))
        return output

    def get_project(self, token: str, project_id: str) -> dict[str, Any] | None:
        rows = self._data_request("GET", f"projects?id=eq.{quote(project_id)}&select=*", token)
        return dict(rows[0]) if isinstance(rows, list) and rows else None

    def update_project(self, token: str, project_id: str, values: Mapping[str, Any]) -> None:
        self._data_request("PATCH", f"projects?id=eq.{quote(project_id)}", token, json=dict(values))

    def get_sections(self, token: str, project_id: str) -> list[dict[str, Any]]:
        rows = self._data_request("GET", f"reg38_sections?project_id=eq.{quote(project_id)}&select=id,section_key,name,enabled,sort_order&order=sort_order", token)
        return [dict(row) for row in rows] if isinstance(rows, list) else []

    def save_scope(self, token: str, project_id: str, scope_type: str, scope_detail: str, enabled: set[str]) -> None:
        self.update_project(token, project_id, {"reg38_scope_type": scope_type, "reg38_scope_detail": scope_detail})
        sections = self.get_sections(token, project_id)
        for section in sections:
            self._data_request("PATCH", f"reg38_sections?id=eq.{quote(str(section['id']))}", token,
                json={"enabled": section["section_key"] in enabled})

    def list_ifc_files(self, token: str, project_id: str) -> list[dict[str, Any]]:
        query = "select=id,original_filename,file_size,status,storage_path,ifc_processing_jobs(id,status,progress_percent)&order=created_at.desc"
        rows = self._data_request("GET", f"ifc_files?project_id=eq.{quote(project_id)}&{query}", token)
        return [dict(row) for row in rows] if isinstance(rows, list) else []

    def upload_ifc(self, token: str, user_id: str, project_id: str, filename: str, content: bytes) -> dict[str, str]:
        safe_name = Path(filename).name
        validate_ifc(safe_name, len(content))
        file_id = str(uuid4())
        storage_path = f"projects/{project_id}/models/{file_id}/{safe_name}"
        url = f"{self.auth.settings.project_url}/storage/v1/object/{self.BUCKET}/{quote(storage_path, safe='/')}"
        headers = self.auth._headers(token)
        headers.update({"Content-Type": "application/octet-stream", "x-upsert": "false"})
        try:
            response = requests.post(url, headers=headers, data=content, timeout=self.auth.settings.request_timeout_seconds)
        except requests.RequestException as exc:
            raise SupabaseAuthError("The IFC upload is temporarily unavailable.", status_code=503, detail=str(exc)) from exc
        if not 200 <= response.status_code < 300:
            raise SupabaseAuthError("The IFC file could not be uploaded.", status_code=response.status_code)
        try:
            self._data_request("POST", "ifc_files", token, json={"id": file_id, "project_id": project_id, "storage_path": storage_path,
                "original_filename": safe_name, "file_size": len(content), "uploaded_by": user_id, "status": "UPLOADED"})
            job_id = str(uuid4())
            self._data_request("POST", "ifc_processing_jobs", token, json={"id": job_id, "project_id": project_id,
                "ifc_file_id": file_id, "status": "QUEUED"})
        except Exception:
            requests.delete(url, headers=self.auth._headers(token), timeout=self.auth.settings.request_timeout_seconds)
            raise
        return {"file_id": file_id, "job_id": job_id, "storage_path": storage_path}

    def remove_ifc(self, token: str, file_id: str, storage_path: str) -> None:
        self._data_request("DELETE", f"ifc_processing_jobs?ifc_file_id=eq.{quote(file_id)}&status=eq.QUEUED", token)
        self._data_request("DELETE", f"ifc_files?id=eq.{quote(file_id)}", token)
        url = f"{self.auth.settings.project_url}/storage/v1/object/{self.BUCKET}/{quote(storage_path, safe='/')}"
        requests.delete(url, headers=self.auth._headers(token), timeout=self.auth.settings.request_timeout_seconds)
