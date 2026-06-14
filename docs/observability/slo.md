# Service Level Objectives — TCP Trading Central Panel

**Stage owner**: Etapa 8 (telemetry & observability).
**Status**: Active for the academic phase. Targets are intentionally generous —
TCP runs on Azure free tiers and a 32-employee target population; real
production SLOs would tighten p95 latency and availability by ~10×.

---

## 1. Why SLOs in an academic project

Even at the academic scale, an SLO/SLI loop forces three decisions to be made
explicitly rather than implicitly:

1. **What does "the assistant is up" mean?** A 200 OK with a refusal envelope
   is still a successful request from the platform's perspective; defining the
   SLI surface determines what the alert actually measures.
2. **How much breakage is tolerable before someone investigates?** The error
   budget makes the trade-off visible: hardening below the target SLO is not
   free — it competes with thesis work, polishing PowerBI, and the security
   sweep.
3. **When does paging escalate from "see it in the morning" to "now"?** The
   burn-rate alerts (severity 1 vs 2) encode that escalation decision once,
   instead of forcing a judgement call every time the latency dashboard
   spikes.

The SLOs below are read by the alerts in [`infra/modules/alerts.bicep`](../../infra/modules/alerts.bicep)
and surfaced on the operations workbook in [`infra/observability/workbook.json`](../../infra/observability/workbook.json).

---

## 2. Service Level Indicators

Each SLI is a ratio of **good events** over **valid events** measured from
Application Insights telemetry over a rolling 30-day window. The denominators
are deliberately small (a single user, ~30 requests/day) so SLI-1 is highly
sensitive — one failure already moves the dial by ~3 percentage points.

### SLI-1 — Assistant availability

| Field | Value |
|---|---|
| Description | Fraction of `/api/ask` requests that completed end-to-end without an internal error. |
| Source | `requests` table, `operation_Name == "ask"`. |
| Good event | `resultCode in ("200", "422", "429")` — 200 success, 422 controlled refusal (model OR validator rejection), 429 documented rate-limit response. |
| Valid event | All `requests` rows for the operation. |
| Bad event | `success == false AND resultCode !in ("422", "429")` — internal handler failures, upstream Anthropic outages, and uncaught exceptions. **The alert query at `infra/modules/alerts.bicep` (askAvailabilityAlert) uses this exact filter** so dashboard + alert + SLI math read the same denominator (obs-MA-05 alignment). |
| Window | Rolling 30 calendar days. |
| Aggregation | `error_rate = bad / valid`; `availability = 1 - error_rate`. |
| Target SLO | **≥ 99.0 %** (≈ 7.2 hours of unavailability per 30-day month). |

### SLI-2 — Daily generator success rate

| Field | Value |
|---|---|
| Description | Fraction of scheduled `TimerTrigger_DailyGenerator` invocations that completed without raising an unhandled exception. |
| Source | `requests` table, `operation_Name == "daily_generator"`. |
| Good event | `success == true` (covers both `status='ok'` and `status='skipped_holiday'` / `status='skipped_non_trading_day'` short-circuits — they are intended outcomes). |
| Valid event | All scheduled invocations on RO business days. |
| Bad event | `success == false` (unhandled exception bubbled up from `tcp.synth.run_daily`). |
| Window | Rolling 30 calendar days (≈ 21 trading days per month). |
| Target SLO | **≥ 99 %** (≤ 1 failure per 30-day window). |

### SLI-3 — Assistant latency (p95)

| Field | Value |
|---|---|
| Description | The 95th percentile end-to-end duration of successful `/api/ask` requests. |
| Source | `requests` table, `operation_Name == "ask"`, `success == true`. |
| Aggregation | `p95 = percentile(duration, 95)` over a 1-hour window. |
| Target SLO | **p95 ≤ 3 000 ms** during the 09:00–18:00 RO business window. |
| Latency budget breakdown (warm path) | Anthropic call ~1 200 ms · safe_query validation ~50 ms · SQL execution ~300 ms · network + serialisation ~150 ms · headroom 1 300 ms. |

Latency is treated as an SLI but **not an availability SLI** — slow-but-correct
responses do not consume the SLI-1 error budget. They do trigger the
`tcp-alert-ask-p95-latency` alert (severity 2) when sustained.

### Cost guardrail SLIs (informational, not paged)

| Indicator | Source | Target |
|---|---|---|
| Anthropic spend (rolling 30 d, EUR) | Kusto query 03 + 03_architecture.md §10 pricing. | Daily projection ≤ €0.50/day (≈ €15/month at sustained burn). |
| SQL Free-Offer vCore-second consumption | `AzureMetrics` `app_cpu_billed`. | ≤ 80 % of 100 000 vCore-seconds before month end. |

These are wired into `tcp-alert-anthropic-cost-burn` and
`tcp-alert-sql-quota-burn` because exhaustion has hard operational consequences
(API key throttling, database auto-pause for the rest of the month), even
though they are not strictly availability SLIs.

---

## 3. Error budgets

| SLI | Target | 30-day budget | What 1 % of "valid" looks like |
|---|---|---|---|
| SLI-1 (availability) | 99.0 % | 1.0 % of requests | At ~30 req/day average → budget = 9 failed requests/month. One bad model release can burn this in minutes. |
| SLI-2 (generator) | 99.0 % | 1 of ~21 invocations | A single failed weekday + the next-day re-run still inside budget. |
| SLI-3 (p95 latency) | n/a (informational SLI) | n/a | Used to prevent gradual regression; budget enforcement deferred until a paid SQL tier removes the cold-pause variable. |

Error-budget policy:

- When **>50 %** of the SLI-1 budget is consumed, freeze any change that
  affects the assistant pipeline (Anthropic prompt edits, `safe_query` rule
  changes, RLS predicate changes) until budget recovers or until a deliberate
  decision overrides — recorded in an ADR.
- When **>90 %** of the SLI-1 budget is consumed, the only allowed merges are
  reliability fixes that target the failing path. Tracked in
  `docs/decisions/ADR-008-error-budget-policy.md` (future ADR; placeholder
  until first breach).

---

## 4. Burn-rate alerts

The Etapa-8 alert rules implement a **single-window** burn-rate detector
(severity 1) plus a **slow-burn** percentile breach detector (severity 2).
A future hardening pass can layer a multi-window / multi-burn-rate detector
(per the Google SRE workbook) once 30 days of telemetry exist for tuning.

### 4.1 Worked burn-rate example

The Google SRE workbook defines **burn rate** as `observed_error_rate / SLO_error_budget`. For SLI-1 with a 99 % SLO over 30 days, the budget is `1 - 0.99 = 0.01` (1 %). The Etapa-8 burn alert fires when the 1-hour error rate exceeds **5 %**:

```
burn_rate = observed / target = 0.05 / 0.01 = 5×
```

A 5× burn sustained for 24 h consumes `0.05 × 24 / (0.01 × 24 × 30) = 16.6 %` of the 30-day budget. Full exhaustion happens at `30 / 5 = 6 days` of sustained breach. The threshold is intentionally aggressive for the low-volume academic system (avg ~30 req/day → a small absolute number of failures is statistically meaningful); revisit once 30 days of baseline traffic exist and switch to a Google-SRE 14.4×/6× multi-window detector (see §6 item 1).

| Alert | Source query | Severity | Window | Trigger | Why this rate |
|---|---|---|---|---|---|
| `tcp-alert-ask-availability-burn` | 06_error_rate_by_operation.kql (filtered) | 1 (page) | 1 h | error_rate > 5 % AND volume > 5 (after excluding controlled 422 + 429 responses per SLI-1 "good event" definition) | 5× burn against the 1 %/30-day budget — exhausts the monthly budget in ~6 days if sustained. Tuned aggressive for low traffic volume. |
| `tcp-alert-ask-p95-latency` | 01_ask_latency_percentiles.kql | 2 (notify) | 15 min × 3 evals | p95 > 4 000 ms three windows in a row | Three 5-min evaluation windows above threshold = sustained user-visible regression, not a single cold-start outlier. |
| `tcp-alert-daily-generator-failed` | 02_daily_generator_outcomes.kql | 1 (page) | 24 h | ≥ 1 failure | 24 h covers the next morning's run. Single failure = SLI-2 budget consumed. |
| `tcp-alert-bacpac-missed` | 08_bacpac_export_health.kql | 2 (notify) | 8 d look-back | No success in 8 days | Sunday cadence + 24 h grace; ADR-004 §"Why Sunday 08:00 RO". |
| `tcp-alert-rate-limit-spike` | 09_rate_limit_hits.kql | 3 (informational) | 1 h | > 50 hits/h | Above the noise floor for 32 employees × 10 req/min budget. |
| `tcp-alert-anthropic-cost-burn` | 03_anthropic_tokens_and_cost.kql | 3 (informational) | 1 d | est_eur > €0.50/day | Catches accidental loops and prompt-cache misses before the monthly bill spikes. |
| `tcp-alert-sql-cpu-high` | n/a (metric alert on `cpu_percent`) | 2 (notify) | 15 min | avg(cpu_percent) > 80 % | Sustained CPU burn correlates with vCore-second exhaustion (see SLI-quota guardrail below). |
| `tcp-alert-sql-quota-burn` | 05_sql_vcore_consumption.kql | 1 (page) | month-to-date | cumulative > 80 % of 100 000 | Exhaustion pauses the database for the rest of the month — production-down for the academic system. |

All alerts route through the `ag-tcp-prod` action group when the
`NOTIFICATION_EMAILS` `azd` env var is non-empty. When empty, alerts still
fire and surface in the Azure portal — the canonical academic-phase posture
(no email pager configured by default; the user opens the dashboard
manually).

---

## 5. Reporting cadence

- **Weekly (informal)**: open the workbook, screenshot the latency chart and
  the daily-generator panel, drop them into the project log if anything
  surprised. Targeted at thesis defence preparation, not formal SRE review.
- **Monthly**: at the start of every month, archive a snapshot of the
  rolling 30-day SLI-1 / SLI-2 numbers in `docs/observability/slo_history.md`
  (created on first capture). Include any error-budget consumption above
  50 %, with the underlying root cause.
- **End of academic year**: the SLI-1 / SLI-2 figures are appended to the
  thesis evaluation chapter as the empirical evidence of operational quality.

---

## 6. Open questions and future work

1. **Multi-window burn-rate**: the current single-window detector trades
   precision for recall. Once 30 days of baseline traffic exist, switch to
   a 1-hour + 6-hour AND-coupled detector (Google SRE workbook chapter 5).
2. **Custom metrics migration** (RR-06): when `azure-monitor-opentelemetry`
   is wired (Etapa-12 polish), replace the `traces`-table queries in
   alerts 1, 2, 5, 6, 8 with the equivalent `customMetrics` reads — the
   query latency drops by ~5× and the cost-burn alert becomes near-realtime.
3. **Synthetic probe**: add a Bicep `Microsoft.Insights/webtests` ping against
   `/api/ping` from two regions to detect SWA / Function-host outages
   independent of user traffic. Free for 5 tests/month.
4. **Anthropic 5xx exclusion**: SLI-1 currently does not distinguish a
   transient Anthropic outage from a TCP bug. The denominator-side filter
   (`resultCode != 503` for upstream-only failures) is wired but has not
   been validated against a real Anthropic outage. Re-validate at first
   real incident and tighten the filter in an ADR if needed.
