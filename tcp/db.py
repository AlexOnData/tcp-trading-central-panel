"""TCP database connection layer.

Implements the SESSION_CONTEXT contract from ADR-003: every check-out
sets 'aad_object_id', and every check-in resets it to NULL. Production
auth path is AAD passwordless via DefaultAzureCredential; development
fallback is SQL auth via env vars.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from uuid import UUID

import pyodbc
import structlog
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger


_DEFAULT_ODBC_DRIVER: Final[str] = "{ODBC Driver 18 for SQL Server}"
_DEFAULT_LOCAL_SERVER: Final[str] = "localhost,1433"
_DEFAULT_LOCAL_DATABASE: Final[str] = "tcp_dev"
_PWD_REDACTION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"((?:PWD|Password)=)([^;]*)", re.IGNORECASE
)

_SQL_SET_CONTEXT: Final[str] = (
    "EXEC sp_set_session_context @key = N'aad_object_id', @value = ?, @read_only = 1"
)
_SQL_RESET_CONTEXT: Final[str] = (
    "EXEC sp_set_session_context @key = N'aad_object_id', @value = NULL, @read_only = 0"
)
_SQL_READ_CONTEXT: Final[str] = (
    "SELECT CAST(SESSION_CONTEXT(N'aad_object_id') AS UNIQUEIDENTIFIER)"
)


_log: BoundLogger = structlog.get_logger(__name__)


class TcpDbError(Exception):
    """Base exception for the TCP DB connection layer."""


class SessionContextUnsetError(TcpDbError):
    """Raised when SESSION_CONTEXT('aad_object_id') is unexpectedly NULL.

    Indicates a violation of the ADR-003 contract — a code path issued a
    query without first opening the connection through ``connection_for_user``.
    """


class TcpConnectionError(TcpDbError):
    """Raised when establishing a pyodbc connection fails."""


class AuthError(TcpDbError):
    """Raised when authentication configuration is invalid or insufficient."""


class SqlConfig(BaseModel):
    """Connection-target configuration (server, database, driver, transport flags)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    server: str = Field(..., min_length=1)
    database: str = Field(..., min_length=1)
    driver: str = _DEFAULT_ODBC_DRIVER
    connect_timeout_seconds: int = Field(default=30, ge=1, le=600)
    encrypt: bool = True
    trust_server_certificate: bool = False

    @classmethod
    def from_env(cls) -> SqlConfig:
        """Build a SqlConfig from TCP_SQL_SERVER and TCP_SQL_DATABASE env vars."""
        return cls(
            server=os.environ.get("TCP_SQL_SERVER", _DEFAULT_LOCAL_SERVER),
            database=os.environ.get("TCP_SQL_DATABASE", _DEFAULT_LOCAL_DATABASE),
        )


class AuthMode(StrEnum):
    """Authentication strategy used to obtain an Azure SQL connection."""

    AAD_MANAGED_IDENTITY = "aad_managed_identity"
    AAD_DEFAULT = "aad_default"
    SQL_AUTH_DEV = "sql_auth_dev"

    @classmethod
    def from_env(cls) -> AuthMode:
        """Resolve the auth mode from environment variables.

        Precedence: explicit dev SQL creds beat the Functions/App-Service MI
        endpoint, which beats the local-developer ``DefaultAzureCredential`` chain.
        """
        if os.environ.get("TCP_SQL_DEV_USER") and os.environ.get("TCP_SQL_DEV_PASSWORD"):
            return cls.SQL_AUTH_DEV
        if os.environ.get("IDENTITY_ENDPOINT"):
            return cls.AAD_MANAGED_IDENTITY
        return cls.AAD_DEFAULT


class SessionContext(BaseModel):
    """Caller identity bound to the connection's SESSION_CONTEXT.

    The value is the AAD ``oid`` claim of the human (or service-principal)
    caller; this is what RLS predicates join against ``dim_UserRoles``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    aad_object_id: UUID


def _build_aad_kwargs(auth_mode: AuthMode) -> dict[str, str]:
    """Return the ODBC Authentication keywords for the chosen AAD mode."""
    match auth_mode:
        case AuthMode.AAD_MANAGED_IDENTITY:
            return {"Authentication": "ActiveDirectoryMsi"}
        case AuthMode.AAD_DEFAULT:
            return {"Authentication": "ActiveDirectoryDefault"}
        case AuthMode.SQL_AUTH_DEV:
            return {}
        case _:
            # Defence-in-depth: StrEnum exhaustiveness is enforced by mypy
            # --strict (so this branch is statically unreachable), but a
            # runtime guard catches a future contributor adding a new mode
            # without updating this builder. The `type: ignore[unreachable]`
            # on the body is intentional — silencing it would lose the
            # runtime safety against the not-yet-added enum case.
            msg = f"Unhandled AuthMode: {auth_mode!r}"  # type: ignore[unreachable]
            raise AuthError(msg)


def _redact(conn_str: str) -> str:
    """Return a copy of the connection string with the SQL-auth password masked."""
    return _PWD_REDACTION_PATTERN.sub(r"\1***", conn_str)


def build_connection_string(config: SqlConfig, auth_mode: AuthMode) -> str:
    """Compose a pyodbc connection string for the requested auth strategy.

    For ``SQL_AUTH_DEV`` the password is read from ``TCP_SQL_DEV_PASSWORD``
    and embedded into the returned string; callers should pass the result
    directly to pyodbc and use ``_redact`` before logging it.
    """
    parts: list[str] = [
        f"Driver={config.driver}",
        f"Server={config.server}",
        f"Database={config.database}",
        f"Encrypt={'yes' if config.encrypt else 'no'}",
        f"TrustServerCertificate={'yes' if config.trust_server_certificate else 'no'}",
        f"Connection Timeout={config.connect_timeout_seconds}",
    ]

    if auth_mode is AuthMode.SQL_AUTH_DEV:
        user = os.environ.get("TCP_SQL_DEV_USER")
        password = os.environ.get("TCP_SQL_DEV_PASSWORD")
        if not user or not password:
            msg = "SQL_AUTH_DEV requires TCP_SQL_DEV_USER and TCP_SQL_DEV_PASSWORD"
            raise AuthError(msg)
        parts.append(f"UID={user}")
        parts.append(f"PWD={password}")
        # Dev path keeps ODBC pooling enabled for fast local iteration; SESSION_CONTEXT
        # leakage is not a concern on a developer workstation.
    else:
        for key, value in _build_aad_kwargs(auth_mode).items():
            parts.append(f"{key}={value}")
        # ADR-003 §4: disable ODBC driver-level pooling on the production AAD paths so a
        # checked-in connection cannot return to the pool with a residual SESSION_CONTEXT
        # value that would leak identity to the next caller.
        parts.append("Pooling=False")

    return ";".join(parts) + ";"


def _open_raw_connection(
    config: SqlConfig | None = None,
    auth_mode: AuthMode | None = None,
) -> pyodbc.Connection:
    """Open a raw pyodbc connection without setting SESSION_CONTEXT.

    Private helper. All user-driven code paths MUST go through
    ``connection_for_user`` so the ADR-003 RLS contract is honoured.
    """
    resolved_config = config or SqlConfig.from_env()
    resolved_mode = auth_mode or AuthMode.from_env()
    conn_str = build_connection_string(resolved_config, resolved_mode)

    _log.info(
        "db.connect",
        server=resolved_config.server,
        database=resolved_config.database,
        auth_mode=resolved_mode.value,
        conn_str=_redact(conn_str),
    )

    try:
        return pyodbc.connect(conn_str, autocommit=False)
    except pyodbc.Error as exc:
        msg = f"pyodbc.connect failed for server={resolved_config.server}: {exc}"
        raise TcpConnectionError(msg) from exc


def open_connection(
    config: SqlConfig | None = None,
    auth_mode: AuthMode | None = None,
    *,
    bypass_session_context: bool = False,
) -> pyodbc.Connection:
    """Open a pyodbc connection WITHOUT setting SESSION_CONTEXT (guarded escape hatch).

    This is the documented escape hatch for infrastructure tasks (schema apply,
    smoke tests, migration tooling). The RLS predicate is deny-by-default when
    SESSION_CONTEXT is unset, but admin-scoped MIs (e.g., the generator MI)
    bypass the FILTER predicate — so an accidental ``INSERT`` here could write
    rows attributed to the wrong trader. ADR-003 §4 directs all user-driven
    paths to ``connection_for_user``; this function refuses to open a raw
    connection unless ``bypass_session_context=True`` is explicitly passed.
    """
    if not bypass_session_context:
        msg = (
            "open_connection() requires bypass_session_context=True for infrastructure "
            "tasks. Use connection_for_user(principal) for all user-driven paths "
            "(see ADR-003 §4)."
        )
        raise AuthError(msg)
    return _open_raw_connection(config=config, auth_mode=auth_mode)


@contextmanager
def connection_for_user(
    principal: SessionContext,
    *,
    config: SqlConfig | None = None,
    auth_mode: AuthMode | None = None,
) -> Iterator[pyodbc.Connection]:
    """Yield a pyodbc connection scoped to ``principal`` via SESSION_CONTEXT.

    Implements the ADR-003 contract: on entry, sets the immutable
    ``aad_object_id`` key with ``@read_only=1``; on exit (including
    exception paths), clears it before closing so a pooled connection
    cannot leak identity to the next caller.
    """
    oid_str = str(principal.aad_object_id)
    # Privacy: only the last 4 chars of the OID are emitted in logs; the full
    # OID lives in SQL audit only (ADR-003 §3).
    log = _log.bind(aad_object_id_suffix=oid_str[-4:])

    # _open_raw_connection raises before cursor creation if pyodbc.connect fails;
    # if it succeeds we own the connection and must close it on every exit path.
    conn = _open_raw_connection(config=config, auth_mode=auth_mode)
    try:
        # CR-04: cursor creation belongs inside the try so that a driver-side
        # failure between connect() and the first cursor (OOM, transport reset)
        # still falls through to conn.close() instead of leaking the connection.
        cursor = conn.cursor()
        try:
            log.debug("db.session_context.set", read_only=True)
            cursor.execute(_SQL_SET_CONTEXT, oid_str)
            log.info("db.session_context.open")
            try:
                yield conn
            finally:
                try:
                    cursor.execute(_SQL_RESET_CONTEXT)
                    log.debug("db.session_context.reset")
                except pyodbc.Error as exc:
                    log.warning("db.session_context.reset_failed", error=str(exc))
        finally:
            try:
                cursor.close()
            except pyodbc.Error:
                # Suppression is intentional: cursor.close may surface a prior
                # transport error that we have already logged and don't want to
                # mask the original exception during context-manager unwinding.
                pass
    finally:
        conn.close()
        log.info("db.session_context.close")


def set_admin_session_context(conn: pyodbc.Connection, mi_object_id: UUID) -> None:
    """Set ``SESSION_CONTEXT('aad_object_id')`` on ``conn`` to ``mi_object_id``.

    Intended for the daily-generator and bootstrap paths that authenticate as
    a Managed Identity registered in ``dim_UserRoles`` with ``scope='admin'``.
    The V001 RLS policy joins the predicate on this key; without it set the
    BLOCK predicate on ``fact_Trades`` returns 0 rows and the daily INSERT
    is rejected as 33504 (see data-engineer review CR-01).

    The key is set with ``@read_only=1`` so subsequent code paths cannot
    inadvertently clear or overwrite it for the lifetime of the connection.

    Args:
        conn: An already-open pyodbc connection (the function does not
            commit; SESSION_CONTEXT is connection-scoped, not
            transaction-scoped).
        mi_object_id: The AAD object id of the admin principal.

    Raises:
        pyodbc.Error: Propagates underlying SQL Server errors verbatim.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(_SQL_SET_CONTEXT, str(mi_object_id))
    finally:
        try:
            cursor.close()
        except pyodbc.Error:
            # See note in connection_for_user: cursor.close failures during
            # cleanup must not mask the primary exception.
            pass


def assert_session_context_set(conn: pyodbc.Connection) -> UUID:
    """Return the bound ``aad_object_id`` or raise ``SessionContextUnsetError``.

    A defensive guard at the top of user-driven code paths. The RLS
    predicate is already deny-by-default when the key is unset (ADR-003 §3),
    but raising early surfaces the bug instead of returning empty results.
    """
    cursor = conn.cursor()
    try:
        row = cursor.execute(_SQL_READ_CONTEXT).fetchone()
    finally:
        try:
            cursor.close()
        except pyodbc.Error:
            # See note in connection_for_user: cursor.close failures during
            # cleanup must not mask the primary exception.
            pass
    if row is None or row[0] is None:
        msg = "SESSION_CONTEXT('aad_object_id') is NULL; ADR-003 contract violated"
        raise SessionContextUnsetError(msg)
    value = row[0]
    if isinstance(value, UUID):
        return value
    return UUID(str(value))
