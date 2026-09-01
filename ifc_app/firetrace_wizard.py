"""Canonical route and display metadata for the FireTrace setup wizard."""

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
