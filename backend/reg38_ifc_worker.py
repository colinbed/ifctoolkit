"""Production worker for Regulation 38 IFC model-scan jobs.

Run this module in a dedicated Railway service, never in the web process.
"""
from __future__ import annotations

import json
import logging
import os
import random
import socket
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from backend.reg38_ifc_processor import Regulation38IfcProcessor

LOG = logging.getLogger("reg38.worker")
RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def _log(event: str, **fields: Any) -> None:
    LOG.info(json.dumps({"event": event, **fields}, default=str, separators=(",", ":")))


class LostLeaseError(RuntimeError):
    """The job was recovered or otherwise ceased to belong to this worker."""


class TransientSupabaseError(RuntimeError):
    """A temporary Supabase transport/control-plane failure."""

    def __init__(self, operation: str, *, status: int | None = None, detail: str = ""):
        super().__init__(detail or f"Transient Supabase failure during {operation}")
        self.operation, self.status, self.detail = operation, status, detail


class ExponentialBackoff:
    def __init__(self, *, base: float = 1.0, maximum: float = 30.0, jitter: float = 0.2,
                 random_value: Any = random.random):
        self.base, self.maximum, self.jitter, self.random_value = base, maximum, jitter, random_value
        self.attempt = 0

    def next_delay(self) -> float:
        self.attempt += 1
        raw = min(self.maximum, self.base * (2 ** (self.attempt - 1)))
        return min(self.maximum, raw + raw * self.jitter * self.random_value())

    def reset(self) -> None:
        self.attempt = 0


class SupabaseBatchSink:
    """Service-role PostgREST/Storage adapter with lease-aware job updates."""

    def __init__(self, url: str, service_key: str, batch_size: int = 1000, worker_id: str | None = None):
        self.url = url.rstrip("/")
        self.headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}", "Content-Type": "application/json"}
        self.batch_size = batch_size
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        operation = self._operation(path)
        try:
            response = requests.request(method, f"{self.url}/{path.lstrip('/')}", headers=self.headers, timeout=120, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TransientSupabaseError(operation, detail=type(exc).__name__) from exc
        self._raise_for_status(response, method=method, path=path)
        return response.json() if response.content else None

    @staticmethod
    def _operation(path: str) -> str:
        if "claim_reg38_ifc_job" in path: return "claim"
        if "recover_stale_reg38_ifc_jobs" in path: return "recover_stale"
        return path.split("?", 1)[0].rsplit("/", 1)[-1]

    @staticmethod
    def _raise_for_status(response: Any, *, method: str, path: str) -> None:
        status = getattr(response, "status_code", 200)
        if not getattr(response, "ok", status < 400):
            _log("postgrest_request_failed", method=method, path=path,
                 response_status=status, response_body=response.text)
        if status in RETRYABLE_STATUSES:
            raise TransientSupabaseError(SupabaseBatchSink._operation(path), status=status,
                                         detail=f"HTTP {status}")
        response.raise_for_status()

    def claim(self) -> dict[str, Any] | None:
        value = self._request("POST", "rest/v1/rpc/claim_reg38_ifc_job", json={"p_worker_id": self.worker_id})
        return value[0] if isinstance(value, list) and value else value if isinstance(value, dict) else None

    def recover_stale(self, stale_seconds: int) -> int:
        value = self._request("POST", "rest/v1/rpc/recover_stale_reg38_ifc_jobs", json={"p_stale_seconds": stale_seconds})
        return int(value or 0)

    def update_job(self, job: dict[str, Any], **values: Any) -> None:
        values["heartbeat_at"] = datetime.now(UTC).isoformat()
        path = (f"rest/v1/ifc_processing_jobs?id=eq.{job['id']}&status=eq.RUNNING"
                f"&claim_token=eq.{job['claim_token']}")
        headers = {**self.headers, "Prefer": "return=representation"}
        response = requests.patch(f"{self.url}/{path}", headers=headers, json=values, timeout=120)
        self._raise_for_status(response, method="PATCH", path=path)
        if not response.json():
            raise LostLeaseError(f"job {job['id']} lease is no longer owned")

    def update_file(self, file_id: str, **values: Any) -> None:
        self._request("PATCH", f"rest/v1/ifc_files?id=eq.{file_id}", json=values)

    def download(self, storage_path: str, destination: Path) -> None:
        # Service role authorises private project-files access; the object name is
        # already the canonical path persisted by finalize_ifc_upload.
        url = f"{self.url}/storage/v1/object/authenticated/project-files/{storage_path.lstrip('/')}"
        with requests.get(url, headers=self.headers, timeout=(30, 1800), stream=True) as response:
            self._raise_for_status(response, method="GET", path="storage/v1/object/authenticated/project-files")
            with destination.open("wb") as output:
                for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                    if chunk:
                        output.write(chunk)

    def insert_result(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        order = ("buildings", "building_storeys", "ifc_objects", "ifc_object_properties",
                 "ifc_object_plan_geometry", "ifc_object_relationships", "project_spaces", "project_zones", "project_zone_members",
                 "project_grids", "project_grid_axes", "fire_requirements", "model_scan_warnings")
        # Every table has an intentional retry identity. Most extractor rows use
        # deterministic primary keys; the exceptions use their declared logical
        # unique constraints.
        conflict_targets = {
            "buildings": "id", "building_storeys": "id",
            "ifc_objects": "ifc_file_id,ifc_global_id", "ifc_object_properties": "id",
            "ifc_object_plan_geometry": "ifc_file_id,ifc_object_id",
            "ifc_object_relationships": "id", "project_spaces": "id", "project_zones": "id",
            "project_zone_members": "zone_id,space_id", "project_grids": "id",
            "project_grid_axes": "id",
            "fire_requirements": ("project_id,ifc_object_id,requirement_type,source_scope,"
                                  "source_property_set,source_property_name,source_property_value,source_type"),
            "model_scan_warnings": "ifc_file_id,ifc_object_id,warning_code",
        }
        headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
        for table in order:
            rows = tables.get(table, [])
            # Deterministic IDs make a re-scan an update. Preserve explicitly
            # curated working fields while refreshing IFC-derived columns.
            if table == "project_spaces" and rows:
                ids = ",".join(str(row["id"]) for row in rows)
                existing = self._request("GET", "rest/v1/project_spaces?"
                    f"id=in.({ids})&select=id,space_number,name,description,occupancy_type,occupancy_capacity,high_risk,"
                    "included_in_reg38,working_geometry,working_fields_edited") or []
                working = {str(row["id"]): row for row in existing}
                curated = ("space_number", "name", "description", "occupancy_type", "occupancy_capacity",
                           "high_risk", "included_in_reg38", "working_geometry")
                for row in rows:
                    previous = working.get(str(row["id"]))
                    if previous and previous.get("working_fields_edited"):
                        row.update({key: previous.get(key) for key in curated})
            elif table == "model_scan_warnings" and rows:
                ids = ",".join(str(row["id"]) for row in rows)
                existing = self._request("GET", "rest/v1/model_scan_warnings?"
                    f"id=in.({ids})&select=id,review_status,reviewed_by,reviewed_at") or []
                reviews = {str(row["id"]): row for row in existing}
                for row in rows:
                    if str(row["id"]) in reviews:
                        row.update({key: reviews[str(row["id"])].get(key)
                                    for key in ("review_status", "reviewed_by", "reviewed_at")})
            _log("phase", phase=f"WRITE_{table.upper()}", rows=len(rows))
            for offset in range(0, len(rows), self.batch_size):
                batch = rows[offset:offset + self.batch_size]
                ids = [row.get("id") for row in batch if row.get("id") is not None]
                batch_number = offset // self.batch_size + 1
                _log("batch_write", table=table, batch_number=batch_number, rows=len(batch),
                     unique_ids=len(set(ids)), duplicate_id_count=len(ids) - len(set(ids)))
                path = f"rest/v1/{table}?on_conflict={conflict_targets[table]}"
                response = requests.post(f"{self.url}/{path}", headers=headers, json=batch, timeout=120)
                self._raise_for_status(response, method="POST", path=path)


def process_job(sink: SupabaseBatchSink, job: dict[str, Any]) -> None:
    job_id, file_id, project_id = str(job["id"]), str(job["ifc_file_id"]), str(job["project_id"])

    def progress(stage: str, percent: int, statistics: dict[str, int]) -> None:
        _log("phase", job_id=job_id, phase=stage, progress_percent=percent)
        if stage == "IFC_OPENED":
            _log("ifc_opened", job_id=job_id, schema=statistics.get("ifc_schema"))
        # COMPLETE is only persisted after all database writes succeed.
        if stage != "COMPLETE":
            sink.update_job(job, current_step=stage, progress_percent=min(percent, 95), statistics=statistics)

    _log("job_claimed", job_id=job_id, file_id=file_id, worker_id=sink.worker_id)
    try:
        sink.update_job(job, current_step="DOWNLOADING_IFC", progress_percent=2)
        with tempfile.TemporaryDirectory(prefix="reg38-ifc-") as directory:
            path = Path(directory) / "source.ifc"
            _log("storage_download_started", job_id=job_id, storage_path=job["storage_path"])
            sink.download(str(job["storage_path"]), path)
            _log("storage_download_completed", job_id=job_id, bytes=path.stat().st_size)
            result = Regulation38IfcProcessor(progress).process(path, project_id=project_id, ifc_file_id=file_id)
            _log("counts_extracted", job_id=job_id, **result.statistics)
            _log("space_geometry_summary", job_id=job_id,
                 spaces_total=result.statistics.get("spaces", 0),
                 spaces_with_polygon=result.statistics.get("spaces_with_plan_geometry", 0),
                 spaces_centroid_only=result.statistics.get("spaces_centroid_only", 0),
                 spaces_geometry_failed=result.statistics.get("spaces_without_plan_geometry", 0),
                 failure_reasons=result.statistics.get("space_geometry_failure_reasons", {}))
            _log("plan_object_geometry_summary", job_id=job_id,
                 plan_objects_total=result.statistics.get("plan_objects_total", 0),
                 plan_objects_with_geometry=result.statistics.get("plan_objects_with_geometry", 0),
                 walls_with_plan_geometry=result.statistics.get("walls_with_plan_geometry", 0),
                 doors_with_plan_geometry=result.statistics.get("doors_with_plan_geometry", 0),
                 columns_with_plan_geometry=result.statistics.get("columns_with_plan_geometry", 0),
                 objects_geometry_failed=result.statistics.get("objects_geometry_failed", 0))
            sink.update_job(job, current_step="WRITING_DATABASE", progress_percent=96, statistics=result.statistics)
            sink.insert_result(result.tables)
            _log("warnings_generated", job_id=job_id, count=len(result.tables["model_scan_warnings"]))
            sink.update_file(file_id, status="PROCESSED", ifc_schema=result.statistics.get("ifc_schema"))
            sink.update_job(job, status="COMPLETED", current_step="COMPLETE", progress_percent=100,
                            statistics=result.statistics, completed_at=datetime.now(UTC).isoformat())
            _log("job_completed", job_id=job_id)
    except LostLeaseError:
        _log("job_lease_lost", job_id=job_id)
        raise
    except Exception as exc:
        try:
            sink.update_job(job, status="FAILED", current_step="FAILED", error_message=f"{type(exc).__name__}: {exc}"[:4000],
                            completed_at=datetime.now(UTC).isoformat())
            sink.update_file(file_id, status="FAILED")
        except LostLeaseError:
            pass
        LOG.exception(json.dumps({"event": "job_failed", "job_id": job_id, "error": str(exc)}))
        raise


def run_once(sink: SupabaseBatchSink, stale_seconds: int = 3600) -> bool:
    recovered = sink.recover_stale(stale_seconds)
    if recovered:
        _log("stale_jobs_recovered", count=recovered)
    job = sink.claim()
    if not job:
        return False
    try:
        process_job(sink, job)
    except Exception:
        pass
    return True


def run_worker_loop(sink: SupabaseBatchSink, *, poll_seconds: float, stale_seconds: int,
                    sleep: Any = time.sleep, monotonic: Any = time.monotonic,
                    random_value: Any = random.random, max_cycles: int | None = None,
                    heartbeat_seconds: float = 300.0) -> None:
    """Run resilient control-plane polling without masking programming defects."""
    claim_backoff = ExponentialBackoff(random_value=random_value)
    recovery_backoff = ExponentialBackoff(random_value=random_value)
    recovery_due = 0.0
    outage_started: float | None = None
    last_success: float | None = None
    last_heartbeat = monotonic()
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        cycles += 1
        now = monotonic()
        if now >= recovery_due:
            try:
                recovered = sink.recover_stale(stale_seconds)
                recovery_backoff.reset(); recovery_due = now
                if recovered: _log("stale_jobs_recovered", count=recovered)
            except TransientSupabaseError as exc:
                delay = recovery_backoff.next_delay(); recovery_due = now + delay
                _log("worker_transient_error", operation="recover_stale", status=exc.status or "unavailable",
                     attempt=recovery_backoff.attempt, retry_in_seconds=round(delay, 3))
        try:
            job = sink.claim()
        except TransientSupabaseError as exc:
            if outage_started is None: outage_started = now
            delay = claim_backoff.next_delay()
            _log("worker_transient_error", operation="claim", status=exc.status or "unavailable",
                 attempt=claim_backoff.attempt, retry_in_seconds=round(delay, 3))
            sleep(delay)
            continue
        last_success = monotonic()
        if outage_started is not None:
            _log("worker_connection_restored", downtime_seconds=round(last_success - outage_started, 3))
            outage_started = None
        claim_backoff.reset()
        if job:
            try:
                process_job(sink, job)
            except Exception:
                # process_job records a terminal failure. Preserve the existing
                # deterministic write path; never retry active mutations here.
                pass
        else:
            sleep(poll_seconds)
        now = monotonic()
        if now - last_heartbeat >= heartbeat_seconds:
            _log("worker_heartbeat", worker_id=sink.worker_id, status="idle" if not job else "processed",
                 last_successful_supabase_request=last_success)
            last_heartbeat = now


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")
    sink = SupabaseBatchSink(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    delay = float(os.getenv("REG38_WORKER_POLL_SECONDS", "3"))
    stale_seconds = int(os.getenv("REG38_WORKER_STALE_SECONDS", "3600"))
    _log("worker_started", worker_id=sink.worker_id, poll_seconds=delay, stale_seconds=stale_seconds)
    run_worker_loop(sink, poll_seconds=delay, stale_seconds=stale_seconds)


if __name__ == "__main__":
    main()
