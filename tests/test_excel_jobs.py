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


def test_update_option_requires_explicit_boolean_and_defaults_off(monkeypatch):
    session_id = app.SESSION_STORE.create()
    root = Path(app.SESSION_STORE.ensure(session_id))
    (root / "model.ifc").write_bytes(b"IFC")
    (root / "model.xlsx").write_bytes(b"XLSX")
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return {"job_id": "split-job", "status": "queued", "progress": 0, "message": "Preparing"}

    monkeypatch.setattr(app.EXCEL_JOBS, "create", create)
    payload = {"ifc_file": "model.ifc", "excel_file": "model.xlsx", "add_new": "no"}
    for value, expected in [(None, False), (False, False), ("false", False), (True, True)]:
        current = dict(payload)
        if value is not None:
            current["split_shared_types"] = value
        app.excel_apply(session_id, current)
        assert captured["spec"]["split_shared_types"] is expected
        assert captured["spec"]["add_new"] == "no"


def test_worker_passes_split_option_to_writeback(monkeypatch, tmp_path):
    from backend import excel_job_runner
    captured = {}

    def writeback(*args, **kwargs):
        captured.update(kwargs)
        return {"changes": {"created_types": [{"source_guid": "source", "new_guid": "new", "name": "Group", "reassigned_occurrence_count": 2}]}}

    monkeypatch.setattr(app, "update_ifc_from_excel", writeback)
    spec = dict(_spec(tmp_path), kind="update", split_shared_types=True, add_new="no", excel_path=str(tmp_path / "model.xlsx"))
    spec_path, result_path = tmp_path / "spec.json", tmp_path / "result.json"
    spec_path.write_text(json.dumps(spec))
    excel_job_runner.main(str(spec_path), str(result_path))
    assert captured["split_shared_types"] is True
    assert captured["add_new"] == "no"
    result = json.loads(result_path.read_text())
    assert result["result"]["changes"]["created_types"][0]["reassigned_occurrence_count"] == 2
