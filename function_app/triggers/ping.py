"""HttpTrigger_Ping — anonymous warm-up endpoint at GET /api/ping.

Returns a small JSON envelope after a ``SELECT 1`` round trip. The user clicks
"Wake up the database" in the SWA UI before the first real ``/api/ask`` call;
this endpoint resumes the auto-paused serverless SQL instance and surfaces the
resume latency back to the browser. See ``03_architecture.md §3.5`` (MJ-06).

The route is intentionally anonymous: it touches no row-scoped data and the
``SELECT 1`` query has no parameter binding. Rate-limiting is delegated to the
SWA platform's anti-abuse layer.
"""

from __future__ import annotations

import json
import time as time_mod

import azure.functions as func
import pyodbc
import structlog

from function_app import app
from tcp.db import open_connection

_log = structlog.get_logger(__name__)


@app.route(
    route="ping",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def ping(req: func.HttpRequest) -> func.HttpResponse:
    """Return ``{"status", "sql_resume_ms", "db_version"}`` after ``SELECT 1``.

    Emits ``tcp.sql.resume_ms`` and ``tcp.func.ping.*`` log events. On a SQL
    transport failure the function returns HTTP 503 with a short JSON body so
    the SWA UI can render a "database asleep, try again" message instead of a
    generic 5xx.

    Args:
        req: The ``HttpRequest`` (unused; the route is parameter-free).

    Returns:
        A 200 OK on success, 503 on a database connectivity failure.
    """
    del req  # the ping route accepts no inputs.
    start = time_mod.perf_counter()
    conn: pyodbc.Connection | None = None
    try:
        conn = open_connection(bypass_session_context=True)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT @@VERSION")
            row = cursor.fetchone()
            db_version = str(row[0]) if row is not None and row[0] is not None else "unknown"
        finally:
            try:
                cursor.close()
            except pyodbc.Error:
                pass
        duration_ms = int((time_mod.perf_counter() - start) * 1000)
        # Heuristic: a resume from auto-pause takes seconds; a warm ping is sub-100 ms.
        status = "resumed" if duration_ms >= 1000 else "warm"
        _log.info(
            "tcp.func.ping.complete",
            sql_resume_ms=duration_ms,
            status=status,
        )
        body = {
            "status": status,
            "sql_resume_ms": duration_ms,
            "db_version": db_version,
        }
        return func.HttpResponse(
            body=json.dumps(body),
            status_code=200,
            mimetype="application/json",
        )
    except Exception as exc:
        duration_ms = int((time_mod.perf_counter() - start) * 1000)
        _log.exception(
            "tcp.func.ping.failed",
            sql_resume_ms=duration_ms,
            error=str(exc),
        )
        body = {
            "status": "unavailable",
            "sql_resume_ms": duration_ms,
            "detail": "database connection failed",
        }
        return func.HttpResponse(
            body=json.dumps(body),
            status_code=503,
            mimetype="application/json",
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except pyodbc.Error:
                pass
