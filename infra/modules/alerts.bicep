// =============================================================================
// Azure Monitor alert rules — Etapa 8 observability surface.
//
// Provisions seven log-query alert rules + one metric alert covering the
// canonical SLI/SLO breaches and cost / quota guardrails enumerated in
// `docs/observability/slo.md`:
//
//   1. tcp-alert-ask-p95-latency          (log query, severity 2)
//   2. tcp-alert-ask-availability-burn    (log query, severity 1; multi-window
//                                          burn-rate per the SLO doc)
//   3. tcp-alert-daily-generator-failed   (log query, severity 1)
//   4. tcp-alert-bacpac-missed            (log query, severity 2)
//   5. tcp-alert-rate-limit-spike         (log query, severity 3)
//   6. tcp-alert-anthropic-cost-burn      (log query, severity 3)
//   7. tcp-alert-sql-cpu-high             (metric alert, severity 2)
//   8. tcp-alert-sql-quota-burn           (log query, severity 1; vCore-second
//                                          burn vs Free-Offer monthly grant)
//
// All log-query rules read App Insights via the workspace-based connection;
// the metric alert (#7) targets the SQL database resource id directly.
//
// Action group: an email action group is provisioned only when at least one
// notification address is supplied. When `notificationEmails` is empty (the
// default during bootstrap) the alerts still fire and are visible in the
// Azure portal — the actions block is **omitted entirely** rather than left
// as `[]`, because ARM validation rejects an empty array on both schemas
// (arch-CR-01 + arch-CR-02 from the Etapa-8 cloud-architect review). The
// README documents the `azd env set NOTIFICATION_EMAILS '["a@example.com"]'`
// step required to enable paging.
// =============================================================================

targetScope = 'resourceGroup'

@description('Azure region; matches the parent resource group.')
param location string

@description('Tags applied to every alert rule and the action group.')
param tags object

@description('Resource id of the workspace-based App Insights component (alert scope for log-query rules).')
param appInsightsId string

@description('Resource id of the Log Analytics workspace (alert scope for AzureMetrics-driven log queries — e.g. SQL vCore-seconds).')
param logAnalyticsWorkspaceId string

@description('Resource id of the Azure SQL Database (target for the metric alert on CPU).')
param sqlDatabaseId string

@description('Email recipients for the alert action group. When empty no action group is created and alerts fire silently into the portal log. Set via: azd env set NOTIFICATION_EMAILS ["alex@example.com"]')
param notificationEmails array = []

@description('Threshold in milliseconds for the assistant-latency alert. Defaults to 4 000 ms (1 000 ms above the SLO p95 target — matches the burn-rate sensitivity tuned in the SLO doc).')
param askLatencyP95ThresholdMs int = 4000

@description('Hourly threshold for rate-limit refusals before an alert fires. Defaults to 50 hits/h. Rationale: 32 employees × 10 req/min budget = 19 200/h theoretical ceiling, so 50 keeps signal-to-noise high without false positives during normal usage.')
param rateLimitHitsPerHourThreshold int = 50

@description('Threshold percent of the monthly Free-Offer SQL vCore-second grant before the quota-burn alert fires. Defaults to 80 %.')
param sqlQuotaBurnPctThreshold int = 80

@description('Daily Anthropic spend (EUR cents) above which the cost-burn alert fires. Defaults to 50 (= €0.50/day ≈ €15/month at sustained burn). Typed as an int so a localised decimal-comma string cannot silently break the KQL parse (obs-MA-04 + arch-mi-04).')
param anthropicDailyBudgetEurCents int = 50

@description('Severity-2 SQL CPU threshold for the metric alert. Default 80 %.')
param sqlCpuPctThreshold int = 80

// -----------------------------------------------------------------------------
// Action group (email) — provisioned only when at least one address is supplied.
// `2023-01-01` is the GA API version per arch-mi-05.
// -----------------------------------------------------------------------------

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = if (!empty(notificationEmails)) {
  // Action groups are global resources; the canonical region for a global
  // resource is `global`. Tags propagate so cost reporting stays consistent.
  name: 'ag-tcp-prod'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'tcpops'
    enabled: true
    emailReceivers: [for (email, idx) in notificationEmails: {
      name: 'email-${idx}'
      emailAddress: email
      useCommonAlertSchema: true
    }]
  }
}

// Single source of truth for the "actions" property shape on every SQR + the
// metric alert. When no recipients are configured, the property collapses to
// the empty object / array literal — and the resources below conditionally
// drop the `actions` field entirely via `union(baseProps, …)` (CR-01 + CR-02).
// Centralising the shape here resolves arch-MA-05 (DRY debt across 8 alerts).
var sqrActionGroupsBlock = empty(notificationEmails) ? {} : {
  actionGroups: [actionGroup.id]
}
var metricAlertActions = empty(notificationEmails) ? [] : [
  {
    actionGroupId: actionGroup.id
  }
]

// -----------------------------------------------------------------------------
// 1. /api/ask p95 latency
// -----------------------------------------------------------------------------

resource askLatencyAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'tcp-alert-ask-p95-latency'
  location: location
  tags: tags
  properties: union(
    {
      description: 'Assistant latency: /api/ask p95 exceeds the SLO budget on SUCCESSFUL requests only (obs-MA-02 alignment with slo.md SLI-3). The volume gate `samples > 5` (obs-MI-03) prevents a single cold-path outlier from tripping the alert. Source query: infra/observability/kusto/01_ask_latency_percentiles.kql.'
      severity: 2
      enabled: true
      evaluationFrequency: 'PT5M'
      windowSize: 'PT15M'
      scopes: [appInsightsId]
      criteria: {
        allOf: [
          {
            query: 'requests | where operation_Name == "ask" | where success == true | summarize p95 = percentile(duration, 95), samples = count() | where p95 > ${askLatencyP95ThresholdMs} and samples > 5'
            timeAggregation: 'Count'
            operator: 'GreaterThan'
            threshold: 0
            // Azure SQR engine requires numberOfEvaluationPeriods=1 when the
            // query does not project a `timestamp` column (we summarize without
            // a time bin). Switching to 1 means a single 15-min window breach
            // fires the alert; the volume gate `samples > 5` already prevents
            // noise from low-traffic windows.
            failingPeriods: {
              numberOfEvaluationPeriods: 1
              minFailingPeriodsToAlert: 1
            }
          }
        ]
      }
      autoMitigate: true
    },
    empty(notificationEmails) ? {} : { actions: sqrActionGroupsBlock }
  )
}

// -----------------------------------------------------------------------------
// 2. /api/ask availability burn-rate (SLI-1, multi-window per SLO doc)
// -----------------------------------------------------------------------------

resource askAvailabilityAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'tcp-alert-ask-availability-burn'
  location: location
  tags: tags
  properties: union(
    {
      description: 'Assistant availability error budget burning fast. Fires when the 1-hour error rate exceeds 5x the SLO target burn rate (5 % failure over 1h ⇒ 1-month error budget exhausted in ~6 days). 422 (model refusal) and 429 (rate-limit) are CONTROLLED failures per slo.md SLI-1 and are excluded from the numerator (obs-MA-05). See docs/observability/slo.md §4 worked example.'
      severity: 1
      enabled: true
      evaluationFrequency: 'PT5M'
      windowSize: 'PT1H'
      scopes: [appInsightsId]
      criteria: {
        allOf: [
          {
            query: 'requests | where operation_Name == "ask" | extend bad = success == false and resultCode !in ("422", "429") | summarize total = count(), failed = countif(bad) | extend error_rate = todouble(failed) / iif(total == 0, 1, total) | where error_rate > 0.05 and total > 5'
            timeAggregation: 'Count'
            operator: 'GreaterThan'
            threshold: 0
            failingPeriods: {
              numberOfEvaluationPeriods: 1
              minFailingPeriodsToAlert: 1
            }
          }
        ]
      }
      autoMitigate: true
    },
    empty(notificationEmails) ? {} : { actions: sqrActionGroupsBlock }
  )
}

// -----------------------------------------------------------------------------
// 3. Daily generator failure (single failure within the previous 24 h)
// -----------------------------------------------------------------------------

resource generatorFailureAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'tcp-alert-daily-generator-failed'
  location: location
  tags: tags
  properties: union(
    {
      description: 'Daily generator (TimerTrigger_DailyGenerator) failed at least once in the previous 24 hours. Source query: infra/observability/kusto/02_daily_generator_outcomes.kql.'
      severity: 1
      enabled: true
      evaluationFrequency: 'PT1H'
      windowSize: 'PT24H'
      scopes: [appInsightsId]
      criteria: {
        allOf: [
          {
            query: 'requests | where operation_Name == "daily_generator" | where success == false | summarize failures = count() | where failures > 0'
            timeAggregation: 'Count'
            operator: 'GreaterThan'
            threshold: 0
            failingPeriods: {
              numberOfEvaluationPeriods: 1
              minFailingPeriodsToAlert: 1
            }
          }
        ]
      }
      autoMitigate: true
    },
    empty(notificationEmails) ? {} : { actions: sqrActionGroupsBlock }
  )
}

// -----------------------------------------------------------------------------
// 4. BACPAC missed run (no successful complete event in 8 days)
// -----------------------------------------------------------------------------

resource bacpacMissedAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'tcp-alert-bacpac-missed'
  location: location
  tags: tags
  properties: union(
    {
      description: 'BACPAC weekly export (ADR-004) has not produced a `tcp.bacpac.complete` event with status="succeeded" in the previous 8 days. Source query: infra/observability/kusto/08_bacpac_export_health.kql.'
      severity: 2
      enabled: true
      evaluationFrequency: 'PT6H'
      // Azure SQR caps overrideQueryTimeRange at 2880 min (P2D); the original
      // P8D look-back is unsupported by the engine. Reduced to P2D — alert
      // detects a missed Sunday BACPAC ~2 days later (by Tuesday). Longer
      // look-back requires a different mechanism (Storage Analytics blob age
      // or Cost Management) — tracked as follow-up.
      windowSize: 'PT6H'
      overrideQueryTimeRange: 'P2D'
      scopes: [appInsightsId]
      criteria: {
        allOf: [
          {
            query: 'traces | where customDimensions["event"] == "tcp.bacpac.complete" | where tostring(customDimensions["status"]) == "succeeded" | summarize last_success = max(timestamp) | extend stale = iff(isnull(last_success) or last_success < ago(8d), 1, 0) | where stale == 1'
            timeAggregation: 'Count'
            operator: 'GreaterThan'
            threshold: 0
            failingPeriods: {
              numberOfEvaluationPeriods: 1
              minFailingPeriodsToAlert: 1
            }
          }
        ]
      }
      autoMitigate: false
    },
    empty(notificationEmails) ? {} : { actions: sqrActionGroupsBlock }
  )
}

// -----------------------------------------------------------------------------
// 5. Rate-limit hit spike
// -----------------------------------------------------------------------------

resource rateLimitSpikeAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'tcp-alert-rate-limit-spike'
  location: location
  tags: tags
  properties: union(
    {
      description: 'Per-OID 429 refusals on /api/ask exceeded the configured hourly threshold. Suggests credential sharing, an abusive client, or a runaway loop. Source query: infra/observability/kusto/09_rate_limit_hits.kql.'
      severity: 3
      enabled: true
      evaluationFrequency: 'PT15M'
      windowSize: 'PT1H'
      scopes: [appInsightsId]
      criteria: {
        allOf: [
          {
            query: 'traces | where message has "tcp.func.ask.rate_limited" | summarize hits = count() | where hits > ${rateLimitHitsPerHourThreshold}'
            timeAggregation: 'Count'
            operator: 'GreaterThan'
            threshold: 0
            failingPeriods: {
              numberOfEvaluationPeriods: 1
              minFailingPeriodsToAlert: 1
            }
          }
        ]
      }
      autoMitigate: true
    },
    empty(notificationEmails) ? {} : { actions: sqrActionGroupsBlock }
  )
}

// -----------------------------------------------------------------------------
// 6. Anthropic spend daily projection
//
// COUPLING NOTE (Etapa-12, closes obs10-MN-04). The four cost constants below
// (`usd_per_token_input`, `usd_per_token_output`, `usd_per_token_cache_read`,
// `usd_to_eur=0.92`) duplicate the canonical declarations in
// `infra/observability/kusto/03_anthropic_tokens_and_cost.kql`. The duplication
// is structural: ARM's `scheduledQueryRules` rejects multi-line query literals,
// so we cannot `loadTextContent('../observability/kusto/03_...kql')` here. Any
// edit to the constants in the .kql file MUST be mirrored on the alert query
// below in the same commit; the .kql file carries a reciprocal comment.
// -----------------------------------------------------------------------------

resource anthropicCostBurnAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'tcp-alert-anthropic-cost-burn'
  location: location
  tags: tags
  properties: union(
    {
      description: 'Anthropic estimated daily spend exceeded the configured EUR-cent threshold (default 50 = €0.50/day ≈ €15/month). Source query: infra/observability/kusto/03_anthropic_tokens_and_cost.kql; pricing pinned in 03_architecture.md §10.'
      severity: 3
      enabled: true
      evaluationFrequency: 'PT1H'
      windowSize: 'PT6H'
      overrideQueryTimeRange: 'P1D'
      scopes: [appInsightsId]
      criteria: {
        allOf: [
          {
            query: 'let usd_per_token_input=1.0/1000000.0; let usd_per_token_output=5.0/1000000.0; let usd_per_token_cache_read=0.1/1000000.0; let usd_to_eur=0.92; let threshold_eur=${anthropicDailyBudgetEurCents}/100.0; traces | where customDimensions["event"] == "tcp.ask.metrics" | extend in_t=todouble(customDimensions["metric_input_tokens"]), out_t=todouble(customDimensions["metric_output_tokens"]), cr_t=todouble(customDimensions["metric_cache_read_tokens"]) | summarize input_tokens=sum(in_t), output_tokens=sum(out_t), cache_read_tokens=sum(cr_t) | extend est_eur=(input_tokens*usd_per_token_input+output_tokens*usd_per_token_output+cache_read_tokens*usd_per_token_cache_read)*usd_to_eur | where est_eur > threshold_eur'
            timeAggregation: 'Count'
            operator: 'GreaterThan'
            threshold: 0
            failingPeriods: {
              numberOfEvaluationPeriods: 1
              minFailingPeriodsToAlert: 1
            }
          }
        ]
      }
      autoMitigate: true
    },
    empty(notificationEmails) ? {} : { actions: sqrActionGroupsBlock }
  )
}

// -----------------------------------------------------------------------------
// 7. SQL CPU > 80 % (metric alert — direct on the SQL DB resource)
// API: 2018-03-01 GA — 2018-08-01 is no longer in the provider's supported
// list (Azure error: "supported api-versions are 2017-09-01-preview,
// 2018-03-01, 2024-01-01-preview, 2024-03-01-preview").
// -----------------------------------------------------------------------------

resource sqlCpuAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'tcp-alert-sql-cpu-high'
  location: 'global'
  tags: tags
  properties: union(
    {
      description: 'Azure SQL Free-Offer database CPU sustained above the configured threshold for 15 minutes. Repeated breaches will exhaust the monthly vCore-second grant ahead of schedule.'
      severity: 2
      enabled: true
      scopes: [sqlDatabaseId]
      evaluationFrequency: 'PT5M'
      windowSize: 'PT15M'
      targetResourceType: 'Microsoft.Sql/servers/databases'
      targetResourceRegion: location
      criteria: {
        'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
        allOf: [
          {
            name: 'cpu_percent_over_threshold'
            metricNamespace: 'Microsoft.Sql/servers/databases'
            metricName: 'cpu_percent'
            operator: 'GreaterThan'
            threshold: sqlCpuPctThreshold
            timeAggregation: 'Average'
            criterionType: 'StaticThresholdCriterion'
          }
        ]
      }
      autoMitigate: true
    },
    empty(notificationEmails) ? {} : { actions: metricAlertActions }
  )
}

// -----------------------------------------------------------------------------
// 8. SQL vCore-second monthly quota burn (Free-Offer)
// -----------------------------------------------------------------------------

resource sqlQuotaBurnAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: 'tcp-alert-sql-quota-burn'
  location: location
  tags: tags
  properties: union(
    {
      description: 'Azure SQL Free-Offer monthly vCore-second consumption crossed the configured threshold of the 100 000 grant. Exhaustion pauses the database for the remainder of the month — see 03_architecture.md §10.1. Source query: infra/observability/kusto/05_sql_vcore_consumption.kql.'
      severity: 1
      enabled: true
      evaluationFrequency: 'PT1H'
      // ISO 8601 fix: `PT1D` is invalid (T-separator followed by D is reserved
      // for hours/min/sec); use `P1D` for 1 day. Also: overrideQueryTimeRange
      // is capped at P2D by Azure SQR engine — the original P31D MTD scan is
      // unsupported. The query's internal `where TimeGenerated > startofmonth`
      // still filters semantically, but only sees the last 2 days of data.
      // Follow-up: re-architect using Cost Management API for true MTD burn.
      windowSize: 'P1D'
      overrideQueryTimeRange: 'P2D'
      scopes: [logAnalyticsWorkspaceId]
      criteria: {
        allOf: [
          {
            query: 'AzureMetrics | where ResourceProvider == "MICROSOFT.SQL" | where MetricName == "app_cpu_billed" | where TimeGenerated > startofmonth(now()) | summarize used = sum(Total) | extend pct = round(100.0 * used / 100000.0, 2) | where pct > ${sqlQuotaBurnPctThreshold}'
            timeAggregation: 'Count'
            operator: 'GreaterThan'
            threshold: 0
            failingPeriods: {
              numberOfEvaluationPeriods: 1
              minFailingPeriodsToAlert: 1
            }
          }
        ]
      }
      autoMitigate: false
    },
    empty(notificationEmails) ? {} : { actions: sqrActionGroupsBlock }
  )
}

// -----------------------------------------------------------------------------
// Outputs — surfaced for STATE.md and runbooks.
// -----------------------------------------------------------------------------

output actionGroupId string = empty(notificationEmails) ? '' : actionGroup.id
output alertRuleNames array = [
  askLatencyAlert.name
  askAvailabilityAlert.name
  generatorFailureAlert.name
  bacpacMissedAlert.name
  rateLimitSpikeAlert.name
  anthropicCostBurnAlert.name
  sqlCpuAlert.name
  sqlQuotaBurnAlert.name
]
