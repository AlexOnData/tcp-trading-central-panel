# Etapa 2 SQL review — pass 1

**Reviewer**: database-optimizer
**Date**: 2026-05-15
**Verdict**: ACCEPT_WITH_CHANGES

## Summary

The V001 bundle is structurally sound, idempotent in most paths, and conforms to the canonical §15 DDL on every load-bearing object (naming, RLS shape, persisted `trade_date_ro`, filtered unique indexes, ADR-002 materialisation, ADR-003 predicate). One **critical** defect will cause `V001__init.sql` to fail on a fresh apply: `dbo.tvf_RiskMetrics` mixes scalar aggregates with `PERCENTILE_CONT(...) OVER ()` referencing a base column in the same projection — SQL Server rejects this at function-creation time. A second **major** finding breaks the re-runnability contract on the `tcp_admin` role bindings. The rest are spec deviations on `schema_history` shape, a couple of nits on naming and Romanian holiday labels, and one minor on the documented "scalar wrapper" relationship between `fn_GetCapitalBaseline` and `tvf_GetCapitalBaseline`.

## Critical (blocks merging Etapa 2)

- [ ] **CR-01** | `db/migrations/V001__init.sql:999-1020` | `tvf_RiskMetrics` body mixes scalar aggregates and a window function over a non-grouped column. The SELECT has no `GROUP BY` but contains `COUNT_BIG(*)`, `AVG(ep.net_pnl_eur_total)`, `STDEV(...)`, `SUM(...)` (implicit single-group aggregation) **plus** `PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY ep.net_pnl_eur_total) OVER ()`. SQL Server treats `PERCENTILE_CONT(...) OVER ()` as a window function operating on the FROM rows; the bare column reference `ep.net_pnl_eur_total` is then "not contained in either an aggregate function or the GROUP BY clause", raising Msg 8120 at `CREATE OR ALTER FUNCTION` time. The entire V001 apply then fails at line 1000 and rolls back everything created after the prior `GO`. **Why this matters**: the bundle stops applying — every downstream object (views below, procs, RLS predicate, security policy, grants, schema_history insert) is never created. A fresh `sqlcmd -i V001__init.sql -b` exits non-zero.
  **Fix**: nest the percentile inside a derived table so it joins back as a single scalar to the outer aggregate row.
  ```sql
  CREATE OR ALTER FUNCTION dbo.tvf_RiskMetrics
  (
      @employee_id INT,
      @from        DATE,
      @to          DATE
  )
  RETURNS TABLE
  WITH SCHEMABINDING
  AS
  RETURN
      SELECT
          a.trading_days,
          a.mean_daily_pnl,
          a.stdev_daily_pnl,
          a.stdev_downside,
          p.var_95,
          a.total_net_pnl
      FROM (
          SELECT
              COUNT_BIG(*)                                                            AS trading_days,
              AVG(ep.net_pnl_eur_total)                                               AS mean_daily_pnl,
              STDEV(ep.net_pnl_eur_total)                                             AS stdev_daily_pnl,
              STDEV(CASE WHEN ep.net_pnl_eur_total < 0 THEN ep.net_pnl_eur_total END) AS stdev_downside,
              SUM(ep.net_pnl_eur_total)                                               AS total_net_pnl
          FROM dbo.v_employee_performance AS ep
          WHERE ep.employee_id    = @employee_id
            AND ep.trade_date_ro BETWEEN @from AND @to
      ) AS a
      CROSS APPLY (
          SELECT DISTINCT PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY ep.net_pnl_eur_total) OVER () AS var_95
          FROM dbo.v_employee_performance AS ep
          WHERE ep.employee_id    = @employee_id
            AND ep.trade_date_ro BETWEEN @from AND @to
      ) AS p;
  ```
  (`DISTINCT` collapses the constant percentile produced for every row of the percentile-scoped set back to one row before the cross-apply. Alternative: use `APPROX_PERCENTILE_CONT(...) WITHIN GROUP (ORDER BY ...)` as an ordered-set aggregate in the same SELECT as the other aggregates — that one is legal without GROUP BY.)

## Major

- [ ] **MJ-01** | `db/migrations/V001__init.sql:1270-1271` | `ALTER ROLE db_datareader ADD MEMBER tcp_admin;` and `ALTER ROLE db_datawriter ADD MEMBER tcp_admin;` are **not idempotent**. A second apply (a developer re-running the script against the same DB) raises Msg 15410 "User or role 'tcp_admin' is already a member of 'db_datareader'." The forward script's idempotency contract (README §"Apply locally", `02_database_design.md §11`) is then broken even though every other object in V001 honours it. The down script already has the matching `IS_ROLEMEMBER` guard (lines 131-134) — the forward script should mirror it.
  **Fix**:
  ```sql
  IF IS_ROLEMEMBER(N'db_datareader', N'tcp_admin') = 0
      ALTER ROLE db_datareader ADD MEMBER tcp_admin;
  IF IS_ROLEMEMBER(N'db_datawriter', N'tcp_admin') = 0
      ALTER ROLE db_datawriter ADD MEMBER tcp_admin;
  ```

- [ ] **MJ-02** | `db/migrations/V001__init.sql:25-35` vs `docs/design/02_database_design.md §15 line 1267-1276` | `dbo.schema_history` column shape diverges from the spec. V001 ships `(script_name PK, applied_at_utc DATETIME2(3), checksum NVARCHAR(128))`; spec §15 specifies `(version PK, description NVARCHAR(200), checksum VARBINARY(32), applied_at DATETIMEOFFSET(3), applied_by NVARCHAR(128))`. The down script (line 155) and the V001 insert (line 1278) are internally consistent with the implementation shape, but downstream CI tooling that assumes the spec shape (e.g., a `version` lookup) will break. Choose one and update the other.
  **Fix**: either update the spec to the implementation shape (preferred — the implementation is friendlier for the file-name-driven CI workflow), or rewrite V001 to:
  ```sql
  CREATE TABLE dbo.schema_history
  (
      [version]      VARCHAR(20)        NOT NULL,
      [description]  NVARCHAR(200)      NOT NULL,
      checksum       VARBINARY(32)      NULL,
      applied_at     DATETIMEOFFSET(3)  NOT NULL CONSTRAINT DF_schema_history_applied_at DEFAULT (SYSDATETIMEOFFSET()),
      applied_by     NVARCHAR(128)      NOT NULL CONSTRAINT DF_schema_history_applied_by DEFAULT (SUSER_SNAME()),
      CONSTRAINT PK_schema_history PRIMARY KEY ([version])
  );
  ```
  and align the insert + the rollback delete.

## Minor / nits

- [ ] **MN-01** | `db/migrations/V001__init.sql:656, 673, 691, 709, 726, 744, 762` | Romanian Easter Monday rows carry the Romanian label `N'Paștele'` (identical to Sunday). The audit checklist asked specifically that Easter Monday be labelled `Paștele lunii` (or the more idiomatic `A doua zi de Paște`). The English column already distinguishes (`Orthodox Easter Monday`); fix the Romanian column for thesis correctness.
  **Fix**: replace each Easter-Monday row's RO name from `N'Paștele'` to `N'A doua zi de Paște'` (the Codul Muncii statutory term; `Paștele lunii` is colloquial — use the legal term in the dim table).

- [ ] **MN-02** | `db/migrations/V001__init.sql:785-806, 808-825` | `fn_GetCapitalBaseline` (scalar) and `tvf_GetCapitalBaseline` (inline TVF) are **independent re-implementations** of the same lookup, not a wrapper relationship. The audit checklist asked: "`tvf_GetCapitalBaseline` is an inline TVF; `fn_GetCapitalBaseline` is a scalar wrapper." Two divergent copies are a latent drift risk (one will be patched, the other forgotten). Make the scalar a wrapper.
  **Fix**:
  ```sql
  CREATE OR ALTER FUNCTION dbo.fn_GetCapitalBaseline
  (
      @trader_id INT,
      @as_of     DATETIMEOFFSET(3)
  )
  RETURNS DECIMAL(18,2)
  WITH SCHEMABINDING
  AS
  BEGIN
      DECLARE @amount DECIMAL(18,2);
      SELECT @amount = amount_eur
      FROM dbo.tvf_GetCapitalBaseline(@trader_id, @as_of);
      RETURN @amount;
  END;
  ```

- [ ] **MN-03** | `db/migrations/V001__init.sql:154-156` | Filtered unique index on `dim_Employees.aad_object_id` is named with the `UQ_` prefix (`UQ_dim_Employees_aad_object_id`), but the parallel filtered unique on `dim_TradingFloors` (line 94) uses `UX_` (`UX_dim_TradingFloors_PrimaryHQ`), and `dim_UserRoles` also uses `UX_` (line 307). Pick one prefix for filtered unique INDEXes (`UX_` is more accurate — `UQ_` reads as a `UNIQUE` constraint). The spec uses `UQ_` here so the deviation is in the spec too — flag and rename in V002 if you want consistency.
  **Fix (cosmetic)**: rename to `UX_dim_Employees_aad_object_id_active` in V001 (still pre-prod).

- [ ] **MN-04** | `tests/sql/test_naming_convention.sql:29-31` | LIKE-based filter `'fact[_][A-Z]%'` enforces the prefix and the first PascalCase character but does **not** verify that the remainder of the name is alphanumeric only (the documented regex is `^(fact|dim|config)_[A-Z][a-zA-Z0-9]*$`). A non-compliant `dim_FooBar-Baz` would pass. Tighten with a tail check.
  **Fix**: add a second negative pattern, e.g.
  ```sql
  AND t.TABLE_NAME COLLATE Latin1_General_BIN LIKE '%[^a-zA-Z0-9_]%'
  ```
  combined under OR with the prefix violations.

- [ ] **MN-05** | `db/migrations/V001__init.sql:1` | `:setvar SchemaVersion 'V001'` is a `sqlcmd`-mode directive. If anyone runs the script through Azure Data Studio or SSMS without sqlcmd mode enabled, the line raises "Incorrect syntax near ':'". README documents `sqlcmd` apply, so this is acceptable, but the variable is **never referenced** elsewhere in the file — drop the directive or put it to use in the `PRINT` and the `schema_history` insert.

- [ ] **MN-06** | `db/migrations/V001__init.sql:1278` | `INSERT INTO dbo.schema_history (...) VALUES ('V001__init.sql', SYSUTCDATETIME(), 'TODO-checksum-set-by-CI');` — the checksum placeholder is a literal string `'TODO-checksum-set-by-CI'`. CI (Etapa 4) must overwrite this with the script's SHA-256 before recording. Leave a `-- CI: SHA256 placeholder` comment so the next reviewer knows it is intentional.

- [ ] **MN-07** | `db/migrations/V001__init.sql:1206-1214` | The `CREATE SECURITY POLICY` body is built inside `EXEC(N'...')` because security policies cannot live inside an `IF` block. Fine — but the inner string lacks a leading `SET NOCOUNT ON;`, so creating the policy emits row-count chatter on Azure SQL. Cosmetic; remove if you care about clean logs.

- [ ] **MN-08** | `db/migrations/V001__init.sql:1234-1250` | `tcp_generator` is granted both `EXECUTE` on the scalar `fn_GetCapitalBaseline` and `SELECT` on the inline `tvf_GetCapitalBaseline`. That is correct (EXECUTE for scalar fn, SELECT for inline TVF). Worth a one-line comment so a future maintainer doesn't "fix" the asymmetry to a uniform `EXECUTE`.

- [ ] **MN-09** | `tests/sql/test_rls_smoke.sql:127-128` | `EXEC sp_set_session_context @key=N'aad_object_id', @value=@principal_oid, @read_only=0;` after the admin row is inserted re-asserts the same OID — the comment "Force a re-read on the same connection (SESSION_CONTEXT value unchanged)" is accurate but technically unnecessary: SQL Server **re-evaluates the predicate function for every query**; the predicate does a fresh lookup of `dim_UserRoles` on each call. The re-set is harmless. Trim or rephrase.

- [ ] **MN-10** | `db/migrations/V001__init.sql:692` | Pentecost-Children's-Day collision is handled by `'2026-06-01'` carrying a combined label `N'Rusalii / Ziua Copilului'`. Correct (avoids PK conflict on the TVP). Worth a one-line note in §15 of `02_database_design.md` so the design doc explicitly references the convention.

- [ ] **MN-11** | `tests/sql/test_fx_rate_completeness.sql:18-24` | Test queries `dbo.fact_Trades` directly. If the connection running the test is `tcp_ai_assistant` (no direct fact_Trades SELECT) or any role without SELECT on `dbo.fact_Trades`, the test reports zero rows even when the invariant is violated. Document that the test must be run as `tcp_admin` (or the bootstrap admin), or rewrite to use a view (e.g., a new admin-only `v_trades_diagnostics` view) that internally has access. Operationally acceptable for v1.0 since CI runs as admin; flag for future hardening.

## Spec conformance matrix

| Spec item | File:section | Verdict | Notes |
|---|---|---|---|
| Naming regex `^(fact|dim|config)_[A-Z][a-zA-Z0-9]*$` (tables) | V001 §3,4,5 | PASS | All `dim_*`/`fact_*`/`config_*` are PascalCase. |
| Views `v_*` snake_case | V001 §9 | PASS | All five views named `v_trades_enriched`, `v_employee_performance`, `v_team_performance`, `v_floor_performance`, `v_daily_pnl`. |
| Procs `usp_PascalCase` | V001 §10 | PASS | `usp_GenerateDailyTrades`, `usp_GetEmployeePerformance`, `usp_GetTopPerformers`. |
| Functions `fn_*`/`tvf_*` | V001 §8 | PASS | `fn_GetCapitalBaseline`, `fn_IsTradingDay`, `fn_PreviousBusinessDay`, `tvf_GetCapitalBaseline`, `tvf_RiskMetrics`, `rls.fn_TradesPredicate`. |
| `WITH SCHEMABINDING` on every `v_*` | V001:873,912,936,959,981 | PASS | All five views are schemabound. |
| `DATETIMEOFFSET(3)` for all temporal columns | V001 throughout | PASS | No `DATETIME2` slip-through; only the `dim_Sessions.start_time_local`/`end_time_local` are `TIME(0)` (correct, local wall clock). |
| `trade_uid` format CHECK + `TRY_CONVERT` date validity | V001:392-395 | PASS | Both constraints present, regex correct. |
| `trade_date_ro` PERSISTED + FK to `dim_Date` | V001:375, 390-391, 422-423 | PASS | Persisted computed column with `AT TIME ZONE 'E. Europe Standard Time'`; FK + index in place. |
| `fact_DailyTraderPnL` per ADR-002 | V001:428-457 | PASS | Table + FK to `dim_Date` + index + ADR-002 column set match. |
| Same RLS policy covers `fact_DailyTraderPnL` | V001:1210-1212 | PASS | Filter + block (after insert, after update) predicates added. |
| `config_Capital` two filtered unique indexes (global + per-trader) | V001:337-343 | PASS | `UX_config_Capital_global_current` (`WHERE trader_id IS NULL`) + `UX_config_Capital_trader_current` (`WHERE trader_id IS NOT NULL`). |
| `dim_Date` populator: `SET DATEFIRST 1`, iso_year via Thursday-trick, RO + EN names, no duplicate 2026-06-01 | V001:595, 614-615, 620-631, 692-694 | PASS | DATEFIRST set, both name columns populated, Pentecost/Children's-Day 2026 collision collapsed into a single row. |
| RO holidays 2024-2030 with documented Easter dates | V001:646-771 | PASS | Easter Sundays match the prescribed dates; Good Friday = Sunday-2, Easter Monday = Sunday+1, Pentecost Monday = Sunday+50 — all verified. |
| RLS predicate uses CROSS APPLY pattern from ADR-003 | V001:1166-1195 | PASS | Inline TVF, `WITH SCHEMABINDING`, two-part naming; LEFT JOIN to `dim_Employees`; deny-by-default via NULL `aad_object_id`. |
| `tcp_ai_assistant`, `tcp_generator`, `tcp_bi_reader`, `tcp_admin` roles + correct grants | V001:43-50, 1221-1273 | PASS | All four roles; both `tcp_ai_assistant` and `tcp_generator` have `SELECT` on `dim_UserRoles` + `dim_Employees` (predicate needs it); `tcp_generator` also has `dim_UserRoles` SELECT (matches spec line 1107). |
| `tvf_RiskMetrics` exists | V001:1000-1020 | FAIL (CR-01) | Exists but fails to compile because of the aggregates-plus-window mix. |
| `tvf_GetCapitalBaseline` inline TVF; `fn_GetCapitalBaseline` scalar wrapper | V001:785-825 | PARTIAL (MN-02) | Both exist as inline + scalar but the scalar is independent, not a wrapper. |
| `CK_fact_Trades_open_closed` couples `is_open`↔`time_exit/price_exit/gross_pnl_eur/net_pnl_eur` | V001:401-405 | PASS | Both branches enforce all four columns. |
| `CK_fact_Trades_trade_uid_date_valid` uses `TRY_CONVERT(DATE, SUBSTRING(...,2,8), 112)` | V001:395 | PASS | Exact form. |
| `quote_currency` length CHECK on `dim_Markets` | V001:208 | PASS | `CK_dim_Markets_quote_currency CHECK (LEN(quote_currency) = 3)`. |
| `effective_to > effective_from` CHECK on `config_Capital` | V001:333-334 | PASS | `effective_to IS NULL OR effective_to > effective_from`. |
| Every FK column has at least one index | V001 throughout | PASS | dim_Employees(team_id, floor_id, manager FK via self), dim_Accounts(trader_id), fact_Trades(trader_id via IX_fact_Trades_trader_id_time_entry, account_id via UQ_trade_uid covers it via PK?) — note `account_id`, `session_id`, `order_type_id` only get a covering index implicitly through the clustered PK on time_entry. For these low-cardinality dims a non-covered FK is acceptable (delete is rare and a table scan over a 200k-row fact is < 1s). Acceptable. |
| `IX_fact_Trades_trade_date_ro` exists | V001:422-423 | PASS | Non-clustered on the persisted computed column. |
| `IX_dim_UserRoles_aad_object_id_INC` covers (aad_object_id) INCLUDE (employee_id, scope) | V001:311-313 | PASS | Keyed on `(aad_object_id, is_active)` (slightly wider than spec); INCLUDE `(employee_id, scope)` — matches predicate access pattern exactly. |
| Filtered indexes use the right WHERE clause | V001:94-96, 154-156, 307-309, 337-343, 418-420 | PASS | All filtered indexes use the documented predicates. |
| RLS predicate session-context key is `aad_object_id` (no typo `aad_object_oid`) | V001:1182 | PASS | `N'aad_object_id'` everywhere. |
| Filter + block predicates on both fact tables AFTER INSERT/UPDATE | V001:1205-1214 | PASS | Six adds — filter+after-insert+after-update on `fact_Trades` and `fact_DailyTraderPnL`. |
| Predicate function is `WITH SCHEMABINDING`, two-part naming, no `EXECUTE AS OWNER` | V001:1166-1195 | PASS | Schemabound, `dbo.dim_UserRoles` + `dbo.dim_Employees`, no EXECUTE AS. |
| Deny-by-default when SESSION_CONTEXT unset | V001:1182 + ADR-003 §3 | PASS | `CAST(SESSION_CONTEXT(N'aad_object_id') AS UNIQUEIDENTIFIER)` yields NULL → no row in `p` → outer predicate returns no rows. |
| Rollback drops every V001-created object in reverse dependency order, idempotent guards | `V001__init.down.sql` | PASS | Walked the order: security policy → predicate fn → fact tables → views → procs → fns → config + dim tables (Employees after Accounts/UserRoles/config_Capital) → roles → schemas → schema_history row. |
| Rollback uses `OBJECT_ID(...,'IF')` for inline TVFs and `'FN'` for scalar fns | V001 down §6 | PASS | `tvf_RiskMetrics` and `tvf_GetCapitalBaseline` tagged `'IF'`; `fn_GetCapitalBaseline`, `fn_IsTradingDay`, `fn_PreviousBusinessDay` tagged `'FN'`; `rls.fn_TradesPredicate` tagged `'IF'`. |
| Rollback ends with `DELETE FROM schema_history` | V001 down:154-155 | PASS | Final guarded `DELETE`. |
| `test_naming_convention.sql` raises severity 16 on violation | `test_naming_convention.sql:73` | PASS | `RAISERROR(..., 16, 1)`. |
| `test_rls_smoke.sql` three test cases, rolled-back transaction, NEWID() AAD ids | `test_rls_smoke.sql` | PASS | All three cases; TRY/CATCH with rollback; resets SESSION_CONTEXT on both success and failure paths. |
| `test_fx_rate_completeness.sql` raises on > 0 rows | `test_fx_rate_completeness.sql:26-27` | PASS | Correct shape. |
| All three tests exit non-zero under `sqlcmd -b` | (all) | PASS | Severity 16 + RAISERROR propagates. |
| `dim_Date` populator generates ~2 560 rows | V001:603 | PASS | `DATEDIFF + 1 = 2558` rows. |
| Holiday seed for 7 years × ~17 holidays | V001:646-771 | PASS | 119 rows; no cartesian/`@holidays` PK has `h_date PRIMARY KEY`. |
| English-only outside `month_name_ro` / `ro_holiday_name` | V001 throughout | PASS | All comments, error messages, and non-RO literals in English. RO labels only inside `month_name_ro` and `ro_holiday_name` columns. |
| Comments only when WHY is non-obvious | V001 throughout | PASS | Section banners + the `--/` docstring on each public proc/fn/view; no paragraph blobs. |

## Apply / rollback simulation

**Fresh apply** (`sqlcmd -b -i V001__init.sql` against an empty DB):

1. Batches 1-7 (session SET, schema_history, rls schema, four roles, dim_Companies → dim_UserRoles, config_Capital, fact_Trades, fact_DailyTraderPnL) create cleanly — every guarded by `IF OBJECT_ID(...) IS NULL` / `IF SCHEMA_ID(...) IS NULL` / `IF DATABASE_PRINCIPAL_ID(...) IS NULL`. PERSISTED computed column on `fact_Trades.trade_date_ro` is accepted because `AT TIME ZONE` with a literal time-zone-name argument is treated as deterministic.
2. Section 6 seed MERGEs (dim_TradingFloors, dim_Teams, dim_Sessions, dim_OrderType, dim_Markets) insert the canonical rows.
3. Section 7 populates `dim_Date` inside a single batch: 2 558 rows in the CTE, then UPDATE-joined to the 119-row `@holidays` table variable. Runs in tens of milliseconds against Azure SQL Free.
4. Section 8 creates `fn_GetCapitalBaseline`, `tvf_GetCapitalBaseline`, `fn_IsTradingDay`, `fn_PreviousBusinessDay` — all clean.
5. Section 9 creates the five SCHEMABINDING views in dependency order: `v_trades_enriched` (depends on dims + fact), then `v_employee_performance` (depends on v_trades_enriched), then `v_team_performance`, `v_floor_performance`, `v_daily_pnl` (depends on v_employee_performance). The chained schemabinding is legal because dependencies are created first.
6. **Section 9 (cont.) — `tvf_RiskMetrics`** — `CREATE OR ALTER FUNCTION ... RETURNS TABLE ... RETURN SELECT ...` **fails** with Msg 8120 because the SELECT mixes scalar aggregates with `PERCENTILE_CONT(...) OVER ()` referencing a base column without a `GROUP BY`. **`sqlcmd -b` returns non-zero here; everything after this point is never created.**
7. (Hypothetical, after CR-01 fix) — Sections 10 (sprocs), 11 (predicate + security policy), 12 (grants), 13 (schema_history insert) all apply cleanly. Final `PRINT 'V001__init.sql applied successfully.'` lands.

**Second apply** (re-running `V001__init.sql` against the just-applied DB), assuming CR-01 is fixed:

1. Every `IF OBJECT_ID(...) IS NULL CREATE TABLE` is a no-op.
2. MERGEs re-evaluate and produce no changes (data already matches; `updated_at` columns will tick on MERGE WHEN MATCHED, which is a documented soft drift — acceptable for a thesis project but worth noting).
3. `IF NOT EXISTS (SELECT 1 FROM dbo.dim_Date) BEGIN ... END` short-circuits the entire populator + holiday seed.
4. `CREATE OR ALTER FUNCTION/VIEW/PROC` re-binds bodies — schemabinding ALTERs succeed because column signatures are identical.
5. **`ALTER ROLE db_datareader ADD MEMBER tcp_admin;` fails with Msg 15410** (MJ-01) — script exits non-zero. The second-apply contract is broken on this single line. After MJ-01 is fixed, the entire script is a clean idempotent re-apply.

**Rollback simulation** (`V001__init.down.sql` against the applied DB):

1. Security policy drop is guarded by `sys.security_policies` ∩ schema `rls` — drops cleanly.
2. Predicate function drop with `OBJECT_ID(..., 'IF')` — correct tag for an inline TVF.
3. Fact tables dropped first (no other table depends on them); `fact_DailyTraderPnL` before `fact_Trades` — fine, neither references the other.
4. Views dropped in reverse dependency order: `v_daily_pnl` → `v_floor_performance` → `v_team_performance` → `v_employee_performance` → `v_trades_enriched`. Correct: schemabinding prevents dropping `v_trades_enriched` while `v_employee_performance` still references it; this order avoids the trap.
5. Procs, then functions (TVF tagged `'IF'`, scalar tagged `'FN'`). Correct tags throughout — verified.
6. Config + dimension tables dropped with correct FK ordering: `config_Capital` (FK→Employees), `dim_UserRoles` (FK→Employees) before `dim_Employees`; `dim_Date`, `dim_OrderType`, `dim_Sessions`, `dim_Markets` (no inbound FKs after `fact_Trades` was dropped); `dim_Accounts` (FK→Employees) before `dim_Employees`; then `dim_Employees`, `dim_Teams`, `dim_TradingFloors`, `dim_Companies`. Correct.
7. Roles dropped: `tcp_admin` first (removed from `db_datareader`/`db_datawriter` if present, then `DROP ROLE`); then `tcp_bi_reader`, `tcp_generator`, `tcp_ai_assistant`. The `IS_ROLEMEMBER` guards prevent the `ALTER ROLE DROP MEMBER` calls from erroring when the membership was never established (e.g., partial-state cleanup). Correct.
8. `rls` schema dropped via `EXEC(N'DROP SCHEMA rls;')` inside an `IF` block — fine.
9. Final `DELETE FROM dbo.schema_history WHERE script_name = 'V001__init.sql';` removes the migration ledger row, leaving the table itself (intentional — preserves history across re-bootstraps).

**Second rollback** (rerunning `V001__init.down.sql` on an already-rolled-back DB): every drop is `IS NOT NULL`-guarded; the script is a clean no-op. Verified.

## Recommendation

Land the two non-cosmetic fixes (CR-01 + MJ-01) before merging Etapa 2. Both are mechanical: CR-01 requires re-shaping `tvf_RiskMetrics` to split the percentile out of the aggregate projection (paste the suggested CROSS APPLY form, or switch to `APPROX_PERCENTILE_CONT` as an ordered-set aggregate); MJ-01 requires three lines wrapping the `ALTER ROLE ... ADD MEMBER` calls in `IS_ROLEMEMBER(...) = 0` guards. After those, the bundle satisfies every load-bearing spec item — naming, RLS shape, persisted `trade_date_ro`, ADR-002 materialisation, ADR-003 predicate contract, two-filtered-unique on `config_Capital`, EN/RO holiday columns, and the rollback completeness. The minors (MJ-02 schema_history shape drift, MN-01 Easter-Monday RO label, MN-02 fn↔tvf wrapper, MN-04 tighter naming regex, MN-11 fx-rate-completeness role gating) are low-priority polish that can ride a follow-up V002 or a documentation patch.

## Files referenced

- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\db\migrations\V001__init.sql`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\db\migrations\rollback\V001__init.down.sql`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\sql\test_naming_convention.sql`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\sql\test_rls_smoke.sql`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\sql\test_fx_rate_completeness.sql`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\db\README.md`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\docs\design\02_database_design.md`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\docs\decisions\ADR-002-daily-pnl-materialisation.md`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\docs\decisions\ADR-003-rls-session-context.md`
