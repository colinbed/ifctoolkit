from types import SimpleNamespace

import pytest

from ifc_app.reg38_projects import Regulation38Repository
from ifc_app.supabase_auth import SupabaseAuthError


PROJECT = "00000000-0000-4000-8000-000000000001"
SPACE_1 = "00000000-0000-4000-8000-000000000002"
SPACE_2 = "00000000-0000-4000-8000-000000000003"
ZONE = "00000000-0000-4000-8000-000000000004"


class SpatialAuth:
    settings = SimpleNamespace(project_url="https://example.supabase.co")

    def __init__(self, role="ADMIN", existing=None):
        self.role, self.existing, self.calls = role, existing or [], []

    def _request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if "project_members?" in url: return [{"role": self.role}]
        if "project_spaces?" in url and "select=id,building_id,storey_id" in url:
            return [{"id": SPACE_1, "building_id": "building", "storey_id": "storey"}, {"id": SPACE_2, "building_id": "building", "storey_id": "storey"}]
        if "project_spaces?" in url and "select=id" in url:
            return [{"id": sid} for sid in (SPACE_1, SPACE_2) if sid in url]
        if "project_zone_members?" in url and method == "GET": return self.existing
        return []


def test_renaming_working_space_never_writes_ifc_source():
    auth = SpatialAuth()
    Regulation38Repository(auth).update_space("token", PROJECT, SPACE_1, {"name": "Plant room", "occupancy_capacity": "2"})
    writes = [call for call in auth.calls if call[0] in {"PATCH", "POST", "DELETE"}]
    assert len(writes) == 1 and "/project_spaces?" in writes[0][1]
    assert writes[0][2]["json"] == {"name": "Plant room", "occupancy_capacity": 2, "working_fields_edited": True}
    assert all("ifc_objects" not in call[1] and "ifc_object_properties" not in call[1] for call in writes)


def test_manual_boundary_is_written_only_as_working_geometry():
    auth = SpatialAuth()
    ring = [[0, 0], [4, 0], [4, 3], [0, 0]]
    Regulation38Repository(auth).update_space("token", PROJECT, SPACE_1,
                                               {"name": "Plant", "working_geometry": {"type": "Polygon", "coordinates": ring}})
    payload = next(call[2]["json"] for call in auth.calls if call[0] == "PATCH")
    assert "source_geometry" not in payload
    assert payload["working_geometry"]["geometry_method"] == "MANUAL"
    assert payload["working_geometry"]["confidence"] == "MANUAL"


def test_manual_zone_creation_inserts_zone_and_members():
    auth = SpatialAuth()
    Regulation38Repository(auth).create_zone("token", PROJECT, "Compartment A", "FIRE_COMPARTMENT", [SPACE_1, SPACE_2])
    posts = [call for call in auth.calls if call[0] == "POST"]
    assert posts[0][1].endswith("/project_zones")
    assert posts[0][2]["json"]["source_kind"] == "MANUAL"
    assert posts[1][1].endswith("/project_zone_members")
    assert {row["space_id"] for row in posts[1][2]["json"]} == {SPACE_1, SPACE_2}


def test_ifc_zone_membership_is_returned_with_provenance():
    auth = SpatialAuth(existing=[{"id": "member", "zone_id": ZONE, "space_id": SPACE_1, "source": "IFC_GROUP_ASSIGNMENT"}])
    # Simulate the zone read so spatial_review performs the membership request.
    original = auth._request_json
    def request(method, url, **kwargs):
        if "project_zones?" in url: auth.calls.append((method, url, kwargs)); return [{"id": ZONE}]
        return original(method, url, **kwargs)
    auth._request_json = request
    result = Regulation38Repository(auth).spatial_review("token", PROJECT)
    assert result["members"][0]["source"] == "IFC_GROUP_ASSIGNMENT"


def test_zone_member_edits_remove_and_add_without_touching_source_tables():
    auth = SpatialAuth(existing=[{"space_id": SPACE_1}])
    Regulation38Repository(auth).update_zone("token", PROJECT, ZONE, "Zone A", "ALARM_ZONE", [SPACE_2])
    methods = [(call[0], call[1]) for call in auth.calls]
    assert any(method == "DELETE" and "project_zone_members" in url for method, url in methods)
    assert any(method == "POST" and url.endswith("/project_zone_members") for method, url in methods)
    assert not any("ifc_objects" in url for _, url in methods)


@pytest.mark.parametrize("role", ["EDITOR", "VIEWER", None])
def test_only_project_admins_can_write_spatial_review(role):
    auth = SpatialAuth(role=role)
    with pytest.raises(SupabaseAuthError) as exc:
        Regulation38Repository(auth).update_space("token", PROJECT, SPACE_1, {"name": "Changed"})
    assert exc.value.status_code == 403
    assert not any(call[0] == "PATCH" for call in auth.calls)


def test_storey_plan_is_lightweight_and_scoped_to_project_and_storey():
    class PlanAuth(SpatialAuth):
        def _request_json(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if "project_members?" in url: return [{"role": "VIEWER"}]
            if "project_spaces?" in url:
                return [{"id": SPACE_1, "storey_id": "storey", "space_number": "01-101",
                         "name": "Breakout", "source_geometry": {"type": "Polygon", "coordinates": [[0, 0], [5, 0], [5, 4], [0, 0]]}}]
            if "ifc_object_plan_geometry?" in url:
                return [{"ifc_object_id": "wall", "geometry_type": "Polygon",
                         "geometry": {"type": "Polygon", "coordinates": [[0, 0], [5, 0], [5, .2], [0, 0]]},
                         "centroid_x": 2.5, "centroid_y": .1,
                         "ifc_objects": {"ifc_global_id": "wall-guid", "ifc_entity": "IfcWallStandardCase", "name": "Wall"}}]
            return []
    auth = PlanAuth()
    plan = Regulation38Repository(auth).spatial_storey_plan("token", PROJECT, "storey")
    assert plan["geometry_status"] == "available" and plan["spaces"][0]["id"] == SPACE_1
    assert plan["objects"][0]["id"] == "wall"
    assert plan["objects"][0]["ifc_entity"] == "IfcWallStandardCase"
    assert plan["objects"][0]["geometry"]["type"] == "Polygon"
    request_url = next(url for method, url, _ in auth.calls if "project_spaces?" in url)
    assert f"project_id=eq.{PROJECT}" in request_url and "storey_id=eq.storey" in request_url
    assert "source_geometry" in request_url and "ifc_objects" not in request_url
    object_url = next(url for method, url, _ in auth.calls if "ifc_object_plan_geometry?" in url)
    assert "project_id=eq." in object_url and "storey_id=eq.storey" in object_url


def test_storey_plan_reports_explicit_missing_geometry_state():
    class EmptyPlanAuth(SpatialAuth):
        def _request_json(self, method, url, **kwargs):
            if "project_members?" in url: return [{"role": "VIEWER"}]
            if "project_spaces?" in url: return [{"id": SPACE_1, "source_geometry": {"coordinates": None}}]
            return []
    assert Regulation38Repository(EmptyPlanAuth()).spatial_storey_plan("token", PROJECT, "storey")["geometry_status"] == "unavailable"


@pytest.mark.parametrize("geometry, expected", [(None, True), ({"type": "Polygon", "coordinates": None}, False),
                                       ({"type": "Centroid", "coordinates": [1, 2]}, False),
                                       ({"type": "Unavailable", "reason": "NO_REPRESENTATION"}, False),
                                       ({"type": "Polygon", "coordinates": [], "reason": "BACKFILL_REQUIRED"}, True)])
def test_spatial_review_only_recommends_backfill_when_rescan_can_help(geometry, expected):
    class MissingGeometryAuth(SpatialAuth):
        def _request_json(self, method, url, **kwargs):
            if "project_spaces?" in url:
                return [{"id": SPACE_1, "source_geometry": geometry}]
            return super()._request_json(method, url, **kwargs)
    assert Regulation38Repository(MissingGeometryAuth()).spatial_review("token", PROJECT)["geometry_backfill_required"] is expected


def test_storey_plan_denies_non_member_before_geometry_query():
    auth = SpatialAuth(role=None)
    with pytest.raises(SupabaseAuthError) as exc:
        Regulation38Repository(auth).spatial_storey_plan("token", PROJECT, "storey")
    assert exc.value.status_code == 403
    assert not any("source_geometry" in url for _, url, _ in auth.calls)


def test_spatial_template_has_fixed_workspace_panes_and_preview_states():
    template = open("templates/saas/reg38_spatial_review.html", encoding="utf-8").read()
    script = open("static/reg38-spatial.js", encoding="utf-8").read()
    css = open("static/saas.css", encoding="utf-8").read()
    for pane in ('class="structure-panel"', 'class="plan-panel"', 'class="details-panel"'):
        assert pane in template
    assert "Preview unavailable" in script and "selected" in script
    assert "Re-run Model Scan" not in template and "create working geometry" in template
    assert "height:clamp(550px,calc(100vh - 330px),720px)" in css and "overflow-y:auto" in css
