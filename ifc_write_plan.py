"""Resolve workbook edits against the untouched source, before any IFC mutation."""
from collections import defaultdict

from ifc_protocol import (
    PROTOCOL_DATA_SHEET, _fields_for_entity, _field_column, _get_type, _find_pset_entity,
    clean_protocol_value as clean, protocol_from_workbook, read_source, write_source,
)
import pandas as pd


_UNSET = object()


def _text(value):
    value = clean(value)
    return "" if value is None else str(value)


class WorkbookWritePlan:
    def __init__(self, model):
        self.model = model
        self.candidates = defaultdict(list)
        self.errors = []
        self.rows = []
        self.resolved = []
        self.unchanged = 0
        self.skipped = []

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
        exported = old if baseline is _UNSET else baseline
        if _text(value) == _text(exported):
            self.unchanged += 1
            return
        self.candidates[identity].append(dict(
            element_guid=element.GlobalId, target_guid=getattr(owner, "GlobalId", None),
            target=target, value=value, old=old, sheet=sheet, row=row, field=field,
            config=config or {},
        ))

    def resolve(self):
        for identity, candidates in self.candidates.items():
            values = {_text(c["value"]) for c in candidates}
            if len(values) > 1:
                detail = "; ".join(
                    f'{c["sheet"]} row {c["row"]} GUID {c["element_guid"]} '
                    f'{c["field"]}={c["value"]!r}' for c in candidates)
                for c in candidates:
                    self.errors.append(dict(
                        level="error", sheet=c["sheet"], row=c["row"], field=c["field"],
                        global_id=c["element_guid"], target_guid=c["target_guid"],
                        value=c["value"], message=f"Conflicting edits for target {identity[0]}: {detail}"))
            else:
                chosen = dict(candidates[0])
                chosen["config"] = dict(chosen["config"])
                for candidate in candidates[1:]:
                    for key, value in candidate["config"].items():
                        if value or key not in chosen["config"]:
                            chosen["config"][key] = value
                self.resolved.append(chosen)
        return self.errors

    def apply(self, model):
        if self.errors:
            raise ValueError("Cannot apply a conflicting workbook write plan")
        for c in self.resolved:
            element = model.by_guid(c["target_guid"] or c["element_guid"])
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
        for c, result in zip(self.resolved, self.rows):
            if result["Status"] != "Applied":
                continue
            actual = read_source(model, model.by_guid(c["target_guid"] or c["element_guid"]), c["target"])
            if _text(actual) != _text(result["ToValue"]):
                raise ValueError(f'Output verification failed for GUID {c["target_guid"]}: '
                                 f'{c["field"]} expected {result["ToValue"]!r}, got {actual!r}')

    def summary(self):
        return dict(applied=sum(r["Status"] == "Applied" for r in self.rows),
                    unchanged=self.unchanged, conflicting=len(self.errors),
                    skipped=len(self.skipped) + sum(r["Status"].startswith("Skipped") for r in self.rows))


def build_workbook_write_plan(model, excel, elements, cobie, add_new="no", civil_fields=(), types=None, project=None):
    plan = WorkbookWritePlan(model)

    def lookup(guid):
        try:
            return model.by_guid(str(guid)) if clean(guid) else None
        except Exception:
            return None

    for index, row in elements.iterrows():
        element = lookup(row.get("GlobalId"))
        if element is None:
            continue
        for column, kind, attr in (
            ("OccurrenceName", "attribute", "Name"),
            ("Name", "attribute", "Name"),
            ("ObjectType", "attribute", "ObjectType"),
            ("OccurrenceType", "attribute", "ObjectType"),
            ("TypeDescription", "attribute", "Description"),
            ("TypeName", "type_attribute", "Name"),
        ):
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
    plan.resolve()
    return plan
