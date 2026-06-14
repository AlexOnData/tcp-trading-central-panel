"""Unit tests for tcp.synth.runner — pyodbc connection fully mocked."""

from __future__ import annotations

import json
from datetime import date, time
from typing import Any, Final, Iterator

import pyodbc
import pytest

from tcp.synth import runner
from tcp.synth.runner import previous_business_day, run_daily

_TEST_GENERATOR_OID: Final[str] = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _patch_generator_oid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin TCP_GENERATOR_OID so the runner can construct an admin session context."""
    monkeypatch.setenv("TCP_GENERATOR_OID", _TEST_GENERATOR_OID)
    # The runner calls set_admin_session_context(conn, oid) on its owned
    # connection; the FakeConn does not implement sp_set_session_context,
    # so we no-op the helper for unit-test purposes.
    monkeypatch.setattr(runner, "set_admin_session_context", lambda conn, oid: None)


# ---------------------------------------------------------------------------
# previous_business_day
# ---------------------------------------------------------------------------


def test_previous_business_day_monday_returns_friday() -> None:
    # 2026-05-18 is Monday -> 2026-05-15 (Fri).
    assert previous_business_day(date(2026, 5, 18)) == date(2026, 5, 15)


def test_previous_business_day_tuesday_returns_monday() -> None:
    # 2026-05-19 is Tuesday -> 2026-05-18 (Mon).
    assert previous_business_day(date(2026, 5, 19)) == date(2026, 5, 18)


def test_previous_business_day_friday_returns_thursday() -> None:
    # 2026-05-15 is Friday -> 2026-05-14 (Thu).
    assert previous_business_day(date(2026, 5, 15)) == date(2026, 5, 14)


def test_previous_business_day_sunday_returns_friday() -> None:
    # 2026-05-17 is Sunday -> 2026-05-15 (Fri).
    assert previous_business_day(date(2026, 5, 17)) == date(2026, 5, 15)


# ---------------------------------------------------------------------------
# run_daily — full happy path with mocked pyodbc
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Cursor double that replays a scripted sequence of fetch results."""

    def __init__(self, scripts: list[Any]) -> None:
        self._scripts: list[Any] = list(scripts)
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._pending: Any = None

    def execute(self, sql: str, *params: Any) -> "_FakeCursor":
        self.executed.append((sql, params))
        # Pop the next scripted result so fetchone/fetchall can return it.
        if self._scripts:
            self._pending = self._scripts.pop(0)
        else:
            self._pending = None
        return self

    def fetchone(self) -> Any:
        value = self._pending
        # fetchone consumes a single row from a list-shaped script.
        if isinstance(value, list):
            self._pending = value[1:]
            return value[0] if value else None
        self._pending = None
        return value

    def fetchall(self) -> Any:
        value = self._pending
        self._pending = None
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def close(self) -> None:
        return None


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def _baseline_scripts(target: date = date(2026, 5, 14)) -> list[Any]:
    """Build a scripted fetch sequence for a happy-path runner invocation."""
    return [
        # 1. previous_business_day lookup -> single date row
        (target,),
        # 2. SELECT active traders -> two rows
        [(1, 1), (2, 2)],
        # 3. SELECT markets
        [
            (1, "AAPL", "equity", "USD", True),
            (2, "EURUSD", "fx", "USD", True),
        ],
        # 4. SELECT sessions
        [
            (1, "pre_market", time(7, 0), time(9, 30)),
            (2, "regular", time(9, 30), time(17, 30)),
            (3, "after_hours", time(17, 30), time(22, 0)),
        ],
        # 5. SELECT order types
        [
            (1, "market"),
            (2, "limit"),
            (3, "stop"),
            (4, "stop_limit"),
        ],
        # 6. EXEC proc -> (rows_inserted, status); 'ok' is one of the
        # three canonical statuses returned by usp_GenerateDailyTrades.
        (10, "ok"),
    ]


@pytest.fixture()
def patch_open_connection(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    cursor = _FakeCursor(_baseline_scripts())
    conn = _FakeConn(cursor)
    monkeypatch.setattr(runner, "_open_raw_connection", lambda *a, **kw: conn)
    yield {"conn": conn, "cursor": cursor}


def test_run_daily_happy_path(patch_open_connection: dict[str, Any]) -> None:
    result = run_daily(today=date(2026, 5, 15))
    assert result["status"] == "ok"
    assert result["trade_date"] == "2026-05-14"
    assert result["rows_inserted"] == 10
    # Connection should have been committed and closed.
    assert patch_open_connection["conn"].commits == 1
    assert patch_open_connection["conn"].rollbacks == 0
    assert patch_open_connection["conn"].closed is True


def test_run_daily_invokes_proc_with_json_payload(
    patch_open_connection: dict[str, Any],
) -> None:
    run_daily(today=date(2026, 5, 15))
    executed = patch_open_connection["cursor"].executed
    # The last executed statement must be the proc call.
    last_sql, last_params = executed[-1]
    assert "usp_GenerateDailyTrades" in last_sql
    assert last_params[0] == date(2026, 5, 14)
    payload = json.loads(last_params[1])
    assert isinstance(payload, list)
    if payload:
        sample = payload[0]
        assert "trade_uid" in sample
        assert "trader_id" in sample
        assert sample["trade_uid"].startswith("T20260514-")


def test_run_daily_dry_run_skips_proc(monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = _baseline_scripts()
    # Drop the final proc-result tuple — it shouldn't be needed.
    scripts.pop()
    cursor = _FakeCursor(scripts)
    conn = _FakeConn(cursor)
    monkeypatch.setattr(runner, "_open_raw_connection", lambda *a, **kw: conn)
    result = run_daily(today=date(2026, 5, 15), dry_run=True)
    assert result["status"] == "ok"
    assert result["trade_date"] == "2026-05-14"
    # No commits, no rollbacks — dry run never hits the proc.
    assert conn.commits == 0
    assert conn.rollbacks == 0
    # No SQL statement should mention the proc.
    assert all("usp_GenerateDailyTrades" not in sql for sql, _ in cursor.executed)


def test_run_daily_skipped_when_no_previous_business_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first fetch returns None -> runner short-circuits with skipped_holiday.
    cursor = _FakeCursor([None])
    conn = _FakeConn(cursor)
    monkeypatch.setattr(runner, "_open_raw_connection", lambda *a, **kw: conn)
    result = run_daily(today=date(2026, 5, 18))
    assert result["status"] == "skipped_holiday"
    assert result["rows_inserted"] == 0
    assert conn.commits == 0


def test_run_daily_rolls_back_on_proc_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _FakeCursor(_baseline_scripts())
    # Override execute() to raise on the proc invocation.
    real_execute = cursor.execute

    def raising_execute(sql: str, *params: Any) -> _FakeCursor:
        if "usp_GenerateDailyTrades" in sql:
            raise pyodbc.Error("simulated SQL failure")
        return real_execute(sql, *params)

    cursor.execute = raising_execute  # type: ignore[method-assign]
    conn = _FakeConn(cursor)
    monkeypatch.setattr(runner, "_open_raw_connection", lambda *a, **kw: conn)

    with pytest.raises(pyodbc.Error):
        run_daily(today=date(2026, 5, 15))
    assert conn.rollbacks == 1
    assert conn.commits == 0
    assert conn.closed is True


def test_run_daily_reports_already_generated_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripts = _baseline_scripts()
    # Proc returns the *existing* row count (192) when status is
    # 'already_generated'; the runner must remap rows_inserted -> 0 and
    # surface the existing count under ``existing_row_count``.
    scripts[-1] = (192, "already_generated")
    cursor = _FakeCursor(scripts)
    conn = _FakeConn(cursor)
    monkeypatch.setattr(runner, "_open_raw_connection", lambda *a, **kw: conn)
    result = run_daily(today=date(2026, 5, 15))
    assert result["status"] == "already_generated"
    assert result["rows_inserted"] == 0
    assert result["existing_row_count"] == 192


def test_run_daily_propagates_skipped_non_trading_day_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CR-02 (data-engineer review): the proc's 'skipped_non_trading_day'
    status must pass through to the runner's response, not be coerced to 'ok'."""
    scripts = _baseline_scripts()
    scripts[-1] = (0, "skipped_non_trading_day")
    cursor = _FakeCursor(scripts)
    conn = _FakeConn(cursor)
    monkeypatch.setattr(runner, "_open_raw_connection", lambda *a, **kw: conn)
    result = run_daily(today=date(2026, 5, 15))
    assert result["status"] == "skipped_non_trading_day"
    assert result["rows_inserted"] == 0


def test_run_daily_raises_when_generator_oid_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing TCP_GENERATOR_OID must raise a clear RuntimeError (CR-01)."""
    monkeypatch.delenv("TCP_GENERATOR_OID", raising=False)
    with pytest.raises(RuntimeError, match="TCP_GENERATOR_OID"):
        run_daily(today=date(2026, 5, 15))


# ---------------------------------------------------------------------------
# previous_business_day helper smoke
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 5, 11), date(2026, 5, 8)),  # Mon -> Fri
        (date(2026, 5, 12), date(2026, 5, 11)),  # Tue -> Mon
        (date(2026, 5, 13), date(2026, 5, 12)),  # Wed -> Tue
        (date(2026, 5, 14), date(2026, 5, 13)),  # Thu -> Wed
        (date(2026, 5, 15), date(2026, 5, 14)),  # Fri -> Thu
        (date(2026, 5, 16), date(2026, 5, 15)),  # Sat -> Fri
        (date(2026, 5, 17), date(2026, 5, 15)),  # Sun -> Fri
    ],
)
def test_previous_business_day_table(today: date, expected: date) -> None:
    assert previous_business_day(today) == expected


def test_invalid_dialect_does_not_break_cursor_close(monkeypatch: pytest.MonkeyPatch) -> None:
    # If cursor.close itself raises, the runner must still close the connection.
    cursor = _FakeCursor(_baseline_scripts())
    original_close = cursor.close

    def boom() -> None:
        original_close()
        raise pyodbc.Error("simulated cursor close failure")

    cursor.close = boom  # type: ignore[method-assign]
    conn = _FakeConn(cursor)
    monkeypatch.setattr(runner, "_open_raw_connection", lambda *a, **kw: conn)
    result = run_daily(today=date(2026, 5, 15))
    assert result["status"] == "ok"
    assert conn.closed is True
