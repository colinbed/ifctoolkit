"""Scan and flatten IFC decomposition assemblies into an existing product.

The output intentionally uses tessellated representation items.  This makes local
placement chains, mapped items and openings deterministic without mutating shared
representation resources in the source model.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.geom
import ifcopenshell.util.placement
import ifcopenshell.util.unit


class FlattenAssembliesError(ValueError):
    pass


PREFERRED_CLASSES = ("IfcBeam", "IfcColumn", "IfcMember", "IfcPlate")


def _label(entity: Any) -> dict[str, Any]:
    return {"guid": getattr(entity, "GlobalId", None), "name": getattr(entity, "Name", None) or "Unnamed", "ifc_class": entity.is_a()}


def _children(entity: Any) -> list[Any]:
    result: list[Any] = []
    for rel in tuple(getattr(entity, "IsDecomposedBy", ()) or ()) + tuple(getattr(entity, "IsNestedBy", ()) or ()):
        result.extend(list(getattr(rel, "RelatedObjects", ()) or ()))
    return result


def _descendants(entity: Any) -> list[Any]:
    result, seen, queue = [], set(), list(_children(entity))
    while queue:
        child = queue.pop(0)
        if child.id() in seen:
            continue
        seen.add(child.id()); result.append(child); queue.extend(_children(child))
    return result


def _can_carry_geometry(entity: Any) -> bool:
    return entity.is_a("IfcProduct") and hasattr(entity, "Representation") and not entity.is_a("IfcSpatialElement")


def scan_assemblies(source_path: Path) -> dict[str, Any]:
    model = ifcopenshell.open(str(source_path))
    assemblies = list(model.by_type("IfcElementAssembly"))
    entity_counts: Counter[str] = Counter()
    rows = []
    descendant_sets: dict[str, set[str]] = {}
    for assembly in assemblies:
        descendants = _descendants(assembly)
        candidates = [item for item in descendants if _can_carry_geometry(item) and not item.is_a("IfcElementAssembly")]
        entity_counts.update(item.is_a() for item in candidates)
        by_class: dict[str, list[dict[str, Any]]] = {}
        for item in candidates:
            by_class.setdefault(item.is_a(), []).append(_label(item))
        guid = assembly.GlobalId
        descendant_sets[guid] = {getattr(item, "GlobalId", "") for item in descendants}
        rows.append({**_label(assembly), "component_count": len(candidates), "components_by_class": by_class, "nested_assembly_count": sum(x.is_a("IfcElementAssembly") for x in descendants)})
    for row in rows:
        row["overlaps"] = [other["guid"] for other in rows if other["guid"] != row["guid"] and other["guid"] in descendant_sets[row["guid"]]]
    entities = [{"ifc_class": key, "count": count} for key, count in sorted(entity_counts.items())]
    default = "IfcBeam" if entity_counts.get("IfcBeam") else next((x for x in PREFERRED_CLASSES if entity_counts.get(x)), entities[0]["ifc_class"] if entities else None)
    return {"source_filename": source_path.name, "schema": model.schema, "product_count": len(model.by_type("IfcProduct")), "target_entities": entities, "default_target_entity": default, "assemblies": rows}


def preview_flattening(source_path: Path, target_class: str) -> dict[str, Any]:
    scan = scan_assemblies(source_path)
    if target_class not in {row["ifc_class"] for row in scan["target_entities"]}:
        raise FlattenAssembliesError("The selected IFC entity is not an eligible component class in this model")
    for assembly in scan["assemblies"]:
        matches = assembly["components_by_class"].get(target_class, [])
        assembly["matching_components"] = matches
        assembly["proposed_survivor"] = matches[0] if len(matches) == 1 else None
        assembly["absorbed_count"] = max(0, assembly["component_count"] - 1) if matches else 0
        assembly["status"] = "ready" if len(matches) == 1 else ("choice_required" if matches else "skipped")
        assembly["reason"] = "" if matches else f"No {target_class} component exists in this assembly"
    scan["selected_target_entity"] = target_class
    return scan


def _shape_mesh(product: Any) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    shape = ifcopenshell.geom.create_shape(settings, product)
    verts = list(shape.geometry.verts); faces = list(shape.geometry.faces)
    return ([tuple(float(v) for v in verts[i:i + 3]) for i in range(0, len(verts), 3)], [tuple(int(v) for v in faces[i:i + 3]) for i in range(0, len(faces), 3)])


def _inverse_transform(points: Iterable[tuple[float, float, float]], product: Any, unit_scale: float) -> list[tuple[float, float, float]]:
    matrix = ifcopenshell.util.placement.get_local_placement(getattr(product, "ObjectPlacement", None))
    # Rigid IFC placements: inverse(R,t) = (R^T,-R^T t).
    rotation = [[float(matrix[r][c]) for c in range(3)] for r in range(3)]
    # Geometry iterator coordinates are SI; placement matrix translations are
    # expressed in project units.
    translation = [float(matrix[r][3]) * unit_scale for r in range(3)]
    return [tuple(sum(rotation[k][r] * (point[k] - translation[k]) for k in range(3)) for r in range(3)) for point in points]


def _context(model: Any) -> Any:
    contexts = [c for c in model.by_type("IfcGeometricRepresentationSubContext") if getattr(c, "ContextIdentifier", None) == "Body"]
    if contexts: return contexts[0]
    contexts = model.by_type("IfcGeometricRepresentationContext")
    if not contexts: raise FlattenAssembliesError("The IFC has no geometric representation context")
    return contexts[0]


def _check_scope(model: Any, choices: list[dict[str, str]]) -> None:
    chosen = {row["assembly_guid"] for row in choices if row.get("include", True)}
    for guid in chosen:
        assembly = model.by_guid(guid)
        nested = {getattr(x, "GlobalId", None) for x in _descendants(assembly) if x.is_a("IfcElementAssembly")}
        conflict = nested & chosen
        if conflict:
            raise FlattenAssembliesError(f"Overlapping assembly scope: {guid} includes {sorted(conflict)[0]}. Select only the ancestor or descendant.")


def validate_flatten_choices(source_path: Path, target_class: str, choices: list[dict[str, Any]]) -> None:
    model = ifcopenshell.open(str(source_path))
    _check_scope(model, choices)
    for choice in choices:
        if not choice.get("include", True):
            continue
        assembly = model.by_guid(str(choice.get("assembly_guid") or ""))
        if not assembly or not assembly.is_a("IfcElementAssembly"):
            raise FlattenAssembliesError("A selected assembly does not exist in the source model")
        matches = [x for x in _descendants(assembly) if _can_carry_geometry(x) and x.is_a() == target_class]
        survivor_guid = choice.get("survivor_guid")
        if matches and not survivor_guid:
            raise FlattenAssembliesError(f"Choose the surviving {target_class} for assembly {assembly.Name or assembly.GlobalId}")
        if survivor_guid and survivor_guid not in {x.GlobalId for x in matches}:
            raise FlattenAssembliesError(f"The selected survivor is not a {target_class} component of assembly {assembly.Name or assembly.GlobalId}")


def _relationship_risks(model: Any, product: Any, assembly_members: set[int]) -> list[str]:
    safe = {"IfcRelAggregates", "IfcRelNests", "IfcRelContainedInSpatialStructure", "IfcRelVoidsElement", "IfcRelFillsElement", "IfcRelDefinesByType", "IfcRelDefinesByProperties", "IfcRelAssociatesMaterial", "IfcRelAssociatesClassification", "IfcStyledItem"}
    risks = []
    for inverse in model.get_inverse(product):
        if inverse.is_a("IfcRelationship") and inverse.is_a() not in safe:
            # Unknown relationship semantics are not rewritten automatically.
            # The entire assembly is left unchanged instead of risking a
            # dangling or misleading external reference.
            risks.append(inverse.is_a())
    return sorted(set(risks))


def _flatten_one(model: Any, assembly_guid: str, survivor_guid: str, target_class: str) -> dict[str, Any]:
    assembly, survivor = model.by_guid(assembly_guid), model.by_guid(survivor_guid)
    if not assembly or not survivor or not assembly.is_a("IfcElementAssembly"):
        raise FlattenAssembliesError("Assembly or survivor no longer exists")
    descendants = _descendants(assembly)
    if survivor.id() not in {item.id() for item in descendants} or survivor.is_a() != target_class:
        raise FlattenAssembliesError(f"The survivor must be an existing {target_class} component of the assembly")
    products = [x for x in [assembly, *descendants] if _can_carry_geometry(x) and getattr(x, "Representation", None)]
    members = {x.id() for x in [assembly, *descendants]}
    risks = {x.GlobalId: _relationship_risks(model, x, members) for x in descendants if x.id() != survivor.id()}
    risks = {k: v for k, v in risks.items() if v}
    if risks:
        raise FlattenAssembliesError(f"Unresolved external relationships would be lost: {risks}")
    meshes, fingerprints = [], set()
    for product in products:
        vertices, faces = _shape_mesh(product)
        fingerprint = (
            tuple(round(value, 6) for point in vertices for value in point),
            tuple(index for face in faces for index in face),
        )
        if fingerprint in fingerprints: continue
        fingerprints.add(fingerprint); meshes.append((_inverse_transform(vertices, survivor, ifcopenshell.util.unit.calculate_unit_scale(model)), faces))
    if not meshes: raise FlattenAssembliesError("No reliable visible geometry could be extracted")
    old_representation = survivor.Representation
    representation = ifcopenshell.api.run(
        "geometry.add_mesh_representation", model, context=_context(model),
        vertices=[vertices for vertices, _ in meshes],
        faces=[[list(face) for face in faces] for _, faces in meshes],
        force_faceted_brep=model.schema.upper().startswith("IFC2X3"),
    )
    survivor.Representation = model.create_entity("IfcProductDefinitionShape", None, None, [representation])
    containers = list(getattr(assembly, "ContainedInStructure", ()) or ())
    if not getattr(survivor, "ContainedInStructure", None) and containers:
        ifcopenshell.api.run("spatial.assign_container", model, products=[survivor], relating_structure=containers[0].RelatingStructure)
    assembly_label, survivor_label = _label(assembly), _label(survivor)
    absorbed = [x for x in descendants if x.id() != survivor.id() and x.is_a("IfcProduct")]
    absorbed_labels = [_label(x) for x in absorbed]
    for product in reversed(absorbed): ifcopenshell.api.run("root.remove_product", model, product=product)
    ifcopenshell.api.run("root.remove_product", model, product=assembly)
    # Old representations are detached, but shared resources are deliberately not deep-deleted.
    return {"assembly": assembly_label, "survivor": survivor_label, "absorbed_components": absorbed_labels, "status": "success", "geometry_conversion": "World-space tessellation transformed into survivor-local coordinates", "style_limitations": "Per-item surface styles may not survive tessellation", "relationship_changes": "Assembly decomposition removed; survivor spatial containment retained or inherited", "old_representation_detached": bool(old_representation)}


def flatten_assemblies(source_path: Path, output_path: Path, report_path: Path, target_class: str, choices: list[dict[str, Any]]) -> dict[str, Any]:
    validate_flatten_choices(source_path, target_class, choices)
    model = ifcopenshell.open(str(source_path)); before = len(model.by_type("IfcProduct"))
    results = []
    for choice in choices:
        if not choice.get("include", True):
            results.append({"assembly": {"guid": choice.get("assembly_guid")}, "status": "skipped", "reason": "Excluded by user"}); continue
        if not choice.get("survivor_guid"):
            results.append({"assembly": {"guid": choice.get("assembly_guid")}, "status": "skipped", "reason": f"No {target_class} survivor selected"}); continue
        snapshot = model.to_string()
        try: results.append(_flatten_one(model, choice["assembly_guid"], choice["survivor_guid"], target_class))
        except Exception as exc:
            model = ifcopenshell.file.from_string(snapshot)
            results.append({"assembly": {"guid": choice.get("assembly_guid")}, "status": "failed", "reason": str(exc)})
    model.write(str(output_path))
    reopened = ifcopenshell.open(str(output_path)); after = len(reopened.by_type("IfcProduct"))
    report = {"source_filename": source_path.name, "ifc_schema": model.schema, "selected_target_entity": target_class, "before_product_count": before, "after_product_count": after, "assemblies": results, "summary": dict(Counter(x["status"] for x in results))}
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report

