import datetime
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

import app


def test_file_retention_minutes_30_produces_real_30_minute_expiry(monkeypatch):
    monkeypatch.setenv("FILE_RETENTION_MINUTES", "30")
    ttl = app._read_file_retention_ttl()
    assert ttl == datetime.timedelta(minutes=30)


def test_invalid_retention_setting_fails_safely():
    code = "import app; app._read_file_retention_ttl()"
    env = os.environ.copy()
    env["FILE_RETENTION_MINUTES"] = "not-a-number"
    proc = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parent.parent, env=env, text=True, capture_output=True)
    assert proc.returncode != 0
    assert "FILE_RETENTION_MINUTES" in proc.stderr


def test_cleanup_removes_expired_inactive_sessions(tmp_path):
    store = app.SessionStore(str(tmp_path), ttl=datetime.timedelta(seconds=1))
    session_id = store.create()
    (tmp_path / session_id / "model.ifc").write_text("ISO-10303-21;")
    store.sessions[session_id] = app.utc_now() - datetime.timedelta(seconds=5)

    store.cleanup_stale()

    assert not (tmp_path / session_id).exists()
    assert session_id not in store.sessions


def test_unknown_session_ids_do_not_create_directories(tmp_path):
    store = app.SessionStore(str(tmp_path), ttl=datetime.timedelta(minutes=30))
    unknown = uuid.uuid4().hex

    assert not store.exists(unknown)
    with pytest.raises(HTTPException):
        store.ensure(unknown)
    assert not (tmp_path / unknown).exists()


def test_debug_endpoints_disabled_by_default():
    with pytest.raises(HTTPException) as excinfo:
        app.session_debug(uuid.uuid4().hex)
    assert excinfo.value.status_code == 404

    with pytest.raises(HTTPException) as excinfo:
        app.session_debug_routes()
    assert excinfo.value.status_code == 404


def test_native_cobie_jobs_use_session_retention_and_drop_memory_state(monkeypatch, tmp_path):
    monkeypatch.setenv("TEMP_UPLOAD_DIR", str(tmp_path))
    job_id = uuid.uuid4().hex
    job_dir = tmp_path / "cobie_qa" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "input.xlsx").write_bytes(b"placeholder")
    expired = app.utc_now().timestamp() - app.SESSION_STORE.ttl.total_seconds() - 10
    os.utime(job_dir, (expired, expired))
    app.COBIE_QA_JOBS[job_id] = {"job_dir": str(job_dir), "status": "complete"}

    app._cleanup_cobie_jobs()

    assert not job_dir.exists()
    assert job_id not in app.COBIE_QA_JOBS
