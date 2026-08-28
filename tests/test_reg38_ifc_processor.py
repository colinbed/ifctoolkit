from pathlib import Path

import ifcopenshell
import ifcopenshell.api

from backend.reg38_ifc_processor import Regulation38IfcProcessor, parse_fire_rating


def make_fixture(path: Path):
    model = ifcopenshell.file(schema="IFC4")
    create = lambda kind, name: ifcopenshell.api.run("root.create_entity", model, ifc_class=kind, name=name)
    project, site, building, storey = create("IfcProject", "Project"), create("IfcSite", "Site"), create("IfcBuilding", "Building"), create("IfcBuildingStorey", "Level 1")
    ifcopenshell.api.run("aggregate.assign_object", model, products=[site], relating_object=project)
    ifcopenshell.api.run("aggregate.assign_object", model, products=[building], relating_object=site)
    ifcopenshell.api.run("aggregate.assign_object", model, products=[storey], relating_object=building)
    space = create("IfcSpace", "Room 101")
    ifcopenshell.api.run("aggregate.assign_object", model, products=[space], relating_object=storey)
    quantities = ifcopenshell.api.run("pset.add_qto", model, product=space, name="Qto_SpaceBaseQuantities")
    ifcopenshell.api.run("pset.edit_qto", model, qto=quantities, properties={"GrossFloorArea": 42.5, "NetFloorArea": 40.0, "Height": 3.0})
    zone = create("IfcZone", "Occupancy zone")
    ifcopenshell.api.run("group.assign_group", model, products=[space], group=zone)
    spatial_zone = create("IfcSpatialZone", "Fire zone")
    spatial_zone.PredefinedType = "FIRESAFETY"
    ifcopenshell.api.run("aggregate.assign_object", model, products=[spatial_zone], relating_object=storey)
    door = create("IfcDoor", "FD door")
    ifcopenshell.api.run("spatial.assign_container", model, products=[door], relating_structure=storey)
    common = ifcopenshell.api.run("pset.add_pset", model, product=door, name="Pset_DoorCommon")
    ifcopenshell.api.run("pset.edit_pset", model, pset=common, properties={"FireRating": "FD60S"})
    door_type = create("IfcDoorType", "Door type")
    custom = ifcopenshell.api.run("pset.add_pset", model, product=door_type, name="ManufacturerData")
    ifcopenshell.api.run("pset.edit_pset", model, pset=custom, properties={"Fire Resistance": "EI60"})
    ifcopenshell.api.run("type.assign_type", model, related_objects=[door], relating_type=door_type)
    p1, p2 = model.createIfcCartesianPoint((0.0, 0.0)), model.createIfcCartesianPoint((10.0, 0.0))
    curve = model.createIfcPolyline((p1, p2))
    axis = model.createIfcGridAxis("A", curve, True)
    grid = create("IfcGrid", "Grid")
    grid.UAxes, grid.VAxes = (axis,), ()
    ifcopenshell.api.run("spatial.assign_container", model, products=[grid], relating_structure=storey)
    model.write(str(path))
    return {"space": space.GlobalId, "zone": zone.GlobalId, "spatial_zone": spatial_zone.GlobalId, "door": door.GlobalId}


def test_space_zone_spatial_zone_grid_and_properties(tmp_path):
    path = tmp_path / "fixture.ifc"
    ids = make_fixture(path)
    result = Regulation38IfcProcessor().process(path, project_id="project", ifc_file_id="file")
    space = result.tables["project_spaces"][0]
    assert space["ifc_global_id"] == ids["space"]
    assert (space["gross_area"], space["net_area"], space["height"]) == (42.5, 40.0, 3.0)
    zones = {row["ifc_global_id"]: row for row in result.tables["project_zones"]}
    assert zones[ids["zone"]]["zone_type"] == "USER_DEFINED"
    assert zones[ids["spatial_zone"]]["zone_type"] == "FIRE_COMPARTMENT"
    assert zones[ids["spatial_zone"]]["source_predefined_type"] == "FIRESAFETY"
    assert len(result.tables["project_zone_members"]) == 1
    assert result.tables["project_grid_axes"][0]["axis_tag"] == "A"
    scopes = {row["source_scope"] for row in result.tables["ifc_object_properties"]}
    assert {"OCCURRENCE", "TYPE", "QUANTITY", "ATTRIBUTE"} <= scopes


def test_standard_and_custom_fire_discovery_and_statistics(tmp_path):
    path = tmp_path / "fixture.ifc"
    make_fixture(path)
    result = Regulation38IfcProcessor().process(path, project_id="project", ifc_file_id="file")
    findings = result.tables["fire_requirements"]
    assert any(r["source_type"] == "STANDARD_IFC_PROPERTY" and r["required_minutes"] == 60 and r["smoke_indication"] for r in findings)
    assert any(r["source_type"] == "KNOWN_CUSTOM_PROPERTY" and r["required_minutes"] == 60 for r in findings)
    assert result.statistics["spaces"] == 1
    assert result.statistics["ifc_zones"] == 1
    assert result.statistics["fire_safety_spatial_zones"] == 1
    assert result.statistics["grid_axes"] == 1
    assert result.statistics["doors_with_detected_fire_rating"] == 1


def test_rating_parser_accepts_safe_forms_and_rejects_missing_or_ambiguous():
    assert parse_fire_rating("FD60S") == (60, True)
    assert parse_fire_rating("EI60") == (60, False)
    assert parse_fire_rating("REI120") == (120, False)
    assert parse_fire_rating(None) == (None, False)
    assert parse_fire_rating("30 or 60 depending on location") == (None, False)


def test_occurrence_type_conflict_is_not_silently_resolved(tmp_path):
    path = tmp_path / "conflict.ifc"
    make_fixture(path)
    model = ifcopenshell.open(str(path)); door = model.by_type("IfcDoor")[0]; door_type = model.by_type("IfcDoorType")[0]
    type_pset = ifcopenshell.api.run("pset.add_pset", model, product=door_type, name="Pset_DoorCommon")
    ifcopenshell.api.run("pset.edit_pset", model, pset=type_pset, properties={"FireRating": "FD30"})
    model.write(str(path))
    result = Regulation38IfcProcessor().process(path, project_id="project", ifc_file_id="file")
    assert any(r["review_status"] == "CONFLICT" for r in result.tables["fire_requirements"])
    assert result.statistics["conflict_count"] == 1
