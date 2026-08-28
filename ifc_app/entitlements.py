"""Central account and tool entitlements for authenticated IFC Toolkit users."""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any, Mapping

ACCOUNT_LEVELS = ("standard", "premium", "admin")

# This registry is the single source of truth used by route enforcement and cards.
TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "ifc_to_excel": {"name": "IFC to Excel", "path": "/excel", "access": "premium", "description": "Extract, edit and round-trip IFC data with Excel.", "route_patterns": (r"/excel(?:/.*)?", r"/api/excel(?:/.*)?", r"/api/session/[^/]+/excel(?:/.*)?", r"/api/jobs/[^/]+")},
    "pset_purge": {"name": "Pset Purge", "path": "/cleaner", "access": "premium", "description": "Remove unwanted property sets and loose properties.", "route_patterns": (r"/cleaner", r"/api/session/[^/]+/clean")},
    "storey_global_z": {"name": "Storey & Global Z Control", "path": "/storeys", "access": "premium", "description": "Review and update storeys, levels and elevations.", "route_patterns": (r"/(?:storeys|levels)", r"/api/session/[^/]+/(?:storeys|levels)(?:/.*)?")},
    "proxy_to_ifcclass": {"name": "Proxy to IFCClass", "path": "/proxy", "access": "premium", "description": "Remap proxy objects into appropriate IFC classes.", "route_patterns": (r"/proxy", r"/api/session/[^/]+/proxy(?:/.*)?")},
    "presentation_layer": {"name": "Presentation Layer Alignment", "path": "/presentation-layer", "access": "premium", "description": "Review and align model presentation layers.", "route_patterns": (r"/presentation-layer", r"/api/presentation-layers(?:/.*)?", r"/api/session/[^/]+/presentation-layer(?:/.*)?")},
    "file_reduction": {"name": "IFC File Size Reducer", "path": "/tools/reduce-file-size", "access": "premium", "description": "Analyse and reduce IFC model file size.", "route_patterns": (r"/tools/reduce-file-size", r"/api/ifc-tools/reduce-file-size(?:/.*)?")},
    "area_space_purge": {"name": "Purge Area Spaces", "path": "/tools/purge-area-spaces", "access": "premium", "description": "Find and remove unwanted area space entities.", "route_patterns": (r"/tools/purge-area-spaces", r"/api/ifc/area-spaces(?:/.*)?")},
    "ifc_data_qa": {"name": "IFC Data QA", "path": "/ifc-qa/extractor", "access": "premium", "description": "Extract and validate structured IFC information.", "route_patterns": (r"/(?:ifc-qa|data-extractor)(?:/.*)?", r"/api/(?:ifc-qa|ifc-data-qa)(?:/.*)?", r"/api/session/[^/]+/(?:ifc-qa|data-extractor)(?:/.*)?", r"/api/session/[^/]+/download", r"/api/ifc/jobs(?:/.*)?", r"/api/extract")},
    "cobie_qc": {"name": "COBie QC", "path": "/tools/cobieqc", "access": "premium", "description": "Run detailed COBie quality and completeness checks.", "route_patterns": (r"/tools/cobieqc", r"/api/tools/cobieqc(?:/.*)?")},
    "cobie_qa": {"name": "COBie QA / QC", "path": "/tools/cobie-qa", "access": "premium", "description": "Validate workbooks and export issue reports.", "route_patterns": (r"/tools/cobie-qa", r"/api/cobie(?:/.*)?")},
    "regulation_38": {"name": "Regulation 38", "path": "/app/regulation-38", "access": "premium", "description": "Prepare structured fire-safety handover information.", "route_patterns": (r"/app/regulation-38", r"/app/projects/[^/]+/regulation-38")},
    "ifc_move_rotate": {"name": "IFC Move / Rotate", "path": "/wip/ifc-move-rotate", "access": "admin", "description": "Internal coordinate transformation workflow."},
    "step_to_ifc": {"name": "STEP to IFC", "path": "/step2ifc", "access": "admin", "description": "Internal STEP conversion workflow."},
    "model_checking": {"name": "Model Checking", "path": "/model-checking", "access": "admin", "description": "Internal model checking configuration."},
}


def tool_for_path(path: str) -> str | None:
    """Return the registry tool owning a page or processing endpoint."""
    for tool_id, tool in TOOL_REGISTRY.items():
        if any(re.fullmatch(pattern, path) for pattern in tool.get("route_patterns", ())):
            return tool_id
    return None


def _date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif value:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def account_level(profile: Mapping[str, Any] | None) -> str:
    value = str((profile or {}).get("account_level") or "standard").lower()
    return value if value in ACCOUNT_LEVELS else "standard"


def trial_is_active(profile: Mapping[str, Any] | None, now: datetime | None = None) -> bool:
    end = _date((profile or {}).get("trial_ends_at"))
    return str((profile or {}).get("subscription_status") or "").lower() == "trial" and bool(end and end > (now or datetime.now(timezone.utc)))


def effective_account_level(profile: Mapping[str, Any] | None, now: datetime | None = None) -> str:
    level = account_level(profile)
    if level == "admin":
        return "admin"
    if level == "premium" or trial_is_active(profile, now):
        return "premium"
    return "standard"


def has_account_level(profile: Mapping[str, Any] | None, required: str, now: datetime | None = None) -> bool:
    rank = {"standard": 1, "premium": 2, "admin": 3}
    return rank[effective_account_level(profile, now)] >= rank.get(required, 99)


def can_access_tool(profile: Mapping[str, Any] | None, tool_id: str, now: datetime | None = None) -> bool:
    tool = TOOL_REGISTRY.get(tool_id)
    return bool(tool and tool["access"] != "hidden" and has_account_level(profile, tool["access"], now))


def trial_summary(profile: Mapping[str, Any] | None, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    end = _date((profile or {}).get("trial_ends_at"))
    active = trial_is_active(profile, current)
    return {"active": active, "ends_at": end, "days_remaining": max(0, math.ceil((end - current).total_seconds() / 86400)) if active and end else 0,
            "label": "Premium trial active" if active else ("Premium trial expired" if end else "No trial recorded")}
