"""Orchestrator that wires the daily generator to ``usp_GenerateDailyTrades``.

Invoked by the Function App's Timer Trigger at 07:00 Europe/Bucharest on
weekdays. The runner:

1. resolves the target trade date via :func:`previous_business_day`
   (Monday targets the previous Friday; Tue-Fri target the prior weekday;
   Romanian public holidays are skipped by the SQL function);
2. opens an admin-scoped connection AND sets ``SESSION_CONTEXT('aad_object_id')``
   to the generator Managed Identity's AAD object id (read from the
   ``TCP_GENERATOR_OID`` environment variable, populated by Etapa-4 IaC).
   The V001 RLS BLOCK PREDICATE on ``fact_Trades`` joins ``dim_UserRoles``
   on ``aad_object_id = SESSION_CONTEXT(N'aad_object_id')``; without the
   context the daily INSERT is rejected (data-engineer review CR-01);
3. fetches the dim_* rows the generator needs;
4. calls :func:`tcp.synth.trades.generate_for_date`;
5. JSON-encodes the rows and passes them to ``dbo.usp_GenerateDailyTrades``
   inside a single transaction with explicit commit/rollback.

A successful call returns a dict with ``rows_inserted``, ``duration_ms``,
``trade_date``, ``status`` (``'ok' | 'already_generated' |
'skipped_non_trading_day' | 'skipped_holiday'``) and, when status is
``'already_generated'``, ``existing_row_count``.
"""

from __future__ import annotations

import json
import os
import time as time_mod
from datetime import date, datetime, time
from typing import Any, Final
from uuid import UUID
from zoneinfo import ZoneInfo

import pyodbc
import structlog

from tcp.db import _open_raw_connection, set_admin_session_context
from tcp.synth.trades import (
    MarketRow,
    OrderTypeRow,
    SessionRow,
    TraderProfile,
    generate_for_date,
)

_GENERATOR_OID_ENV: Final[str] = "TCP_GENERATOR_OID"
_VALID_PROC_STATUSES: Final[frozenset[str]] = frozenset(
    {"ok", "already_generated", "skipped_non_trading_day"}
)

_TZ_BUCHAREST: Final[ZoneInfo] = ZoneInfo("Europe/Bucharest")

_SQL_SELECT_ACTIVE_TRADERS: Final[str] = """
SELECT e.employee_id, MIN(a.account_id) AS account_id
FROM dbo.dim_Employees AS e
JOIN dbo.dim_Accounts AS a ON a.trader_id = e.employee_id AND a.is_active = 1
WHERE e.employee_role IN ('trader', 'team_lead')
  AND e.is_active = 1
GROUP BY e.employee_id
ORDER BY e.employee_id;
"""

_SQL_SELECT_MARKETS: Final[str] = """
SELECT market_id, symbol, asset_class, quote_currency, is_active
FROM dbo.dim_Markets
WHERE is_active = 1
ORDER BY market_id;
"""

_SQL_SELECT_SESSIONS: Final[str] = """
SELECT session_id, session_code, start_time_local, end_time_local
FROM dbo.dim_Sessions
ORDER BY session_id;
"""

_SQL_SELECT_ORDER_TYPES: Final[str] = """
SELECT order_type_id, order_type_code
FROM dbo.dim_OrderType
ORDER BY order_type_id;
"""

_SQL_PREVIOUS_BUSINESS_DAY: Final[str] = """
SELECT TOP 1 calendar_date
FROM dbo.dim_Date
WHERE calendar_date < ?
  AND is_weekday = 1
  AND is_ro_holiday = 0
ORDER BY calendar_date DESC;
"""

_SQL_EXEC_PROC: Final[str] = (
    "EXEC dbo.usp_GenerateDailyTrades @trade_date = ?, @trades = ?"
)


_log = structlog.get_logger(__name__)


def previous_business_day(today: date) -> date:
    """Return the most recent business day strictly before ``today``.

    Defined in Python so unit tests can exercise the runner without
    requiring a live ``dim_Date`` table. The production path (called from
    inside :func:`run_daily`) uses the SQL-side ``dim_Date`` lookup, which
    additionally respects Romanian public holidays.

    Behaviour: Saturday/Sunday return the prior Friday; Monday returns
    Friday; Tuesday-Friday return the prior weekday. Holidays are NOT
    considered here — use the SQL ``dim_Date`` query for that.

    Args:
        today: The reference date (typically "today" in Europe/Bucharest).

    Returns:
        The previous business day (Mon-Fri).
    """
    candidate = today
    # Step at least one day back, then keep stepping while we land on Sat/Sun.
    while True:
        candidate = date.fromordinal(candidate.toordinal() - 1)
        # weekday(): Monday=0 ... Sunday=6. Mon-Fri are 0..4.
        if candidate.weekday() < 5:
            return candidate


def _fetch_traders(cursor: pyodbc.Cursor) -> list[TraderProfile]:
    """Return the active trading-eligible employees with their primary account."""
    cursor.execute(_SQL_SELECT_ACTIVE_TRADERS)
    return [
        TraderProfile(trader_id=int(row[0]), account_id=int(row[1]))
        for row in cursor.fetchall()
    ]


def _fetch_markets(cursor: pyodbc.Cursor) -> list[MarketRow]:
    """Return the active rows from ``dim_Markets``."""
    cursor.execute(_SQL_SELECT_MARKETS)
    return [
        MarketRow(
            market_id=int(row[0]),
            symbol=str(row[1]),
            asset_class=str(row[2]),  # type: ignore[arg-type]
            quote_currency=str(row[3]),
            is_active=bool(row[4]),
        )
        for row in cursor.fetchall()
    ]


def _coerce_time(value: object) -> time:
    """Coerce a pyodbc TIME / datetime.time / string into ``datetime.time``."""
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    # Fallback: parse strings like '09:30:00'.
    text = str(value)
    parts = text.split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    second = int(parts[2].split(".")[0]) if len(parts) > 2 else 0
    return time(hour=hour, minute=minute, second=second)


def _fetch_sessions(cursor: pyodbc.Cursor) -> list[SessionRow]:
    """Return the rows from ``dim_Sessions``."""
    cursor.execute(_SQL_SELECT_SESSIONS)
    return [
        SessionRow(
            session_id=int(row[0]),
            session_code=str(row[1]),  # type: ignore[arg-type]
            start_time_local=_coerce_time(row[2]),
            end_time_local=_coerce_time(row[3]),
        )
        for row in cursor.fetchall()
    ]


def _fetch_order_types(cursor: pyodbc.Cursor) -> list[OrderTypeRow]:
    """Return the rows from ``dim_OrderType``."""
    cursor.execute(_SQL_SELECT_ORDER_TYPES)
    return [
        OrderTypeRow(
            order_type_id=int(row[0]),
            order_type_code=str(row[1]),  # type: ignore[arg-type]
        )
        for row in cursor.fetchall()
    ]


def _resolve_target_date(cursor: pyodbc.Cursor, today: date) -> date | None:
    """Return the previous business day via ``dim_Date`` (NULL = no row found)."""
    cursor.execute(_SQL_PREVIOUS_BUSINESS_DAY, today)
    row = cursor.fetchone()
    if row is None or row[0] is None:
        return None
    value = row[0]
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value))


def _resolve_generator_oid() -> UUID:
    """Return the generator MI's AAD object id, sourced from env.

    Raises:
        RuntimeError: When ``TCP_GENERATOR_OID`` is unset or malformed.
            Surfaced as a clear early error so the Etapa-4 IaC step can
            be diagnosed without trawling RLS-policy rejections.
    """
    raw = os.environ.get(_GENERATOR_OID_ENV)
    if not raw:
        msg = (
            f"{_GENERATOR_OID_ENV} env var is required for the daily generator "
            "path; set it to the generator MI's AAD object id."
        )
        raise RuntimeError(msg)
    try:
        return UUID(raw)
    except (ValueError, TypeError) as exc:
        msg = f"{_GENERATOR_OID_ENV}={raw!r} is not a valid UUID: {exc}"
        raise RuntimeError(msg) from exc


def run_daily(
    *,
    today: date | None = None,
    dry_run: bool = False,
    conn: pyodbc.Connection | None = None,
) -> dict[str, Any]:
    """Generate one day of synthetic trades and persist them via the SQL proc.

    Args:
        today: Override "today" in Europe/Bucharest. Defaults to the
            current local date. The runner derives the target trade date
            via the SQL-side ``dim_Date`` previous-business-day lookup so
            Romanian public holidays are skipped.
        dry_run: If ``True``, the rows are generated and serialised but
            the stored procedure is NOT invoked; the function still
            returns the row count it would have inserted.
        conn: Optional pre-opened admin connection. When provided, the
            caller owns the transaction (no commit/close here) AND the
            caller is responsible for having set
            ``SESSION_CONTEXT('aad_object_id')`` to an admin principal
            (the test fixture handles this). When ``None`` the runner
            opens its own connection via ``_open_raw_connection``, sets
            the SESSION_CONTEXT from the ``TCP_GENERATOR_OID`` env var,
            and manages commit/rollback/close.

    Returns:
        A dict with keys ``trade_date`` (ISO date string), ``rows_inserted``
        (int), ``duration_ms`` (int), and ``status`` (one of ``'ok'``,
        ``'already_generated'``, ``'skipped_non_trading_day'``,
        ``'skipped_holiday'`` or, for an unrecognised proc response,
        ``'unknown'``). When ``status == 'already_generated'``,
        ``rows_inserted`` is ``0`` and the response also carries
        ``existing_row_count`` (int) — the count of rows that pre-existed
        the call.

    Raises:
        RuntimeError: When ``conn is None`` and ``TCP_GENERATOR_OID`` is
            unset or malformed — the runner cannot prove its principal to
            the V001 RLS predicate otherwise.
    """
    start = time_mod.perf_counter()
    today_resolved = today or datetime.now(_TZ_BUCHAREST).date()
    log = _log.bind(today=today_resolved.isoformat())
    log.info("tcp.synth.start")

    owned_conn = conn is None
    if owned_conn:
        # Resolve the generator OID before opening the connection so a
        # mis-configuration fails fast (no half-opened resources).
        generator_oid = _resolve_generator_oid()
        conn = _open_raw_connection()
    else:
        generator_oid = None
        # When a connection is injected the caller is expected to have set
        # SESSION_CONTEXT themselves (see the test fixture in
        # tests/integration/test_generator_idempotency.py).
    # Etapa-11 typing narrow: by this point either `owned_conn` opened a
    # fresh connection or the caller supplied one. `conn is None` would mean
    # `_open_raw_connection()` returned None (it never does — it raises on
    # failure) or the caller passed an explicit None alongside its own
    # opened state. Both are programmer errors. The assert pins the runtime
    # contract AND lets mypy narrow `pyodbc.Connection | None` → `Connection`.
    assert conn is not None, "run_daily: connection unavailable after setup phase"
    try:
        if owned_conn and generator_oid is not None:
            set_admin_session_context(conn, generator_oid)
        cursor = conn.cursor()
        try:
            target = _resolve_target_date(cursor, today_resolved)
            if target is None:
                duration_ms = int((time_mod.perf_counter() - start) * 1000)
                log.warning("tcp.synth.skipped_holiday")
                return {
                    "trade_date": None,
                    "rows_inserted": 0,
                    "duration_ms": duration_ms,
                    "status": "skipped_holiday",
                }

            traders = _fetch_traders(cursor)
            markets = _fetch_markets(cursor)
            sessions = _fetch_sessions(cursor)
            order_types = _fetch_order_types(cursor)

            rows = generate_for_date(
                target,
                traders=traders,
                markets=markets,
                sessions=sessions,
                order_types=order_types,
            )
            payload_json = json.dumps([r.to_json_dict() for r in rows], default=str)

            if dry_run:
                duration_ms = int((time_mod.perf_counter() - start) * 1000)
                log.info(
                    "tcp.synth.dry_run",
                    trade_date=target.isoformat(),
                    rows_generated=len(rows),
                    duration_ms=duration_ms,
                )
                return {
                    "trade_date": target.isoformat(),
                    "rows_inserted": len(rows),
                    "duration_ms": duration_ms,
                    "status": "ok",
                }

            try:
                # Force pyodbc to send the JSON payload as NVARCHAR(MAX) (SQL_WVARCHAR
                # with size 0), not the legacy SQL_LONGVARCHAR which maps to `text`/
                # `ntext` and is rejected by the UTF-8 collation
                # (Latin1_General_100_CI_AS_SC_UTF8) with SQL error 4189.
                cursor.setinputsizes([None, (pyodbc.SQL_WVARCHAR, 0, 0)])
                cursor.execute(_SQL_EXEC_PROC, target, payload_json)
                result_row = cursor.fetchone()
                if owned_conn:
                    conn.commit()
            except Exception:
                if owned_conn:
                    try:
                        conn.rollback()
                    except pyodbc.Error as exc:
                        log.warning("tcp.synth.rollback_failed", error=str(exc))
                raise

            raw_row_count = 0
            status = "ok"
            if result_row is not None:
                raw_row_count = int(result_row[0]) if result_row[0] is not None else 0
                status_raw = result_row[1] if len(result_row) > 1 else None
                if status_raw is not None:
                    text = str(status_raw)
                    # CR-02 (data-engineer review): preserve all three proc
                    # statuses ('ok', 'already_generated',
                    # 'skipped_non_trading_day') and only coerce truly
                    # unexpected values to 'unknown'.
                    status = text if text in _VALID_PROC_STATUSES else "unknown"

            # MA-03 (python-pro review): when the day was already generated,
            # the proc returns the *existing* row count, not the count of
            # rows inserted by this call. Disambiguate to avoid double-
            # counting in App Insights metrics on replay.
            duration_ms = int((time_mod.perf_counter() - start) * 1000)
            response: dict[str, Any] = {
                "trade_date": target.isoformat(),
                "duration_ms": duration_ms,
                "status": status,
            }
            if status == "already_generated":
                response["rows_inserted"] = 0
                response["existing_row_count"] = raw_row_count
            else:
                response["rows_inserted"] = raw_row_count
            log.info(
                "tcp.synth.complete",
                trade_date=target.isoformat(),
                rows_inserted=response["rows_inserted"],
                duration_ms=duration_ms,
                status=status,
            )
            return response
        finally:
            try:
                cursor.close()
            except pyodbc.Error:
                pass
    except Exception as exc:
        log.error("tcp.synth.failed", error=str(exc))
        raise
    finally:
        if owned_conn:
            conn.close()
