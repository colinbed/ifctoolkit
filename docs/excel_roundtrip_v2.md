# Excel / IFC round trip, workbook version 2.0

Exports retain the model's exact `schema_identifier`. Imports resolve existing
objects by GlobalId, never names, row positions or STEP IDs. New exports remove
redundant editable aliases. Grey headers with comments mark read-only reference
columns. Blank cells preserve source values; deletion is not represented by a
blank cell in this workflow.

`Elements` edits native occurrence Name, Description and ObjectType. Its Type
Name and Type Description resolve through `IfcRelDefinesByType.RelatingType`.
`Types` exports one row per assigned type object, with native Name, Description,
class, PredefinedType and ElementType, plus a reference assignment count. A
Predefined Type field without a native attribute is blank and explicitly marked
unavailable; it never substitutes a type's effective/inherited value.

`BuildingStoreys` exports every existing IfcBuildingStorey, including models
exported with an Elements class filter. Name, Description and Long Name write to
the storey. Building GlobalId and Elevation are reference-only. No storey edits
propagate to contained components. Shared property definitions are copied before
a target-specific property edit.

## COBie floor mapping

The repository contains different floor representations. They remain distinct:

| Workbook field | IFC target / source |
| --- | --- |
| `COBie_Floor.Category` | Explicit property representation: `IfcPropertySet.Name=COBie_Floor`, property `Category`. The Category field comes from the Floor template in `config/cobie/sheets.json`; the template does **not** define an IFC property-set mapping. This v2 mapping is an explicit new convention, not an existing native IFC attribute. |
| `COBie_FloorAttributes.GrossExternalArea`, `GrossInternalArea`, `HeightToHaunch`, `NetInternalArea`, `RoofFloorArea`, `TotalInternalWallArea` | Exact property names and property-set spelling from `backend/ifc_qa/reference/ifc_qa_config.default.json`, IfcBuildingStorey mapping. |
| Other existing `COBie_Floor.*` / `COBie_FloorAttributes.*` fields | Discovered by exact property-set/property names on storeys and exported without renaming. |
| `Classification: COBie Floors Reference`, `Classification: COBie Floors Name` | Separate `IfcRelAssociatesClassification` association, referencing the `COBie Floors` classification system used by DfE check 04.07 and the existing storey editor. Reference uses IFC2X3 ItemReference or IFC4+ Identification. |

Missing floor property sets and properties can be created independently of the
legacy Add new switch. Existing numeric properties retain their IFC measure
types. Classification edits detach only the edited storey from a shared relation
and use a new reference, preserving other objects. A classification reference is
never substituted for a floor property. The workbook Instructions sheet describes
these conventions.

## Planning and shared types

The existing `WorkbookWritePlan` resolver and approved type-splitting implementation
are reused. Native/property/material proposals are compared to the source IFC
before mutation and grouped by canonical GlobalId plus actual IFC target. The
hidden writeback baseline is retained across repeated imports so unchanged aliases
cannot undo prior edits. The
same resolution includes editable ProtocolData; unchanged protocol values are
ignored, including fields whose exported source differs from their write target.
Conflicting explicit aliases or shared-type requests produce structured,
grouped worksheet/row/field errors before output is written. Consistent shared
edits update the original type and affect its assigned instances.

The UI's **Split shared types** checkbox sends the explicit boolean
`split_shared_types: true` through the existing job endpoint to the importer.
The Python API also accepts that keyword. Differing per-instance type names then form separate assigned type groups;
other attribute/property/simple-material edits follow the instance's group.
Unedited instances keep the original type. Original occurrence GUIDs and
geometric references remain intact. A
workbook cell cannot grant splitting. Conflicts involving an explicit Types row
still require consistent edits. Type-class conversions are edited coherently on
Types rather than split through per-instance class requests.

Version 1 header aliases remain accepted, including `OccurrenceName`, `TypeName`,
`TargetEntity` and `TargetPredefinedType`. The latter two retain the
`ApplyChange=Yes` gate. Legacy `TypeDescription` was exported from
occurrence.Description and retains that historical meaning; v2 `Type Description`
correctly targets the assigned type. Conflicting explicit aliases are rejected;
an unchanged alias cannot reverse another edit. New Instructions identify version
2.0; workbooks without that metadata are treated as version 1.

## Materials

Material edits change IFC associations, never a replacement Material property.
The exported effective material includes its assignment scope and IFC assignment
class. For simple `IfcMaterial` associations, editing the label detaches only the
actual target from a shared relation and creates/reuses a material by name. It
never renames a shared material entity. With no assignment, an edit creates a
simple occurrence assignment. A type-scoped edit affects all assigned instances
and participates in shared-type conflict planning.

Layer sets/usages, profile sets/usages, constituent sets and material lists are
reference-only. Their structured entities are preserved; edits to the simple cell
are rejected with an explanation. Blank cells do not remove assignments. A
material name is not sufficient to author or modify a structured material graph.

## Schema choices and conversion safety

Visible `SchemaEntities` and `SchemaChoices` worksheets use IfcOpenShell EXPRESS
declarations and enum introspection for the exact input schema. The former
contains schema, category, abstract/selectable status, occurrence/type
correspondences, native PredefinedType availability, per-class enum values,
generation/library version and official documentation links/descriptions. Text
comes from IfcOpenShell's bundled buildingSMART documentation; no runtime web
request is required.

Occurrence and type entity lists are separate. Dropdowns use named ranges and
class-dependent INDIRECT references, including empty ranges for classes without
PredefinedType. There is no combined enum list and no inline-list size limit.
Server validation independently uses the same schema data. Invalid pasted enums
are rejected, never converted to USERDEFINED. USERDEFINED requires the native
ObjectType/ElementType companion where applicable.

Selectable conversion targets are concrete element/type classes with a known
installed occurrence/type correspondence and no extra mandatory construction or
operation attributes that this workbook cannot supply. Such unsupported classes
remain documented in the reference table. Spatial-class conversions are excluded.
Assigned occurrence/type conversions must be compatible. Type-class edits
coherently convert their assigned occurrences; incompatible existing occurrence
enums require explicit valid occurrence Predefined Type edits. Supported
conversion APIs preserve GUIDs and inverse relationships; there is no proxy
fallback.

The importer writes into a temporary directory, reopens the IFC and verifies
requested native values, properties, materials, GUID-resolved type relationships,
exact schema, placements, representations, hierarchy, containment and elevation.
It verifies the original source digest, then atomically publishes the separate
output. Failures leave an existing output untouched. Applied/skipped proposals
are returned and written to ChangeLog; conflicts return structured errors.

## Validation

Validation result: **93 tests passed** with the complete relevant suite run outside the execution sandbox. JavaScript syntax and `git diff --check` also passed.

For a sandbox-compatible focused run:

```sh
python -m pytest tests/test_excel* tests/test_ifc_excel_compat_fixes.py \
  tests/test_session_and_excel_hotfix.py tests/test_ifc_protocol.py \
  -q -k 'not session_route_contract_roundtrip'
```

The separately excluded session-upload test hangs in this execution sandbox
inside an asyncio/AnyIO upload wait, but passes when run outside the sandbox:

```sh
python -m pytest tests/test_session_and_excel_hotfix.py::test_session_route_contract_roundtrip -q
```

It does not exercise Excel extraction or import.
Workbook tests reopen XLSX validation definitions and named ranges. IFC tests
reopen outputs and assert actual native occurrence/type attributes, assigned type
relationships, shared edits/splits, materials, storeys and geometry references.
