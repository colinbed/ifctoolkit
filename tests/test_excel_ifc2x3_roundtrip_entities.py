"""IFC2X3 v2 export and v1 workbook import compatibility."""
import ifcopenshell
import pandas as pd
import pytest
from ifcopenshell.guid import new as new_guid
from openpyxl import load_workbook

from app import ExcelWritebackValidationError, extract_to_excel, update_ifc_from_excel


def _build_ifc2x3_model(tmp_path):
    model = ifcopenshell.file(schema="IFC2X3")
    model.create_entity("IfcProject", GlobalId=new_guid(), Name="P", LongName="PN-001")
    proxy = model.create_entity("IfcBuildingElementProxy", GlobalId=new_guid(), Name="ProxyOcc")
    proxy_type = model.create_entity("IfcBuildingElementProxyType", GlobalId=new_guid(), Name="ProxyType", PredefinedType="NOTDEFINED")
    wall_type = model.create_entity("IfcWallType", GlobalId=new_guid(), Name="Unassigned", PredefinedType="NOTDEFINED")
    model.create_entity("IfcRelDefinesByType", GlobalId=new_guid(), RelatedObjects=[proxy], RelatingType=proxy_type)
    return model, proxy, proxy_type, wall_type


def _export(tmp_path):
    model, proxy, proxy_type, wall_type = _build_ifc2x3_model(tmp_path)
    src, xlsx, output = [tmp_path / n for n in ("src.ifc", "out.xlsx", "updated.ifc")]
    model.write(str(src))
    extract_to_excel(str(src), str(xlsx))
    return src, xlsx, output, proxy, proxy_type, wall_type


def _legacy_type_request(xlsx, type_id, **request):
    wb = load_workbook(xlsx)
    ws = wb["Types"]
    ws.delete_rows(1, ws.max_row)
    columns = ["GlobalId", "CurrentEntity", "TargetEntity", "TargetPredefinedType", "ApplyChange", "ObjectType or ElementType"]
    ws.append(columns)
    ws.append([type_id, "IfcBuildingElementProxyType", request.get("entity", "IfcBuildingElementProxyType"), request.get("predefined", "NOTDEFINED"), "Yes", request.get("element_type")])
    wb.save(xlsx); wb.close()


def test_export_includes_only_assigned_types_and_reference_data(tmp_path):
    _, xlsx, _, proxy, proxy_type, wall_type = _export(tmp_path)
    elements = pd.read_excel(xlsx, "Elements")
    types = pd.read_excel(xlsx, "Types")
    assert proxy.GlobalId in set(elements["GlobalId"])
    assert proxy_type.GlobalId in set(types["Type GlobalId"])
    assert wall_type.GlobalId not in set(types["Type GlobalId"])
    assert {"Instance Name", "Instance Description", "Type Name", "Type Description", "IFC Entity", "Predefined Type", "Type GlobalId"}.issubset(elements.columns)
    assert "TypeDescription" not in elements.columns
    assert "RawStepLine" in pd.read_excel(xlsx, "RawEntities").columns


def test_workbook_lookup_validations_roundtrip_safe(tmp_path):
    _, xlsx, _, _, _, _ = _export(tmp_path)
    wb = load_workbook(xlsx)
    assert {"SchemaEntities", "SchemaChoices", "BuildingStoreys"}.issubset(wb.sheetnames)
    assert wb["SchemaEntities"].sheet_state == "visible"
    assert "OccurrenceEntities" in wb.defined_names
    assert "TypeEntities" in wb.defined_names
    validations = list(wb["Elements"].data_validations.dataValidation)
    assert any(dv.formula1 == "=OccurrenceEntities" for dv in validations)
    assert any('INDIRECT("PT_"' in dv.formula1 for dv in validations)
    assert all(dv.sqref for dv in validations)
    wb.save(tmp_path / "rewritten.xlsx"); wb.close()


def test_legacy_type_reclassification_preserves_assigned_type(tmp_path):
    src, xlsx, output, proxy, typ, _ = _export(tmp_path)
    _legacy_type_request(xlsx, typ.GlobalId, entity="IfcWallType")
    update_ifc_from_excel(str(src), str(xlsx), str(output))
    f = ifcopenshell.open(str(output))
    assert f.by_guid(typ.GlobalId).is_a() == "IfcWallType"
    assert f.by_guid(proxy.GlobalId).is_a() == "IfcWall"
    assert ifcopenshell.util.element.get_type(f.by_guid(proxy.GlobalId)).GlobalId == typ.GlobalId


def test_reject_invalid_entity_predefined_combo_before_writing(tmp_path):
    src, xlsx, output, _, typ, _ = _export(tmp_path)
    _legacy_type_request(xlsx, typ.GlobalId, entity="IfcWall", predefined="NOT_A_VALID_TYPE")
    with pytest.raises(ExcelWritebackValidationError):
        update_ifc_from_excel(str(src), str(xlsx), str(output))
    assert not output.exists()


def test_invalid_predefined_does_not_fall_back_to_userdefined(tmp_path):
    src, xlsx, output, _, typ, _ = _export(tmp_path)
    _legacy_type_request(xlsx, typ.GlobalId, predefined="CULVERT")
    with pytest.raises(ExcelWritebackValidationError, match="Invalid.*PredefinedType"):
        update_ifc_from_excel(str(src), str(xlsx), str(output))
    assert not output.exists()
