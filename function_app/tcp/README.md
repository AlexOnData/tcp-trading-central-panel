# `tcp` — Trading Central Panel Python package

> **Component scope.** This README documents the `tcp/` Python package only. For project-wide context, deploy walkthrough, troubleshooting, and the full doc index, see the [top-level README](../README.md). For terminology, see the [glossary](../docs/glossary.md).

This package hosts the production Python code for the TCP — Trading Central
Panel project. The current module, `tcp.db`, is the **connection layer for
Azure SQL** and implements the SESSION_CONTEXT contract defined in
[ADR-003](../docs/decisions/ADR-003-rls-session-context.md).

Future modules (`tcp.synth`, `tcp.functions`, …) will be added in later
stages of the build.

## Purpose

`tcp.db` is the only sanctioned way for the Function App and the daily
generator to open a SQL connection. It guarantees that:

1. Every user-driven connection sets `SESSION_CONTEXT('aad_object_id')`
   with `@read_only=1` immediately after the check-out, so RLS predicates
   can resolve the caller's scope from `dim_UserRoles`.
2. Every connection clears `SESSION_CONTEXT` on check-in, so a pooled
   connection cannot leak identity to the next caller.
3. The production auth path is AAD passwordless (Managed Identity in
   Azure, `DefaultAzureCredential` locally); SQL auth is allowed only as
   a development fallback gated by explicit env vars.

See [`02_database_design.md` §9](../docs/design/02_database_design.md) for
the RLS predicate that consumes these values, and the [ADR-003
contract](../docs/decisions/ADR-003-rls-session-context.md) for the
full failure-mode discussion.

## Install

From the repo root:

```bash
uv sync                # production deps
uv sync --extra dev    # + tooling (ruff, mypy, pytest, types-pyodbc)
```

The project targets Python 3.12+ and ships a `py.typed` marker, so
`mypy --strict` consumers will get full type information.

## Testing

Unit tests have **no live-DB requirement** — they mock `pyodbc.connect`:

```bash
uv run pytest tests/unit -v
```

The integration suite is opt-in and requires a reachable SQL Server:

```bash
TCP_SQL_SERVER=sql-tcp-dev-weu.database.windows.net \
TCP_SQL_DATABASE=sqldb-tcp-dev-weu \
uv run pytest tests/integration -v -m integration
```

When `TCP_SQL_SERVER` is absent, the integration tests skip cleanly.

## Auth-mode resolution

`AuthMode.from_env()` selects one of three strategies, in this order:

| Env vars present                                  | Resolved `AuthMode`     | Use case                                  |
| ------------------------------------------------- | ----------------------- | ----------------------------------------- |
| `TCP_SQL_DEV_USER` **and** `TCP_SQL_DEV_PASSWORD` | `SQL_AUTH_DEV`          | Local SQL Server, bootstrap, dev fallback |
| `IDENTITY_ENDPOINT` (set by Azure host)           | `AAD_MANAGED_IDENTITY`  | Function App / App Service runtime        |
| _none of the above_                               | `AAD_DEFAULT`           | Local dev with `az login`                 |

`SQL_AUTH_DEV` always wins when its creds are present, even on a Functions
host — this lets us troubleshoot from a workstation against a Free-tier
SQL using a temporary SQL admin without changing code.

`SqlConfig.from_env()` reads `TCP_SQL_SERVER` and `TCP_SQL_DATABASE`,
falling back to `localhost,1433` / `tcp_dev` for the LocalDB / docker dev
loop.

## Example

```python
from uuid import UUID

from tcp.db import SessionContext, connection_for_user

principal = SessionContext(aad_object_id=UUID("11111111-2222-3333-4444-555555555555"))
with connection_for_user(principal) as conn:
    rows = conn.cursor().execute("SELECT * FROM dbo.v_employee_performance").fetchall()
```

The RLS predicate will filter `v_employee_performance` to the rows
visible to the principal's scope (`trader` / `team_lead` / `floor_manager`
/ `admin`) — `tcp.db` never has to know which.

## Defensive helpers

- `assert_session_context_set(conn)` — re-reads the bound `aad_object_id`
  and raises `SessionContextUnsetError` if NULL. Useful as a guard at the
  top of `safe_query.py` (Etapa 5) so a coding mistake fails loudly
  instead of returning empty result sets.
- `TcpDbError` is the base exception; `SessionContextUnsetError`,
  `TcpConnectionError`, and `AuthError` are the three concrete subclasses.

## Logging

`tcp.db` emits structured events via `structlog`. The
`connection_for_user` context manager binds only the **last four
characters** of the `aad_object_id` to its log lines, so logs stay
correlatable without becoming a PII surface. Connection strings are
redacted with `_redact()` before they hit the structured fields.

## Out of scope (and where they live)

- Synthetic trade generation: `tcp.synth.*` (Etapa 3).
- LLM safety / SQL allowlisting: `tcp.safe_query` (Etapa 5).
- HTTP wiring for `/api/ask`: `function_app.py` (Etapa 5).
- Bicep / infra: `infra/` (Etapa 4).
