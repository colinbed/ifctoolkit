"""Deterministic micro-benchmark for the Excel proxy mapping architecture."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.excel_proxy_mapping import build_mapping_plan


class Element:
    def is_a(self):
        return "IfcBuildingElementProxy"


def inference(_signature, row):
    # Stable CPU work stands in for a remote/model inference request.
    value = row["ObjectType"].encode()
    for _ in range(100):
        value = hashlib.sha256(value).digest()
    return "IfcBeam"


def main(rows_count=12_000, signatures=24):
    rows = [(i, {"GlobalId": f"g{i}", "ObjectType": f"steel-{i % signatures}"}, Element())
            for i in range(rows_count)]
    started = time.perf_counter()
    for _, row, _ in rows:
        inference(None, row)
    before_mapping = time.perf_counter() - started

    calls = 0

    def counted(signature, row):
        nonlocal calls
        calls += 1
        return inference(signature, row)

    started = time.perf_counter()
    plan, metrics = build_mapping_plan(
        rows, entity_is_valid=lambda entity: entity in {"IfcBuildingElementProxy", "IfcBeam"},
        predefined_is_valid=lambda *_: True, infer=counted,
    )
    plan_time = time.perf_counter() - started
    apply_started = time.perf_counter()
    applied = [(item.global_id, item.resolved_target_entity) for item in plan]
    application_time = time.perf_counter() - apply_started
    print(json.dumps({
        "total_rows": rows_count, "proxy_rows": rows_count,
        "unique_mapping_signatures": metrics["unique_signatures"],
        "before": {"inference_calls": rows_count, "database_api_calls": 0,
                   "mapping_plan_s": round(before_mapping, 4),
                   "element_application_s": None, "total_write_s": round(before_mapping, 4)},
        "after": {"inference_calls": calls, "database_api_calls": 0,
                  "mapping_plan_s": round(plan_time, 4),
                  "element_application_s": round(application_time, 4),
                  "total_write_s": round(plan_time + application_time, 4)},
        "applied_rows": len(applied),
    }, indent=2))


if __name__ == "__main__":
    main()
