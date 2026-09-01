"""Crash-isolated jobs for IFC/Excel round trips.

Job metadata is persisted beside the temporary session store.  The work itself is
executed by a fresh Python interpreter so an IfcOpenShell native crash cannot take
the ASGI server with it.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional

LOGGER = logging.getLogger("ifc_app.excel_jobs")
TERMINAL = {"completed", "failed"}


def _rss_mb() -> Optional[float]:
    """Observe the web parent without importing/opening an IFC in that process."""
    try:
        import psutil
        return round(psutil.Process().memory_info().rss / 1048576, 2)
    except ImportError:  # pragma: no cover - optional operational metric
        return None


class ExcelJobStore:
    def __init__(self, root: Path, max_workers: int = 1):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="excel-job")
        self._recover()

    def _job_path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def _persist(self, job: Dict[str, Any]) -> None:
        path = self._job_path(job["job_id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def _recover(self) -> None:
        for path in self.root.glob("*.json"):
            if path.name.endswith(".result.json") or path.name.endswith(".spec.json"):
                continue
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                LOGGER.exception("EXCEL_JOB_STATE_INVALID path=%s", path)
                continue
            if job.get("status") in {"queued", "running"}:
                previous_status = job.get("status")
                job.update(status="failed", progress=100, message="Processing was interrupted by an application restart. Retry the job.", error="worker_interrupted", recoverable=True)
                self._persist(job)
                LOGGER.warning("EXCEL_JOB_INTERRUPTED job_id=%s session_id=%s previous_status=%s", job.get("job_id"), job.get("session_id"), previous_status)
            self._jobs[job["job_id"]] = job
        LOGGER.info("EXCEL_JOB_STARTUP recovered_jobs=%d", len(self._jobs))

    def create(self, *, kind: str, session_id: str, input_filename: str, input_size: int, spec: Dict[str, Any]) -> Dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = time.time()
        job = {"job_id": job_id, "status": "queued", "progress": 0, "message": "Preparing model...", "output_file_id": None, "error": None, "recoverable": False, "kind": kind, "session_id": session_id, "input_filename": input_filename, "input_size": input_size, "created_at": now, "updated_at": now}
        spec_path = self.root / f"{job_id}.spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        with self._lock:
            self._jobs[job_id] = job
            self._persist(job)
            response = dict(job)
        self._executor.submit(self._execute, job_id, spec_path)
        return response

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(values, updated_at=time.time())
            self._persist(job)

    def _execute(self, job_id: str, spec_path: Path) -> None:
        job = self.get(job_id) or {}
        self._update(job_id, status="running", progress=5, message="Reading IFC...")
        result_path = self.root / f"{job_id}.result.json"
        command = [sys.executable, "-m", "backend.excel_job_runner", str(spec_path), str(result_path)]
        started = time.monotonic()
        event = "IFC_WRITEBACK" if job.get("kind") == "update" else "EXCEL_EXTRACTION"
        LOGGER.info("%s_PARENT_MEMORY job_id=%s web_rss_mb=%s worker_ifc_loaded=false", event, job_id, _rss_mb())
        completed = subprocess.run(command, cwd=str(Path(__file__).resolve().parents[1]), check=False)
        elapsed = time.monotonic() - started
        LOGGER.info("%s_PARENT_MEMORY job_id=%s web_rss_mb=%s worker_finished=true", event, job_id, _rss_mb())
        if completed.returncode != 0:
            if completed.returncode < 0:
                reason = f"worker terminated by signal {-completed.returncode}"
            elif completed.returncode == 137:
                reason = "worker killed (exit 137; likely memory limit/SIGKILL)"
            else:
                reason = f"worker exited with code {completed.returncode}"
            self._update(job_id, status="failed", progress=100, message=f"Processing failed: {reason}. You can retry.", error=reason, recoverable=True)
            LOGGER.error("%s_FAILED job_id=%s session_id=%s file=%s size_bytes=%s elapsed_s=%.3f reason=%s", event, job_id, job.get("session_id"), job.get("input_filename"), job.get("input_size"), elapsed, reason)
            return
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._update(job_id, status="failed", progress=100, message="Worker did not return a valid result. You can retry.", error=str(exc), recoverable=True)
            return
        if result.get("status") == "completed":
            details = result.get("result") or {}
            self._update(job_id, status="completed", progress=100, message=details.get("message", "Complete"), output_file_id=result.get("output_file_id"), result=details,
                         warnings=details.get("warnings", []), errors=[], recoverable=False)
            LOGGER.info("%s_JOB_SUCCESS job_id=%s output_file_id=%s warnings=%d", event, job_id, result.get("output_file_id"), len(details.get("warnings", [])))
        else:
            self._update(job_id, status="failed", progress=100, message=result.get("message") or "Processing failed. You can retry.", error=result.get("error"),
                         errors=result.get("errors", []), warnings=result.get("warnings", []), recoverable=True)
            LOGGER.error("%s_JOB_FAILURE job_id=%s error=%s", event, job_id, result.get("error"))
