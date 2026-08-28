from datetime import datetime, timedelta, timezone
from pathlib import Path

from ifc_app.entitlements import TOOL_REGISTRY, can_access_tool, effective_account_level, has_account_level, trial_summary


NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def profile(level="standard", status="expired", ends=None):
    return {"account_level": level, "subscription_status": status, "trial_ends_at": ends}


def test_standard_expired_trial_access():
    user = profile(ends=NOW - timedelta(days=1))
    assert can_access_tool(user, "ifc_to_excel", NOW)
    assert not can_access_tool(user, "cobie_qc", NOW)
    assert not has_account_level(user, "premium", NOW)
    assert not has_account_level(user, "admin", NOW)


def test_active_trial_has_premium_but_not_admin_access():
    user = profile(status="trial", ends=NOW + timedelta(days=90))
    assert effective_account_level(user, NOW) == "premium"
    assert can_access_tool(user, "cobie_qc", NOW)
    assert has_account_level(user, "premium", NOW)
    assert not can_access_tool(user, "ifc_move_rotate", NOW)
    assert trial_summary(user, NOW)["days_remaining"] == 90


def test_premium_and_admin_access():
    assert can_access_tool(profile("premium"), "cobie_qa", NOW)
    assert not can_access_tool(profile("premium"), "step_to_ifc", NOW)
    admin = profile("admin")
    assert all(can_access_tool(admin, tool_id, NOW) for tool_id in TOOL_REGISTRY)


def test_new_user_migration_assigns_standard_ninety_day_trial_and_protects_fields():
    sql = Path("supabase/migrations/202608280001_account_entitlements.sql").read_text()
    assert "'standard', now(), now() + interval '90 days', 'trial'" in sql
    assert "no browser INSERT/UPDATE policy" in sql


def test_pricing_has_free_trial_and_paid_tiers_are_coming_soon():
    html = Path("templates/public/pricing.html").read_text()
    assert "£0" in html and "90-day Premium feature trial" in html
    assert html.count("Coming soon") >= 3
    assert "automatic charge" in html
