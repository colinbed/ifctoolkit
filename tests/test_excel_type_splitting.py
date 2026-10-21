import hashlib

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element as util
import pytest
from ifcopenshell.guid import new as new_guid

from app import ExcelWritebackValidationError, extract_to_excel, update_ifc_from_excel
from ifc_protocol import default_protocol_config
from test_excel_name_resolution import edit, exported


def update(paths, **kwargs):
    source, workbook, output, _, _ = paths
    return update_ifc_from_excel(str(source), str(workbook), str(output), split_shared_types=True, **kwargs)


@pytest.mark.parametrize("sheets", [("Elements",), ("ProtocolData",), ("Elements", "ProtocolData")])
def test_split_two_names_preserves_occurrences_and_is_repeatable(exported, tmp_path, sheets):
    source, workbook, output, guids, original_guid = exported
    edit(workbook, [(sheet, guid, "Type Name" if sheet == "Elements" else "Type Name", name)
                    for sheet in sheets for guid, name in zip(guids, ["Beam022", "Beam023"])])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    result = update(exported, add_new="no")
    model = ifcopenshell.open(str(output))
    assert len(model.by_type("IfcBeamType")) == 3
    groups = [util.get_type(model.by_guid(guid)) for guid in guids]
    assert [group.Name for group in groups] == ["Beam022", "Beam023"]
    assert len({group.GlobalId for group in groups}) == 2
    assert all(group.GlobalId != original_guid for group in groups)
    assert model.by_guid(original_guid).Name == "PB-2"
    before = ifcopenshell.open(str(source))
    for guid in guids:
        # Native occurrence attributes, references, and geometry are untouched.
        assert str(before.by_guid(guid)) == str(model.by_guid(guid))
        assert util.get_material(before.by_guid(guid)).Name == util.get_material(model.by_guid(guid)).Name
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    created = result["changes"]["created_types"]
    assert {item["source_guid"] for item in created} == {original_guid}
    assert {item["name"] for item in created} == {"Beam022", "Beam023"}
    assert all(item["reassigned_occurrence_count"] == 1 for item in created)
    second = tmp_path / "again.ifc"
    again = update_ifc_from_excel(str(output), str(workbook), str(second), split_shared_types=True)
    reopened = ifcopenshell.open(str(second))
    assert len(reopened.by_type("IfcBeamType")) == 3
    assert again["changes"]["created_types"] == []
    assert again["changes"]["applied"] == 0
    assert [util.get_type(reopened.by_guid(g)).GlobalId for g in guids] == [t.GlobalId for t in groups]


def test_unedited_occurrence_keeps_original_type(exported):
    source, workbook, output, guids, original_guid = exported
    edit(workbook, [("Elements", guids[0], "Type Name", "Renamed")])
    update(exported)
    model = ifcopenshell.open(str(output))
    assert util.get_type(model.by_guid(guids[0])).Name == "Renamed"
    assert util.get_type(model.by_guid(guids[1])).GlobalId == original_guid
    assert model.by_guid(original_guid).Name == "PB-2"
    assert len(model.by_type("IfcBeamType")) == 2


def test_consistent_rename_uses_existing_type(exported):
    source, workbook, output, guids, original_guid = exported
    edit(workbook, [("Elements", guid, "Type Name", "Renamed") for guid in guids])
    result = update(exported)
    model = ifcopenshell.open(str(output))
    assert len(model.by_type("IfcBeamType")) == 1
    assert model.by_guid(original_guid).Name == "Renamed"
    assert result["changes"]["created_types"] == []


def test_same_occurrence_contradiction_still_fails(exported):
    source, workbook, output, guids, original_guid = exported
    edit(workbook, [("Elements", guids[0], "Type Name", "ONE"),
                    ("ProtocolData", guids[0], "Type Name", "TWO")])
    with pytest.raises(ExcelWritebackValidationError) as caught:
        update(exported)
    assert len(caught.value.findings["errors"]) == 1
    error = caught.value.findings["errors"][0]
    assert error["target_guid"] == original_guid
    assert {row["global_id"] for group in error["requested_values"] for row in group["affected"]} == {guids[0]}
    assert not output.exists()


def test_clone_preserves_type_data_and_isolates_group_properties(exported):
    source, workbook, output, guids, original_guid = exported
    model = ifcopenshell.open(str(source))
    original = model.by_guid(original_guid)
    original.Description = "Original description"
    original.Tag = "Type tag"
    model.by_guid(guids[0]).ObjectType = "Preserve occurrence ObjectType"
    model.by_guid(guids[0]).PredefinedType = "USERDEFINED"
    pset = ifcopenshell.api.run("pset.add_pset", model, product=original, name="Pset_Group")
    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"Code": "Initial"})
    material = ifcopenshell.api.run("material.add_material", model, name="Type Steel")
    ifcopenshell.api.run("material.assign_material", model, products=[original], material=material)
    classification = model.create_entity("IfcClassification", Name="Test classification")
    model.create_entity("IfcRelAssociatesClassification", GlobalId=new_guid(), RelatedObjects=[original], RelatingClassification=classification)
    shape = model.by_guid(guids[0]).Representation.Representations[0]
    original.RepresentationMaps = [model.create_entity("IfcRepresentationMap", MappingOrigin=model.by_guid(guids[0]).ObjectPlacement.RelativePlacement, MappedRepresentation=shape)]
    model.write(str(source))
    config = default_protocol_config()
    config["entities"][0]["fields"].append({
        "id": "group_code", "label": "Group Code", "editable": True,
        "source": {"kind": "type_property", "pset": "Pset_Group", "property": "Code"},
        "write": {"enabled": True},
    })
    extract_to_excel(str(source), str(workbook), plan_payload={"protocol": config})
    edit(workbook, [("Elements", guid, "Type Name", f"Type-{i}") for i, guid in enumerate(guids)] +
         [("ProtocolData", guid, "Group Code", f"Code-{i}") for i, guid in enumerate(guids)])
    update(exported)
    model = ifcopenshell.open(str(output))
    original = model.by_guid(original_guid)
    assert model.by_guid(guids[0]).ObjectType == "Preserve occurrence ObjectType"
    assert model.by_guid(guids[0]).PredefinedType == "USERDEFINED"
    assert util.get_psets(original)["Pset_Group"]["Code"] == "Initial"
    for i, guid in enumerate(guids):
        clone = util.get_type(model.by_guid(guid))
        assert clone.is_a() == "IfcBeamType"
        assert clone.Description == "Original description"
        assert clone.Tag == "Type tag"
        assert clone.PredefinedType == original.PredefinedType
        assert clone.RepresentationMaps == original.RepresentationMaps
        assert util.get_psets(clone)["Pset_Group"]["Code"] == f"Code-{i}"
        assert clone.HasPropertySets[0].id() != original.HasPropertySets[0].id()
        assert util.get_material(clone).Name == "Type Steel"
        assert util.get_material(clone).id() == util.get_material(original).id()
        assert any(rel.is_a("IfcRelAssociatesClassification") and rel.RelatingClassification.Name == "Test classification"
                   for rel in clone.HasAssociations)


def test_unrelated_types_with_matching_names_remain_separate(exported):
    source, workbook, output, guids, original_guid = exported
    model = ifcopenshell.open(str(source))
    unrelated = model.create_entity("IfcBeamType", GlobalId=new_guid(), Name="UNRELATED", PredefinedType="BEAM")
    extra = model.create_entity("IfcBeam", GlobalId=new_guid(), Name="Extra")
    ifcopenshell.api.run("type.assign_type", model, related_objects=[extra], relating_type=unrelated)
    extra_guid, unrelated_guid = extra.GlobalId, unrelated.GlobalId
    model.write(str(source))
    extract_to_excel(str(source), str(workbook), plan_payload={"protocol": default_protocol_config()})
    edit(workbook, [("Elements", guids[0], "Type Name", "MATCH"), ("Elements", extra_guid, "Type Name", "MATCH")])
    update(exported)
    model = ifcopenshell.open(str(output))
    assert util.get_type(model.by_guid(guids[0])).GlobalId != util.get_type(model.by_guid(extra_guid)).GlobalId
    assert util.get_type(model.by_guid(extra_guid)).GlobalId == unrelated_guid


def test_concise_grouped_conflict_for_24_occurrences():
    from ifc_write_plan import WorkbookWritePlan
    model = ifcopenshell.file(schema="IFC4")
    shared = model.create_entity("IfcBeamType", GlobalId="29d6H0ALv02eXGj8cfAO5Z", Name="Original", PredefinedType="BEAM")
    beams = [model.create_entity("IfcBeam", GlobalId=new_guid(), Name="BEAM") for _ in range(24)]
    ifcopenshell.api.run("type.assign_type", model, related_objects=beams, relating_type=shared)
    plan = WorkbookWritePlan(model)
    for i, beam in enumerate(beams):
        plan.add(beam, dict(kind="type_attribute", attribute="Name"),
                 "EST_S_Beam_Beam022" if i < 14 else "EST_S_Beam_Beam023", "Elements", 623 + i, "Type Name")
    plan.resolve()
    assert len(plan.errors) == 1
    error = plan.errors[0]
    assert error["target_guid"] == shared.GlobalId
    assert [len(group["affected"]) for group in error["requested_values"]] == [14, 10]
    assert len(error["message"]) < 150
    assert plan.summary()["conflicting"] == 1
    assert plan.summary()["affected_conflict_rows"] == 24


def test_occurrence_edits_are_not_routed_to_new_types(exported):
    source, workbook, output, guids, _ = exported
    edit(workbook, [("Elements", guids[0], "Type Name", "GROUP"),
                    ("Elements", guids[0], "Instance Name", "EDITED OCCURRENCE"),
                    ("COBieMapping", guids[0], "COBie_Component.Name", "EDITED PROPERTY")])
    update(exported)
    model = ifcopenshell.open(str(output))
    occurrence = model.by_guid(guids[0])
    clone = util.get_type(occurrence)
    assert occurrence.Name == "EDITED OCCURRENCE"
    assert clone.Name == "GROUP"
    assert util.get_psets(occurrence, should_inherit=False)["COBie_Component"]["Name"] == "EDITED PROPERTY"
    assert "COBie_Component" not in util.get_psets(clone)
