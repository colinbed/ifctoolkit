from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

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
        if "ifc_object_properties?" in url: return [{"ifc_object_id":"object","property_set":"Pset_DoorCommon",
            "property_name":"FireRating","property_value_text":"FD60","source_scope":"OCCURRENCE"}]
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
    object_queries = [url for method, url, _kwargs in auth.calls if method == "GET" and "ifc_objects?" in url]
    assert object_queries and all("ifc_object_properties(" not in url and "select=*" not in url for url in object_queries)
    property_queries = [url for method, url, _kwargs in auth.calls if method == "GET" and "ifc_object_properties?" in url]
    assert property_queries and all("is_fire_relevant=eq.true" in url and "limit=500" in url for url in property_queries)


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


def test_production_scale_load_is_paged_and_only_reads_materialised_fire_properties():
    """A 2,300-object/100,000-property model returns only its 350 candidates."""
    class ProductionScale(FireAuth):
        candidates = [{"id": f"candidate-{i}", "ifc_global_id": f"guid-{i}", "ifc_entity": "IfcDoor",
                       "name": f"Door {i}", "storey_id": None} for i in range(300)]
        generic = [{"id": f"generic-{i}", "ifc_global_id": f"generic-guid-{i}", "ifc_entity": "IfcBuildingElementProxy",
                    "name": f"Equipment {i}", "storey_id": None} for i in range(50)]
        fire_properties = [{"ifc_object_id": f"generic-{i}", "property_set": "CustomFireData",
                            "property_name": "FireResistanceRating", "property_value_text": "60 min",
                            "source_scope": "OCCURRENCE"} for i in range(50)]

        def _request_json(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            query = parse_qs(urlparse(url).query)
            offset, limit = int(query.get("offset", [0])[0]), int(query.get("limit", [500])[0])
            if "project_members?" in url: return [{"role": "EDITOR"}]
            if "ifc_files?" in url: return [{"id": MODEL, "status": "PROCESSED"}]
            if "ifc_processing_jobs?" in url: return [{"status": "COMPLETED"}]
            if "ifc_object_properties?" in url:
                assert "is_fire_relevant=eq.true" in url
                return self.fire_properties[offset:offset + limit]
            if "ifc_objects?" in url:
                if "ifc_entity=in." in url: return self.candidates[offset:offset + limit]
                ids = query.get("id", [""])[0].removeprefix("in.(").removesuffix(")").split(",")
                return [row for row in self.generic if row["id"] in ids]
            if "fire_strategy_reviews?" in url and method == "GET": return self.records[offset:offset + limit]
            if method == "POST":
                self.records.extend([{**row, "id": f"review-{len(self.records) + i}", "relevance": "NOT_ASSESSED",
                                      "categories": [], "review_status": "NOT_STARTED"}
                                     for i, row in enumerate(kwargs["json"])]); return []
            return []

    auth = ProductionScale()
    result = Regulation38Repository(auth).fire_strategy("token", PROJECT, "user")

    assert result["ready"] and len(result["objects"]) == 350 and len(result["reviews"]) == 350
    assert all("ifc_object_properties(" not in url for method, url, _kwargs in auth.calls if method == "GET")
    assert max(len(kwargs.get("json", [])) for method, _url, kwargs in auth.calls if method == "POST") == 350


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
