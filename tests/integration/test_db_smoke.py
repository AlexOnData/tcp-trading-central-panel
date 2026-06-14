"""End-to-end smoke tests against a live SQL Server.

Skipped automatically unless TCP_SQL_SERVER is set. Wraps mutating work in
a transaction that is rolled back at the end, so the database is left
identical to its pre-test state.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pyodbc
import pytest

from tcp.db import (
    AuthMode,
    SessionContext,
    SqlConfig,
    assert_session_context_set,
    connection_for_user,
    open_connection,
)

pytestmark = pytest.mark.integration


def _skip_if_no_live_db() -> None:
    if not os.environ.get("TCP_SQL_SERVER"):
        pytest.skip("TCP_SQL_SERVER not set; live-DB integration tests skipped")


def test_open_connection_runs_select_version() -> None:
    _skip_if_no_live_db()
    # Infrastructure smoke test: bypass_session_context=True is the documented
    # escape hatch (ADR-003 §4). All user-driven paths must use connection_for_user.
    conn = open_connection(bypass_session_context=True)
    try:
        row = conn.cursor().execute("SELECT @@VERSION").fetchone()
        assert row is not None
        assert isinstance(row[0], str) and row[0]
    finally:
        conn.close()


def test_connection_for_user_round_trip() -> None:
    _skip_if_no_live_db()
    principal_oid = uuid4()
    principal = SessionContext(aad_object_id=principal_oid)

    with connection_for_user(principal) as conn:
        bound = assert_session_context_set(conn)
        assert bound == principal_oid

        cursor = conn.cursor()
        # Seed dim_UserRoles with an admin scope so the RLS predicate evaluates
        # without DML restrictions during the read below. The connection is
        # already autocommit=False (set in _open_raw_connection), so a single
        # conn.rollback() at the end unwinds every DML in this block.
        try:
            # All variable values must be parameter-bound; only the constant
            # scope 'admin' is inlined as a SQL literal here.
            cursor.execute(
                "INSERT INTO dbo.dim_UserRoles "
                "(aad_object_id, employee_id, scope, is_active) "
                "VALUES (?, NULL, 'admin', 1)",
                str(principal_oid),
            )
            row = cursor.execute("SELECT COUNT(*) FROM dbo.fact_Trades").fetchone()
            assert row is not None
            assert isinstance(row[0], int)
        finally:
            try:
                conn.rollback()
            except pyodbc.Error:
                # The connection may already have aborted on a prior error;
                # the caller's connection_for_user finalisation still runs.
                pass

    _assert_context_cleared_on_fresh_connection()


def _assert_context_cleared_on_fresh_connection() -> None:
    """Open a second connection and confirm the previous OID does not leak.

    The reset on check-in only matters if pyodbc happened to reuse the same
    underlying connection. With a fresh ``pyodbc.connect`` the SESSION_CONTEXT
    must start NULL anyway; the test acts as a regression guard against any
    future pooling layer that might recycle without reset.
    """
    conn = open_connection(bypass_session_context=True)
    try:
        row = conn.cursor().execute(
            "SELECT CAST(SESSION_CONTEXT(N'aad_object_id') AS UNIQUEIDENTIFIER)"
        ).fetchone()
        assert row is not None
        assert row[0] is None
    finally:
        conn.close()


def test_auth_mode_resolution_matches_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _skip_if_no_live_db()
    # Without dev creds and without the Functions identity endpoint we expect
    # the local-developer ``DefaultAzureCredential`` path.
    monkeypatch.delenv("TCP_SQL_DEV_USER", raising=False)
    monkeypatch.delenv("TCP_SQL_DEV_PASSWORD", raising=False)
    monkeypatch.delenv("IDENTITY_ENDPOINT", raising=False)
    assert AuthMode.from_env() in {AuthMode.AAD_DEFAULT, AuthMode.AAD_MANAGED_IDENTITY}


def test_sql_config_picks_up_server_from_env() -> None:
    _skip_if_no_live_db()
    cfg = SqlConfig.from_env()
    assert cfg.server == os.environ["TCP_SQL_SERVER"]
    # An UUID is constructible from a string repr; we only verify the typed
    # principal model still works end-to-end here.
    _ = SessionContext(aad_object_id=UUID(int=0))
