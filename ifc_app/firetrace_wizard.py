"""Canonical route, display metadata and persisted-state progression for FireTrace."""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

FIRETRACE_WIZARD_STEPS = (
    ("details", "Project Details"),
    ("scope", "Project Scope"),
    ("model", "Design Model"),
    ("model-scan", "Model Scan"),
    ("spatial", "Spatial Review"),
    ("fire-strategy", "Fire Strategy"),
    ("evidence", "Evidence"),
    ("compliance", "Compliance Review"),
    ("handover", "Handover / Export"),
)

# Slugs exposed by the pre-FireTrace Regulation 38 wizard.  These are aliases,
# not a second wizard definition: each alias points at a canonical FireTrace slug.
LEGACY_REGULATION_38_STEP_ALIASES = {
    "details": "details",
    "scope": "scope",
    "upload-ifc": "model",
    "model-scan": "model-scan",
    "spaces-zones": "spatial",
    "fire-construction": "fire-strategy",
    "plans": "evidence",
    "information-requirements": "compliance",
    "summary": "handover",
}


def firetrace_wizard_url(project_id: str, step: int) -> str:
    """Return the canonical, refresh-safe URL for a numbered wizard step."""
    bounded_step = max(1, min(step, len(FIRETRACE_WIZARD_STEPS)))
    slug = FIRETRACE_WIZARD_STEPS[bounded_step - 1][0]
    return f"/app/firetrace/projects/{project_id}/setup/{slug}"


def firetrace_wizard_step(slug: str) -> int | None:
    """Return the one-based step number for a canonical slug."""
    return next(
        (index for index, (candidate, _) in enumerate(FIRETRACE_WIZARD_STEPS, 1) if candidate == slug),
        None,
    )


@dataclass(frozen=True)
class FireTraceProgress:
    resume_step: str
    completed_steps: frozenset[str]
    available_steps: frozenset[str]
    model: Mapping[str, Any] | None = None
    job: Mapping[str, Any] | None = None

    @property
    def highest_completed_step(self) -> int:
        return max((index for index, (slug, _) in enumerate(FIRETRACE_WIZARD_STEPS, 1)
                    if slug in self.completed_steps), default=0)


def get_firetrace_resume_step(project: Mapping[str, Any], scope: Mapping[str, Any] | None,
                              model: Mapping[str, Any] | None, job: Mapping[str, Any] | None,
                              sections: Sequence[Mapping[str, Any]] = ()) -> FireTraceProgress:
    """Calculate progression from durable data; the latest job overrides file status."""
    completed: set[str] = set()
    if str(project.get("name") or "").strip() and str(project.get("project_reference") or "").strip():
        completed.add("details")
    if scope and scope.get("scope_type"):
        completed.add("scope")
    if model:
        completed.add("model")
    if model and str((job or {}).get("status") or "").upper() in {"COMPLETED", "SUCCEEDED"}:
        completed.add("model-scan")
    statuses = {str(row.get("section_key") or ""): str(row.get("completion_status") or "").upper()
                for row in sections}
    if "model-scan" in completed and statuses.get("SPATIAL_OCCUPANCY") == "COMPLETE":
        completed.add("spatial")
    if "spatial" in completed and statuses.get("FIRE_SAFETY_STRATEGY") == "COMPLETE":
        completed.add("fire-strategy")
    resume = next((slug for slug, _ in FIRETRACE_WIZARD_STEPS if slug not in completed),
                  FIRETRACE_WIZARD_STEPS[-1][0])
    available = set(completed) | {resume}
    if model and job:
        available.add("model-scan")
    return FireTraceProgress(resume, frozenset(completed), frozenset(available), model, job)
