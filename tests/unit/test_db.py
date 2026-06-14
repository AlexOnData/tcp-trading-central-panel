"""Unit tests for tcp.db — fully mocked, no live SQL Server required."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pyodbc
import pytest
from pydantic import ValidationError

from tcp.db import (
    AuthError,
    AuthMode,
    SessionContext,
    SessionContextUnsetError,
    SqlConfig,
    _redact,
    assert_session_context_set,
    build_connection_string,
    connection_for_user,
)

_TEST_OID = UUID("11111111-2222-3333-4444-555555555555")


# ---------------------------------------------------------------------------
# SqlConfig.from_env
# ---------------------------------------------------------------------------


def test_sql_config_from_env_uses_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TCP_SQL_SERVER", raising=False)
    monkeypatch.delenv("TCP_SQL_DATABASE", raising=False)

    cfg = SqlConfig.from_env()

    assert cfg.server == "localhost,1433"
    assert cfg.database == "tcp_dev"
    assert cfg.driver == "{ODBC Driver 18 for SQL Server}"
    assert cfg.encrypt is True
    assert cfg.trust_server_certificate is False
    assert cfg.connect_timeout_seconds == 30


def test_sql_config_from_env_reads_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TCP_SQL_SERVER", "sql-tcp-prod-weu.database.windows.net")
    monkeypatch.setenv("TCP_SQL_DATABASE", "sqldb-tcp-prod-weu")

    cfg = SqlConfig.from_env()

    assert cfg.server == "sql-tcp-prod-weu.database.windows.net"
    assert cfg.database == "sqldb-tcp-prod-weu"


def test_sql_config_is_frozen() -> None:
    cfg = SqlConfig(server="s", database="d")
    with pytest.raises(ValidationError):
        # Pydantic v2's frozen=True raises ValidationError on attribute
        # mutation. The earlier `# type: ignore[misc]` is no longer needed
        # since pydantic's typing now exposes the right `__setattr__` shape.
        cfg.server = "other"


# ---------------------------------------------------------------------------
# AuthMode.from_env
# ---------------------------------------------------------------------------


def test_auth_mode_from_env_prefers_sql_auth_when_dev_creds_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TCP_SQL_DEV_USER", "sa")
    monkeypatch.setenv("TCP_SQL_DEV_PASSWORD", "secret")
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://169.254.169.254/")

    assert AuthMode.from_env() is AuthMode.SQL_AUTH_DEV


def test_auth_mode_from_env_managed_identity_when_endpoint_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TCP_SQL_DEV_USER", raising=False)
    monkeypatch.delenv("TCP_SQL_DEV_PASSWORD", raising=False)
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://169.254.169.254/")

    assert AuthMode.from_env() is AuthMode.AAD_MANAGED_IDENTITY


def test_auth_mode_from_env_default_when_nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TCP_SQL_DEV_USER", raising=False)
    monkeypatch.delenv("TCP_SQL_DEV_PASSWORD", raising=False)
    monkeypatch.delenv("IDENTITY_ENDPOINT", raising=False)

    assert AuthMode.from_env() is AuthMode.AAD_DEFAULT


# ---------------------------------------------------------------------------
# build_connection_string
# ---------------------------------------------------------------------------


def test_build_connection_string_aad_default() -> None:
    cfg = SqlConfig(server="s.database.windows.net", database="sqldb")

    s = build_connection_string(cfg, AuthMode.AAD_DEFAULT)

    assert "Driver={ODBC Driver 18 for SQL Server}" in s
    assert "Server=s.database.windows.net" in s
    assert "Database=sqldb" in s
    assert "Encrypt=yes" in s
    assert "TrustServerCertificate=no" in s
    assert "Authentication=ActiveDirectoryDefault" in s
    assert "UID=" not in s
    assert "PWD=" not in s


def test_build_connection_string_aad_managed_identity() -> None:
    cfg = SqlConfig(server="s", database="d")

    s = build_connection_string(cfg, AuthMode.AAD_MANAGED_IDENTITY)

    assert "Authentication=ActiveDirectoryMsi" in s
    assert "UID=" not in s


def test_build_connection_string_sql_auth_dev_embeds_creds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TCP_SQL_DEV_USER", "sa")
    monkeypatch.setenv("TCP_SQL_DEV_PASSWORD", "p@ssw0rd!")
    cfg = SqlConfig(server="s", database="d")

    s = build_connection_string(cfg, AuthMode.SQL_AUTH_DEV)

    assert "UID=sa" in s
    assert "PWD=p@ssw0rd!" in s
    assert "Authentication=" not in s


def test_build_connection_string_sql_auth_dev_raises_when_creds_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TCP_SQL_DEV_USER", raising=False)
    monkeypatch.delenv("TCP_SQL_DEV_PASSWORD", raising=False)
    cfg = SqlConfig(server="s", database="d")

    with pytest.raises(AuthError):
        build_connection_string(cfg, AuthMode.SQL_AUTH_DEV)


# ---------------------------------------------------------------------------
# _redact
# ---------------------------------------------------------------------------


def test_redact_masks_password() -> None:
    raw = "Driver={...};Server=s;UID=sa;PWD=secret;Encrypt=yes;"
    redacted = _redact(raw)
    assert "PWD=***" in redacted
    assert "secret" not in redacted
    assert "UID=sa" in redacted


def test_redact_is_noop_when_no_password() -> None:
    raw = "Driver={...};Server=s;Authentication=ActiveDirectoryDefault;"
    assert _redact(raw) == raw


def test_redact_is_case_insensitive() -> None:
    raw = "Driver={...};pwd=topsecret;"
    assert "topsecret" not in _redact(raw)


# ---------------------------------------------------------------------------
# connection_for_user — the ADR-003 contract
# ---------------------------------------------------------------------------


def _make_mock_connection() -> tuple[MagicMock, MagicMock]:
    """Return (conn, cursor) mocks wired so conn.cursor() returns the cursor."""
    cursor = MagicMock(name="cursor")
    conn = MagicMock(name="connection", spec=pyodbc.Connection)
    conn.cursor.return_value = cursor
    return conn, cursor


def _patched_open(monkeypatch: pytest.MonkeyPatch, conn: MagicMock) -> None:
    monkeypatch.setattr("tcp.db.pyodbc.connect", lambda *a, **k: conn)


def test_connection_for_user_sets_and_resets_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, cursor = _make_mock_connection()
    _patched_open(monkeypatch, conn)
    principal = SessionContext(aad_object_id=_TEST_OID)
    cfg = SqlConfig(server="s", database="d")

    with connection_for_user(principal, config=cfg, auth_mode=AuthMode.AAD_DEFAULT) as c:
        assert c is conn

    set_calls = [
        call for call in cursor.execute.call_args_list
        if "sp_set_session_context" in call.args[0]
    ]
    assert len(set_calls) == 2

    first_sql, first_oid = set_calls[0].args
    assert "@read_only = 1" in first_sql
    assert first_oid == str(_TEST_OID)

    second_sql = set_calls[1].args[0]
    assert "@value = NULL" in second_sql
    assert "@read_only = 0" in second_sql
    assert len(set_calls[1].args) == 1  # no parameter for the reset

    conn.close.assert_called_once()


def test_connection_for_user_resets_even_when_body_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, cursor = _make_mock_connection()
    _patched_open(monkeypatch, conn)
    principal = SessionContext(aad_object_id=_TEST_OID)

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        with connection_for_user(principal, auth_mode=AuthMode.AAD_DEFAULT):
            raise _Boom("intentional")

    set_calls = [
        call for call in cursor.execute.call_args_list
        if "sp_set_session_context" in call.args[0]
    ]
    assert len(set_calls) == 2
    assert "@value = NULL" in set_calls[1].args[0]
    conn.close.assert_called_once()


def test_connection_for_user_closes_connection_on_set_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, cursor = _make_mock_connection()
    cursor.execute.side_effect = pyodbc.Error("driver exploded")
    _patched_open(monkeypatch, conn)
    principal = SessionContext(aad_object_id=_TEST_OID)

    with pytest.raises(pyodbc.Error):
        with connection_for_user(principal, auth_mode=AuthMode.AAD_DEFAULT):
            pytest.fail("body should not run when SET fails")

    conn.close.assert_called_once()


def test_session_context_requires_uuid_type() -> None:
    with pytest.raises(ValidationError):
        SessionContext(aad_object_id="not-a-uuid")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# assert_session_context_set
# ---------------------------------------------------------------------------


def _conn_returning(value: Any) -> MagicMock:
    cursor = MagicMock()
    cursor.execute.return_value = cursor
    cursor.fetchone.return_value = (value,) if value is not None else (None,)
    conn = MagicMock(spec=pyodbc.Connection)
    conn.cursor.return_value = cursor
    return conn


def test_assert_session_context_set_returns_uuid_when_set() -> None:
    conn = _conn_returning(_TEST_OID)
    assert assert_session_context_set(conn) == _TEST_OID


def test_assert_session_context_set_parses_string_value() -> None:
    conn = _conn_returning(str(_TEST_OID))
    assert assert_session_context_set(conn) == _TEST_OID


def test_assert_session_context_set_raises_when_null() -> None:
    conn = _conn_returning(None)
    with pytest.raises(SessionContextUnsetError):
        assert_session_context_set(conn)


def test_assert_session_context_set_raises_when_no_row() -> None:
    cursor = MagicMock()
    cursor.execute.return_value = cursor
    cursor.fetchone.return_value = None
    conn = MagicMock(spec=pyodbc.Connection)
    conn.cursor.return_value = cursor

    with pytest.raises(SessionContextUnsetError):
        assert_session_context_set(conn)


# ---------------------------------------------------------------------------
# open_connection — error translation
# ---------------------------------------------------------------------------


def test_open_connection_translates_pyodbc_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from tcp.db import TcpConnectionError, open_connection

    def _raise(*_a: object, **_k: object) -> pyodbc.Connection:
        raise pyodbc.Error("network down")

    monkeypatch.setattr("tcp.db.pyodbc.connect", _raise)

    with pytest.raises(TcpConnectionError):
        open_connection(
            SqlConfig(server="s", database="d"),
            AuthMode.AAD_DEFAULT,
            bypass_session_context=True,
        )


def test_open_connection_uses_env_when_args_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    from tcp.db import open_connection

    captured: dict[str, str] = {}

    def _fake_connect(conn_str: str, **_k: object) -> MagicMock:
        captured["conn_str"] = conn_str
        return MagicMock(spec=pyodbc.Connection)

    monkeypatch.delenv("TCP_SQL_DEV_USER", raising=False)
    monkeypatch.delenv("TCP_SQL_DEV_PASSWORD", raising=False)
    monkeypatch.delenv("IDENTITY_ENDPOINT", raising=False)
    monkeypatch.setenv("TCP_SQL_SERVER", "envserver")
    monkeypatch.setenv("TCP_SQL_DATABASE", "envdb")
    monkeypatch.setattr("tcp.db.pyodbc.connect", _fake_connect)

    open_connection(bypass_session_context=True)

    assert "Server=envserver" in captured["conn_str"]
    assert "Database=envdb" in captured["conn_str"]
    assert "Authentication=ActiveDirectoryDefault" in captured["conn_str"]


def test_open_connection_refuses_without_bypass_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from tcp.db import open_connection

    # pyodbc.connect must never be reached; the guard should raise first.
    def _explode(*_a: object, **_k: object) -> pyodbc.Connection:
        pytest.fail("pyodbc.connect must not be called when bypass flag is False")

    monkeypatch.setattr("tcp.db.pyodbc.connect", _explode)

    with pytest.raises(AuthError, match="bypass_session_context"):
        open_connection(SqlConfig(server="s", database="d"), AuthMode.AAD_DEFAULT)


def test_connection_for_user_closes_when_cursor_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CR-04 regression: a pyodbc error during conn.cursor() must still close conn."""
    conn = MagicMock(name="connection", spec=pyodbc.Connection)
    conn.cursor.side_effect = pyodbc.OperationalError("driver OOM between connect and cursor")
    _patched_open(monkeypatch, conn)
    principal = SessionContext(aad_object_id=_TEST_OID)

    with pytest.raises(pyodbc.OperationalError):
        with connection_for_user(principal, auth_mode=AuthMode.AAD_DEFAULT):
            pytest.fail("body should not run when cursor creation fails")

    conn.close.assert_called_once()


def test_build_connection_string_pins_pooling_off_for_aad_modes() -> None:
    """MJ-03 security: AAD paths must disable ODBC driver pooling."""
    cfg = SqlConfig(server="s", database="d")
    aad_default = build_connection_string(cfg, AuthMode.AAD_DEFAULT)
    aad_mi = build_connection_string(cfg, AuthMode.AAD_MANAGED_IDENTITY)
    assert "Pooling=False" in aad_default
    assert "Pooling=False" in aad_mi


def test_build_connection_string_keeps_pooling_for_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQL_AUTH_DEV keeps pooling enabled for fast local iteration."""
    monkeypatch.setenv("TCP_SQL_DEV_USER", "sa")
    monkeypatch.setenv("TCP_SQL_DEV_PASSWORD", "p@ssw0rd!")
    cfg = SqlConfig(server="s", database="d")
    s = build_connection_string(cfg, AuthMode.SQL_AUTH_DEV)
    assert "Pooling=False" not in s


def test_redact_masks_long_form_password_keyword() -> None:
    """MN-11: extend redaction to the Password= long form."""
    raw = "Driver={...};Server=s;UID=sa;Password=secret;Encrypt=yes;"
    redacted = _redact(raw)
    assert "secret" not in redacted
    assert "Password=***" in redacted
