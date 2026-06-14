# Etapa 8 — Observability surface review

| Field | Value |
|---|---|
| **Reviewer** | observability-engineer (pass 1) |
| **Date** | 2026-05-16 |
| **Scope** | `infra/observability/workbook.json`, `infra/observability/kusto/0[1-9]_*.kql`, `infra/modules/alerts.bicep`, `infra/modules/workbook.bicep`, `infra/main.bicep` (observability wiring), `docs/observability/slo.md`, `docs/observability/README.md`, `docs/design/03_architecture.md §10 + §12`, `tests/integration/test_telemetry_no_pii.py` |
| **Branch** | `feat/azure-rewrite` |
| **Verdict** | **CHANGES-REQUESTED** — two correctness defects in the audit/cost surface and one SLO-math error must land before the workbook is presented as evidence; the rest are tightening / drift items. |

---

## 1. Summary

Etapa 8 adds a coherent, well-documented observability surface: nine canonical KQL queries under `infra/observability/kusto/`, an Azure Monitor workbook (`workbook.json`) wired into Bicep via `modules/workbook.bicep`, eight alert rules in `modules/alerts.bicep`, an SLO/SLI doc (`docs/observability/slo.md`), an operator README (`docs/observability/README.md`), and a PII-redaction integration test (`tests/integration/test_telemetry_no_pii.py`).

The architectural shape is right — single-source KQL files mirrored into a workbook, alert rules linked back to the same queries, SLI/SLO doc enumerating the burn-rate rationale, README that an on-call operator can follow. The bicep is clean (parameterised thresholds, conditional action group, deterministic workbook GUID).

The defects fall into three buckets:

1. **Telemetry contract that no code emits.** Tile 9 ("Question fingerprints") and query 07 reference a `tcp.ask.audit` event with a `question_sha256` dimension — but `function_app/triggers/ask.py` never emits that event. The audit panel is a permanent empty table.
2. **SLO burn-rate math is wrong.** `docs/observability/slo.md` §4 calls 5 % error over 1 h a "14.4× burn rate"; against a 1 % monthly budget the actual ratio is 5×. The 14.4× number is the Google SRE workbook's textbook fast-burn threshold (corresponding to a *14.4 % failure rate* over 1 h, not 5 %). The alert threshold is defensible at 5 % but the math caption is incorrect.
3. **Workbook ↔ .kql drift.** The README claims the workbook JSON is mirrored from the .kql sources; three tiles diverge non-trivially (token query missing the `customMetrics` arm, cold-start tile drops the `unknown` bucket, latency tile/`.kql` does not filter `success == true` though the alert does and the SLO defines SLI-3 over successful requests).

Counts:

| Severity | Count |
|---|---:|
| Critical | 2 |
| Major | 5 |
| Minor | 7 |
| Nits | 4 |
| Strengths | 6 |

---

## 2. Critical findings

### obs-CR-01 — `tcp.ask.audit` event is referenced but never emitted

**Location**
- `infra/observability/kusto/07_ask_question_audit.kql:18-22`
- `infra/observability/workbook.json:218-228` (tile-audit)
- `docs/observability/README.md:35` (workbook section claims a 9th panel "Question fingerprints — last 50 distinct SHA-256 question hashes")
- Expected producer: `function_app/triggers/ask.py` — **no emission found**

**Summary**
Query 07 filters `traces | where customDimensions["event"] == "tcp.ask.audit"` and reads `customDimensions["question_sha256"]`. A repo-wide grep for either string yields only three hits — the .kql file, the workbook JSON, and `docs/design/03_architecture.md §12.2 query 7`. The handler in `function_app/triggers/ask.py` emits `tcp.func.ask.*` events and `tcp.ask.metrics`, but never `tcp.ask.audit` and never a SHA-256 question hash.

**Why it matters**
- Tile 9 of the workbook (Question fingerprints) will always be empty, contradicting the README's panel inventory at `docs/observability/README.md:35` and the SLO doc's audit posture at `slo.md:127` (the rate-limit-spike alert is documented as cross-referenced from "abusive repetition" detection in this audit panel).
- The audit panel was advertised as the GDPR §13 / threat-model S05 mitigation — the operator who is supposed to "verify the audit hook is wired" (workbook header text at `workbook.json:213-215`) has no signal that confirms or denies it.
- `tests/integration/test_telemetry_no_pii.py` does not assert that an audit event is emitted (only that the question text is *not* leaked). A regression that removes the audit hook would slip past CI silently because the hook never existed.

**Suggested fix**
Add to `_emit_metrics()` (or a separate `_emit_audit()` call right before the Anthropic call) an event of the form:

```python
log.info(
    "tcp.ask.audit",
    question_sha256=hashlib.sha256(question_text.encode("utf-8")).hexdigest(),
)
```

Then strengthen `tests/integration/test_telemetry_no_pii.py` to positively assert that the SHA-256 of the canary question lands in *exactly one* captured event with `event == "tcp.ask.audit"` and that the raw question text never lands anywhere. This converts a documentation aspiration into an enforced contract.

If the audit emission is intentionally deferred to a later stage, the workbook tile, the .kql file, the README §1 panel inventory, and the SLO doc §4 cross-reference all need a `(deferred — see Etapa 12)` marker so the on-call operator does not waste 30 minutes chasing an empty panel.

### obs-CR-02 — SLO doc burn-rate math is mathematically wrong

**Location**
- `docs/observability/slo.md:122` and `docs/observability/slo.md:96-97`
- `infra/modules/alerts.bicep:138` (description text)
- Cross-reference: `docs/observability/README.md:70`

**Summary**
The SLO doc states: *"5 % failure over 1h ≈ 14.4× the 1 %/30-day budget burn rate. Exhausts the entire monthly budget in ≤2 days if sustained — fast burn."* The alert rule's description repeats the claim.

The Google SRE workbook (chapter 5) defines burn rate as:

```
burn_rate = (actual_error_rate / SLO_error_budget)
```

For a 99 % SLO over 30 days, the budget is `1 - 0.99 = 0.01` (1 %). A 5 % observed error rate over 1 h is `0.05 / 0.01 = 5×` burn — **not 14.4×**.

The 14.4× number is the canonical fast-burn threshold from the Google SRE workbook; it corresponds to a configuration where 2 % of the 30-day budget is consumed in 1 h: `0.02 × 24 × 30 / 1 = 14.4`. To trigger that with a 1 %/30-day budget you need an observed error rate of `14.4 × 0.01 = 14.4 %` over 1 h, not 5 %.

The "exhausts the monthly budget in ≤2 days" claim is also wrong arithmetic for the stated threshold: 5 % sustained for 1 day burns `0.05 × 24 / (0.01 × 24 × 30) = 0.05 × 24 / 7.2 = ~16.6 %` of the monthly budget, so the budget actually empties in `30 / 5 = 6 days`, not 2.

**Why it matters**
- The SLO document is the single source-of-truth used by the academic thesis (per §5 reporting cadence "appended to the thesis evaluation chapter as the empirical evidence of operational quality"). A factually wrong burn-rate calculation in defended thesis work is hard to walk back.
- The alert rule description (`alerts.bicep:138`) inherits the wrong number; an on-call reading "14.4× burn rate" will assume this is a textbook canonical fast-burn alert and will not realize it is a 5× burn (much more sensitive — fires earlier, fires on transient anomalies).
- The threshold `error_rate > 0.05 and total > 5` at `alerts.bicep:148` is itself defensible (low-volume system; 5× burn is a reasonable severity-1 threshold for an academic deployment). The defect is the *description*, not the threshold.

**Suggested fix**
Two coordinated edits in the same commit:

1. In `docs/observability/slo.md:122`, change "≈ 14.4× the 1 %/30-day budget burn rate. Exhausts the entire monthly budget in ≤2 days if sustained — fast burn." to: "= 5× the 1 %/30-day budget burn rate. Sustained for 24 h consumes ~16.6 % of the monthly budget; full exhaustion at ~6 days. Tuned aggressive for the low-volume academic system; revisit once 30 days of traffic exist."
2. Mirror the new wording into `alerts.bicep:138` so the alert blade in the portal matches the doc.

While you are there, add a 4-line worked example in §4 of the SLO doc — `burn_rate = observed / target = 0.05 / 0.01 = 5×` — so the next reviewer does not have to redo the algebra.

---

## 3. Major findings

### obs-MA-01 — Workbook token tile drops the `customMetrics` arm; drifts from .kql 03

**Location**
- `infra/observability/workbook.json:118-126` (tile-tokens)
- `infra/observability/kusto/03_anthropic_tokens_and_cost.kql:25-65`

**Summary**
The .kql file unions a `from_traces` branch and a `from_metrics` branch, explicitly so that "a future migration to true customMetrics (Etapa-12 polish) is transparent: we union the two sources and let the missing one contribute 0" (file header lines 9-12). The workbook tile uses *only* the `traces` branch.

The README at `docs/observability/README.md:56-59` is explicit: "Editing protocol: when a query changes in the workbook (Azure Portal → Edit → Advanced Editor), **mirror the change back into the .kql file in the same PR**. A drift between the file and the deployed workbook is a deployment hazard — the next `azd deploy` would silently undo the edit."

That contract is already broken on day one.

**Why it matters**
- When the Etapa-12 migration to `customMetrics` happens, the .kql file will keep working but the workbook tile will silently start under-reporting (the migrated half will not land in the tile). Cost-burn alerts will under-fire.
- The README's editing protocol is the load-bearing rule for keeping the observability surface coherent; an undeclared drift on launch day undermines the operator's trust in the protocol.
- Same anti-pattern in tile-bacpac vs `08_bacpac_export_health.kql` — the workbook tile is the unioned shape, but the .kql also unions a customMetrics branch with `status = "metric"` which the workbook tile does not.

**Suggested fix**
Pick one of two postures and apply it everywhere:

- **(preferred)** Make the workbook tile load the .kql verbatim. The Etapa 8 deploy pipeline should `Get-Content` each .kql, escape it for JSON, and substitute into a `serializedData` template — eliminating drift mechanically.
- **(fallback)** If the workbook tile is the canonical shape on purpose (e.g. the `customMetrics` branch is dead code until Etapa 12), strip the `from_metrics` half from the .kql and add a comment "Re-add the customMetrics arm in Etapa 12 — see RR-06."

The current state — half-drifted in both directions — is the worst of both worlds.

### obs-MA-02 — Latency tile / .kql does not filter `success == true`, but SLI-3 and the alert do

**Location**
- `docs/observability/slo.md:69` ("Source: `requests` table, `operation_Name == "ask"`, `success == true`.")
- `infra/observability/kusto/01_ask_latency_percentiles.kql:20-22` (no `success` filter)
- `infra/observability/workbook.json:52` (no `success` filter)
- `infra/modules/alerts.bicep:111` (`requests | where operation_Name == "ask" | where success == true | ...`)

**Summary**
SLI-3 (`slo.md:64-72`) is explicitly defined over "successful `/api/ask` requests" so that a slow refusal does not consume the same SLI as a slow real answer. The alert rule honours that — its query filters `success == true` before percentile-ing.

The .kql file and the workbook tile do **not** filter on `success`. The dashboard p95 line and the alert threshold are reading different populations.

**Why it matters**
- The dashboard's "p95 = 2 900 ms — green" can co-exist with an alert that says "p95 > 4 000 ms — page" because they are computing percentiles over different denominators. On an outage where most requests fail fast (low duration) and a minority of successes are slow, the workbook percentile would *under-state* the customer-visible latency while the alert correctly *over-states* relative to the SLO.
- Once obs-CR-01 lands and refusals get an audit event, a refused request still produces a `requests` row. If refusals are fast (typical), they drag the dashboard p95 down further; if refusals are slow (model deliberation), they spuriously inflate it.

**Suggested fix**
Add `| where success == true` to both `01_ask_latency_percentiles.kql` and the workbook tile-latency query. While there, add the same filter to the workbook header note at line 44 ("p95 latency target: ≤ 3 000 ms (warm requests)") — clarify it is *successful* warm requests.

### obs-MA-03 — Cold-start tile drops the `unknown` bucket; drifts from .kql 04

**Location**
- `infra/observability/workbook.json:140` (case has `unknown` but summarize drops it)
- `infra/observability/kusto/04_function_cold_starts.kql:20-30` (case has `unknown` AND summarize counts it)

**Summary**
In `04_function_cold_starts.kql:30`, `unknown_starts = countif(bucket == "unknown")` is part of the projection. In the workbook tile, the `case(...)` still classifies records as `"unknown"` but the `summarize` line drops that bucket. A row that lands in `bucket=="unknown"` will not be counted into any output column and will silently vanish.

**Why it matters**
The `unknown` bucket is the signal that *something* changed in the Functions runtime emission shape (e.g., `StartupDurationMs` renamed, custom dimensions repackaged) — exactly the kind of cold-start regression the panel exists to detect. Dropping that bucket means a 100 % regression to "unknown" looks like *zero* cold starts in the dashboard.

**Suggested fix**
Add `, unknown=countif(bucket == "unknown")` to the workbook tile's summarize. Also consider making the timechart `render` line stack-bar instead of line — cold/lukewarm/warm/unknown as a stacked bar conveys ratio at a glance, which is what the operator actually wants.

### obs-MA-04 — Anthropic cost-burn alert threshold is string-interpolated without a cast

**Location**
- `infra/modules/alerts.bicep:58` (`param anthropicDailyBudgetEur string = '0.50'`)
- `infra/modules/alerts.bicep:300` (`| where est_eur > ${anthropicDailyBudgetEur}`)

**Summary**
The parameter is typed `string` and substituted raw into the KQL body. KQL parses `0.50` as a real when the literal is bare, so the current default works. But:

1. A future operator passing `--parameters anthropicDailyBudgetEur='€0.50'` corrupts the query (`| where est_eur > €0.50` is a parse error and the alert silently disables).
2. A negative value (`'-0.50'`) parses as a literal subtraction in KQL (`est_eur > -0.50` is always true once any cost is recorded).
3. A `'1.0e-3'` value parses as scientific notation, which is fine — but inconsistent typing across Bicep parameters (the other threshold params are `int`) is a footgun.

**Why it matters**
- Bicep linting will not catch the string-interpolation hazard; only a deploy + alert misfire will surface it. The detection latency is ~24 h (the next time an evaluator window passes).
- The same pattern is used at `alerts.bicep:111, 263, 376` — all three substitutions are `int` parameters which the KQL parser handles cleanly, so the audit shows only this one row as a concrete risk. But the precedent is what concerns me.

**Suggested fix**
Convert to a number type (`@minValue(0)` on a `string` cannot stay typed; the cleanest fix is `param anthropicDailyBudgetEurMilli int = 500` interpreted as milli-EUR, then `| where est_eur * 1000.0 > ${anthropicDailyBudgetEurMilli}`). Alternatively, keep the string but wrap in KQL: `| where est_eur > todouble("${anthropicDailyBudgetEur}")` — `todouble` of a malformed string returns `null`, which is comparison-safe (always false).

### obs-MA-05 — SLI-1 "good event" definition contradicts how `success` is set in App Insights

**Location**
- `docs/observability/slo.md:45-47` (Good event includes `resultCode in {200, 422, 429}`; bad event is `{500}`)
- `infra/modules/alerts.bicep:148` (`error_rate = failed / total` where `failed = success == false`)
- `infra/observability/kusto/06_error_rate_by_operation.kql:11-16` (same `success == false` shape)

**Summary**
The SLO doc explicitly classifies 422 (model refusal / validator rejection) and 429 (rate limit) as *good events*. The alert query and the underlying error-rate query do not — both bucket `success == false` as failed. In the App Insights Functions Python runtime, `requests.success` is set to `false` for any HTTP response code ≥ 400 by default (configurable via `success_status_codes` but not configured here).

Implication: when the rate-limiter or the validator fires, the request shows up as a "bad event" in the alert query even though the SLO doc says it should not.

**Why it matters**
- A burst of legitimate 429s (e.g. one trader pasting an inadvertent loop into a SWA-bound REPL) will trip the burn-rate alert as if the assistant had a 5xx outage. The page is wrong.
- The SLI-1 numerator/denominator split spelled out in §2 of the SLO doc is computable only if the alert query has the same filter — currently it does not.

**Suggested fix**
Tighten the alert query at `alerts.bicep:148` to: `| extend bad = success == false and resultCode != "422" and resultCode != "429" | summarize total = count(), failed = countif(bad)` — and document the filter alignment in `slo.md` §4 so any future query edit honours the same exclusion. Same edit applies to `06_error_rate_by_operation.kql` for consistency.

If 422 must be excluded *only* when it represents a model refusal (and not when it represents a SafeQuery rejection from a legitimate question), the discriminator lives in `customDimensions["error_code"]` of the corresponding trace event — that is a deeper refactor and a candidate for the Etapa-12 polish noted in `slo.md` §6 item 2.

---

## 4. Minor findings

### obs-MI-01 — `customDimensions["Category"] == "Host.Startup"` may not match the Python v2 runtime emission

**Location**
- `infra/observability/kusto/04_function_cold_starts.kql:17`
- `infra/observability/workbook.json:140`

**Summary**
The Python v2 runtime (`function_app/function_app.py`) routes worker-init traces through the Azure Functions host log channels. The `Category` dimension is set by the .NET host, not by Python — it appears in App Insights as `customDimensions.Category = "Host.Startup"` only when the Worker stack logs through the host's `ILogger`. With `azure-functions>=1.21` and structlog as the only logger configured in this project (no `azure-monitor-opentelemetry`, no `OpenCensusLogHandler` mention), it is worth verifying that this dimension actually arrives in App Insights at all. A cold start that emits via the Python `logging` module reaches App Insights with a *different* category dimension (`category` or `LoggerName` depending on SDK).

**Why it matters**
If the dimension key is wrong, the panel is empty for a different reason than obs-CR-01: the data is there but the filter excludes it. A simple `traces | where message has "Worker" | take 10 | project customDimensions` from the portal will tell you. Worth a one-time smoke test from `postprovision.ps1` or a follow-up validation step.

**Suggested fix**
Add an Etapa-8 verification checklist item in `docs/observability/README.md` under "Day-2 operations": "Within the first 24 h after deploy, open the portal, run the four `traces`-based queries with a wide time window, and confirm at least one row returns. If empty, the customDimensions key has drifted — capture the actual dimension names and update the .kql + workbook in lockstep." This is the same defensive sweep that would have caught obs-CR-01.

### obs-MI-02 — `windowSize: PT15M` on the latency alert combined with `failingPeriods: 3 of 3` over `evaluationFrequency: PT5M` gives a 45-min worst-case detection delay

**Location**
- `infra/modules/alerts.bicep:104-118`

**Summary**
The alert evaluates every 5 minutes against the previous 15 minutes, and requires 3 consecutive failing periods to alert. Mean detection delay = 15 min (worst-case = 25 min for the first window's data to clear, plus 2 × 5 min = 35 min). The README claims the latency alert fires "when p95 exceeds 4 000 ms for 15 minutes" — but it actually fires after ~25-35 minutes due to the 3-of-3 sliding requirement.

**Why it matters**
Operators read "fires for 15 minutes" and expect to see the page ~15 minutes after the breach. Reality is 25-35 minutes. For a severity-2 notify-only alert this is acceptable; for any escalation to severity-1 the detection delay needs to be in the SLO budget calculation.

**Suggested fix**
Clarify in `slo.md:123` the worst-case detection time. Either lower `failingPeriods` to 2-of-2 (saves one 5-min slice), or accept the delay and document it. The README at `slo.md:123` already hints at this with "Three 5-min evaluation windows above threshold = sustained user-visible regression" — make the resulting 25-35 min detection window explicit so the operator understands what they are buying with the 3-of-3 requirement.

### obs-MI-03 — Latency alert has no volume gate; a single slow sample can trip it

**Location**
- `infra/modules/alerts.bicep:111`

**Summary**
The query computes `p95 = percentile(duration, 95)` over the 15-min window. `percentile` of a single sample = that sample. With 32 employees making ~30 req/day combined, a 15-min window can be entirely empty most of the time, then a single 4 100 ms cold-path request lands and the alert tries to fire (the 3-of-3 gate saves it for consecutive isolated breaches, but not from a sustained pattern of "one cold start every 5 min").

The availability alert at `alerts.bicep:148` correctly includes `total > 5`. The latency alert does not.

**Suggested fix**
Add `| where samples > 5` to the latency query (where `samples = count()` is added to the projection). The number 5 should match the availability alert's volume gate for consistency.

### obs-MI-04 — `prev(Resource) != Resource` in query 05 is fragile under multi-DB scenarios

**Location**
- `infra/observability/kusto/05_sql_vcore_consumption.kql:22-24`
- `infra/observability/workbook.json:161` (same shape)

**Summary**
`row_cumsum(..., prev(Resource) != Resource)` resets the running sum at the *previous-row boundary*. This relies on the input being sorted by `Resource` then `TimeGenerated`. The query does `order by TimeGenerated asc` (line 20) which keeps multiple resources interleaved — `prev(Resource)` would then toggle on every row and reset the cumulative sum to zero.

In single-DB deployments (the current state), only one Resource appears, `prev(Resource) != Resource` evaluates false except for the first row, and the cumulative sum works by accident. The moment a second DB appears (e.g. the restore-drill DB at `03_architecture.md:506`), the math collapses.

**Suggested fix**
Either:
- Restate the order: `| order by Resource asc, TimeGenerated asc` (then `prev(Resource) != Resource` is correctly true only at resource boundaries).
- Drop the partition entirely and project per-resource cumulative sums with `partition by Resource (...)`: `partition by Resource (sort by TimeGenerated asc | extend cumulative_used = row_cumsum(used_vcore_seconds))`.

The second form is more readable and survives any future Resource permutation.

### obs-MI-05 — PII test does not cover the bad-JSON / missing-question / question-too-long paths

**Location**
- `tests/integration/test_telemetry_no_pii.py:170-287` (5 tests)
- `function_app/triggers/ask.py:559-588` (three early-return paths not covered)

**Summary**
The test covers: success, refusal, validator-rejected SQL, unknown principal, SQL execute failure. It does not cover the early-exit paths at lines 562 (`invalid_json_body`), 572 (`missing_question`), and 579 (`question_too_long`). Those paths exit with a 400 envelope without emitting metrics, but the `log = _log.bind(oid_suffix=oid.hex[-8:])` at line 556 has already run, so any future regression that adds `question_text=question_text` to one of those `log.warning(...)` calls slips past the test.

The `forwarded_secret_mismatch` (line 524), `missing_principal` (line 536), `unparseable_principal` (line 547), and `scope_lookup_failed` (line 594) paths are similarly uncovered. The first three exit before `oid` is parsed, so they cannot leak `oid` — but they *could* leak the raw `principal_header` (a base64 blob that decodes to user details including UPN), and there is no test gate against that regression.

**Why it matters**
The README at `docs/observability/README.md:104-118` advertises the test as covering "Five paths". A reader cross-checking which paths are protected sees only those five paths called out. Future PRs that touch `ask.py` failure handling will likely regress an uncovered path silently.

**Suggested fix**
Extend the test with three additional paths:

1. **Bad JSON body** — `body=b"not-json"`, expect 400, assert no `principal_header`, no UPN, no `oid` full-form leaks.
2. **Question too long** — `body={"question": "x" * (max_chars + 1)}`, expect 400, same redaction asserts plus assert the long string itself (canary-shaped) does not appear.
3. **Forwarded-secret mismatch** — wrong `X-SWA-Forwarded` header, expect 403, assert the principal blob does not appear in logs (because it is read but not yet decoded).

Two of these are 5-line additions; the third opens a small refactor opportunity to centralise the per-test fixture builder.

### obs-MI-06 — Refused-question test does not exercise model-driven PII echo

**Location**
- `tests/integration/test_telemetry_no_pii.py:205-221`

**Summary**
The refusal test mocks `ask_claude` to return a refusal with `refusal_reason="Out of scope for this assistant."`. The handler logs that reason at `ask.py:660` via `reason=answer.refusal_reason[:120]`. In production, Anthropic's actual refusal text can quote phrases from the user's question — that is a *real* PII leakage vector that the current test does not exercise.

**Why it matters**
The structlog event `tcp.func.ask.refused` carries `reason=<model output>` directly. A real refusal containing "I cannot answer 'why does Andrei outperform Bogdan'" would surface "Andrei outperform Bogdan" in App Insights, defeating the PII posture.

**Suggested fix**
Add a sixth test variant where the mocked `ask_claude` returns a refusal whose `refusal_reason` contains the canary question text verbatim. Assert that even when the model echoes the question, the captured `tcp.func.ask.refused` event does not surface the canary string. This forces the handler to either redact the refusal reason or to hash it before logging — a fix-forward in `ask.py:660`.

### obs-MI-07 — Workbook auto-refresh / time-range parameter not configured

**Location**
- `infra/observability/workbook.json:7` (header text claims "workbook auto-refreshes every 5 minutes")
- `infra/observability/workbook.json:11-39` (no `refreshInterval` or `auto-refresh` field anywhere in the JSON)

**Summary**
The header markdown asserts a 5-minute auto-refresh. The workbook JSON has no auto-refresh setting (the field is `"items[*].refreshInterval"` in the workbook v1 schema or `"items[*].refreshSettings"` in v2; neither appears). The Azure Portal default for a workbook with no explicit refresh is *manual* — the user has to click the refresh button.

**Why it matters**
Operators trust the header text and assume the panels are live. They are not.

**Suggested fix**
Either remove the "auto-refreshes every 5 minutes" sentence at workbook.json line 7, or add the refresh setting to every KqlItem block:

```json
"refreshIntervalSettings": {
  "isEnabled": true,
  "intervalInMs": 300000
}
```

The former is the lower-risk fix (academic-phase posture: the operator opens the workbook on demand). The latter is the production-grade fix.

---

## 5. Nits

### obs-NIT-01 — USD-to-EUR rate is hardcoded twice and will drift

`infra/observability/kusto/03_anthropic_tokens_and_cost.kql:28` and `infra/modules/alerts.bicep:300` both hardcode `usd_to_eur = 0.92`. When the FX rate moves (which it will), the dashboard and the alert will diverge if only one is updated. Either pull from a single source (a Bicep param threaded into both) or — better — drop the FX conversion and project the est_usd column directly in EUR via an `ECB rate at deploy time` Bicep input. For the academic phase, accepting the rounding is fine; just deduplicate the constant.

### obs-NIT-02 — `samples=count()` projection in the workbook latency tile is unused

`infra/observability/workbook.json:52` projects `samples = count()` but the timechart renderer ignores extra columns. Either remove it or change the renderer to a multi-series tile that exposes `samples` as a secondary axis. The .kql at `01_ask_latency_percentiles.kql:27` does the same. Harmless; just dead bytes.

### obs-NIT-03 — Workbook `serializedData` is loaded via `loadTextContent`; size limit risk

`infra/modules/workbook.bicep:39` uses `loadTextContent('../observability/workbook.json')`. The ARM template embeds the JSON as a string literal; ARM template payload limit is 4 MB total. The current workbook.json is ~10 KB, so this is fine for now — but as panels grow (e.g. the multi-window burn-rate panel deferred in §6 of the SLO doc), the limit creeps closer. Worth a comment in `workbook.bicep:39` reminding the reader.

### obs-NIT-04 — `targetResourceTypes` field is set on every scheduled-query-rule; not strictly required

`infra/modules/alerts.bicep` sets `targetResourceTypes: ['microsoft.insights/components']` on every log-query rule. `scheduledQueryRules@2023-12-01` infers this from `scopes`. Harmless redundancy; remove for tightness or leave for explicit documentation. The metric alert at line 333 correctly uses the singular `targetResourceType` (note: different field name from the log rule's plural form — easy to confuse).

---

## 6. Strengths

For a single-developer academic build, this is a creditable observability surface.

1. **Single-source KQL**: The `infra/observability/kusto/*.kql` files have well-written headers (purpose, source line in 03_architecture, target workbook tile, target alert rule). The editing protocol in `README.md:56-59` is the right discipline even though it has already drifted (obs-MA-01, obs-MA-03).
2. **Parameterised alert thresholds**: Every alert threshold is a Bicep parameter with a `@description` justifying the default. The reasoning ("32 employees × 10 req/min budget = 19 200/h theoretical ceiling, so 50 keeps signal-to-noise high") is exactly the kind of rationale a thesis defence wants to see.
3. **Conditional action group**: `alerts.bicep:67-90` correctly gates the action group on a non-empty email array, with the cascading `actionGroupsArray` variable threading through every alert. Bootstrap-friendly.
4. **Deterministic workbook GUID**: `workbook.bicep:30` derives the workbook id from `guid(appInsightsId, 'tcp-ops-workbook')`, so a re-deploy reuses the same resource rather than orphaning a copy. Small touch, often missed.
5. **SLO doc has an error-budget policy**: `slo.md:100-109` includes the change-freeze threshold (50 % budget burn) and the reliability-only threshold (90 % budget burn). Many production-grade SLO docs stop at the SLI/SLO table.
6. **PII test ships in the fast unit job**: The README at `docs/observability/README.md:115-117` confirms the PII redaction test runs in `pytest tests/unit tests/integration/test_telemetry_no_pii.py` — a regression actually blocks merge rather than being an aspirational guideline.

---

## 7. Cross-component contract matrix

| Producer | Consumer | Contract field | Status |
|---|---|---|---|
| `function_app/triggers/ask.py:478-489` (`_emit_metrics`) | `traces.customDimensions.event == "tcp.ask.metrics"` in queries 03, 09 | event name | OK — emitted; structlog event positional arg lands in `customDimensions` for Python v2 runtime |
| `function_app/triggers/ask.py` (**missing**) | `traces.customDimensions.event == "tcp.ask.audit"` in query 07, workbook tile 9 | event name | **BROKEN** — see obs-CR-01 |
| `function_app/triggers/ask.py:619` (`tcp.func.ask.rate_limited` warning) | `traces.message has "tcp.func.ask.rate_limited"` in query 09, alert 5 | message contains | OK |
| `function_app/triggers/bacpac_export.py:424-430` (`tcp.bacpac.*` events) | `traces.customDimensions.event in (...)` in query 08, alert 4 | event name + status enum | OK on traces side; the `customMetrics` arm in .kql 08 is aspirational (RR-06) — see obs-MA-01 |
| `function_app/function_app.py` (`@app.route(route="ask")`) | `requests.operation_Name == "ask"` in queries 01, 06, alerts 1, 2 | operation name | OK — Python v2 runtime emits the function name (the function is `ask`, not `HttpTrigger_AskAssistant` — `03_architecture.md §12.2 query 1` had the wrong name; the .kql library and alerts correctly use `"ask"`) |
| `function_app/triggers/daily_generator.py` (`@app.timer_trigger(...)` with `function_name` inferred) | `requests.operation_Name == "daily_generator"` in query 02, alert 3 | operation name | OK — function is `daily_generator` (line 27 of daily_generator.py) |
| `infra/main.bicep:328` (`notificationEmails`) | `alerts.bicep:46` `notificationEmails` param | array passthrough | OK |
| `infra/main.bicep:309-317` (workbook module) | `workbook.bicep:39` (`loadTextContent`) | JSON path | OK |
| `tests/integration/test_telemetry_no_pii.py` | Implicit contract that `oid_suffix` is bound for every log event after parsing | binding present | OK on the 5 tested paths; uncovered paths in obs-MI-05 |
| `slo.md:122` (14.4× claim) | `alerts.bicep:138` (same claim) | numeric claim | **WRONG** — see obs-CR-02 |
| `slo.md:71` (p95 ≤ 3 000 ms warm path) | `infra/modules/alerts.bicep:49` (`askLatencyP95ThresholdMs = 4000`) | 1 000 ms guard band | OK — intentional, documented in the param description |
| `slo.md:71` (p95 over successful requests) | `01_ask_latency_percentiles.kql` (no success filter) | population alignment | **DRIFT** — see obs-MA-02 |

---

## 8. No-regression sweep

I confirmed (via reading the wired files) that Etapa 8 adds:

- `infra/observability/workbook.json` (new)
- `infra/observability/kusto/*.kql` (new, 9 files)
- `infra/modules/alerts.bicep` (new)
- `infra/modules/workbook.bicep` (new)
- `docs/observability/slo.md` (new)
- `docs/observability/README.md` (new)
- `tests/integration/test_telemetry_no_pii.py` (new)
- 2 modules wired into `infra/main.bicep:309-330` (workbook + alerts)
- 1 output added in `infra/main.bicep:362` (`AZURE_OBSERVABILITY_WORKBOOK_ID`)
- 1 emit-call refactor in `function_app/triggers/ask.py:_emit_metrics` (now takes the bound logger)

The refactor of `_emit_metrics` to take the bound logger is a clean change — the `oid_suffix` and `scope` context now propagate to the telemetry event, which is exactly what enables query 09 (rate-limit refusals by oid_suffix) and the future audit query 07 (once obs-CR-01 lands). No regression in the prior surface.

No prior-stage files were modified, with one exception worth calling out: `function_app/triggers/ask.py` was edited to pass `log` into `_emit_metrics`. That is a legitimate Etapa-8 refactor and the PII test covers the new binding semantics (line 199-202).

---

## 9. English-only & secrets sweep

- **English-only**: confirmed across every Etapa-8 file. The workbook header markdown, the .kql headers, the alert descriptions, the SLO doc, and the README are all in English. No Romanian strings except RO time-zone references ("Europe/Bucharest", "Mon–Fri 09:00–18:00 RO") which are timezone identifiers, not natural language.
- **Secrets**: no real secrets. The only credential-shaped strings in alerts.bicep are placeholders in the `@description` examples (`'\'["alex@example.com"]'\''` at line 45). `gitleaks` will not fire. The workbook JSON contains no resource ids — it relies on `sourceId` binding at deploy time from `workbook.bicep:41`.

---

## 10. Recommendation

**CHANGES-REQUESTED.**

Block merge on the two Critical items:

- **obs-CR-01** (`tcp.ask.audit` event implementation or workbook/.kql/SLO deferral marker).
- **obs-CR-02** (burn-rate math correction — small but the thesis chapter depends on it).

Fold the five Majors into the same commit batch:

- **obs-MA-01** (token tile mirroring).
- **obs-MA-02** (latency `success` filter alignment).
- **obs-MA-03** (cold-start `unknown` bucket).
- **obs-MA-04** (cost-burn parameter typing).
- **obs-MA-05** (SLI-1 "good event" alignment with the alert query).

The Minor and Nit items can be deferred to an Etapa-12 polish pass without operational risk — except **obs-MI-05** (PII test gaps on uncovered paths) which is cheap to fix and removes a real regression risk.

After this batch lands, a single convergence pass that runs the alert queries against an empty-but-deployed workspace (returns zero rows, no syntax errors) should be sufficient to declare Etapa 8 closed.

---

## 11. Suggested follow-ups (out of scope for the convergence)

- **Multi-window burn-rate detector** (already deferred in `slo.md` §6 item 1) — the single-window detector trades precision for recall. The Google SRE workbook's 1h × 6h coupled detector reduces alert fatigue; revisit once 30 days of baseline traffic exist.
- **Custom-metrics migration** (RR-06, `slo.md` §6 item 2) — once the `azure-monitor-opentelemetry` SDK is wired, every `traces`-table read in the alert library collapses to a `customMetrics` read with ~5× lower query latency, and the obs-MA-01 / obs-MA-03 drift class disappears entirely (the .kql becomes single-source).
- **Synthetic probe** (`slo.md` §6 item 3) — a `Microsoft.Insights/webtests` pinging `/api/ping` from two regions detects platform outages independent of user traffic. Free for 5 tests/month; one Bicep block.
- **Workbook-from-KQL generator** — a `scripts/build_workbook.ps1` that templates the `serializedData` JSON from the .kql files would mechanically eliminate the drift class. Half-day of work; high ROI given the editing protocol cost.

---
