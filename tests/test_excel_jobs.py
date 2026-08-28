import json
import time
from pathlib import Path
from types import SimpleNamespace

import app
from backend.excel_jobs import ExcelJobStore


def _wait(store, job_id):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        job = store.get(job_id)
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def _spec(tmp_path):
    return {"kind": "extract", "session_id": "session", "input_filename": "model.ifc", "input_size": 12,
            "input_path": str(tmp_path / "model.ifc"), "output_path": str(tmp_path / "model.xlsx")}


def test_job_progresses_queued_running_completed(monkeypatch, tmp_path):
    store = ExcelJobStore(tmp_path / "jobs")

    def completed(command, **kwargs):
        result_path = Path(command[-1])
        result_path.write_text(json.dumps({"status": "completed", "output_file_id": "model.xlsx", "result": {}}))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("backend.excel_jobs.subprocess.run", completed)
    job = store.create(kind="extract", session_id="session", input_filename="model.ifc", input_size=12, spec=_spec(tmp_path))
    assert job["status"] == "queued"
    finished = _wait(store, job["job_id"])
    assert finished["status"] == "completed"
    assert finished["output_file_id"] == "model.xlsx"


def test_native_worker_crash_is_reported_without_crashing_web_process(monkeypatch, tmp_path):
    store = ExcelJobStore(tmp_path / "jobs")
    monkeypatch.setattr("backend.excel_jobs.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=-11))
    job = store.create(kind="extract", session_id="session", input_filename="model.ifc", input_size=12, spec=_spec(tmp_path))
    failed = _wait(store, job["job_id"])
    assert failed["status"] == "failed"
    assert "signal 11" in failed["error"]
    assert app.health_live()["status"] == "ok"


def test_running_job_is_marked_recoverable_after_restart(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    state = {"job_id": "stale", "status": "running", "progress": 20, "message": "Reading IFC", "session_id": "s"}
    (root / "stale.json").write_text(json.dumps(state))
    recovered = ExcelJobStore(root).get("stale")
    assert recovered["status"] == "failed"
    assert recovered["recoverable"] is True
    assert "restart" in recovered["message"]


def test_extract_endpoint_queues_and_returns_job(monkeypatch, tmp_path):
    session_id = app.SESSION_STORE.create()
    root = Path(app.SESSION_STORE.ensure(session_id))
    (root / "model.ifc").write_bytes(b"IFC")
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return {"job_id": "job-1", "status": "queued", "progress": 0, "message": "Preparing model..."}

    monkeypatch.setattr(app.EXCEL_JOBS, "create", create)
    response = app.excel_extract(session_id, {"ifc_file": "model.ifc"})
    assert response == {"job_id": "job-1", "status": "queued", "progress": 0, "message": "Preparing model..."}
    assert captured["spec"]["input_path"].endswith("model.ifc")
