# Review — Etapa 7: PowerBI Deploy Automation

| Field | Value |
|---|---|
| **Reviewer role** | Cloud architect (deployment automation) |
| **Date** | 2026-05-16 |
| **Branch** | `feat/azure-rewrite` |
| **Artifacts under review** | `powerbi/deploy.ps1`, `powerbi/scripts/Helpers.psm1`, `docs/runbooks/powerbi_deploy.md` |
| **Reference contracts** | ADR-001 (deployment), ADR-003 (RLS contract), `03_architecture.md` §3.3 + §5, `credentials_rotation.md` |
| **Verdict** | **ACCEPT WITH MINOR CHANGES** |

---

## 1. Verdict summary

The Etapa-7 deploy automation is **substantively correct, idempotent, and faithful to ADR-001**. The script wires a clean REST-API path against `https://api.powerbi.com/v1.0/myorg`, acquires a bearer token through the active `az` session against the correct PowerBI resource (`https://analysis.windows.net/powerbi/api`), uses `E. Europe Standard Time` as the scheduled-refresh timezone id (not the PowerBI Service display alias), and chains TMDL compilation through `pbi-tools` → Tabular Editor 3 → pre-built `.bim` with an explicit abort message. Cross-references to ADR-001, ADR-003, and `03_architecture.md` §3.3 / §5 are present in both the script header and the runbook §12 table. Helpers.psm1 has a clean export surface (`Export-ModuleMember -Function 'Get-PowerBIToken','Invoke-PowerBIRequest','Wait-ForImport'` only).

Three categories of issues remain — none block merge, but three of them (M1, M2, M5) should be fixed before a thesis-grade demo, and one (m6) is a documentation-only divergence between `deploy.ps1` and the runbook table.

**Counts**: 0 critical, 5 major, 6 minor, 4 informational. Total **15 findings**.

---

## 2. Audit checklist coverage

| # | Audit item | Status | Notes |
|---|---|---|---|
| 1 | ADR-001 compliance — REST API primary path | PASS | All endpoints in ADR-001 §"Primary path" appear in the script. |
| 1 | ADR-001 compliance — 8 steps implemented | PARTIAL | Script has 7 numbered steps (0–7); runbook §6 table lists 8 steps. See M6. |
| 2 | Idempotency | PASS | Workspace lookup-or-create, 409-swallow on SP Admin grant, `CreateOrOverwrite` import, TakeOver try/catch, `Overwrite` report import. |
| 3 | OIDC preferred + client-secret fallback documented | PASS | `deploy.ps1` lines 137 + 146 warn explicitly; runbook §4.3 vs §4.3b. |
| 3 | Bearer token resource correct | PASS | `Get-PowerBIToken` uses `https://analysis.windows.net/powerbi/api`. |
| 4 | `$ErrorActionPreference = 'Stop'` + `Set-StrictMode` | PARTIAL | Present in `deploy.ps1` (lines 77–78); `Helpers.psm1` has `Set-StrictMode` but lacks `$ErrorActionPreference` at module scope. See m1. |
| 4 | Every REST call has status-code check | PARTIAL | Helper has transient retry + 404 suppress + 401 refresh; raw multipart calls (Step 3 + Step 6) bypass the helper. See M2. |
| 5 | Schedule timezone id | PASS | `E. Europe Standard Time` (not "GMT+02:00 Bucharest"). Comment on line 468–469 explicitly flags the alias trap. |
| 6 | No secrets in source | PASS | All config via `Resolve-Setting` → `azd env` or process env. |
| 7 | TMDL compile chain | PASS | pbi-tools → TE3 → pre-built fallback with multi-line abort message. One dead branch — see m2. |
| 8 | Cross-references | PASS | Script header lines 62–65; helper header lines 23–25; runbook §12 table. |
| 9 | `Helpers.psm1` clean export surface | PASS | Explicit `Export-ModuleMember` — internal `Write-Helper*` not exported. |
| 10 | Runbook — 8 steps in operator order | PARTIAL | Table present but step 7 ("Trigger an immediate refresh") does not match `deploy.ps1` step 7 (verify only). See M6. |
| 10 | Runbook — SP registration with concrete `az` commands | PASS | §4.1–4.7. |
| 10 | Runbook — tenant toggle flagged manual | PASS | §4.5 with blockquote callout "portal only". |
| 10 | Runbook — `dim_UserRoles` registration | PASS | §4.6 with full INSERT. |
| 10 | Runbook — `tcp_bi_reader` user creation | PASS | §4.7. |
| 10 | Runbook — top-6 failure modes | PASS | §10 covers 7 failure modes (HTTP 401, 403, login failed, couldn't connect, pbi-tools missing, TMDL compile, scheduled refresh missing). |
| 10 | Runbook — manual PBIR finalisation trade-off | PASS | §8 documents it as a v1.0 trade-off; ADR-001 §"Final visual polish" agrees. |

---

## 3. Findings

### 3.1 Critical (must fix before merge)

*None.*

### 3.2 Major (should fix before demo)

**M1 — Step 4b updates the data-source binding but never binds OAuth credentials.**
`deploy.ps1` lines 412–438 issue `POST /Default.UpdateDatasources` with `connectionDetails` (server + database) only. The dataset will still fail its first refresh with `Login failed for user 'tcp-powerbi-sp'` because the OAuth-on-behalf-of binding is never set programmatically. The comment block at lines 455–459 acknowledges this and pushes the OAuth flow to "the SP running an interactive refresh in the service", but in practice the SP cannot run the interactive Web-based OAuth dance — the credentials must be bound via either `PATCH /gateways/{gw}/datasources/{ds}` with an `OAuth2` credential type, or by an interactive Power BI portal user on the first refresh failure. **Either implement the `PATCH /gateways/.../datasources/.../credentials` call against the auto-created cloud gateway, or document explicitly in the runbook that the first scheduled refresh will fail and the operator must click "Edit credentials → Sign in as service principal" in the dataset settings UI once.** Currently this gap is invisible to the operator — the script reports `[SUCCESS]` at Step 4 and Step 6, and the failure surfaces only at the 07:30 RO scheduled-refresh run the next weekday.

**M2 — Multipart `Invoke-RestMethod -Form` calls bypass the helper's retry/auth-refresh.**
`deploy.ps1` lines 363–376 (dataset upload) and 524–535 (report upload) call `Invoke-RestMethod -Form` directly with a manually-built `Authorization` header. These calls have no 429/503 retry, no 401-on-token-expiry recovery, and no structured logging on failure. A 600-second `Wait-ForImport` poll afterwards uses the helper, but the upload itself is a single-shot. **Either extend `Invoke-PowerBIRequest` to accept a `-Form` parameter and route multipart through the same retry path, or wrap the two upload sites in a local retry loop with the same backoff schedule (2/4/8/16/32 s, capped 60 s) as the helper.**

**M3 — 401-on-token-expiry refresh in `Invoke-PowerBIRequest` is not propagated to the caller.**
`Helpers.psm1` lines 175–180 refresh the bearer token locally on 401 and retry the request, but the new token is never returned to the caller's `$pbiToken` variable in `deploy.ps1`. A long deploy that crosses the ~1-hour token TTL will silently incur a refresh per call from that point onward — every helper call pays an extra `az account get-access-token` round-trip. Functionally correct (the deploy succeeds) but inefficient and obscures the underlying renewal. **Either return a tuple `(response, token)` from `Invoke-PowerBIRequest`, or hoist token acquisition into a module-scope cache helper (`Get-PowerBIToken -Cached`) that the deploy script always reads through.**

**M4 — `Step 0e` SQL reachability is a no-op comment, not a probe.**
`deploy.ps1` line 231 writes `0e: SQL reachability is checked lazily at first refresh (informational only).` This contradicts the runbook §6 step-1 description ("verify env vars, pbi-tools binary, SQL connectivity") and weakens preflight. Lazy reachability is fine for the dataset refresh, but the operator only finds out the SQL FQDN is wrong 5+ minutes into the deploy. **Add a 3-second `Test-NetConnection -ComputerName $sqlServerFqdn -Port 1433` (PowerShell-native, no `sqlcmd` dependency) — non-fatal warning on failure.** Alternatively, remove the runbook claim that step 1 verifies SQL connectivity.

**M5 — `pbi-tools compile-model` / `compile-report` commands are not the actual `pbi-tools` CLI verbs.**
`deploy.ps1` lines 308 (`pbi-tools compile-model`) and 496 (`pbi-tools compile-report`) reference subcommands that do not exist in the upstream `pbi-tools` CLI. The real verbs are `pbi-tools compile <project-dir> [-pbixOutPath <path>]` and `pbi-tools extract` (the inverse). Both invocations will fail with `Unknown command` on a clean install. **Verify against `pbi-tools info` output on the actual installed version and correct the verbs.** Cross-check with runbook §6 step-3 row: `pbi-tools compile powerbi/model -format TMDL` — same issue, the `-format TMDL` flag does not exist on `pbi-tools compile`.

### 3.3 Minor (nice to fix)

**m1 — `Helpers.psm1` lacks `$ErrorActionPreference = 'Stop'` at module scope.**
Line 28 sets `Set-StrictMode -Version Latest` but not the error preference. The helper functions catch and re-throw, so in practice this is fine, but a non-terminating error in an `az` invocation inside the module would not abort. **Add `$ErrorActionPreference = 'Stop'` immediately after the `Set-StrictMode` line.**

**m2 — Dead branch in the TMDL compile chain.**
`deploy.ps1` lines 297–300 unconditionally remove the existing `dataset.bim`; lines 316–318 then test for it again in the third `elseif`. Because the file was just removed, that branch can only fire if neither `pbi-tools` nor `TabularEditor` is on PATH **and** a pre-build `.bim` is staged in a non-default location — but the test still references `$bimPath`, which was unlinked. The branch is unreachable as written. **Move the pre-built `.bim` detection into a separate variable (e.g., `$prebuiltBim`) before the `Remove-Item`, or remove the dead branch.**

**m3 — `azd env get-values` regex strips at most one quote per side.**
`deploy.ps1` line 161: `^\s*([A-Z0-9_]+)\s*=\s*"?(.*?)"?\s*$`. The non-greedy `(.*?)` combined with optional `"?` on both sides only strips a single pair. A value with embedded quotes (e.g., `KEY="value with \"escaped\" quotes"`) round-trips with the inner quotes preserved but the outer `"` retained. Unlikely in practice for `AZURE_SQL_*` outputs, but safer to use `azd env get-value <NAME>` per-key, or to call `azd env get-values --output json` and parse with `ConvertFrom-Json`.

**m4 — Runbook §10 troubleshooting count drifts from the audit spec.**
The audit asks for the "top 6 failure modes" — the runbook delivers 7. Not a defect, but the §10 ordering could surface the two highest-probability failures (403 tenant toggle, login-failed SQL user) first. Current ordering is a mix of HTTP error codes and symptom names.

**m5 — `Invoke-RestMethod -Form` requires PowerShell 7.0+.**
Acknowledged in the script comment (line 360) but not in the runbook. The runbook §2-prereqs says "PowerShell 7+" but the failure mode if an operator launches `pwsh 5.1` on Windows is silent: `-Form` is ignored, the upload fails opaquely. **Add a `#Requires -Version 7.0` echo at script start (already present at line 1 — good) AND mention the strict requirement in the runbook §2 alongside the `pwsh --version` verification command.**

**m6 — Runbook §6 step table is misaligned with the script's actual steps.**
The script numbers steps 0–7 (preflight, workspace, compile, publish dataset, bind creds, schedule, publish report, verify). The runbook §6 table numbers 1–8 with different content: step 1 = preflight, step 4 = import dataset, step 7 = "trigger an immediate refresh", step 8 = publish report. The script does NOT trigger an immediate refresh (Step 7 in `deploy.ps1` only reads `GET /refreshes?$top=1`). **Align the runbook table with the script's step numbering and content. Either remove the "trigger an immediate refresh" row, or add the `POST /refreshes` call to the script.** This is the most operator-visible discrepancy in the package.

### 3.4 Informational (no action required)

**i1 — `Default.TakeOver` failure is silently downgraded to a warning.**
`deploy.ps1` lines 400–408: catching the entire `[Exception]` and warning is correct on a re-deploy (SP already owns the dataset), but a real authorization failure (e.g., the SP is not workspace Admin yet) also becomes a warning. The subsequent UpdateDatasources will fail with 401/403 and surface the underlying problem, so this is self-correcting — just noting the swallow.

**i2 — Workspace name with em-dash carries through to the URL filter.**
`POWERBI_WORKSPACE_NAME` default is `TCP — Trading Central Panel` (U+2014). The filter on line 242 single-quotes the value and passes it to `Invoke-PowerBIRequest`, which does not URL-encode the path. Power BI's OData filter accepts the literal em-dash, but if anyone re-uses this pattern with apostrophes (e.g., `O'Brien`), the single-quote injection would break the query. Acceptable for the documented workspace name.

**i3 — Helpers.psm1 retry backoff uses `[Math]::Pow(2, $attempt)`.**
First retry is 2 s, capped at 60 s. Maximum cumulative wait across 5 retries: 2+4+8+16+32 = 62 s. Reasonable.

**i4 — `credentials_rotation.md` cross-reference is present but indirect.**
Runbook §12 lists `credentials_rotation.md` and §4.3b references it for client-secret rotation. ADR-001 itself does not link the rotation doc — minor cross-reference gap, but the runbook is the operator-facing artifact and it does cover the linkage.

---

## 4. Recommended fix order

1. **M5** — broken `pbi-tools` verbs would prevent the script from compiling the TMDL model on a clean machine. Highest-impact, lowest-effort fix.
2. **M1** — undocumented OAuth credential gap. Even if the fix is documentation-only ("operator must bind credentials in the portal on first refresh"), the gap must be visible.
3. **M6** — runbook/script step-number drift. Documentation fix only.
4. **M2** — multipart upload retry. Improves resilience.
5. **M4** — preflight SQL reachability check OR runbook correction.
6. **M3** — token-refresh propagation.
7. **m1–m6** — best-effort.

---

## 5. Re-review trigger

Re-review is required after **M1, M5, and M6** are addressed. M2/M3/M4 may be deferred to a post-Etapa-7 follow-up commit without blocking the stage closure.
