# Regulation 38 IFC processing

Uploaded models remain immutable in the private `reg38-evidence` bucket. The web
request creates an `ifc_files` row and a queued `ifc_processing_jobs` row; it does
not parse IFC in the browser or request process.

`python -m backend.reg38_ifc_worker` runs as a separate service using
`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`. Workers claim jobs atomically,
download a temporary read-only source copy, and pass it to
`Regulation38IfcProcessor`. The processor uses IfcOpenShell and emits deterministic,
table-shaped batches. The sink sends at most one PostgREST request per 1,000 rows,
in foreign-key order. A temporary source copy is deleted after each job.

Progress is reported through `current_step` as `UPLOADED`, `VALIDATING_IFC`,
`EXTRACTING_SPATIAL_STRUCTURE`, `EXTRACTING_OBJECTS`, `EXTRACTING_PROPERTIES`,
`EXTRACTING_RELATIONSHIPS`, `SCANNING_FIRE_PROPERTIES`, `PREPARING_PLAN_DATA`, and
`COMPLETE` (or `FAILED`), with a percentage and scan statistics.

## Example model-scan statistics

```json
{
  "buildings": 1,
  "storeys": 4,
  "spaces": 86,
  "ifc_zones": 3,
  "ifc_spatial_zones": 5,
  "fire_safety_spatial_zones": 4,
  "grid_axes": 18,
  "walls": 214,
  "walls_with_detected_fire_rating": 63,
  "doors": 97,
  "doors_with_detected_fire_rating": 41,
  "custom_property_only_fire_findings": 7,
  "conflict_count": 2,
  "unnamed_spaces": 1,
  "ifc_schema": "IFC4"
}
```

Every source object stores its file ID, IFC GlobalId, entity and STEP ID. Property
rows retain property set/name, original value, and occurrence/type/quantity/
classification/attribute scope. Grid-axis provenance is embedded with its parent
grid GlobalId and IFC STEP ID. Fire discoveries preserve the original property
text and provenance; parsing a safe rating never replaces that source text.
