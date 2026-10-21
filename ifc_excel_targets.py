"""Association targets shared by extraction, planning, mutation and verification."""
import ifcopenshell.util.element as util
from ifcopenshell.guid import new as new_guid

FLOOR_SYSTEM = "COBie Floors"
# Repository QA template: IfcBuildingStorey-, COBie_FloorAttributes.
FLOOR_PROPERTIES = ("GrossExternalArea", "GrossInternalArea", "HeightToHaunch",
                    "NetInternalArea", "RoofFloorArea", "TotalInternalWallArea")
FLOOR_REFERENCE = "COBie Floors Classification Reference"
FLOOR_NAME = "COBie Floors Classification Name"


def material_assignment(element):
    owner = element
    rels = [r for r in (getattr(owner, "HasAssociations", ()) or ()) if r.is_a("IfcRelAssociatesMaterial")]
    scope = "occurrence" if not element.is_a("IfcTypeObject") else "type"
    if not rels and not element.is_a("IfcTypeObject"):
        owner = util.get_type(element)
        rels = [r for r in (getattr(owner, "HasAssociations", ()) or ()) if r.is_a("IfcRelAssociatesMaterial")]
        scope = "type" if rels else "occurrence"
    if not rels:
        return element, None, scope, "unassigned", ""
    mat = rels[0].RelatingMaterial
    kind = mat.is_a() if len(rels) == 1 else "multiple associations"
    if kind == "IfcMaterial":
        label = mat.Name or ""
    else:
        names = ", ".join(m.Name or "(unnamed)" for m in util.get_materials(element))
        label = f"[{kind}: {names}; reference only]"
    return owner, mat, scope, kind, label


def floor_reference(element):
    for rel in getattr(element, "HasAssociations", ()) or ():
        if not rel.is_a("IfcRelAssociatesClassification"):
            continue
        ref = rel.RelatingClassification
        if ref.is_a("IfcClassificationReference") and (getattr(getattr(ref, "ReferencedSource", None), "Name", None) == FLOOR_SYSTEM or ref.Name == FLOOR_SYSTEM):
            return ref
    return None


def read_association(element, target):
    if target["kind"] == "entity":
        return element.is_a()
    if target["kind"] == "material":
        return material_assignment(element)[4]
    ref = floor_reference(element)
    attr = "Name" if target.get("attribute") == "Name" else ("Identification" if ref is not None and hasattr(ref, "Identification") else "ItemReference")
    return getattr(ref, attr, None) if ref is not None else None


def detach(model, owner, relationship):
    remaining = [o for o in relationship.RelatedObjects if o != owner]
    if remaining:
        relationship.RelatedObjects = remaining
    else:
        model.remove(relationship)


def write_association(model, element, target, value):
    old = read_association(element, target)
    kind = target["kind"]
    if kind == "entity":
        import ifcopenshell.util.schema
        ifcopenshell.util.schema.reassign_class(model, element, str(value))
    elif kind == "material":
        owner, mat, _, assignment_kind, _ = material_assignment(element)
        if assignment_kind not in {"IfcMaterial", "unassigned"}:
            return old, old, "Rejected: structured materials are reference only"
        # A new definition protects every unrelated user of a shared material.
        replacement = util.copy(model, mat) if mat else model.create_entity("IfcMaterial")
        replacement.Name = str(value)
        if mat:
            for inverse in model.get_inverse(mat):
                if inverse.is_a("IfcMaterialDefinitionRepresentation"):
                    copied = util.copy(model, inverse)
                    copied.RepresentedMaterial = replacement
                elif inverse.is_a("IfcMaterialProperties") and hasattr(inverse, "Material"):
                    copied = util.copy_deep(model, inverse, exclude=["IfcMaterial"])
                    copied.Material = replacement
        for rel in list(getattr(owner, "HasAssociations", ()) or ()):
            if rel.is_a("IfcRelAssociatesMaterial"):
                detach(model, owner, rel)
        model.create_entity("IfcRelAssociatesMaterial", GlobalId=new_guid(),
                            OwnerHistory=getattr(owner, "OwnerHistory", None),
                            RelatedObjects=[owner], RelatingMaterial=replacement)
    else:
        ref = floor_reference(element)
        system = getattr(ref, "ReferencedSource", None) if ref else None
        if system is None:
            system = next((s for s in model.by_type("IfcClassification") if s.Name == FLOOR_SYSTEM), None)
        if system is None:
            system = model.create_entity("IfcClassification", Name=FLOOR_SYSTEM)
        replacement = util.copy(model, ref) if ref else model.create_entity("IfcClassificationReference", ReferencedSource=system)
        attr = "Name" if target.get("attribute") == "Name" else ("Identification" if hasattr(replacement, "Identification") else "ItemReference")
        setattr(replacement, attr, str(value))
        for rel in list(getattr(element, "HasAssociations", ()) or ()):
            if rel.is_a("IfcRelAssociatesClassification") and ref is not None and rel.RelatingClassification == ref:
                detach(model, element, rel)
        model.create_entity("IfcRelAssociatesClassification", GlobalId=new_guid(),
                            OwnerHistory=getattr(element, "OwnerHistory", None),
                            RelatedObjects=[element], RelatingClassification=replacement)
    return old, value, "Applied"
