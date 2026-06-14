"""One-shot seed of dim_Employees + dim_Accounts via pyodbc + AAD access token.

Reads the access token from stdin (one line), uses it to connect to Azure SQL,
sets SESSION_CONTEXT to the Function App MI OID (admin scope), and calls
seed_employees. Pure Python; no azd / azure-identity dependency.

Usage:
    az account get-access-token --resource https://database.windows.net \\
        --query accessToken -o tsv | python scripts/seed_aad.py
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import pyodbc

REPO_ROOT = Path(__file__).resolve().parent.parent
# Use the deployed copy under function_app/tcp because that has been kept in
# sync with the Function App package; the repo-root tcp/ is a stale duplicate
# pre-dating the function-app bundle fix.
sys.path.insert(0, str(REPO_ROOT / "function_app"))

from tcp.synth.seed_employees import seed_employees  # noqa: E402

SERVER = "sql-tcp-prod-weu.database.windows.net"
DATABASE = "sqldb-tcp-prod-weu"
FUNCTION_APP_MI_OID = "1ddab34c-671f-45ee-9a08-a997070423bf"


def _open_conn_with_token(token: str) -> pyodbc.Connection:
    token_bytes = token.encode("UTF-16-LE")
    token_struct = struct.pack(f"=I{len(token_bytes)}s", len(token_bytes), token_bytes)
    SQL_COPT_SS_ACCESS_TOKEN = 1256
    # Try Driver 18 then 17 — local SQL Server 2022 client ships with 17 only.
    drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    driver = next((d for d in drivers if "18" in d), None) or next((d for d in drivers if "17" in d), None) or drivers[0]
    print(f"Using ODBC driver: {driver}", file=sys.stderr)
    conn_str = (
        f"Driver={{{driver}}};"
        f"Server={SERVER},1433;Database={DATABASE};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=90;"
    )
    conn = pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct}, autocommit=False)
    return conn


def main() -> int:
    token = sys.stdin.read().strip()
    if not token:
        print("ERROR: no access token on stdin", file=sys.stderr)
        return 1

    conn = _open_conn_with_token(token)
    # The Function App MI is in dim_UserRoles with admin scope; impersonate it
    # via SESSION_CONTEXT so RLS lets the seed write across all rows.
    cursor = conn.cursor()
    cursor.execute("EXEC sp_set_session_context @key=N'aad_object_id', @value=?", FUNCTION_APP_MI_OID)
    # seed_employees uses the supplied conn AS-IS — it does not commit/close.
    # Wrap RLS off/on so the INSERTs are unblocked even though we're admin.
    cursor.execute("ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = OFF)")
    try:
        counts = seed_employees(conn=conn)
        conn.commit()
        print(f"OK: {counts}")
    finally:
        cursor.execute("ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = ON)")
        conn.commit()
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
