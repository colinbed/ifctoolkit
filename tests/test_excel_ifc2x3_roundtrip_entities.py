import pandas as pd
import ifcopenshell
from ifcopenshell.guid import new as new_guid
from openpyxl import load_workbook

import pytest
from app import extract_to_excel, update_ifc_from_excel, ExcelWritebackValidationError
from test_excel_name_resolution import edit


def _build_ifc2x3_model(tmp_path):
    model = ifcopenshell.file(schema="IFC2X3")
    project = model.create_entity("IfcProject", GlobalId=new_guid(), Name="P", LongName="PN-001")
    site = model.create_entity("IfcSite", GlobalId=new_guid(), Name="S")
    building = model.create_entity("IfcBuilding", GlobalId=new_guid(), Name="B")
    model.create_entity("IfcRelAggregates", GlobalId=new_guid(), RelatingObject=project, RelatedObjects=[site])
    model.create_entity("IfcRelAggregates", GlobalId=new_guid(), RelatingObject=site, RelatedObjects=[building])

    proxy = model.create_entity("IfcBuildingElementProxy", GlobalId=new_guid(), Name="ProxyOcc")
    proxy_type = model.create_entity("IfcBuildingElementProxyType", GlobalId=new_guid(), Name="ProxyType", PredefinedType="NOTDEFINED")
    wall_type = model.create_entity("IfcWallType", GlobalId=new_guid(), Name="WallType", PredefinedType="NOTDEFINED")
    model.create_entity("IfcRelDefinesByType", GlobalId=new_guid(), RelatedObjects=[proxy], RelatingType=proxy_type)
    wall = model.create_entity("IfcWall", GlobalId=new_guid(), Name="Wall")
    model.create_entity("IfcRelDefinesByType", GlobalId=new_guid(), RelatedObjects=[wall], RelatingType=wall_type)
    return model, proxy, proxy_type, wall_type



def export(tmp_path):
    model, proxy, proxy_type, wall_type = _build_ifc2x3_model(tmp_path)
    paths = [tmp_path / n for n in ["source.ifc", "book.xlsx", "output.ifc"]]
    model.write(str(paths[0])); extract_to_excel(str(paths[0]), str(paths[1]))
    return paths, proxy.GlobalId, proxy_type.GlobalId, wall_type.GlobalId


def test_export_includes_types_raw_entities_and_dropdowns(tmp_path):
    paths, proxy, typ, _ = export(tmp_path)
    elements = pd.read_excel(paths[1], "Elements")
    types = pd.read_excel(paths[1], "Types")
    assert proxy in set(elements['GlobalId'])
    assert typ in set(types['Type GlobalId'])
    assert 'IfcBuildingElementProxy' in set(elements['IFC Entity'])
    assert 'RawStepLine' in pd.read_excel(paths[1], 'RawEntities').columns
    assert 'Name' not in elements and 'TypeDescription' not in elements
    assert elements.loc[elements.GlobalId == proxy, 'Predefined Type'].iloc[0] == 'Unavailable'


def test_workbook_lookup_validations_roundtrip_safe(tmp_path):
    paths, *_ = export(tmp_path)
    wb = load_workbook(paths[1])
    assert wb['SchemaReference'].sheet_state == 'visible'
    assert wb.defined_names.get('IFC_Occurrences') is not None
    assert wb.defined_names.get('IFC_Enum_IfcWallType') is not None
    formulas = {dv.formula1 for dv in wb['Elements'].data_validations.dataValidation}
    assert '=IFC_Occurrences' in formulas
    assert any('INDIRECT' in formula for formula in formulas)
    wb.save(paths[1]); wb.close()
    reopened = load_workbook(paths[1]); assert len(reopened['Elements'].data_validations.dataValidation) == 2; reopened.close()


def test_reupload_predefined_only_and_valid_type_reclassification(tmp_path):
    paths, proxy, typ, wall_type = export(tmp_path)
    edit(paths[1], [('Types', wall_type, 'Predefined Type', 'USERDEFINED'),
                    ('Types', wall_type, 'Element Type', 'Custom wall'),
                    ('Types', typ, 'IFC Entity', 'IfcWallType')])
    update_ifc_from_excel(*map(str, paths))
    revised = ifcopenshell.open(str(paths[2]))
    assert revised.by_guid(typ).is_a() == 'IfcWallType'
    assert revised.by_guid(proxy).is_a() == 'IfcWall'
    assert revised.by_guid(wall_type).PredefinedType == 'USERDEFINED'
    assert revised.by_guid(wall_type).ElementType == 'Custom wall'


def test_reject_invalid_entity_predefined_combo(tmp_path):
    paths, _, typ, _ = export(tmp_path)
    edit(paths[1], [('Types', typ, 'IFC Entity', 'IfcWall')])
    with pytest.raises(ExcelWritebackValidationError):
        update_ifc_from_excel(*map(str, paths))
    assert not paths[2].exists()


def test_type_invalid_predefined_is_rejected_without_userdefined_fallback(tmp_path):
    paths, _, _, typ = export(tmp_path)
    edit(paths[1], [('Types', typ, 'Predefined Type', 'CULVERT')])
    with pytest.raises(ExcelWritebackValidationError):
        update_ifc_from_excel(*map(str, paths))
    assert not paths[2].exists()


def test_legacy_apply_change_import(tmp_path):
    paths, proxy, typ, _ = export(tmp_path)
    wb = load_workbook(paths[1]); ws = wb['Types']
    for cell in ws[1]:
        if cell.value == 'Type GlobalId': cell.value = 'GlobalId'
        elif cell.value == 'Type Name': cell.value = 'Name'
    # v1 TargetEntity/ApplyChange continue to work without row order or STEP identity.
    headers = {cell.value: cell.column for cell in ws[1]}
    row = next(i for i in range(2, ws.max_row + 1) if ws.cell(i, headers['GlobalId']).value == typ)
    col = ws.max_column + 1
    ws.cell(1, col, 'TargetEntity'); ws.cell(row, col, 'IfcWallType')
    ws.cell(1, col + 1, 'ApplyChange'); ws.cell(row, col + 1, 'Yes')
    wb.save(paths[1]); wb.close()
    update_ifc_from_excel(*map(str, paths))
    revised = ifcopenshell.open(str(paths[2]))
    assert revised.by_guid(typ).is_a() == 'IfcWallType'
    assert revised.by_guid(proxy).is_a() == 'IfcWall'
