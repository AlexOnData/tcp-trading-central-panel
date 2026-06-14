# ADR-003: Row-Level Security via SESSION_CONTEXT contract

- **Status**: Accepted
- **Date**: 2026-05-15
- **Stage**: Etapa 1 (design), enforced from Etapa 2 (schema) and Etapa 5 (Function App code)

## Context

The TCP platform's AI assistant runs every user question through a single Function App connection to Azure SQL. The Function App authenticates to SQL with a Managed Identity that holds two database roles: `tcp_ai_assistant` (SELECT on `v_*` views) and `tcp_generator` (INSERT/UPDATE on `fact_Trades` + `fact_DailyTraderPnL`). With a single MI servicing all users, **per-user filtering cannot come from SQL principals** — there is only one SQL principal, regardless of which AAD user clicked "Ask".

The pass-1 reviews (`review_arch_pass1.md` CR-01/CR-02; `review_db_pass1.md` CR-06/MJ-05/MJ-06; `review_holistic_pass1.md` MA-07) converged on the requirement that **per-user row filtering** must come from SQL Server's Row-Level Security mechanism — but RLS predicates need a way to identify the *human* caller behind the single MI.

`SESSION_CONTEXT` is SQL Server's per-connection key-value store. The Function App can write the caller's AAD `oid` claim into `SESSION_CONTEXT('aad_object_id')` immediately after opening a connection, before issuing any query. The RLS predicate function then joins `dim_UserRoles` on that value to resolve the caller's scope (`trader` / `team_lead` / `floor_manager` / `admin`) and filters `fact_Trades` accordingly.

The mechanism is correct but the contract has several non-obvious failure modes that must be pinned down in this ADR so Etapa 2 and Etapa 5 code does not regress.

## Decision

**The Function App MUST set `SESSION_CONTEXT('aad_object_id')` on every connection check-out, before any user-driven query.** The mechanism follows these rules:

### 1. AAD `oid` is the identity binding

The Function App parses the `x-ms-client-principal` header injected by Static Web Apps `linked backend`, extracts the `claims[].typ == "http://schemas.microsoft.com/identity/claims/objectidentifier"` value (also exposed as `oid`), and uses that GUID as the key.

The `oid` is the **immutable** AAD object id of the user. It does not change with username/email rotation, group membership, or password changes. This is the right identity binding for a long-lived RLS mapping.

### 2. Set, then read-only-lock

After parsing claims, the Function App opens a connection and runs **exactly**:

```sql
EXEC sp_set_session_context @key=N'aad_object_id', @value=@oid, @read_only=1;
```

`@read_only=1` prevents the rest of the query batch from overwriting the value — including any SQL emitted by the LLM. Once locked, the value is stable for the lifetime of the connection.

### 3. Deny-by-default when SESSION_CONTEXT is unset

The RLS predicate function (`rls.fn_TradesPredicate`) starts with a CROSS APPLY that resolves the principal's scope from `dim_UserRoles`:

```sql
RETURN
    SELECT 1 AS result FROM (
        SELECT TOP 1 ur.scope, ur.employee_id, e.team_id AS principal_team, e.floor_id AS principal_floor
        FROM dbo.dim_UserRoles AS ur
        LEFT JOIN dbo.dim_Employees AS e ON e.employee_id = ur.employee_id
        WHERE ur.is_active = 1
          AND ur.aad_object_id = CAST(SESSION_CONTEXT(N'aad_object_id') AS UNIQUEIDENTIFIER)
    ) AS p
    WHERE p.scope = 'admin'
       OR (p.scope = 'trader' AND p.employee_id = @trader_id_in_row)
       OR EXISTS (
            SELECT 1 FROM dbo.dim_Employees t
            WHERE t.employee_id = @trader_id_in_row
              AND ((p.scope = 'team_lead'     AND t.team_id  = p.principal_team)
                OR (p.scope = 'floor_manager' AND t.floor_id = p.principal_floor))
       );
```

If `SESSION_CONTEXT('aad_object_id')` is **unset** (e.g., a forgotten connection setup, a pooled connection that bypassed the lifecycle hook, or a service principal that has not been registered in `dim_UserRoles`), `SESSION_CONTEXT(...)` returns NULL, the inner CROSS APPLY produces no rows, and the outer SELECT returns no rows. The RLS policy then filters out every fact row. **Deny-by-default is the safe failure mode.**

### 4. Connection-pool hygiene

The Function App uses connection pooling (default in the `pyodbc` + Azure Functions stack). Pooled connections retain their session context across check-out/check-in unless explicitly reset. To avoid cross-user leakage:

- The Function App `tcp/db.py` module **MUST** call `sp_set_session_context @key=N'aad_object_id', @value=NULL, @read_only=0` on connection check-in.
- Before setting the new value on check-out, it calls the same with `@value=<new oid>, @read_only=1`.
- A pytest `tests/integration/test_session_context_isolation.py` (Etapa 5) verifies that two consecutive requests with different `oid` values return only their respective scopes.

### 5. Generator path: MI registered with `scope='admin'`

The daily generator (`TimerTrigger_DailyGenerator`) and the BACPAC trigger (`TimerTrigger_BacpacExport`) authenticate as the Function App's Managed Identity — they do not have a human `oid`. Their identity is the MI's own AAD object id, which is provisioned into `dim_UserRoles` with `scope='admin'` by the Etapa-4 post-provision hook:

```sql
-- run by infra/scripts/postprovision.ps1 after first deploy
INSERT INTO dbo.dim_UserRoles (aad_object_id, employee_id, scope, is_active)
VALUES ('<MI-object-id-from-az-cli>', NULL, 'admin', 1);
```

`employee_id` is `NULL` for service-principal rows (allowed by the schema's `CHECK CK_dim_UserRoles_scope_employee` which forbids NULL `employee_id` only for non-admin scopes).

At connection time, the generator code looks up its own MI `oid` via the `IDENTITY_ENDPOINT` IMDS call and sets `SESSION_CONTEXT('aad_object_id', @mi_oid)`. The RLS predicate then returns `TRUE` for every row, allowing inserts to bypass the block predicate (BLOCK PREDICATE AFTER INSERT runs through the same filter predicate).

### 6. PowerBI service principal also registered as admin

The PowerBI scheduled-refresh job authenticates as a separate service principal. Same pattern — the SP's `oid` is registered in `dim_UserRoles` with `scope='admin'` during the Etapa-7 setup script.

### 7. CI verification

Etapa-4 CI (`ci.yml`) includes an integration smoke test that:

1. Creates a synthetic `dim_UserRoles` row for a test AAD object id with `scope='trader'`.
2. Opens a SQL connection, calls `sp_set_session_context @key=N'aad_object_id', @value=<test oid>`.
3. Issues `SELECT COUNT(*) FROM v_trades_enriched WHERE trader_id = <some-other-trader>` — asserts 0 rows.
4. Tears the synthetic row down.

A second test confirms that without `SESSION_CONTEXT` set, the same query returns 0 rows (deny-by-default).

## Consequences

- **Architecture doc** `03_architecture.md` §3.2 and §5 reference this ADR and the SESSION_CONTEXT contract.
- **DB design** `02_database_design.md` §9 (RLS) restates the contract and pins the predicate function shape.
- **Function App code** (Etapa 5) inherits a strict connection lifecycle: every connection check-out sets the context; every check-in clears it. No other code paths may emit SQL on the same connection.
- **`safe_query.py`** (Etapa 5) MUST refuse any LLM-emitted SQL that contains `sp_set_session_context`, `SESSION_CONTEXT`, or any `EXEC sys.sp_*` form. The sqlglot allowlist forbids stored-proc calls outside the documented two (`usp_GetEmployeePerformance`, `usp_GetTopPerformers`).
- **Performance**: each `/api/ask` adds one extra round trip (~5 ms warm) to set the context. Acceptable inside the §14 warm budget.
- **Auditability**: every SQL audit log row carries the connection's `SESSION_CONTEXT` value, which traces back to a specific AAD `oid` → `dim_UserRoles` row → human employee. Forensic chain is intact.

## Alternatives rejected

- **One SQL principal per AAD user (contained users)**: Azure SQL supports AAD users, but creating one per assistant user adds onboarding ceremony and does not scale beyond a few dozen humans before the per-user `CREATE USER ... FROM EXTERNAL PROVIDER` step becomes operationally awkward. Also forces a different role-grant story per user.
- **JWT-claims-based predicate (no SESSION_CONTEXT)**: SQL Server's RLS predicates cannot read HTTP headers directly. There is no built-in mechanism to consume an AAD claim inside the predicate function — the only application-supplied input is via `SESSION_CONTEXT` or `CONTEXT_INFO` (deprecated).
- **Row-by-row filtering in the Function App after the query**: defeats the purpose; the SQL Server would still return all rows, exposing data via memory/log inspection and consuming bandwidth + vCore-seconds.

## References

- `docs/design/02_database_design.md` §9 (Row-Level Security) and §10 (Roles & permissions).
- `docs/design/03_architecture.md` §3.2 (User-question path) and §5 (RBAC matrix).
- `docs/design/reviews/review_arch_pass1.md` CR-01, CR-02 (the originating findings).
- `docs/design/reviews/review_db_pass1.md` CR-06, MJ-05, MJ-06 (the originating findings on the DB side).
- `docs/design/reviews/review_holistic_pass1.md` MA-07 (the cross-cutting finding).
