"""Resolve workbook edits against the untouched source, before any IFC mutation."""
from collections import defaultdict

from ifc_protocol import (
    PROTOCOL_DATA_SHEET, _fields_for_entity, _field_column, _get_type, _find_pset_entity,
    clean_protocol_value as clean, protocol_from_workbook, read_source, write_source,
)
import json
import pandas as pd
import ifcopenshell.util.element
from ifcopenshell.guid import new as new_guid


BASELINE_SHEET = "_IFCWritebackBaseline"
_UNSET = object()


# Version 1 called occurrence.Description "TypeDescription". Preserve that
# historical meaning; version 2's spaced header always targets the assigned type.
ELEMENT_HEADER_ALIASES = {
    1: (("OccurrenceName", "attribute", "Name"), ("Name", "attribute", "Name"),
        ("ObjectType", "attribute", "ObjectType"), ("OccurrenceType", "attribute", "ObjectType"),
        ("TypeDescription", "attribute", "Description"), ("TypeName", "type_attribute", "Name")),
}


def _text(value):
    value = clean(value)
    return "" if value is None else str(value)


class WorkbookWritePlan:
    def __init__(self, model, split_shared_types=False):
        self.model = model
        self.split_shared_types = split_shared_types is True
        self.splits = []
        self.created_types = []
        self.assignments = {}
        self.type_members = defaultdict(set)
        for rel in model.by_type("IfcRelDefinesByType"):
            self.type_members[rel.RelatingType.GlobalId].update(obj.GlobalId for obj in rel.RelatedObjects)
        self.candidates = defaultdict(list)
        self.errors = []
        self.rows = []
        self.resolved = []
        self.unchanged = 0
        self.skipped = []
        self.baselines = {}
        self.protected = {}
        def identity(obj):
            return getattr(obj, "GlobalId", None) or obj.id()
        for entity in model:
            if entity.is_a("IfcRelAggregates") or entity.is_a("IfcRelContainedInSpatialStructure"):
                self.protected[entity.id()] = (entity.is_a(), tuple(
                    (key, tuple(identity(v) for v in value) if isinstance(value, tuple) else identity(value) if hasattr(value, "id") else value)
                    for key, value in entity.get_info().items() if key != "id"))
            elif entity.is_a("IfcProduct"):
                self.protected[entity.GlobalId] = tuple((attr, getattr(entity, attr).id() if getattr(entity, attr, None) else None)
                    for attr in ("ObjectPlacement", "Representation"))
        self.protected_geometry = {e.id(): str(e) for kind in ("IfcRepresentation", "IfcRepresentationItem", "IfcObjectPlacement") for e in model.by_type(kind)}

    def add(self, element, target, value, sheet, row, field, config=None, baseline=_UNSET):
        value = clean(value)
        if value is None:  # All existing workbook writers preserve blanks.
            self.unchanged += 1
            return
        target = dict(target)
        kind = str(target.get("kind", "attribute")).lower()
        owner = element
        if kind in {"type_attribute", "typeattribute", "type_property", "typeproperty"}:
            owner = _get_type(element)
            kind = "attribute" if "attribute" in kind else "property"
        if owner is None:
            self.skipped.append(dict(GlobalId=element.GlobalId, Sheet=sheet, Row=row,
                                     Field=field, FromValue=None, ToValue=None,
                                     Status="Skipped: type object missing",
                                     Message=f"{sheet} row {row}: {field} requested {value!r}"))
            return
        if kind == "material":
            from ifc_excel_targets import material_assignment
            owner = material_assignment(element)[0]
        if kind == "occurrence_property":
            kind = "property"
        if kind in {"predefined_type", "predefinedtype"}:
            kind = "attribute"
            target["attribute"] = "PredefinedType"
        if kind == "class":
            kind = "classification"
        identity = (getattr(owner, "GlobalId", None) or owner.id(), kind,
                    target.get("attribute", ""), target.get("pset", ""),
                    target.get("property", ""), target.get("qto", ""),
                    target.get("quantity", ""), target.get("system") or target.get("classification_system", ""))
        if kind == "attribute" and target.get("attribute") in {
            "Name", "Description", "ObjectType", "ElementType", "Phase", "LongName"
        }:
            value = str(value)
        old = read_source(self.model, element, target)
        if owner != element:
            target["kind"] = kind
        key = (sheet, element.GlobalId, field)
        exported = self.baselines.setdefault(key, old if baseline is _UNSET else baseline)
        if _text(value) == _text(exported):
            self.unchanged += 1
            return
        self.candidates[identity].append(dict(
            element_guid=element.GlobalId, target_guid=getattr(owner, "GlobalId", None),
            target=target, value=value, old=old, sheet=sheet, row=row, field=field,
            config=config or {}, via_type=owner != element,
        ))

    def _conflict(self, identity, candidates):
        groups = defaultdict(list)
        for c in candidates:
            groups[_text(c["value"])].append(dict(sheet=c["sheet"], row=c["row"],
                                                   global_id=c["element_guid"], field=c["field"]))
        self.errors.append(dict(
            level="error", sheet=candidates[0]["sheet"], row=None,
            field=identity[2] or candidates[0]["field"], target_guid=identity[0],
            attribute=identity[2] or identity[4],
            requested_values=[dict(value=value, affected=rows) for value, rows in groups.items()],
            affected_rows=len({(c["sheet"], c["row"]) for c in candidates}),
            message=f"Conflicting edits for target {identity[0]} "
                    f"{identity[2] or identity[4]}: {len(groups)} requested values, "
                    f"{len({c['element_guid'] for c in candidates})} affected GUIDs."))

    def resolve(self):
        # First validate names for each occurrence, before allowing shared-type grouping.
        if self.split_shared_types:
            names = defaultdict(list)
            for identity, candidates in self.candidates.items():
                if identity[1:3] != ("attribute", "Name") or identity[0] not in self.type_members:
                    continue
                for c in candidates:
                    members = [c["element_guid"]] if c["via_type"] else self.type_members[identity[0]]
                    for guid in members:
                        names[(identity[0], guid)].append(c)
            contradictory = defaultdict(list)
            for (type_guid, guid), candidates in names.items():
                if len({_text(c["value"]) for c in candidates}) > 1:
                    contradictory[type_guid].extend(candidates)
            for type_guid, candidates in contradictory.items():
                self._conflict((type_guid, "attribute", "Name", "", ""), candidates)
            if self.errors:
                return self.errors
            requested = {key: _text(values[0]["value"]) for key, values in names.items()}
            for type_guid, members in self.type_members.items():
                original = self.model.by_guid(type_guid)
                groups = defaultdict(list)
                for guid in members:
                    groups[requested.get((type_guid, guid), _text(original.Name))].append(guid)
                if len(groups) <= 1:
                    continue
                for name, guids in groups.items():
                    if name == _text(original.Name):
                        continue
                    clone_guid = new_guid()
                    self.splits.append(dict(source_guid=type_guid, new_guid=clone_guid,
                                            name=name, occurrence_guids=sorted(guids),
                                            reassigned_occurrence_count=len(guids)))
                    for guid in guids:
                        self.assignments[guid] = clone_guid
            routed = defaultdict(list)
            for identity, candidates in self.candidates.items():
                for c in candidates:
                    destinations = [self.assignments.get(c["element_guid"], c["target_guid"])
                                    if c["via_type"] else c["target_guid"]]
                    if not c["via_type"] and c["target_guid"] in self.type_members:
                        destinations += [split["new_guid"] for split in self.splits
                                         if split["source_guid"] == c["target_guid"]]
                    for destination in destinations:
                        routed[(destination,) + identity[1:]].append(dict(c, target_guid=destination))
            self.candidates = routed
        for identity, candidates in self.candidates.items():
            if len({_text(c["value"]) for c in candidates}) > 1:
                self._conflict(identity, candidates)
            else:
                chosen = dict(candidates[0])
                chosen["config"] = dict(chosen["config"])
                for candidate in candidates[1:]:
                    for key, value in candidate["config"].items():
                        if value or key not in chosen["config"]:
                            chosen["config"][key] = value
                self.resolved.append(chosen)
        return self.errors

    def _apply_splits(self, model):
        util = ifcopenshell.util.element
        for split in self.splits:
            source = model.by_guid(split["source_guid"])
            clone = util.copy(model, source)
            clone.GlobalId = split["new_guid"]
            # Representation maps remain shared; no occurrence geometry is remapped.
            # Property graphs are copied independently for every new type.
            clone.HasPropertySets = tuple(util.copy_deep(model, pset)
                                          for pset in (source.HasPropertySets or ()))
            for rel in source.HasAssociations or ():
                if not (rel.is_a("IfcRelAssociatesMaterial") or rel.is_a("IfcRelAssociatesClassification")):
                    continue
                association = util.copy(model, rel)
                association.RelatedObjects = [clone]
                # Keep material/classification definitions and their inverse
                # styles/metadata intact; the association itself is independent.
            occurrences = [model.by_guid(guid) for guid in split["occurrence_guids"]]
            members = set(occurrences)
            relationships = [rel for rel in model.get_inverse(source)
                             if rel.is_a("IfcRelDefinesByType") and members.intersection(rel.RelatedObjects)]
            # Only change relationships. type.assign_type also clears occurrence
            # ObjectType/PredefinedType, which is inappropriate for a name split.
            assigned = util.copy(model, relationships[0])
            assigned.RelatingType = clone
            assigned.RelatedObjects = occurrences
            for relationship in relationships:
                remaining = [obj for obj in relationship.RelatedObjects if obj not in members]
                if remaining:
                    relationship.RelatedObjects = remaining
                else:
                    model.remove(relationship)
            self.created_types.append({k: v for k, v in split.items() if k != "occurrence_guids"})

    def apply(self, model):
        if self.errors:
            raise ValueError("Cannot apply a conflicting workbook write plan")
        self._apply_splits(model)
        for c in self.resolved:
            element = model.by_guid(c["target_guid"] or c["element_guid"])
            current = read_source(model, element, c["target"])
            if _text(current) == _text(c["value"]):
                applied, status = current, "Unchanged"
                self.unchanged += 1
            else:
                _, applied, status = write_source(model, element, c["target"], c["value"], c["config"])
            self.rows.append(dict(
                GlobalId=c["element_guid"], TargetGlobalId=c["target_guid"],
                Sheet=c["sheet"], Row=c["row"], Field=c["field"],
                FromValue=c["old"], ToValue=applied, Status=status,
                Message=f'{c["sheet"]} row {c["row"]}: {c["field"]}',
            ))
            if status.startswith("Rejected"):
                raise ValueError(f"Workbook write failed: {self.rows[-1]}")
        return self.rows + self.skipped

    def verify(self, model):
        for step_id, expected in self.protected_geometry.items():
            if str(model.by_id(step_id)) != expected:
                raise ValueError(f"Output verification failed: geometry/placement #{step_id} changed")
        for key, expected in self.protected.items():
            if isinstance(key, str):
                entity = model.by_guid(key)
                actual = tuple((attr, getattr(entity, attr).id() if getattr(entity, attr, None) else None)
                               for attr in ("ObjectPlacement", "Representation"))
            else:
                entity = model.by_id(key)
                def identity(obj):
                    return getattr(obj, "GlobalId", None) or obj.id()
                actual = (entity.is_a(), tuple(
                    (attr, tuple(identity(v) for v in value) if isinstance(value, tuple) else identity(value) if hasattr(value, "id") else value)
                    for attr, value in entity.get_info().items() if attr != "id"))
            if actual != expected:
                raise ValueError(f"Output verification failed: spatial hierarchy/geometry reference {key} changed")
        if self.type_members:
            for original_guid, members in self.type_members.items():
                for guid in members:
                    expected = self.assignments.get(guid, original_guid)
                    assigned = _get_type(model.by_guid(guid))
                    if assigned is None or assigned.GlobalId != expected:
                        raise ValueError(f"Output verification failed for type assignment {guid}")
        for split in self.splits:
            for guid in split["occurrence_guids"]:
                assigned = _get_type(model.by_guid(guid))
                if assigned is None or assigned.GlobalId != split["new_guid"] or _text(assigned.Name) != split["name"]:
                    raise ValueError(f"Output verification failed for split assignment {guid}")
        for c, result in zip(self.resolved, self.rows):
            if result["Status"] not in {"Applied", "Unchanged"}:
                continue
            actual = read_source(model, model.by_guid(c["target_guid"] or c["element_guid"]), c["target"])
            if _text(actual) != _text(result["ToValue"]):
                raise ValueError(f'Output verification failed for GUID {c["target_guid"]}: '
                                 f'{c["field"]} expected {result["ToValue"]!r}, got {actual!r}')

    def baseline_dataframe(self):
        return pd.DataFrame([dict(Sheet=sheet, GlobalId=guid, Field=field,
                                  Value=json.dumps(value, default=str))
                             for (sheet, guid, field), value in self.baselines.items()])

    def summary(self):
        return dict(applied=sum(r["Status"] == "Applied" for r in self.rows),
                    unchanged=self.unchanged, conflicting=sum(bool(e["requested_values"]) for e in self.errors),
                    rejected=sum(not e["requested_values"] for e in self.errors),
                    affected_conflict_rows=len({(row["sheet"], row["row"])
                        for error in self.errors for group in error["requested_values"] for row in group["affected"]}),
                    created_types=self.created_types,
                    skipped=len(self.skipped) + sum(r["Status"].startswith("Skipped") for r in self.rows))


def build_workbook_write_plan(model, excel, elements, cobie, add_new="no", civil_fields=(), types=None, project=None, split_shared_types=False):
    plan = WorkbookWritePlan(model, split_shared_types)
    if BASELINE_SHEET in excel.sheet_names:
        for _, row in pd.read_excel(excel, BASELINE_SHEET, keep_default_na=False).iterrows():
            plan.baselines[(row["Sheet"], row["GlobalId"], row["Field"])] = json.loads(row["Value"])

    def lookup(guid):
        try:
            return model.by_guid(str(guid)) if clean(guid) else None
        except Exception:
            return None

    for index, row in elements.iterrows():
        element = lookup(row.get("GlobalId"))
        if element is None:
            continue
        for column, kind, attr in ELEMENT_HEADER_ALIASES[1]:
            if column in elements.columns:
                plan.add(element, dict(kind=kind, attribute=attr), row.get(column),
                         "Elements", index + 2, column)
    if types is not None:
        for index, row in types.iterrows():
            element = lookup(row.get("GlobalId"))
            if element is not None and "Name" in types.columns:
                plan.add(element, dict(kind="attribute", attribute="Name"), row.get("Name"),
                         "Types", index + 2, "Name")
    if project is not None:
        for index, row in project.iterrows():
            label = clean(row.get("DataType"))
            entities = model.by_type("Ifc" + str(label)) if label in {"Project", "Site", "Building"} else []
            if not entities:
                continue
            element = entities[0]
            for column, attr in (("Name", "Name"), ("Description", "Description"),
                                 ("Phase", "Phase"), ("ProjectNumber", "LongName")):
                if hasattr(element, attr) and column in project.columns:
                    plan.add(element, dict(kind="attribute", attribute=attr), row.get(column),
                             "ProjectData", index + 2, column)
    for index, row in cobie.iterrows():
        element = lookup(row.get("GlobalId"))
        if element is None:
            continue
        for column in cobie.columns:
            if column in {"IFCElement.Name", "IFCElementType.Name"}:
                continue  # These columns have always been read-only.
            if "." in column:
                pset, prop = (s.strip() for s in column.split(".", 1))
            elif column in civil_fields:
                pset, prop = "Additional_Pset_GeneralCommon", column
            else:
                continue
            plan.add(element, dict(kind="property", pset=pset, property=prop),
                     row.get(column), "COBieMapping", index + 2, column,
                     dict(create_if_missing=add_new == "yes" or _find_pset_entity(element, pset) is not None))
    protocol = protocol_from_workbook(excel)
    if protocol and PROTOCOL_DATA_SHEET in excel.sheet_names:
        data = pd.read_excel(excel, PROTOCOL_DATA_SHEET)
        for index, row in data.iterrows():
            element = lookup(row.get("GlobalId"))
            if element is None:
                continue
            fields = _fields_for_entity(protocol, str(clean(row.get("Protocol Entity")) or ""), element)
            for field in fields:
                cfg = field.get("write") or {}
                column = _field_column(field)
                if not field.get("editable", True) or not cfg.get("enabled") or column not in data.columns:
                    continue
                target = cfg.get("target") or field.get("source") or {}
                # The exported source may be a fallback or a remapped property.
                baseline = read_source(model, element, field.get("source") or target, row_values=row.to_dict())
                plan.add(element, target, row.get(column), PROTOCOL_DATA_SHEET,
                         index + 2, column, cfg, baseline=baseline)
    from ifc_excel_native_plan import add_native_sheets, expand_classes, validate_resolved
    add_native_sheets(plan, excel, elements, types)
    expand_classes(plan)
    plan.resolve()
    if not plan.errors:
        validate_resolved(plan)
    plan.resolved.sort(key=lambda c: c["target"].get("kind") != "entity")
    return plan
