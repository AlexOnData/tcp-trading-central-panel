# Observability — operator's guide

> **Component scope.** This document is the observability operator index. For project-wide context, deploy walkthrough, and troubleshooting, see the [top-level README](../../README.md). For SLI/SLO definitions: [`slo.md`](slo.md). For glossary: [`../glossary.md`](../glossary.md).

Etapa 8 of the TCP Trading Central Panel build adds a complete observability
surface on top of the existing App Insights + Log Analytics wiring. This
document is the index to that surface — every artefact lives somewhere in
the repository and is linked from here so an operator can move quickly from
"the dashboard says X" to "the source query / alert / SLI is here".

## TL;DR — where to look

| Need | Open this |
|---|---|
| The single dashboard view of everything | The **TCP — Operations dashboard** workbook in Azure Portal → `Monitor → Workbooks → Recent`. Bicep-deployed from [`infra/observability/workbook.json`](../../infra/observability/workbook.json). |
| The canonical query for a metric | [`infra/observability/kusto/`](../../infra/observability/kusto/) — one .kql per query, mirrored into the workbook. |
| Why an alert fired | The alert name maps 1:1 to a section in [`slo.md`](slo.md) §4, which links to the source .kql query. |
| The SLO target / error-budget math | [`slo.md`](slo.md) §2 (SLIs) + §3 (budgets). |
| The threat-model justification for PII redaction | [`docs/security/threat_model.md`](../security/threat_model.md) §S05 + the enforcement test [`tests/integration/test_telemetry_no_pii.py`](../../tests/integration/test_telemetry_no_pii.py). |
| The migration ledger integrity story | [`scripts/compute_migration_checksum.py`](../../scripts/compute_migration_checksum.py) + [`infra/scripts/postprovision.ps1`](../../infra/scripts/postprovision.ps1) Step 0. |

## Surface inventory

### 1. Workbook — `infra/observability/workbook.json`

A single Azure Monitor workbook that surfaces every SLI/SLO panel in one
place. Sections:

1. **SLI-1 latency** — p50/p95/p99 timechart over the selected time range.
2. **SLI-1 errors** — error rate per `operation_Name`, sorted descending.
3. **SLI-2 generator** — daily success/failure table, Mon–Fri only.
4. **Anthropic spend** — daily token consumption + EUR projection.
5. **Cold starts** — Python worker init duration buckets (warm / lukewarm / cold).
6. **SQL vCore** — Free-Offer monthly grant burn vs 100 000 cap.
7. **BACPAC** — weekly export status with 7-day buckets.
8. **Rate-limit refusals** — 429 returns per hour with distinct OID-suffix count.
9. **Question fingerprints** — last 50 distinct SHA-256 question hashes (audit only).

The workbook is deployed via [`infra/modules/workbook.bicep`](../../infra/modules/workbook.bicep)
as a `Microsoft.Insights/workbooks` resource. To preview locally without a
full `azd provision`, paste the JSON into Azure Portal → Monitor → Workbooks
→ New → Advanced Editor.

### 2. Kusto query library — `infra/observability/kusto/`

| File | Purpose | Workbook tile |
|---|---|---|
| `01_ask_latency_percentiles.kql` | `/api/ask` p50/p95/p99 latency | "Assistant latency p50 / p95 / p99" |
| `02_daily_generator_outcomes.kql` | Generator runs / successes / failures | "Daily generator runs vs failures" |
| `03_anthropic_tokens_and_cost.kql` | Token spend + EUR projection | "Anthropic tokens + EUR estimate" |
| `04_function_cold_starts.kql` | Worker startup distribution | "Cold vs warm worker starts" |
| `05_sql_vcore_consumption.kql` | Free-Offer monthly burn | "SQL vCore-second consumption" |
| `06_error_rate_by_operation.kql` | Error rate per operation | "Error rate by operation" |
| `07_ask_question_audit.kql` | PII-redacted question fingerprints | "Last 50 distinct question fingerprints" |
| `08_bacpac_export_health.kql` | Weekly BACPAC status (ADR-004) | "BACPAC weekly health" |
| `09_rate_limit_hits.kql` | 429 refusals per hour | "Rate-limit refusals" |

Editing protocol: when a query changes in the workbook (Azure Portal →
Edit → Advanced Editor), **mirror the change back into the .kql file in the
same PR**. A drift between the file and the deployed workbook is a deployment
hazard — the next `azd deploy` would silently undo the edit.

### 3. Alert rules — `infra/modules/alerts.bicep`

Eight rules covering the canonical SLO breaches and cost guardrails. All
log-query rules read App Insights via the workspace-based connection; the
metric alert (#7) targets the SQL database resource id directly.

| Name | Severity | Condition | Source |
|---|---|---|---|
| `tcp-alert-ask-p95-latency` | 2 | p95 > 4 000 ms × 3 windows | Query 01 |
| `tcp-alert-ask-availability-burn` | 1 | error_rate > 5 % AND volume > 5 over 1 h | Query 06 (filtered) |
| `tcp-alert-daily-generator-failed` | 1 | ≥ 1 failure in 24 h | Query 02 |
| `tcp-alert-bacpac-missed` | 2 | no success event in 8 d | Query 08 |
| `tcp-alert-rate-limit-spike` | 3 | > 50 hits/h | Query 09 |
| `tcp-alert-anthropic-cost-burn` | 3 | est_eur > €0.50/day | Query 03 |
| `tcp-alert-sql-cpu-high` | 2 | avg(cpu_percent) > 80 % over 15 min | Metric alert |
| `tcp-alert-sql-quota-burn` | 1 | cumulative > 80 % of 100 000 vCore-s | Query 05 |

Alerts route through the `ag-tcp-prod` action group **only** when at least
one address is configured via `azd env set NOTIFICATION_EMAILS '["…"]'`.
The default empty array is intentional — Etapa-8 ships without a paging
address; the operator inspects the workbook manually until the email
recipient is set.

### 4. SLO definition — `slo.md`

The single source of truth for what counts as a "good" event vs a "bad"
event for each SLI, the rolling-window math, and the burn-rate alerting
thresholds. Read this before approving any change to the alert rules — a
threshold tweak in [`infra/modules/alerts.bicep`](../../infra/modules/alerts.bicep)
without a corresponding update in this doc is a configuration drift.

### 5. Schema-ledger integrity — RR-09

Closes the residual identified in [`docs/security/threat_model.md`](../security/threat_model.md)
RR-09. The `__V<n>_CHECKSUM__` placeholder in each migration is replaced
at apply time with the canonicalised SHA-256 (computed by
[`scripts/compute_migration_checksum.py`](../../scripts/compute_migration_checksum.py)).
The CI gate in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)
re-computes the value on every PR; the CD smoke job
[`.github/workflows/cd.yml`](../../.github/workflows/cd.yml) reads the
post-deploy `dbo.schema_history` table and fails when an unsubstituted
placeholder leaks through.

### 6. PII redaction enforcement — `tests/integration/test_telemetry_no_pii.py`

A self-contained test (no live SQL or Anthropic) that drives the full
`/api/ask` handler chain with a canary OID + canary question text, captures
every structlog event emitted, and asserts:

- The full AAD object id (dashed UUID and 32-char hex form) **never** appears.
- Only the 8-char `oid_suffix` may appear.
- The user's question text **never** appears.

Five paths covered: success, refusal, validator-rejected SQL, unknown
principal, and SQL execution failure. The test ships in the fast unit job
(`uv run pytest tests/unit tests/integration/test_telemetry_no_pii.py`) so
a regression blocks merge.

## Day-2 operations

### "How do I find out why latency spiked at 14:00 RO yesterday?"

1. Open the workbook → set Time range = 4 h, scroll to mid-yesterday.
2. Cross-reference the `Cold vs warm worker starts` panel — sustained cold
   starts during business hours suggest the warmup timer is not firing.
3. Pull the corresponding 5-minute window from `06_error_rate_by_operation.kql`
   to confirm the spike was latency-only (no error-rate bump → SLI-3 only;
   error-rate bump → SLI-1 budget consumed too).
4. If error rate rose, jump to `traces` filtered by `tcp.func.ask.*` to see
   the structured-log dimensions for the failing requests.

### "How do I add a new alert?"

1. Author the .kql file under `infra/observability/kusto/` with the same
   header style (purpose, source line in 03_architecture, target tile).
2. Add a `Microsoft.Insights/scheduledQueryRules` block in
   [`infra/modules/alerts.bicep`](../../infra/modules/alerts.bicep) — mirror
   the `description`, `severity`, `evaluationFrequency`, `windowSize`
   pattern of an existing rule.
3. Document the SLO/SLI rationale in [`slo.md`](slo.md) §4 (which window,
   why this severity, the link to the source query).
4. Update the table in §3 of this README.
5. Push and let `azd deploy` provision the new alert; verify in Azure
   Portal → Monitor → Alerts → Alert rules.

### "How do I rotate the Anthropic key without breaking telemetry?"

The Anthropic call is wrapped in `tcp.ai.anthropic_client.ask_claude`,
which emits `tcp.ask.metrics` events on every successful return. A botched
rotation surfaces as a sustained Anthropic 401 → SLI-1 burn-rate alert
(`tcp-alert-ask-availability-burn`) within 5 minutes, not silently. See
[`docs/security/credentials_rotation.md`](../security/credentials_rotation.md) §1
for the procedure; the verification step there explicitly opens this
workbook to confirm the new key is processing requests.

## Open follow-ups

The full list lives in [`slo.md`](slo.md) §6. The two that the next stage
(Etapa 9 documentation pass) should highlight:

- **Multi-window burn-rate**: switch to a Google-SRE-style 1h + 6h coupled
  detector once the system has 30 days of baseline traffic to tune against.
- **Custom-metrics migration** (RR-06): when `azure-monitor-opentelemetry`
  is wired (Etapa-12 polish), every `traces`-table read in the alert library
  collapses to a `customMetrics` read with ~5× lower query latency.
