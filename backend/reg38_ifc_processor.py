"""Server-side IFC normalisation for the Regulation 38 model scan.

The extractor is deliberately independent of Supabase.  It produces table-shaped
batches which can be inspected in tests and committed atomically by a background
worker.  The source file is opened read-only and is never written by this module.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import NAMESPACE_URL, uuid5

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.placement


STAGES = (
    "UPLOADED", "VALIDATING_IFC", "IFC_OPENED", "EXTRACTING_SPATIAL_STRUCTURE", "EXTRACTING_OBJECTS",
    "EXTRACTING_PROPERTIES", "EXTRACTING_RELATIONSHIPS", "SCANNING_FIRE_PROPERTIES",
    "PREPARING_PLAN_DATA", "COMPLETE",
)
ENTITY_TYPES = (
    "IfcProject", "IfcSite", "IfcBuilding", "IfcBuildingStorey", "IfcSpace", "IfcZone",
    "IfcSpatialZone", "IfcGrid", "IfcWall", "IfcWallStandardCase", "IfcDoor",
    "IfcOpeningElement", "IfcSlab", "IfcCurtainWall", "IfcColumn", "IfcBeam", "IfcCovering",
    "IfcBuildingElementProxy", "IfcDamper", "IfcFireSuppressionTerminal", "IfcAlarm", "IfcSensor",
    "IfcLightFixture", "IfcFlowTerminal", "IfcFlowController",
)
STANDARD_FIRE = {
    ("pset_doorcommon", "firerating"): "FIRE_DOOR_RATING",
    ("pset_doorcommon", "fireexit"): "FIRE_EXIT",
    ("pset_doorcommon", "selfclosing"): "SELF_CLOSING",
    ("pset_doorcommon", "smokestop"): "SMOKE_CONTROL",
    ("pset_wallcommon", "firerating"): "FIRE_RESISTANCE",
    ("pset_openingelementcommon", "firerating"): "FIRE_RESISTANCE",
}
STRONG_FIRE_NAMES = {
    "fire rating", "firerating", "fire resistance", "fireresistance", "fire resistance rating",
    "fire-resistance", "fire designation", "fire classification", "fire performance",
    "smoke rating", "smoke seal", "smokestop", "self closing", "self-closing", "fire door",
    "compartment", "frr",
}


@dataclass
class ScanResult:
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {
        name: [] for name in ("buildings", "building_storeys", "ifc_objects", "ifc_object_properties",
                              "ifc_object_relationships", "project_spaces", "project_zones",
                              "project_zone_members", "project_grids", "project_grid_axes",
                              "fire_requirements", "model_scan_warnings")
    })
    statistics: dict[str, int] = field(default_factory=dict)


def parse_fire_rating(value: Any) -> tuple[int | None, bool]:
    """Return (minutes, smoke-indicated), declining ambiguous free-form values."""
    if value is None or isinstance(value, bool):
        return None, False
    text = str(value).strip().upper()
    if re.fullmatch(r"(?:FD|EI|REI)\s*(30|60|90|120)S?", text):
        return int(re.search(r"\d+", text).group()), text.endswith("S")
    if re.fullmatch(r"(30|60|90|120)(?:\s*(?:MIN|MINS|MINUTES?))?", text):
        return int(re.match(r"\d+", text).group()), False
    return None, False


def _id(file_id: str, kind: str, identity: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"reg38:{file_id}:{kind}:{identity}"))


def _fire_identity(file_id: str, object_id: str, requirement: str, prop: dict[str, Any]) -> str:
    """Return the canonical identity of one source fire finding.

    JSON avoids delimiter ambiguity while retaining every source coordinate that
    can distinguish two properties on the same IFC object.
    """
    return json.dumps([
        file_id, object_id, requirement, prop["source_scope"],
        prop.get("property_set"), prop["property_name"],
        prop.get("property_value_text"),
    ], ensure_ascii=False, separators=(",", ":"))


def _safe_by_type(model: Any, entity: str) -> list[Any]:
    try:
        return list(model.by_type(entity))
    except (RuntimeError, ValueError):  # Entity absent in this IFC schema.
        return []


def _value(value: Any) -> Any:
    if hasattr(value, "wrappedValue"):
        return value.wrappedValue
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _predefined(obj: Any) -> str | None:
    value = getattr(obj, "PredefinedType", None)
    return str(value) if value is not None else None


def _container(obj: Any) -> Any | None:
    try:
        container = ifcopenshell.util.element.get_container(obj)
        return container or ifcopenshell.util.element.get_aggregate(obj)
    except Exception:
        return None


def _ancestors(obj: Any) -> Iterable[Any]:
    current = _container(obj)
    seen = set()
    while current is not None and current.id() not in seen:
        yield current
        seen.add(current.id())
        current = _container(current)


def _centroid(obj: Any) -> dict[str, float] | None:
    placement = getattr(obj, "ObjectPlacement", None)
    if not placement:
        return None
    try:
        matrix = ifcopenshell.util.placement.get_local_placement(placement)
        xyz = matrix[:3, 3]
        if all(math.isfinite(float(v)) for v in xyz):
            return {"x": float(xyz[0]), "y": float(xyz[1]), "z": float(xyz[2])}
    except Exception:
        pass
    return None


def _representation_types(obj: Any) -> list[str]:
    """Return useful representation entities, including nested mapped items."""
    found: set[str] = set()
    seen: set[int] = set()

    def visit(value: Any) -> None:
        if value is None or not hasattr(value, "is_a") or value.id() in seen:
            return
        seen.add(value.id())
        kind = value.is_a()
        if kind not in {"IfcProductDefinitionShape", "IfcShapeRepresentation", "IfcRepresentationMap"}:
            found.add(kind)
        for attribute in ("Representations", "Items", "MappingSource", "MappedRepresentation"):
            child = getattr(value, attribute, None)
            for item in child if isinstance(child, (tuple, list)) else (child,):
                visit(item)

    visit(getattr(obj, "Representation", None))
    return sorted(found)


def _polygon_centroid(ring: list[list[float]]) -> tuple[float, float] | None:
    area2 = cx = cy = 0.0
    for a, b in zip(ring, ring[1:]):
        cross = a[0] * b[1] - b[0] * a[1]
        area2 += cross; cx += (a[0] + b[0]) * cross; cy += (a[1] + b[1]) * cross
    if abs(area2) < 1e-12:
        return None
    return cx / (3 * area2), cy / (3 * area2)


def _curve_points(curve: Any) -> list[list[float]]:
    """Read the common curve forms used by IFC2X3 Revit space boundaries."""
    if not curve:
        return []
    if curve.is_a("IfcPolyline"):
        return [[float(v) for v in point.Coordinates] for point in curve.Points]
    if curve.is_a("IfcCompositeCurve"):
        points: list[list[float]] = []
        for segment in curve.Segments:
            part = _curve_points(segment.ParentCurve)
            if getattr(segment, "SameSense", True) is False:
                part.reverse()
            points.extend(part if not points else part[1:])
        return points
    if curve.is_a("IfcTrimmedCurve"):
        return _curve_points(curve.BasisCurve)
    return []


def _boundary_points(connection: Any) -> list[list[float]]:
    geometry = getattr(connection, "SurfaceOnRelatingElement", None) or getattr(connection, "CurveOnRelatingElement", None)
    if not geometry:
        return []
    curve = getattr(geometry, "OuterBoundary", None) or geometry
    points = _curve_points(curve)
    position = getattr(geometry, "BasisSurface", None)
    position = getattr(position, "Position", None) or getattr(geometry, "Position", None)
    if position and points:
        matrix = ifcopenshell.util.placement.get_axis2placement(position)
        points = [(matrix @ [*(point + [0.0] * (3 - len(point)))[:3], 1.0])[:3].tolist() for point in points]
    return points


def _polygonise_segments(segments: list[tuple[list[float], list[float]]], tolerance: float = 1e-4) -> list[list[float]] | None:
    """Join unordered XY segments deterministically and return the largest closed loop."""
    def key(point):
        return (round(point[0] / tolerance), round(point[1] / tolerance))
    edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    coords: dict[tuple[int, int], list[float]] = {}
    for start, end in segments:
        a, b = key(start), key(end)
        if a == b:
            continue
        coords.setdefault(a, [round(start[0], 6), round(start[1], 6)])
        coords.setdefault(b, [round(end[0], 6), round(end[1], 6)])
        edges.add(tuple(sorted((a, b))))
    loops = []
    while edges:
        first = min(edges); edges.remove(first); loop = [first[0], first[1]]
        while loop[-1] != loop[0]:
            candidates = sorted(edge for edge in edges if loop[-1] in edge)
            if not candidates:
                break
            edge = candidates[0]; edges.remove(edge)
            loop.append(edge[1] if edge[0] == loop[-1] else edge[0])
        if len(loop) >= 4 and loop[-1] == loop[0]:
            ring = [coords[item] for item in loop]
            if abs(_signed_area(ring)) > tolerance * tolerance:
                loops.append(ring)
    return max(loops, key=lambda ring: abs(_signed_area(ring)), default=None)


def _space_boundaries(obj: Any) -> list[Any]:
    return sorted((rel for rel in (getattr(obj, "BoundedBy", ()) or ())
                   if rel.is_a("IfcRelSpaceBoundary")), key=lambda rel: rel.id())


def _boundary_geometry(obj: Any, storey: Any) -> dict[str, Any] | None:
    boundaries = _space_boundaries(obj)
    segments = []
    space_matrix = ifcopenshell.util.placement.get_local_placement(obj.ObjectPlacement) if getattr(obj, "ObjectPlacement", None) else None
    connection_types, element_types = set(), set()
    for relation in boundaries:
        connection = getattr(relation, "ConnectionGeometry", None)
        if connection:
            connection_types.add(connection.is_a())
        element = getattr(relation, "RelatedBuildingElement", None)
        if element:
            element_types.add(element.is_a())
        points = _boundary_points(connection)
        if space_matrix is not None:
            points = [(space_matrix @ [*(point + [0.0] * (3 - len(point)))[:3], 1.0])[:3].tolist() for point in points]
        for a, b in zip(points, points[1:]):
            segments.append((a, b))
    ring = _polygonise_segments(segments)
    if not ring:
        return None
    offset = _centroid(storey) or {"x": 0.0, "y": 0.0, "z": 0.0}
    ring = [[round(p[0] - offset["x"], 6), round(p[1] - offset["y"], 6)] for p in ring]
    centroid = _polygon_centroid(ring)
    if not centroid:
        return None
    return {"type": "Polygon", "coordinates": ring,
            "centroid": {"x": centroid[0], "y": centroid[1], "z": 0.0},
            "coordinate_system": "storey-local", "world_offset": offset,
            "geometry_method": "SPACE_BOUNDARY", "source": "IFC", "confidence": "HIGH",
            "boundary_count": len(boundaries), "connection_geometry_types": sorted(connection_types),
            "related_building_element_types": sorted(element_types)}


def _bounding_element_geometry(obj: Any, storey: Any) -> dict[str, Any] | None:
    """Use boundary-linked element centre-lines only when they form closed topology."""
    elements = {rel.RelatedBuildingElement for rel in _space_boundaries(obj)
                if getattr(rel, "RelatedBuildingElement", None)}
    segments = []
    settings = ifcopenshell.geom.settings(); settings.set(settings.USE_WORLD_COORDS, True)
    if hasattr(settings, "CONVERT_BACK_UNITS"):
        settings.set(settings.CONVERT_BACK_UNITS, True)
    for element in sorted(elements, key=lambda item: item.id()):
        try:
            shape = ifcopenshell.geom.create_shape(settings, element); verts = shape.geometry.verts
            points = sorted({(round(float(verts[i]), 6), round(float(verts[i + 1]), 6))
                             for i in range(0, len(verts), 3)})
        except Exception:
            continue
        if len(points) < 2:
            continue
        # A wall's furthest XY pair is a stable approximation of its centre-line.
        a, b = max(((a, b) for index, a in enumerate(points) for b in points[index + 1:]),
                   key=lambda pair: (pair[0][0] - pair[1][0]) ** 2 + (pair[0][1] - pair[1][1]) ** 2)
        segments.append((list(a), list(b)))
    ring = _polygonise_segments(segments, tolerance=0.05)
    if not ring:
        return None
    offset = _centroid(storey) or {"x": 0.0, "y": 0.0, "z": 0.0}
    ring = [[round(p[0] - offset["x"], 6), round(p[1] - offset["y"], 6)] for p in ring]
    centroid = _polygon_centroid(ring)
    if not centroid:
        return None
    return {"type": "Polygon", "coordinates": ring,
            "centroid": {"x": centroid[0], "y": centroid[1], "z": 0.0},
            "coordinate_system": "storey-local", "world_offset": offset,
            "geometry_method": "BOUNDING_ELEMENTS", "source": "IFC", "confidence": "MEDIUM",
            "boundary_count": len(_space_boundaries(obj)),
            "related_building_element_types": sorted({element.is_a() for element in elements})}


def _space_geometry(obj: Any, storey: Any) -> dict[str, Any]:
    """Tessellate a space and derive its lowest horizontal closed mesh boundary.

    Geometry is requested in IFC project units so placement matrices and vertices
    have the same scale.  The world-space mesh is normalised by the storey origin;
    this keeps previews numerically stable while retaining a reversible offset.
    """
    representations = _representation_types(obj)
    if not getattr(obj, "Representation", None):
        recovered = _boundary_geometry(obj, storey)
        if recovered:
            return recovered
        recovered = _bounding_element_geometry(obj, storey)
        if recovered:
            return recovered
        boundaries = _space_boundaries(obj)
        return {"type": "Unavailable", "reason": "NO_BOUNDARY_DATA" if boundaries else "NO_REPRESENTATION",
                "direct_representation": False, "representation_types": [], "boundary_count": len(boundaries),
                "connection_geometry_types": sorted({rel.ConnectionGeometry.is_a() for rel in boundaries if rel.ConnectionGeometry}),
                "related_building_element_types": sorted({rel.RelatedBuildingElement.is_a() for rel in boundaries if rel.RelatedBuildingElement})}
    try:
        settings = ifcopenshell.geom.settings()
        settings.set(settings.USE_WORLD_COORDS, True)
        if hasattr(settings, "CONVERT_BACK_UNITS"):
            settings.set(settings.CONVERT_BACK_UNITS, True)
        # Keep the shape wrapper alive while reading its tuple-backed buffers.
        # Taking ``create_shape(...).geometry`` directly can leave verts/faces
        # pointing at released native memory (the production centroid-only bug).
        shape = ifcopenshell.geom.create_shape(settings, obj)
        mesh = shape.geometry
        vertices = [(float(mesh.verts[i]), float(mesh.verts[i + 1]), float(mesh.verts[i + 2]))
                    for i in range(0, len(mesh.verts), 3)]
        faces = list(mesh.faces)
    except Exception as exc:
        return {"type": "Unavailable", "reason": "GEOMETRY_ENGINE_FAILURE",
                "detail": type(exc).__name__, "representation_types": representations}
    if len(vertices) < 3 or len(faces) < 3:
        return {"type": "Unavailable", "reason": "NO_CLOSED_BOUNDARY", "representation_types": representations}

    z_min = min(v[2] for v in vertices)
    z_span = max(v[2] for v in vertices) - z_min
    tolerance = max(1e-7, z_span * 1e-6)
    edge_counts: dict[tuple[int, int], int] = {}
    for i in range(0, len(faces), 3):
        tri = faces[i:i + 3]
        if len(tri) == 3 and all(abs(vertices[index][2] - z_min) <= tolerance for index in tri):
            for a, b in zip(tri, (tri[1], tri[2], tri[0])):
                edge = tuple(sorted((int(a), int(b))))
                edge_counts[edge] = edge_counts.get(edge, 0) + 1
    boundary = [edge for edge, count in edge_counts.items() if count == 1]
    adjacency: dict[int, list[int]] = {}
    for a, b in boundary:
        adjacency.setdefault(a, []).append(b); adjacency.setdefault(b, []).append(a)
    loops: list[list[int]] = []
    unused = set(boundary)
    while unused:
        first = unused.pop(); loop = [first[0], first[1]]
        while loop[-1] != loop[0]:
            candidates = [n for n in adjacency.get(loop[-1], []) if tuple(sorted((loop[-1], n))) in unused]
            if not candidates: break
            nxt = candidates[0]; unused.remove(tuple(sorted((loop[-1], nxt)))); loop.append(nxt)
        if len(loop) >= 4 and loop[-1] == loop[0]: loops.append(loop)
    if not loops:
        return {"type": "Unavailable", "reason": "NO_CLOSED_BOUNDARY", "representation_types": representations}

    offset = _centroid(storey) or {"x": 0.0, "y": 0.0, "z": 0.0}
    rings = [[[round(vertices[index][0] - offset["x"], 6), round(vertices[index][1] - offset["y"], 6)]
              for index in loop] for loop in loops]
    ring = max(rings, key=lambda candidate: abs(_signed_area(candidate)))
    centroid = _polygon_centroid(ring)
    if centroid is None:
        return {"type": "Unavailable", "reason": "INVALID_POLYGON", "representation_types": representations}
    return {"type": "Polygon", "coordinates": ring,
            "centroid": {"x": centroid[0], "y": centroid[1], "z": z_min - offset["z"]},
            "coordinate_system": "storey-local", "world_offset": offset,
            "geometry_method": "DIRECT_REPRESENTATION", "source": "IFC", "confidence": "HIGH",
            "extraction_method": "IFCOPENSHELL_LOWEST_HORIZONTAL_MESH_BOUNDARY",
            "representation_types": representations}


def _signed_area(ring: list[list[float]]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:])) / 2


def _flatten_psets(obj: Any, scope: str) -> list[dict[str, Any]]:
    rows = []
    for quantities, source_scope in ((False, scope), (True, "QUANTITY")):
        try:
            groups = ifcopenshell.util.element.get_psets(
                obj, psets_only=not quantities, qtos_only=quantities, should_inherit=False
            )
        except Exception:
            groups = {}
        for group, values in groups.items():
            for name, value in values.items():
                if name == "id":
                    continue
                primitive = _value(value)
                rows.append({"source_scope": source_scope, "property_set": group, "property_name": name,
                             "property_value_text": None if primitive is None else str(primitive),
                             "property_value_number": primitive if isinstance(primitive, (int, float)) and not isinstance(primitive, bool) else None,
                             "property_value_boolean": primitive if isinstance(primitive, bool) else None,
                             "raw_value": {"value": primitive}})
    return rows


def _classifications(obj: Any) -> list[dict[str, Any]]:
    rows = []
    for rel in getattr(obj, "HasAssociations", ()) or ():
        if rel.is_a("IfcRelAssociatesClassification"):
            ref = rel.RelatingClassification
            value = getattr(ref, "Identification", None) or getattr(ref, "ItemReference", None) or getattr(ref, "Name", None)
            rows.append({"source_scope": "CLASSIFICATION", "property_set": getattr(ref, "Name", None),
                         "property_name": "Classification", "property_value_text": str(value or ""),
                         "property_value_number": None, "property_value_boolean": None,
                         "raw_value": {"entity": ref.is_a(), "identification": _value(value)}})
    return rows


def _axis_geometry(axis: Any) -> dict[str, Any]:
    curve = getattr(axis, "AxisCurve", None)
    points = []
    if curve and curve.is_a("IfcPolyline"):
        points = [[float(v) for v in point.Coordinates] for point in curve.Points]
    return {"curve_entity": curve.is_a() if curve else None, "points": points}


class Regulation38IfcProcessor:
    def __init__(self, progress: Callable[[str, int, dict[str, int]], None] | None = None):
        self.progress = progress or (lambda stage, percent, statistics: None)

    def process(self, path: str | Path, *, project_id: str, ifc_file_id: str) -> ScanResult:
        result = ScanResult()
        self.progress("VALIDATING_IFC", 5, {})
        model = ifcopenshell.open(str(path))
        schema = model.schema
        self.progress("IFC_OPENED", 10, {"ifc_schema": schema})
        objects: list[Any] = []
        seen = set()
        for kind in ENTITY_TYPES:
            for obj in _safe_by_type(model, kind):
                gid = getattr(obj, "GlobalId", None)
                if gid and gid not in seen:
                    seen.add(gid); objects.append(obj)

        self.progress("EXTRACTING_SPATIAL_STRUCTURE", 15, {})
        buildings = _safe_by_type(model, "IfcBuilding")
        building_ids = {o.GlobalId: _id(ifc_file_id, "building", o.GlobalId) for o in buildings}
        for obj in buildings:
            result.tables["buildings"].append({"id": building_ids[obj.GlobalId], "project_id": project_id,
                "source_ifc_file_id": ifc_file_id,
                "ifc_source_guid": obj.GlobalId, "name": getattr(obj, "Name", None) or "Unnamed building",
                "description": getattr(obj, "Description", None)})
        storeys = _safe_by_type(model, "IfcBuildingStorey")
        storey_ids = {o.GlobalId: _id(ifc_file_id, "storey", o.GlobalId) for o in storeys}
        for obj in storeys:
            building = next((a for a in _ancestors(obj) if a.is_a("IfcBuilding")), None)
            if not building and buildings: building = buildings[0]
            if building:
                result.tables["building_storeys"].append({"id": storey_ids[obj.GlobalId], "building_id": building_ids[building.GlobalId],
                    "source_ifc_file_id": ifc_file_id,
                    "ifc_source_guid": obj.GlobalId, "name": getattr(obj, "Name", None) or "Unnamed storey",
                    "long_name": getattr(obj, "LongName", None), "elevation": getattr(obj, "Elevation", None)})

        self.progress("EXTRACTING_OBJECTS", 30, {})
        object_ids = {o.GlobalId: _id(ifc_file_id, "object", o.GlobalId) for o in objects}
        properties_by_object: dict[str, list[dict[str, Any]]] = {}
        for obj in objects:
            ancestors = list(_ancestors(obj))
            storey = next((a for a in ancestors if a.is_a("IfcBuildingStorey")), None)
            building = next((a for a in ancestors if a.is_a("IfcBuilding")), None)
            type_obj = ifcopenshell.util.element.get_type(obj)
            centroid = _centroid(obj)
            oid = object_ids[obj.GlobalId]
            result.tables["ifc_objects"].append({"id": oid, "project_id": project_id, "ifc_file_id": ifc_file_id,
                "building_id": building_ids.get(getattr(building, "GlobalId", "")), "storey_id": storey_ids.get(getattr(storey, "GlobalId", "")),
                "ifc_global_id": obj.GlobalId, "ifc_entity": obj.is_a(), "name": getattr(obj, "Name", None),
                "long_name": getattr(obj, "LongName", None), "description": getattr(obj, "Description", None),
                "object_type": getattr(obj, "ObjectType", None), "predefined_type": _predefined(obj), "tag": getattr(obj, "Tag", None),
                "type_global_id": getattr(type_obj, "GlobalId", None), "source_data": {"ifc_file_id": ifc_file_id, "step_id": obj.id()},
                "geometry_metadata": {"centroid": centroid, "representation": bool(getattr(obj, "Representation", None))}})
            rows = _flatten_psets(obj, "OCCURRENCE")
            for name in ("Name", "LongName", "Description", "ObjectType", "PredefinedType", "Tag"):
                if hasattr(obj, name):
                    rows.append({"source_scope": "ATTRIBUTE", "property_set": None, "property_name": name,
                                 "property_value_text": None if getattr(obj, name) is None else str(getattr(obj, name)),
                                 "property_value_number": None, "property_value_boolean": None, "raw_value": {"value": _value(getattr(obj, name))}})
            if type_obj:
                rows += _flatten_psets(type_obj, "TYPE")
                for name in ("Name", "Description", "PredefinedType", "Tag", "ElementType"):
                    if hasattr(type_obj, name):
                        rows.append({"source_scope": "TYPE", "property_set": "TYPE_ATTRIBUTES", "property_name": name,
                                     "property_value_text": None if getattr(type_obj, name) is None else str(getattr(type_obj, name)),
                                     "property_value_number": None, "property_value_boolean": None, "raw_value": {"value": _value(getattr(type_obj, name))}})
            rows += _classifications(obj)
            properties_by_object[oid] = rows
            for row in rows:
                result.tables["ifc_object_properties"].append({"id": _id(ifc_file_id, "property", f"{oid}:{len(result.tables['ifc_object_properties'])}"),
                                                               "ifc_object_id": oid, **row})

        self._spaces_zones_grids(model, result, project_id, ifc_file_id, object_ids, building_ids, storey_ids)
        self.progress("EXTRACTING_PROPERTIES", 55, {})
        self._relationships(model, result, project_id, ifc_file_id, object_ids)
        self.progress("EXTRACTING_RELATIONSHIPS", 68, {})
        self._fire(result, project_id, ifc_file_id, properties_by_object)
        self.progress("SCANNING_FIRE_PROPERTIES", 82, {})
        self._statistics(result)
        self.progress("PREPARING_PLAN_DATA", 95, result.statistics)
        result.statistics["ifc_schema"] = schema
        self.progress("COMPLETE", 100, result.statistics)
        return result

    def _spaces_zones_grids(self, model, result, project_id, file_id, object_ids, building_ids, storey_ids):
        spaces = _safe_by_type(model, "IfcSpace")
        space_ids = {}
        for obj in spaces:
            oid = object_ids[obj.GlobalId]; ancestors = list(_ancestors(obj))
            storey = next((a for a in ancestors if a.is_a("IfcBuildingStorey")), None)
            building = next((a for a in ancestors if a.is_a("IfcBuilding")), None)
            if not (building and storey): continue
            psets = ifcopenshell.util.element.get_psets(obj)
            def quantity(names):
                for values in psets.values():
                    for name in names:
                        if name in values: return _value(values[name])
                return None
            sid = _id(file_id, "space", obj.GlobalId); space_ids[obj.GlobalId] = sid
            geometry = _space_geometry(obj, storey)
            c = geometry.get("centroid") or _centroid(obj) or {}
            result.tables["project_spaces"].append({"id": sid, "project_id": project_id, "building_id": building_ids[building.GlobalId],
                "storey_id": storey_ids[storey.GlobalId], "source_ifc_object_id": oid, "ifc_global_id": obj.GlobalId,
                "source_kind": "IFC_SPACE", "space_number": getattr(obj, "Tag", None), "name": getattr(obj, "Name", None) or "Unnamed space",
                "long_name": getattr(obj, "LongName", None), "description": getattr(obj, "Description", None),
                "gross_area": quantity(("GrossFloorArea",)), "net_area": quantity(("NetFloorArea",)),
                "height": quantity(("Height", "FinishCeilingHeight", "GrossHeight")), "volume": quantity(("GrossVolume", "NetVolume")),
                "centroid_x": c.get("x"), "centroid_y": c.get("y"), "centroid_z": c.get("z"),
                "source_geometry": geometry})
            if not (getattr(obj, "Name", None) or getattr(obj, "LongName", None) or getattr(obj, "Tag", None)):
                result.tables["model_scan_warnings"].append(self._warning(project_id, file_id, oid, "SPACE_MISSING_NAME", "Space missing name", "Space has no name or number"))
        for zone in _safe_by_type(model, "IfcZone") + _safe_by_type(model, "IfcSpatialZone"):
            is_spatial = zone.is_a("IfcSpatialZone"); fire = is_spatial and (_predefined(zone) or "").upper() == "FIRESAFETY"
            zid = _id(file_id, "zone", zone.GlobalId)
            result.tables["project_zones"].append({"id": zid, "project_id": project_id, "source_ifc_object_id": object_ids[zone.GlobalId],
                "ifc_global_id": zone.GlobalId, "source_kind": "IFC_SPATIAL_ZONE" if is_spatial else "IFC_ZONE",
                "name": getattr(zone, "Name", None) or "Unnamed zone", "description": getattr(zone, "Description", None),
                "zone_type": "FIRE_COMPARTMENT" if fire else "USER_DEFINED", "source_geometry": {"centroid": _centroid(zone)},
                "source_predefined_type": _predefined(zone)})
            for rel in getattr(zone, "IsGroupedBy", ()) or ():
                for member in getattr(rel, "RelatedObjects", ()) or ():
                    if getattr(member, "GlobalId", None) in space_ids:
                        result.tables["project_zone_members"].append({"id": _id(file_id, "zone-member", f"{zone.GlobalId}:{member.GlobalId}"),
                            "zone_id": zid, "space_id": space_ids[member.GlobalId], "source": "IFC_GROUP_ASSIGNMENT"})
            if not any(row["zone_id"] == zid for row in result.tables["project_zone_members"]):
                result.tables["model_scan_warnings"].append(self._warning(
                    project_id, file_id, object_ids[zone.GlobalId], "ZONE_WITHOUT_MEMBERS",
                    "Zone without members", "The source IFC zone has no extracted space members."))
        for grid in _safe_by_type(model, "IfcGrid"):
            gid = _id(file_id, "grid", grid.GlobalId)
            result.tables["project_grids"].append({"id": gid, "project_id": project_id, "source_ifc_object_id": object_ids[grid.GlobalId],
                "ifc_global_id": grid.GlobalId, "name": getattr(grid, "Name", None)})
            for axis_type, axes in (("U", grid.UAxes), ("V", grid.VAxes), ("W", getattr(grid, "WAxes", None) or ())):
                for i, axis in enumerate(axes or ()):
                    result.tables["project_grid_axes"].append({"id": _id(file_id, "grid-axis", f"{grid.GlobalId}:{axis_type}:{i}"),
                        "grid_id": gid, "axis_tag": axis.AxisTag or f"{axis_type}{i + 1}", "axis_type": axis_type,
                        "same_sense": axis.SameSense, "geometry": {**_axis_geometry(axis), "source_ifc_file_id": file_id,
                            "parent_grid_global_id": grid.GlobalId, "axis_step_id": axis.id()}})

    def _relationships(self, model, result, project_id, file_id, object_ids):
        relationship_types = ("IfcRelContainedInSpatialStructure", "IfcRelAggregates", "IfcRelDefinesByType",
                              "IfcRelSpaceBoundary", "IfcRelAssignsToGroup", "IfcRelVoidsElement", "IfcRelFillsElement")
        pairs = {"IfcRelContainedInSpatialStructure": ("RelatedElements", "RelatingStructure"),
                 "IfcRelAggregates": ("RelatedObjects", "RelatingObject"), "IfcRelDefinesByType": ("RelatedObjects", "RelatingType"),
                 "IfcRelAssignsToGroup": ("RelatedObjects", "RelatingGroup"), "IfcRelVoidsElement": ("RelatedOpeningElement", "RelatingBuildingElement"),
                 "IfcRelFillsElement": ("RelatedBuildingElement", "RelatingOpeningElement")}
        for kind in relationship_types:
            for rel in _safe_by_type(model, kind):
                if kind == "IfcRelSpaceBoundary": sources, target = [rel.RelatingSpace], rel.RelatedBuildingElement
                else:
                    left, right = pairs[kind]; value = getattr(rel, left, ())
                    sources = list(value) if isinstance(value, tuple) else [value]; target = getattr(rel, right, None)
                for source in sources:
                    s, t = object_ids.get(getattr(source, "GlobalId", "")), object_ids.get(getattr(target, "GlobalId", ""))
                    if s and t and s != t:
                        result.tables["ifc_object_relationships"].append({"id": _id(file_id, "relationship", f"{rel.id()}:{s}:{t}"),
                            "project_id": project_id, "source_object_id": s, "target_object_id": t,
                            "relationship_type": kind.removeprefix("IfcRel").upper(), "source_ifc_relationship": kind,
                            "metadata": {"ifc_relationship_global_id": getattr(rel, "GlobalId", None), "step_id": rel.id()}})

    def _fire(self, result, project_id, file_id, by_object):
        emitted: set[str] = set()
        object_types = {row["id"]: row["ifc_entity"] for row in result.tables["ifc_objects"]}
        for oid, properties in by_object.items():
            candidates = []
            for prop in properties:
                name = prop["property_name"].strip().lower(); pset = (prop.get("property_set") or "").lower()
                standard = STANDARD_FIRE.get((pset, name))
                if not standard and name == "firerating" and pset.startswith("pset_") and pset.endswith("common"):
                    standard = "FIRE_RESISTANCE"
                compact = re.sub(r"[^a-z0-9]+", " ", name).strip()
                if standard: source, confidence, requirement = "STANDARD_IFC_PROPERTY", "HIGH", standard
                elif compact in STRONG_FIRE_NAMES: source, confidence, requirement = "KNOWN_CUSTOM_PROPERTY", "MEDIUM", "FIRE_RESISTANCE"
                elif re.fullmatch(r"(?:fr|fd|ei|rei)(?: rating| resistance)?", compact): source, confidence, requirement = "FUZZY_PROPERTY_MATCH", "LOW", "FIRE_RESISTANCE"
                else: continue
                raw = prop.get("property_value_text")
                if raw is None or not str(raw).strip(): continue
                minutes, smoke = parse_fire_rating(raw)
                identity = _fire_identity(file_id, oid, requirement, prop)
                finding_key = _id(file_id, "fire-finding", identity)
                if finding_key in emitted:
                    continue
                emitted.add(finding_key)
                row = {"id": finding_key, "source_finding_key": finding_key, "project_id": project_id,
                       "ifc_object_id": oid, "object_type": object_types[oid],
                       "requirement_type": requirement, "required_value_text": str(raw), "required_minutes": minutes,
                       "source_type": source, "source_property_set": prop.get("property_set"), "source_property_name": prop["property_name"],
                       "source_property_value": str(raw), "confidence": confidence, "review_status": "UNREVIEWED",
                       "source_scope": prop["source_scope"], "smoke_indication": smoke}
                candidates.append(row)
            high_values = {r["required_minutes"] for r in candidates if r["confidence"] == "HIGH" and r["required_minutes"] is not None}
            rating_scopes = {(r["source_scope"], r["required_minutes"]) for r in candidates if r["required_minutes"] is not None}
            occurrence = {v for s, v in rating_scopes if s == "OCCURRENCE"}; types = {v for s, v in rating_scopes if s == "TYPE"}
            conflict = len(high_values) > 1 or bool(occurrence and types and occurrence != types)
            if conflict:
                for row in candidates: row["review_status"] = "CONFLICT"
                result.tables["model_scan_warnings"].append(self._warning(project_id, file_id, oid, "FIRE_RATING_CONFLICT", "Conflicting fire ratings", "Occurrence/type or high-confidence fire ratings conflict"))
            if candidates and all(r["source_type"] != "STANDARD_IFC_PROPERTY" for r in candidates):
                result.tables["model_scan_warnings"].append(self._warning(project_id, file_id, oid, "FIRE_RATING_CUSTOM_PROPERTY", "Custom fire property match", "Fire finding is based only on custom properties"))
            result.tables["fire_requirements"] += candidates

    @staticmethod
    def _warning(project_id, file_id, oid, code, title, description):
        return {"id": _id(file_id, "warning", f"{oid}:{code}"), "project_id": project_id, "ifc_file_id": file_id,
                "ifc_object_id": oid, "warning_code": code, "category": "MODEL_DATA", "title": title,
                "description": description, "source_data": {}, "severity": "WARNING", "review_status": "UNREVIEWED"}

    def _statistics(self, result):
        objects = result.tables["ifc_objects"]; fires = result.tables["fire_requirements"]
        fire_objects = {r["ifc_object_id"] for r in fires}
        count = lambda kind: sum(o["ifc_entity"] == kind for o in objects)
        space_geometries = [row.get("source_geometry") or {} for row in result.tables["project_spaces"]]
        geometry_failures: dict[str, int] = {}
        for geometry in space_geometries:
            if geometry.get("type") != "Polygon":
                reason = geometry.get("reason", "UNSUPPORTED_REPRESENTATION")
                geometry_failures[reason] = geometry_failures.get(reason, 0) + 1
        result.statistics.update({"buildings": count("IfcBuilding"), "storeys": count("IfcBuildingStorey"),
            "spaces": count("IfcSpace"), "ifc_zones": count("IfcZone"), "ifc_spatial_zones": count("IfcSpatialZone"),
            "fire_safety_spatial_zones": sum(o["ifc_entity"] == "IfcSpatialZone" and (o.get("predefined_type") or "").upper() == "FIRESAFETY" for o in objects),
            "grid_axes": len(result.tables["project_grid_axes"]), "walls": count("IfcWall") + count("IfcWallStandardCase"),
            "walls_with_detected_fire_rating": sum(o["id"] in fire_objects and o["ifc_entity"] in ("IfcWall", "IfcWallStandardCase") for o in objects),
            "doors": count("IfcDoor"), "doors_with_detected_fire_rating": sum(o["id"] in fire_objects and o["ifc_entity"] == "IfcDoor" for o in objects),
            "custom_property_only_fire_findings": sum(w["warning_code"] == "FIRE_RATING_CUSTOM_PROPERTY" for w in result.tables["model_scan_warnings"]),
            "conflict_count": sum(w["warning_code"] == "FIRE_RATING_CONFLICT" for w in result.tables["model_scan_warnings"]),
            "unnamed_spaces": sum(w["warning_code"] == "SPACE_MISSING_NAME" for w in result.tables["model_scan_warnings"]),
            "objects_total": len(objects), "properties_total": len(result.tables["ifc_object_properties"]),
            "spaces_with_plan_geometry": sum(g.get("type") == "Polygon" and bool(g.get("coordinates")) for g in space_geometries),
            "spaces_without_plan_geometry": sum(g.get("type") != "Polygon" for g in space_geometries),
            "spaces_direct_geometry": sum(g.get("geometry_method") == "DIRECT_REPRESENTATION" for g in space_geometries),
            "spaces_boundary_derived": sum(g.get("geometry_method") == "SPACE_BOUNDARY" for g in space_geometries),
            "spaces_element_derived": sum(g.get("geometry_method") == "BOUNDING_ELEMENTS" for g in space_geometries),
            "spaces_missing_direct_representation": sum(not g.get("direct_representation", g.get("geometry_method") == "DIRECT_REPRESENTATION") for g in space_geometries),
            "spaces_centroid_only": 0, "space_geometry_failure_reasons": geometry_failures})
