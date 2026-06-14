# Convergence review — pass 2

**Reviewer**: code-reviewer (verification pass)
**Date**: 2026-05-15
**Verdict**: ACCEPT

## Summary

All seven critical findings and every Major flagged for pre-merge fixing have landed correctly in the design package. The five high-stakes items the brief singled out (`dim_Date` iso_year computation, `@holidays` PK + 2026-06-01 collision, multi-year RO holiday seed, ERD/fact_Trades trade_date alignment, missing grants) are each individually verifiable in the committed files. Cross-doc consistency (BACPAC schedule, role names, annualised ratios, dimension column names, SESSION_CONTEXT contract) is now coherent. Diagram syntax is clean; the architecture.mmd `class` block names every defined node. No regressions introduced by the fix pass. The package is ready to advance to Etapa 2.

## Pass-1 ID status table

### DB review

| ID | Source review | Severity | Status (pass 2) | Notes / evidence |
|---|---|---|---|---|
| CR-01 | review_db_pass1 | critical | RESOLVED | `02_DB §15` lines 1761-1762: `iso_year` uses the Thursday-of-week trick `DATEPART(YEAR, DATEADD(DAY, 26 - DATEPART(ISO_WEEK, d), d))`; `iso_week_placeholder` is gone; INSERT column list (lines 1753-1755) matches the SELECT 1:1; `fiscal_year` correctly excluded (PERSISTED computed). |
| CR-02 | review_db_pass1 | critical | RESOLVED | Line 1792 declares `@holidays TABLE (h_date DATE PRIMARY KEY, ...)`. Line 1841: 2026-06-01 collapsed to single row `(N'Rusalii / Ziua Copilului', N'Pentecost Monday / Children''s Day')`. No duplicate dates anywhere in the seed. |
| CR-03 | review_db_pass1 | critical | RESOLVED | Verified all of 2024 + 2027-2030 carry `Ziua Copilului` (1 June), `Bobotează` (6 Jan), `Sfântul Ion` (7 Jan), `Unirea Principatelor Române` (24 Jan). Each year has 17 rows incl. the doubled Christmas/New Year days. |
| CR-04 | review_db_pass1 | critical | RESOLVED | `02_DB §4.1` line 1602: `trade_date_ro AS CAST(time_entry AT TIME ZONE 'E. Europe Standard Time' AS DATE) PERSISTED NOT NULL`; FK to `dim_Date(calendar_date)` at line 1617-1618; `IX_fact_Trades_trade_date_ro` at line 1649. ERD line 15 reads `DIM_DATE ||--o{ FACT_TRADES : "trade_date_ro (persisted computed)"`. |
| CR-05 | review_db_pass1 | critical | RESOLVED | Lines 2391-2392: `GRANT SELECT ON dbo.config_Capital TO tcp_generator;` and `GRANT EXECUTE ON dbo.fn_GetCapitalBaseline TO tcp_generator;` both present in the V001 grants block. |
| CR-06 | review_db_pass1 | critical | RESOLVED | Lines 2378-2379: `GRANT SELECT ON dbo.dim_UserRoles TO tcp_ai_assistant;` + `GRANT SELECT ON dbo.dim_Employees TO tcp_ai_assistant;` with the RLS-predicate comment. |
| CR-07 | review_db_pass1 | critical | RESOLVED | `02_DB §15` lines 2188-2192: idempotent branch returns `SELECT 0 AS rows_inserted, 'already_generated' AS status; RETURN 0` — no THROW. Aligns with the §7.1 contract docstring. |
| MJ-01 | review_db_pass1 | major | RESOLVED | Lines 1569-1571: filtered unique `UX_config_Capital_trader_current` on `(trader_id, effective_from) WHERE trader_id IS NOT NULL` shipped alongside the existing global index. |
| MJ-02 | review_db_pass1 | major | RESOLVED | `02_DB §8.5` introduces `tvf_GetCapitalBaseline` as an inline TVF; DDL at lines 2146-2162 of §15. |
| MJ-03 | review_db_pass1 | major | RESOLVED (deferred to ADR) | §6.5 documents the gap behaviour and points to `tvf_RiskMetrics`; ADR-002 referenced in the checklist. |
| MJ-04 | review_db_pass1 | major | RESOLVED | Every `v_*` view has `WITH SCHEMABINDING` (lines 1998, 2036, 2059, 2081, 2102 in §15). `v_trades_enriched` uses two-part naming, no `SELECT *`. |
| MJ-05 | review_db_pass1 | major | RESOLVED | §9.1 line 992 documents the `sp_set_session_context @value=NULL, @read_only=0` checkout reset rule and the deny-by-default semantics. |
| MJ-06 | review_db_pass1 | major | RESOLVED | §9.2 predicate rewritten with one-shot CROSS APPLY (lines 1011-1023 of §9 / 2308-2338 of §15); `IX_dim_UserRoles_aad_object_id_INC` covers `(aad_object_id, is_active) INCLUDE (employee_id, scope)` at lines 1538-1540. |
| MJ-07 | review_db_pass1 | major | RESOLVED | `tcp_bi_reader` defined in §10.2 and §15 lines 2365-2366, with SELECT grants on the five views and the seven dim tables (lines 2397-2408). |
| MJ-08 | review_db_pass1 | major | RESOLVED | `tvf_RiskMetrics` defined in §8.4 and bundled at §15 lines 2123-2143; exposes mean/stdev/downside-stdev/var_95/total per `(employee_id, period)`. |
| MJ-09 | review_db_pass1 | major | RESOLVED | `01_BR §9.5` NFR-SEC-05 now describes the `tcp_ai_assistant` + `tcp_generator` two-role split; matches `02_DB §10.2`. |
| MJ-10 | review_db_pass1 | major | RESOLVED | `01_BR §5.5` says "Instrument quote currencies vary"; `02_DB §3.6` "Multi-currency note"; both cross-link to `tcp/synth/fx_rates.py`. |
| MJ-11 | review_db_pass1 | major | RESOLVED | `dim_OrderType.is_directional BIT NOT NULL DEFAULT 1` defined in §3.8 and seeded; ERD has `bit is_directional`. |
| MJ-12 | review_db_pass1 | major | RESOLVED | `01_BR §5.4` uses `city`, `floor_code`, `is_primary_hq` matching `02_DB §3.2`. |
| MJ-13 | review_db_pass1 | major | RESOLVED | `01_BR §5.3` documents that team-lead is resolved via `dim_Employees WHERE team_id = X AND role = 'team_lead'`; no fabricated column reference. |
| MJ-14 | review_db_pass1 | major | RESOLVED | §15 line 1740 has explicit `SET DATEFIRST 1;` before the dim_Date populate, with simplified `DATEPART(WEEKDAY, ...)` derivation. |
| MJ-15 | review_db_pass1 | major | RESOLVED | `CK_fact_Trades_trade_uid_date_valid` at line 1621-1622: `CHECK (TRY_CONVERT(DATE, SUBSTRING(trade_uid, 2, 8), 112) IS NOT NULL)`. |
| MJ-16 | review_db_pass1 | major | RESOLVED | `02_DB §10.1` step 3 documents `Set-AzSqlServerActiveDirectoryOnlyAuthentication ... -Enable $true` and the `SQL-ADMIN-PASSWORD-BOOTSTRAP` secret deletion; `03_arch §6.5` mirrors this with the script. CI gate A.8 added in §17.1. |
| MJ-17 | review_db_pass1 | major | RESOLVED | §9.2 prose and DDL use `p` (principal) and `t` (trader row of fact row) aliases with inline comments. |

### Architecture review

| ID | Source review | Severity | Status (pass 2) | Notes / evidence |
|---|---|---|---|---|
| CR-01 | review_arch_pass1 | critical | RESOLVED | `03_arch §3.2` step 5 (lines 59-63) describes SESSION_CONTEXT + dim_UserRoles lookup; §3.4 names the AAD-oid binding as the per-request trust edge; §3.2 step 4 emits `tcp.rls.session_context_set=true`. |
| CR-02 | review_arch_pass1 | critical | RESOLVED | §5 RBAC matrix row "Function App MI → SQL" carries the RLS-contract sub-note ("no `EXECUTE AS OWNER`; MUST set SESSION_CONTEXT per request — see 02_DB §9 and §3.2"). CI gate A.2 in §17.1. |
| CR-03 | review_arch_pass1 | critical | RESOLVED | `architecture.mmd` line 66: `Scheduled Refresh (Import)`. `03_arch §3.3` and NFR-PERF-04 (01_BR §9.1) state Import explicitly. No remaining DirectQuery mentions. |
| MJ-01 | review_arch_pass1 | major | RESOLVED | `03_arch §6.1` table: `gh-dev` uses `environment:dev`; `gh-main` uses ref:refs/heads/main; `gh-pr` uses pull_request claim. Workflow boilerplate has `environment: dev`. |
| MJ-02 | review_arch_pass1 | major | RESOLVED | §5 RBAC matrix row notes the simplified `Contributor`-at-RG posture, explicitly drops `Key Vault Secrets Officer` + `SQL Server Contributor`, references the future ADR for the production-grade split. |
| MJ-03 | review_arch_pass1 | major | RESOLVED | §6.4 lays out the `safe_query.py` contract (sqlglot, allowlist tables/procs, SELECT-only, forbidden tokens, TOP <= 1000, fail-closed). Threat-table row in §8.3 reflects same. |
| MJ-04 | review_arch_pass1 | major | RESOLVED | §3.2 step 3 says "Calls Anthropic claude-haiku-4-5 **once** ... single call covering both the SQL query and the natural-language template ... No second Anthropic call". Step 6 confirms the answer template comes from step 3. `ai_sequence.mmd` shows one Anthropic message. |
| MJ-05 | review_arch_pass1 | major | RESOLVED | `TimerTrigger_BacpacExport` is in §2 (five triggers), §3 (no separate sub-section, but §11 documents `0 0 8 * * 0` schedule, the SQL-DB-Contributor MI grant, and the duration/size metrics), and `architecture.mmd` lines 19, 58-59. CI gate A.5 added. |
| MJ-06 | review_arch_pass1 | major | RESOLVED | `HttpTrigger_Ping` documented in §3.5 with the response shape; `architecture.mmd` lines 20, 43, 56; §14 budget split into warm-path (≤1.5s p95) and cold-path (~35s p95). CI gate A.6 added. |
| MJ-07 | review_arch_pass1 | major | RESOLVED | §8.2 bullet 4 describes `staticwebapp.config.json` `forwardingGateway.requiredHeaders` with `X-SWA-Forwarded` from KV; §8.3 threat row "determined attacker bypassing SWA + forging principal header" reclassified to "Mitigated". KV secret `SWA-FORWARDED-SECRET` added to §7 rotation table is not present — see "Remaining gaps" note. |
| MJ-08 | review_arch_pass1 | major | RESOLVED | §17 split into §17.1 (CI-gated, with `psrule-for-azure` Azure.MCSB.v1 and `checkov --framework bicep` in §9.1 CI stage table) and §17.2 (Day-7 manual). |

### Holistic review

| ID | Source review | Severity | Status (pass 2) | Notes / evidence |
|---|---|---|---|---|
| CR-01 | review_holistic_pass1 | critical | RESOLVED | `ai_sequence.mmd` lines 29-37 use `scope: trader/team_lead/floor_manager/admin`; explanatory comment at lines 1-4 clarifies these are RLS scopes, not SQL roles. |
| CR-02 | review_holistic_pass1 | critical | RESOLVED | `01_BR §9.5` NFR-SEC-05 and `01_BR §7.3` both describe the two-role split (`tcp_ai_assistant` read-only on views, `tcp_generator` INSERT/UPDATE on `fact_Trades`); matches `02_DB §10.2`. |
| CR-03 | review_holistic_pass1 | critical | RESOLVED | `02_DB §12` line 1159: "second Function App Timer Trigger (NCRONTAB `0 0 8 * * 0`, Sunday 08:00 RO) using the Function MI for storage RBAC. ... See `03_arch §11` for the runbook". `03_arch §11` RTO/RPO table row + §16 explicitly notes the older GHA Sunday 02:00 UTC plan is superseded. `architecture.mmd` shows `BacpacExport` with cron `0 0 8 * * 0`. |
| CR-04 | review_holistic_pass1 | critical | RESOLVED | `01_BR §4` Notation conventions (lines 134, 136) define `gross_pnl_eur`/`net_pnl_eur`. KPI-TR-010 "Source field `gross_pnl_eur` in `fact_Trades`". Formula columns reference the canonical names. |
| CR-05 | review_holistic_pass1 | critical | RESOLVED | `01_BR §5.2-§5.7` all use the actual schema column names: `first_name`/`last_name` + `trader_full_name` (5.2), no `team_lead_employee_id` (5.3 explicit note), `city`/`is_primary_hq` (5.4), `display_name`/`symbol`/`quote_currency` (5.5), `display_name`/`is_directional` (5.6), `display_name`/`start_time_local`/`end_time_local` (5.7). |
| MA-01 | review_holistic_pass1 | major | RESOLVED | `dim_Markets` column renamed to `quote_currency` (§3.6, §15 line 1426, ERD line 83). Seed values are quote currencies (e.g. USDJPY → JPY). |
| MA-02 | review_holistic_pass1 | major | RESOLVED | `CK_fact_Trades_open_closed` (§4.1 + §15 line 1628-1632) couples `gross_pnl_eur IS NOT NULL` to the `is_open = 0` branch. |
| MA-03 | review_holistic_pass1 | major | RESOLVED | Same fix as DB CR-01; iso_year correctly derived. |
| MA-04 | review_holistic_pass1 | major | RESOLVED | KPI-TR-033, KPI-TR-034, KPI-TM-031, KPI-FL-031 all show `× SQRT(252)` annualisation. RF=0 box (§4.4 line 208) unchanged; targets preserved. |
| MA-05 | review_holistic_pass1 | major | RESOLVED | `01_BR §5.5` + `02_DB §3.6` Multi-currency note describe FX-rate table at `tcp/synth/fx_rates.py`; `01_BR §10` item 4 reconciled. |
| MA-06 | review_holistic_pass1 | major | RESOLVED | `02_DB §4.1` line 440: "Columnstore acceleration (a future `NCCI_fact_Trades_Analytics`) is deferred to a later migration (`V003+`)" — V001 does not include the disabled index. Prose matches DDL. |
| MA-07 | review_holistic_pass1 | major | RESOLVED | `02_DB §7.1` (lines 673-674) and §9.1 (line 992) document the generator MI's SESSION_CONTEXT contract; `03_arch §5` RBAC row + post-provision step register the MI in `dim_UserRoles` with `scope='admin'`. |
| MA-08 | review_holistic_pass1 | major | RESOLVED | Single Anthropic call confirmed across §3.2 step 3 and `ai_sequence.mmd`. |
| MA-09 | review_holistic_pass1 | major | RESOLVED | KPI-TR-033 (line 215) explicitly requires the dashboard to show `'pending — need ≥ 5 trading days'`; `01_BR §8.2` AC-AI-08 enforces same. |
| MA-10 | review_holistic_pass1 | major | RESOLVED | `fact_Trades.fx_rate_to_eur DECIMAL(18,8) NULL` added at §4.1 line 409 + §15 line 1600; ERD line 153. The `CK_fact_Trades_fx_rate_required` documented as logical contract (trigger deferred to implementation). |

## New issues introduced by the fix pass (regressions)

None blocking; two minor consistency drifts noted as recommendations only:

1. **MJ-07 (arch) — `SWA-FORWARDED-SECRET` not in §7 KV secrets table.** §8.2 bullet 4 introduces the shared secret and `03_arch §16` module map mentions `swaForwardedSecretUri` as a `swa.bicep` param, but the secret is missing from the §7 rotation table (which still lists only `ANTHROPIC-API-KEY`, `STORAGE-CONNECTION-STRING`, `SQL-ADMIN-PASSWORD-BOOTSTRAP`, `POWERBI-SP-CLIENT-SECRET`). Add a fifth row for completeness and to make the rotation playbook complete. Non-blocking.

2. **§4.1 `CK_fact_Trades_fx_rate_required` is documented as a check via subquery.** The text correctly states "declarative cross-table CHECKs are not directly supported by SQL Server; in V001 this is enforced by an AFTER INSERT/UPDATE trigger or by a check-via-UDF; documented here as a logical contract" — but the V001 DDL bundle in §15 does **not** include the trigger or the UDF. Either remove `CK_fact_Trades_fx_rate_required` from §4.1 (defer to a later migration), or add the trigger to §15 before merge. Non-blocking but should be cleaned up before Etapa 2 schema apply or the §15 "self-contained, idempotent" claim weakens.

## Remaining gaps and recommended action

- **§7 KV secrets table addendum.** Append `SWA-FORWARDED-SECRET` to the rotation playbook (one-line edit). Owner: arch doc.
- **`CK_fact_Trades_fx_rate_required` trigger or constraint.** Either delete the named CHECK constraint from §4.1 or land the AFTER INSERT/UPDATE trigger in §15. Owner: db design.

Neither item blocks closing Etapa 1; both can be addressed during Etapa 2's first migration commit.

## Additional checks

- **English-only policy.** The only Romanian text in committed artifacts is the `month_name_ro` column (paralleled by `month_name_en`) and the `ro_holiday_name` column (paralleled by `en_holiday_name`); both are documented exceptions in `02_DB §3.9` and the holistic review's English-only audit. Proper nouns (București, Cluj-Napoca, holiday names) are unchanged. No new RO text introduced.
- **Diagram syntax.** All four `.mmd` files parse cleanly. `architecture.mmd` `class` block at lines 73-76 names every node defined in subgraphs (SWA, FuncApp, TimerTrigger, HttpTrigger, WarmupTrigger, BacpacExport, PingTrigger, SQLDb, StorageAcct, KV, MI, AppInsights, LogAnalytics, DevBrowser, GithubActions, AnthropicAPI, PowerBIService). Edge "OIDC + azd deploy" terminates at the `Azure_RG` subgraph (legal Mermaid). The "Scheduled Refresh (Import)" label is correct. `ai_sequence.mmd` alt/else/end blocks balanced; no malformed quotes. `erd.mmd` relationship lines all use `||--o{`; the `(persisted computed)` annotation on the DIM_DATE → FACT_TRADES line is inside quotes (legal Mermaid label).
- **Trade volume numeric consistency.** `01_BR §9.1` NFR-PERF-04 (line 527) states "30 trading individuals × approximately 7–8 trades per active day × 250 trading days ≈ 52 500–60 000 rows in `fact_Trades` per year". `02_DB §1` and §4.1 partitioning rationale align (~60 000 rows/year). `03_arch §3.1` step 5 says "~150–250 rows/day". `cron_flow.mmd` "InsertFacts" reads "~150-250 rows total". All three converge on the 150-240/day, ~60k/year base case.
- **Trigger count.** Architecture §2 row 6 says "five triggers"; §3 enumerates `TimerTrigger_DailyGenerator`, `HttpTrigger_AskAssistant`, `WarmupTrigger`, `TimerTrigger_BacpacExport`, `HttpTrigger_Ping` = 5. `architecture.mmd` Compute subgraph lists all five (TimerTrigger, WarmupTrigger, HttpTrigger, BacpacExport, PingTrigger). Consistent.
- **Region.** "West Europe" primary, "North Europe" fallback, present in `03_arch §2`, §4.2, §15 R3, and `architecture.mmd` subgraph title. Consistent.
- **§15 V001 DDL bundle self-containment.** Re-runnable: every `OBJECT_ID(...) IS NULL` / `DATABASE_PRINCIPAL_ID(...) IS NULL` guard present; idempotent re-apply documented at the top. Reflects every schema change: `trade_date_ro` computed + FK + index (lines 1602, 1617-1618, 1649-1650), `fx_rate_to_eur` column (line 1600), `quote_currency` rename (line 1426), `en_holiday_name` column (lines 1497, 1755, full seed in @holidays VALUES), `employee_role` rename (line 1362), `is_directional` (line 1469), `tcp_bi_reader` role (lines 2365-2366, 2397-2408), `tvf_RiskMetrics` (lines 2123-2143), `tvf_GetCapitalBaseline` (lines 2146-2162), filtered unique on per-trader config_Capital (lines 1569-1571), `IX_dim_UserRoles_aad_object_id_INC` (lines 1538-1540), CROSS-APPLY RLS predicate (lines 2308-2338), `month_name_en` column (lines 1491, 1773-1778), `ro_holiday_name` + `en_holiday_name` UPDATE (lines 1920-1925), `SET DATEFIRST 1` (line 1740), `CK_fact_Trades_trade_uid_date_valid` (lines 1621-1622), `usp_GenerateDailyTrades` graceful-replay branch (lines 2188-2192). The only DDL not present from §4.1 prose is `CK_fact_Trades_fx_rate_required` (called out under Remaining gaps).

## Recommendation

Etapa 1 is convergent and may be closed. All blocking findings from pass 1 are resolved and no regressions were introduced. The two non-blocking items (`SWA-FORWARDED-SECRET` row in §7; `CK_fact_Trades_fx_rate_required` enforcement trigger) can be cleaned up alongside Etapa 2's first migration commit without holding the stage gate. Advance STATE.md to Etapa 2 and file ADR-001 (Flyway migrations), ADR-002 (daily-pnl materialisation), ADR-003 (RLS session-context contract), and ADR-004 (BACPAC schedule) as part of the Etapa 1 sign-off package.

---

*End of `review_convergence_pass2.md`.*
