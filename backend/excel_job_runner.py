"""One-shot child process for an Excel extraction/update job."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

LOGGER = logging.getLogger("ifc_app.excel_worker")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


def rss_mb():
    return round(psutil.Process().memory_info().rss / 1048576, 2) if psutil else None


def stage(spec, name, started, **extra):
    LOGGER.info("EXCEL_EXTRACTION_STAGE stage=%s session_id=%s input_filename=%s file_size_bytes=%s rss_mb=%s elapsed_s=%.3f %s", name, spec["session_id"], spec["input_filename"], spec["input_size"], rss_mb(), time.monotonic() - started, " ".join(f"{k}={v}" for k, v in extra.items()))


def main(spec_path: str, result_path: str) -> int:
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    started = time.monotonic()
    LOGGER.info("EXCEL_EXTRACTION_STARTED session_id=%s input_filename=%s file_size_bytes=%s rss_mb=%s", spec["session_id"], spec["input_filename"], spec["input_size"], rss_mb())
    try:
        # Import only in the disposable worker: importing IfcOpenShell itself loads native code.
        from app import extract_to_excel, update_ifc_from_excel
        stage(spec, "open_ifc", started)
        if spec["kind"] == "extract":
            stage(spec, "extract_entities", started)
            result = extract_to_excel(spec["input_path"], spec["output_path"], plan_payload=spec.get("plan"))
            rows = (result.get("counts") or {})
            stage(spec, "write_workbook", started, workbook_rows=sum(v for v in rows.values() if isinstance(v, int)))
        else:
            stage(spec, "read_workbook", started)
            update_ifc_from_excel(spec["input_path"], spec["excel_path"], spec["output_path"], update_mode=spec.get("update_mode", "update"), add_new=spec.get("add_new", "no"), session_id=spec["session_id"], endpoint="excel_job_worker")
            result = {}
            stage(spec, "write_ifc", started)
        payload = {"status": "completed", "output_file_id": Path(spec["output_path"]).name, "result": result}
        LOGGER.info("EXCEL_EXTRACTION_COMPLETED session_id=%s input_filename=%s file_size_bytes=%s rss_mb=%s elapsed_s=%.3f", spec["session_id"], spec["input_filename"], spec["input_size"], rss_mb(), time.monotonic() - started)
    except Exception as exc:
        LOGGER.error("EXCEL_EXTRACTION_FAILED session_id=%s input_filename=%s file_size_bytes=%s rss_mb=%s elapsed_s=%.3f error=%s\n%s", spec.get("session_id"), spec.get("input_filename"), spec.get("input_size"), rss_mb(), time.monotonic() - started, exc, traceback.format_exc())
        payload = {"status": "failed", "message": f"Processing failed: {exc}. You can retry.", "error": str(exc)}
    Path(result_path).write_text(json.dumps(payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
