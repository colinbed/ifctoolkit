from pathlib import Path
import re
from types import SimpleNamespace

from ifc_app.reg38_projects import Regulation38Repository

PROJECT = "00000000-0000-4000-8000-000000000001"
MODEL = "00000000-0000-4000-8000-000000000002"


class FireAuth:
    settings = SimpleNamespace(project_url="https://example.supabase.co")
    def __init__(self, records=None): self.calls=[]; self.records=records or []
    def _request_json(self, method, url, **kwargs):
        self.calls.append((method,url,kwargs))
        if "project_members?" in url: return [{"role":"EDITOR"}]
        if "ifc_files?" in url: return [{"id":MODEL,"status":"PROCESSED"}]
        if "ifc_processing_jobs?" in url: return [{"status":"COMPLETED"}]
        if "ifc_objects?" in url: return [{"id":"object","ifc_global_id":"door-guid","ifc_entity":"IfcDoor","name":"Door 1",
            "long_name":None,"description":None,"storey_id":"level","building_storeys":{"id":"level","name":"Level 1"},
            "ifc_object_properties":[{"property_set":"Pset_DoorCommon","property_name":"FireRating","property_value_text":"FD60"}]}]
        if "fire_strategy_reviews?" in url and method=="GET": return self.records
        if method=="POST": self.records.extend([{**row,"id":f"review-{i}","relevance":"NOT_ASSESSED","categories":[],"review_status":"NOT_STARTED"} for i,row in enumerate(kwargs["json"])]); return []
        return []


def test_fire_property_creates_reasoned_unapproved_suggestion_without_duplicates():
    auth=FireAuth(); repo=Regulation38Repository(auth)
    first=repo.fire_strategy("token",PROJECT,"user"); second=repo.fire_strategy("token",PROJECT,"user")
    assert first["ready"] and first["reviews"][0]["automatically_suggested"]
    assert "Pset_DoorCommon.FireRating" in first["reviews"][0]["suggestion_reason"]
    assert first["reviews"][0]["relevance"] == "NOT_ASSESSED"
    assert len([c for c in auth.calls if c[0]=="POST"]) == 1 and len(second["reviews"]) == 1


def test_production_schema_without_is_current_resolves_current_model_and_loads_geometry():
    """Fire Strategy must use the same newest-upload rule as the other steps."""
    older = "00000000-0000-4000-8000-000000000001"
    auth = FireAuth()
    def request(method, url, **kwargs):
        auth.calls.append((method, url, kwargs))
        if "project_members?" in url: return [{"role": "EDITOR"}]
        if "ifc_files?" in url:
            assert "is_current" not in url
            assert "order=created_at.desc,id.desc" in url
            return [{"id": MODEL, "status": "PROCESSED", "created_at": "2026-09-02"},
                    {"id": older, "status": "PROCESSED", "created_at": "2026-09-01"}]
        if "ifc_processing_jobs?" in url: return [{"status": "COMPLETED"}]
        if "project_spaces?" in url:
            return [{"id": "space", "storey_id": "level", "source_geometry": {"type": "Polygon"}}]
        if "ifc_objects?" in url or "fire_strategy_reviews?" in url: return []
        return []
    auth._request_json = request

    result = Regulation38Repository(auth).fire_strategy("token", PROJECT)

    assert result["ready"] is True
    assert result["model"]["id"] == MODEL
    assert result["spaces"][0]["source_geometry"]["type"] == "Polygon"
    assert all("is_current" not in call[1] for call in auth.calls)


def test_ifc_file_select_contract_matches_migrated_production_schema():
    source = Path("ifc_app/reg38_projects.py").read_text(encoding="utf-8")
    migration = Path("supabase/migrations/202608280002_reg38_project_foundation.sql").read_text(encoding="utf-8")
    table = re.search(r"create table if not exists public\.ifc_files \((.*?)\n\);", migration, re.S).group(1)
    migrated_columns = set(re.findall(r"(?:^|,\s*|\n\s*)([a-z][a-z0-9_]*)\s+[a-z]", table))
    selected_columns = {"id", "original_filename", "file_size", "status", "storage_path", "ifc_schema", "created_at"}
    assert selected_columns <= migrated_columns
    assert "is_current=eq" not in source


def test_completion_requires_category_and_evidence_for_in_scope_items():
    base={"automatically_suggested":True,"relevance":"IN_SCOPE","categories":[],"evidence_required":"","no_evidence_required":False}
    assert not Regulation38Repository._fire_strategy_summary([base])["complete"]
    done={**base,"categories":["FIRE_DOORS_SHUTTERS"],"no_evidence_required":True}
    assert Regulation38Repository._fire_strategy_summary([done])["complete"]


def test_bulk_patch_is_project_scoped_and_updates_only_selected_ids():
    auth=FireAuth(); Regulation38Repository(auth).update_fire_strategy("token",PROJECT,["one","two"],{"relevance":"OUT_OF_SCOPE"},"user")
    patch=next(c for c in auth.calls if c[0]=="PATCH")
    assert f"project_id=eq.{PROJECT}" in patch[1] and "id=in.(one,two)" in patch[1]
    assert patch[2]["json"]["relevance"] == "OUT_OF_SCOPE"


def test_missing_scan_blocks_page_data():
    class Missing(FireAuth):
        def _request_json(self,method,url,**kwargs):
            if "project_members?" in url:return [{"role":"VIEWER"}]
            if "ifc_files?" in url:return []
            return []
    result=Regulation38Repository(Missing()).fire_strategy("token",PROJECT)
    assert not result["ready"] and "Model Scan" in result["error"]


def test_template_replaces_placeholder_and_controls_next():
    template=open("templates/saas/reg38_fire_strategy.html",encoding="utf-8").read()
    assert "MODEL STRUCTURE" in template and "completion summary" in template
    assert "fire_strategy.summary.complete" in template and "Back to Spatial Review" in template
