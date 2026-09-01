import hashlib

import ifcopenshell

from backend.reg38_space_writeback import write_reviewed_spaces
from tests.test_reg38_ifc_processor import make_fixture


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def test_canonical_space_writeback_round_trip_preserves_blank_and_source(tmp_path):
    source=tmp_path/"source.ifc"; ids=make_fixture(source); before=digest(source)
    output=tmp_path/"reviewed.ifc"
    result=write_reviewed_spaces(source,output,[{"ifc_global_id":ids["space"],"space_number":"01-127c",
        "name":"Edited classroom","description":""}])
    space=ifcopenshell.open(str(output)).by_guid(ids["space"])
    assert result == {"updated":1,"missing":0}
    assert (space.Name,space.LongName,space.Description) == ("01-127c","Edited classroom","Teaching space")
    assert digest(source)==before and source.read_bytes()!=output.read_bytes()
    assert len(ifcopenshell.open(str(output)).by_type("IfcSpace")) == 1


def test_writeback_rejects_source_overwrite(tmp_path):
    source=tmp_path/"source.ifc"; make_fixture(source)
    try: write_reviewed_spaces(source,source,[])
    except ValueError as exc: assert "separate" in str(exc)
    else: raise AssertionError("source overwrite was accepted")
