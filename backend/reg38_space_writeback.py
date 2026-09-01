"""Safe canonical write-back of reviewed spaces to a separate IFC file."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

import ifcopenshell


def write_reviewed_spaces(source_ifc: str | Path, output_ifc: str | Path,
                          rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Write non-blank working values using the FireTrace IfcSpace mapping.

    The source is opened read-only and the model is written only to ``output_ifc``.
    Looking up existing entities by GlobalId preserves identity, geometry,
    containment and relationships and cannot create duplicate spaces.
    """
    source, output = Path(source_ifc), Path(output_ifc)
    if source.resolve() == output.resolve():
        raise ValueError("Output IFC must be separate from the source IFC.")
    model = ifcopenshell.open(str(source)); updated = missing = 0
    for row in rows:
        guid = str(row.get("ifc_global_id") or "").strip()
        space = model.by_guid(guid) if guid else None
        if space is None or not space.is_a("IfcSpace"):
            missing += 1; continue
        # Preserve-on-blank is intentional for every corresponding IFC value.
        for working, attribute in (("space_number", "Name"), ("name", "LongName"), ("description", "Description")):
            value = row.get(working)
            if value is not None and str(value).strip():
                setattr(space, attribute, str(value))
        updated += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    model.write(str(output))
    return {"updated": updated, "missing": missing}
