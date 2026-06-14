"""Integration tests for the tcp.synth daily generator pipeline.

All tests run against a live SQL Server and are automatically skipped unless
``TCP_SQL_SERVER`` is set in the environment.  Every test that mutates the
database wraps its work inside a transaction that is rolled back on teardown,
so the schema is left identical to its pre-test state.

The tests verify:
- The full seed → run_daily → idempotency path (run twice, get same rows).
- The holiday-skip path (status == "skipped_non_trading_day").
- Correct previous-business-day resolution when today is a Monday.
- Proc-level validation: malformed JSON payload is rejected.
- Proc-level validation: cross-date time_entry is rejected.

ADR-003 contract:
  Integration tests connect with admin scope (``bypass_session_context=True``
  via ``open_connection``); the ``seeded_employees`` fixture also inserts a
  synthetic admin row into ``dim_UserRoles`` before running the generator so
  the RLS block predicate does not reject the INSERT.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, Final, Generator

import pyodbc
import pytest

from tcp.db import open_connection
from tcp.synth import run_daily, seed_employees

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

_ENV_KEY = "TCP_SQL_SERVER"


def _skip_if_no_live_db() -> None:
    """Skip the calling test if TCP_SQL_SERVER is not present in the environment."""
    if not os.environ.get(_ENV_KEY):
        pytest.skip(f"{_ENV_KEY} not set; live-DB integration tests skipped")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> Generator[pyodbc.Connection, None, None]:
    """Yield an admin pyodbc connection wrapped in a transaction that rolls back.

    Uses ``open_connection(bypass_session_context=True)`` per the documented
    escape hatch for infrastructure tasks (ADR-003 §4).  The connection is
    returned to the caller in ``autocommit=False`` mode (the pyodbc default);
    the fixture executes ``BEGIN TRANSACTION`` explicitly so the ROLLBACK in
    teardown is guaranteed to undo everything the test body does, including
    any nested SAVE POINTs the stored proc may create.
    """
    _skip_if_no_live_db()
    conn = open_connection(bypass_session_context=True)
    try:
        cursor = conn.cursor()
        cursor.execute("BEGIN TRANSACTION")
        cursor.close()
        yield conn
    finally:
        try:
            conn.rollback()
        except pyodbc.Error:
            pass
        conn.close()


_TEST_ADMIN_OID: Final[str] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def seeded_employees(db_conn: pyodbc.Connection) -> dict[str, int]:
    """Seed the organisational chart and return the seed_employees result dict.

    Registers a synthetic admin row into ``dim_UserRoles`` first AND sets
    ``SESSION_CONTEXT('aad_object_id')`` to the same OID before calling
    ``seed_employees(conn=db_conn)``. Without the SESSION_CONTEXT, the V001
    RLS predicate cannot resolve the principal and any subsequent INSERT
    into fact_Trades is rejected — see data-engineer review CR-01.

    ``seed_employees`` populates dim_Employees and dim_Accounts (30 live-EUR
    accounts, one per trading-eligible employee = 24 traders + 6 team leads)
    using the same ``db_conn`` connection that wraps the roll-back transaction.
    """
    cursor = db_conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO dbo.dim_UserRoles "
            "(aad_object_id, employee_id, scope, is_active) "
            "VALUES (?, NULL, 'admin', 1)",
            # A well-known test OID — stable across runs, rolled back at end.
            _TEST_ADMIN_OID,
        )
        cursor.execute(
            "EXEC sys.sp_set_session_context "
            "@key = N'aad_object_id', @value = ?, @read_only = 1",
            _TEST_ADMIN_OID,
        )
    finally:
        cursor.close()

    result: dict[str, int] = seed_employees(conn=db_conn)
    return result


# ---------------------------------------------------------------------------
# Test: seed_employees then run_daily twice (idempotency)
# ---------------------------------------------------------------------------


def test_seed_employees_then_run_daily_twice(
    db_conn: pyodbc.Connection,
    seeded_employees: dict[str, int],
) -> None:
    """Seeding produces 32 employees and 24 accounts; run_daily is idempotent.

    Steps:
    1. Verify seed_employees returned the correct counts.
    2. Run the daily generator for date 2026-05-14 (Thursday) by passing
       today=2026-05-15 (Friday).  Assert status="ok" and rows_inserted > 100.
    3. Run the generator again for the same date.  Assert
       status="already_generated" and rows_inserted equals the first count.
    4. Verify fact_DailyTraderPnL has exactly one row per active trader for
       trade_date_ro = 2026-05-14 (i.e. 30 rows).
    """
    # Step 1: verify seed counts.
    assert seeded_employees["employees"] == 32, (
        f"Expected 32 employees, got {seeded_employees['employees']}"
    )
    # 30 = 24 traders + 6 team leads (team leads also trade per KPI-LR-001).
    assert seeded_employees["accounts"] == 30, (
        f"Expected 30 accounts, got {seeded_employees['accounts']}"
    )

    # Step 2: first run.
    today_friday = date(2026, 5, 15)
    first_result = run_daily(today=today_friday, conn=db_conn)
    assert first_result["status"] == "ok", (
        f"Expected status='ok' on first run, got {first_result['status']!r}"
    )
    rows_first = first_result["rows_inserted"]
    assert rows_first > 100, (
        f"Expected > 100 rows on first run, got {rows_first}"
    )

    # Step 3: second run — same date, must be idempotent.
    second_result = run_daily(today=today_friday, conn=db_conn)
    assert second_result["status"] == "already_generated", (
        f"Expected status='already_generated' on second run, "
        f"got {second_result['status']!r}"
    )
    rows_second = second_result["rows_inserted"]
    assert rows_second == rows_first, (
        f"Idempotency breach: second run inserted {rows_second} rows "
        f"but first inserted {rows_first}"
    )

    # Step 4: verify fact_DailyTraderPnL materialisation.
    target_date = date(2026, 5, 14)
    cursor = db_conn.cursor()
    try:
        row = cursor.execute(
            "SELECT COUNT(*) FROM dbo.fact_DailyTraderPnL "
            "WHERE trade_date_ro = ?",
            target_date,
        ).fetchone()
        assert row is not None
        pnl_row_count = row[0]
    finally:
        cursor.close()

    # 30 trading employees (24 traders + 6 team leads) should each have 1 row.
    assert pnl_row_count == 30, (
        f"Expected 30 fact_DailyTraderPnL rows for {target_date}, "
        f"got {pnl_row_count}"
    )


# ---------------------------------------------------------------------------
# Test: holiday skip path
# ---------------------------------------------------------------------------


def test_run_daily_skips_holiday(
    db_conn: pyodbc.Connection,
    seeded_employees: dict[str, int],
) -> None:
    """Generator returns status='skipped_non_trading_day' for a RO public holiday.

    2026-01-02 (Friday) has previous_business_day == 2026-01-01 (Anul Nou),
    which must be marked is_ro_holiday=1 in dim_Date.  The proc detects this
    and returns 'skipped_non_trading_day' without inserting any rows.
    """
    today_jan2 = date(2026, 1, 2)
    result = run_daily(today=today_jan2, conn=db_conn)
    assert result["status"] == "skipped_non_trading_day", (
        f"Expected status='skipped_non_trading_day' for RO holiday path, "
        f"got {result['status']!r}"
    )


# ---------------------------------------------------------------------------
# Test: Monday → previous business day is Friday (not Saturday/Sunday)
# ---------------------------------------------------------------------------


def test_run_daily_weekend_handling(
    db_conn: pyodbc.Connection,
    seeded_employees: dict[str, int],
) -> None:
    """Generator resolves previous business day to Friday when today is Monday.

    today=2026-05-18 (Monday) → previous_business_day = 2026-05-15 (Friday).
    After run_daily, fact_Trades must contain rows dated 2026-05-15 only —
    no Saturday (2026-05-16) or Sunday (2026-05-17) rows must appear.
    """
    today_monday = date(2026, 5, 18)
    result = run_daily(today=today_monday, conn=db_conn)

    # Status must be ok (or already_generated if tests share a connection scope).
    assert result["status"] in {"ok", "already_generated"}, (
        f"Unexpected status {result['status']!r} for Monday run"
    )

    expected_trade_date = date(2026, 5, 15)
    cursor = db_conn.cursor()
    try:
        # Check for any rows on Saturday or Sunday — there must be none.
        bad_row = cursor.execute(
            "SELECT TOP 1 "
            "CAST(time_entry AT TIME ZONE 'E. Europe Standard Time' AS DATE) "
            "AS trade_date_ro "
            "FROM dbo.fact_Trades "
            "WHERE CAST(time_entry AT TIME ZONE 'E. Europe Standard Time' AS DATE) "
            "IN (?, ?)",
            date(2026, 5, 16),
            date(2026, 5, 17),
        ).fetchone()
        assert bad_row is None, (
            f"Found trades on weekend date {bad_row[0]}; "
            "generator must resolve Monday → previous Friday"
        )

        # Confirm at least some rows landed on Friday.
        friday_count_row = cursor.execute(
            "SELECT COUNT(*) FROM dbo.fact_Trades "
            "WHERE CAST(time_entry AT TIME ZONE 'E. Europe Standard Time' AS DATE) = ?",
            expected_trade_date,
        ).fetchone()
        assert friday_count_row is not None
        friday_count = friday_count_row[0]
        assert friday_count > 0, (
            f"No trades found for expected Friday date {expected_trade_date}"
        )
    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# Test: malformed JSON payload is rejected by the stored procedure
# ---------------------------------------------------------------------------


def test_run_daily_malformed_json_rejected(db_conn: pyodbc.Connection) -> None:
    """Direct proc invocation with a garbage JSON payload raises a SQL error.

    The stored procedure ``usp_GenerateDailyTrades`` validates the @trades
    parameter via ISJSON() and calls RAISERROR with a message that includes
    "not valid JSON" when the check fails.
    """
    cursor = db_conn.cursor()
    try:
        with pytest.raises(pyodbc.DatabaseError) as exc_info:
            cursor.execute(
                "EXEC dbo.usp_GenerateDailyTrades "
                "@trade_date = ?, @trades = N'not json at all'",
                date(2026, 5, 14),
            )
            cursor.fetchall()  # Force execution if the driver is lazy.
        error_message = str(exc_info.value).lower()
        assert "not valid json" in error_message, (
            f"Expected 'not valid JSON' in error message, got: {exc_info.value!r}"
        )
    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# Test: cross-date time_entry is rejected
# ---------------------------------------------------------------------------


def test_run_daily_cross_date_rejected(
    db_conn: pyodbc.Connection,
    seeded_employees: dict[str, int],
) -> None:
    """A payload row with time_entry on a different date triggers RAISERROR.

    The proc checks that every time_entry, when cast through the
    'E. Europe Standard Time' time zone, falls on @trade_date.  A row dated
    the day before must cause the proc to raise with a message containing
    "time_entry whose Europe/Bucharest date".
    """
    # Retrieve a valid trader_id, market_id, session_id and order_type_id
    # so the cross-date row itself is otherwise well-formed.
    cursor = db_conn.cursor()
    try:
        emp_row = cursor.execute(
            "SELECT TOP 1 employee_id FROM dbo.dim_Employees WHERE is_active = 1"
        ).fetchone()
        assert emp_row is not None, "No active employees found; run seeded_employees first"
        trader_id: int = emp_row[0]

        acc_row = cursor.execute(
            "SELECT TOP 1 account_id FROM dbo.dim_Accounts WHERE trader_id = ?",
            trader_id,
        ).fetchone()
        assert acc_row is not None, f"No account found for trader_id={trader_id}"
        account_id: int = acc_row[0]

        mkt_row = cursor.execute("SELECT TOP 1 market_id FROM dbo.dim_Markets").fetchone()
        assert mkt_row is not None, "No markets found"
        market_id: int = mkt_row[0]

        sess_row = cursor.execute("SELECT TOP 1 session_id FROM dbo.dim_Sessions").fetchone()
        assert sess_row is not None, "No sessions found"
        session_id: int = sess_row[0]

        ot_row = cursor.execute(
            "SELECT TOP 1 order_type_id FROM dbo.dim_OrderType"
        ).fetchone()
        assert ot_row is not None, "No order types found"
        order_type_id: int = ot_row[0]
    finally:
        cursor.close()

    trade_date = date(2026, 5, 14)
    # time_entry is on 2026-05-13 (the day before trade_date) — this must fail.
    wrong_time_entry = "2026-05-13T10:00:00.000+03:00"

    payload: list[dict[str, Any]] = [
        {
            "trade_uid": "T20260514-0001",
            "trader_id": trader_id,
            "account_id": account_id,
            "market_id": market_id,
            "session_id": session_id,
            "order_type_id": order_type_id,
            "side": "B",
            "quantity": "10.0000",
            "price_entry": "150.0000",
            "price_exit": None,
            "time_entry": wrong_time_entry,
            "time_exit": None,
            "is_open": 1,
            "gross_pnl_eur": "0.0000",
            "commission_eur": "0.0500",
            "net_pnl_eur": "-0.0500",
            "fx_rate_to_eur": "0.9100",
        }
    ]

    cursor2 = db_conn.cursor()
    try:
        with pytest.raises(pyodbc.DatabaseError) as exc_info:
            cursor2.execute(
                "EXEC dbo.usp_GenerateDailyTrades @trade_date = ?, @trades = ?",
                trade_date,
                json.dumps(payload),
            )
            cursor2.fetchall()
        error_message = str(exc_info.value).lower()
        assert "time_entry whose europe/bucharest date" in error_message, (
            f"Expected cross-date error message; got: {exc_info.value!r}"
        )
    finally:
        cursor2.close()
