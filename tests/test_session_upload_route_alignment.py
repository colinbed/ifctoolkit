import uuid
import asyncio
import io
from pathlib import Path

from fastapi import UploadFile
from fastapi import HTTPException

import app


def _upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


def test_route_list_includes_shared_session_upload_paths():
    route_paths = {getattr(route, "path", "") for route in app.app.routes}
    assert "/api/session/{session_id}/files" in route_paths
    assert "/api/session/{session_id}/upload" in route_paths


def test_get_files_for_created_session_returns_empty_list():
    session_id = app.SESSION_STORE.create()
    payload = app.list_files(session_id)
    assert payload["files"] == []


def test_get_files_for_unknown_session_returns_404_without_creating_directory():
    session_id = uuid.uuid4().hex
    path = app.SESSION_STORE.session_path(session_id)
    try:
        app.list_files(session_id)
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail["code"] == "SESSION_NOT_FOUND"
        assert exc.detail["recoverable"] is True
    else:
        raise AssertionError("Expected HTTPException for unknown session id")
    assert not app.os.path.exists(path)


def test_create_session_replaces_stale_processing_session():
    stale_id = uuid.uuid4().hex
    payload = app.create_session({"session_id": stale_id})

    assert payload["status"] == "recovered"
    assert payload["session_id"] != stale_id
    assert app.SESSION_STORE.exists(payload["session_id"])


def test_create_session_reuses_existing_processing_session():
    session_id = app.SESSION_STORE.create()
    payload = app.create_session({"session_id": session_id})

    assert payload["status"] == "ready"
    assert payload["session_id"] == session_id


def test_shared_frontend_recovers_once_and_uses_tab_scoped_storage():
    source = (Path(app.__file__).parent / "static" / "session_shared.js").read_text(encoding="utf-8")

    assert "sessionStorage.setItem(STORAGE_KEY" in source
    assert "localStorage.removeItem(STORAGE_KEY)" in source
    assert "!options.retried && isSessionNotFound" in source
    assert "recoverSession" in source


def test_upload_frontend_retries_once_after_processing_session_recovery():
    source = (Path(app.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")

    assert "isSessionNotFound?.(uploadError.status, uploadError.body)" in source
    assert "recoverSession(activeSessionId)" in source


def test_upload_then_list_files_roundtrip():
    session_id = app.SESSION_STORE.create()
    uploaded = asyncio.run(app.upload_files(session_id, [_upload("sample.ifc", b"ISO-10303-21;\n")]))
    assert uploaded["files"][0]["id"] == "sample.ifc"

    files = app.list_files(session_id)["files"]
    assert any(item["id"] == "sample.ifc" for item in files)


def test_invalid_session_id_format_returns_400_for_listing():
    try:
        app.list_files("bad-session-id")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "Invalid session id format" in str(exc.detail)
    else:
        raise AssertionError("Expected HTTPException for invalid session id")
