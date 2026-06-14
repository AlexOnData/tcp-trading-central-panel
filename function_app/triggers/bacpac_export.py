"""TimerTrigger_BacpacExport — weekly Azure SQL BACPAC export (ADR-004).

Cron: ``0 0 8 * * 0`` (Sunday 08:00 in ``WEBSITE_TIME_ZONE = E. Europe Standard Time``).
The trigger calls the Azure Management REST API to start an asynchronous BACPAC
export of ``sqldb-tcp-prod-weu`` into the ``bacpac-exports`` blob container, then
polls the returned ``Location`` URL until the operation reaches a terminal state.

Operational contract (per ADR-004 §"Implementation contract"):

1. Identity: Function App system-assigned MI (bearer token via DefaultAzureCredential).
2. RBAC: ``SQL DB Contributor`` on the database, ``Storage Blob Data Contributor`` on
   ``bacpac-exports`` — both wired by Bicep in Etapa 4.
3. Endpoint: ``POST {mgmt}/subscriptions/{sub}/resourceGroups/{rg}/providers/
   Microsoft.Sql/servers/{server}/databases/{db}/export?api-version=2023-08-01-preview``.
4. Payload: target blob URI, storage-account key (via KV reference),
   ``administratorLogin = 'tcpadmin'``, ``administratorLoginPassword`` via
   ``SQL-ADMIN-PASSWORD-EXPORT`` KV secret (see ADR-004 §"Open caveat").
5. Poll cadence: 10 s, cap 30 min, then surface a timeout to App Insights.
6. Metrics: ``tcp.bacpac.duration_ms``, ``tcp.bacpac.size_bytes``, ``tcp.bacpac.status``.

Idempotency: if ``bacpac-exports/tcp-YYYYMMDD.bacpac`` already exists for today,
the trigger short-circuits with ``status='already_exists'`` instead of starting a
duplicate export. The Storage lifecycle policy (28 d) prunes older snapshots.
"""

from __future__ import annotations

import os
import time as time_mod
from collections.abc import Callable
from datetime import date, datetime
from typing import Final, Literal
from zoneinfo import ZoneInfo

import azure.functions as func
import httpx as httpx  # noqa: PLC0414  # explicit re-export for monkeypatch tests
import structlog
from azure.identity import DefaultAzureCredential
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from function_app import app

_log = structlog.get_logger(__name__)

_TZ_BUCHAREST: Final[ZoneInfo] = ZoneInfo("Europe/Bucharest")

_ENV_SUBSCRIPTION_ID: Final[str] = "AZURE_SUBSCRIPTION_ID"
_ENV_RESOURCE_GROUP: Final[str] = "AZURE_RESOURCE_GROUP"
_ENV_SQL_SERVER_NAME: Final[str] = "TCP_SQL_SERVER_NAME"
_ENV_SQL_DATABASE_NAME: Final[str] = "TCP_SQL_DATABASE_NAME"
_ENV_BACPAC_CONTAINER_URI: Final[str] = "TCP_BACPAC_CONTAINER_URI"
_ENV_BACPAC_STORAGE_KEY: Final[str] = "STORAGE_ACCOUNT_KEY"
_ENV_SQL_ADMIN_LOGIN: Final[str] = "TCP_SQL_ADMIN_LOGIN"
_ENV_SQL_ADMIN_PASSWORD: Final[str] = "SQL_ADMIN_PASSWORD_EXPORT"

_MANAGEMENT_RESOURCE: Final[str] = "https://management.azure.com/.default"
_STORAGE_RESOURCE: Final[str] = "https://storage.azure.com/.default"
_MANAGEMENT_BASE: Final[str] = "https://management.azure.com"
_EXPORT_API_VERSION: Final[str] = "2023-08-01-preview"
_BLOB_API_VERSION: Final[str] = "2023-11-03"

# Polling configuration: 180 attempts × 10 s = 30 min cap.
_POLL_INTERVAL_SECONDS: Final[int] = 10
_POLL_MAX_MINUTES_DEFAULT: Final[int] = 30
_HTTP_TIMEOUT_SECONDS: Final[float] = 30.0


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class BacpacConfig(BaseModel):
    """Configuration loaded from App Settings for a BACPAC export run.

    Every field is required; ``from_env`` raises ``ValueError`` if any of the
    backing env vars is missing or empty. Secrets are wrapped in ``SecretStr``
    so accidental ``repr``/``str`` calls produce ``'**********'`` instead of
    leaking the value into App Insights or stdout.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: str = Field(..., min_length=1)
    resource_group: str = Field(..., min_length=1)
    sql_server_name: str = Field(..., min_length=1)
    sql_database_name: str = Field(..., min_length=1)
    sql_admin_login: str = Field(..., min_length=1)
    sql_admin_password: SecretStr
    bacpac_container_uri: str = Field(..., min_length=1)
    storage_account_key: SecretStr

    @classmethod
    def from_env(cls) -> BacpacConfig:
        """Build a ``BacpacConfig`` from the documented App Setting env vars.

        Raises ``ValueError`` listing every missing variable so an operator can
        fix the deployment in one pass instead of failing on the first miss.
        """
        spec: dict[str, str] = {
            "subscription_id": _ENV_SUBSCRIPTION_ID,
            "resource_group": _ENV_RESOURCE_GROUP,
            "sql_server_name": _ENV_SQL_SERVER_NAME,
            "sql_database_name": _ENV_SQL_DATABASE_NAME,
            "sql_admin_login": _ENV_SQL_ADMIN_LOGIN,
            "sql_admin_password": _ENV_SQL_ADMIN_PASSWORD,
            "bacpac_container_uri": _ENV_BACPAC_CONTAINER_URI,
            "storage_account_key": _ENV_BACPAC_STORAGE_KEY,
        }
        values: dict[str, str] = {}
        missing: list[str] = []
        for field_name, env_name in spec.items():
            raw = os.environ.get(env_name, "").strip()
            if not raw:
                missing.append(env_name)
            else:
                values[field_name] = raw
        if missing:
            msg = "BacpacConfig missing required env vars: " + ", ".join(sorted(missing))
            raise ValueError(msg)
        return cls(
            subscription_id=values["subscription_id"],
            resource_group=values["resource_group"],
            sql_server_name=values["sql_server_name"],
            sql_database_name=values["sql_database_name"],
            sql_admin_login=values["sql_admin_login"],
            sql_admin_password=SecretStr(values["sql_admin_password"]),
            bacpac_container_uri=values["bacpac_container_uri"].rstrip("/"),
            storage_account_key=SecretStr(values["storage_account_key"]),
        )


class ExportResult(BaseModel):
    """Terminal (or in-progress) status returned by the Export operation poll."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["InProgress", "Succeeded", "Failed", "Canceled"]
    error: str | None = None


# ---------------------------------------------------------------------------
# Helpers — token, URLs, idempotency
# ---------------------------------------------------------------------------


def _load_config() -> BacpacConfig:
    """Return a ``BacpacConfig`` populated from env, raising on any missing var."""
    return BacpacConfig.from_env()


def _get_mi_token(resource: str = _MANAGEMENT_RESOURCE) -> str:
    """Return a bearer token for ``resource`` using the Function App's MI.

    Wraps ``DefaultAzureCredential().get_token(...)`` so unit tests can patch
    the symbol in one place. The credential chain resolves to the Functions
    MI endpoint in production and to a developer credential locally.
    """
    credential = DefaultAzureCredential()
    return credential.get_token(resource).token


def _build_target_blob_name(today: date) -> str:
    """Return the blob name ``tcp-YYYYMMDD.bacpac`` for the export date."""
    return f"tcp-{today.strftime('%Y%m%d')}.bacpac"


def _blob_url(config: BacpacConfig, blob_name: str) -> str:
    """Compose the absolute blob URL ``<container_uri>/<blob_name>``."""
    return f"{config.bacpac_container_uri}/{blob_name}"


def _export_endpoint(config: BacpacConfig) -> str:
    """Return the management-plane URL for the database Export action."""
    return (
        f"{_MANAGEMENT_BASE}/subscriptions/{config.subscription_id}"
        f"/resourceGroups/{config.resource_group}"
        f"/providers/Microsoft.Sql/servers/{config.sql_server_name}"
        f"/databases/{config.sql_database_name}"
        f"/export?api-version={_EXPORT_API_VERSION}"
    )


def _blob_already_exists(config: BacpacConfig, blob_name: str) -> bool:
    """Return True iff ``HEAD <blob_url>`` succeeds with a 2xx response.

    Uses an MI bearer token against the Storage data plane (``Storage Blob
    Data Contributor`` is wired by Bicep). A 404 means the blob does not yet
    exist for today and the workflow proceeds; any other non-2xx is raised so
    the timer retry policy can fire.
    """
    token = _get_mi_token(_STORAGE_RESOURCE)
    headers = {
        "Authorization": f"Bearer {token}",
        "x-ms-version": _BLOB_API_VERSION,
    }
    url = _blob_url(config, blob_name)
    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        response = client.head(url, headers=headers)
    if response.status_code == 404:
        return False
    if 200 <= response.status_code < 300:
        return True
    msg = (
        f"Unexpected status {response.status_code} probing blob "
        f"{blob_name} (Azure Storage HEAD)."
    )
    raise RuntimeError(msg)


def _start_export(config: BacpacConfig, blob_name: str) -> str:
    """POST to the Export endpoint and return the operation polling URL.

    The Azure SQL Export API is asynchronous: a successful call returns
    ``202 Accepted`` with a ``Location`` header pointing to a status URL that
    the caller must GET until the operation reaches a terminal state. The
    body materialises the SQL admin credentials and the storage-account key
    only at call time and is **never** logged in full (we redact secrets
    before emitting any structlog event).
    """
    token = _get_mi_token(_MANAGEMENT_RESOURCE)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "storageKeyType": "StorageAccessKey",
        "storageKey": config.storage_account_key.get_secret_value(),
        "storageUri": _blob_url(config, blob_name),
        "administratorLogin": config.sql_admin_login,
        "administratorLoginPassword": config.sql_admin_password.get_secret_value(),
        "authenticationType": "SQL",
    }
    # Redacted body for logging only — secrets are stripped before emission.
    redacted_body = {
        **body,
        "storageKey": "***",
        "administratorLoginPassword": "***",
    }
    _log.info(
        "tcp.bacpac.request",
        endpoint=_export_endpoint(config),
        body=redacted_body,
    )

    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        response = client.post(_export_endpoint(config), json=body, headers=headers)
    if response.status_code not in (200, 201, 202):
        # security MN-03: log the truncated Azure response body at debug
        # level (which observability sampling can drop), but keep the
        # raised RuntimeError generic so the App Insights ``exception``
        # event does not echo internal resource ids / partial credentials.
        _log.debug(
            "tcp.bacpac.start_api_error_body",
            status_code=response.status_code,
            body_snippet=response.text[:256],
        )
        msg = f"Export API rejected the request: HTTP {response.status_code}"
        raise RuntimeError(msg)
    location = response.headers.get("Location") or response.headers.get("location")
    if not location:
        # Some preview API versions inline ``operationStatusLink`` in the body
        # instead of the Location header — accept that form as well.
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        location = payload.get("operationStatusLink") if isinstance(payload, dict) else None
    if not location:
        msg = "Export API response missing Location header and operationStatusLink."
        raise RuntimeError(msg)
    # Etapa-11 typing fix: `payload.get(...)` returns `Any`; assert the
    # operation-link is a str so callers don't have to re-narrow.
    if not isinstance(location, str):
        msg = "Export API operationStatusLink was not a string."
        raise RuntimeError(msg)
    return location


def _poll_export(
    config: BacpacConfig,  # noqa: ARG001  # reserved for future per-tenant tracing
    operation_url: str,
    max_minutes: int = _POLL_MAX_MINUTES_DEFAULT,
    *,
    sleep: Callable[[float], None] | None = None,
) -> ExportResult:
    """Poll ``operation_url`` every 10 s until terminal or ``max_minutes`` elapse.

    Raises ``TimeoutError`` if the operation has not reached a terminal state
    within the allotted window — the timer retry policy then surfaces the
    failure to App Insights. The injectable ``sleep`` parameter exists so the
    unit tests can fast-forward without burning real wall-time.
    """
    token = _get_mi_token(_MANAGEMENT_RESOURCE)
    headers = {"Authorization": f"Bearer {token}"}
    sleeper = sleep if sleep is not None else time_mod.sleep
    max_attempts = max(1, (max_minutes * 60) // _POLL_INTERVAL_SECONDS)

    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        for attempt in range(1, max_attempts + 1):
            response = client.get(operation_url, headers=headers)
            if response.status_code >= 400:
                # security MN-03: same redaction posture as _start_export.
                _log.debug(
                    "tcp.bacpac.poll_api_error_body",
                    status_code=response.status_code,
                    body_snippet=response.text[:256],
                )
                msg = f"Poll GET failed: HTTP {response.status_code}"
                raise RuntimeError(msg)
            payload = response.json() if response.content else {}
            status = _extract_status(payload)
            error_message = _extract_error_message(payload)
            _log.info(
                "tcp.bacpac.poll",
                attempt=attempt,
                status=status,
                operation_url=operation_url,
            )
            if status in ("Succeeded", "Failed", "Canceled"):
                return ExportResult(status=status, error=error_message)
            sleeper(_POLL_INTERVAL_SECONDS)

    msg = (
        f"BACPAC export polling timed out after {max_minutes} min "
        f"({max_attempts} attempts at {_POLL_INTERVAL_SECONDS}s)."
    )
    raise TimeoutError(msg)


def _extract_status(payload: object) -> Literal["InProgress", "Succeeded", "Failed", "Canceled"]:
    """Return the operation status from a poll-response payload.

    Defends against the two response shapes Azure SQL emits during a long
    export: the bare ``{status: ...}`` form (early in the operation) and the
    nested ``{properties: {status: ...}}`` form once provisioning completes.
    Anything we cannot parse maps to ``InProgress`` so the loop keeps going
    until either a terminal state arrives or ``max_minutes`` elapses.
    """
    if not isinstance(payload, dict):
        return "InProgress"
    raw = payload.get("status")
    if raw is None:
        properties = payload.get("properties")
        if isinstance(properties, dict):
            raw = properties.get("status")
    # Etapa-11 typing fix: narrow `raw: Any` to the function's return Literal
    # by explicit membership check + cast-via-assert. The previous
    # `# type: ignore[return-value]` was the wrong code; mypy reports
    # `no-any-return` for the `Any` payload value. The pattern below preserves
    # the same defensive runtime behaviour without a `type: ignore`.
    if raw == "Succeeded":
        return "Succeeded"
    if raw == "Failed":
        return "Failed"
    if raw == "Canceled":
        return "Canceled"
    if raw == "InProgress":
        return "InProgress"
    return "InProgress"


def _extract_error_message(payload: object) -> str | None:
    """Return a human-readable error string from a poll payload, if present."""
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    properties = payload.get("properties")
    if isinstance(properties, dict):
        nested = properties.get("errorMessage")
        if isinstance(nested, str) and nested:
            return nested
    return None


def _blob_size(config: BacpacConfig, blob_name: str) -> int:
    """Return the size in bytes of the exported blob via a ``HEAD`` request.

    The Storage REST API returns the size in the ``Content-Length`` header.
    Failure to read the size is non-fatal — the export itself already
    succeeded — so the caller treats ``0`` as "unknown" rather than retrying.
    """
    token = _get_mi_token(_STORAGE_RESOURCE)
    headers = {
        "Authorization": f"Bearer {token}",
        "x-ms-version": _BLOB_API_VERSION,
    }
    url = _blob_url(config, blob_name)
    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        response = client.head(url, headers=headers)
    if response.status_code < 200 or response.status_code >= 300:
        _log.warning(
            "tcp.bacpac.size_probe_failed",
            status_code=response.status_code,
            target=blob_name,
        )
        return 0
    raw = response.headers.get("Content-Length") or response.headers.get("content-length")
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------


@app.timer_trigger(
    schedule="0 0 8 * * 0",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def bacpac_export(timer: func.TimerRequest) -> None:
    """Export a BACPAC to ``bacpac-exports/tcp-{YYYYMMDD}.bacpac``.

    Cron: ``0 0 8 * * 0`` (Sunday 08:00 Europe/Bucharest, per ADR-004).
    Polls the Azure SQL Export API for up to 30 minutes; emits
    ``tcp.bacpac.{duration_ms,size_bytes,status}`` metrics through structlog
    so the App Insights §12 alert can fire on a missed run.
    """
    start = time_mod.perf_counter()
    log = _log.bind(trigger="bacpac_export")
    if timer.past_due:
        log.warning("tcp.bacpac.past_due")

    try:
        config = _load_config()
        today_ro = datetime.now(_TZ_BUCHAREST).date()
        target_blob = _build_target_blob_name(today_ro)

        if _blob_already_exists(config, target_blob):
            duration_ms = int((time_mod.perf_counter() - start) * 1000)
            log.info(
                "tcp.bacpac.skipped",
                reason="already_exists",
                target=target_blob,
                duration_ms=duration_ms,
                status="already_exists",
            )
            return

        operation_url = _start_export(config, target_blob)
        log.info(
            "tcp.bacpac.export_started",
            operation_url=operation_url,
            target=target_blob,
        )

        result = _poll_export(config, operation_url, max_minutes=_POLL_MAX_MINUTES_DEFAULT)
        if result.status != "Succeeded":
            msg = f"BACPAC export failed: {result.status} {result.error or ''}".strip()
            raise RuntimeError(msg)

        duration_ms = int((time_mod.perf_counter() - start) * 1000)
        size_bytes = _blob_size(config, target_blob)
        log.info(
            "tcp.bacpac.complete",
            duration_ms=duration_ms,
            size_bytes=size_bytes,
            target=target_blob,
            status="succeeded",
        )
    except Exception:
        # Capture wall-time even on the failure path so the alert query can
        # distinguish a fast failure (config error) from a 30-min timeout.
        duration_ms = int((time_mod.perf_counter() - start) * 1000)
        log.exception(
            "tcp.bacpac.failed",
            duration_ms=duration_ms,
            status="failed",
        )
        raise
