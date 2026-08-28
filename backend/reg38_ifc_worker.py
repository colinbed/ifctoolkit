"""Background worker and batched PostgREST sink for Regulation 38 IFC jobs."""
from __future__ import annotations

import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from backend.reg38_ifc_processor import Regulation38IfcProcessor


class SupabaseBatchSink:
    """Service-role adapter: one HTTP request per batch, never per IFC property."""
    def __init__(self, url: str, service_key: str, batch_size: int = 1000):
        self.url = url.rstrip("/")
        self.headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}", "Content-Type": "application/json"}
        self.batch_size = batch_size

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = requests.request(method, f"{self.url}/{path.lstrip('/')}", headers=self.headers, timeout=120, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def claim(self) -> dict[str, Any] | None:
        value = self._request("POST", "rest/v1/rpc/claim_reg38_ifc_job", json={})
        return value[0] if isinstance(value, list) and value else value if isinstance(value, dict) else None

    def update_job(self, job_id: str, **values: Any) -> None:
        self._request("PATCH", f"rest/v1/ifc_processing_jobs?id=eq.{job_id}", json=values)

    def update_file(self, file_id: str, **values: Any) -> None:
        self._request("PATCH", f"rest/v1/ifc_files?id=eq.{file_id}", json=values)

    def download(self, storage_path: str, destination: Path) -> None:
        with requests.get(f"{self.url}/storage/v1/object/authenticated/project-files/{storage_path}",
                          headers=self.headers, timeout=(30, 1800), stream=True) as response:
            response.raise_for_status()
            with destination.open("wb") as output:
                for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                    if chunk:
                        output.write(chunk)

    def insert_result(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        # Parent-first ordering satisfies all foreign keys. Each slice is a bulk request.
        order = ("buildings", "building_storeys", "ifc_objects", "ifc_object_properties",
                 "ifc_object_relationships", "project_spaces", "project_zones", "project_zone_members",
                 "project_grids", "project_grid_axes", "fire_requirements", "model_scan_warnings")
        headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
        for table in order:
            rows = tables.get(table, [])
            for offset in range(0, len(rows), self.batch_size):
                response = requests.post(f"{self.url}/rest/v1/{table}", headers=headers,
                                         json=rows[offset:offset + self.batch_size], timeout=120)
                response.raise_for_status()


def process_job(sink: SupabaseBatchSink, job: dict[str, Any]) -> None:
    job_id, file_id, project_id = str(job["id"]), str(job["ifc_file_id"]), str(job["project_id"])
    def progress(stage: str, percent: int, statistics: dict[str, int]) -> None:
        sink.update_job(job_id, status="RUNNING" if stage != "COMPLETE" else "COMPLETED",
                        current_step=stage, progress_percent=percent, statistics=statistics)
    try:
        with tempfile.TemporaryDirectory(prefix="reg38-ifc-") as directory:
            path = Path(directory) / "source.ifc"
            sink.download(str(job["storage_path"]), path)
            result = Regulation38IfcProcessor(progress).process(path, project_id=project_id, ifc_file_id=file_id)
            sink.insert_result(result.tables)
            sink.update_file(file_id, status="PROCESSED", ifc_schema=result.statistics.get("ifc_schema"))
            sink.update_job(job_id, status="COMPLETED", current_step="COMPLETE", progress_percent=100,
                            statistics=result.statistics, completed_at=datetime.now(UTC).isoformat())
    except Exception as exc:
        sink.update_job(job_id, status="FAILED", error_message=str(exc)[:4000],
                        completed_at=datetime.now(UTC).isoformat())
        sink.update_file(file_id, status="FAILED")
        raise


def main() -> None:
    sink = SupabaseBatchSink(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    delay = float(os.getenv("REG38_WORKER_POLL_SECONDS", "3"))
    while True:
        job = sink.claim()
        if job:
            try: process_job(sink, job)
            except Exception: pass
        else:
            time.sleep(delay)


if __name__ == "__main__":
    main()
