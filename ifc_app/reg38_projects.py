"""Typed server-side helpers for Regulation 38 project persistence.

The helpers call Supabase REST/RPC with the user's access token so database RLS,
not application-side filtering, remains the authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Mapping

from ifc_app.supabase_auth import SupabaseAuthError, SupabaseAuthService

REG38_DEFAULT_SECTIONS: tuple[tuple[str, str], ...] = (
    ("PROJECT_BUILDING_INFORMATION", "Project & Building Information"),
    ("FIRE_SAFETY_STRATEGY", "Fire Safety Strategy"),
    ("SPATIAL_OCCUPANCY", "Spatial & Occupancy"),
    ("ESCAPE_EVACUATION", "Escape & Evacuation"),
    ("COMPARTMENTATION", "Compartmentation"),
    ("FIRE_DOORS_OPENINGS", "Fire Doors & Openings"),
    ("FIRE_STOPPING_PENETRATIONS", "Fire Stopping / Penetrations"),
    ("DETECTION_ALARM", "Detection & Alarm"),
    ("EMERGENCY_LIGHTING_SIGNAGE", "Emergency Lighting & Signage"),
    ("SUPPRESSION_FIREFIGHTING", "Suppression & Firefighting"),
    ("SMOKE_CONTROL", "Smoke Control"),
    ("ELECTRICAL_CRITICAL_SYSTEMS", "Electrical / Critical Systems"),
    ("FIRE_RESCUE_FACILITIES", "Fire & Rescue Facilities"),
    ("SPECIFICATIONS_OM", "Specifications & O&M"),
    ("TESTING_COMMISSIONING", "Testing & Commissioning"),
    ("DRAWINGS_MODELS", "Drawings & Models"),
    ("HANDOVER", "Handover"),
)


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
    planned_handover_date: date | None = None
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
        payload = asdict(self)
        payload["name"] = self.name.strip()
        if self.planned_handover_date:
            payload["planned_handover_date"] = self.planned_handover_date.isoformat()
        return payload


@dataclass(frozen=True)
class ProjectSummary:
    id: str
    name: str
    project_reference: str | None
    project_status: str
    role: str


class Regulation38Repository:
    def __init__(self, auth: SupabaseAuthService | None = None):
        self.auth = auth or SupabaseAuthService()

    def _data_request(self, method: str, path: str, access_token: str, **kwargs: Any) -> Any:
        return self.auth._request_json(  # centralised authenticated REST/error handling
            method,
            f"{self.auth.settings.project_url}/rest/v1/{path}",
            access_token=access_token,
            public_error="The Regulation 38 project service is temporarily unavailable.",
            **kwargs,
        )

    def create_project(self, access_token: str, project: ProjectCreate) -> str:
        result = self._data_request("POST", "rpc/create_reg38_project", access_token, json={"project_data": project.payload()})
        project_id = result if isinstance(result, str) else None
        if not project_id:
            raise SupabaseAuthError("The project could not be created.", status_code=502)
        return project_id

    def list_projects(self, access_token: str) -> list[ProjectSummary]:
        rows = self._data_request(
            "GET", "project_members?select=role,projects(id,name,project_reference,project_status)&order=created_at.desc", access_token
        )
        summaries: list[ProjectSummary] = []
        for row in rows if isinstance(rows, list) else []:
            project = row.get("projects") if isinstance(row, Mapping) else None
            if not isinstance(project, Mapping):
                continue
            summaries.append(ProjectSummary(str(project["id"]), str(project["name"]), project.get("project_reference"), str(project["project_status"]), str(row["role"])))
        return summaries
