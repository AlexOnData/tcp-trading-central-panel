"""Backfill synthetic trades for every business day in a date range.

Calls tcp.synth.runner.run_daily for each calendar day, letting the SQL
side `_resolve_target_date` skip weekends and Romanian holidays. The
runner's `usp_GenerateDailyTrades` proc is idempotent — re-running for a
day with existing rows returns status="already_generated" without
duplicating data.

Usage:
    az account get-access-token --resource https://database.windows.net \\
        --query accessToken -o tsv | \\
        python scripts/backfill_trades.py 2025-01-01 2026-05-18
"""
from __future__ import annotations

import os
import struct
import sys
import time as time_mod
from datetime import date, timedelta
from pathlib import Path

import pyodbc

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "function_app"))

# Set generator OID BEFORE importing runner (runner reads env at run-time, OK
# either order, but setting here keeps the import deterministic).
GENERATOR_OID = "1ddab34c-671f-45ee-9a08-a997070423bf"
os.environ.setdefault("TCP_GENERATOR_OID", GENERATOR_OID)

from tcp.synth.runner import run_daily  # noqa: E402

SERVER = "sql-tcp-prod-weu.database.windows.net"
DATABASE = "sqldb-tcp-prod-weu"


def _open_conn(token: str) -> pyodbc.Connection:
    token_bytes = token.encode("UTF-16-LE")
    token_struct = struct.pack(f"=I{len(token_bytes)}s", len(token_bytes), token_bytes)
    drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    driver = (
        next((d for d in drivers if "18" in d), None)
        or next((d for d in drivers if "17" in d), None)
        or drivers[0]
    )
    print(f"Using ODBC: {driver}", file=sys.stderr)
    conn_str = (
        f"Driver={{{driver}}};"
        f"Server={SERVER},1433;Database={DATABASE};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=90;"
    )
    return pyodbc.connect(conn_str, attrs_before={1256: token_struct}, autocommit=False)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: <token-via-stdin> backfill_trades.py START_DATE END_DATE (ISO format)", file=sys.stderr)
        return 1
    start = date.fromisoformat(sys.argv[1])
    end = date.fromisoformat(sys.argv[2])

    token = sys.stdin.read().strip()
    if not token:
        print("ERROR: no token on stdin", file=sys.stderr)
        return 1

    conn = _open_conn(token)
    cursor = conn.cursor()
    # Set admin session context once (we hold the connection across all days)
    cursor.execute("EXEC sp_set_session_context @key=N'aad_object_id', @value=?", GENERATOR_OID)
    conn.commit()

    totals = {"trades": 0, "ok_days": 0, "already_days": 0, "skipped_days": 0, "failed_days": 0}
    failures: list[tuple[date, str]] = []
    t0 = time_mod.perf_counter()

    current = start
    while current <= end:
        # run_daily uses `today` to pick the previous business day. To target a
        # specific date X, pass today=X+1 (so the lookup returns X if it is a
        # business day). For weekends/holidays the SQL-side `fn_PreviousBusinessDay`
        # walks further back, so this naturally skips non-trading days.
        today_sim = current + timedelta(days=1)
        try:
            result = run_daily(today=today_sim, conn=conn)
            status = result.get("status", "unknown")
            rows = result.get("rows_inserted", 0)
            if status == "ok":
                totals["trades"] += int(rows)
                totals["ok_days"] += 1
                conn.commit()
                # Brief checkpoint output every 20 days
                if totals["ok_days"] % 20 == 0:
                    elapsed = time_mod.perf_counter() - t0
                    print(f"  [{current}] {totals['ok_days']} days OK, {totals['trades']} trades, {elapsed:.0f}s elapsed", flush=True)
            elif status == "already_generated":
                totals["already_days"] += 1
            elif status in ("skipped_non_trading_day", "skipped_holiday"):
                totals["skipped_days"] += 1
            else:
                print(f"  [{current}] UNKNOWN status={status}: {result}", flush=True)
        except Exception as exc:
            totals["failed_days"] += 1
            failures.append((current, str(exc)))
            print(f"  [{current}] FAILED: {exc}", flush=True)
            try:
                conn.rollback()
            except Exception:
                pass
        current += timedelta(days=1)

    elapsed = time_mod.perf_counter() - t0
    print("\n=== Backfill summary ===")
    print(f"  Range:           {start} → {end} ({(end - start).days + 1} calendar days)")
    print(f"  OK days:         {totals['ok_days']:>4}  ({totals['trades']:>6} trades inserted)")
    print(f"  Already-gen:     {totals['already_days']:>4}")
    print(f"  Skipped (we/ho): {totals['skipped_days']:>4}")
    print(f"  Failed:          {totals['failed_days']:>4}")
    print(f"  Elapsed:         {elapsed:.1f}s")
    if failures:
        print("\n=== Failures (first 10) ===")
        for d, err in failures[:10]:
            print(f"  {d}: {err[:200]}")
    conn.close()
    return 0 if totals["failed_days"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
