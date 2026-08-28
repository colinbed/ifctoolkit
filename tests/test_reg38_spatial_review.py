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
    assert writes[0][2]["json"] == {"name": "Plant room", "occupancy_capacity": 2}
    assert all("ifc_objects" not in call[1] and "ifc_object_properties" not in call[1] for call in writes)


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
