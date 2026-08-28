from datetime import datetime, timedelta, timezone
from pathlib import Path

from ifc_app.entitlements import TOOL_REGISTRY, can_access_tool, effective_account_level, has_account_level, tool_for_path, trial_summary
from ifc_app.supabase_auth import _route_requirement


NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)
PREMIUM_TOOLS = {
    "ifc_to_excel", "pset_purge", "storey_global_z", "proxy_to_ifcclass", "presentation_layer",
    "file_reduction", "area_space_purge", "ifc_data_qa", "cobie_qc", "cobie_qa", "regulation_38",
}


def profile(level="standard", status="expired", ends=None):
    return {"account_level": level, "subscription_status": status, "trial_ends_at": ends}


def test_standard_expired_trial_access():
    user = profile(ends=NOW - timedelta(days=1))
    assert not any(can_access_tool(user, tool_id, NOW) for tool_id in TOOL_REGISTRY)
    assert not has_account_level(user, "premium", NOW)
    assert not has_account_level(user, "admin", NOW)


def test_complete_tool_classification():
    assert {tool_id for tool_id, tool in TOOL_REGISTRY.items() if tool["access"] == "premium"} == PREMIUM_TOOLS
    assert {tool_id for tool_id, tool in TOOL_REGISTRY.items() if tool["access"] == "admin"} == {
        "ifc_move_rotate", "step_to_ifc", "model_checking",
    }


def test_active_trial_has_premium_but_not_admin_access():
    user = profile(status="trial", ends=NOW + timedelta(days=90))
    assert effective_account_level(user, NOW) == "premium"
    assert can_access_tool(user, "cobie_qc", NOW)
    assert all(can_access_tool(user, tool_id, NOW) for tool_id, tool in TOOL_REGISTRY.items() if tool["access"] == "premium")
    assert has_account_level(user, "premium", NOW)
    assert not can_access_tool(user, "ifc_move_rotate", NOW)
    assert trial_summary(user, NOW)["days_remaining"] == 90


def test_premium_and_admin_access():
    assert all(can_access_tool(profile("premium"), tool_id, NOW) for tool_id, tool in TOOL_REGISTRY.items() if tool["access"] == "premium")
    assert not can_access_tool(profile("premium"), "step_to_ifc", NOW)
    admin = profile("admin")
    assert all(can_access_tool(admin, tool_id, NOW) for tool_id in TOOL_REGISTRY)


def test_ifc_processing_pages_and_endpoints_share_registry_entitlements():
    expected = {
        "/excel": "ifc_to_excel", "/api/session/session-id/excel/update": "ifc_to_excel",
        "/api/session/session-id/clean": "pset_purge", "/api/session/session-id/storeys/apply": "storey_global_z",
        "/api/session/session-id/proxy": "proxy_to_ifcclass",
        "/api/session/session-id/presentation-layer/purge/apply": "presentation_layer",
        "/api/ifc-tools/reduce-file-size/run": "file_reduction", "/api/ifc/area-spaces/purge": "area_space_purge",
        "/api/ifc-qa/run": "ifc_data_qa", "/api/session/session-id/data-extractor/start": "ifc_data_qa",
        "/api/session/session-id/download": "ifc_data_qa",
    }
    assert {path: tool_for_path(path) for path in expected} == expected
    assert all(TOOL_REGISTRY[tool_id]["access"] == "premium" for tool_id in expected.values())
    assert all(_route_requirement(path) == ("tool", tool_id) for path, tool_id in expected.items())


def test_admin_processing_routes_cannot_bypass_admin_entitlement():
    for path in (
        "/wip/ifc-move-rotate", "/api/session/session-id/ifc-move-rotate",
        "/step2ifc", "/api/session/session-id/step2ifc/auto",
        "/model-checking", "/api/session/session-id/checks/apply",
    ):
        assert _route_requirement(path) == ("level", "admin")


def test_new_user_migration_assigns_standard_ninety_day_trial_and_protects_fields():
    sql = Path("supabase/migrations/202608280001_account_entitlements.sql").read_text()
    assert "'standard', now(), now() + interval '90 days', 'trial'" in sql
    assert "no browser INSERT/UPDATE policy" in sql


def test_pricing_has_free_trial_and_paid_tiers_are_coming_soon():
    html = Path("templates/public/pricing.html").read_text()
    assert "£0" in html and "90-day Premium feature trial" in html
    assert html.count("Coming soon") >= 3
    assert "automatic charge" in html
