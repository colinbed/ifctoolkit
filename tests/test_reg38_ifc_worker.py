from pathlib import Path

import pytest

from backend import reg38_ifc_worker as worker
from backend.reg38_ifc_processor import ScanResult


class FakeSink:
    worker_id = "test-worker"

    def __init__(self, *, download_error=None):
        self.download_error = download_error
        self.job_updates = []
        self.file_updates = []
        self.inserted = None
        self.recovered = 0
        self.jobs = []

    def recover_stale(self, seconds):
        self.recovery_seconds = seconds
        return self.recovered

    def claim(self):
        return self.jobs.pop(0) if self.jobs else None

    def update_job(self, job, **values):
        self.job_updates.append(values)

    def update_file(self, file_id, **values):
        self.file_updates.append((file_id, values))

    def download(self, storage_path, destination: Path):
        if self.download_error:
            raise self.download_error
        destination.write_text("IFC", encoding="utf-8")

    def insert_result(self, tables):
        self.inserted = tables


JOB = {"id": "job", "ifc_file_id": "file", "project_id": "project",
       "storage_path": "project/source.ifc", "claim_token": "lease"}


def test_success_updates_progress_writes_results_and_completes(monkeypatch):
    result = ScanResult()
    result.statistics = {"ifc_schema": "IFC4", "objects": 2}
    result.tables["ifc_objects"] = [{"id": "one"}, {"id": "two"}]

    class Processor:
        def __init__(self, progress): self.progress = progress
        def process(self, *args, **kwargs):
            self.progress("VALIDATING_IFC", 5, {})
            self.progress("EXTRACTING_OBJECTS", 30, {"objects": 2})
            self.progress("COMPLETE", 100, result.statistics)
            return result

    monkeypatch.setattr(worker, "Regulation38IfcProcessor", Processor)
    sink = FakeSink()
    worker.process_job(sink, JOB)

    assert sink.inserted is result.tables
    assert [u["progress_percent"] for u in sink.job_updates[:3]] == [2, 5, 30]
    assert sink.job_updates[-1]["status"] == "COMPLETED"
    assert sink.job_updates[-1]["progress_percent"] == 100
    assert sink.job_updates[-1]["completed_at"]
    assert sink.file_updates[-1] == ("file", {"status": "PROCESSED", "ifc_schema": "IFC4"})


def test_failure_is_terminal_and_useful(monkeypatch):
    sink = FakeSink(download_error=ValueError("storage unavailable"))
    with pytest.raises(ValueError, match="storage unavailable"):
        worker.process_job(sink, JOB)
    failure = sink.job_updates[-1]
    assert failure["status"] == "FAILED" and failure["current_step"] == "FAILED"
    assert "ValueError: storage unavailable" in failure["error_message"]
    assert failure["completed_at"]
    assert sink.file_updates[-1] == ("file", {"status": "FAILED"})


def test_run_once_recovers_stale_before_claim(monkeypatch):
    sink = FakeSink()
    sink.recovered = 2
    assert worker.run_once(sink, stale_seconds=721) is False
    assert sink.recovery_seconds == 721


def test_claim_migration_is_atomic_lease_based_and_service_role_only():
    sql = Path("supabase/migrations/202608310003_reg38_worker_execution.sql").read_text().lower()
    assert "for update skip locked limit 1" in sql
    assert "status='running'" in sql
    assert "claim_token=gen_random_uuid()" in sql
    assert "grant execute on function public.claim_reg38_ifc_job(text) to service_role" in sql
    assert "recover_stale_reg38_ifc_jobs" in sql and "status='queued'" in sql


def test_lease_guard_prevents_an_old_worker_updating_reclaimed_job(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return []

    monkeypatch.setattr(worker.requests, "patch", lambda *args, **kwargs: Response())
    sink = worker.SupabaseBatchSink("https://example.test", "secret")
    with pytest.raises(worker.LostLeaseError):
        sink.update_job(JOB, progress_percent=50)


def test_result_tables_use_explicit_retry_conflict_targets(monkeypatch, caplog):
    caplog.set_level("INFO", logger="reg38.worker")
    class Response:
        ok, content, status_code, text = True, b"", 201, ""
        def raise_for_status(self): pass

    calls = []
    monkeypatch.setattr(worker.requests, "post",
                        lambda url, **kwargs: calls.append((url, kwargs)) or Response())
    sink = worker.SupabaseBatchSink("https://example.test", "secret", batch_size=10)
    sink.insert_result({"fire_requirements": [
        {"id": "one", "source_finding_key": "one"},
        {"id": "two", "source_finding_key": "two"},
    ]})
    assert calls[0][0].endswith(
        "/fire_requirements?on_conflict=project_id,ifc_object_id,requirement_type,source_scope,"
        "source_property_set,source_property_name,source_property_value,source_type")
    assert calls[0][1]["headers"]["Prefer"] == "resolution=merge-duplicates,return=minimal"
    assert '"unique_ids":2,"duplicate_id_count":0' in caplog.text


def test_failed_postgrest_response_is_logged_before_raise(monkeypatch, caplog):
    caplog.set_level("INFO", logger="reg38.worker")
    class Response:
        ok, content, status_code, text = False, b'{"message":"cardinality"}', 400, '{"message":"cardinality"}'
        def raise_for_status(self): raise RuntimeError("HTTP 400")

    monkeypatch.setattr(worker.requests, "post", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="HTTP 400"):
        worker.SupabaseBatchSink("https://example.test", "secret").insert_result(
            {"fire_requirements": [{"id": "same", "source_finding_key": "same"}]})
    assert '"response_status":400' in caplog.text
    assert "cardinality" in caplog.text
