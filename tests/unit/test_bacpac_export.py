"""Unit tests for function_app.triggers.bacpac_export.

The Azure SQL Export REST call, the Storage blob HEAD probe, and the MI
bearer-token chain are fully mocked so the tests stay hermetic. The trigger
decorator registers ``bacpac_export`` against a real ``FunctionApp`` at
module-import time, which is harmless in unit tests because we never invoke
the Functions runtime — we call the underlying Python function directly.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Final
from unittest.mock import MagicMock, patch

import azure.functions as func
import httpx
import pytest

from function_app.triggers import bacpac_export as bex
from function_app.triggers.bacpac_export import (
    BacpacConfig,
    ExportResult,
    _blob_already_exists,
    _build_target_blob_name,
    _extract_error_message,
    _extract_status,
    _load_config,
    _poll_export,
    _start_export,
    bacpac_export,
)

_ENV_PAIRS: Final[dict[str, str]] = {
    "AZURE_SUBSCRIPTION_ID": "11111111-1111-1111-1111-111111111111",
    "AZURE_RESOURCE_GROUP": "rg-tcp-prod-weu",
    "TCP_SQL_SERVER_NAME": "sql-tcp-prod-weu",
    "TCP_SQL_DATABASE_NAME": "sqldb-tcp-prod-weu",
    "TCP_SQL_ADMIN_LOGIN": "tcpadmin",
    "SQL_ADMIN_PASSWORD_EXPORT": "super-secret-password-do-not-leak",
    "TCP_BACPAC_CONTAINER_URI": "https://sttcpprodweu.blob.core.windows.net/bacpac-exports",
    "STORAGE_ACCOUNT_KEY": "ZmFrZS1zdG9yYWdlLWFjY291bnQta2V5",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin every BACPAC env var so ``_load_config`` returns a valid model."""
    for key, value in _ENV_PAIRS.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _patch_mi_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the MI bearer-token helper with a deterministic stub.

    The real helper calls ``DefaultAzureCredential().get_token(...)`` which
    reaches the Functions MI endpoint at runtime; unit tests never exercise
    that path.
    """
    monkeypatch.setattr(bex, "_get_mi_token", lambda *_args, **_kwargs: "fake-token")


def _make_timer(past_due: bool = False) -> func.TimerRequest:
    """Return a minimal ``TimerRequest`` stub with the requested ``past_due`` flag."""
    timer = MagicMock(spec=func.TimerRequest)
    timer.past_due = past_due
    return timer


def _http_response(
    *,
    status_code: int,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    text: str = "",
) -> httpx.Response:
    """Build a fully-formed ``httpx.Response`` for ``MagicMock`` return values."""
    request = httpx.Request("GET", "https://example.invalid/op")
    if json_body is not None:
        return httpx.Response(
            status_code=status_code,
            request=request,
            headers=headers or {},
            json=json_body,
        )
    return httpx.Response(
        status_code=status_code,
        request=request,
        headers=headers or {},
        text=text,
    )


# ---------------------------------------------------------------------------
# _load_config / BacpacConfig
# ---------------------------------------------------------------------------


def test_load_config_raises_when_any_env_var_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in _ENV_PAIRS:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError) as excinfo:
        _load_config()
    # Every missing env var should be enumerated so an operator can fix the
    # whole deployment in one round trip.
    for key in _ENV_PAIRS:
        assert key in str(excinfo.value)


def test_load_config_loads_all_fields(env: None) -> None:
    config = _load_config()
    assert config.subscription_id == _ENV_PAIRS["AZURE_SUBSCRIPTION_ID"]
    assert config.resource_group == _ENV_PAIRS["AZURE_RESOURCE_GROUP"]
    assert config.sql_server_name == _ENV_PAIRS["TCP_SQL_SERVER_NAME"]
    assert config.sql_database_name == _ENV_PAIRS["TCP_SQL_DATABASE_NAME"]
    assert config.sql_admin_login == _ENV_PAIRS["TCP_SQL_ADMIN_LOGIN"]
    assert config.bacpac_container_uri == _ENV_PAIRS["TCP_BACPAC_CONTAINER_URI"]
    # Secrets must be wrapped so accidental repr/str does not leak them.
    assert "super-secret-password" not in repr(config)
    assert "super-secret-password" not in str(config)
    assert (
        config.sql_admin_password.get_secret_value()
        == _ENV_PAIRS["SQL_ADMIN_PASSWORD_EXPORT"]
    )


def test_bacpac_config_strips_trailing_slash_in_container_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _ENV_PAIRS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv(
        "TCP_BACPAC_CONTAINER_URI",
        _ENV_PAIRS["TCP_BACPAC_CONTAINER_URI"] + "/",
    )
    config = _load_config()
    assert not config.bacpac_container_uri.endswith("/")


# ---------------------------------------------------------------------------
# _build_target_blob_name
# ---------------------------------------------------------------------------


def test_build_target_blob_name_format() -> None:
    assert _build_target_blob_name(date(2026, 5, 17)) == "tcp-20260517.bacpac"
    assert _build_target_blob_name(date(2026, 1, 4)) == "tcp-20260104.bacpac"


# ---------------------------------------------------------------------------
# _start_export
# ---------------------------------------------------------------------------


def _build_config() -> BacpacConfig:
    """Return a fully populated ``BacpacConfig`` without touching env vars."""
    return BacpacConfig.from_env()


def test_start_export_sends_correct_body_without_leaking_secrets(
    env: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _build_config()
    blob = "tcp-20260517.bacpac"

    captured_request: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
            captured_request["url"] = url
            captured_request["body"] = json
            captured_request["headers"] = headers
            return _http_response(
                status_code=202,
                headers={"Location": "https://management.azure.com/op/123"},
            )

    with patch.object(bex.httpx, "Client", _FakeClient):
        operation_url = _start_export(config, blob)

    assert operation_url == "https://management.azure.com/op/123"
    # Endpoint composition + API version pinned by ADR-004.
    assert "Microsoft.Sql/servers/sql-tcp-prod-weu" in captured_request["url"]
    assert "databases/sqldb-tcp-prod-weu" in captured_request["url"]
    assert "api-version=2023-08-01-preview" in captured_request["url"]
    # Bearer header is set with the stubbed MI token.
    assert captured_request["headers"]["Authorization"] == "Bearer fake-token"
    # Body conforms to the Azure SQL Export contract (ADR-004).
    body = captured_request["body"]
    assert body["storageKeyType"] == "StorageAccessKey"
    assert body["storageKey"] == _ENV_PAIRS["STORAGE_ACCOUNT_KEY"]
    assert body["storageUri"] == f"{_ENV_PAIRS['TCP_BACPAC_CONTAINER_URI']}/{blob}"
    assert body["administratorLogin"] == _ENV_PAIRS["TCP_SQL_ADMIN_LOGIN"]
    assert (
        body["administratorLoginPassword"]
        == _ENV_PAIRS["SQL_ADMIN_PASSWORD_EXPORT"]
    )
    assert body["authenticationType"] == "SQL"
    # Logs must not carry the raw secrets — caplog captures structlog routed
    # through stdlib in pytest's default configuration.
    log_blob = caplog.text
    assert _ENV_PAIRS["SQL_ADMIN_PASSWORD_EXPORT"] not in log_blob
    assert _ENV_PAIRS["STORAGE_ACCOUNT_KEY"] not in log_blob


def test_start_export_falls_back_to_operation_status_link(env: None) -> None:
    config = _build_config()

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def post(self, url: str, *, json: Any, headers: Any) -> httpx.Response:
            return _http_response(
                status_code=202,
                json_body={"operationStatusLink": "https://management.azure.com/op/abc"},
            )

    with patch.object(bex.httpx, "Client", _FakeClient):
        operation_url = _start_export(config, "tcp-20260517.bacpac")
    assert operation_url == "https://management.azure.com/op/abc"


def test_start_export_raises_on_non_2xx(env: None) -> None:
    config = _build_config()

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def post(self, url: str, *, json: Any, headers: Any) -> httpx.Response:
            return _http_response(status_code=403, text="Forbidden")

    with patch.object(bex.httpx, "Client", _FakeClient), pytest.raises(RuntimeError):
        _start_export(config, "tcp-20260517.bacpac")


# ---------------------------------------------------------------------------
# _poll_export
# ---------------------------------------------------------------------------


def test_poll_export_returns_succeeded_after_in_progress(env: None) -> None:
    config = _build_config()
    responses = [
        _http_response(status_code=200, json_body={"status": "InProgress"}),
        _http_response(status_code=200, json_body={"status": "InProgress"}),
        _http_response(status_code=200, json_body={"status": "Succeeded"}),
    ]
    sleeps: list[float] = []

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def get(self, url: str, headers: Any) -> httpx.Response:
            return responses.pop(0)

    with patch.object(bex.httpx, "Client", _FakeClient):
        result = _poll_export(
            config,
            "https://management.azure.com/op/123",
            max_minutes=1,
            sleep=sleeps.append,
        )

    assert result == ExportResult(status="Succeeded", error=None)
    # Two sleeps for the two InProgress polls; the terminal poll skips sleep.
    assert sleeps == [10, 10]


def test_poll_export_raises_timeout_when_only_in_progress(env: None) -> None:
    config = _build_config()

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def get(self, url: str, headers: Any) -> httpx.Response:
            return _http_response(status_code=200, json_body={"status": "InProgress"})

    sleeps: list[float] = []
    with (
        patch.object(bex.httpx, "Client", _FakeClient),
        pytest.raises(TimeoutError),
    ):
        # max_minutes=1 → 6 attempts (60 s / 10 s) before the loop gives up.
        _poll_export(
            config,
            "https://management.azure.com/op/123",
            max_minutes=1,
            sleep=sleeps.append,
        )
    assert len(sleeps) == 6  # 1 min / 10s polling interval


def test_poll_export_returns_failed_with_error_message(env: None) -> None:
    config = _build_config()

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def get(self, url: str, headers: Any) -> httpx.Response:
            return _http_response(
                status_code=200,
                json_body={"status": "Failed", "error": {"message": "boom"}},
            )

    with patch.object(bex.httpx, "Client", _FakeClient):
        result = _poll_export(
            config,
            "https://management.azure.com/op/123",
            max_minutes=1,
            sleep=lambda _: None,
        )
    assert result.status == "Failed"
    assert result.error == "boom"


# ---------------------------------------------------------------------------
# Status / error parsing helpers
# ---------------------------------------------------------------------------


def test_extract_status_handles_nested_properties_shape() -> None:
    assert _extract_status({"properties": {"status": "Succeeded"}}) == "Succeeded"
    assert _extract_status({"status": "Failed"}) == "Failed"
    assert _extract_status({}) == "InProgress"
    assert _extract_status("not-a-dict") == "InProgress"


def test_extract_error_message_prefers_nested_error_message() -> None:
    assert _extract_error_message({"error": {"message": "x"}}) == "x"
    assert (
        _extract_error_message({"properties": {"errorMessage": "y"}}) == "y"
    )
    assert _extract_error_message({}) is None


# ---------------------------------------------------------------------------
# bacpac_export — top-level trigger
# ---------------------------------------------------------------------------


def test_bacpac_export_short_circuits_when_blob_already_exists(
    env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bex, "_blob_already_exists", lambda *_a, **_kw: True)
    started = MagicMock()
    monkeypatch.setattr(bex, "_start_export", started)
    polled = MagicMock()
    monkeypatch.setattr(bex, "_poll_export", polled)

    bacpac_export(_make_timer(past_due=False))

    started.assert_not_called()
    polled.assert_not_called()


def test_bacpac_export_full_happy_path(
    env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bex, "_blob_already_exists", lambda *_a, **_kw: False)
    monkeypatch.setattr(
        bex,
        "_start_export",
        lambda *_a, **_kw: "https://management.azure.com/op/xyz",
    )
    monkeypatch.setattr(
        bex,
        "_poll_export",
        lambda *_a, **_kw: ExportResult(status="Succeeded", error=None),
    )
    monkeypatch.setattr(bex, "_blob_size", lambda *_a, **_kw: 1024 * 1024)

    # Past_due is logged but does NOT short-circuit the run.
    bacpac_export(_make_timer(past_due=True))


def test_bacpac_export_raises_when_poll_returns_failed(
    env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bex, "_blob_already_exists", lambda *_a, **_kw: False)
    monkeypatch.setattr(
        bex,
        "_start_export",
        lambda *_a, **_kw: "https://management.azure.com/op/xyz",
    )
    monkeypatch.setattr(
        bex,
        "_poll_export",
        lambda *_a, **_kw: ExportResult(status="Failed", error="quota exceeded"),
    )

    with pytest.raises(RuntimeError, match="Failed"):
        bacpac_export(_make_timer(past_due=False))


# ---------------------------------------------------------------------------
# _blob_already_exists — wrapper round-trip
# ---------------------------------------------------------------------------


def test_blob_already_exists_returns_true_on_2xx(env: None) -> None:
    config = _build_config()

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def head(self, url: str, headers: Any) -> httpx.Response:
            return _http_response(status_code=200, headers={"Content-Length": "12345"})

    with patch.object(bex.httpx, "Client", _FakeClient):
        assert _blob_already_exists(config, "tcp-20260517.bacpac") is True


def test_blob_already_exists_returns_false_on_404(env: None) -> None:
    config = _build_config()

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def head(self, url: str, headers: Any) -> httpx.Response:
            return _http_response(status_code=404)

    with patch.object(bex.httpx, "Client", _FakeClient):
        assert _blob_already_exists(config, "tcp-20260517.bacpac") is False
