from pathlib import Path

import ifcopenshell
import ifcopenshell.api

from backend.reg38_ifc_processor import Regulation38IfcProcessor, ScanResult, parse_fire_rating


def make_fixture(path: Path):
    model = ifcopenshell.file(schema="IFC4")
    create = lambda kind, name: ifcopenshell.api.run("root.create_entity", model, ifc_class=kind, name=name)
    project, site, building, storey = create("IfcProject", "Project"), create("IfcSite", "Site"), create("IfcBuilding", "Building"), create("IfcBuildingStorey", "Level 1")
    ifcopenshell.api.run("aggregate.assign_object", model, products=[site], relating_object=project)
    ifcopenshell.api.run("aggregate.assign_object", model, products=[building], relating_object=site)
    ifcopenshell.api.run("aggregate.assign_object", model, products=[storey], relating_object=building)
    space = create("IfcSpace", "01-127b")
    space.LongName = "INDUSTRY LEARNING CLASSROOM 01B"
    space.Description = "Teaching space"
    ifcopenshell.api.run("aggregate.assign_object", model, products=[space], relating_object=storey)
    ifcopenshell.api.run("unit.assign_unit", model)
    model_context = ifcopenshell.api.run("context.add_context", model, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", model, context_type="Model",
                                context_identifier="Body", target_view="MODEL_VIEW", parent=model_context)
    profile = ifcopenshell.api.run("profile.add_arbitrary_profile", model,
                                   profile=[(0.0, 0.0), (5.2, 0.0), (5.2, 4.1), (2.0, 3.2), (0.0, 4.1)])
    representation = ifcopenshell.api.run("geometry.add_profile_representation", model, context=body,
                                           profile=profile, depth=3.0)
    ifcopenshell.api.run("geometry.assign_representation", model, product=space, representation=representation)
    quantities = ifcopenshell.api.run("pset.add_qto", model, product=space, name="Qto_SpaceBaseQuantities")
    ifcopenshell.api.run("pset.edit_qto", model, qto=quantities, properties={"GrossFloorArea": 42.5, "NetFloorArea": 40.0, "Height": 3.0})
    zone = create("IfcZone", "Occupancy zone")
    ifcopenshell.api.run("group.assign_group", model, products=[space], group=zone)
    spatial_zone = create("IfcSpatialZone", "Fire zone")
    spatial_zone.PredefinedType = "FIRESAFETY"
    ifcopenshell.api.run("aggregate.assign_object", model, products=[spatial_zone], relating_object=storey)
    door = create("IfcDoor", "FD door")
    ifcopenshell.api.run("spatial.assign_container", model, products=[door], relating_structure=storey)
    door_profile = ifcopenshell.api.run("profile.add_arbitrary_profile", model,
                                        profile=[(1.0, 0.0), (2.0, 0.0), (2.0, .15), (1.0, .15)])
    door_representation = ifcopenshell.api.run("geometry.add_profile_representation", model, context=body,
                                                profile=door_profile, depth=2.1)
    ifcopenshell.api.run("geometry.assign_representation", model, product=door, representation=door_representation)
    common = ifcopenshell.api.run("pset.add_pset", model, product=door, name="Pset_DoorCommon")
    ifcopenshell.api.run("pset.edit_pset", model, pset=common, properties={"FireRating": "FD60S"})
    door_type = create("IfcDoorType", "Door type")
    custom = ifcopenshell.api.run("pset.add_pset", model, product=door_type, name="ManufacturerData")
    ifcopenshell.api.run("pset.edit_pset", model, pset=custom, properties={"Fire Resistance": "EI60"})
    ifcopenshell.api.run("type.assign_type", model, related_objects=[door], relating_type=door_type)
    wall = create("IfcWallStandardCase", "External wall")
    column = create("IfcColumn", "Column C1")
    ifcopenshell.api.run("spatial.assign_container", model, products=[wall, column], relating_structure=storey)
    for product, points, depth in ((wall, [(0., 0.), (5., 0.), (5., .25), (0., .25)], 3.0),
                                   (column, [(3., 2.), (3.4, 2.), (3.4, 2.4), (3., 2.4)], 3.0)):
        product_profile = ifcopenshell.api.run("profile.add_arbitrary_profile", model, profile=points)
        product_representation = ifcopenshell.api.run("geometry.add_profile_representation", model, context=body,
                                                       profile=product_profile, depth=depth)
        ifcopenshell.api.run("geometry.assign_representation", model, product=product, representation=product_representation)
    p1, p2 = model.createIfcCartesianPoint((0.0, 0.0)), model.createIfcCartesianPoint((10.0, 0.0))
    curve = model.createIfcPolyline((p1, p2))
    axis = model.createIfcGridAxis("A", curve, True)
    grid = create("IfcGrid", "Grid")
    grid.UAxes, grid.VAxes = (axis,), ()
    ifcopenshell.api.run("spatial.assign_container", model, products=[grid], relating_structure=storey)
    model.write(str(path))
    return {"space": space.GlobalId, "zone": zone.GlobalId, "spatial_zone": spatial_zone.GlobalId,
            "door": door.GlobalId, "wall": wall.GlobalId, "column": column.GlobalId}


def test_space_zone_spatial_zone_grid_and_properties(tmp_path):
    path = tmp_path / "fixture.ifc"
    ids = make_fixture(path)
    result = Regulation38IfcProcessor().process(path, project_id="project", ifc_file_id="file")
    space = result.tables["project_spaces"][0]
    assert space["ifc_global_id"] == ids["space"]
    assert space["space_number"] == "01-127b"
    assert space["name"] == "INDUSTRY LEARNING CLASSROOM 01B"
    assert space["description"] == "Teaching space"
    assert (space["gross_area"], space["net_area"], space["height"]) == (42.5, 40.0, 3.0)
    geometry = space["source_geometry"]
    assert geometry["type"] == "Polygon"
    assert len(geometry["coordinates"]) >= 4
    assert geometry["coordinates"][0] == geometry["coordinates"][-1]
    assert geometry["centroid"] == {"x": geometry["centroid"]["x"], "y": geometry["centroid"]["y"], "z": 0.0}
    assert geometry["coordinate_system"] == "storey-local"
    assert geometry["geometry_method"] == "DIRECT_REPRESENTATION" and geometry["confidence"] == "HIGH"
    assert "IfcExtrudedAreaSolid" in geometry["representation_types"]
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
    assert result.statistics["spaces_with_plan_geometry"] == 1
    assert result.statistics["spaces_without_plan_geometry"] == 0
    assert result.statistics["ifc_zones"] == 1
    assert result.statistics["fire_safety_spatial_zones"] == 1
    assert result.statistics["grid_axes"] == 1
    assert result.statistics["doors_with_detected_fire_rating"] == 1


def test_relevant_objects_have_simplified_storey_local_plan_footprints(tmp_path):
    path = tmp_path / "plan-objects.ifc"
    ids = make_fixture(path)

    result = Regulation38IfcProcessor().process(path, project_id="project", ifc_file_id="file")
    objects = {row["ifc_global_id"]: row for row in result.tables["ifc_objects"]}
    geometry = {row["ifc_object_id"]: row for row in result.tables["ifc_object_plan_geometry"]}

    for kind in ("wall", "door", "column"):
        row = geometry[objects[ids[kind]]["id"]]
        assert row["geometry_type"] == "Polygon"
        assert row["geometry"]["coordinate_system"] == "storey-local"
        assert row["geometry"]["coordinates"][0] == row["geometry"]["coordinates"][-1]
        assert len(row["geometry"]["coordinates"]) <= 6
    assert result.statistics["walls_with_plan_geometry"] == 1
    assert result.statistics["doors_with_plan_geometry"] == 1
    assert result.statistics["columns_with_plan_geometry"] == 1


def test_multiple_fire_properties_on_one_object_have_unique_stable_identities(tmp_path):
    path = tmp_path / "multiple-fire-properties.ifc"
    make_fixture(path)
    model = ifcopenshell.open(str(path))
    door = model.by_type("IfcDoor")[0]
    extra = ifcopenshell.api.run("pset.add_pset", model, product=door, name="AssetFireData")
    ifcopenshell.api.run("pset.edit_pset", model, pset=extra,
                        properties={"Fire Rating": "FD60S", "Smoke Rating": "EI30"})
    model.write(str(path))

    processor = Regulation38IfcProcessor()
    first = processor.process(path, project_id="project", ifc_file_id="file").tables["fire_requirements"]
    second = processor.process(path, project_id="project", ifc_file_id="file").tables["fire_requirements"]

    assert len(first) >= 4
    assert len({row["id"] for row in first}) == len(first)
    assert len({row["source_finding_key"] for row in first}) == len(first)
    assert [row["id"] for row in first] == [row["id"] for row in second]


def test_identical_logical_fire_findings_are_deduplicated():
    result = ScanResult()
    result.tables["ifc_objects"] = [{"id": "object", "ifc_entity": "IfcDoor"}]
    prop = {"source_scope": "OCCURRENCE", "property_set": "Custom",
            "property_name": "Fire Rating", "property_value_text": "EI60"}
    Regulation38IfcProcessor()._fire(result, "project", "file", {"object": [prop, dict(prop)]})
    assert len(result.tables["fire_requirements"]) == 1


def test_space_without_representation_is_explicitly_unavailable(tmp_path):
    path = tmp_path / "no-geometry.ifc"
    make_fixture(path)
    model = ifcopenshell.open(str(path))
    model.by_type("IfcSpace")[0].Representation = None
    model.write(str(path))
    result = Regulation38IfcProcessor().process(path, project_id="project", ifc_file_id="file")
    geometry = result.tables["project_spaces"][0]["source_geometry"]
    assert geometry["type"] == "Unavailable" and geometry["reason"] == "NO_REPRESENTATION"
    assert geometry["boundary_count"] == 0 and geometry["direct_representation"] is False
    assert result.statistics["spaces_without_plan_geometry"] == 1
    assert result.statistics["space_geometry_failure_reasons"] == {"NO_REPRESENTATION": 1}


def test_space_boundary_geometry_recovers_closed_polygon_deterministically(tmp_path):
    path = tmp_path / "boundary.ifc"
    make_fixture(path)
    model = ifcopenshell.open(str(path)); space = model.by_type("IfcSpace")[0]
    space.Representation = None
    boundary_element = model.create_entity("IfcVirtualElement", GlobalId=ifcopenshell.guid.new(), Name="Virtual room boundary")
    corners = ((0., 0.), (5., 0.), (5., 4.), (0., 4.))
    for index, (a, b) in enumerate(zip(corners, corners[1:] + corners[:1])):
        curve = model.createIfcPolyline((model.createIfcCartesianPoint(a), model.createIfcCartesianPoint(b)))
        connection = model.createIfcConnectionCurveGeometry(curve, None)
        model.create_entity("IfcRelSpaceBoundary", GlobalId=ifcopenshell.guid.new(), Name=f"Boundary {index}",
                            RelatingSpace=space, RelatedBuildingElement=boundary_element, ConnectionGeometry=connection,
                            PhysicalOrVirtualBoundary="VIRTUAL", InternalOrExternalBoundary="INTERNAL")
    model.write(str(path))
    processor = Regulation38IfcProcessor()
    first = processor.process(path, project_id="project", ifc_file_id="file")
    second = processor.process(path, project_id="project", ifc_file_id="file")
    geometry = first.tables["project_spaces"][0]["source_geometry"]
    assert geometry["type"] == "Polygon" and geometry["coordinates"][0] == geometry["coordinates"][-1]
    assert geometry["geometry_method"] == "SPACE_BOUNDARY" and geometry["confidence"] == "HIGH"
    assert geometry == second.tables["project_spaces"][0]["source_geometry"]
    assert first.statistics["spaces_boundary_derived"] == 1


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
