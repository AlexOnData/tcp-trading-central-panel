# Glossary

Single source of truth for terminology used across the TCP — Trading Central Panel codebase, documentation, and academic thesis. Terms are organised alphabetically within five groups:

1. [Trading & KPI domain](#1-trading--kpi-domain)
2. [Database & data engineering](#2-database--data-engineering)
3. [Azure & infrastructure](#3-azure--infrastructure)
4. [Observability & reliability](#4-observability--reliability)
5. [Security & compliance](#5-security--compliance)
6. [Project conventions](#6-project-conventions)

When a term appears in more than one group (e.g., RBAC spans Azure + security), it is defined in the most specific group and cross-referenced elsewhere.

This page **supersedes** the per-document glossaries that appeared in earlier stages:

- The Etapa-1 glossary in [`docs/design/01_business_requirements.md`](design/01_business_requirements.md) §11.
- Ad-hoc definitions in [`docs/security/threat_model.md`](security/threat_model.md), [`docs/observability/slo.md`](observability/slo.md), and [`docs/design/03_architecture.md`](design/03_architecture.md).

Those documents now link back here for canonical definitions.

---

## 1. Trading & KPI domain

| Term | Definition |
|---|---|
| **Capital Baseline** | Per-trader allocated trading capital, expressed in EUR. Default 80 000 EUR; per-trader overrides supported via `config_Capital` with `effective_from` semantics. Drives ROC, Sharpe normalisation, and Capital Utilisation Ratio. |
| **Capital Utilisation Ratio** | Ratio of the average notional value of open positions to the trader's capital baseline. > 1.0 indicates leverage. |
| **Drawdown** | Peak-to-trough decline in a cumulative PnL equity curve. Max drawdown measures the largest such decline over a period. Expressed as EUR or as a percentage of capital baseline. |
| **Equity Curve** | Time-series line of cumulative net PnL for a trader, team, or floor. Rising = profitable. |
| **FX Rate** | Foreign-exchange conversion rate from the trade's quote currency to EUR. Stored per closed non-EUR trade in `fx_rate_to_eur` so PnL can be aggregated in a single reporting currency. |
| **Gross PnL** | Raw difference between entry and exit trade value. Before commissions and fees. |
| **Holding Time** | Duration between a trade's entry and exit, in minutes. Short = intraday scalping; long = swing or position trading. |
| **Intraday Trade** | A trade opened and closed within the same calendar trading day. |
| **Intra-Team Variance** | Standard deviation of per-trader PnL within a team. Low = consistent team; high = one trader dominates. |
| **ISO Week** | Week numbering per ISO 8601: weeks start Monday; the first week of the year contains the first Thursday of January. |
| **Leadership Multiplier** | See *Team-Lead PnL Multiplier*. |
| **Max Consecutive Losses** | Longest unbroken sequence of trades (ordered by entry time) where every trade has a negative net PnL. |
| **MTD (Month-to-Date)** | Period from the first calendar day of the current month through today. |
| **Net PnL** | Gross PnL minus commissions and fees. The canonical performance number throughout the system. |
| **Notional Value** | Absolute exposure of a position: `quantity × entry_price_eur`. Distinct from PnL — a 10 000 EUR notional position can have a +50 EUR PnL. |
| **Overnight Position** | A trade where the exit occurs on a different calendar date than the entry. Carries gap risk. |
| **PnL (Profit and Loss)** | Net financial result of trading activity. See *Gross PnL* + *Net PnL*. |
| **Profit Day Rate** | Proportion of trading days on which a trader's daily net PnL is positive. |
| **Profit Factor** | Ratio of total gross profit (sum of winning trades) to total gross loss (absolute sum of losing trades). > 1.0 = net profitable; > 1.5 good; > 2.0 excellent. |
| **Return on Capital (ROC)** | Net PnL divided by allocated capital baseline, expressed as a percentage. Normalises PnL across traders with different capital allocations. |
| **Sharpe Ratio** | Risk-adjusted performance: mean daily net PnL / standard deviation of daily net PnL (with RF = 0). Annualised by `× sqrt(252)`. ≥ 1.0 acceptable; ≥ 2.0 excellent. |
| **Slippage** | Difference between expected and actual execution price. In TCP's synthetic system, modelled rather than directly observed. |
| **Sortino Ratio** | Sharpe variant that penalises only **downside** volatility (standard deviation of negative daily returns). Preferred for strategies with occasional large wins and controlled losses. |
| **Swing Trade** | A trade held open for more than one trading day. |
| **Team-Lead PnL Multiplier** | Ratio comparing the average net PnL of a team's traders (excluding the team lead) to the average net PnL of all traders on the same floor. > 1.0 = the team lead's team outperforms the floor average. |
| **Trade UID** | Unique identifier for a trade: `T<YYYYMMDD>-<NNNN>` (e.g., `T20260514-0001`). Date is the Europe/Bucharest local date. |
| **Value at Risk (VaR)** | Statistical measure of potential loss at a given confidence level over a defined period. TCP uses historical VaR at 95 % = 5th percentile of daily net PnL. |
| **Weekend Carry** | A trade open over Saturday or Sunday. Higher gap risk due to multi-day market closure. |
| **Win Rate** | Proportion of trades with positive net PnL. Always interpreted alongside average win vs. average loss size. |
| **YTD (Year-to-Date)** | Period from the first calendar day of the current year through today. |

---

## 2. Database & data engineering

| Term | Definition |
|---|---|
| **BACPAC** | A self-contained `.bacpac` file holding both schema and data for an Azure SQL or SQL Server database. Used in TCP for weekly archival snapshots (ADR-004). |
| **config_*** | Configuration table — slowly-changing settings with effective-date semantics. Naming: `config_PascalCase` (`config_Capital`). The third table-prefix the CI naming-convention check enforces alongside `dim_*` and `fact_*`. |
| **DACPAC** | Data-Tier Application Package. Captures only the schema. Not used in TCP v1.0. |
| **DATETIMEOFFSET** | SQL Server data type storing a datetime plus timezone offset. Used for every `time_entry` / `time_exit` / `created_at` column with `Europe/Bucharest` offset (+02:00 EET / +03:00 EEST). |
| **dim_*** | Dimension table — descriptive attributes joined to facts. Naming: `dim_PascalCase` (`dim_Employees`, `dim_TradingFloors`). |
| **DTU** | Database Transaction Unit. A blended measure of CPU + memory + I/O in Azure SQL's DTU-based tiers. TCP uses vCore-based Serverless instead. |
| **fact_*** | Fact table — append-only event records with numeric measures. Naming: `fact_PascalCase` (`fact_Trades`, `fact_DailyTraderPnL`). |
| **MERGE** | T-SQL upsert statement combining INSERT, UPDATE, and DELETE in a single atomic operation. Used in `usp_GenerateDailyTrades` for `fact_DailyTraderPnL` aggregation (ADR-002) and in `schema_history` for the checksum upsert (Etapa 8). |
| **NCRONTAB** | Cron expression format used by Azure Functions Timer Triggers. Six fields (second-precision): `{sec} {min} {hour} {day} {month} {weekday}`. Example: `0 0 7 * * 1-5` = 07:00:00 Mon-Fri. |
| **PBIR (PowerBI Report)** | Text-based, git-friendly format for PowerBI report definitions. Used in [`powerbi/report/`](../powerbi/report/) to version-control the page skeleton; final visual layout is done in PowerBI Desktop per [`powerbi/README.md`](../powerbi/README.md) Known Limitations. Sibling format to TMDL. |
| **PITR (Point-in-Time Restore)** | Azure SQL automated backup capability allowing restore to any second within the retention window (7 days on the Free Offer). TCP's primary DR mechanism. |
| **RLS (Row-Level Security)** | SQL Server feature filtering rows visible to a connection based on a predicate function. In TCP, the predicate joins `dim_UserRoles` on `SESSION_CONTEXT('aad_object_id')` to resolve the caller's scope (ADR-003). |
| **RPO (Recovery Point Objective)** | Maximum acceptable data loss in time terms after a failure. TCP target: ≤ 1 hour (PITR). |
| **RTO (Recovery Time Objective)** | Maximum acceptable time to restore service. TCP target: ≤ 30 minutes for PITR; ≤ 4 hours for BACPAC restore. |
| **SCD1 (Slowly Changing Dimension Type 1)** | Strategy where a dimension row is overwritten in place when an attribute changes. No history. Used for most TCP dimensions. |
| **SCD2 (Slowly Changing Dimension Type 2)** | Strategy where attribute changes insert a new row with effective-date columns; full history preserved. Not used in v1.0. |
| **SCHEMABINDING** | T-SQL view/function modifier that pins the schema of referenced objects so they cannot be ALTERed underneath the view. Used on every TCP `v_*` view. |
| **schema_history** | Migration ledger table (`dbo.schema_history`) keyed by file name. Stores `applied_at_utc` + canonicalised SHA-256 `checksum`. CI and CD gates assert no unsubstituted placeholder. |
| **SESSION_CONTEXT** | SQL Server per-connection key-value store. TCP writes `aad_object_id` here on every check-out; RLS predicates read it. See ADR-003. |
| **sqlcmd** | Microsoft's CLI for SQL Server (`-G` for AAD auth, `-i` for input file, `-b` for fail-fast). Used by every migration apply path. |
| **Star Schema** | Database pattern with a central fact table surrounded by dimension tables. TCP's `fact_Trades` is the central fact; ten `dim_*` tables surround it. |
| **TMDL (Tabular Model Definition Language)** | Text-based, git-friendly format for PowerBI / Analysis Services tabular models. Used throughout [`powerbi/model/`](../powerbi/model/) to version-control 20 TMDL files (database header + model parameters + 15 tables + relationships + roles + ro-RO culture). Deployed via the PowerBI REST API per ADR-001. |
| **TVF (Table-Valued Function)** | SQL Server function returning a row set. TCP uses `tvf_RiskMetrics` (returns Sharpe/Sortino/VaR per trader/window) — name prefix `tvf_` per naming convention. |
| **TVP (Table-Valued Parameter)** | SQL Server feature passing a table as a parameter to a proc or function. Considered for trade bulk-load but currently the synth generator uses an OPENJSON pattern. |
| **vCore-second** | Unit of compute consumption in Azure SQL Serverless. Free Offer grant: 100 000 vCore-seconds / month. One vCore × one second = one vCore-second. |
| **v_*** | View (snake_case suffix) — derived artefact, not a source-of-truth table. Examples: `v_trades_enriched`, `v_daily_pnl`, `v_employee_performance`. |

---

## 3. Azure & infrastructure

| Term | Definition |
|---|---|
| **AAD (Azure Active Directory)** | Microsoft's cloud identity service. TCP uses AAD-only authentication post-bootstrap for SQL, Functions, and SWA. |
| **AAD-only authentication** | SQL Server mode where only AAD principals can connect (no SQL-auth). TCP flips to AAD-only in postprovision Step 3. |
| **App Insights (Application Insights)** | Azure's APM service. TCP runs workspace-based App Insights routing all telemetry to the shared Log Analytics workspace. |
| **App Service Plan** | Container that hosts Azure Web Apps and Function Apps. TCP uses a Y1 (Consumption) plan — pay-per-execution with a 1M-executions/month free grant. |
| **azd (Azure Developer CLI)** | Open-source CLI from Microsoft for the build → provision → deploy → monitor lifecycle. TCP's `azure.yaml` defines two services (`api` → Functions, `web` → SWA). |
| **Bicep** | Azure-native DSL compiling to ARM templates. Used for all TCP infrastructure-as-code under `infra/`. |
| **Consumption plan** | See *Y1*. |
| **DefaultAzureCredential** | Python SDK convenience that walks a chain of authentication strategies (env vars → MI → Visual Studio → CLI). TCP uses it in `bacpac_export.py` so the same code works locally + in Functions. |
| **Function App** | Azure compute primitive hosting serverless functions on a triggered model. TCP's single Function App hosts 5 triggers (daily generator, warmup, BACPAC export, ping, ask). |
| **Key Vault (KV)** | Azure secret store. TCP stores `ANTHROPIC-API-KEY`, `SQL-ADMIN-PASSWORD-EXPORT`, `SWA-FORWARDED-SECRET`, `STORAGE-CONNECTION-STRING`. Function App reads via KV reference syntax. |
| **Log Analytics workspace** | Underlying store for App Insights telemetry + Azure Monitor logs + AzureMetrics. TCP uses one workspace named `log-tcp-prod-weu` with a 0.5 GB/day cap. |
| **Managed Identity (MI)** | AAD identity automatically managed by Azure for a resource. TCP's Function App has a *system-assigned* MI used as the SQL principal, the KV consumer, and the Storage Blob writer. |
| **OIDC (OpenID Connect) federation** | Authentication flow where GitHub Actions exchanges a workflow-run JWT for an Azure access token via an AAD app's federated credential. No static secrets are stored in GitHub. TCP uses OIDC for `ci.yml` and `cd.yml`. |
| **Static Web App (SWA)** | Azure service for static-site hosting with built-in AAD auth + a "linked backend" proxy. TCP's SWA hosts `swa/` and proxies `/api/*` to the Function App. |
| **vCore Serverless** | Azure SQL pricing model billing per second of compute usage with auto-pause after a configurable idle window. TCP uses the Free Offer variant (100 000 vCore-seconds + 32 GB included forever). |
| **WEBSITE_TIME_ZONE** | Function App setting interpreting NCRONTAB expressions in a specific timezone. TCP sets `E. Europe Standard Time` so all timers fire in Europe/Bucharest with DST handled automatically. |
| **Y1 (Consumption plan)** | The cheapest Functions hosting tier. 1M executions + 400 000 GB-seconds / month free. Cold starts in the 1-3 s range. TCP's sole Functions plan. |

---

## 4. Observability & reliability

| Term | Definition |
|---|---|
| **Alert rule** | Azure Monitor object that evaluates a condition periodically and notifies an action group on match. TCP defines 8 in [`infra/modules/alerts.bicep`](../infra/modules/alerts.bicep). |
| **Burn rate** | Speed at which an SLI consumes its monthly error budget. Defined as `observed_error_rate / SLO_error_budget`. A 5× burn against a 1 % monthly budget exhausts the budget in ~6 days. |
| **customDimensions** | Free-form key-value store on every App Insights telemetry row. TCP emits all `tcp.*` events as structlog calls; their kwargs land here. |
| **customMetrics** | The dedicated metric table in App Insights (faster than `traces`-table reads). RR-06 acknowledges TCP currently emits metrics as `traces.customDimensions` events; migration deferred to Etapa 12. |
| **Error budget** | The (`1 - SLO`) × volume of valid events. For a 99 % SLO on a system that handles 100 events/month, the error budget is 1 event. |
| **KQL (Kusto Query Language)** | Query language for Azure Monitor / App Insights / Log Analytics. TCP's canonical queries live in [`infra/observability/kusto/*.kql`](../infra/observability/kusto/). |
| **Notification email** | Email recipient routed via an action group. TCP's `notificationEmails` parameter on `alerts.bicep` defaults to `[]` — alerts still fire to the portal but do not page. |
| **operation_Name** | App Insights field auto-populated by the Functions Python v2 runtime with the function name. TCP filters on `operation_Name == "ask"` and `operation_Name == "daily_generator"`. |
| **scheduledQueryRules** | Azure Monitor resource type running a KQL query on a schedule and alerting when the result crosses a threshold. TCP uses `Microsoft.Insights/scheduledQueryRules@2023-12-01` for 7 of its 8 alerts. |
| **SLI (Service Level Indicator)** | A measurable signal of service quality, typically a ratio of good events to valid events. TCP defines 3 SLIs (availability, generator success, p95 latency). |
| **SLO (Service Level Objective)** | The target value for an SLI over a window (e.g., "≥ 99 % SLI-1 over 30 days"). TCP's SLO doc: [`docs/observability/slo.md`](observability/slo.md). |
| **structlog** | Structured logging library used in `tcp/`, `function_app/`, and `scripts/`. Emits `event=<name> key1=v1 key2=v2` lines that land in `traces.customDimensions`. |
| **traces table** | The catch-all App Insights table for log-style telemetry. TCP queries it for `tcp.ask.audit`, `tcp.func.ask.*`, `tcp.bacpac.*`, etc. |
| **Workbook** | Azure Monitor's dashboard primitive. TCP deploys one workbook (`infra/observability/workbook.json`) with 9 tiles mirroring the `.kql` library. |

---

## 5. Security & compliance

| Term | Definition |
|---|---|
| **aad_object_id (oid)** | The immutable AAD GUID identifying a user. TCP's RLS contract pins identity to this value (ADR-003); never to UPN, display name, or email. |
| **Bootstrap window** | The 3-8 minute period during `azd up` between Step 0 (schema apply with SQL-auth alive) and Step 3 (AAD-only flip). Documented as RR-08 in the threat model; full operator runbook in [`docs/security/bootstrap_window.md`](security/bootstrap_window.md). |
| **DefaultAction (KV)** | Network ACL default. TCP's KV ships with `defaultAction: 'Allow'` because the Y1 plan has dynamic outbound IPs — RR-02. Tightening requires a Premium plan. |
| **GDPR** | EU General Data Protection Regulation. TCP's synthetic data has no real personal data, but the design includes a future-hook procedure `usp_DeleteEmployeeData` for right-to-erasure. |
| **gitleaks** | Secret-scanning tool. Runs in `ci.yml › secret-scan` job on every PR. |
| **incident response** | The set of procedures for handling production incidents. TCP defines 4 severities (P0..P3) and 6 named scenarios in [`docs/security/incident_response.md`](security/incident_response.md). |
| **OWASP Top 10** | A widely-used list of web-app security risks (A01..A10). TCP's posture per A01..A10 is in [`docs/security/threat_model.md`](security/threat_model.md) §8. |
| **PII (Personally Identifiable Information)** | Data that identifies a specific person. TCP's posture: zero real PII (Faker-generated); telemetry logs only the last 8 hex chars of `aad_object_id` (`oid_suffix`); raw question text is hashed to SHA-256 before logging. Enforced by `tests/integration/test_telemetry_no_pii.py`. |
| **RBAC (Role-Based Access Control)** | Permission model assigning roles to principals. TCP's RBAC matrix is documented in [`docs/design/03_architecture.md`](design/03_architecture.md) §5. |
| **Residual risk (RR)** | A risk that has been considered, partially mitigated, and accepted for a documented reason. TCP's RR-01..RR-09 live in the threat model §7. RR-09 was closed in Etapa 8. |
| **Service Principal (SP)** | AAD application identity for automated tooling. Distinct from Managed Identity — an SP requires secret/cert management. TCP uses an SP for GitHub Actions OIDC and PowerBI Service deployment. |
| **STRIDE** | Threat-modelling taxonomy: Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege. TCP's STRIDE matrix covers 11 surfaces × 6 categories in the threat model §3. |
| **safe_query** | The TCP module (`tcp/safe_query.py`) that validates LLM-emitted SQL against an allowlist + deny-list before execution. The third independent gate after Anthropic's own safety + AST re-serialisation. |
| **SWA forwarded secret** | Shared secret injected by the Static Web App's `forwardingGateway.requiredHeaders` and validated by the Function App. Stops a caller from probing the raw Function URL with a well-formed principal blob. |
| **Threat model** | Document enumerating threats by STRIDE category, mapping to OWASP Top 10, and listing residual risks. TCP's threat model: [`docs/security/threat_model.md`](security/threat_model.md). |
| **X-SWA-Forwarded** | The header name that carries the SWA forwarded secret. Validated FIRST in `function_app/triggers/ask.py` (ahead of even principal parsing). |

---

## 6. Project conventions

| Term | Definition |
|---|---|
| **ADR (Architecture Decision Record)** | Short markdown record of a load-bearing design decision. TCP's ADRs live under [`docs/decisions/ADR-NNN-*.md`](decisions/). Index: [`docs/decisions/INDEX.md`](decisions/INDEX.md). |
| **Conventional Commits** | The commit-message standard TCP follows: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `build:`, `ci:`. See `CLAUDE.md` § Git. |
| **Etapa (singular) / Etape (plural)** | Romanian for "stage". TCP's build plan has 13 etape (Etapa 0 through Etapa 13) tracked in `.claude/STATE.md`. Each etapa closes with a multi-agent review + a convergence pass. |
| **Multi-agent review** | The TCP quality bar: at every major stage, 2-4 specialised Claude Code subagents (e.g., `database-architect`, `code-reviewer`, `security-auditor`) review the deliverables independently. Findings are reconciled in a convergence pass. Reports live under [`docs/design/reviews/`](design/reviews/). |
| **Multi-agent verification** | Synonym for *multi-agent review*. |
| **placeholder (TODO)** | Intentionally-deferred value (author name, advisor name, screenshots, license terms). All placeholders are inventoried in [`docs/PLACEHOLDERS.md`](PLACEHOLDERS.md) and resolved at thesis submission — never edited mid-build. |
| **STATE.md** | `.claude/STATE.md` — single source of truth for session continuity. Read first when resuming the project; updated at every safe stop point. |
| **uv** | Astral's modern Python package manager. Replaces `pip + venv + requirements.txt`. TCP uses `uv sync` for environment setup; `uv run` for command execution. |
| **V001 / V002 / ...** | Forward migration scripts under `db/migrations/`. Numbered, never edited after merge. Each records itself in `dbo.schema_history` with a canonicalised SHA-256 checksum (RR-09 closure). |

---

## Term-finding tip

If a term appears in code or a doc and is not in this glossary, the canonical source is usually:

- KPI terms → [`docs/design/01_business_requirements.md`](design/01_business_requirements.md) §4
- Schema terms → [`docs/design/02_database_design.md`](design/02_database_design.md)
- Azure-resource terms → [`docs/design/03_architecture.md`](design/03_architecture.md) §4
- Threat / risk terms → [`docs/security/threat_model.md`](security/threat_model.md) §3 + §7
- SLI / SLO terms → [`docs/observability/slo.md`](observability/slo.md) §2

If you find a term that *should* be here, add it in a `docs: expand glossary` PR.
