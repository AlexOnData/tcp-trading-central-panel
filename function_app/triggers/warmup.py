"""WarmupTrigger — resume SQL DB from auto-pause at 06:55 RO every weekday.

Runs ``SELECT 1`` against the database. Total work: ~30 s on a cold start (the
resume itself), milliseconds otherwise. The daily generator at 07:00 then finds
the DB warm, keeping the §14 latency budget comfortably under target.

The trigger uses the documented escape-hatch ``open_connection(bypass_session_context=True)``
because the warmup does not query a single user-scoped row — it only nudges the
serverless instance out of auto-pause. No ADR-003 contract concern applies.
"""

from __future__ import annotations

import time as time_mod

import azure.functions as func
import pyodbc
import structlog

from function_app import app
from tcp.db import open_connection

_log = structlog.get_logger(__name__)


@app.timer_trigger(
    schedule="0 55 6 * * 1-5",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def warmup(timer: func.TimerRequest) -> None:
    """Issue ``SELECT 1`` to resume the auto-paused Azure SQL serverless instance.

    Emits ``tcp.sql.resume_ms`` so the dashboard can distinguish a true cold start
    (≥ 5 s) from a warm ping (< 100 ms). The trigger never raises; a failure here
    is recoverable on the next manual ``/api/ping`` or the daily generator itself.

    Args:
        timer: The Functions-runtime ``TimerRequest`` (carries ``past_due``).
    """
    if timer.past_due:
        _log.warning("tcp.func.warmup.past_due")

    start = time_mod.perf_counter()
    conn: pyodbc.Connection | None = None
    try:
        conn = open_connection(bypass_session_context=True)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        finally:
            try:
                cursor.close()
            except pyodbc.Error:
                pass
        duration_ms = int((time_mod.perf_counter() - start) * 1000)
        _log.info("tcp.func.warmup.complete", sql_resume_ms=duration_ms)
    except Exception as exc:
        duration_ms = int((time_mod.perf_counter() - start) * 1000)
        _log.exception(
            "tcp.func.warmup.failed",
            sql_resume_ms=duration_ms,
            error=str(exc),
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except pyodbc.Error:
                pass
