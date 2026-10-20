from __future__ import annotations

import pytest

import ifc_app.supabase_auth as supabase_auth
from ifc_app.reg38_projects import Regulation38Repository


class SuccessfulResponse:
    status_code = 200
    content = b"{}"
    text = "{}"

    @staticmethod
    def json():
        return {}


@pytest.fixture
def service():
    settings = supabase_auth.AuthSettings(
        app_url="https://app.example",
        supabase_url="https://project.supabase.co",
        publishable_key="publishable-key",
        request_timeout_seconds=11,
    )
    return supabase_auth.SupabaseAuthService(settings)


def capture_request(monkeypatch):
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return SuccessfulResponse()

    monkeypatch.setattr(supabase_auth.requests, "request", request)
    return calls


def test_request_json_sends_default_auth_headers_once(service, monkeypatch):
    calls = capture_request(monkeypatch)

    service._request_json("GET", "https://project.supabase.co/rest/v1/projects",
                          access_token="user-token", public_error="failed")

    assert len(calls) == 1
    assert calls[0][1]["headers"] == {
        "apikey": "publishable-key", "Accept": "application/json",
        "Content-Type": "application/json", "Authorization": "Bearer user-token",
    }


def test_request_json_merges_custom_headers_with_defaults(service, monkeypatch):
    calls = capture_request(monkeypatch)

    service._request_json("POST", "https://project.supabase.co/rest/v1/example",
                          access_token="user-token", public_error="failed",
                          headers={"X-Request-Mode": "custom"}, json={"ok": True})

    headers = calls[0][1]["headers"]
    assert headers["Authorization"] == "Bearer user-token"
    assert headers["apikey"] == "publishable-key"
    assert headers["X-Request-Mode"] == "custom"


def test_request_json_preserves_postgrest_prefer_header(service, monkeypatch):
    calls = capture_request(monkeypatch)
    prefer = "resolution=merge-duplicates,return=minimal"

    service._request_json("POST", "https://project.supabase.co/rest/v1/fire_strategy_reviews",
                          access_token="user-token", public_error="failed",
                          headers={"Prefer": prefer}, json=[])

    assert calls[0][1]["headers"]["Prefer"] == prefer


def test_request_json_removes_explicit_transport_kwargs_before_forwarding(service, monkeypatch):
    calls = capture_request(monkeypatch)

    # Before the fix, supplying headers here raised "multiple values for
    # keyword argument 'headers'" before requests.request could run.
    service._request_json("POST", "https://project.supabase.co/rest/v1/example",
                          access_token="user-token", public_error="failed",
                          headers={"Prefer": "return=representation"}, timeout=3,
                          params={"on_conflict": "id"}, json={"id": "one"})

    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 3
    assert calls[0][1]["params"] == {"on_conflict": "id"}
    assert calls[0][1]["json"] == {"id": "one"}


def test_fire_strategy_repository_upsert_merges_prefer_and_auth_headers(service, monkeypatch):
    calls = capture_request(monkeypatch)

    Regulation38Repository(service)._data_request(
        "POST",
        "fire_strategy_reviews?on_conflict=project_id,model_id,ifc_global_id",
        "user-token",
        json=[{"ifc_global_id": "door-guid"}],
        headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
    )

    args, kwargs = calls[0]
    assert args[:2] == ("POST", "https://project.supabase.co/rest/v1/fire_strategy_reviews?on_conflict=project_id,model_id,ifc_global_id")
    assert kwargs["headers"]["Authorization"] == "Bearer user-token"
    assert kwargs["headers"]["Prefer"] == "resolution=merge-duplicates,return=minimal"
