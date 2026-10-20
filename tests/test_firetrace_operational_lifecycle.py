from ifc_app.firetrace_wizard import FIRETRACE_WIZARD_STEPS, LEGACY_REGULATION_38_STEP_ALIASES
from ifc_app.reg38_projects import Regulation38Repository


def test_setup_ends_with_scope_review_and_excludes_delivery_areas():
    assert [label for _, label in FIRETRACE_WIZARD_STEPS] == [
        "Project Details", "Project Scope", "Design Model", "Model Scan", "Spatial Review", "Fire Scope Review"
    ]
    assert not {"plans", "information-requirements", "summary"} & LEGACY_REGULATION_38_STEP_ALIASES.keys()


def test_scope_summary_does_not_require_evidence():
    summary = Regulation38Repository._fire_strategy_summary([{
        "relevance": "IN_SCOPE", "categories": ["FIRE_DOORS_SHUTTERS"], "review_status": "APPROVED",
        "evidence_required": "", "no_evidence_required": False, "orphaned": False,
    }])
    assert summary["complete"] is True
    assert "missing_evidence" not in summary


def test_scope_summary_keeps_review_required_unresolved():
    summary = Regulation38Repository._fire_strategy_summary([{
        "relevance": "REVIEW_REQUIRED", "categories": [], "review_status": "IN_PROGRESS", "orphaned": False,
    }])
    assert summary["unresolved"] == 1
    assert summary["complete"] is True  # REVIEW_REQUIRED is an explicit unresolved scope decision


def test_operational_migration_is_deterministic_and_private():
    sql = open("supabase/migrations/202609090002_firetrace_operational_lifecycle.sql", encoding="utf-8").read()
    assert "r.relevance='IN_SCOPE'" in sql
    assert "complete_firetrace_setup" in sql
    assert "setup_completed_at" in sql and "setup_completed_by" in sql
    assert "projects/{project_id}/evidence" in sql
    assert "bucket_id='project-files'" in sql
    assert "status='ACCEPTED'" in sql
