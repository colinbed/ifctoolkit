"""Batch planning for Excel-to-IFC class changes.

The planner is deliberately independent of IFC mutation.  In particular, a
repository or inference implementation can only be called while constructing
the plan; consumers of :class:`MappingPlanRow` need no external services.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, Sequence


SIGNATURE_FIELDS = (
    "source_entity", "requested_entity", "predefined_type", "object_type",
    "type_global_id", "type_name", "classification_system",
    "classification_reference", "mapping_properties",
)


@dataclass(frozen=True)
class MappingSignature:
    value: str


@dataclass
class MappingPlanRow:
    row_index: Any
    global_id: str
    source_entity: str
    explicit_target_entity: Optional[str]
    mapping_signature: MappingSignature
    resolved_target_entity: str
    resolution_source: str
    predefined_type: str = ""


class ProxyMappingRepository(Protocol):
    def fetch_bulk(self, signatures: Sequence[str], *, scope: Mapping[str, str]) -> Mapping[str, str]: ...
    def upsert_bulk(self, mappings: Sequence[Mapping[str, str]], *, scope: Mapping[str, str]) -> None: ...


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:  # NaN
            return ""
    except Exception:
        pass
    return str(value).strip()


def _first(row: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = _text(row.get(name))
        if value:
            return value
    return ""


def mapping_signature(row: Mapping[str, Any], source_entity: str) -> MappingSignature:
    requested = _first(row, "TargetEntity", "ExtObject", "IFC_Enumeration")
    values = {
        "source_entity": source_entity,
        "requested_entity": requested,
        "predefined_type": _first(row, "TargetPredefinedType", "CurrentPredefinedType"),
        "object_type": _first(row, "OccurrenceType", "ObjectType"),
        "type_global_id": _text(row.get("TypeGlobalId")),
        "type_name": _text(row.get("TypeName")),
        "classification_system": _text(row.get("ClassificationSystem")),
        "classification_reference": _text(row.get("ClassificationReference")),
        "mapping_properties": _text(row.get("MappingProperties")),
    }
    return MappingSignature(json.dumps(values, sort_keys=True, separators=(",", ":")))


def build_mapping_plan(
    rows: Iterable[tuple[Any, Mapping[str, Any], Any]],
    *,
    entity_is_valid: Callable[[str], bool],
    predefined_is_valid: Callable[[str, str], bool],
    infer: Callable[[MappingSignature, Mapping[str, Any]], Optional[str]],
    repository: Optional[ProxyMappingRepository] = None,
    scope: Optional[Mapping[str, str]] = None,
    page_size: int = 500,
) -> tuple[list[MappingPlanRow], dict[str, Any]]:
    """Resolve all rows before IFC mutation and return plan plus metrics."""
    started = time.monotonic()
    prepared = []
    validation_cache: dict[tuple[str, str], bool] = {}

    def valid(entity: str, predef: str) -> bool:
        key = (entity, predef)
        if key not in validation_cache:
            validation_cache[key] = entity_is_valid(entity) and predefined_is_valid(entity, predef)
        return validation_cache[key]

    for index, row, element in rows:
        source = element.is_a()
        signature = mapping_signature(row, source)
        explicit = _first(row, "TargetEntity", "ExtObject", "IFC_Enumeration") or None
        predef = _first(row, "TargetPredefinedType", "CurrentPredefinedType")
        prepared.append((index, row, element, source, signature, explicit, predef))

    unresolved = {item[4].value for item in prepared if not (item[5] and valid(item[5], item[6]))}
    persisted: dict[str, str] = {}
    if repository:
        keys = sorted(unresolved)
        for start in range(0, len(keys), max(1, page_size)):
            persisted.update(repository.fetch_bulk(keys[start:start + page_size], scope=scope or {}))

    signature_results: dict[str, tuple[str, str]] = {}
    new_mappings: dict[str, str] = {}
    result: list[MappingPlanRow] = []
    counts = {name: 0 for name in ("explicit_excel_entity", "existing_proxy_mapping", "signature_cache", "inferred_mapping", "unchanged_fallback")}
    for index, row, _element, source, signature, explicit, predef in prepared:
        if explicit and valid(explicit, predef):
            target, resolution = explicit, "explicit_excel_entity"
        elif signature.value in signature_results:
            target, _original = signature_results[signature.value]
            resolution = "signature_cache"
        else:
            saved = persisted.get(signature.value)
            if saved and valid(saved, predef):
                target, resolution = saved, "existing_proxy_mapping"
            else:
                candidate = infer(signature, row)
                if candidate and valid(candidate, predef):
                    target, resolution = candidate, "inferred_mapping"
                    new_mappings[signature.value] = candidate
                else:
                    target, resolution = source, "unchanged_fallback"
            signature_results[signature.value] = (target, resolution)
        counts[resolution] += 1
        result.append(MappingPlanRow(index, _text(row.get("GlobalId")), source, explicit,
                                     signature, target, resolution, predef))

    if repository and new_mappings:
        repository.upsert_bulk(
            [{"signature": key, "target_entity": value} for key, value in sorted(new_mappings.items())],
            scope=scope or {},
        )
    metrics = {"rows": len(result), "unique_signatures": len({r.mapping_signature.value for r in result}),
               **counts, "elapsed_s": time.monotonic() - started}
    return result, metrics
