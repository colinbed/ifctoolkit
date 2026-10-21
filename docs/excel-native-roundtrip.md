# Excel native round trip (workbook version 2)

`Instructions` identifies version 2. Native editable columns are grouped at the
start of Elements. GlobalId and Type GlobalId address existing objects; STEP IDs,
labels and row order never identify write targets. Blanks mean preserve, not delete.
Types lists assigned types, once per GUID. Elements Type Name/Description and Types
resolve to the same canonical type attributes. Explicit disagreements, including
legacy aliases or ProtocolData, produce grouped errors before mutation. Unchanged
sources are removed from the write plan before conflict resolution.

Version 1 aliases remain accepted: OccurrenceName/Name → occurrence.Name,
OccurrenceType/ObjectType → occurrence.ObjectType, TypeName → assigned type.Name.
The misleading old **TypeDescription** header retains its historical target,
occurrence.Description. Version 2 **Instance Description** targets occurrence
Description and **Type Description** targets assigned type Description. Legacy
TargetEntity/TargetPredefinedType require ApplyChange=Yes. Invalid values now fail;
they are never converted implicitly to USERDEFINED. Type Name edits retain the
existing explicit `split_shared_types` workflow; other contradictory shared edits
are grouped for correction. A type sheet edit applies to the whole type group.

## BuildingStoreys and COBie

Name, Description and Long Name write directly to the storey's native attributes.
Building and Elevation are reference only. The repository's QA template
`backend/ifc_qa/reference/ifc_qa_config.default.json`, `IfcBuildingStorey-`, defines
**COBie_FloorAttributes** with GrossExternalArea, GrossInternalArea, HeightToHaunch,
NetInternalArea, RoofFloorArea and TotalInternalWallArea. These exact properties
are exported, with creation of missing property sets/properties enabled.
Existing properties in COBie_Floor or COBie_FloorAttributes are also exported under
their original names. No new native attribute named COBie_Floor is introduced.

The repository's Levels editor and DfE model checks separately use the classification
system **COBie Floors**. `COBie Floors Classification Reference` writes
IfcClassificationReference.Identification (IFC4+) or ItemReference (IFC2X3), and
`COBie Floors Classification Name` writes its Name. Missing references are created
and associated with the storey using IfcRelAssociatesClassification. These columns
are explicitly distinct from COBie property columns. Shared classification
references and shared floor property graphs are isolated before editing.

## Materials

Material displays the effective association, plus read-only scope and kind.
Simple IfcMaterial edits replace the association at its existing occurrence or
type scope, with a separate material definition. Type-scope edits affect all
instances assigned to that type and are conflict-checked together. Shared material
names and unrelated associations remain intact. Material representation/property
metadata is retained when copying a simple definition. Unassigned instances can
receive a simple assignment. There is no substitute Material property.

Layer, profile, constituent, material-list, usage and multiple assignments are
reference only; text edits are rejected before writing. Editing those structures
requires a dedicated IFC material editor.

## Schema and class changes

SchemaReference and SchemaChoices are visible. Executable classes, abstractness,
attributes and enums come from the input's exact `schema_identifier` in the
installed IfcOpenShell EXPRESS declarations. Descriptions and documentation links
come from its bundled buildingSMART documentation; documentation never adds an
executable enum. Range-backed names and class-dependent INDIRECT validation work
without macros or network access. Occurrence and type lists are separate.
Concrete element families with no additional unsupported required attributes are
selectable, including classes absent from the source. Unsupported native fields
display Unavailable. Inherited enums are not editable occurrence attributes.

Server validation uses the same schema reference. USERDEFINED requires the schema's
ObjectType/ElementType companion where applicable. Class requests expand into
explicit, coherent assigned-type/member requests, then pass the same conflict
planner. IfcOpenShell's schema reassign primitive (also used by root.reassign_class)
preserves GUIDs and inverse references, without its free-text USERDEFINED fallback.
Conversions that discard populated attributes, need missing required values, or
have ambiguous correspondence are rejected with row context. A retained enum must
be valid in the target class; edit Types and Elements where necessary. A request
on a shared type can affect all of its instances, as stated in Instructions.

The original IFC cannot be the output path. Output is reopened and verified for
requested values, assigned-type GUIDs, geometry, placements, containment and spatial
hierarchy. Applied/skipped/unchanged/conflict/rejected counts come from the plan.
No schema conversion or deployment is performed.
