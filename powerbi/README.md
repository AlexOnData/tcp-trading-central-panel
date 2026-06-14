# TCP — Trading Central Panel: PowerBI Semantic Model & Report

> **Component scope.** This README documents the `powerbi/` directory only. For project-wide context, deploy walkthrough, troubleshooting, and the full doc index, see the [top-level README](../README.md). The PowerBI deploy runbook lives at [`docs/runbooks/powerbi_deploy.md`](../docs/runbooks/powerbi_deploy.md). For terminology, see the [glossary](../docs/glossary.md).

## Overview

This directory contains the version-controlled source for the TCP Trading Central Panel PowerBI dataset and report. The model is authored in **TMDL** (Tabular Model Definition Language) and the report skeleton in **PBIR** (PowerBI Report Definition), both text-based formats that git-diff cleanly.

The highest-value artifact here is the **semantic model** (`model/`): 15 tables (5 view tables + 9 dim tables + 1 hidden `_Measures` host), 16 star-schema relationships, **69 DAX measures** covering all 48 KPI families from [`docs/design/01_business_requirements.md`](../docs/design/01_business_requirements.md) §4 (some KPI families ship trader / team / floor / company variants — see the `displayFolder` grouping in `_Measures.tmdl`), and 4 dataset-level RLS roles. The report pages (`report/`) are a minimal skeleton — final visual layout is completed in PowerBI Desktop (documented in [`docs/runbooks/powerbi_deploy.md`](../docs/runbooks/powerbi_deploy.md)).

---

## Directory Structure

```
powerbi/
├── model/
│   ├── database.tmdl           — model header, culture ro-RO, Import mode, SQL data source
│   ├── model.tmdl              — M parameters: SqlServer, SqlDatabase (substituted at deploy)
│   ├── tables/
│   │   ├── _Measures.tmdl      — all 48 KPIs as DAX measures grouped by display folder
│   │   ├── v_trades_enriched.tmdl
│   │   ├── v_employee_performance.tmdl
│   │   ├── v_team_performance.tmdl
│   │   ├── v_floor_performance.tmdl
│   │   ├── v_daily_pnl.tmdl    — sourced from fact_DailyTraderPnL (ADR-002)
│   │   ├── dim_Date.tmdl       — dataCategory: time; Calendar hierarchy; isKey: calendar_date
│   │   ├── dim_Companies.tmdl
│   │   ├── dim_TradingFloors.tmdl
│   │   ├── dim_Teams.tmdl
│   │   ├── dim_Employees.tmdl
│   │   ├── dim_Accounts.tmdl
│   │   ├── dim_Markets.tmdl
│   │   ├── dim_Sessions.tmdl
│   │   └── dim_OrderType.tmdl
│   ├── relationships.tmdl      — 16 star-schema relationships, all active, one-direction
│   ├── roles.tmdl              — Admin / FloorManager / TeamLead / Trader RLS roles
│   └── cultures/
│       └── ro-RO.tmdl          — Romanian display-name translations for measures and tables
└── report/
    ├── definition.pbir         — dataset reference (pbiModelDatabaseName substituted at deploy)
    ├── report.json             — report-level config (locale ro-RO, theme)
    ├── pages.json              — page list and display order
    └── pages/
        ├── floor-performance/page.json    — exec view (UC-01, UC-11, UC-15)
        ├── team-performance/page.json     — team lead view (UC-02, UC-03, UC-09, UC-10)
        ├── trader-detail/page.json        — individual view (UC-04, UC-07, UC-13)
        └── ai-assistant/page.json         — hyperlink button to the SWA AI assistant (opens in new tab)
```

---

## TMDL vs PBIR

| Aspect | TMDL | PBIR |
|---|---|---|
| What it describes | Semantic model: tables, columns, measures, relationships, roles, translations | Report: pages, visuals, filters, layout |
| Primary value | DAX business logic, RLS, locale, star schema — the durable, re-usable layer | Visual layout — finalised in Desktop |
| Format | `.tmdl` text (indentation-significant) | `.json` (standard JSON) |
| Git-diffable | Yes | Yes |
| Deploy path | XMLA endpoint or Tabular Editor CLI | REST API import or Desktop publish |

---

## Locale

- Model culture: `ro-RO`
- Date format: `dd.MM.yyyy`
- Decimal separator: `,` (Romanian standard)
- Thousand separator: `.`
- EUR format string on every monetary measure: `"#,##0.00 €"`
- Romanian translations for measure display names: `cultures/ro-RO.tmdl`

PowerBI will render `12345.67` as `12.345,67 €` when the report locale matches `ro-RO`.

---

## Semantic Model: Key Design Decisions

### Measure centralisation

All 48 KPIs live in a single `_Measures` table rather than being distributed across the 5 view tables. Rationale: discoverability (one place in the Field List), avoidance of host-table coupling for cross-table DAX, and cleaner display-folder organisation. The trade-off is a hidden placeholder column; this is standard practice for centralised measure tables.

### Import mode (not DirectQuery)

Mandated by ADR-001 (CR-03 architecture change request). The Azure SQL Free Offer database auto-pauses after 60 minutes of inactivity. DirectQuery would wake the database on every visual render and exhaust the free vCore-second budget. Import mode materialises a full copy at each scheduled refresh (07:30 Europe/Bucharest, after the 07:00 data generation). This fits the daily-snapshot reporting pattern.

### v_daily_pnl sources from fact_DailyTraderPnL

Per ADR-002, `v_daily_pnl` is a thin presentation view over the materialised `fact_DailyTraderPnL` table rather than the stacked `v_employee_performance -> v_trades_enriched` chain. Risk KPI DAX (Sharpe, Sortino, Max Drawdown, VaR-95) therefore runs against a ~36,000-row pre-aggregated table rather than the full `fact_Trades` join stack.

### RLS: dataset-level roles are independent of SQL RLS

The SQL-side RLS (SESSION_CONTEXT contract, ADR-003) guards the `/api/ask` AI assistant path. The dataset-level roles defined in `roles.tmdl` guard the PowerBI report path. Both resolve the same conceptual hierarchy (Admin > FloorManager > TeamLead > Trader) but through different mechanisms. v1.0 loads via the service principal (Admin scope); per-user role assignment in the PowerBI Service is a future hardening pass.

### AI Assistant page: hyperlink, not iframe

The `ai-assistant` report page hosts a hyperlink/button visual that opens the Static Web Apps URL in a new tab — it does NOT embed the SWA in an iframe. This design choice preserves the Etapa 6 clickjacking hardening: the SWA serves `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'`, which the browser would enforce by refusing to render the SWA inside PowerBI. Opening in a new tab keeps the AAD session intact (no cross-origin cookie partitioning issues) and the SWA's security headers stay strict. `deploy.ps1` substitutes the `<SWA_HOSTNAME>` placeholder in the page JSON from `AZURE_STATIC_WEB_APP_HOSTNAME` at deploy time.

---

## Deploy Procedure

See `docs/runbooks/powerbi_deploy.md` (authored by the docs-architect parallel agent) for the step-by-step deploy runbook.

High-level steps:

1. Run `powerbi/deploy.ps1` (authored by the deploy-agent parallel agent) which calls the PowerBI REST API via `az rest`.
2. The script substitutes `<SERVER_FQDN>`, `<DATABASE_NAME>`, `<TENANT_ID>`, and `<DATASET_ID_AT_DEPLOY_TIME>` from Key Vault secrets.
3. After import, the dataset is bound to the Azure SQL data source via `Default.UpdateDatasources`.
4. Ownership is taken by the service principal via `Default.TakeOver`.
5. Scheduled refresh is configured to run at 07:30 Europe/Bucharest on weekdays.

References: `docs/decisions/ADR-001-powerbi-deployment.md`, `docs/design/03_architecture.md §3.3 and §4.2`.

---

## Local Development Workflow

### PowerBI Desktop (recommended for visual layout)

PowerBI Desktop (November 2023 or later) reads TMDL natively:

1. Open Desktop.
2. `File > Open > Browse this device` — navigate to this repo.
3. Select any `.tmdl` file; Desktop resolves the full model from `database.tmdl`.
4. For the live connection to Azure SQL, set the M parameters `SqlServer` and `SqlDatabase` when prompted.

### Tabular Editor 3 / pbi-tools (CI / headless)

For older Desktop versions or CI validation of the TMDL schema without a GUI:

```powershell
# Validate model with Tabular Editor 3 CLI (requires TE3 license)
TabularEditor3.exe "powerbi/model/database.tmdl" -Validate

# Or use pbi-tools to compile TMDL to a .pbix for upload
pbi-tools compile "powerbi/" -format PBIX -outPath "dist/tcp-trading-central-panel.pbix"
```

### PBIR visual layout (Desktop only)

The `pages/<page>/page.json` files are minimal stubs. Complete the visual layout in Desktop, then use `File > Save` to write back the PBIR-format changes (requires Desktop preview feature "Store reports using enhanced metadata format" enabled). Commit the updated `page.json` files to git.

---

## KPI Coverage Summary

| Family | KPI IDs | Count |
|---|---|---|
| Volume & Activity | KPI-TR-001 to KPI-TR-007 | 7 |
| PnL | KPI-TR-009 (Total Commission helper), KPI-TR-010 to KPI-TR-019, KPI-TM-010/011, KPI-FL-010/011, KPI-CO-010/011 | 17 |
| Performance vs Capital | KPI-TR-020 to KPI-TR-023, KPI-TM-020, KPI-FL-020, KPI-CO-020 | 7 |
| Risk | KPI-TR-030 to KPI-TR-040, KPI-TM-030/031, KPI-FL-030/031 | 15 |
| Behavioral | KPI-TR-050 to KPI-TR-056 | 7 |
| Quality | KPI-TR-060 to KPI-TR-062, KPI-TM-060, KPI-FL-060 | 5 |
| Team & Floor Aggregates | KPI-TM-070 to KPI-TM-073, KPI-FL-070 to KPI-FL-072 | 7 |
| Leadership & Coverage | KPI-LR-001 to KPI-LR-004 | 4 |
| **Total** | | **48** (1 deferred: KPI-TR-039 exact consecutive-loss streak) |

**KPI-TR-039 (Max Consecutive Losses)** and **KPI-TR-063 (Slippage Estimate)** are deferred:
- KPI-TR-039 returns BLANK() in DAX because consecutive-loss streak requires ordered window logic not expressible without a dedicated DB column. Marked `TODO Etapa-12 polish` in `_Measures.tmdl`. The AI assistant (which can run ordered SQL CTEs) handles this KPI accurately.
- KPI-TR-063 is out of scope for v1.0 per `01_business_requirements.md §10 item 11`.

---

## Known Limitations (deferred to Etapa-12)

The following items were intentionally left as v1.0 trade-offs and tracked for the Etapa-12 polish pass. None of them blocks the dataset from refreshing, the report from rendering, or the AI assistant from answering correctly.

- **Capital baseline hardcoded to 80 000 EUR.** Measures KPI-TR-020, KPI-TR-021, KPI-TR-022, KPI-TR-023, and KPI-TR-031 read `VAR Capital = 80000` directly instead of `LOOKUPVALUE(config_Capital[capital_baseline_eur], ...)`. Switching to a per-trader override read becomes meaningful once `config_Capital` carries non-uniform values. Tracked: business-analyst review M-03.
- **KPI-TR-039 Max Consecutive Losses returns BLANK().** Ordered window logic over closed trades cannot be expressed in pure DAX without a pre-aggregated streak column. The AI assistant covers the use-case via a SQL CTE today. Tracked: business-analyst review M-05.
- **KPI-TR-052 Overnight Position Frequency is an approximation.** The DAX does not yet compare `CAST(time_exit AS DATE) > CAST(time_entry AS DATE)`. Needs an `is_overnight` computed column on `v_trades_enriched`. Tracked: business-analyst review M-06.
- **KPI-TR-053 Weekend Carry / KPI-TR-054 Intraday Rate are approximations.** Needs `is_weekend_carry` and `is_intraday` computed columns on `v_trades_enriched`. Tracked: business-analyst review M-07.
- **KPI-TR-031 Max Drawdown Pct / team / floor variants compute the capital denominator from `DISTINCTCOUNT(employees) × 80000`.** The spec intent is `SUMX(config_Capital[capital_baseline_eur])`. Numerically identical today, semantically diverges once per-trader overrides land. Tracked: business-analyst review M-04.
- **`dim_UserRoles` is not exposed to the PowerBI semantic model.** RLS resolves identity via `dim_Employees[email] = USERPRINCIPALNAME()`, which is correct because the project aligns `email` to the AAD UPN. Tracked: database-architect review MN-01.
- **`dim_Accounts` is loaded but has no relationships.** It is dead weight in v1.0 (no view exposes `account_id`). Either drop the table or wire `account_id` onto `v_trades_enriched`. Tracked: database-architect review MN-02.
- **`dim_Employees.aad_object_id` not declared in the TMDL.** Adding the hidden column future-proofs an OID-based RLS hardening pass. Tracked: database-architect review MN-03.

---

## References

- `docs/decisions/ADR-001-powerbi-deployment.md` — deploy strategy (REST API via `az rest`)
- `docs/decisions/ADR-002-daily-pnl-materialisation.md` — why `v_daily_pnl` sources from `fact_DailyTraderPnL`
- `docs/decisions/ADR-003-rls-session-context.md` — SQL RLS vs PowerBI dataset RLS boundary
- `docs/design/01_business_requirements.md §4` — KPI catalog (48 KPIs)
- `docs/design/02_database_design.md §6` — view schemas sourced by each table
- `docs/design/03_architecture.md §3.3, §4.2` — BI path and PowerBI service connection
