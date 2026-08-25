import pandas as pd
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element
from ifcopenshell.guid import new as new_guid

from app import extract_to_excel, update_ifc_from_excel
from ifc_protocol import PROTOCOL_CONFIG_SHEET, PROTOCOL_DATA_SHEET, default_protocol_config, extract_protocol_to_dataframe


def _build_protocol_model(tmp_path):
    model = ifcopenshell.file(schema="IFC4")
    model.create_entity("IfcProject", GlobalId=new_guid(), Name="P", LongName="PN-001")
    wall = model.create_entity("IfcWall", GlobalId=new_guid(), Name="Wall A", ObjectType="Legacy Type")
    legacy_pset = ifcopenshell.api.run("pset.add_pset", model, product=wall, name="Pset_LegacyAssetData")
    ifcopenshell.api.run("pset.edit_pset", model, pset=legacy_pset, properties={"AssetReference": "LEG-001"})
    src = tmp_path / "protocol.ifc"
    model.write(str(src))
    return src, wall.GlobalId


def _protocol_config():
    return {
        "schema_version": "1.0",
        "name": "Project Asset Data",
        "project_client": "GPA",
        "description": "Asset information exchange configuration",
        "version": "1.0",
        "created_by": "Test",
        "created_date": "2026-01-01T00:00:00Z",
        "last_modified": "2026-01-01T00:00:00Z",
        "ifc_schemas": ["IFC4"],
        "entities": [
            {
                "entity": "IfcWall",
                "include_subtypes": True,
                "fields": [
                    {
                        "id": "asset_reference",
                        "label": "Asset Reference",
                        "source": {
                            "kind": "property",
                            "pset": "Pset_LegacyAssetData",
                            "property": "AssetReference",
                        },
                        "datatype": "text",
                        "editable": True,
                        "write": {
                            "enabled": True,
                            "target": {
                                "kind": "property",
                                "pset": "Additional_Pset_GeneralCommon",
                                "property": "AssetReference",
                            },
                            "create_if_missing": True,
                            "data_type": "IfcLabel",
                        },
                    }
                ],
            }
        ],
    }


def _read_workbook(path):
    xls = pd.ExcelFile(path)
    payload = {name: pd.read_excel(xls, name) for name in xls.sheet_names}
    xls.close()
    return payload


def _write_workbook(path, sheets):
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)


def test_protocol_extracts_configured_data_and_embeds_config(tmp_path):
    src, guid = _build_protocol_model(tmp_path)
    xlsx = tmp_path / "protocol.xlsx"

    extract_to_excel(str(src), str(xlsx), plan_payload={"protocol": _protocol_config()})

    sheets = _read_workbook(xlsx)
    assert PROTOCOL_DATA_SHEET in sheets
    assert PROTOCOL_CONFIG_SHEET in sheets
    data = sheets[PROTOCOL_DATA_SHEET]
    row = data[data["GlobalId"] == guid].iloc[0]
    assert row["Asset Reference"] == "LEG-001"


def test_protocol_writeback_can_remap_to_approved_target_pset(tmp_path):
    src, guid = _build_protocol_model(tmp_path)
    xlsx = tmp_path / "protocol.xlsx"
    updated = tmp_path / "updated.ifc"

    extract_to_excel(str(src), str(xlsx), plan_payload={"protocol": _protocol_config()})
    sheets = _read_workbook(xlsx)
    data = sheets[PROTOCOL_DATA_SHEET]
    data.loc[data["GlobalId"] == guid, "Asset Reference"] = "APPROVED-001"
    sheets[PROTOCOL_DATA_SHEET] = data
    _write_workbook(xlsx, sheets)

    update_ifc_from_excel(str(src), str(xlsx), str(updated), add_new="no")

    model = ifcopenshell.open(str(updated))
    wall = model.by_guid(guid)
    psets = ifcopenshell.util.element.get_psets(wall)
    assert psets["Pset_LegacyAssetData"]["AssetReference"] == "LEG-001"
    assert psets["Additional_Pset_GeneralCommon"]["AssetReference"] == "APPROVED-001"


def test_base_and_specific_entity_fields_share_one_protocol_row():
    model = ifcopenshell.file(schema="IFC4")
    model.create_entity("IfcProject", GlobalId=new_guid(), Name="P")
    door = model.create_entity("IfcDoor", GlobalId=new_guid(), Name="Door A")
    pset = ifcopenshell.api.run("pset.add_pset", model, product=door, name="Pset_DoorCommon")
    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"FireRating": "FD60"})

    data = extract_protocol_to_dataframe(model, default_protocol_config())
    rows = data[data["GlobalId"] == door.GlobalId]

    assert len(rows.index) == 1
    assert rows.iloc[0]["Asset Name"] == "Door A"
    assert rows.iloc[0]["Fire Rating"] == "FD60"
    assert rows.iloc[0]["Protocol Entity"] == "IfcElement | IfcDoor"
