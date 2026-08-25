import datetime
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element
import pandas as pd
from ifcopenshell.guid import new as new_guid

from classification_writer import attach_classification, find_classification_value


PROTOCOL_SCHEMA_VERSION = "1.0"
PROTOCOL_DATA_SHEET = "ProtocolData"
PROTOCOL_FIELDS_SHEET = "ProtocolFields"
PROTOCOL_CONFIG_SHEET = "_IFCProtocol"


BASE_PROTOCOL_COLUMNS = ["GlobalId", "StepId", "IFC Entity", "Protocol Entity", "SourceFile"]


def utc_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_protocol_config() -> Dict[str, Any]:
    now = utc_iso()
    return {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "name": "Default IFC Toolkit configuration",
        "project_client": "",
        "description": "Default IFC to Excel extraction and write-back configuration.",
        "version": "1.0",
        "created_by": "IFC Toolkit",
        "created_date": now,
        "last_modified": now,
        "ifc_schemas": ["IFC2X3", "IFC4", "IFC4X3"],
        "visibility": "private",
        "entities": [
            {
                "entity": "IfcElement",
                "include_subtypes": True,
                "fields": [
                    {
                        "id": "asset_name",
                        "label": "Asset Name",
                        "source": {"kind": "attribute", "attribute": "Name"},
                        "datatype": "text",
                        "editable": True,
                        "write": {
                            "enabled": True,
                            "target": {"kind": "attribute", "attribute": "Name"},
                        },
                    },
                    {
                        "id": "asset_type",
                        "label": "Asset Type",
                        "source": {
                            "kind": "first_non_empty",
                            "sources": [
                                {"kind": "attribute", "attribute": "ObjectType"},
                                {"kind": "predefined_type"},
                                {"kind": "constant", "value": "UNCLASSIFIED"},
                            ],
                        },
                        "datatype": "text",
                        "editable": True,
                        "write": {
                            "enabled": True,
                            "target": {"kind": "attribute", "attribute": "ObjectType"},
                        },
                    },
                    {
                        "id": "type_name",
                        "label": "Type Name",
                        "source": {"kind": "type_attribute", "attribute": "Name"},
                        "datatype": "text",
                        "editable": True,
                        "write": {
                            "enabled": True,
                            "target": {"kind": "type_attribute", "attribute": "Name"},
                        },
                    },
                    {
                        "id": "manufacturer",
                        "label": "Manufacturer",
                        "source": {
                            "kind": "type_property",
                            "pset": "Pset_ManufacturerTypeInformation",
                            "property": "Manufacturer",
                        },
                        "datatype": "text",
                        "editable": True,
                        "write": {
                            "enabled": True,
                            "create_if_missing": True,
                            "target": {
                                "kind": "type_property",
                                "pset": "Pset_ManufacturerTypeInformation",
                                "property": "Manufacturer",
                            },
                        },
                    },
                    {
                        "id": "uniclass_pr",
                        "label": "Uniclass Pr",
                        "source": {
                            "kind": "first_non_empty",
                            "sources": [
                                {
                                    "kind": "classification",
                                    "system": "Uniclass Pr Products",
                                    "value": "reference",
                                },
                                {
                                    "kind": "property",
                                    "pset": "Additional_Pset_GeneralCommon",
                                    "property": "UniclassPr",
                                },
                            ],
                        },
                        "datatype": "text",
                        "editable": True,
                        "write": {
                            "enabled": True,
                            "target": {
                                "kind": "property",
                                "pset": "Additional_Pset_GeneralCommon",
                                "property": "UniclassPr",
                            },
                            "create_if_missing": True,
                            "data_type": "IfcLabel",
                        },
                    },
                ],
            },
            {
                "entity": "IfcDoor",
                "include_subtypes": True,
                "fields": [
                    {
                        "id": "fire_rating",
                        "label": "Fire Rating",
                        "source": {
                            "kind": "property",
                            "pset": "Pset_DoorCommon",
                            "property": "FireRating",
                        },
                        "datatype": "text",
                        "editable": True,
                        "write": {
                            "enabled": True,
                            "target": {
                                "kind": "property",
                                "pset": "Pset_DoorCommon",
                                "property": "FireRating",
                            },
                            "create_if_missing": True,
                            "data_type": "IfcLabel",
                        },
                    }
                ],
            },
        ],
    }


def normalize_protocol(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    protocol = dict(payload)
    protocol.setdefault("schema_version", PROTOCOL_SCHEMA_VERSION)
    protocol.setdefault("name", "Untitled IFC Toolkit configuration")
    protocol.setdefault("description", "")
    protocol.setdefault("version", "1.0")
    protocol.setdefault("created_by", "")
    protocol.setdefault("created_date", utc_iso())
    protocol.setdefault("last_modified", utc_iso())
    protocol.setdefault("ifc_schemas", ["IFC2X3", "IFC4", "IFC4X3"])
    protocol.setdefault("entities", [])
    protocol["entities"] = [entity for entity in protocol.get("entities", []) if isinstance(entity, dict)]
    for entity in protocol["entities"]:
        entity.setdefault("enabled", True)
        entity.setdefault("include_subtypes", True)
        entity.setdefault("fields", [])
        entity["fields"] = [field for field in entity.get("fields", []) if isinstance(field, dict)]
        for field in entity["fields"]:
            field.setdefault("id", slugify(field.get("label") or "field"))
            field.setdefault("label", field.get("id", "Field"))
            field.setdefault("datatype", "text")
            field.setdefault("editable", True)
            field.setdefault("source", {"kind": "attribute", "attribute": "Name"})
            field.setdefault("write", {"enabled": False})
    return protocol


def slugify(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "field"


def clean_protocol_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "wrappedValue"):
        return value.wrappedValue
    if isinstance(value, str):
        value = value.strip()
        return value if value else None
    return value


def is_non_empty(value: Any) -> bool:
    return clean_protocol_value(value) is not None


def _iter_entity_instances(model: Any, entity_name: str, include_subtypes: bool) -> List[Any]:
    if not entity_name:
        return []
    try:
        return list(model.by_type(entity_name, include_subtypes=include_subtypes))
    except TypeError:
        return list(model.by_type(entity_name))
    except Exception:
        return []


def _get_type(element: Any) -> Any:
    try:
        return ifcopenshell.util.element.get_type(element)
    except Exception:
        return None


def _safe_get_psets(element: Any) -> Dict[str, Dict[str, Any]]:
    if element is None:
        return {}
    try:
        return ifcopenshell.util.element.get_psets(element) or {}
    except Exception:
        return {}


def _read_pset_value(element: Any, pset_name: str, property_name: str) -> Any:
    if not element or not pset_name or not property_name:
        return None
    psets = _safe_get_psets(element)
    values = psets.get(pset_name) or {}
    if property_name not in values:
        return None
    value = values[property_name]
    if isinstance(value, dict) and "NominalValue" in value:
        return clean_protocol_value(value.get("NominalValue"))
    return clean_protocol_value(value)


def _read_quantity_value(element: Any, qto_name: str, quantity_name: str) -> Any:
    if not element or not qto_name or not quantity_name:
        return None
    for rel in getattr(element, "IsDefinedBy", []) or []:
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        qto = getattr(rel, "RelatingPropertyDefinition", None)
        if not qto or not qto.is_a("IfcElementQuantity"):
            continue
        if getattr(qto, "Name", "") != qto_name:
            continue
        for quantity in getattr(qto, "Quantities", []) or []:
            if getattr(quantity, "Name", "") != quantity_name:
                continue
            for attr in ("LengthValue", "AreaValue", "VolumeValue", "CountValue", "WeightValue", "TimeValue"):
                value = getattr(quantity, attr, None)
                if value is not None:
                    return clean_protocol_value(value)
    psets = _safe_get_psets(element)
    return clean_protocol_value((psets.get(qto_name) or {}).get(quantity_name))


def _classification_label(element: Any, system_name: str, value_key: str = "reference") -> Any:
    if value_key.lower() in {"reference", "identification", "itemreference"}:
        return find_classification_value(element, system_name)
    for rel in getattr(element, "HasAssociations", []) or []:
        if not rel.is_a("IfcRelAssociatesClassification"):
            continue
        ref = getattr(rel, "RelatingClassification", None)
        source = getattr(ref, "ReferencedSource", None) if ref else None
        source_name = getattr(source, "Name", "") or getattr(ref, "Name", "") if ref else ""
        if source_name and source_name.strip().lower() == system_name.strip().lower():
            return getattr(ref, "Name", None)
    return None


def _relationship_value(element: Any, relationship: str, attribute: str) -> Any:
    rel_name = (relationship or "").strip().lower()
    attr_name = attribute or "Name"
    if rel_name in {"type", "relating_type"}:
        type_obj = _get_type(element)
        return clean_protocol_value(getattr(type_obj, attr_name, None)) if type_obj else None
    if rel_name in {"container", "spatial_container"}:
        try:
            container = ifcopenshell.util.element.get_container(element)
        except Exception:
            container = None
        return clean_protocol_value(getattr(container, attr_name, None)) if container else None
    if rel_name in {"system", "assigned_system"}:
        file_obj = getattr(getattr(element, "wrapped_data", None), "file", None)
        if not file_obj:
            return None
        for rel in file_obj.by_type("IfcRelAssignsToGroup"):
            group = getattr(rel, "RelatingGroup", None)
            if group and group.is_a("IfcSystem") and element in (getattr(rel, "RelatedObjects", []) or []):
                return clean_protocol_value(getattr(group, attr_name, None))
    if rel_name in {"aggregate_parent", "decomposes"}:
        for rel in getattr(element, "Decomposes", []) or []:
            parent = getattr(rel, "RelatingObject", None)
            if parent:
                return clean_protocol_value(getattr(parent, attr_name, None))
    return None


def read_source(model: Any, element: Any, source: Dict[str, Any], row_values: Optional[Dict[str, Any]] = None) -> Any:
    if not isinstance(source, dict):
        return None
    kind = str(source.get("kind") or "attribute").strip().lower()
    if kind == "attribute":
        return clean_protocol_value(getattr(element, source.get("attribute") or "", None))
    if kind in {"type_attribute", "typeattribute"}:
        type_obj = _get_type(element)
        return clean_protocol_value(getattr(type_obj, source.get("attribute") or "", None)) if type_obj else None
    if kind in {"property", "occurrence_property"}:
        return _read_pset_value(element, source.get("pset") or "", source.get("property") or "")
    if kind in {"type_property", "typeproperty"}:
        return _read_pset_value(_get_type(element), source.get("pset") or "", source.get("property") or "")
    if kind == "quantity":
        return _read_quantity_value(element, source.get("qto") or source.get("pset") or "", source.get("quantity") or source.get("property") or "")
    if kind in {"classification", "class"}:
        return clean_protocol_value(_classification_label(element, source.get("system") or source.get("classification_system") or "", source.get("value") or "reference"))
    if kind in {"predefined_type", "predefinedtype"}:
        return clean_protocol_value(getattr(element, "PredefinedType", None))
    if kind in {"constant", "default"}:
        return clean_protocol_value(source.get("value", source.get("default")))
    if kind in {"relationship", "relation"}:
        return _relationship_value(element, source.get("relationship") or source.get("name") or "", source.get("attribute") or "Name")
    if kind in {"first_non_empty", "fallback"}:
        for item in source.get("sources") or []:
            value = read_source(model, element, item, row_values=row_values)
            if is_non_empty(value):
                return value
        return None
    if kind in {"concat", "concatenation", "calculated"}:
        separator = str(source.get("separator", ""))
        parts = []
        for item in source.get("parts") or source.get("sources") or []:
            value = read_source(model, element, item, row_values=row_values)
            if is_non_empty(value):
                parts.append(str(clean_protocol_value(value)))
        if parts:
            return separator.join(parts)
        expression = source.get("expression")
        if expression and row_values:
            result = str(expression)
            for key, value in row_values.items():
                result = result.replace("{" + str(key) + "}", "" if value is None else str(value))
            return clean_protocol_value(result)
    return None


def read_field(model: Any, element: Any, field: Dict[str, Any], row_values: Optional[Dict[str, Any]] = None) -> Any:
    sources = field.get("sources")
    if isinstance(sources, list) and sources:
        for source in sources:
            value = read_source(model, element, source, row_values=row_values)
            if is_non_empty(value):
                return value
        return None
    return read_source(model, element, field.get("source") or {}, row_values=row_values)


def _field_column(field: Dict[str, Any]) -> str:
    return str(field.get("label") or field.get("id") or "Field")


def _entity_config_matches(element: Any, entity_cfg: Dict[str, Any]) -> bool:
    entity_name = str(entity_cfg.get("entity") or "").strip()
    if not entity_name:
        return False
    if element.is_a() == entity_name:
        return True
    if not entity_cfg.get("include_subtypes", True):
        return False
    try:
        return bool(element.is_a(entity_name))
    except Exception:
        return False


def extract_protocol_to_dataframe(model: Any, protocol_payload: Dict[str, Any], source_file_name: str = "") -> pd.DataFrame:
    protocol = normalize_protocol(protocol_payload)
    if not protocol:
        return pd.DataFrame(columns=BASE_PROTOCOL_COLUMNS)

    enabled_configs = [
        entity_cfg
        for entity_cfg in protocol.get("entities", [])
        if entity_cfg.get("enabled") is not False
    ]
    elements_by_key: Dict[str, Any] = {}
    for entity_cfg in enabled_configs:
        entity_name = str(entity_cfg.get("entity") or "").strip()
        for element in _iter_entity_instances(model, entity_name, bool(entity_cfg.get("include_subtypes", True))):
            global_id = str(getattr(element, "GlobalId", "") or "")
            if global_id:
                elements_by_key.setdefault(global_id, element)

    rows: List[Dict[str, Any]] = []
    for element in sorted(elements_by_key.values(), key=lambda item: item.id()):
        matching_configs = [entity_cfg for entity_cfg in enabled_configs if _entity_config_matches(element, entity_cfg)]
        row: Dict[str, Any] = {
            "GlobalId": getattr(element, "GlobalId", ""),
            "StepId": element.id(),
            "IFC Entity": element.is_a(),
            "Protocol Entity": " | ".join(str(item.get("entity") or "") for item in matching_configs),
            "SourceFile": source_file_name,
        }
        for entity_cfg in matching_configs:
            for field in entity_cfg.get("fields") or []:
                row[_field_column(field)] = read_field(model, element, field, row_values=row)
        rows.append(row)
    columns = list(BASE_PROTOCOL_COLUMNS)
    for entity_cfg in enabled_configs:
        for field in entity_cfg.get("fields", []) or []:
            column = _field_column(field)
            if column not in columns:
                columns.append(column)
    return pd.DataFrame(rows, columns=columns)


def protocol_fields_to_dataframe(protocol_payload: Dict[str, Any]) -> pd.DataFrame:
    protocol = normalize_protocol(protocol_payload)
    rows: List[Dict[str, Any]] = []
    if not protocol:
        return pd.DataFrame()
    for entity_cfg in protocol.get("entities", []):
        for field in entity_cfg.get("fields", []) or []:
            source = field.get("source") or {}
            write = field.get("write") or {}
            target = write.get("target") or source
            rows.append(
                {
                    "Entity": entity_cfg.get("entity", ""),
                    "Output Column": field.get("label", ""),
                    "IFC Source": source_summary(source),
                    "Write Target": source_summary(target) if write.get("enabled") else "",
                    "Data Type": field.get("datatype", "text"),
                    "Editable": bool(field.get("editable", True)),
                    "Write Back": bool(write.get("enabled", False)),
                    "Required": bool(field.get("required", False)),
                    "Allowed Values": ", ".join(str(v) for v in (field.get("allowed_values") or [])),
                }
            )
    return pd.DataFrame(rows)


def protocol_to_workbook_dataframe(protocol_payload: Dict[str, Any]) -> pd.DataFrame:
    text = json.dumps(normalize_protocol(protocol_payload) or {}, ensure_ascii=False, indent=2)
    chunks = [text[index : index + 30000] for index in range(0, len(text), 30000)] or [""]
    return pd.DataFrame({"Chunk": chunks})


def protocol_from_workbook(excel: Any) -> Optional[Dict[str, Any]]:
    try:
        if PROTOCOL_CONFIG_SHEET not in excel.sheet_names:
            return None
        df = pd.read_excel(excel, PROTOCOL_CONFIG_SHEET)
        if "Chunk" not in df.columns:
            return None
        text = "".join(str(value) for value in df["Chunk"].tolist())
        return normalize_protocol(json.loads(text))
    except Exception:
        return None


def source_summary(source: Dict[str, Any]) -> str:
    if not isinstance(source, dict):
        return ""
    kind = str(source.get("kind") or "").strip().lower()
    if kind == "attribute":
        return f"Attribute.{source.get('attribute', '')}"
    if kind in {"type_attribute", "typeattribute"}:
        return f"Type.{source.get('attribute', '')}"
    if kind in {"property", "occurrence_property"}:
        return f"{source.get('pset', '')}.{source.get('property', '')}"
    if kind in {"type_property", "typeproperty"}:
        return f"Type.{source.get('pset', '')}.{source.get('property', '')}"
    if kind == "quantity":
        return f"{source.get('qto') or source.get('pset', '')}.{source.get('quantity') or source.get('property', '')}"
    if kind == "classification":
        return f"Classification[{source.get('system') or source.get('classification_system', '')}]"
    if kind in {"predefined_type", "predefinedtype"}:
        return "PredefinedType"
    if kind in {"constant", "default"}:
        return f"Constant:{source.get('value', source.get('default', ''))}"
    if kind in {"first_non_empty", "fallback"}:
        return "First non-empty: " + " > ".join(source_summary(item) for item in source.get("sources") or [])
    if kind in {"concat", "concatenation", "calculated"}:
        return "Calculated"
    if kind in {"relationship", "relation"}:
        return f"Relationship.{source.get('relationship') or source.get('name', '')}.{source.get('attribute', 'Name')}"
    return kind


def _owner_history(model: Any) -> Any:
    for owner_history in model.by_type("IfcOwnerHistory"):
        return owner_history
    try:
        return model.create_entity("IfcOwnerHistory")
    except Exception:
        return None


def _property_rels(element: Any) -> Iterable[Any]:
    try:
        for rel in getattr(element, "IsDefinedBy", []) or []:
            yield rel
    except Exception:
        return


def _find_pset_entity(element: Any, pset_name: str) -> Any:
    for rel in _property_rels(element):
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        pset = getattr(rel, "RelatingPropertyDefinition", None)
        if pset and pset.is_a("IfcPropertySet") and getattr(pset, "Name", "") == pset_name:
            return pset
    return None


def _ensure_pset(model: Any, element: Any, pset_name: str, create_missing: bool) -> Any:
    pset = _find_pset_entity(element, pset_name)
    if pset or not create_missing:
        return pset
    try:
        return ifcopenshell.api.run("pset.add_pset", model, product=element, name=pset_name)
    except Exception:
        owner_history = _owner_history(model)
        kwargs = {
            "GlobalId": new_guid(),
            "Name": pset_name,
            "HasProperties": [],
        }
        if owner_history is not None:
            kwargs["OwnerHistory"] = owner_history
        pset = model.create_entity("IfcPropertySet", **kwargs)
        rel_kwargs = {
            "GlobalId": new_guid(),
            "RelatingPropertyDefinition": pset,
            "RelatedObjects": [element],
        }
        if owner_history is not None:
            rel_kwargs["OwnerHistory"] = owner_history
        model.create_entity("IfcRelDefinesByProperties", **rel_kwargs)
        return pset


def _coerce_property_value(model: Any, value: Any, data_type: str = "IfcLabel") -> Any:
    if hasattr(value, "is_a"):
        return value
    type_name = data_type or "IfcLabel"
    if type_name.lower() in {"text", "string"}:
        type_name = "IfcLabel"
    if type_name.lower() in {"number", "real", "float"}:
        type_name = "IfcReal"
    if type_name.lower() in {"integer", "int"}:
        type_name = "IfcInteger"
    if type_name.lower() in {"boolean", "bool"}:
        type_name = "IfcBoolean"
    try:
        if type_name in {"IfcReal", "IfcLengthMeasure", "IfcAreaMeasure", "IfcVolumeMeasure"}:
            return model.create_entity(type_name, float(value or 0.0))
        if type_name == "IfcInteger":
            return model.create_entity(type_name, int(float(value or 0)))
        if type_name == "IfcBoolean":
            if isinstance(value, str):
                value = value.strip().lower() in {"1", "yes", "true", "y"}
            return model.create_entity(type_name, bool(value))
        return model.create_entity(type_name, "" if value is None else str(value))
    except Exception:
        return model.create_entity("IfcLabel", "" if value is None else str(value))


def _set_property_value(model: Any, element: Any, source: Dict[str, Any], value: Any, write_cfg: Dict[str, Any]) -> Tuple[Any, Any, str]:
    pset_name = source.get("pset") or "Pset_Custom"
    property_name = source.get("property") or "Value"
    create_missing = bool(
        write_cfg.get("create_if_missing")
        or write_cfg.get("create_pset_if_missing")
        or write_cfg.get("create_property_if_missing")
    )
    old_value = _read_pset_value(element, pset_name, property_name)
    pset = _ensure_pset(model, element, pset_name, create_missing=create_missing)
    if pset is None:
        return old_value, old_value, "Skipped: target property set missing"
    prop = None
    for existing in getattr(pset, "HasProperties", []) or []:
        if getattr(existing, "Name", "") == property_name:
            prop = existing
            break
    if prop is None and not create_missing:
        return old_value, old_value, "Skipped: target property missing"
    try:
        if prop is None:
            prop = model.create_entity(
                "IfcPropertySingleValue",
                Name=property_name,
                NominalValue=_coerce_property_value(model, value, write_cfg.get("data_type") or write_cfg.get("datatype") or "IfcLabel"),
            )
            pset.HasProperties = list(getattr(pset, "HasProperties", []) or []) + [prop]
        elif prop.is_a("IfcPropertySingleValue"):
            prop.NominalValue = _coerce_property_value(model, value, write_cfg.get("data_type") or write_cfg.get("datatype") or "IfcLabel")
        else:
            try:
                ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={property_name: value})
            except Exception:
                return old_value, old_value, f"Skipped: unsupported property type {prop.is_a()}"
        return old_value, value, "Applied"
    except Exception as exc:
        return old_value, old_value, f"Rejected: {exc}"


def _find_qto_entity(element: Any, qto_name: str) -> Any:
    for rel in _property_rels(element):
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        qto = getattr(rel, "RelatingPropertyDefinition", None)
        if qto and qto.is_a("IfcElementQuantity") and getattr(qto, "Name", "") == qto_name:
            return qto
    return None


def _ensure_qto(model: Any, element: Any, qto_name: str, create_missing: bool) -> Any:
    qto = _find_qto_entity(element, qto_name)
    if qto or not create_missing:
        return qto
    try:
        return ifcopenshell.api.run("pset.add_qto", model, product=element, name=qto_name)
    except Exception:
        owner_history = _owner_history(model)
        kwargs = {"GlobalId": new_guid(), "Name": qto_name, "Quantities": []}
        if owner_history is not None:
            kwargs["OwnerHistory"] = owner_history
        qto = model.create_entity("IfcElementQuantity", **kwargs)
        rel_kwargs = {"GlobalId": new_guid(), "RelatingPropertyDefinition": qto, "RelatedObjects": [element]}
        if owner_history is not None:
            rel_kwargs["OwnerHistory"] = owner_history
        model.create_entity("IfcRelDefinesByProperties", **rel_kwargs)
        return qto


def _quantity_entity_name(quantity_name: str) -> Tuple[str, str]:
    lower = quantity_name.lower()
    if "area" in lower:
        return "IfcQuantityArea", "AreaValue"
    if "volume" in lower:
        return "IfcQuantityVolume", "VolumeValue"
    if "count" in lower or "number" in lower:
        return "IfcQuantityCount", "CountValue"
    return "IfcQuantityLength", "LengthValue"


def _set_quantity_value(model: Any, element: Any, source: Dict[str, Any], value: Any, write_cfg: Dict[str, Any]) -> Tuple[Any, Any, str]:
    qto_name = source.get("qto") or source.get("pset") or "BaseQuantities"
    quantity_name = source.get("quantity") or source.get("property") or "Quantity"
    create_missing = bool(write_cfg.get("create_if_missing"))
    old_value = _read_quantity_value(element, qto_name, quantity_name)
    qto = _ensure_qto(model, element, qto_name, create_missing=create_missing)
    if qto is None:
        return old_value, old_value, "Skipped: target quantity set missing"
    quantity = None
    for existing in getattr(qto, "Quantities", []) or []:
        if getattr(existing, "Name", "") == quantity_name:
            quantity = existing
            break
    entity_name, value_attr = _quantity_entity_name(quantity_name)
    try:
        numeric_value = float(value or 0.0)
    except Exception:
        return old_value, old_value, "Rejected: quantity value is not numeric"
    if quantity is None:
        if not create_missing:
            return old_value, old_value, "Skipped: target quantity missing"
        quantity = model.create_entity(entity_name, Name=quantity_name, **{value_attr: numeric_value})
        qto.Quantities = list(getattr(qto, "Quantities", []) or []) + [quantity]
    else:
        target_attr = next(
            (attr for attr in ("LengthValue", "AreaValue", "VolumeValue", "CountValue", "WeightValue", "TimeValue") if hasattr(quantity, attr)),
            value_attr,
        )
        setattr(quantity, target_attr, numeric_value)
    return old_value, numeric_value, "Applied"


def write_source(model: Any, element: Any, source: Dict[str, Any], value: Any, write_cfg: Dict[str, Any]) -> Tuple[Any, Any, str]:
    if not isinstance(source, dict):
        return None, None, "Skipped: target missing"
    kind = str(source.get("kind") or "attribute").strip().lower()
    if kind == "attribute":
        attr = source.get("attribute") or ""
        old_value = clean_protocol_value(getattr(element, attr, None))
        if not attr or not hasattr(element, attr):
            return old_value, old_value, "Skipped: target attribute missing"
        setattr(element, attr, "" if value is None else value)
        return old_value, value, "Applied"
    if kind in {"type_attribute", "typeattribute"}:
        type_obj = _get_type(element)
        if type_obj is None:
            return None, None, "Skipped: type object missing"
        attr = source.get("attribute") or ""
        old_value = clean_protocol_value(getattr(type_obj, attr, None))
        if not attr or not hasattr(type_obj, attr):
            return old_value, old_value, "Skipped: target type attribute missing"
        setattr(type_obj, attr, "" if value is None else value)
        return old_value, value, "Applied"
    if kind in {"property", "occurrence_property"}:
        return _set_property_value(model, element, source, value, write_cfg)
    if kind in {"type_property", "typeproperty"}:
        type_obj = _get_type(element)
        if type_obj is None:
            return None, None, "Skipped: type object missing"
        return _set_property_value(model, type_obj, source, value, write_cfg)
    if kind == "quantity":
        return _set_quantity_value(model, element, source, value, write_cfg)
    if kind in {"classification", "class"}:
        system_name = source.get("system") or source.get("classification_system") or "Classification"
        old_value = find_classification_value(element, system_name)
        try:
            attach_classification(model, element, system_name, "" if value is None else str(value))
            return old_value, value, "Applied"
        except Exception as exc:
            return old_value, old_value, f"Rejected: {exc}"
    if kind in {"predefined_type", "predefinedtype"}:
        old_value = clean_protocol_value(getattr(element, "PredefinedType", None))
        if not hasattr(element, "PredefinedType"):
            return old_value, old_value, "Skipped: target has no PredefinedType"
        setattr(element, "PredefinedType", "" if value is None else str(value))
        return old_value, value, "Applied"
    return None, None, f"Skipped: target kind {kind} is read-only"


def _fields_for_entity(protocol: Dict[str, Any], protocol_entity: str, element: Any) -> List[Dict[str, Any]]:
    configured_entities = {item.strip() for item in protocol_entity.split("|") if item.strip()}
    fields_by_column: Dict[str, Dict[str, Any]] = {}
    for entity_cfg in protocol.get("entities", []) or []:
        if entity_cfg.get("enabled") is False:
            continue
        entity_name = str(entity_cfg.get("entity") or "")
        if entity_name not in configured_entities and not _entity_config_matches(element, entity_cfg):
            continue
        for field in entity_cfg.get("fields") or []:
            fields_by_column[_field_column(field)] = field
    return list(fields_by_column.values())


def apply_protocol_workbook(model: Any, excel: Any) -> List[Dict[str, Any]]:
    protocol = protocol_from_workbook(excel)
    if not protocol or PROTOCOL_DATA_SHEET not in excel.sheet_names:
        return []
    data_df = pd.read_excel(excel, PROTOCOL_DATA_SHEET, usecols=lambda c: c is not None)
    if data_df.empty:
        return []
    change_rows: List[Dict[str, Any]] = []
    for _, row in data_df.iterrows():
        guid = clean_protocol_value(row.get("GlobalId"))
        if not guid:
            continue
        try:
            element = model.by_guid(str(guid))
        except Exception:
            element = None
        if element is None:
            continue
        fields = _fields_for_entity(protocol, str(clean_protocol_value(row.get("Protocol Entity")) or ""), element)
        for field in fields:
            write_cfg = field.get("write") or {}
            if not field.get("editable", True) or not write_cfg.get("enabled"):
                continue
            column = _field_column(field)
            if column not in data_df.columns:
                continue
            new_value = clean_protocol_value(row.get(column))
            if new_value is None:
                continue
            target = write_cfg.get("target") or field.get("source") or {}
            old_value, applied_value, status = write_source(model, element, target, new_value, write_cfg)
            if status == "Applied" and str(clean_protocol_value(old_value) or "") == str(clean_protocol_value(applied_value) or ""):
                continue
            change_rows.append(
                {
                    "RowKey": f"{guid}:{column}",
                    "GlobalId": guid,
                    "StepId": element.id(),
                    "Status": status,
                    "Message": f"Protocol field '{column}'",
                    "Field": column,
                    "FromValue": old_value,
                    "ToValue": applied_value,
                    "Protocol": protocol.get("name", ""),
                    "ProtocolVersion": protocol.get("version", ""),
                }
            )
    return change_rows
