"""Versioned workbook contract and a source-relative, canonical IFC write plan.

All proposals are resolved before mutation. An unchanged alias/protocol value is
never a proposal, so worksheet order cannot reverse an explicit edit.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from numbers import Real
import json

from ifc_write_plan import BASELINE_SHEET, WorkbookWritePlan

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element as element_util
import pandas as pd
from ifcopenshell.guid import new as new_guid

from backend.excel_schema import schema_reference
from backend.excel_proxy_mapping import build_mapping_plan
from ifc_protocol import (read_source, read_field, write_source, protocol_from_workbook,
                          _fields_for_entity, _field_column)

WORKBOOK_VERSION = "2.0"
ELEMENT_COLUMNS = ["GlobalId", "Instance Name", "Instance Description", "Type Name", "Type Description",
                   "Object Type", "Material", "IFC Entity", "Predefined Type", "Type GlobalId",
                   "Material Scope", "Material Kind", "Material Edit Note", "Predefined Type Availability",
                   "IFCPresentationLayer", "SourceFile"]
TYPE_COLUMNS = ["Type GlobalId", "Type Name", "Type Description", "IFC Entity", "Predefined Type", "Element Type", "Assigned Instance Count"]
# v1 TypeDescription was exported from occurrence.Description; retain that meaning.
HEADER_ALIASES = {
    "Elements": {"Name": ("Instance Name", "OccurrenceName", "Name"),
                 "Description": ("Instance Description", "OccurrenceDescription", "TypeDescription"),
                 "ObjectType": ("Object Type", "OccurrenceType", "ObjectType", "ObjectType or ElementType"),
                 "Type.Name": ("Type Name", "TypeName"), "Type.Description": ("Type Description",)},
    "Types": {"Name": ("Type Name", "TypeName", "Name"), "Description": ("Type Description", "TypeDescription"),
              "ElementType": ("Element Type", "ObjectType or ElementType")},
    "BuildingStoreys": {"Name": ("Name",), "Description": ("Description",), "LongName": ("Long Name",)},
}
FLOOR_DEFAULTS = [("COBie_Floor", "Category")] + [("COBie_FloorAttributes", n) for n in (
    "GrossExternalArea", "GrossInternalArea", "HeightToHaunch", "NetInternalArea", "RoofFloorArea", "TotalInternalWallArea")]


def clean(value):
    if value is None or (not isinstance(value, (list, tuple, dict)) and pd.isna(value)):
        return None
    if isinstance(value, str):
        return value.strip() or None
    return value


def same(a, b):
    a, b = clean(a), clean(b)
    if isinstance(a, Real) and isinstance(b, Real):
        return a == b
    return ("" if a is None else str(a)) == ("" if b is None else str(b))


def material_info(obj):
    own = element_util.get_material(obj, should_inherit=False)
    typ = element_util.get_type(obj)
    material = own or (element_util.get_material(typ, should_inherit=False) if typ else None)
    scope = "occurrence" if own else "type" if material else "none"
    if material is None:
        return None, obj, "", scope, "none", "Creates a simple occurrence material assignment."
    simple = material.is_a() == "IfcMaterial"
    definition = getattr(material, "ForLayerSet", None) or getattr(material, "ForProfileSet", None) or material
    label = getattr(definition, "Name", None) or getattr(definition, "LayerSetName", None) or material.is_a()
    return material, obj if own else typ, label, scope, material.is_a(), "Editable simple association; creates/reuses a material without renaming shared entities." if simple else "Reference only: structured material assignment is preserved."


def element_row(obj, filename):
    typ = element_util.get_type(obj)
    _, _, material, scope, kind, note = material_info(obj)
    return [obj.GlobalId, obj.Name, obj.Description, typ.Name if typ else None,
            typ.Description if typ else None, getattr(obj, "ObjectType", None), material,
            obj.is_a(), getattr(obj, "PredefinedType", None), typ.GlobalId if typ else None,
            scope, kind, note, "Native" if hasattr(obj, "PredefinedType") else "Unavailable: no native attribute in this schema",
            None, filename]


def type_row(obj):
    return [obj.GlobalId, obj.Name, obj.Description, obj.is_a(), getattr(obj, "PredefinedType", None),
            getattr(obj, "ElementType", None), len(element_util.get_types(obj))]


def floor_pairs(model):
    pairs = set(FLOOR_DEFAULTS)
    for obj in model.by_type("IfcBuildingStorey"):
        for pset, props in element_util.get_psets(obj, should_inherit=False).items():
            if pset in {"COBie_Floor", "COBie_FloorAttributes"}:
                pairs.update((pset, prop) for prop in props if prop != "id")
    return sorted(pairs)


def floor_classification(obj):
    for rel in obj.HasAssociations or ():
        if not rel.is_a("IfcRelAssociatesClassification"):
            continue
        ref = rel.RelatingClassification
        source = getattr(ref, "ReferencedSource", None)
        if getattr(source, "Name", None) == "COBie Floors":
            return getattr(ref, "Identification", None) or getattr(ref, "ItemReference", None), ref.Name
    return None, None


def storey_row(obj, pairs):
    parent = element_util.get_aggregate(obj)
    seen = set()
    while parent is not None and not parent.is_a("IfcBuilding") and parent.id() not in seen:
        seen.add(parent.id())
        parent = element_util.get_aggregate(parent)
    props = element_util.get_psets(obj, should_inherit=False)
    return [obj.GlobalId, getattr(parent, "GlobalId", None), obj.Name, obj.Description,
            obj.LongName, obj.Elevation, *[props.get(p, {}).get(n) for p, n in pairs], *floor_classification(obj)]


@dataclass
class Proposal:
    value: object
    sheet: str
    row: int
    column: str
    origin: str = ""
    config: dict = field(default_factory=dict)


class RoundTripPlan:
    def __init__(self, model, excel, elements, types, *, split_shared_types=False, create_missing=False, mapping_repository=None, mapping_inference=None, mapping_scope=None):
        self.model = model
        self.version = "1.0"
        if "Instructions" in excel.sheet_names:
            instructions = pd.read_excel(excel, "Instructions")
            if {"Topic", "Instructions"}.issubset(instructions.columns):
                versions = instructions.loc[instructions["Topic"] == "Workbook Version", "Instructions"]
                if len(versions):
                    self.version = str(versions.iloc[0])
        self.schema = schema_reference(model.schema_identifier)
        self.requests = defaultdict(list)
        self.resolved = {}
        self.shared_plan = WorkbookWritePlan(model, split_shared_types)
        self.errors = self.shared_plan.errors
        if BASELINE_SHEET in excel.sheet_names:
            for _, row in pd.read_excel(excel, BASELINE_SHEET, keep_default_na=False).iterrows():
                self.shared_plan.baselines[(row["Sheet"], row["GlobalId"], row["Field"])] = json.loads(row["Value"])
        self.previous_splits = {}
        self.previous_split_records = []
        if "TypeSplits" in excel.sheet_names:
            self.previous_split_records = pd.read_excel(excel, "TypeSplits").to_dict("records")
            for row in self.previous_split_records:
                self.previous_splits[str(row["new_guid"])] = str(row["source_guid"])
        self.clone_sources = {}
        self.logs = []
        if self.version.split(".")[0] not in {"1", "2"}:
            self.error("Instructions", 2, "Workbook Version", f"Unsupported workbook version {self.version}; export a new workbook.")
        self.skipped = set()
        self.create_missing = create_missing
        self.split_shared_types = split_shared_types
        self.preserved = {}
        self.expected_types = {}
        self.hierarchy = {}
        for obj in model.by_type("IfcProduct"):
            parent = element_util.get_aggregate(obj)
            container = element_util.get_container(obj, should_get_direct=True)
            self.hierarchy[obj.GlobalId] = (getattr(parent, "GlobalId", None), getattr(container, "GlobalId", None))
        self._collect_table("Elements", elements)
        self._collect_table("Types", types)
        self._collect_legacy_mapping(elements, mapping_repository, mapping_inference, mapping_scope)
        if "BuildingStoreys" in excel.sheet_names:
            self._collect_table("BuildingStoreys", pd.read_excel(excel, "BuildingStoreys"))
        self._collect_property_tables(excel)
        self._collect_protocol(excel)
        self._resolve()
        self._expand_type_conversions()
        self._validate()

    def error(self, sheet, row, column, message):
        self.errors.append({"level": "error", "sheet": sheet, "row": row, "field": column, "message": message})

    def lookup(self, guid, sheet, row, column):
        try:
            return self.model.by_guid(str(guid)) if clean(guid) else None
        except Exception:
            self.error(sheet, row, column, "GlobalId does not resolve in the source IFC.")
            return None

    def propose(self, obj, kind, name, value, sheet, row, column, *, baseline=None, origin="", config=None):
        if obj is None or clean(value) is None or value == "UNCHANGED":
            self.shared_plan.unchanged += 1
            return
        if baseline is None:
            baseline = self.read(obj, kind, name)
        baseline_key = (sheet, origin or obj.GlobalId, name if sheet == "Properties" else column)
        baseline = self.shared_plan.baselines.setdefault(baseline_key, baseline)
        if kind == "property" and isinstance(baseline, Real) and not isinstance(baseline, bool):
            try:
                value = float(value)
            except (ValueError, TypeError):
                self.error(sheet, row, column, "This IFC measure property requires a numeric value.")
                return
        if same(value, baseline):
            self.shared_plan.unchanged += 1
            return
        key = (obj.GlobalId, kind, name)
        self.requests[key].append(Proposal(clean(value), sheet, row, column, origin, config or {}))

    def read(self, obj, kind, name):
        if kind == "attribute":
            return getattr(obj, name, None)
        if kind == "entity":
            return obj.is_a()
        if kind == "material":
            return material_info(obj)[2]
        if kind == "classification":
            from classification_writer import find_classification_value
            return find_classification_value(obj, name)
        if kind == "floor_classification":
            return floor_classification(obj)[0 if name == "Reference" else 1]
        if kind == "property":
            pset, prop = name.split(".", 1)
            return element_util.get_psets(obj, should_inherit=False).get(pset, {}).get(prop)
        return None

    def _collect_table(self, sheet, frame):
        seen = set()
        for index, row in frame.iterrows():
            number = int(index) + 2
            guid = clean(row.get("Type GlobalId" if sheet == "Types" else "GlobalId")) or clean(row.get("GlobalId"))
            obj = self.lookup(guid, sheet, number, "GlobalId")
            if obj is None:
                self.error(sheet, number, "GlobalId", "A source GlobalId is required; STEP IDs are reference only."); continue
            if guid in seen:
                self.error(sheet, number, "GlobalId", "Duplicate GlobalId."); continue
            seen.add(guid)
            row_key = str(clean(row.get("RowKey")) or "")
            exported_guid = row_key.split(":", 1)[0] if ":" in row_key else ""
            if len(exported_guid) == 22 and exported_guid != guid:
                self.error(sheet, number, "GlobalId", "Read-only GlobalId differs from the exported RowKey identity.")
            if (sheet == "Types" and not obj.is_a("IfcTypeObject")) or (sheet == "BuildingStoreys" and not obj.is_a("IfcBuildingStorey")):
                self.error(sheet, number, "GlobalId", "GlobalId has the wrong entity category."); continue
            if sheet == "Types" and clean(row.get("Type GlobalId")) and clean(row.get("GlobalId")) and row["Type GlobalId"] != row["GlobalId"]:
                self.error(sheet, number, "Type GlobalId", "Conflicting read-only type identity aliases.")
            typ = element_util.get_type(obj) if sheet == "Elements" else None
            if sheet == "Elements" and clean(row.get("Type GlobalId")) and (not typ or row["Type GlobalId"] not in self.known_type_identities(typ.GlobalId)):
                self.error(sheet, number, "Type GlobalId", "Read-only assigned type identity differs from IfcRelDefinesByType.RelatingType.")
            for attribute, aliases in HEADER_ALIASES[sheet].items():
                target = typ if attribute.startswith("Type.") else obj
                attr = attribute.split(".")[-1]
                for column in aliases:
                    if target is None and clean(row.get(column)) is not None:
                        self.error(sheet, number, column, "No assigned type. Assign a type in IFC before editing type attributes.")
                    self.propose(target, "attribute", attr, row.get(column), sheet, number, column, origin=obj.GlobalId)
            if sheet == "BuildingStoreys":
                for column in row.index:
                    if column.startswith(("COBie_Floor.", "COBie_FloorAttributes.")):
                        self.propose(obj, "property", column, row[column], sheet, number, column, config={"create_if_missing": True})
                for name in ("Reference", "Name"):
                    column = f"Classification: COBie Floors {name}"
                    self.propose(obj, "floor_classification", name, row.get(column), sheet, number, column)
                continue
            for column in ("IFC Entity", "IfcEntity", "TargetEntity"):
                if column == "TargetEntity" and str(clean(row.get("ApplyChange")) or "").lower() not in {"yes", "true", "1", "y"}:
                    continue
                self.propose(obj, "entity", "class", row.get(column), sheet, number, column, origin=obj.GlobalId)
            for column in ("Predefined Type", "PredefinedType", "TargetPredefinedType"):
                if column == "TargetPredefinedType" and str(clean(row.get("ApplyChange")) or "").lower() not in {"yes", "true", "1", "y"}:
                    continue
                self.propose(obj, "attribute", "PredefinedType", row.get(column), sheet, number, column, origin=obj.GlobalId)
            if sheet == "Elements":
                material, target, _, _, _, _ = material_info(obj)
                if material and not material.is_a("IfcMaterial"):
                    if clean(row.get("Material")) is not None and not same(row.get("Material"), material_info(obj)[2]):
                        self.error(sheet, number, "Material", "Structured material assignment is reference only; edit its layers/profiles/constituents in an IFC authoring tool.")
                else:
                    self.propose(target, "material", "Name", row.get("Material"), sheet, number, "Material", origin=obj.GlobalId, baseline=material_info(obj)[2])

    def known_type_identities(self, guid):
        identities = {guid}
        while guid in self.previous_splits and self.previous_splits[guid] not in identities:
            guid = self.previous_splits[guid]
            identities.add(guid)
        return identities

    def _collect_legacy_mapping(self, elements, repository, inference, scope):
        # Keep the existing signature/cache/repository planner for authorised v1
        # mapping rows. New native headers already produce canonical proposals.
        rows = []
        for index, row in elements.iterrows():
            if str(clean(row.get("ApplyChange")) or "").lower() not in {"yes", "y", "true", "1"}:
                continue
            obj = self.lookup(row.get("GlobalId"), "Elements", index + 2, "GlobalId")
            if obj is None:
                continue
            values = row.to_dict()
            if values.get("TargetPredefinedType") == "UNCHANGED":
                values["TargetPredefinedType"] = ""
            rows.append((index, values, obj))
        decisions, self.mapping_metrics = build_mapping_plan(
            rows, entity_is_valid=lambda name: bool(self.schema.get(name, {}).get("selectable")),
            predefined_is_valid=lambda name, enum: not enum or enum in self.schema.get(name, {}).get("enum", ()),
            infer=inference or (lambda *_: None), repository=repository, scope=scope)
        for decision in decisions:
            obj = self.model.by_guid(decision.global_id)
            # Invalid explicit values must be rejected by preflight, never
            # silently replaced with the planner's unchanged-source fallback.
            target = decision.explicit_target_entity or decision.resolved_target_entity
            self.propose(obj, "entity", "class", target, "Elements", decision.row_index + 2, "TargetEntity")

    def _collect_property_tables(self, excel):
        if "COBieMapping" in excel.sheet_names:
            for index, row in pd.read_excel(excel, "COBieMapping").iterrows():
                obj = self.lookup(row.get("GlobalId"), "COBieMapping", index + 2, "GlobalId")
                if obj is None:
                    continue
                effective = element_util.get_psets(obj)
                for column in row.index:
                    if "." not in column or column.startswith("IFCElement"):
                        continue
                    pset, prop = column.split(".", 1)
                    self.propose(obj, "property", column, row[column], "COBieMapping", index + 2, column,
                                 baseline=effective.get(pset, {}).get(prop), config={"create_if_missing": self.create_missing})
        if "Properties" in excel.sheet_names:
            for index, row in pd.read_excel(excel, "Properties").iterrows():
                obj = self.lookup(row.get("GlobalId"), "Properties", index + 2, "GlobalId")
                if obj is None or not clean(row.get("PropertySet")) or not clean(row.get("Property")):
                    continue
                name = f"{row['PropertySet']}.{row['Property']}"
                pset = element_util.get_psets(obj, should_inherit=False).get(row["PropertySet"], {})
                # Quantity editing continues to use ProtocolData; this table also
                # contains quantities, which must never become replacement psets.
                definition = self.model.by_id(pset["id"]) if pset.get("id") else None
                prop = next((p for p in getattr(definition, "HasProperties", ()) if p.Name == row["Property"]), None)
                if prop is not None and prop.is_a("IfcPropertySingleValue"):
                    self.propose(obj, "property", name, row.get("Value"), "Properties", index + 2, "Value")
        if "ProjectData" in excel.sheet_names:
            labels = {"Project": "IfcProject", "Site": "IfcSite", "Building": "IfcBuilding"}
            for index, row in pd.read_excel(excel, "ProjectData").iterrows():
                cls = labels.get(row.get("DataType"))
                objects = self.model.by_type(cls) if cls else []
                if len(objects) != 1:
                    continue
                for col, attr in (("Name", "Name"), ("Description", "Description"), ("Phase", "Phase"), ("ProjectNumber", "LongName")):
                    if hasattr(objects[0], attr):
                        self.propose(objects[0], "attribute", attr, row.get(col), "ProjectData", index + 2, col)

    def _collect_protocol(self, excel):
        protocol = protocol_from_workbook(excel)
        if not protocol or "ProtocolData" not in excel.sheet_names:
            return
        frame = pd.read_excel(excel, "ProtocolData")
        for index, row in frame.iterrows():
            obj = self.lookup(row.get("GlobalId"), "ProtocolData", index + 2, "GlobalId")
            if obj is None:
                continue
            for spec in _fields_for_entity(protocol, str(clean(row.get("Protocol Entity")) or ""), obj):
                cfg = spec.get("write") or {}
                if not spec.get("editable", True) or not cfg.get("enabled"):
                    continue
                col = _field_column(spec); value = clean(row.get(col))
                # Compare with the exported source, which may differ from the write target.
                baseline = self.shared_plan.baselines.setdefault(("ProtocolData", obj.GlobalId, col), read_field(self.model, obj, spec))
                if value is None or same(value, baseline):
                    self.shared_plan.unchanged += 1
                    continue
                source = cfg.get("target") or spec.get("source") or {}
                kind = source.get("kind", "attribute").lower()
                target = element_util.get_type(obj) if kind in {"type_attribute", "typeattribute", "type_property", "typeproperty"} else obj
                if target is None:
                    self.error("ProtocolData", index + 2, col, "No assigned type."); continue
                if kind in {"attribute", "type_attribute", "typeattribute", "predefined_type", "predefinedtype"}:
                    attr = source.get("attribute") or "PredefinedType"
                    self.propose(target, "attribute", attr, value, "ProtocolData", index + 2, col, origin=obj.GlobalId)
                elif kind in {"property", "occurrence_property", "type_property", "typeproperty"}:
                    name = f"{source.get('pset')}.{source.get('property')}"
                    self.propose(target, "property", name, value, "ProtocolData", index + 2, col, origin=obj.GlobalId, config=cfg)
                elif kind in {"classification", "class"}:
                    system = source.get("system") or source.get("classification_system") or "Classification"
                    if system == "COBie Floors":
                        self.propose(obj, "floor_classification", "Reference", value, "ProtocolData", index + 2, col)
                    else:
                        self.propose(obj, "classification", system, value, "ProtocolData", index + 2, col)
                else:
                    name = f"{source.get('qto') or source.get('pset')}.{source.get('quantity') or source.get('property')}" if kind == "quantity" else col
                    key = (obj.GlobalId, "protocol", name)
                    self.requests[key].append(Proposal(value, "ProtocolData", index + 2, col, obj.GlobalId, {"source": source, **cfg}))

    def _resolve(self):
        # Reuse the existing shared-target resolver and authorised name-splitting
        # workflow. Native schema/material handlers execute its canonical targets.
        for (guid, kind, name), proposals in self.requests.items():
            attr, pset, prop, system = "", "", "", ""
            target = {"kind": kind}
            if kind == "attribute":
                attr = name; target["attribute"] = name
            elif kind == "property":
                pset, prop = name.split(".", 1); target.update(pset=pset, property=prop)
            elif kind == "classification":
                system = name; target["system"] = name
            else:
                attr = name
            identity = (guid, kind, attr, pset, prop, "", "", system)
            for proposal in proposals:
                origin = proposal.origin or guid
                self.shared_plan.candidates[identity].append(dict(
                    element_guid=origin, target_guid=guid, target=target,
                    value=proposal.value, old=self.read(self.model.by_guid(guid), kind, name),
                    sheet=proposal.sheet, row=proposal.row, field=proposal.column,
                    config=proposal.config, via_type=origin != guid,
                    native_kind=kind, native_name=name, proposal=proposal,
                ))
        self.shared_plan.resolve()
        self.clone_sources = {split["new_guid"]: split["source_guid"] for split in self.shared_plan.splits}
        for candidate in self.shared_plan.resolved:
            key = (candidate["target_guid"], candidate["native_kind"], candidate["native_name"])
            p = candidate["proposal"]
            self.resolved[key] = Proposal(candidate["value"], p.sheet, p.row, p.column, p.origin, candidate["config"])

    def source_object(self, guid):
        return self.model.by_guid(self.clone_sources.get(guid, guid))

    def baseline_dataframe(self):
        return self.shared_plan.baseline_dataframe()

    def summary(self):
        return dict(applied=sum(r["Status"] == "Applied" for r in self.logs),
                    unchanged=self.shared_plan.unchanged + sum(r["Status"] == "Unchanged" for r in self.logs),
                    skipped=sum(str(r["Status"]).startswith("Skipped") for r in self.logs),
                    conflicting=len(self.errors), created_types=self.shared_plan.created_types,
                    affected_conflict_rows=len({(row["sheet"], row["row"]) for error in self.errors
                        for group in error.get("requested_values", []) for row in group["affected"]}))

    def _expand_type_conversions(self):
        for (guid, kind, _), proposal in list(self.resolved.items()):
            obj = self.source_object(guid)
            if kind != "entity" or not obj.is_a("IfcTypeProduct"):
                continue
            info = self.schema.get(str(proposal.value), {})
            choices = info.get("correspondence") or ()
            if not choices:
                continue
            for occurrence in element_util.get_types(obj):
                key = (occurrence.GlobalId, "entity", "class")
                if key not in self.resolved:
                    # Preserve compatible occurrence classes; otherwise use the
                    # installed occurrence/type correspondence's primary class.
                    desired = occurrence.is_a() if occurrence.is_a() in choices else choices[0]
                    self.resolved[key] = Proposal(desired, proposal.sheet, proposal.row, proposal.column)

    def _validate(self):
        for key, proposal in self.resolved.items():
            guid, kind, name = key; obj = self.source_object(guid)
            entity_request = self.resolved.get((guid, "entity", "class"))
            entity = str(entity_request.value) if entity_request else obj.is_a()
            info = self.schema.get(entity)
            if not info:
                self.error(proposal.sheet, proposal.row, proposal.column, f"{entity} is not in {self.model.schema_identifier}."); continue
            if kind == "entity":
                source = self.schema[obj.is_a()]
                if not source["selectable"] or not info["selectable"] or info["category"] != source["category"] or (source["category"] == "type" and not obj.is_a("IfcTypeProduct")):
                    self.error(proposal.sheet, proposal.row, proposal.column, "Unsupported conversion: choose a concrete compatible occurrence/type class from SchemaEntities.")
                # Occurrence conversion would otherwise implicitly change all typed siblings.
                typ = element_util.get_type(obj) if obj.is_a("IfcElement") else None
                if typ and typ.is_a() not in info["correspondence"]:
                    requested = self.resolved.get((typ.GlobalId, "entity", "class"))
                    if not requested or requested.value not in info["correspondence"]:
                        self.error(proposal.sheet, proposal.row, proposal.column, "Assigned type is incompatible; edit its IFC Entity on Types coherently (affects all assigned instances).")
            if kind == "attribute":
                if name in {"GlobalId", "OwnerHistory", "ObjectPlacement", "Representation", "RepresentationMaps", "HasPropertySets", "Elevation"}:
                    self.error(proposal.sheet, proposal.row, proposal.column, f"{name} is read-only in Excel writeback.")
                if name not in info["attributes"]:
                    self.error(proposal.sheet, proposal.row, proposal.column, f"{entity} has no native {name} in {self.model.schema_identifier}.")
                elif name == "PredefinedType":
                    if proposal.value not in info["enum"]:
                        self.error(proposal.sheet, proposal.row, proposal.column, f"Invalid {entity}.PredefinedType '{proposal.value}'. Allowed: {', '.join(info['enum'])}.")
                    elif proposal.value == "USERDEFINED":
                        companion = "ElementType" if obj.is_a("IfcElementType") else "ObjectType"
                        request = self.resolved.get((guid, "attribute", companion))
                        if companion in info["attributes"] and not clean(request.value if request else getattr(obj, companion, None)):
                            self.error(proposal.sheet, proposal.row, proposal.column, f"USERDEFINED requires native {companion}.")
            if kind == "entity":
                existing = getattr(obj, "PredefinedType", None)
                requested = self.resolved.get((guid, "attribute", "PredefinedType"))
                if "PredefinedType" in info["required"] and not requested and not existing:
                    self.error(proposal.sheet, proposal.row, proposal.column, "Target class requires a native Predefined Type; provide a valid value.")
                if existing and not requested and info["enum"] and existing not in info["enum"]:
                    self.error(proposal.sheet, proposal.row, proposal.column, "Existing PredefinedType is incompatible with target; provide a valid Predefined Type.")
                if obj.is_a("IfcElementType"):
                    for occurrence in element_util.get_types(obj):
                        target_occurrence = self.resolved.get((occurrence.GlobalId, "entity", "class"))
                        desired = target_occurrence.value if target_occurrence else occurrence.is_a()
                        if desired not in info["correspondence"]:
                            self.error(proposal.sheet, proposal.row, proposal.column, "Type class change needs compatible occurrence class edits for its assigned instances.")

    def apply(self):
        # Snapshot native graph references for output verification, without copying geometry.
        self.original_values = {key: self.read(self.source_object(key[0]), key[1], key[2]) for key in self.resolved}
        for obj in self.model.by_type("IfcProduct"):
            self.preserved[obj.GlobalId] = {a: str(getattr(obj, a, None)) for a in ("ObjectPlacement", "Representation", "Elevation") if hasattr(obj, a)}
        for obj in self.model.by_type("IfcElement"):
            typ = element_util.get_type(obj)
            self.expected_types[obj.GlobalId] = typ.GlobalId if typ else None
        self.shared_plan._apply_splits(self.model)
        self.expected_types.update(self.shared_plan.assignments)
        # The API coherently reassigns occurrences when reassigning a type.
        ordered = sorted(self.resolved.items(), key=lambda item: (item[0][1] != "entity", not self.model.by_guid(item[0][0]).is_a("IfcTypeObject")))
        for key, proposal in ordered:
            self._apply_one(key, proposal)
        return self.logs

    def _apply_one(self, key, proposal):
        try:
            return self._apply_one_unchecked(key, proposal)
        except Exception as exc:
            raise ValueError(f"{proposal.sheet}!{proposal.row} {proposal.column} ({key[0]}): IFC write failed: {exc}") from exc

    def _apply_one_unchecked(self, key, proposal):
        guid, kind, name = key; obj = self.model.by_guid(guid)
        old = self.original_values.get(key, self.read(obj, kind, name))
        status = "Applied"
        if kind != "floor_classification" and same(self.read(obj, kind, name), proposal.value):
            self.logs.append({"GlobalId": guid, "Status": "Unchanged", "Sheet": proposal.sheet, "Row": proposal.row,
                              "Field": proposal.column, "FromValue": old, "ToValue": proposal.value, "Message": f"{kind}: {name}"})
            return
        if kind == "attribute":
            setattr(obj, name, str(proposal.value))
        elif kind == "entity":
            if obj.is_a() != proposal.value:
                if obj.is_a("IfcTypeObject"):
                    ifcopenshell.api.run("root.reassign_class", self.model, product=obj, ifc_class=proposal.value)
                else:
                    # Same-schema occurrence conversion retains inverse relationships;
                    # root.reassign_class would implicitly reclassify shared siblings.
                    from ifcopenshell.util.schema import reassign_class
                    reassign_class(self.model, obj, str(proposal.value))
        elif kind == "property":
            pset, prop = name.split(".", 1)
            self._isolate_property(obj, pset, prop)
            _, _, status = write_source(self.model, obj, {"kind": "property", "pset": pset, "property": prop}, proposal.value, proposal.config)
        elif kind == "material":
            # Detach only this target from a shared relation; never rename IfcMaterial.
            for rel in list(getattr(obj, "HasAssociations", ()) or ()):
                if rel.is_a("IfcRelAssociatesMaterial"):
                    others = [o for o in rel.RelatedObjects if o != obj]
                    if others: rel.RelatedObjects = others
                    else: self.model.remove(rel)
            mat = next((m for m in self.model.by_type("IfcMaterial") if m.Name == proposal.value), None)
            if mat is None: mat = self.model.create_entity("IfcMaterial", Name=str(proposal.value))
            self.model.create_entity("IfcRelAssociatesMaterial", GlobalId=new_guid(), OwnerHistory=getattr(obj, "OwnerHistory", None), RelatedObjects=[obj], RelatingMaterial=mat)
        elif kind == "classification":
            self._replace_classification(obj, name, proposal.value, None)
        elif kind == "floor_classification":
            self._apply_floor_classification(obj)
        elif kind == "protocol":
            _, _, status = write_source(self.model, obj, proposal.config["source"], proposal.value, proposal.config)
        if kind == "entity" and same(old, proposal.value):
            status = "Skipped: unchanged class"
        if str(status).startswith("Rejected"):
            raise ValueError(f"{proposal.sheet}!{proposal.row} {proposal.column}: {status}")
        if str(status).startswith("Skipped"):
            self.skipped.add(key)
        self.logs.append({"GlobalId": guid, "Status": status, "Sheet": proposal.sheet, "Row": proposal.row, "Field": proposal.column, "FromValue": old, "ToValue": proposal.value, "Message": f"{kind}: {name}"})

    def _isolate_property(self, obj, pset_name, prop_name):
        """Copy shared property definitions before a target-specific edit."""
        from ifc_protocol import _find_pset_entity
        from ifcopenshell.util.element import copy, copy_deep
        pset = _find_pset_entity(obj, pset_name)
        if pset is None:
            return
        related = [r for r in self.model.get_inverse(pset) if r.is_a("IfcRelDefinesByProperties")]
        targets = {o.id() for r in related for o in r.RelatedObjects}
        targets.update(o.id() for o in self.model.get_inverse(pset) if o.is_a("IfcTypeObject"))
        if len(targets) > 1:
            cloned = copy(self.model, pset)
            cloned.HasProperties = [copy_deep(self.model, p) for p in pset.HasProperties]
            if obj.is_a("IfcTypeObject"):
                obj.HasPropertySets = [cloned if p == pset else p for p in obj.HasPropertySets]
            else:
                for rel in related:
                    if obj not in rel.RelatedObjects:
                        continue
                    others = [o for o in rel.RelatedObjects if o != obj]
                    if others: rel.RelatedObjects = others
                    else: self.model.remove(rel)
                self.model.create_entity("IfcRelDefinesByProperties", GlobalId=new_guid(), OwnerHistory=getattr(obj, "OwnerHistory", None), RelatedObjects=[obj], RelatingPropertyDefinition=cloned)
            pset = cloned
        else:
            # A single property may itself be shared between separate sets.
            properties = list(pset.HasProperties)
            for index, prop in enumerate(properties):
                if prop.Name == prop_name and len(self.model.get_inverse(prop)) > 1:
                    properties[index] = copy_deep(self.model, prop)
                    pset.HasProperties = properties

    def _apply_floor_classification(self, obj):
        ref, name = floor_classification(obj)
        for attr in ("Reference", "Name"):
            request = self.resolved.get((obj.GlobalId, "floor_classification", attr))
            if request:
                if attr == "Reference": ref = request.value
                else: name = request.value
        self._replace_classification(obj, "COBie Floors", ref, name)

    def _replace_classification(self, obj, system, ref, name):
        source = next((c for c in self.model.by_type("IfcClassification") if c.Name == system), None)
        if source is None: source = self.model.create_entity("IfcClassification", Name=system)
        for rel in list(obj.HasAssociations or ()):
            if rel.is_a("IfcRelAssociatesClassification") and getattr(getattr(rel.RelatingClassification, "ReferencedSource", None), "Name", None) == system:
                others = [o for o in rel.RelatedObjects if o != obj]
                if others: rel.RelatedObjects = others
                else: self.model.remove(rel)
        field = "ItemReference" if self.model.schema == "IFC2X3" else "Identification"
        cref = self.model.create_entity("IfcClassificationReference", **{field: ref}, Name=name, ReferencedSource=source)
        self.model.create_entity("IfcRelAssociatesClassification", GlobalId=new_guid(), OwnerHistory=getattr(obj, "OwnerHistory", None), RelatedObjects=[obj], RelatingClassification=cref)

    def verify(self, model):
        for key, proposal in self.resolved.items():
            if key in self.skipped:
                continue
            guid, kind, name = key
            obj = model.by_guid(guid)
            if kind == "protocol":
                actual = read_source(model, obj, proposal.config["source"])
            else:
                actual = self.read(obj, kind, name)
            if not same(actual, proposal.value):
                raise ValueError(f"Output verification failed: {guid} {kind} {name}: expected {proposal.value!r}, got {actual!r}.")
        for guid, fields in self.preserved.items():
            obj = model.by_guid(guid)
            for attr, expected in fields.items():
                if str(getattr(obj, attr, None)) != expected:
                    raise ValueError(f"Output verification failed: {guid} {attr} changed unexpectedly.")
        for guid, expected in self.hierarchy.items():
            obj = model.by_guid(guid)
            parent = element_util.get_aggregate(obj)
            container = element_util.get_container(obj, should_get_direct=True)
            if (getattr(parent, "GlobalId", None), getattr(container, "GlobalId", None)) != expected:
                raise ValueError(f"Output verification failed: spatial hierarchy/containment changed for {guid}.")
        for guid, expected in self.expected_types.items():
            typ = element_util.get_type(model.by_guid(guid))
            if (typ.GlobalId if typ else None) != expected:
                raise ValueError(f"Output verification failed: assigned type relationship changed for {guid}.")
