# DB design review — pass 1

**Reviewer**: database-architect
**Date**: 2026-05-15
**Verdict**: ACCEPT_WITH_CHANGES

## Summary

The design is fundamentally sound: a clean light star schema, correct prefix naming convention, well-justified clustered key, sensible RLS posture, and a thoughtful effective-dated `config_Capital`. Strong on contracts, performance budget, and migration discipline. **The biggest risk** is a small cluster of *real* defects in the V001 DDL bundle that will block a clean apply in Etapa 2: a column mis-mapping in the `dim_Date` populator, a duplicate-key day in the 2026 holiday seed, incomplete `iso_year` derivation, missing rows for `Ziua Copilului` from 2027 onward, and several cross-document inconsistencies (ERD shows a `trade_date` FK that does not exist in `fact_Trades`; `tcp_bi_reader` is named in the architecture but never created here; `tcp_generator` is missing `SELECT` on `config_Capital`). The RLS predicate also runs under ownership chaining that needs at least one explicit `GRANT` to keep `tcp_ai_assistant` working when the security policy fires. Nothing structural needs to be re-designed; everything below is a concrete fix.

## Critical (blocks Etapa 2)

- [ ] **CR-01** | §15 (V001) line 1597-1617, `dim_Date` populator | The INSERT lists 14 target columns starting with `iso_year, iso_week, ...`, but the SELECT list emits `iso_week_placeholder` (aliased) followed by `iso_week` — there is no expression that computes the ISO **year**. With this DDL, `iso_year` will be populated with the ISO **week** number, not the ISO year, and ISO-year-bucketed reports will silently produce garbage. | Many KPIs slice by ISO week; reports straddling Jan-1 (e.g., 2024-W01 = the week containing the first Thursday) need an accurate `iso_year` to disambiguate, otherwise 2024-W01 rows that fall in calendar 2023 will be misattributed. | Replace the placeholder expression with a real ISO-year derivation and drop the duplicate column from the SELECT:
  ```sql
  -- iso_year per ISO-8601 (the year of the Thursday in the same week)
  DATEPART(YEAR, DATEADD(DAY, 26 - DATEPART(ISO_WEEK, d), d)) AS iso_year,
  DATEPART(ISO_WEEK, d)                                       AS iso_week,
  ```
  and make sure the column count matches the INSERT target.

- [ ] **CR-02** | §15 (V001) line 1652-1653, 2026 holiday seed | `2026-06-01` is inserted twice — once as `Ziua Copilului` and once as `Rusalii`. The subsequent `UPDATE d ... JOIN @holidays h ON h.h_date = d.calendar_date` will perform a non-deterministic multi-row update (SQL Server will pick one arbitrarily, or in strict mode raise *"The query processor could not produce a query plan because of the hints defined in this query."* via the multi-row target). Also, the calendar for **Pentecost 2026** is wrong: Orthodox Pentecost 2026 is **Sunday 31 May / Monday 1 June** (this is why the duplicate happens). | The populator silently picks one of the two names; if it picks `Ziua Copilului`, the platform will treat 1 Jun 2026 as a fixed-date holiday but `Rusalii` (Pentecost Monday) will be missing for the year, breaking `fn_IsTradingDay` for 2026-06-01. | Either (a) collapse the row to a single `('2026-06-01', N'Rusalii / Ziua Copilului')` line, or (b) keep the date once and pick the legally-defined name. Add a `PRIMARY KEY (h_date)` to the `@holidays` table variable so this kind of collision raises at insert time instead of corrupting data:
  ```sql
  DECLARE @holidays TABLE (h_date DATE PRIMARY KEY, h_name NVARCHAR(80));
  ```

- [ ] **CR-03** | §15 (V001) line 1658-1689, 2027-2030 holiday seed | `Ziua Copilului` (1 June, a statutory non-working day in Romania since 2017) is **missing from 2027, 2028, 2029 and 2030**. `Bobotează` (6 Jan) and `Sfântul Ion` (7 Jan) are present for 2025 and 2026 but missing for 2024, 2027, 2028, 2029, 2030. `Unirea Principatelor` (24 Jan) is missing for 2027-2030. | The cron generator calls `fn_IsTradingDay` to gate insertion; on a missing holiday, the generator will produce trades on a public holiday, contaminating the synthetic dataset and breaking the demo runbook claim "no trades are generated on Romanian public holidays". | Audit the seed against the official Romanian non-working-day list per year (`Codul Muncii`, art. 139) and complete the missing rows for 2024-2030. Recommend extracting the list into a CSV at `db/seed/ro_holidays_2024_2030.csv` and loading via `BULK INSERT`, so the list is reviewable in one place and easier to amend.

- [ ] **CR-04** | `docs/diagrams/erd.mmd` line 15 vs. §4.1 `fact_Trades` schema | ERD declares `DIM_DATE ||--o{ FACT_TRADES : "trade_date"`, but `fact_Trades` has **no** `trade_date` (or `date_id`) column. The relationship is fabricated. | Anyone implementing dim-table joins from the ERD will assume a direct FK to `dim_Date` and write incorrect DAX/SQL. PowerBI's relationship engine will also reject it because no physical FK exists. | Choose one of two corrections: (a) drop the relationship from the ERD and rely on the derived `trade_date_ro` computed in `v_trades_enriched`, or (b) add a persisted computed column `trade_date_ro AS CAST(time_entry AT TIME ZONE 'E. Europe Standard Time' AS DATE) PERSISTED` plus a FK to `dim_Date(calendar_date)`. Option (b) is preferable for PowerBI because it gives the model a real relationship to the date dimension at the cost of one extra index. If you choose (b), add `IX_fact_Trades_trade_date_ro` and update §4.1.

- [ ] **CR-05** | §10.2 line 990-1001, `tcp_generator` role grants | `tcp_generator` has `SELECT` on every dimension *except* `dbo.config_Capital`. The synthetic generator must read each trader's effective capital baseline to size positions correctly (capital × utilisation = notional). Without the grant the generator can either (a) hardcode 80 000 EUR (defeats `OQ-10` and the entire effective-dated configuration design) or (b) fail at runtime when calling `fn_GetCapitalBaseline`, which itself reads `config_Capital`. | Generator breaks on first run after Etapa 5 introduces per-trader overrides; until then, the issue is latent but ships. | Add to §10.2 and the V001 grants block:
  ```sql
  GRANT SELECT  ON dbo.config_Capital     TO tcp_generator;
  GRANT EXECUTE ON dbo.fn_GetCapitalBaseline TO tcp_generator;
  ```

- [ ] **CR-06** | §9 (RLS) line 917-944 and §10.2 grants for `tcp_ai_assistant` | The RLS predicate function `rls.fn_TradesPredicate` reads `dbo.dim_UserRoles` and `dbo.dim_Employees`. `tcp_ai_assistant` is granted `SELECT` on the `v_*` views but **not** on the underlying dim tables. Ownership chaining will bridge the views' joins because the views and tables share `dbo` ownership, but the **security predicate** does not benefit from ownership chaining the same way: it is evaluated as part of the query plan over `fact_Trades`, and when the calling principal has no direct `SELECT` on the predicate's referenced tables, SQL Server returns 0 rows from the predicate (silent denial). | Every assistant query will return an empty result set as soon as RLS is enabled — there is no error, just empty answers, which is the worst possible failure mode for a demo. | Either (a) sign the predicate function with a certificate that has implicit access (heavy), or (b) the safer path: explicitly grant `SELECT` on the two tables to `tcp_ai_assistant` and to `tcp_generator` (which also needs to insert rows that pass the block predicate):
  ```sql
  GRANT SELECT ON dbo.dim_UserRoles TO tcp_ai_assistant, tcp_generator;
  GRANT SELECT ON dbo.dim_Employees TO tcp_ai_assistant; -- tcp_generator already has this
  ```
  Add a smoke test in CI that issues a representative `SELECT` against `v_trades_enriched` with `SESSION_CONTEXT` set to a seeded test AAD object id and asserts the rowcount matches the expected scope.

- [ ] **CR-07** | §15 (V001) line 1891-1931, `usp_GenerateDailyTrades` | The procedure body has no INSERT (it is "intentionally minimal"), returns `@inserted = 0`, but logs success. The architecture's `cd.yml` smoke test (§9.2 of `03_architecture.md`) asserts "≥ 100 rows inserted into `fact_Trades`" — that assertion will fail until Etapa 5 ships `V002__synth_tvp.sql`, but Etapa 2 may merge to `main` and the CD pipeline goes red. Separately, the THROW 50003 path inside the `IF EXISTS (...)` branch **does** rollback then raise, contradicting the "idempotent no-op" claim in §7.1 contract docstring — the worker will see an exception rather than a graceful "0 rows inserted because already done" return. | Confusing contract; rollback path is logically inconsistent with "idempotent" wording; tests will be wrong. | Two fixes:
  1. Change the duplicate-day branch to a no-op return, not a throw:
     ```sql
     IF EXISTS (SELECT 1 FROM dbo.fact_Trades WHERE ...) 
     BEGIN
         ROLLBACK; -- nothing to roll back; just close the txn
         SELECT 0 AS rows_inserted; -- truly idempotent
         RETURN;
     END;
     ```
  2. Mark the smoke test in `cd.yml` to skip until `V002` ships (gate on schema_history version), or move the smoke test from Etapa 2 to Etapa 5 explicitly in the acceptance checklist.

## Major (should fix before Etapa 2 starts coding)

- [ ] **MJ-01** | §5.1 `config_Capital` | The filtered unique index `UX_config_Capital_global_current` only enforces uniqueness on `(trader_id, effective_from) WHERE trader_id IS NULL` — i.e., only **global** rows are protected from duplicate effective_from. **Per-trader rows can overlap arbitrarily**: two rows for trader 5 with `effective_from='2024-01-01'`, `effective_to=NULL` are both legal, so `fn_GetCapitalBaseline` returns one of them non-deterministically. | Per-trader capital baselines (the whole point of the override mechanism) become unreliable; KPI-TR-020 (ROC) silently inconsistent across runs. | Add a second filtered unique index plus a trigger or check for range overlap:
  ```sql
  CREATE UNIQUE INDEX UX_config_Capital_trader_current
      ON dbo.config_Capital(trader_id, effective_from)
      WHERE trader_id IS NOT NULL;
  ```
  For full range-overlap prevention (which a unique index cannot do), document a stored proc `usp_UpsertCapital(@trader_id, @amount, @effective_from)` that closes the previous open row (`effective_to = @effective_from`) and inserts the new one in a single transaction.

- [ ] **MJ-02** | §8.1 `fn_GetCapitalBaseline` | The scalar UDF is the documented lookup path, but scalar UDFs in T-SQL are a well-known optimizer block (no inlining unless declared `INLINE = ON` and shape-eligible). Under SQL Server 2019+/Azure SQL the Scalar UDF Inlining feature *may* apply, but the `TOP 1 ... ORDER BY ... CASE WHEN ...` makes it ineligible. Every `WHERE fn_GetCapitalBaseline(...) >= X` predicate will run row-by-row. | Capital-derived KPIs (ROC) become the slowest queries in the assistant, and the `usp_GetEmployeePerformance` path may breach the 800 ms cold budget in §13. | Recommend converting to an inline table-valued function (iTVF) and using `OUTER APPLY`:
  ```sql
  CREATE OR ALTER FUNCTION dbo.tvf_GetCapitalBaseline (@trader_id INT, @as_of DATETIMEOFFSET(3))
  RETURNS TABLE WITH SCHEMABINDING AS
  RETURN
      SELECT TOP 1 c.amount_eur
      FROM dbo.config_Capital AS c
      WHERE (c.trader_id = @trader_id OR c.trader_id IS NULL)
        AND c.effective_from <= @as_of
        AND (c.effective_to IS NULL OR c.effective_to > @as_of)
      ORDER BY CASE WHEN c.trader_id = @trader_id THEN 0 ELSE 1 END,
               c.effective_from DESC;
  ```
  Keep the scalar as a thin wrapper if you need the scalar shape elsewhere, but make the iTVF the canonical join path.

- [ ] **MJ-03** | §6.5 `v_daily_pnl` | View aggregates from `v_employee_performance`, which itself aggregates from `v_trades_enriched`, which is an 8-table join. Each query against `v_daily_pnl` re-runs the full stack. The performance budget in §13 quotes 200 ms warm for the leaderboard, but does **not** budget for `v_daily_pnl` (used by every Sharpe/Sortino/drawdown calc). | All Risk KPIs in `01_BR §4.4` ultimately depend on `v_daily_pnl`; if a single user opens the "Trader Detail" page with five risk visuals, that's 5× full-stack scans. | Either (a) add a materialised summary table refreshed by the generator's transaction (e.g., `fact_DailyTraderPnL`), or (b) drop one layer by having `v_daily_pnl` source directly from `fact_Trades` and `dim_Employees` instead of stacking on `v_employee_performance`. Document the chosen path as ADR-002. (Indexed views are *not* an option here because `AT TIME ZONE` is non-deterministic.)

- [ ] **MJ-04** | §6 (views), all five `v_*` views | None of the views are `WITH SCHEMABINDING`. As a result they cannot back indexed views in future, do not pin column types against underlying changes, and break silently if a column is renamed under them. | Loss of refactoring safety; PowerBI's DirectQuery on a non-schemabound view will not benefit from any future indexed-view materialisation. | Add `WITH SCHEMABINDING` to all five views. Note that this requires two-part naming (`dbo.fact_Trades` — already done), no `SELECT *`, no `AT TIME ZONE` for indexed views (already a blocker), and disallows `WHERE is_open = 0` style filters on a view that needs to be indexed — but for SCHEMABINDING alone (not indexed), all of these queries qualify after a `WITH SCHEMABINDING` clause is added.

- [ ] **MJ-05** | §9 (RLS) line 947-950, `SESSION_CONTEXT` default behaviour | The function uses `CAST(SESSION_CONTEXT(N'aad_object_id') AS UNIQUEIDENTIFIER)` but never handles the case where `SESSION_CONTEXT` was not set. When unset, `SESSION_CONTEXT(...)` returns NULL, the CAST returns NULL, and the predicate `ur.aad_object_id = NULL` is never true → the predicate excludes **all** rows. That is safe (deny-by-default), but: (a) the architecture's bootstrap admin path needs an escape hatch, and (b) when a connection is pooled and the prior session's value is sticky, you can leak rows across users. | (a) PowerBI service-principal connections (which won't set SESSION_CONTEXT) will see zero rows unless the principal is granted `admin` scope, which the design does not state. (b) Connection pool stickiness is the #1 cause of cross-tenant RLS leaks in production. | Two changes: (i) `sp_set_session_context @key=N'aad_object_id', @value=NULL, @read_only=0` at the start of every checked-out connection (the worker is responsible), and document this as a hard requirement in `tcp/db.py`. (ii) State explicitly in §9.1 that connections **without** `SESSION_CONTEXT` set see zero rows by design, and add an `admin` row to `dim_UserRoles` for the PowerBI service principal at deploy time.

- [ ] **MJ-06** | §9.2 predicate function performance | `rls.fn_TradesPredicate(@trader_id_in_row)` is invoked **once per row** of `fact_Trades`. Inside it: a `LEFT JOIN dim_UserRoles → dim_Employees`, plus two `EXISTS` subqueries that re-scan `dim_Employees` again. The optimizer cannot pre-resolve the user's scope once-per-query because `SESSION_CONTEXT` is a runtime value. Without an explicit index on `dim_UserRoles(aad_object_id, is_active)` covering `employee_id, scope`, every row hits the predicate with two scans of `dim_Employees`. | The current `IX_dim_Employees_team_id_role` / `IX_dim_Employees_floor_id_role` cover the inner EXISTS, but the lookup on `e.employee_id = ur.employee_id` (clustered, fine) plus `t.employee_id = @trader_id_in_row` (clustered, fine) is two index probes per row. At 360k rows over six years, the predicate alone is ~720k probes per full scan. | (i) Wrap the user's scope resolution in a one-shot CROSS APPLY at the top:
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
  (ii) Add `INCLUDE (employee_id, scope)` to `UQ_dim_UserRoles_aad_object_id` so the seek is index-only.

- [ ] **MJ-07** | §10 vs `03_architecture.md` §5, `tcp_bi_reader` role | The architecture doc (§5 row 9) maps the PowerBI Service Principal to a DB role called **`tcp_bi_reader`**. That role is never defined in `02_database_design.md`. Etapa 7 PowerBI deployment will fail at first connection. | Documents diverge; PowerBI on-boarding step in Etapa 7 is undefined; the RBAC matrix is unimplementable as-written. | Add `tcp_bi_reader` to §10.2:
  ```sql
  IF DATABASE_PRINCIPAL_ID('tcp_bi_reader') IS NULL
      CREATE ROLE tcp_bi_reader AUTHORIZATION dbo;
  GRANT SELECT ON dbo.v_trades_enriched      TO tcp_bi_reader;
  GRANT SELECT ON dbo.v_employee_performance TO tcp_bi_reader;
  GRANT SELECT ON dbo.v_team_performance     TO tcp_bi_reader;
  GRANT SELECT ON dbo.v_floor_performance    TO tcp_bi_reader;
  GRANT SELECT ON dbo.v_daily_pnl            TO tcp_bi_reader;
  -- Plus SELECT on dim_* for PowerBI relationships (it cannot use views as dim tables effectively)
  GRANT SELECT ON dbo.dim_Employees, dbo.dim_Teams, dbo.dim_TradingFloors, dbo.dim_Markets,
                  dbo.dim_Sessions, dbo.dim_OrderType, dbo.dim_Date TO tcp_bi_reader;
  ```
  Also add an `admin`-scope `dim_UserRoles` row for the PowerBI SP (see MJ-05).

- [ ] **MJ-08** | KPI coverage for Risk family (`01_BR §4.4`) and `02_DB §6` views | Sharpe, Sortino, max drawdown, VaR-95 all require a **daily PnL series**. `v_daily_pnl` provides per-employee daily cumulative PnL, which is enough for max drawdown. **But**: (a) Sortino requires the standard deviation of *negative* daily PnL only, with NULL handling when zero losing days exist — no view exposes that. (b) VaR-95 requires `PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY daily_net_pnl_total)` over the period — no view exposes that as a window. (c) `MEAN(daily_net_pnl)` and `STDEV(daily_net_pnl)` are not exposed anywhere. | All four headline risk KPIs are not computable from the documented views; the AI assistant will have to inline these calculations, which makes prompt-template engineering harder and `safe_query.py`'s allowlist larger. | Add a `v_risk_metrics` view at the period level (parameterised via a TVF):
  ```sql
  CREATE OR ALTER FUNCTION dbo.tvf_RiskMetrics
      (@employee_id INT, @from DATE, @to DATE)
  RETURNS TABLE WITH SCHEMABINDING AS
  RETURN
      WITH d AS (
          SELECT trade_date_ro, net_pnl_eur_total
          FROM dbo.v_employee_performance
          WHERE employee_id = @employee_id
            AND trade_date_ro BETWEEN @from AND @to
      )
      SELECT
          COUNT(*)                                                                    AS trading_days,
          AVG(net_pnl_eur_total)                                                       AS mean_daily_pnl,
          STDEV(net_pnl_eur_total)                                                     AS stdev_daily_pnl,
          STDEV(CASE WHEN net_pnl_eur_total < 0 THEN net_pnl_eur_total END)            AS stdev_downside,
          PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY net_pnl_eur_total) OVER ()      AS var_95,
          SUM(net_pnl_eur_total)                                                       AS total_net_pnl
      FROM d;
  ```
  Document the consumers in `01_BR §4.4` notes.

- [ ] **MJ-09** | `01_BR §7.3` and `02_DB §9` | `01_BR §7.3` claims the AI assistant uses **`db_datareader`** as its SQL role (NFR-SEC-05); `02_DB §10.2` defines a much narrower **`tcp_ai_assistant`** role with grants only on views and two procs. The two documents pick different security models. | If Etapa 5 implementer reads `01_BR` first they'll add the assistant principal to `db_datareader`, which silently gives it read access to `dim_Employees` raw (PII bypass) and `config_Capital` (salary-adjacent baselines). | Reconcile to **one** authoritative statement. The narrower `tcp_ai_assistant` is the correct posture; update `01_BR §7.3` and NFR-SEC-05 to reference `tcp_ai_assistant`, not `db_datareader`.

- [ ] **MJ-10** | §3.6 `dim_Markets` seed vs `01_BR §5.5` | `dim_Markets` seed has `base_currency` = `'USD'` for nearly all rows (`AAPL`/`MSFT`/.../`BTCUSD`/`XAUUSD`). `01_BR §5.5` states "all instruments are denominated in EUR in v1.0" and lists `currency` as an attribute on the market dimension. The DB model is internally consistent (PnL is converted to EUR at exit time per §4.1 commentary), but the business doc claims otherwise. | Cross-doc inconsistency; future Etapa 3 implementers building the generator will read `01_BR` and hardcode EUR everywhere, breaking the FX-conversion path. | Update `01_BR §5.5` to say "instrument quote currencies vary (USD/JPY/CHF/etc.); the generator converts realised PnL to EUR using a deterministic rate table maintained in the Python layer, and persists `gross_pnl_eur` / `net_pnl_eur`". Cross-link to `02_DB §4.1 "Why gross_pnl_eur and net_pnl_eur are stored"`.

- [ ] **MJ-11** | `01_BR §5.6` `dim_OrderType.is_directional` attribute | `01_BR §5.6` claims `is_directional` is an attribute of `dim_OrderType`. `02_DB §3.8` does not define this column. KPI-TR-063 (slippage) doesn't reference it. | Dead attribute; PowerBI modeller will look for it and not find it; minor confusion. | Either add the column (`is_directional BIT NOT NULL DEFAULT 1` and seed values market/limit/stop/stop_limit all = 1), or remove the reference from `01_BR §5.6`. The column is harmless and tiny, so adding it is cheapest.

- [ ] **MJ-12** | `01_BR §5.4` `dim_TradingFloors.floor_name` and `is_primary` | `01_BR §5.4` references `floor_name` and `is_primary` columns. `02_DB §3.2` defines `city` and `is_primary_hq`. | Cross-doc inconsistency; PowerBI semantic-model design (Etapa 8) will pick the wrong column names. | Update `01_BR §5.4` to reference `city` (display label) and `is_primary_hq`. No DDL change needed.

- [ ] **MJ-13** | `01_BR §5.3` `dim_Teams.team_lead_employee_id` | `01_BR §5.3` lists `team_lead_employee_id` as an attribute. `02_DB §3.3` does not define this column; instead the relationship is `dim_Employees.team_id = @T AND role = 'team_lead'`. | Cross-doc inconsistency; KPIs about team-lead behaviour (KPI-LR-001/002/004) become unclear at the DDL level. | Two options: (a) add the column to `dim_Teams` (`team_lead_employee_id INT NULL`, FK to `dim_Employees`) — but introduces a circular FK that complicates the seed order; or (b) keep the inferred relationship and update `01_BR §5.3` to say "team-lead resolved via `dim_Employees WHERE team_id = X AND role = 'team_lead'`". Recommend (b).

- [ ] **MJ-14** | §15 (V001) `dim_Date` populator, `day_of_week` derivation | The formula `((DATEPART(WEEKDAY, d) + @@DATEFIRST - 2) % 7) + 1` is correct **only if** `@@DATEFIRST` is the SQL Server default (7 = Sunday) at the time of execution. If a connection inherits a non-default `DATEFIRST` from a session-init script (some pyodbc drivers set 1 on Connect), the formula returns shifted values. | Latent bug; only fires if someone changes `DATEFIRST` upstream, which is realistic for cross-platform ORMs. | Add an explicit `SET DATEFIRST 1;` at the top of the populator block (which is what ISO requires anyway), and simplify the expression to:
  ```sql
  -- with DATEFIRST 1, DATEPART(WEEKDAY, d) returns 1=Mon..7=Sun directly
  DATEPART(WEEKDAY, d) AS day_of_week,
  ```

- [ ] **MJ-15** | §15 (V001) line 1467 `CK_fact_Trades_trade_uid_format` | The CHECK constraint enforces 14 characters via `LIKE 'T[0-9]{8}-[0-9]{4}'` pattern, which is correct, but the column is declared `VARCHAR(14)` so any 14-character string passes the length test before the regex check fires. **The regex does not require `T<YYYYMMDD>`** to be a *real* date — `T20260230-0001` (30 Feb) passes. | Synthetic data is generated by the Python worker which will never produce invalid dates, but the constraint should refuse them defensively because the AI assistant could in principle issue a write under an admin scope (RLS won't help if the user is `admin`). | Add a stricter check via `TRY_CONVERT`:
  ```sql
  CONSTRAINT CK_fact_Trades_trade_uid_date_valid CHECK (
      TRY_CONVERT(DATE, SUBSTRING(trade_uid, 2, 8), 112) IS NOT NULL
  )
  ```
  (112 = `yyyymmdd` style.)

- [ ] **MJ-16** | §10.1 bootstrap path, AAD-only enforcement | The bootstrap path drops the SQL admin's `CONNECT` on the **user** database, but does not say *when* `administrators.azureADOnlyAuthentication = true` flips on the **server**. Architecture §8.2 says "AAD-only after bootstrap" but does not say what triggers the flip in the deploy pipeline. | Risk: the SQL auth admin login persists at server level indefinitely, providing a credential-theft surface in violation of NFR-SEC-01. | Document in §10.1 a numbered post-deploy step: "After the first successful schema apply, the post-provision hook (`infra/scripts/postprovision.ps1`) sets `Set-AzSqlServerActiveDirectoryOnlyAuthentication -ServerName ... -Enable $true`, then deletes the `SQL-ADMIN-PASSWORD-BOOTSTRAP` Key Vault secret. CI verifies the secret is absent before marking the deploy green." Tie this to the Etapa 4 acceptance checklist.

- [ ] **MJ-17** | §6.1 `v_trades_enriched`, RLS path | The view is a non-schemabound 8-table join over `fact_Trades`. RLS filters `fact_Trades` directly via the security policy; the predicate fires before the joins. **However**, the LEFT JOIN to `dim_Employees` inside the RLS predicate function uses the *outer* `aad_object_id`, but the joins inside `v_trades_enriched` use `e.employee_id = f.trader_id` (a different employee — the *trader*, not the principal). For a Team Lead with scope `team_lead`, the predicate must check whether the *trader's* team matches the *principal's* team. The current function does this correctly, but it is non-obvious and the inner `EXISTS` re-queries `dim_Employees` for the trader — that's a third scan of the same table per row. | Performance, see MJ-06. Also clarity: the two roles of `dim_Employees` (principal vs trader) need to be spelt out in §9.2 prose. | Annotate the function code with comments (`-- p = principal row`, `-- t = trader row of fact row`) and rename the join aliases so it is unambiguous on first read.

## Minor / nits

- [ ] **MN-01** | §3.4 `dim_Employees` column name `role` | The column is wrapped in brackets `[role]` because of the keyword collision. The `01_BR §5.2` and `5.8` documents use the unbracketed name in formulas. Both work, but bracket usage should be consistent in the docs. | Inconsistent quoting in derived SQL snippets across the team. | Either rename to `employee_role` (preferred — avoids the keyword entirely and self-documents) or commit to `[role]` throughout the docs. Recommend the rename; the schema is not yet applied.

- [ ] **MN-02** | §3.10 `dim_UserRoles.aad_object_id` uniqueness | Column is `UNIQUEIDENTIFIER NOT NULL` with `UQ_dim_UserRoles_aad_object_id UNIQUE` — but the design also wants soft-revoke via `is_active = 0`. With a hard unique constraint, you cannot have an old revoked row and a new active row for the same principal. | Re-onboarding the same principal requires deleting the prior row (audit trail loss) or extending the unique constraint to `(aad_object_id, is_active)` with a filter. | Convert to filtered unique:
  ```sql
  CREATE UNIQUE INDEX UX_dim_UserRoles_aad_object_id_active
      ON dbo.dim_UserRoles(aad_object_id)
      WHERE is_active = 1;
  ```
  Drop `UQ_dim_UserRoles_aad_object_id`. Preserve the constraint name in the docs.

- [ ] **MN-03** | §3.4 `dim_Employees.email` CHECK | The CHECK `email LIKE '%@tcp-capital.ro'` is too permissive: `evil@nottcp-capital.ro@tcp-capital.ro` passes. | Defence in depth for synthetic-data sanity, not a real attack surface. | Tighten to `email LIKE '_%@tcp-capital.ro' AND email NOT LIKE '%@%@%'`, or simpler — store the local-part and join the suffix client-side. Not blocking; tighten if convenient.

- [ ] **MN-04** | §4.1 `fact_Trades` storage of `gross_pnl_eur`/`net_pnl_eur` as NULLable | The CHECK constraint `CK_fact_Trades_open_closed` enforces that when `is_open = 1` these are NULL and when `is_open = 0` they are NOT NULL. The two halves are correctly coupled. **However**, the CHECK assumes the generator never updates `is_open` from 1→0 without also setting all three fields atomically; if the worker forgets, the constraint will catch it — good. But the docstring in §4.1 says "Stored. Computed by the generator at exit time, then frozen." That word "frozen" implies immutability; consider an `INSTEAD OF UPDATE` trigger or a column-level no-update guard to enforce it. | Minor — generator owns this invariant today, but TVP-based updates in `V002` could regress. | Optional: add an `AFTER UPDATE` trigger that throws when a closed-row's pnl columns are mutated. Or simply note in §4.1 that the implementation MUST treat pnl columns as write-once. Recommend the second option until measurable abuse appears.

- [ ] **MN-05** | §6.5 `v_daily_pnl` ordering for window function | `SUM(ep.net_pnl_eur_total) OVER (PARTITION BY ep.employee_id ORDER BY ep.trade_date_ro ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)` — correct, but `trade_date_ro` for two open positions that close on the same day appears once in `v_employee_performance` (aggregated). On days the employee did not trade, no row exists (gap). The cumulative curve will therefore look "stepped" not "smooth"; reports that want a calendar-day equity curve must left-join to `dim_Date`. | Drawdown calc assumes a daily equity point — gaps are fine for MAX-MIN, but users may misread the visual. | Document the gap behaviour in §6.5. Optional: add a `v_employee_daily_equity` that left-joins to `dim_Date` and forward-fills the cumulative (uses `LAG()` with `IGNORE NULLS` available in modern SQL Server / Azure SQL).

- [ ] **MN-06** | §13 performance budget mention of "Hash Aggregate" | The plan estimate "IX_fact_Trades_trader_id_time_entry Seek → Nested Loops to dims → Hash Aggregate" is reasonable but should be validated post-deploy. | Aspirational, not blocking. | Add a `tests/sql/test_query_plans.sql` that asserts the operator types for the three benchmark queries; fail CI on regression. (Etapa 8 deliverable.)

- [ ] **MN-07** | §3.7 `dim_Sessions` columns vs `01_BR §5.7` | 01_BR uses `start_time` / `end_time`; 02_DB uses `start_time_local` / `end_time_local`. The `_local` suffix is good (it documents the timezone semantics) but the BR doc should match. | Cross-doc inconsistency. | Update `01_BR §5.7` to use `start_time_local` / `end_time_local`.

- [ ] **MN-08** | §15 (V001) `dim_Markets` `base_currency` not constrained | The column is `CHAR(3)` but has no CHECK enforcing length-3. (Compare with `dim_Companies.base_currency` which does.) | Could store `'US'` or `'USDX'` silently. | Add `CONSTRAINT CK_dim_Markets_base_currency CHECK (LEN(base_currency) = 3)`.

- [ ] **MN-09** | §15 (V001) `n0` CTE | `SELECT 1 AS x UNION ALL SELECT 1` — the second SELECT does not have an `AS x` alias. Works, but stylistically lopsided. | Cosmetic. | `SELECT 1 AS x UNION ALL SELECT 1 AS x`.

- [ ] **MN-10** | §10.2 `usp_GenerateDailyTrades` GRANT — duplicates Etapa 3 dependency | The role granted EXECUTE on a proc whose body is empty in `V001` and will be replaced in `V002`. When `V002` uses `CREATE OR ALTER PROCEDURE`, SQL Server preserves grants — but only if the proc was not dropped first. | Acceptable risk; just document. | Add a one-liner in §10.2: "Grants persist across `CREATE OR ALTER` but are lost on `DROP/CREATE`. Always use `CREATE OR ALTER` for procs that hold grants."

- [ ] **MN-11** | §3.9 `dim_Date.fiscal_year` always equals `year` | The column is documented as "Equal to `year` (Romania = calendar fiscal year)". | Stored as a redundant copy; wastes 4 bytes/row × 2 558 rows = ~10 KB (rounding error). | Either drop the column and add a computed column for forward-compatibility:
  ```sql
  fiscal_year AS [year] PERSISTED
  ```
  or keep it and add a CHECK `fiscal_year = [year]` so future writes cannot drift.

- [ ] **MN-12** | KPI-TR-063 (slippage) and OQ-04 | `01_BR` KPI-TR-063 requires a `modeled_pnl_eur` column that `02_DB` does not define. OQ-04 explicitly defers this. | Soft, but the KPI will be unimplementable in v1.0 unless `02_DB` evolves. | Resolve OQ-04 now: either add `modeled_pnl_eur DECIMAL(18,4) NULL` to `fact_Trades` (cheap, ~1 byte storage per row given NULL bitmap) or remove KPI-TR-063 from the v1.0 catalogue. Recommend adding the column with a comment "populated by Etapa 5 generator; NULL today".

- [ ] **MN-13** | §13 cold p95 budget vs `03_architecture.md §14` | `02_DB §13` quotes cold p95 of 800 ms for the assistant proc "accounts for auto-resume from pause". `03_architecture.md §14` quotes assistant cold start ≤ 4 s p95 "Python worker init ~1.5 s + SQL resume ~30–60 s if paused". The two figures are radically different. | Confusing. | Reconcile: the **SQL** cold budget is the auto-resume + first query (~30-60 s); the **assistant end-to-end** cold budget includes Python init + Anthropic call. State both, layered: "SQL cold path: 30 s (resume) + 200 ms (query); assistant cold path: SQL cold + 1.5 s python + 2.5 s Anthropic = ~34 s p95 if SQL was paused; warm path: 200 ms + 1.5 s = 1.7 s p95". The 4 s p95 in `03_architecture` only holds when SQL is warm.

- [ ] **MN-14** | §15 (V001) `OBJECT_ID(N'rls.TradesAccessPolicy', N'SP')` | `'SP'` is the object-type tag for a *stored procedure*, but a SECURITY POLICY object has type `SP` (yes, the same tag — both are listed under `'SP'` for compatibility, but the canonical check is via `sys.security_policies`). The current code uses *both* clauses (`OR` would be a bug, `AND` is overcautious but safe). | Defensive but stylistically odd. | Replace with:
  ```sql
  IF NOT EXISTS (SELECT 1 FROM sys.security_policies WHERE [name] = N'TradesAccessPolicy' AND [schema_id] = SCHEMA_ID(N'rls'))
  ```
  and drop the `OBJECT_ID` half.

- [ ] **MN-15** | §3.5 `dim_Accounts.opened_on` NOT NULL with no default | The column is required at insert time. The Python generator will need to supply it. | Cosmetic — the worker must pass a value. | Either add a default `SYSDATETIMEOFFSET()` cast to `DATE`, or note in §3.5 that the generator computes `opened_on = hire_date` of the trader.

- [ ] **MN-16** | §10.2 `tcp_admin` grants too broad | `ALTER ROLE db_datareader ADD MEMBER tcp_admin; ALTER ROLE db_datawriter ADD MEMBER tcp_admin; GRANT EXECUTE ON SCHEMA::dbo TO tcp_admin;` — gives `tcp_admin` SELECT/INSERT/UPDATE/DELETE on every table including `dim_UserRoles`, which means a tcp_admin can self-grant any RLS scope. | Acceptable for a one-person thesis project; would be a violation of least-privilege in production. | Document the trade-off in §10.2 as a thesis-context decision. No code change.

- [ ] **MN-17** | View `v_trades_enriched.return_pct` uses `f.price_entry` in quote currency, `f.quantity` in units, but `f.net_pnl_eur` is already EUR-converted | The division `net_pnl_eur / (quantity * price_entry)` mixes currencies (EUR / quote-currency-notional). For a non-EUR instrument the ratio is meaningless. | Latent semantic bug; if the AI assistant uses `return_pct`, answers will be wrong for non-EUR instruments. | Either (a) restrict `return_pct` to EUR-denominated instruments via `CASE WHEN m.base_currency = 'EUR' THEN ... ELSE NULL END`, or (b) drop `return_pct` from `v_trades_enriched` and recompute it from `gross_pnl_eur / capital_baseline_eur` at the period level. Recommend (b).

## Cross-doc consistency findings

- **ERD vs `02_DB`**: ERD declares a `fact_Trades.trade_date → dim_Date` relationship that does not exist in the table (see CR-04).
- **ERD vs `02_DB`**: ERD column `DIM_USERROLES.scope` is `varchar` but `02_DB §3.10` specifies `VARCHAR(20)` with a CHECK on a closed enum — ERD is fine, just imprecise. Acceptable in a Mermaid diagram.
- **`01_BR §4` field names**: `pnl_eur` is the alias the KPI catalogue uses; the actual column is `gross_pnl_eur`. The `01_BR Notation conventions` block does map `pnl_eur → gross_pnl_eur` indirectly, but several KPI formulas still write `pnl_eur` (KPI-TR-010, KPI-TR-012). Recommend either renaming the alias to `gross_pnl_eur` throughout `01_BR §4` or making the alias mapping explicit at the top.
- **`01_BR §5.4` vs `02_DB §3.2`**: `floor_name` / `is_primary` vs `city` / `is_primary_hq` (see MJ-12).
- **`01_BR §5.3` vs `02_DB §3.3`**: `team_lead_employee_id` is referenced but not modelled (see MJ-13).
- **`01_BR §5.5` vs `02_DB §3.6`**: instrument currency model (EUR-only vs multi-quote-currency) is contradictory (see MJ-10).
- **`01_BR §5.6` vs `02_DB §3.8`**: `is_directional` attribute is referenced but not defined (see MJ-11).
- **`01_BR §5.7` vs `02_DB §3.7`**: column names `start_time` vs `start_time_local` (see MN-07).
- **`01_BR §7.3` NFR-SEC-05 vs `02_DB §10.2`**: `db_datareader` vs `tcp_ai_assistant` (see MJ-09).
- **`03_architecture.md §5` vs `02_DB §10.2`**: `tcp_bi_reader` is named but not defined (see MJ-07).
- **`03_architecture.md §14` vs `02_DB §13`**: cold-start budgets do not stack consistently (see MN-13).

## Acceptance checklist for Etapa 2

The implementation under `db/migrations/V001__init.sql` is "ready to apply" when **all** of these are true:

- [ ] CR-01 fixed: `dim_Date` populator emits `iso_year` correctly (per ISO-8601, not a placeholder) and the INSERT column list matches the SELECT list 1:1.
- [ ] CR-02 fixed: `@holidays` table variable has a `PRIMARY KEY (h_date)` and `2026-06-01` collision is resolved.
- [ ] CR-03 fixed: 2024 + 2027-2030 Romanian public-holiday seed is audited against `Codul Muncii art. 139` and complete (`Ziua Copilului`, `Bobotează`, `Sfântul Ion`, `Unirea Principatelor` all present every year).
- [ ] CR-04 fixed: ERD relationship and/or `fact_Trades.trade_date_ro PERSISTED` + FK alignment.
- [ ] CR-05 fixed: `tcp_generator` has `SELECT ON config_Capital` and `EXECUTE ON fn_GetCapitalBaseline`.
- [ ] CR-06 fixed: `tcp_ai_assistant` has explicit `SELECT` on `dim_UserRoles` and `dim_Employees`; an RLS smoke test passes for each scope.
- [ ] CR-07 fixed: `usp_GenerateDailyTrades` returns gracefully on the already-generated path; smoke-test gating documented.
- [ ] MJ-01 fixed: per-trader unique filtered index on `config_Capital(trader_id, effective_from) WHERE trader_id IS NOT NULL`.
- [ ] MJ-02 fixed (or scheduled): inline TVF version of `fn_GetCapitalBaseline` documented in §8.
- [ ] MJ-03 decided: ADR-002 captures the materialised-vs-view trade-off for `v_daily_pnl`.
- [ ] MJ-04 fixed: every `v_*` view has `WITH SCHEMABINDING`.
- [ ] MJ-05 fixed: `SESSION_CONTEXT` reset is part of the `tcp/db.py` connection lifecycle; behaviour-without-context documented as deny-all.
- [ ] MJ-06 fixed: RLS predicate rewritten with CROSS APPLY; `UQ_dim_UserRoles_aad_object_id` covers `INCLUDE (employee_id, scope)`.
- [ ] MJ-07 fixed: `tcp_bi_reader` role defined in §10.2 with the documented grants.
- [ ] MJ-08 fixed: `tvf_RiskMetrics` (or equivalent) exposes mean / stdev / downside-stdev / VaR-95 per (employee_id, period).
- [ ] MJ-09 fixed: `01_BR` NFR-SEC-05 reconciled to `tcp_ai_assistant`.
- [ ] MJ-10 fixed: `01_BR §5.5` reconciled to multi-quote-currency + persisted EUR PnL.
- [ ] MJ-11, MJ-12, MJ-13 fixed: cross-doc column-name reconciliation completed.
- [ ] MJ-14 fixed: `SET DATEFIRST 1` at the top of the dim_Date populator.
- [ ] MJ-15 fixed: `TRY_CONVERT(DATE, SUBSTRING(trade_uid, 2, 8), 112) IS NOT NULL` check added.
- [ ] MJ-16 fixed: AAD-only-auth flip step documented in the bootstrap runbook.
- [ ] CI gate: the naming-convention script in §14 returns zero rows against the deployed DB.
- [ ] CI gate: a representative `SELECT TOP 1 *` against every `v_*` view succeeds under the `tcp_ai_assistant` role with `SESSION_CONTEXT` set to a seeded test principal.
- [ ] CI gate: `usp_GenerateDailyTrades` smoke test gated on `schema_history` version `>= V002`.
- [ ] Documentation: `docs/decisions/ADR-001-flyway-migrations.md`, `ADR-002-daily-pnl-materialisation.md`, `ADR-003-rls-session-context.md` all filed.
- [ ] Free-tier sanity: a `bicep what-if` on `azd provision` shows zero unexpected resources; the SQL Free Offer flag is `useFreeLimit: true` and `freeLimitExhaustionBehavior: 'AutoPause'`.
- [ ] Rollback: `db/migrations/rollback/V001__init.down.sql` exists and drops every object created by `V001` in reverse dependency order, even though the documented stance is "rollback = restore from PITR".

When this checklist is fully green, Etapa 2 can merge to `feat/azure-rewrite` and STATE.md advances to Etapa 3.

---

*End of review — `review_db_pass1.md`.*
