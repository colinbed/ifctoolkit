"""Version 2 worksheet adapters and schema validation for the resolved write plan."""
import pandas as pd
import ifcopenshell
from ifc_excel_schema import schema_reference, validate_native
from ifc_excel_targets import material_assignment, FLOOR_REFERENCE, FLOOR_NAME
from ifc_protocol import clean_protocol_value as clean, _get_type


def error(plan, sheet, row, field, message):
    plan.errors.append(dict(level="error", sheet=sheet, row=row, field=field, message=message,
                            requested_values=[]))


def add_native_sheets(plan, excel, elements, types):
    model = plan.model

    def lookup(row, sheet, index, category):
        guid = clean(row.get("Type GlobalId" if sheet == "Types" and "Type GlobalId" in row else "GlobalId"))
        try:
            obj = model.by_guid(str(guid)) if guid else None
        except RuntimeError:
            obj = None
        if obj is None:
            error(plan, sheet, index + 2, "GlobalId", "GUID does not resolve in source IFC.")
        elif category == "type" and not obj.is_a("IfcTypeObject"):
            error(plan, sheet, index + 2, "Type GlobalId", "GUID must identify an existing type object.")
            obj = None
        elif category == "storey" and not obj.is_a("IfcBuildingStorey"):
            error(plan, sheet, index + 2, "GlobalId", "GUID must identify an existing building storey.")
            obj = None
        return obj

    for sheet, frame, category in (("Elements", elements, "occurrence"), ("Types", types, "type")):
        if frame is None:
            continue
        seen = set()
        for index, row in frame.iterrows():
            obj = lookup(row, sheet, index, category)
            if obj is None:
                continue
            if obj.GlobalId in seen:
                error(plan, sheet, index + 2, "GlobalId", "Duplicate GUID in worksheet.")
                continue
            seen.add(obj.GlobalId)
            attrs = (("Instance Name", "Name"), ("Instance Description", "Description"), ("Object Type", "ObjectType")) if category == "occurrence" else (("Type Name", "Name"), ("Type Description", "Description"), ("Element Type", "ElementType"))
            for col, attr in attrs:
                if col in frame:
                    plan.add(obj, dict(kind="attribute", attribute=attr), row.get(col), sheet, index + 2, col)
            if category == "occurrence":
                for col, attr in (("Type Name", "Name"), ("Type Description", "Description")):
                    if col in frame:
                        plan.add(obj, dict(kind="type_attribute", attribute=attr), row.get(col), sheet, index + 2, col)
                if "Material" in frame:
                    owner, mat, scope, kind, label = material_assignment(obj)
                    value = clean(row.get("Material"))
                    if value is not None and str(value) != label:
                        if kind not in {"IfcMaterial", "unassigned"}:
                            error(plan, sheet, index + 2, "Material", f"{kind} is reference only; edit the structured IFC assignment outside this workbook.")
                        else:
                            plan.add(obj, dict(kind="material"), value, sheet, index + 2, "Material")
            for col, target in (("IFC Entity", dict(kind="entity")), ("Predefined Type", dict(kind="attribute", attribute="PredefinedType"))):
                if col in frame:
                    if col == "Predefined Type" and row.get(col) == "Unavailable" and not hasattr(obj, "PredefinedType"):
                        continue
                    plan.add(obj, target, row.get(col), sheet, index + 2, col)
            if category == "occurrence":
                for col, kind, attr in (("IfcEntity", "entity", ""), ("PredefinedType", "attribute", "PredefinedType")):
                    if col in frame:
                        plan.add(obj, dict(kind=kind, **({"attribute": attr} if attr else {})), row.get(col), sheet, index + 2, col)
            # Legacy conversion requests enter the same canonical plan. No STEP fallback.
            if str(clean(row.get("ApplyChange")) or "").lower() in {"yes", "y", "true", "1"}:
                if clean(row.get("TargetEntity")):
                    plan.add(obj, dict(kind="entity"), row.get("TargetEntity"), sheet, index + 2, "TargetEntity")
                value = clean(row.get("TargetPredefinedType"))
                if value and value != "UNCHANGED":
                    plan.add(obj, dict(kind="attribute", attribute="PredefinedType"), value, sheet, index + 2, "TargetPredefinedType")

    if "BuildingStoreys" in excel.sheet_names:
        frame = pd.read_excel(excel, "BuildingStoreys")
        seen = set()
        for index, row in frame.iterrows():
            obj = lookup(row, "BuildingStoreys", index, "storey")
            if obj is None:
                continue
            if obj.GlobalId in seen:
                error(plan, "BuildingStoreys", index + 2, "GlobalId", "Duplicate storey GUID.")
                continue
            seen.add(obj.GlobalId)
            for col, attr in (("Name", "Name"), ("Description", "Description"), ("Long Name", "LongName")):
                plan.add(obj, dict(kind="attribute", attribute=attr), row.get(col), "BuildingStoreys", index + 2, col)
            for col, attr in ((FLOOR_REFERENCE, "Reference"), (FLOOR_NAME, "Name")):
                plan.add(obj, dict(kind="floor_classification", attribute=attr), row.get(col), "BuildingStoreys", index + 2, col)
            for col in frame:
                if "." in col:
                    pset, prop = col.split(".", 1)
                    if pset in {"COBie_Floor", "COBie_FloorAttributes"}:
                        plan.add(obj, dict(kind="property", pset=pset, property=prop), row.get(col), "BuildingStoreys", index + 2, col, dict(create_if_missing=True))


def expand_classes(plan):
    """Make the API's implicit shared-type conversions explicit and conflict checked."""
    refs = schema_reference(plan.model.schema_identifier)
    requests = [c for key, group in list(plan.candidates.items()) if key[1] == "entity" for c in group]
    for c in requests:
        obj = plan.model.by_guid(c["target_guid"])
        old = refs[obj.is_a()]
        new = refs.get(str(c["value"]))
        if not new or not new["selectable"] or old["category"] != new["category"] or not (obj.is_a("IfcElement") or obj.is_a("IfcElementType")):
            error(plan, c["sheet"], c["row"], c["field"], f"Unsupported conversion {obj.is_a()} to {c['value']} in {plan.model.schema_identifier}; select a concrete {old['category']} element class.")
            continue
        is_type = obj.is_a("IfcTypeObject")
        assigned = obj if is_type else _get_type(obj)
        if assigned is None:
            continue
        corresponding = [n for n in new["corresponding"] if refs.get(n, {}).get("selectable")]
        if not corresponding:
            error(plan, c["sheet"], c["row"], c["field"], "No supported type/occurrence correspondence for this class in the input schema.")
            continue
        members = [plan.model.by_guid(g) for g in plan.type_members[assigned.GlobalId]]
        # Preserve a compatible current class where the schema allows alternatives.
        counterpart = (members[0].is_a() if is_type and members else assigned.is_a())
        if counterpart not in corresponding:
            definition = ifcopenshell.schema_by_name(plan.model.schema_identifier)
            from ifc_excel_schema import is_subtype
            roots = [n for n in corresponding if all(is_subtype(definition.declaration_by_name(other), n) for other in corresponding)]
            counterpart = roots[0] if len(roots) == 1 else None
        if counterpart is None:
            error(plan, c["sheet"], c["row"], c["field"], "Ambiguous type/occurrence correspondence; select a class with a unique supported correspondence.")
            continue
        changes = [(member, counterpart if is_type else new["entity"]) for member in members]
        if not is_type:
            changes.append((assigned, counterpart))
        for target, entity in changes:
            if target.GlobalId == obj.GlobalId:
                continue
            plan.add(target, dict(kind="entity"), entity, c["sheet"], c["row"], c["field"])


def validate_resolved(plan):
    schema = plan.model.schema_identifier
    refs = schema_reference(schema)
    values = {(c["target_guid"], c["target"].get("kind", "attribute"), c["target"].get("attribute", "")): c["value"] for c in plan.resolved}
    # Validate the final enum for every class change, including unchanged enum cells.
    checks = list(plan.resolved)
    split_sources = {s["new_guid"]: s["source_guid"] for s in plan.splits}

    def original(guid):
        return plan.model.by_guid(split_sources.get(guid, guid))

    for c in plan.resolved:
        if c["target"].get("kind") != "entity":
            continue
        obj = original(c["target_guid"])
        new = refs[str(c["value"])]
        for attr in refs[obj.is_a()]["attributes"]:
            old = getattr(obj, attr, None)
            if old not in (None, "", (), []) and attr not in new["attributes"]:
                error(plan, c["sheet"], c["row"], c["field"], f"Conversion would discard populated {attr}; use a compatible class.")
        if "PredefinedType" in new["attributes"]:
            current = getattr(obj, "PredefinedType", None)
            final = values.get((c["target_guid"], "attribute", "PredefinedType"), current)
            if final:
                checks.append(dict(c, target=dict(kind="attribute", attribute="PredefinedType"), value=final))
            elif not next(a for a in ifcopenshell.schema_by_name(schema).declaration_by_name(new["entity"]).all_attributes() if a.name() == "PredefinedType").optional():
                error(plan, c["sheet"], c["row"], "Predefined Type", "Target class requires an explicit native Predefined Type.")
    for c in checks:
        kind = c["target"].get("kind", "attribute")
        if kind != "attribute":
            continue
        obj = original(c["target_guid"])
        entity = values.get((c["target_guid"], "entity", ""), obj.is_a())
        attr = c["target"].get("attribute")
        message = validate_native(schema, entity, attr, c["value"])
        if message:
            error(plan, c["sheet"], c["row"], c["field"], message)
        if attr == "PredefinedType" and c["value"] == "USERDEFINED":
            companion = "ElementType" if obj.is_a("IfcTypeObject") else "ObjectType"
            if companion in refs[entity]["attributes"]:
                final = values.get((c["target_guid"], "attribute", companion), getattr(obj, companion, None))
                if not clean(final):
                    error(plan, c["sheet"], c["row"], c["field"], f"USERDEFINED requires native {companion}.")
