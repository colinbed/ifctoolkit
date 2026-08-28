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
ZONE_TYPES = ("FIRE_COMPARTMENT", "SMOKE_ZONE", "ALARM_ZONE", "SPRINKLER_ZONE", "EVACUATION_ZONE",
              "OCCUPANCY_ZONE", "REFUGE", "HIGH_RISK", "USER_DEFINED")


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
            public_error="Projects could not be loaded.", **kwargs)

    def can_create_project(self, token: str) -> bool:
        return self._data_request("POST", "rpc/can_create_project", token, json={}) is True

    def create_project(self, token: str, project: ProjectCreate) -> str:
        result = self._data_request("POST", "rpc/create_reg38_project", token, json={"project_data": project.payload()})
        if not isinstance(result, str) or not result:
            raise SupabaseAuthError("The project could not be created.", status_code=502)
        return result

    def list_projects(self, token: str) -> list[ProjectSummary]:
        select = "role,projects(id,name,building_name,project_reference,project_status,building_type,planned_handover_date)"
        rows = self._data_request("GET", f"project_members?select={select}&order=created_at.desc", token)
        output = []
        for row in rows if isinstance(rows, list) else []:
            project = row.get("projects") if isinstance(row, Mapping) else None
            if isinstance(project, Mapping):
                output.append(ProjectSummary(str(project["id"]), str(project["name"]), project.get("project_reference"),
                    str(project["project_status"]), str(row["role"]), project.get("building_name"), project.get("building_type"), project.get("planned_handover_date")))
        return output

    def get_project(self, token: str, project_id: str) -> dict[str, Any] | None:
        rows = self._data_request("GET", f"projects?id=eq.{quote(project_id)}&select=*", token)
        return dict(rows[0]) if isinstance(rows, list) and rows else None

    def update_project(self, token: str, project_id: str, values: Mapping[str, Any]) -> None:
        self._data_request("PATCH", f"projects?id=eq.{quote(project_id)}", token, json=dict(values))

    def get_sections(self, token: str, project_id: str) -> list[dict[str, Any]]:
        rows = self._data_request("GET", f"reg38_sections?project_id=eq.{quote(project_id)}&select=id,section_key,name,enabled,applicability_status,sort_order&order=sort_order", token)
        return [dict(row) for row in rows] if isinstance(rows, list) else []

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

    def project_role(self, token: str, project_id: str) -> str | None:
        rows = self._data_request("GET", f"project_members?project_id=eq.{quote(project_id)}&select=role", token)
        return str(rows[0].get("role")) if isinstance(rows, list) and rows else None

    def require_project_admin(self, token: str, project_id: str) -> None:
        if self.project_role(token, project_id) not in {"OWNER", "ADMIN"}:
            raise SupabaseAuthError("Only a project owner or administrator can review spaces and zones.", status_code=403)

    def spatial_review(self, token: str, project_id: str) -> dict[str, Any]:
        """Return source and working spatial data separately; source tables are read-only."""
        pid = quote(project_id)
        spaces = self._data_request("GET", f"project_spaces?project_id=eq.{pid}&select=*,building_storeys(id,name,elevation),ifc_objects(ifc_entity,name,long_name,description,source_data)&order=name", token)
        zones = self._data_request("GET", f"project_zones?project_id=eq.{pid}&select=*&order=name", token)
        grids = self._data_request("GET", f"project_grids?project_id=eq.{pid}&select=*,project_grid_axes(*)&order=name", token)
        members = self._data_request("GET", f"project_zone_members?zone_id=in.({','.join(str(z['id']) for z in zones)})&select=id,zone_id,space_id,source", token) if zones else []
        return {"spaces": spaces if isinstance(spaces, list) else [], "zones": zones if isinstance(zones, list) else [],
                "grids": grids if isinstance(grids, list) else [], "members": members if isinstance(members, list) else [],
                "can_admin": self.project_role(token, project_id) in {"OWNER", "ADMIN"}}

    def update_space(self, token: str, project_id: str, space_id: str, values: Mapping[str, Any]) -> None:
        self.require_project_admin(token, project_id)
        allowed = {"space_number", "name", "description", "occupancy_type", "occupancy_capacity", "high_risk", "included_in_reg38"}
        payload = {key: values[key] for key in allowed if key in values}
        if not str(payload.get("name") or "").strip():
            raise ValueError("Space name is required.")
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
