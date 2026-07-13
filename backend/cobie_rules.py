from __future__ import annotations
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List
ROOT = Path(__file__).resolve().parents[1]
SHEETS_PATH = ROOT / "config" / "cobie" / "sheets.json"
RULEPACK_DIR = ROOT / "config" / "cobie" / "rulepacks"
CORE_SHEETS = {"Contact","Facility","Floor","Space","Type","Component","System","Attribute","PickLists"}
FALLBACK_ALLOWED_VALUES = {
    "Facility.LinearUnits": {"millimeters","meters","feet","inches"},
    "Facility.AreaUnits": {"square meters","square feet"},
    "Facility.VolumeUnits": {"cubic meters","cubic feet"},
    "Facility.CurrencyUnit": {"GBP","USD","EUR"},
    "Type.AssetType": {"fixed","moveable","fixed asset","moveable asset"},
    "Issue.Risk": {"low","medium","high"}, "Issue.Chance": {"low","medium","high"}, "Issue.Impact": {"low","medium","high"},
}
PLACEHOLDERS = {"tbc","tbd","n/a","na","none","unknown","-","0"}
NUMERIC_FIELDS = {
    "Floor": ["Elevation","Height"], "Space": ["UsableHeight","GrossArea","NetArea"],
    "Type": ["WarrantyDurationParts","WarrantyDurationLabor","NominalLength","NominalWidth","NominalHeight"],
    "Coordinate": ["CoordinateXAxis","CoordinateYAxis","CoordinateZAxis"],
}
DATE_FIELDS = {"CreatedOn","InstallationDate","WarrantyStartDate"}
TEXT_KEY_FIELDS = {"Name","Email","CreatedBy","FloorName","TypeName","Space","ComponentNames","SpaceNames","SheetName","RowName","ExternalIdentifier","ExtIdentifier","Category"}
GENERIC_NAMES = {"default","unnamed","object","component","element"}
@lru_cache
def load_sheet_config() -> List[Dict[str, Any]]:
    return json.loads(SHEETS_PATH.read_text(encoding="utf-8"))
@lru_cache
def load_rulepack(rule_pack: str | None) -> Dict[str, Any]:
    key = (rule_pack or "ifctoolkit-recommended").strip() or "ifctoolkit-recommended"
    path = RULEPACK_DIR / f"{key}.json"
    if not path.exists(): path = RULEPACK_DIR / "ifctoolkit-recommended.json"
    return json.loads(path.read_text(encoding="utf-8"))
def list_rulepacks() -> List[Dict[str, str]]:
    return [{"id": p.stem, "name": json.loads(p.read_text(encoding="utf-8")).get("name", p.stem)} for p in sorted(RULEPACK_DIR.glob("*.json"))]
