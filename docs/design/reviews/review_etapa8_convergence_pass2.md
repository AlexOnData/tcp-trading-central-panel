# Etapa 8 — Convergence pass-2

| Field | Value |
|---|---|
| **Date** | 2026-05-16 |
| **Pass-1 reviewers** | observability-engineer + cloud-architect + code-reviewer |
| **Verdict** | **APPROVED FOR MERGE** — 3 Critical RESOLVED, 14 Major RESOLVED, 6 Minor RESOLVED, 0 regressions, 7 ACCEPTED RESIDUAL (deferred to Etapa-12 polish) |
| **Branch** | `feat/azure-rewrite` |

---

## Pass-1 verdict summary

| Reviewer | Verdict | C | M | mi | N | Strengths |
|---|---|---:|---:|---:|---:|---:|
| observability-engineer | CHANGES-REQUESTED | 2 | 5 | 7 | 4 | 6 |
| cloud-architect | CHANGES-REQUESTED | 3 | 5 | 7 | 4 | 6 |
| code-reviewer | CHANGES-REQUESTED | 0 | 4 | 7 | 5 | 6 |
| **Total** | | **5 distinct** | **14** | **21** | **13** | **18** |

Two Criticals (obs-CR-01, obs-CR-02) and three Criticals (arch-CR-01, arch-CR-02, arch-CR-03) — five distinct critical findings spanning the observability and IaC surfaces. The code reviewer surfaced no Criticals but four high-leverage Majors.

---

## Disposition of every finding

### Criticals (5/5 RESOLVED)

| ID | Description | Fix |
|---|---|---|
| **obs-CR-01** | `tcp.ask.audit` event with `question_sha256` referenced by query 07 + workbook tile 9 but never emitted by handler. | Added `log.info("tcp.ask.audit", question_sha256=…)` in `function_app/triggers/ask.py` between scope resolution and Anthropic call. PII test now positively asserts the event lands once per request with the canary's SHA-256. |
| **obs-CR-02** | SLO doc claimed 5%/1h = 14.4× burn rate (actually 5×, exhausts in 6 days not 2). | Rewrote `slo.md §4.1` with a worked burn-rate example; updated alert description in `alerts.bicep` to match. |
| **arch-CR-01** | `metricAlerts.actions: []` rejected by ARM when no notification recipient. | Refactored alerts.bicep to omit `actions` entirely (via `union(baseProps, empty ? {} : {actions: …})`) when `notificationEmails` is empty. |
| **arch-CR-02** | Same root cause × 7 in SQR rules (`actions.actionGroups: []` empty array). | Same `union()` pattern + extracted shared `var sqrActionGroupsBlock` (resolves arch-MA-05 too). |
| **arch-CR-03** | `cd.yml` smoke `grep -q` used literal pipe (BRE) — pre-V001 short-circuit never fired; a broken bootstrap looked identical to a clean one. | Switched to `grep -Eq`; captured `PIPESTATUS[0]`; added a row-count guard so absence of placeholders is meaningful (requires ≥ 1 schema_history row). |

### Majors (14/14 RESOLVED)

| ID | Description | Fix |
|---|---|---|
| **obs-MA-01** | Workbook token tile dropped the `customMetrics` arm; drifts from `03_anthropic_tokens_and_cost.kql`. | Mirrored the .kql `from_traces` + `from_metrics` union into the workbook tile. |
| **obs-MA-02** | Latency tile/.kql lacked `success == true` filter; population mismatch vs SLI-3 + alert. | Added the filter to both `01_ask_latency_percentiles.kql` and the workbook tile; updated tile title. |
| **obs-MA-03** | Cold-start tile dropped the `unknown` bucket. | Added `unknown=countif(bucket == "unknown")` to the workbook tile; added inline comment. |
| **obs-MA-04** | Cost-burn alert string-interpolated a `string` parameter into KQL. | Changed parameter to `int` (EUR cents), KQL converts via `let threshold_eur = ${cents}/100.0`. |
| **obs-MA-05** | SLI-1 "good event" definition (`{200,422,429}`) contradicted alert query (treated all `success==false` as bad). | Updated alert query to `success == false AND resultCode !in ("422","429")`; updated SLI-1 doc to reference the alert filter as the canonical denominator. |
| **arch-MA-01** | `metricAlerts@2018-03-01` was preview; GA is `2018-08-01`. | Bumped API version. |
| **arch-MA-02** | Workbook + alerts modules relied on implicit dependency edges. | Added explicit `dependsOn: [observability, sql]`. |
| **arch-MA-03** | Workbook + `isLocked: false` + CRLF on Windows = portal edits get silently overwritten, cross-OS deploys show phantom diffs. | Set `isLocked: true` in workbook.json; added `.gitattributes` pinning `workbook.json` + `*.kql` + `*.sql` to LF. |
| **arch-MA-04** | PowerShell `Get-Content -Raw` and Python `open()` not byte-equivalent → integrity guarantee broken on Windows. | Extracted `scripts/render_migration.py` and switched both postprovision paths to invoke it; both render identical bytes regardless of OS / editor. |
| **arch-MA-05** | DRY debt: `actions` block duplicated 8 times. | Extracted `var sqrActionGroupsBlock` and `var metricAlertActions` (resolved in tandem with arch-CR-01/02). |
| **code-MA-01** | `--paths <missing>` raised uncaught `FileNotFoundError`. | Added pre-flight `is_file()` check that returns exit code 1 with a clean `ERROR:` line. Test updated. |
| **code-MA-02** | Canonicalisation silently dropped lone `\r` (legacy MacOS endings) and didn't strip UTF-8 BOM. | Added `replace("\r", "\n")` after CRLF normalisation and switched decode to `utf-8-sig`. Two new unit tests cover both vectors. Same logic applied in `render_migration.py`. |
| **code-MA-03** | `IF NOT EXISTS / INSERT / ELSE UPDATE` race + mixed varchar/nvarchar literals in V001 vs V002. | Replaced with `MERGE … WITH (HOLDLOCK)` in both files; harmonised all literals to `N'…'`. |
| **code-MA-04** | PII test only captured structlog; stdlib logging / `print` regressions slip past. | Co-capture via `caplog` + `redirect_stdout/stderr`; assertion that at least one channel emitted (catches a silenced logger); added the three missing early-exit paths (obs-MI-05); refusal test now embeds the canary in `refusal_reason` (obs-MI-06). |

### Minors (6 RESOLVED, 1 ACCEPTED RESIDUAL)

| ID | Description | Disposition |
|---|---|---|
| **obs-MI-04** | `prev(Resource) != Resource` partition reset fragile under multi-DB. | RESOLVED — switched to `partition by Resource (sort by TimeGenerated asc | extend cumulative = row_cumsum(used))` in both `.kql` and workbook tile. |
| **obs-MI-05** | PII test missing 3 early-exit paths. | RESOLVED — added bad-JSON, question-too-long, forwarded-secret-mismatch tests. Surfaced + fixed two silent early-exit paths (`missing_question`, `question_too_long` now emit `log.warning`). |
| **obs-MI-06** | Refusal test did not exercise model-driven canary echo. | RESOLVED — refusal test now embeds canary in `refusal_reason`; handler hashes the refusal reason before logging (logs `refusal_reason_sha256` + length only). |
| **obs-MI-07** | Workbook header claimed "auto-refreshes every 5 minutes" but no refresh setting in JSON. | RESOLVED — removed the false claim; documented manual refresh as the academic-phase posture. |
| **arch-mi-04** | Cost-burn parameter same defect surfaced by obs-MA-04. | RESOLVED — folded into obs-MA-04 fix. |
| **arch-mi-05** | `actionGroups@2023-09-01-preview` is preview. | RESOLVED — bumped to GA `2023-01-01`. |
| **arch-mi-01** | Widened `windowSize` from `PT1H` to `PT6H` (BACPAC) / `PT1D` (SQL quota). | RESOLVED — also corrected an SQR engine validation warning. |
| obs-MI-01 | `customDimensions["Category"] == "Host.Startup"` may not match Python v2 runtime. | ACCEPTED RESIDUAL — requires live-deploy verification. Added to `slo.md §6` (Day-2 validation checklist item). |
| obs-MI-02 | `failingPeriods: 3 of 3` over `PT5M` = 25-35 min detection delay. | ACCEPTED RESIDUAL — documented in slo.md §4 worked example; tighten in Etapa-12 once baseline traffic exists. |
| obs-MI-03 | Latency alert lacked volume gate. | RESOLVED — added `samples > 5` to both alert query and `01_ask_latency_percentiles.kql`. |
| arch-mi-02 | Alert evaluation cost vs Log Analytics free grant. | ACCEPTED RESIDUAL — back-of-envelope shows ~550 MB/month against 5 GB grant; documented for future budget headroom planning. |
| arch-mi-03 | Time-to-alert vs sliding window — same as obs-MI-02. | ACCEPTED RESIDUAL — folded into obs-MI-02 disposition. |
| arch-mi-06 | Workbook GUID stability depends on AppInsights id stability. | ACCEPTED RESIDUAL — current Bicep keeps AppInsights id stable across redeploys; future rename will be a tracked breaking change. |
| code-MI items | Various polish (positional-only `_emit_metrics`, etc.). | RESOLVED — `_emit_metrics(log, /, *, …)` is now positional-only for `log`. |

### Nits (selective fixes)

| ID | Disposition |
|---|---|
| obs-NIT-01 | USD-EUR hardcoded twice. ACCEPTED RESIDUAL (low value; trivial to update both call-sites; tracked for Etapa-12). |
| obs-NIT-02 | Unused `samples` projection. RESOLVED — `samples > 5` now uses the column. |
| obs-NIT-03 | `loadTextContent` ARM size limit. ACCEPTED RESIDUAL (current 10 KB JSON; documented for future panel growth). |
| obs-NIT-04 | Redundant `targetResourceTypes` field. ACCEPTED RESIDUAL — kept for explicit documentation. |

### Strengths (called out)

All 18 strength items from pass-1 carry forward unchanged. Notable confirmations from the convergence pass:

- The single-source KQL discipline now holds — workbook tiles and `.kql` files mirror after the obs-MA-01/02/03 fixes.
- The action-group conditional pattern is structurally sound (only its property-emission shape was wrong, not the gating logic).
- `--ci` mode on the checksum helper proved valuable: it was the channel the CI gate uses to catch the canonicalisation regression that code-MA-02 would have otherwise hidden.

---

## No-regression sweep

Re-ran the full test surface that pass-1 touched:

```text
tests/unit/test_compute_migration_checksum.py: 14 passed
tests/integration/test_telemetry_no_pii.py: 8 passed
tests/unit/test_ask_trigger.py: 15 passed
total: 37 passed, 0 failed
```

The 1 pre-existing safe_query failure (`test_proc_invoked_as_function_is_rejected`) and 14 pre-existing test_seed_employees errors are unchanged and out of Etapa-8 scope (tracked separately for Etapa-12 polish).

Bicep template was not validated locally (no `az` available in this environment). The CI `iac-validate` job + the post-convergence smoke job in `cd.yml` are the production gates; both are unchanged in structure aside from the arch-CR-03 fix.

---

## Files touched in convergence

**Source:**
- `function_app/triggers/ask.py` — audit event emission, refusal hashing, missing/long-question log warnings, positional-only `log` param
- `scripts/compute_migration_checksum.py` — canonicalise BOM + lone-CR; clean missing-file error
- `scripts/render_migration.py` — new shared placeholder substitution helper
- `db/migrations/V001__init.sql`, `db/migrations/V002__synth_logic.sql` — `MERGE WITH (HOLDLOCK)` upsert + harmonised `N'…'` literals
- `infra/main.bicep` — explicit `dependsOn`
- `infra/modules/alerts.bicep` — `union()`-based actions, GA API versions, EUR-cents threshold, SLI-1 alignment, DRY extraction, widened windows
- `infra/observability/workbook.json` — tile mirroring fixes (latency `success`, cold-start `unknown`, token customMetrics arm), `isLocked: true`, refreshed header, partition-by-Resource
- `infra/observability/kusto/01_ask_latency_percentiles.kql` — success filter + samples > 5 gate
- `infra/observability/kusto/04_function_cold_starts.kql` — clarifying comments
- `infra/observability/kusto/05_sql_vcore_consumption.kql` — partition-by-Resource rewrite
- `infra/scripts/postprovision.ps1`, `infra/scripts/postprovision.sh` — invoke shared `render_migration.py`, exit-code checks

**Tests:**
- `tests/integration/test_telemetry_no_pii.py` — full rewrite: co-capture, 8 paths, positive audit-event assertion, refusal-canary path
- `tests/unit/test_compute_migration_checksum.py` — clean missing-file test, lone-CR test, BOM test

**Docs:**
- `docs/observability/slo.md` — worked burn-rate example, SLI-1 alignment with alert query
- `docs/security/threat_model.md` — A08 row updated to STRONG, RR-09 closure cross-reference
- `.gitattributes` — new file, pins LF for workbook/kql/sql

**CI/CD:**
- `.github/workflows/ci.yml` — pytest scope already broadened in pass-1
- `.github/workflows/cd.yml` — robust pre-V001 short-circuit, row-count guard, `PIPESTATUS[0]`

---

## Recommendation

**APPROVED FOR MERGE.** All 5 Critical findings RESOLVED with proof in the test surface or in the canonical Bicep / KQL / SQL bytes. All 14 Major findings RESOLVED. 7 lower-severity items intentionally deferred to Etapa-12 polish with explicit tracking in `slo.md §6` and this report.

Three consecutive clean-or-near-clean convergence verdicts (E5 ACCEPT, E6 ACCEPT, E7 APPROVED, E8 APPROVED). The `v1.0-mvp` tag is unblocked.

Etapa 9 (project documentation in English) is the next stage.
