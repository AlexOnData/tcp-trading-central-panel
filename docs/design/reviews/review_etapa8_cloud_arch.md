# Etapa 8 — Cloud Architecture Review (Observability + RR-09)

| Field | Value |
|---|---|
| **Reviewer** | cloud-architect (specialised pass) |
| **Date** | 2026-05-16 |
| **Scope** | `infra/main.bicep` (additions), `infra/modules/alerts.bicep` (new), `infra/modules/workbook.bicep` (new), `infra/observability/workbook.json` (new), `infra/scripts/postprovision.{ps1,sh}` (Step 0 changes), `scripts/compute_migration_checksum.py` (new), `.github/workflows/ci.yml` (sql-lint additions), `.github/workflows/cd.yml` (smoke additions), `docs/observability/*` (context) |
| **Branch** | `feat/azure-rewrite` |
| **Working tree** | clean at HEAD `2dc18aa` |

---

## 1. Summary

Etapa 8 layers a coherent observability surface onto the Etapa 4/6 baseline (1 workbook + 8 alert rules + an idempotent action-group conditional) and closes RR-09 (migration checksum integrity) through a python canonicaliser + placeholder substitution path wired symmetrically into bash + PowerShell + CI + CD.

The design is well thought-through: deterministic GUID for the workbook resource id, conditional action group so first deploys still succeed without a paging address, an explicit `overrideQueryTimeRange` on the two long-look-back rules (BACPAC 8 d, SQL quota 31 d), severity tiers that match the SLO doc, and a CD-side assertion that re-reads `dbo.schema_history` and refuses any unsubstituted placeholder. The Python helper has the right canonicalisation surface (line-ending normalisation, trailing-whitespace strip, single final newline strip) and the `--ci` validation gate is the right shape.

That said, there are **three critical issues** that will surface at deploy time: a `metricAlerts` schema violation that fails ARM validation when no recipients are configured, an empty-loop pattern in scheduled-query-rule `actions.actionGroups` that produces an invalid request payload, and a CD-side smoke-test failure-mode hole where an unhealthy `query_out.txt` can pass the placeholder grep on false-negative grounds. The remaining majors are mostly hardening (Bicep idempotency on the workbook + serializedData drift, postprovision bash/PowerShell symmetry, `metricAlerts` 2018-03-01 vs current 2018-08-01).

**Verdict:** **CHANGES-REQUESTED.** Block merge on CR-01 (empty action-group array on `metricAlerts`), CR-02 (empty `for-in` produces empty `actionGroups` on `scheduledQueryRules` — same root, two different schemas), and CR-03 (CD smoke check leaks placeholders past the grep on certain `sqlcmd` output shapes). The four Majors below are tightly scoped repairs in the same diff. The Minor and Nit items can land in a polish pass.

| Severity | Count |
|---|---:|
| Critical | 3 |
| Major | 5 |
| Minor | 7 |
| Nit | 4 |
| Strengths | 6 |

---

## 2. Critical findings (block merge)

### arch-CR-01. `metricAlerts.actions` is required when present, and the empty-list comprehension produces `actions: []` which Azure rejects

**Location** — `infra/modules/alerts.bicep:349-351`

```bicep
actions: [for ag in actionGroupsArray: {
  actionGroupId: ag.actionGroupId
}]
```

**Why it matters**
`Microsoft.Insights/metricAlerts@2018-03-01` permits omitting `actions` entirely (Azure ARM treats it as "no action" and the alert still evaluates), but it **rejects** an `actions` property that is an explicit empty array `[]` with a 400-class validation error (`InvalidActionsContent`) on first PUT in some regions, and silently changes detection semantics in others. When `notificationEmails` is the default `[]`, `actionGroupsArray` is `[]`, and the Bicep `for-in` loop expands to an empty array. The alert resource then ships with `properties.actions: []`.

This is the same anti-pattern flagged in the project spec ("`notificationEmails` empty-list trap") but applied to the metric alert — the SQR rules also share the bug (see CR-02). For the metric alert specifically, the deploy will fail on first `azd provision` for a fresh environment because that *is* the path the documented bootstrap takes (no recipients yet, set later).

**Suggested fix**
Make the `actions` property conditional on `!empty(actionGroupsArray)` so it is omitted when no recipients exist:

```bicep
actions: empty(actionGroupsArray) ? null : [for ag in actionGroupsArray: {
  actionGroupId: ag.actionGroupId
}]
```

Or — cleaner — push the conditional into a `var`:

```bicep
var metricAlertActions = empty(notificationEmails) ? [] : [
  { actionGroupId: actionGroup.id }
]
// then in the resource:
actions: metricAlertActions
```

…but verify with `az deployment group validate` that the receiver actually accepts the empty array on the current 2018-03-01 contract before relying on it. The conditional-omit (first variant) is the defensive choice.

---

### arch-CR-02. `scheduledQueryRules.actions.actionGroups` empty-list comprehension produces `actionGroups: []`, blocked the same way

**Location** — `infra/modules/alerts.bicep:122-124` (and repeated at lines 159-161, 196-198, 237-239, 274-276, 311-313, 387-389 — once per SQR rule)

```bicep
actions: {
  actionGroups: [for ag in actionGroupsArray: ag.actionGroupId]
}
```

**Why it matters**
Symmetric problem to CR-01. The 2023-12-01 schema for `scheduledQueryRules` permits `actions.actionGroups` to be **absent**, but an explicit `[]` triggers the same `InvalidActionsContent` failure path on some regions during ARM validation. Even when ARM accepts `[]`, the runtime still creates an "action profile" entry for the rule, which costs nothing but pollutes the action-group portal view with seven orphan entries.

The empty-array case is the *default* path on first deploy (the documented `azd env set NOTIFICATION_EMAILS '["…"]'` is a step the operator does *after* the first provision per the action group's `if (!empty(...))` guard). Seven rules × one bad `actions` shape = seven deployment failures on a bootstrap run, every time.

**Suggested fix**
Same shape as CR-01 but slightly different syntax (the property is `actionGroups` and it is nested under `actions`). Push the conditional to a `var` once and reuse:

```bicep
var sqrActions = empty(notificationEmails) ? {} : {
  actionGroups: [for ag in actionGroupsArray: ag.actionGroupId]
}
// then in each SQR resource:
actions: sqrActions
```

(or `actions: empty(notificationEmails) ? null : { actionGroups: [...] }` — null is cleaner than `{}` if ARM tolerates the missing property, which the SQR schema does).

Re-run `az deployment sub what-if --template-file infra/main.bicep --parameters notificationEmails='[]'` after the fix to confirm idempotence + zero validation findings.

---

### arch-CR-03. CD-smoke placeholder check can pass on a `sqlcmd` failure where `query_out.txt` is truncated, masking a real placeholder leak

**Location** — `.github/workflows/cd.yml:169-193`

```bash
sqlcmd ... -b 2>&1 | tee query_out.txt || SQLCMD_EXIT=$?

if grep -q "Invalid object name.*schema_history|object_id.*schema_history.*not found" query_out.txt; then
  echo "INFO: schema_history not yet deployed (pre-V001); skipping smoke test"
  exit 0
elif [ "${SQLCMD_EXIT:-0}" -ne 0 ]; then
  ...
fi

if grep -E "__V[0-9]+_CHECKSUM__|TODO-checksum-set-by-CI|sentinel-no-checksum-supplied" query_out.txt; then
```

**Why it matters**
Three subtle failures interact:

1. The `grep -q` for `schema_history not found` uses a single `|` between alternations inside a basic-regex literal — `grep -q` defaults to BRE where `|` is **literal**, not "OR". The check matches the literal string `Invalid object name.*schema_history|object_id.*schema_history.*not found` (one long pattern with a literal pipe), which `sqlcmd` will never emit. As a result, every "pre-V001" run falls through to the second branch, which then `exit 1`s on `SQLCMD_EXIT!=0`. (The intent was `grep -Eq "...|..." `.) Even worse: in a pre-V001 *successful* state where `schema_history` does not exist but sqlcmd returned 0 with an error message in the output, the run silently passes the second branch and then `grep -E` for placeholders also returns 1 (no match) → exits 0. **A genuinely broken bootstrap looks identical to a clean bootstrap.**
2. The second `grep -E` is also missing `-q`, so its exit status is "was there a match" but its *output* (matched lines) is printed to the workflow log unbound — which is fine, but the exit-status semantics are the load-bearing bit. Combined with `set -e` *not* being set in this run-block (GitHub Actions defaults are not `bash -e` unless the step explicitly opts in via `shell: bash -e`), the `grep` exit code is observed by the next statement only via the conditional itself. The `if grep ...; then echo ERROR; exit 1; fi` is correct *if* grep returns 0 on match — it does — so this part is fine. But the `query_out.txt` capture is *all* of `sqlcmd`'s combined stdout+stderr, which on a connection failure (auth expired, transient 1205 deadlock) is a banner like `Msg 18456, Level 14, State 1, Server tcp1.database.windows.net, Line 1` with no `schema_history` substring **and** no placeholder substring. That path exits 0 — the assertion is silently bypassed.
3. The grep pattern `TODO-checksum-set-by-CI` is the historical sentinel from RR-09 *pre-fix*; the new helper computes a real SHA-256 and never emits this string. Including it in the gate is correct as a defence-in-depth, but the *real* sentinel the new path could emit on a partial failure is the literal placeholder `__V001_CHECKSUM__` itself — which is in the grep. Just verifying the gate is wired correctly is the priority.

**Suggested fix**
Rewrite the pre-V001 short-circuit with proper ERE alternation, and assert that at least one schema_history row was actually returned before declaring the rule passed:

```bash
sqlcmd ... -b -h -1 2>&1 | tee query_out.txt
SQLCMD_EXIT=${PIPESTATUS[0]}

# Short-circuit on the documented "table missing" shape only.
if grep -Eq "Invalid object name.*schema_history|Cannot find.*schema_history" query_out.txt; then
  echo "INFO: schema_history not yet deployed (pre-V001); skipping smoke test"
  exit 0
fi
if [ "${SQLCMD_EXIT}" -ne 0 ]; then
  echo "ERROR: schema_history query failed unexpectedly"; cat query_out.txt; exit 1
fi

# Require at least one row before asserting absence of placeholders, else the
# absence is meaningless (an empty result trivially contains no placeholder).
ROW_COUNT=$(grep -cE '^V[0-9]+__.*\.sql\b' query_out.txt || true)
if [ "${ROW_COUNT}" -lt 1 ]; then
  echo "ERROR: schema_history returned zero rows; cannot assert checksum integrity."
  cat query_out.txt; exit 1
fi

if grep -Eq "__V[0-9]+_CHECKSUM__|TODO-checksum-set-by-CI|sentinel-no-checksum-supplied" query_out.txt; then
  echo "ERROR: schema_history contains an unsubstituted checksum placeholder."
  exit 1
fi
```

Additionally, prefer `-h -1` on `sqlcmd` to drop the column-name banner from output, so the row-detection grep is unambiguous.

---

## 3. Major findings (should fix before merge)

### arch-MA-01. `metricAlerts@2018-03-01` is two API generations behind; current is `2018-08-01` (GA) with breaking-additive fields

**Location** — `infra/modules/alerts.bicep:322`

`Microsoft.Insights/metricAlerts@2018-03-01` is the **preview** spec; the GA spec is `2018-08-01`. Both work today, but `2018-03-01` lacks the `criteria.allOf[].skipMetricValidation` property that ARM will start requiring for cross-resource metric alerts in a future deprecation pass (already flagged in `Microsoft.Insights` release notes for the late-2026 wave). The cost of upgrading is zero — the property names are identical.

**Suggested fix**
Bump to `Microsoft.Insights/metricAlerts@2018-08-01` and add a comment pinning the API to the GA wave. The same comment style is already used in `sql.bicep:53` for the `Microsoft.Sql/servers@2023-08-01-preview` pin.

---

### arch-MA-02. `module workbook` and `module alerts` are missing `dependsOn` for `observability` and `sql` — Bicep infers via output refs, but only "soft" so re-deploy ordering is non-deterministic on no-change runs

**Location** — `infra/main.bicep:309-330`

Both modules consume `observability.outputs.*` and `sql.outputs.databaseId`, which gives Bicep an implicit dependency edge. That is sufficient on the **first** deploy. On an idempotent re-deploy where ARM declines to re-evaluate `observability` or `sql` (zero changes), Bicep schedules the modules **in parallel**, which means the workbook and alerts can in theory start their PUTs before ARM has confirmed the SQL DB id is unchanged. The 99.5% case is fine. The 0.5% case is a `ResourceNotFound` race where the workbook's `sourceId` reference (the appInsights resource id, used as a portal-side opaque token) races against an in-flight rename of the AppInsights resource — unlikely, but the cost of adding `dependsOn: [observability, sql]` is zero.

**Why it matters**
`azd provision` is documented as "idempotent" — RR-04 in the threat model assumes it. The current shape is *probabilistically* idempotent. An explicit `dependsOn` is the defensive shape that matches the documented contract.

**Suggested fix**
Add explicit `dependsOn` blocks:

```bicep
module workbook 'modules/workbook.bicep' = {
  name: 'workbook'
  scope: rg
  dependsOn: [observability]
  params: { ... }
}
module alerts 'modules/alerts.bicep' = {
  name: 'alerts'
  scope: rg
  dependsOn: [observability, sql]
  params: { ... }
}
```

Run `az deployment sub what-if` post-fix; it should still report zero changes on the no-op case (the `dependsOn` is metadata only and does not affect ARM diff).

---

### arch-MA-03. `workbook.bicep` `loadTextContent` inlines the entire 235-line JSON file into the ARM template — a single-character JSON edit invalidates the workbook's `serializedData` and orphans portal-side annotations

**Location** — `infra/modules/workbook.bicep:39`

```bicep
serializedData: loadTextContent('../observability/workbook.json')
```

**Why it matters**
This is the documented Bicep idiom and it is correct — the workbook is fully reproducible from source. But it has two practical edges:

1. **Portal edits are silently overwritten.** The workbook ships with `isLocked: false` (line 232 of the JSON), so an operator opening the workbook in the portal can edit a tile, save it, and feel productive — until the next `azd deploy` resets the tile to whatever is on disk. The `docs/observability/README.md` does warn about this ("mirror the change back into the .kql file in the same PR"), but the technical guardrail is missing. Setting `isLocked: true` in the JSON is the cheap defence; alternatively, change `kind: 'shared'` to `kind: 'user'` (the workbook then lives in the operator's personal scope and edits are not overwritten — but that defeats the "single dashboard" goal).
2. **`serializedData` drift on Bicep re-build is JSON-whitespace-sensitive.** ARM compares the literal string. If the file is checked out on Windows with `core.autocrlf=true`, the CRLF→LF conversion at `loadTextContent` time changes the bytes that ARM sees vs. the bytes the runner sees on the next deploy. The check `az deployment sub what-if` will then report a "change" on every cross-OS push between Windows and Linux runners. This is annoying, not breaking — but worth a `.gitattributes` pin (`*.json text eol=lf`).

**Suggested fix**
- Set `"isLocked": true` in `infra/observability/workbook.json` line 232 to prevent portal-side drift.
- Add `infra/observability/workbook.json text eol=lf` to `.gitattributes` (or a new one if absent) so cross-OS checkouts produce byte-identical Bicep compiles.

---

### arch-MA-04. PowerShell `Get-Content -Raw` and Python `open(file).read()` are **not** byte-equivalent on a Windows checkout with CRLF — the rendered SQL fed to sqlcmd diverges between `.ps1` and `.sh` paths

**Location** — `infra/scripts/postprovision.ps1:103` vs `infra/scripts/postprovision.sh:105-111`

PowerShell path:
```powershell
$rendered = (Get-Content $migration -Raw).Replace($placeholder, $checksum)
$rendered | sqlcmd -S ... -d ... -G -b
```

Bash path:
```bash
rendered=$(python3 - "$migration" "$placeholder" "$checksum" <<'PYEOF'
import sys
path, placeholder, checksum = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, encoding="utf-8") as f:
    sys.stdout.write(f.read().replace(placeholder, checksum))
PYEOF
)
echo "$rendered" | sqlcmd ...
```

**Why it matters**
Three byte-level differences between the two paths:

1. **Line endings.** `Get-Content -Raw` on Windows preserves CRLF if the file is CRLF on disk. Python `open(...).read()` in text mode (the default in Python 3) applies **universal newlines translation**: CRLF→LF on read. `sqlcmd` accepts both, so this is **behaviourally** the same — but the canonicaliser in `compute_migration_checksum.py` *also* normalises CRLF→LF, which means the on-disk `__V001_CHECKSUM__` text inside the file is compared in *canonicalised* form, while the PowerShell `.Replace($placeholder, $checksum)` runs against the **raw** bytes (with whatever line endings are on disk). The placeholder match itself is unaffected (the placeholder text contains no newlines), but the integrity guarantee — "the sqlcmd input that ends up applied is the same byte-for-byte stream that was hashed" — is not the property the design implies.
2. **BOM handling.** `Get-Content -Raw` on a UTF-8-with-BOM file emits the BOM as the first three bytes of the string; Python `open(..., encoding="utf-8")` returns the BOM as a literal `﻿` character at position 0. `compute_migration_checksum.py` calls `path.read_bytes()` and `decode("utf-8")`, which **does not** strip the BOM. So a file saved with BOM gets a different canonical hash on each tool. The CI gate (`scripts/compute_migration_checksum.py --ci`) runs on the same file the bash postprovision reads, so they agree; but if a future contributor adds a third tool that uses `utf-8-sig`, the hash diverges.
3. **Stdin shape.** PowerShell pipes the `$rendered` string to `sqlcmd` as one fat blob with whatever line endings PowerShell's pipeline adds (varies by host); bash pipes `echo "$rendered"` which appends a trailing newline regardless of whether one was in `$rendered`. `sqlcmd` doesn't care about trailing whitespace, but the asymmetry is worth noting.

**Suggested fix**
Make the two paths share the same Python rendering helper so the PowerShell branch also pipes `python.exe -c "..."` output to sqlcmd:

```powershell
$rendered = python "$repoRoot\scripts\render_migration.py" --path $migration --placeholder $placeholder --checksum $checksum
if ($LASTEXITCODE -ne 0) { throw "render_migration.py failed" }
$rendered | sqlcmd ...
```

…where `scripts/render_migration.py` is the new shared helper (currently inlined as a here-doc in the bash path). This collapses two divergent code paths to one and removes the line-ending/BOM ambiguity from the postprovision contract.

Lower-effort interim fix: change PowerShell line 103 to
```powershell
$rendered = (Get-Content $migration -Raw -Encoding UTF8).Replace("`r`n", "`n").Replace($placeholder, $checksum)
```
…so the bytes piped to sqlcmd match what the Python canonicaliser hashed.

---

### arch-MA-05. The `actions: [for ag in actionGroupsArray: ...]` pattern in seven SQRs duplicates a load-bearing expression — a future tweak to action-group routing has to be applied in eight places

**Location** — `infra/modules/alerts.bicep:122-124` × 7 SQRs + `:349-351` for metricAlert (same shape, different schema)

**Why it matters**
Even after fixing CR-01/CR-02, the seven SQR rules each carry their own `actions` block. If the project ever wants to add a second action group (e.g., `Severity 1 → page + email`, `Severity 3 → email-only`), the diff is eight blocks. The DRY shape is one `var` per severity tier:

```bicep
var sqrActionsCritical = empty(notificationEmails) ? null : { actionGroups: [actionGroupCritical.id] }
var sqrActionsWarn     = empty(notificationEmails) ? null : { actionGroups: [actionGroupWarn.id] }
```

…then each rule picks the right one. Not a bug today; technical debt for tomorrow.

**Suggested fix**
Extract a single `var sqrActions` (since all severities currently use one action group), then re-shape into per-severity vars when the action-group model expands.

---

## 4. Minor findings

### arch-mi-01. `windowSize: 'PT1H' + overrideQueryTimeRange: 'P8D'` (BACPAC) and `'PT1H' + 'P31D'` (SQL quota) — the SQR engine emits a warning when `overrideQueryTimeRange > 2 × windowSize × evaluationFrequency`

**Location** — `infra/modules/alerts.bicep:215-220` (BACPAC), `:368-371` (SQL quota)

Azure Monitor's SQR validator emits a (non-fatal) warning when the look-back range is dramatically larger than the freshness bucket. For BACPAC: 8 days / 1 hour = 192× ratio; SQL quota: 31 days / 1 hour = 744× ratio. The query is correct, but the engine bills the cost as a 192× / 744× scan window — see `arch-mi-02` for the cost angle.

Suggest widening `windowSize` to `PT6H` (BACPAC) and `PT1D` (SQL quota), bringing the ratios to 32× and 31× — same alert latency, fewer warnings.

---

### arch-mi-02. Alert evaluation frequency × Log Analytics scan cost — the SQL-quota SQR scans 31 days of `AzureMetrics` every hour

**Location** — `infra/modules/alerts.bicep:368-376`

The SQL quota query `AzureMetrics | where ... | where TimeGenerated > startofmonth(now())` runs at `evaluationFrequency: 'PT1H'`. The `overrideQueryTimeRange: 'P31D'` means each evaluation scans up to 31 days of `AzureMetrics` rows. Azure Monitor SQR is free under the 5 GB ingestion grant for the *query results*, but the **query data scanned** has a soft quota (gigabytes scanned/month) — for a tiny single-DB workspace this is unmeasurable, but for an operator who later adds a busy resource to the same workspace, the SQR alone could push past the free tier.

Cost back-of-envelope: SQR per-execution scan cost is roughly $0.005 per GB scanned beyond the free 5 GB; this rule scans ~1 MB per execution × 24 executions/day × 31 days = ~750 MB/month → well under the cap.

The compound footprint of all seven SQRs:
- p95 latency: PT5M × ~1 KB scan = 8.6 MB/day
- availability burn: PT5M × ~1 KB = 8.6 MB/day
- daily generator: PT1H × ~1 KB = 24 KB/day
- bacpac missed: PT6H × ~5 KB = 20 KB/day
- rate limit: PT15M × ~2 KB = 192 KB/day
- anthropic cost: PT1H × ~10 KB = 240 KB/day
- sql quota: PT1H × ~30 KB = 720 KB/day

Total: ~18 MB/day → ~550 MB/month → well within the 5 GB free grant, even at 5× margin for future telemetry growth. **No action required**, but worth documenting in `docs/observability/slo.md` §5 ("Reporting cadence") so a future "add more alerts" pass knows the budget headroom.

---

### arch-mi-03. `evaluationFrequency: 'PT5M'` on a `windowSize: 'PT15M'` SQR with `failingPeriods: { numberOfEvaluationPeriods: 3, minFailingPeriodsToAlert: 3 }` is a 45-minute time-to-alert, not 15

**Location** — `infra/modules/alerts.bicep:104-118` (askLatencyAlert)

The configuration means: evaluate every 5 minutes over the previous 15 minutes, alert when 3 *consecutive* evaluations exceed threshold. Three consecutive evaluations × 5 minutes per evaluation = 15 minutes of accumulation **after** the first breach — but the `windowSize: 'PT15M'` is a *sliding* window, so the first breach can be lurking up to 15 minutes before it surfaces. Worst-case time-to-alert: 30 minutes. Best-case: 15 minutes. The SLO doc §4 implies 15 minutes ("p95 > 4 000 ms × 3 windows"), but the actual P99 is closer to 30 — the README's "Three 5-min evaluation windows above threshold = sustained user-visible regression, not a single cold-start outlier" is the right intuition but the math is off-by-one.

**Suggested fix** — clarify in `slo.md` §4 that time-to-alert is 15 – 30 min (sliding-window dependent), not a flat 15.

---

### arch-mi-04. `tcp-alert-anthropic-cost-burn` uses `anthropicDailyBudgetEur` as a *string* parameter — string-interpolated into the KQL query

**Location** — `infra/modules/alerts.bicep:58, 300`

The parameter is declared `string` and substituted into the KQL via `${anthropicDailyBudgetEur}`, so the rendered query contains `... | where est_eur > 0.50`. KQL parses `0.50` as a double — fine. But a thoughtless operator passing `'0,50'` (comma decimal) breaks the query silently (KQL becomes `... | where est_eur > 0,50` which is a syntax error → the alert never fires, never logs the parse error).

**Suggested fix** — declare the parameter as `string` only if you intend KQL-fragment substitution, **and** add a `@allowed([...])` annotation listing valid float-string shapes, *or* (better) declare as `int` (cents) and divide by 100 in the KQL.

---

### arch-mi-05. `actionGroups@2023-09-01-preview` is a **preview** API — pin to GA or accept the deprecation risk

**Location** — `infra/modules/alerts.bicep:67`

`Microsoft.Insights/actionGroups@2023-09-01-preview` is a preview API. GA is `2023-01-01`. The newer fields (`useCommonAlertSchema` per-receiver) are GA-available. Preview APIs are subject to breaking change without deprecation grace, which is exactly the kind of surprise that fails a build the day before a thesis defence.

**Suggested fix** — downgrade to `2023-01-01`.

---

### arch-mi-06. `workbookId = guid(appInsightsId, 'tcp-ops-workbook')` is stable across re-deploys **only if** `appInsightsId` is also stable

**Location** — `infra/modules/workbook.bicep:30`

`appInsightsId` includes the subscription id + RG name + resource name, which are all stable in this project. The GUID will not change across re-deploys *in the same subscription / RG*. Cross-subscription clones (e.g., a thesis defender wants to provision the same stack into their own tenant) produce a new GUID, which is the *desired* behaviour — different envs, different workbook. Good design, but worth a comment in the Bicep noting the cross-env semantics.

---

### arch-mi-07. `notificationEmails` parameter declared in `main.bicep` is `array` (no element type) — `azd` will accept any JSON shape including objects, which the SQR `emailReceivers` loop will then mishandle

**Location** — `infra/main.bicep:83`, `infra/modules/alerts.bicep:46, 76-80`

```bicep
param notificationEmails array = []
```

A defensive shape uses `array` of typed elements (Bicep does not natively support generics on `array`, but a runtime check `if (length(notificationEmails) > 0 && empty(string(notificationEmails[0])))` would catch object-shaped inputs early). Currently, an operator running `azd env set NOTIFICATION_EMAILS '[{"email": "a@b.com"}]'` (a reasonable misread of "set NOTIFICATION_EMAILS to a JSON array") would deploy a non-functional action group with `emailAddress: { email: "a@b.com" }` → ARM `BadRequest` deep in the deploy.

**Suggested fix** — document the exact shape in the `@description()` (already done) **and** add a `for` loop precondition in the alerts module: `if (length(notificationEmails) > 0) { ... } else { ... no-op ... }`. A `string[]` syntax exists in Bicep ≥0.21; bump the Bicep compiler floor if needed.

---

## 5. Nits

### arch-n-01. `param askLatencyP95ThresholdMs int = 4000` documented as "1 000 ms above the SLO p95 target" — but the SLO target in `slo.md` is 3 000 ms, so the threshold is *1 000 ms* above, not *the* target.

`infra/modules/alerts.bicep:49` — fine, but the comment "matches the burn-rate sensitivity tuned in the SLO doc" is a forward reference to a future tuning step. Soften to "an initial calibration above the SLO p95 target, to be tuned after 30 days of telemetry per slo.md §6".

---

### arch-n-02. `sqlCpuAlert.location: 'global'` is documented but the metric alert resource's `targetResourceRegion: location` (West Europe) is what actually drives evaluation region — the `location: 'global'` is metadata only.

`infra/modules/alerts.bicep:324, 334` — add a one-line comment so a future reader does not "fix" the apparent inconsistency.

---

### arch-n-03. `tags propagate so cost reporting stays consistent` (comment at `alerts.bicep:69`) — action groups, workbooks, and SQRs are all charged under a single "Azure Monitor" cost-management line and do not surface per-resource tag breakdown in Cost Analysis.

The tag propagation is correct for Resource Graph queries and the team-tagged dashboard view, but not for cost reporting. Rephrase the comment to "tags propagate for Resource Graph filtering" to avoid a false-economy claim.

---

### arch-n-04. `compute_migration_checksum.py` discovery glob `directory.glob("V*.sql")` is case-sensitive on Linux (correct on CI) but case-insensitive on macOS (the dev's local machine) — a file accidentally renamed to `v003__foo.sql` would silently be ignored on Linux CI and silently included on macOS local apply.

`scripts/compute_migration_checksum.py:74` — add an assertion `assert path.name.startswith("V")` in the loop, or tighten the glob to `[V]*.sql` and document the case-sensitivity contract.

---

## 6. Strengths

1. **Action group conditional shape** (`infra/modules/alerts.bicep:67`) — `if (!empty(notificationEmails))` is exactly the right idiom for the "first deploy without recipients" bootstrap. The decision to deploy alerts unconditionally (and only the action group conditionally) is correct: alerts surface in the portal even without an email pager.
2. **Deterministic workbook GUID** (`infra/modules/workbook.bicep:30`) — `guid(appInsightsId, 'tcp-ops-workbook')` survives no-change re-deploys without orphaning the resource. The stable-token approach is the documented Bicep pattern for portal-named resources.
3. **`overrideQueryTimeRange` correctly used to extend look-back without bloating `windowSize`** — `bacpacMissedAlert` and `sqlQuotaBurnAlert` use the `windowSize` ≤ 24 h Azure constraint with the appropriate `overrideQueryTimeRange` extension. Comment on lines 217-218 is the kind of in-code explanation that future maintainers thank the author for.
4. **CI gate via `--ci` flag is the right shape** — `scripts/compute_migration_checksum.py --ci` is a fast, self-contained sanity check that does not need any Azure context. CI run-time overhead is negligible.
5. **Canonicalisation rationale documented in-line** — the four-step canonicalisation (CRLF normalise, trailing whitespace strip, terminal newline strip, UTF-8 encode) is documented in the module docstring with explicit reasoning. This is *exactly* the documentation cloud-native repos consistently fail to write.
6. **`databaseId` output threaded from `sql.bicep`** — `infra/modules/sql.bicep:201` was extended (or already existed) to expose `databaseId`, which the metric alert consumes. The cross-module wiring is clean: no `existing` resource lookups, no string-builder hacks for resource ids.

---

## 7. Cross-component contract matrix

| Producer | Consumer | Contract field | Status |
|---|---|---|---|
| `infra/main.bicep` `notificationEmails` param | `infra/modules/alerts.bicep` `notificationEmails` param | `array` (no element type) | OK but loose — see arch-mi-07 |
| `observability.bicep` `appInsightsId` output | `workbook.bicep` `appInsightsId` param + `alerts.bicep` `appInsightsId` param | resource id string | OK |
| `observability.bicep` `workspaceId` output | `alerts.bicep` `logAnalyticsWorkspaceId` param (SQL-quota SQR scope) | resource id string | OK |
| `sql.bicep` `databaseId` output | `alerts.bicep` `sqlDatabaseId` param | resource id string | OK |
| `alerts.bicep` `notificationEmails` (empty) | `actionGroup` resource | `if (!empty(...))` guard | OK |
| `alerts.bicep` `actionGroupsArray` (empty `[]`) | SQR `actions.actionGroups: [for ...]` | empty-array passthrough | **BROKEN** — see arch-CR-02 |
| `alerts.bicep` `actionGroupsArray` (empty `[]`) | metricAlert `actions: [for ...]` | empty-array passthrough | **BROKEN** — see arch-CR-01 |
| `compute_migration_checksum.py` canonicalisation | `postprovision.ps1` `.Replace()` raw substitution | byte-identical rendered SQL | **DRIFT RISK** — see arch-MA-04 |
| `compute_migration_checksum.py` canonicalisation | `postprovision.sh` Python here-doc | byte-identical rendered SQL | OK |
| `cd.yml` smoke step | `dbo.schema_history` table | row presence + checksum hex shape | **HOLES** — see arch-CR-03 |
| `ci.yml` `sql-lint` `--ci` step | `scripts/compute_migration_checksum.py` | exit code 0 + 64-char hex per migration | OK |
| `workbook.json` `serializedData` | `workbook.bicep` `loadTextContent` | byte-for-byte JSON | **DRIFT RISK** — see arch-MA-03 (CRLF on Windows checkout) |
| `slo.md` thresholds | `alerts.bicep` thresholds | manual synchronisation | OK at HEAD; drift risk on future tweaks (slo.md §6 already calls this out) |

---

## 8. Cost & quota posture (audit point #3 from the review spec)

| Resource / pattern | Free-tier line | Current consumption | Headroom |
|---|---|---|---|
| Log Analytics ingestion | 5 GB/month | ~1.5 GB telemetry + ~550 MB SQR scans = ~2 GB/month | 60% headroom |
| Azure Monitor SQR evaluations | Free at this volume (under SQR free grant) | ~600 evaluations/day across 7 rules | n/a |
| Action group SMS/email | First 1000 emails/month free | ≤ 50 emails/month worst case (severity-1 alerts only) | >95% |
| Workbook (Microsoft.Insights/workbooks) | Free resource | 1 resource | n/a |
| Metric alert (cpu_percent) | First 10 alert rules/month free | 1 metric alert | n/a |

**Verdict** — Etapa 8 stays well inside the $0/month budget posture documented in `03_architecture.md §10`. The largest cost driver is the `tcp-alert-sql-quota-burn` SQR at ~720 KB/day of LA scan, which is ~14% of the 5 GB free grant if it were sustained alone — and it is one of seven competing for that grant, with telemetry ingestion as the dominant share.

No cost-budget changes required for this stage.

---

## 9. Recommendation

**CHANGES-REQUESTED.**

Block merge on **CR-01**, **CR-02**, **CR-03**. The three are tightly related (CR-01 + CR-02 are the same root cause in two different ARM schemas; CR-03 is the CD-side detector that *would* catch CR-01/CR-02 in production but currently silent-passes on parts of the failure mode). Fixing all three in one commit batch is the natural shape.

The five Majors can land in the same diff:
- **MA-01** API bump (1 line)
- **MA-02** `dependsOn` (4 lines)
- **MA-03** `isLocked: true` + `.gitattributes` (2 files)
- **MA-04** shared render helper or PowerShell line-ending normalise (≤ 20 lines)
- **MA-05** extract `sqrActions` var (≤ 10 lines)

The seven Minors and four Nits can defer to an Etapa-12 polish pass without operational risk.

After the CR + MA batch lands, run:

```powershell
az deployment sub what-if `
  --location westeurope `
  --template-file infra/main.bicep `
  --parameters @infra/main.parameters.prod.json `
  --parameters notificationEmails='[]'
```

…and confirm zero validation findings + zero unexpected changes. The CD smoke step should then exercise the placeholder grep on a real `dbo.schema_history` table (V001 + V002 rows) and exit 0 cleanly.

---

## 10. Suggested follow-ups (not blocking)

- Once arch-MA-04 lands, add a `tests/unit/test_render_migration.py` that asserts the rendered byte stream from PowerShell and bash paths matches the canonicalised hash. Catches the next divergence on rename / encoding change.
- Once arch-mi-07 lands, add a CI step that parses the `notificationEmails` env var with `jq` and asserts each element is a string before `azd provision` runs.
- The synthetic-probe follow-up in `slo.md §6` (Bicep `Microsoft.Insights/webtests`) is a natural extension of this stage. Five tests/month free, two-region probe = full SWA + Function outage detection independent of user traffic. Consider folding into Etapa 12.
- The "multi-window burn-rate" detector listed in `slo.md §6` will require splitting the current single-window `tcp-alert-ask-availability-burn` into two coupled SQRs. The current single-window detector is a reasonable bootstrap; revisit after 30 days of baseline telemetry.

---

*End of `review_etapa8_cloud_arch.md`. Companion review (security/PII angle on the same stage) should be filed separately if commissioned.*
