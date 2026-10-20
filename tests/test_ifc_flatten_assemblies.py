from pathlib import Path

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.geom
import numpy as np
import pytest

from backend.ifc_flatten_assemblies import FlattenAssembliesError, flatten_assemblies, preview_flattening, scan_assemblies, validate_flatten_choices


CUBE_VERTS = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
CUBE_FACES = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7)]


def make_model(path: Path, *, two_beams=False, nested=False):
    model = ifcopenshell.api.run("project.create_file", version="IFC4")
    ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject", name="Project")
    ifcopenshell.api.run("unit.assign_unit", model)
    context = ifcopenshell.api.run("context.add_context", model, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", model, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=context)
    assembly = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcElementAssembly", name="Frame")
    beam = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBeam", name="Retained beam")
    beam.Description, beam.Tag = "Keep me", "B-01"
    plate = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcPlate", name="Connection plate")
    products = [beam, plate]
    if two_beams:
        products.append(ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBeam", name="Second beam"))
    nested_assembly = None
    if nested:
        nested_assembly = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcElementAssembly", name="Nested frame")
        member = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcMember", name="Nested member")
        ifcopenshell.api.run("aggregate.assign_object", model, products=[member], relating_object=nested_assembly)
        products.append(nested_assembly)
    ifcopenshell.api.run("aggregate.assign_object", model, products=products, relating_object=assembly)
    pset = ifcopenshell.api.run("pset.add_pset", model, product=beam, name="Pset_Test")
    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"Rating": "60"})
    qto = ifcopenshell.api.run("pset.add_qto", model, product=beam, name="Qto_Test")
    ifcopenshell.api.run("pset.edit_qto", model, qto=qto, properties={"OriginalLength": 1.0})
    beam_type = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBeamType", name="Original type")
    ifcopenshell.api.run("type.assign_type", model, related_objects=[beam], relating_type=beam_type)
    material = ifcopenshell.api.run("material.add_material", model, name="Steel")
    ifcopenshell.api.run("material.assign_material", model, products=[beam], material=material)
    classification = ifcopenshell.api.run("classification.add_classification", model, classification="Uniclass")
    ifcopenshell.api.run("classification.add_reference", model, products=[beam], identification="Ss_20", name="Structure", classification=classification)
    geometric = [x for x in _all_descendants(assembly) if x.is_a("IfcProduct") and not x.is_a("IfcElementAssembly")]
    for index, product in enumerate(geometric):
        representation = ifcopenshell.api.run("geometry.add_mesh_representation", model, context=body, vertices=[CUBE_VERTS], faces=[CUBE_FACES])
        ifcopenshell.api.run("geometry.assign_representation", model, product=product, representation=representation)
        matrix = np.eye(4); matrix[0, 3] = index * 2 + 10; matrix[1, 3] = 5
        if index:
            angle = np.deg2rad(30); matrix[0, 0] = matrix[1, 1] = np.cos(angle); matrix[0, 1] = -np.sin(angle); matrix[1, 0] = np.sin(angle)
        ifcopenshell.api.run("geometry.edit_object_placement", model, product=product, matrix=matrix)
    unrelated = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcColumn", name="Unrelated")
    model.write(str(path))
    return assembly.GlobalId, beam.GlobalId, plate.GlobalId, getattr(nested_assembly, "GlobalId", None), unrelated.GlobalId


def _all_descendants(product):
    result = []
    for rel in getattr(product, "IsDecomposedBy", ()) or ():
        for child in rel.RelatedObjects:
            result.append(child); result.extend(_all_descendants(child))
    return result


def _bounds(product):
    settings = ifcopenshell.geom.settings(); settings.set(settings.USE_WORLD_COORDS, True)
    verts = list(ifcopenshell.geom.create_shape(settings, product).geometry.verts)
    points = [verts[i:i + 3] for i in range(0, len(verts), 3)]
    return tuple(round(fn(p[axis] for p in points), 5) for axis in range(3) for fn in (min, max))


def test_scan_uses_decomposition_and_populates_real_entity_counts(tmp_path):
    source = tmp_path / "source.ifc"; assembly, beam, _, _, _ = make_model(source)
    scan = scan_assemblies(source)
    assert scan["default_target_entity"] == "IfcBeam"
    assert {x["ifc_class"]: x["count"] for x in scan["target_entities"]} == {"IfcBeam": 1, "IfcPlate": 1}
    preview = preview_flattening(source, "IfcBeam")["assemblies"][0]
    assert preview["proposed_survivor"]["guid"] == beam and preview["absorbed_count"] == 1 and preview["status"] == "ready"


def test_multiple_matches_require_explicit_choice_and_missing_class_is_rejected(tmp_path):
    source = tmp_path / "source.ifc"; assembly, *_ = make_model(source, two_beams=True)
    row = preview_flattening(source, "IfcBeam")["assemblies"][0]
    assert row["status"] == "choice_required" and row["proposed_survivor"] is None
    with pytest.raises(FlattenAssembliesError, match="Choose the surviving"):
        validate_flatten_choices(source, "IfcBeam", [{"assembly_guid": assembly, "survivor_guid": None, "include": True}])
    with pytest.raises(FlattenAssembliesError): preview_flattening(source, "IfcDoor")


def test_flatten_preserves_survivor_data_unrelated_products_and_world_bounds(tmp_path):
    source, output, report = tmp_path / "source.ifc", tmp_path / "output.ifc", tmp_path / "report.json"
    assembly, beam, _, _, unrelated = make_model(source)
    original = ifcopenshell.open(str(source)); survivor = original.by_guid(beam)
    combined_bounds = [_bounds(x) for x in _all_descendants(original.by_guid(assembly)) if getattr(x, "Representation", None)]
    expected = tuple(min(b[i] for b in combined_bounds) if i % 2 == 0 else max(b[i] for b in combined_bounds) for i in range(6))
    result = flatten_assemblies(source, output, report, "IfcBeam", [{"assembly_guid": assembly, "survivor_guid": beam, "include": True}])
    reopened = ifcopenshell.open(str(output)); retained = reopened.by_guid(beam)
    assert result["summary"] == {"success": 1}
    with pytest.raises(RuntimeError): reopened.by_guid(assembly)
    assert retained.Name == "Retained beam" and retained.Description == "Keep me" and retained.Tag == "B-01"
    assert ifcopenshell.util.element.get_psets(retained)["Pset_Test"]["Rating"] == "60"
    assert ifcopenshell.util.element.get_psets(retained, qtos_only=True)["Qto_Test"]["OriginalLength"] == 1.0
    assert ifcopenshell.util.element.get_type(retained).Name == "Original type"
    assert ifcopenshell.util.element.get_material(retained).Name == "Steel"
    assert any(rel.is_a("IfcRelAssociatesClassification") for rel in reopened.get_inverse(retained))
    assert reopened.by_guid(unrelated).Name == "Unrelated"
    assert _bounds(retained) == expected


def test_nested_overlap_is_rejected_before_any_output_is_written(tmp_path):
    source, output = tmp_path / "source.ifc", tmp_path / "output.ifc"
    assembly, beam, _, nested, _ = make_model(source, nested=True)
    with pytest.raises(FlattenAssembliesError, match="Overlapping assembly scope"):
        flatten_assemblies(source, output, tmp_path / "report.json", "IfcBeam", [
            {"assembly_guid": assembly, "survivor_guid": beam, "include": True},
            {"assembly_guid": nested, "survivor_guid": None, "include": True},
        ])
    assert not output.exists()


def test_assembly_failure_is_atomic_and_source_is_unchanged(tmp_path, monkeypatch):
    source, output = tmp_path / "source.ifc", tmp_path / "output.ifc"
    assembly, beam, plate, _, _ = make_model(source)
    import backend.ifc_flatten_assemblies as module
    real = module._shape_mesh
    monkeypatch.setattr(module, "_shape_mesh", lambda product: (_ for _ in ()).throw(RuntimeError("bad geometry")) if product.GlobalId == plate else real(product))
    result = flatten_assemblies(source, output, tmp_path / "report.json", "IfcBeam", [{"assembly_guid": assembly, "survivor_guid": beam, "include": True}])
    reopened = ifcopenshell.open(str(output))
    assert result["summary"] == {"failed": 1} and reopened.by_guid(assembly) and reopened.by_guid(plate)
    assert ifcopenshell.open(str(source)).by_guid(assembly)
