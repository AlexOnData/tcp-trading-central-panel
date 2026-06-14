# `db/` — SQL migrations for TCP Trading Central Panel

> **Component scope.** This README documents the `db/` migrations directory only. For project-wide context, deploy walkthrough, troubleshooting, and the full doc index, see the [top-level README](../README.md). For terminology, see the [glossary](../docs/glossary.md).

This directory holds the canonical T-SQL schema for the TCP project. Every change to the database goes through a numbered forward script in `migrations/` and a paired rollback under `migrations/rollback/`.

## Layout

```
db/
  README.md                              -- this file
  migrations/
    V001__init.sql                       -- initial schema (Etapa 2)
    rollback/
      V001__init.down.sql                -- destructive rollback for V001
```

Future migrations land alongside `V001__init.sql` as `V002__*.sql`, `V003__*.sql`, etc.

## Source of truth and decisions

- Schema design: [`docs/design/02_database_design.md`](../docs/design/02_database_design.md) (canonical §15 DDL bundle).
- `fact_DailyTraderPnL` materialisation: [`docs/decisions/ADR-002-daily-pnl-materialisation.md`](../docs/decisions/ADR-002-daily-pnl-materialisation.md).
- Row-Level Security via `SESSION_CONTEXT('aad_object_id')`: [`docs/decisions/ADR-003-rls-session-context.md`](../docs/decisions/ADR-003-rls-session-context.md).
- BACPAC export schedule and rollback policy: [`docs/decisions/ADR-004-bacpac-export-schedule.md`](../docs/decisions/ADR-004-bacpac-export-schedule.md).

## Apply locally (Docker SQL Server 2022)

A local SQL Server 2022 container gives a high-fidelity Azure SQL development experience. Replace the SA password with one of your own (12+ chars, upper/lower/digit/special).

```bash
docker run -d --name tcp-sql \
  -e "ACCEPT_EULA=Y" \
  -e "MSSQL_SA_PASSWORD=YourStrong!Passw0rd" \
  -p 1433:1433 mcr.microsoft.com/mssql/server:2022-latest

sqlcmd -S localhost,1433 -U sa -P 'YourStrong!Passw0rd' \
  -Q "CREATE DATABASE tcp_dev;"

sqlcmd -S localhost,1433 -U sa -P 'YourStrong!Passw0rd' \
  -d tcp_dev -i db/migrations/V001__init.sql -b
```

Re-running the same migration is safe; every object is guarded by an `IF OBJECT_ID(...) IS NULL` (or schema/role/policy equivalent) and every seed insert is an idempotent `MERGE` or `IF NOT EXISTS ...`.

## Apply against Azure SQL (after Etapa 4 provisions the server)

Azure SQL accepts AAD interactive (`-G`) login from `sqlcmd` 17.10+. No SQL-auth password is required; `az login` provides the token.

```bash
az login
sqlcmd -S sql-tcp-prod-weu.database.windows.net \
       -d sqldb-tcp -G -i db/migrations/V001__init.sql -b
```

Rollback (destructive — only for clean re-bootstrap of a non-production database):

```bash
sqlcmd -S sql-tcp-prod-weu.database.windows.net \
       -d sqldb-tcp -G -i db/migrations/rollback/V001__init.down.sql -b
```

For production data corruption inside the 7-day retention window, restore from Azure SQL PITR instead of running the rollback script (see ADR-004).

## Run the integration tests

The three SQL tests live under `tests/sql/`. Each script exits non-zero (via `RAISERROR` severity 16) on failure so `sqlcmd -b` propagates the failure to CI.

```bash
sqlcmd -S <conn> -d <db> -G -i tests/sql/test_naming_convention.sql   -b
sqlcmd -S <conn> -d <db> -G -i tests/sql/test_rls_smoke.sql           -b
sqlcmd -S <conn> -d <db> -G -i tests/sql/test_fx_rate_completeness.sql -b
```

What each test covers:

- `test_naming_convention.sql` — enforces the `(fact|dim|config)_PascalCase` / `v_snake_case` / `usp_PascalCase` / `fn_PascalCase` / `tvf_PascalCase` regex from `02_database_design.md §14`.
- `test_rls_smoke.sql` — three test cases for the SESSION_CONTEXT-based RLS contract (deny-by-default, trader scope visibility, admin scope visibility). Non-destructive — wraps inserts in a transaction and rolls back.
- `test_fx_rate_completeness.sql` — CI guard for the application-layer invariant that every closed non-EUR trade has a `fx_rate_to_eur` value.

## Migration policy

- Numbered forward scripts only: `V001`, `V002`, ... Never edit a script that has been applied to any environment; instead add a new one.
- Each forward script records itself in `dbo.schema_history (script_name, applied_at_utc, checksum)`. The CI pipeline (Etapa 4) computes the checksum and verifies the recorded value against the script-on-disk to detect drift.
- Rollback scripts under `migrations/rollback/` mirror the forward script number and end in `.down.sql`. They are advisory and DESTRUCTIVE — production rollback goes through PITR or BACPAC restore, not these scripts.
- Idempotency contract: applying any forward script twice in a row produces the same end state. Tests must be non-destructive (RLS smoke wraps all writes in a rolled-back transaction).
