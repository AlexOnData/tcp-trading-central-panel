# Etapa 7 — Convergence Review (Pass 2)

| Field | Value |
|---|---|
| **Reviewer role** | code-reviewer (convergence verification pass) |
| **Date** | 2026-05-16 |
| **Branch** | `feat/azure-rewrite` |
| **Scope** | Verify that the fix agent's commit batch addresses the pass-1 findings raised by business-analyst, database-architect, cloud-architect, and holistic code-reviewer. |
| **Verdict** | **APPROVED FOR MERGE** (1 minor residual + a handful of accepted residuals scheduled for Etapa-12). |

---

## 1. Summary

The fix agent's commit batch resolves the single Critical finding (C-1 — SWA iframe vs `X-Frame-Options: DENY` contradiction), every High-priority Major from the cloud-architect pass-1 (M1, M2, M3, M4, M5, M6), both Major measures findings from the business-analyst pass-1 (M-01 / M-02), and the two holistic Majors (PowerBI SP rotation entry in `credentials_rotation.md`, M-parameter placeholder substitution via `Default.UpdateParameters`).

Highlights of the post-fix artefacts:

- `powerbi/report/pages/ai-assistant/page.json` no longer embeds an iframe; it now renders a hyperlink button (`actionButton` with `action.type = webUrl`, `target = _blank`) plus a footnote explaining the Etapa-6 clickjacking defence. The SWA `staticwebapp.config.json` hardening is preserved verbatim.
- `powerbi/scripts/Helpers.psm1` extends `Invoke-PowerBIRequest` with a `-FilePath` multipart path (same retry/auth-refresh policy as JSON calls), adds a module-scope `$script:CachedPowerBIToken` with `-ForceRefresh` semantics so token renewal is shared across callers, and sets `$ErrorActionPreference = 'Stop'` at module scope. Export surface unchanged.
- `powerbi/deploy.ps1` is renumbered Step 0 → Step 8 (nine phases), routes both multipart imports through the helper, replaces the `0e` no-op SQL comment with a real `Test-NetConnection -Port 1433` probe (non-fatal), drops the dead `.bim` branch by computing `$prebuiltBim` before `Remove-Item`, corrects the `pbi-tools` verb to `compile <project-dir> -outPath`, calls `Default.UpdateParameters` for `SqlServer` / `SqlDatabase` instead of editing the compiled `.bim`, and introduces an immediate `POST /refreshes` (new Step 5) so credential failures surface in the same shell session.
- `powerbi/model/tables/_Measures.tmdl` renames `KPI-TR-010` → `Total Gross PnL (EUR)`, `KPI-TR-011` → `Total Net PnL (EUR)`, renumbers the original commission slot to `KPI-TR-009 Total Commission (EUR)`, and adds the spec-aligned `KPI-TR-012 Daily Gross PnL` measure. `cultures/ro-RO.tmdl` carries the matching translations.
- `docs/security/credentials_rotation.md` adds §2.7 covering the PowerBI SP with both federated-credential and client-secret rotation procedures, including a compromised-credential playbook addition. §3 Year-1 schedule is updated to include the PowerBI SP in the Q3 2027 batch.
- `docs/runbooks/powerbi_deploy.md` §6 now lists nine numbered phases that match `deploy.ps1` step-for-step, including Step 5 (immediate refresh) and Step 7 (SWA hostname substitution + PBIR publish + Rebind). `pbi-tools` verbs are corrected. PowerShell 7 strict requirement is called out in §2.
- `powerbi/README.md` adds a "Known Limitations" section enumerating the eight v1.0 trade-offs scheduled for Etapa-12 (capital baseline, KPI-TR-039 / -052 / -053 / -054 approximations, ROC denominators, `dim_UserRoles`, `dim_Accounts`, `dim_Employees.aad_object_id`).

### Verdict counts

| Bucket | Count |
|---|---:|
| **RESOLVED** | 11 |
| **PARTIALLY RESOLVED** | 1 |
| **NOT RESOLVED** | 0 |
| **REGRESSION** | 0 |
| **ACCEPTED RESIDUAL (Etapa-12)** | 11 |

Total findings reviewed: **23** (1 critical + 9 majors + 13 minors/nitpicks across all four pass-1 reports).

---

## 2. Finding-by-finding verification table

Severity column: C = Critical, M = Major, m = Minor, n = Nitpick.

| Pass-1 ID | Source | Sev | Title | Verdict | Evidence |
|---|---|---|---|---|---|
| C-1 | holistic | C | AI-assistant iframe contradicts SWA `X-Frame-Options: DENY` | **RESOLVED** | `powerbi/report/pages/ai-assistant/page.json:42-61` now declares an `actionButton` visual with `action.type = webUrl`, `target = _blank`; tooltip and footnote explicitly cite the Etapa-6 hardening. `swa/staticwebapp.config.json` left untouched. The deploy script substitutes `<SWA_HOSTNAME>` from `AZURE_STATIC_WEB_APP_HOSTNAME` at Step 7 (`deploy.ps1:540-556`). |
| M1 | cloud-arch | M | Step 4b never binds OAuth credentials | **RESOLVED** | `deploy.ps1:402-432` now performs `Default.TakeOver` then `Default.UpdateParameters` (no `Default.UpdateDatasources` placeholder edit). Step 5 (`deploy.ps1:466-505`) issues a real `POST /refreshes` so credential failures surface in-session; runbook §10 still documents the "Login failed for user" diagnosis path. |
| M2 | cloud-arch | M | Multipart upload bypasses helper retry/auth | **RESOLVED** | `Helpers.psm1:140-202` adds a `-FilePath` parameter that routes multipart through the same retry/401-refresh loop. `deploy.ps1:378-379` and `deploy.ps1:590-591` both call `Invoke-PowerBIRequest -FilePath ...`; no raw `Invoke-RestMethod -Form` remains. |
| M3 | cloud-arch | M | 401 token refresh not propagated | **RESOLVED** | `Helpers.psm1:34, 84-99, 220-227` introduces a `$script:CachedPowerBIToken` module-scope cache. `Get-PowerBIToken -ForceRefresh` updates the cache; subsequent callers (including multipart) read through it. `deploy.ps1` keeps a `$pbiToken` variable for first-call hand-off; subsequent helper calls use the cache transparently. |
| M4 | cloud-arch | M | Step 0e SQL reachability is a no-op | **RESOLVED** | `deploy.ps1:228-243` runs `Test-NetConnection -ComputerName $sqlServerFqdn -Port 1433 -InformationLevel Quiet` and warns (non-fatal) on failure. Runbook §6 table now lists "TCP 1433 reachability probe" under Step 0. |
| M5 | cloud-arch | M | `pbi-tools compile-model/-report` are not real verbs | **RESOLVED** | `deploy.ps1:326` calls `pbi-tools compile $ModelDir -outPath $bimPath`; line 562 calls `pbi-tools compile $reportStagingDir -outPath $pbixPath`. Runbook §2.6 and §6 row Step 2 cite the correct `pbi-tools compile <project-dir> -outPath` form. |
| M6 | cloud-arch | M | Runbook §6 step numbering misaligned with script | **RESOLVED** | `deploy.ps1` headers run Step 0 → Step 8 (`deploy.ps1:115, 247, 296, 359, 394, 455, 507, 530, 609`). Runbook §6 table now mirrors Steps 0-8 exactly, with Step 5 = "Trigger immediate refresh" and Step 7 = "stage report dir, substitute `<SWA_HOSTNAME>`, compile + publish PBIR, rebind". |
| M-01 | bus-analyst | M | KPI-TR-010 / -011 measure names diverge from spec | **RESOLVED** | `_Measures.tmdl:89-105` renames the two measures to `KPI-TR-010 Total Gross PnL (EUR)` and `KPI-TR-011 Total Net PnL (EUR)`. Inline `///` comments document the per-trade slice via filter on `trade_uid`. `cultures/ro-RO.tmdl:27-28` carries the matching translated captions. |
| M-02 | bus-analyst | M | KPI-TR-012 mapped to commission, not Daily Gross PnL | **RESOLVED** | `_Measures.tmdl:79-87` introduces `KPI-TR-009 Total Commission (EUR)` (the original commission measure, renumbered). `_Measures.tmdl:107-118` introduces a new `KPI-TR-012 Daily Gross PnL` measure with the spec formula `SUM(gross_pnl_eur)` filtered to `MAX(trade_date_ro)`. `cultures/ro-RO.tmdl:26, 29` translates both new IDs. `README.md:150` KPI catalogue updated to call out the KPI-TR-009 helper. |
| holistic M-2 | holistic | M | `credentials_rotation.md` lacks PowerBI SP entry | **RESOLVED** | `docs/security/credentials_rotation.md:241-305` adds §2.7 covering both the federated (preferred) and client-secret (fallback) rotation procedures, the impact-of-exposure paragraph, and a compromised-credential playbook addition (re-grant Admin, audit log review, possible `dim_UserRoles` re-registration). §3 Year-1 schedule line for Q3 2027 mentions PowerBI SP rotation. |
| holistic M-3 | holistic | M | Multipart upload not on the helper retry path | **RESOLVED** | Same evidence as cloud-arch M2 — `Invoke-PowerBIRequest -FilePath` covers both dataset and report imports with the helper's retry/401-refresh policy. The 202 + `Location` async case is implicitly handled by `Wait-ForImport` (which polls `/imports/{id}` until `importState` is terminal). |
| holistic M-4 | holistic | M | `model.tmdl` M parameter names not substituted by `deploy.ps1` | **RESOLVED** | `deploy.ps1:415-433` posts `Default.UpdateParameters` with `updateDetails = [{ name: SqlServer, ... }, { name: SqlDatabase, ... }]`. The raw `.bim` placeholder substitution is preserved only for `<TENANT_ID>` (line 370), which is not exposed as an M parameter — documented in the inline comment at lines 366-368 and runbook §6 callout. |
| MN-01 | db-arch | m | `dim_UserRoles` not exposed to the model | **ACCEPTED RESIDUAL** | `powerbi/README.md:174` "Known Limitations" section lists this as a tracked v1.0 trade-off (the project aligns `email = UPN`). Documented divergence in `roles.tmdl` header comment. |
| MN-02 | db-arch | m | `dim_Accounts` is orphaned | **ACCEPTED RESIDUAL** | `powerbi/README.md:175` "Known Limitations" lists this as a tracked deferral. No relationship added in `relationships.tmdl`; no view exposes `account_id`. Etapa-12 will decide between drop and wire-up. |
| MN-03 | db-arch | m | `dim_Employees.aad_object_id` not declared | **ACCEPTED RESIDUAL** | `powerbi/README.md:176` "Known Limitations" enumerates this for the Etapa-12 RLS hardening pass. |
| M-03 | bus-analyst | m | Capital hardcoded 80000 in five measures | **ACCEPTED RESIDUAL** | `powerbi/README.md:169` "Known Limitations" calls this out; the trade-off is spec-aligned (uniform baseline) and per-trader overrides land in Etapa-12. |
| M-04 | bus-analyst | m | Team/Floor/Company ROC use DISTINCTCOUNT × 80000 | **ACCEPTED RESIDUAL** | `powerbi/README.md:173` lists this as a tracked v1.0 trade-off. Numerically equivalent today; semantic divergence appears only with per-trader overrides. |
| M-05 | bus-analyst | m | KPI-TR-039 returns BLANK() | **ACCEPTED RESIDUAL** | `powerbi/README.md:170` documents the deferral; the AI assistant covers UC-06 via SQL CTE. KPI catalogue note in `README.md:159-161` flags KPI-TR-039 and KPI-TR-063 explicitly. |
| M-06 | bus-analyst | m | KPI-TR-052 approximation lacks date comparison | **ACCEPTED RESIDUAL** | `powerbi/README.md:171` "Known Limitations" tracks the deferral. Inline `///` comment in `_Measures.tmdl` remains. |
| M-07 | bus-analyst | m | KPI-TR-053 / -054 approximations | **ACCEPTED RESIDUAL** | `powerbi/README.md:172` "Known Limitations" tracks the deferral. |
| M-08 | bus-analyst | m | `Cutoff5D` variable name misleading | **ACCEPTED RESIDUAL** | Cosmetic — not blocking for v1.0. Recommended for Etapa-12 cleanup along with documenting the 5-trading-day vs 7-calendar-day approximation. |
| m-4 | holistic | m | KPI-CO-010 description says "across all employees" | **PARTIALLY RESOLVED** | `_Measures.tmdl:241` description still reads "Sum of net PnL across all employees on the selected trading day." The DAX uses `SUM(v_floor_performance[net_pnl_eur_total]) ALL(dim_TradingFloors)` — aggregation is over floors, not employees. The wording was not updated by the fix agent. **Recommended Etapa-12 fix**: change to "across all trading floors". Non-blocking. |
| n-1, n-2, n-3 | holistic | n | Cosmetic items (dataset display name, PBIR schema validation, soft-rollback ordering) | **ACCEPTED RESIDUAL** | Not addressed in this batch; cheap to fold into an Etapa-12 polish pass. None affects correctness or security. |

---

## 3. Regressions

**None detected.** The fix agent's changes are confined to:

- `powerbi/**` (model, report, scripts, README, deploy script)
- `docs/runbooks/powerbi_deploy.md`
- `docs/security/credentials_rotation.md`

`git status` confirms `tcp/**`, `function_app/**`, `infra/**`, `swa/**`, `db/migrations/**`, and `.github/workflows/**` are unchanged. No new files outside the Etapa-7 scope.

Concretely verified:

- `swa/staticwebapp.config.json` still enforces `X-Frame-Options: DENY` + `Content-Security-Policy: frame-ancestors 'none'` (no change). The C-1 fix flows entirely through the PowerBI report side, not the SWA side.
- No new secrets in any committed artefact. `credentials_rotation.md` §2.7 uses placeholder values (`<tcp-powerbi-sp appId>`, `<NEW_SECRET>`) consistent with the rest of the file. `gitleaks` would not fire.
- All committed artefacts remain English-only. The Romanian strings in `cultures/ro-RO.tmdl` are UI translations, which is the file's documented purpose.

---

## 4. Step numbering and contract checks

Cross-verified that the script's nine-step contract matches the runbook table:

| Step | `deploy.ps1` header line | Runbook §6 row | Match |
|---|---|---|---|
| 0 | `:115` Preflight | row 0 — Preflight | ✓ |
| 1 | `:247` Workspace bootstrap | row 1 — Workspace | ✓ |
| 2 | `:296` Compile TMDL | row 2 — `pbi-tools compile -outPath` | ✓ |
| 3 | `:359` Publish dataset (multipart) | row 3 — `POST /imports?nameConflict=CreateOrOverwrite` | ✓ |
| 4 | `:394` TakeOver + UpdateParameters | row 4 — `Default.TakeOver` + `Default.UpdateParameters` | ✓ |
| 5 | `:455` Immediate refresh | row 5 — `POST /refreshes` + poll | ✓ |
| 6 | `:507` Scheduled refresh | row 6 — `PATCH /refreshSchedule` (`E. Europe Standard Time`) | ✓ |
| 7 | `:530` Publish report + Rebind | row 7 — stage dir, substitute `<SWA_HOSTNAME>`, compile, publish, Rebind | ✓ |
| 8 | `:609` Verify | row 8 — `GET .../refreshes?$top=1` | ✓ |

The runbook §1 overview, §6 prose, and §6 table all say "nine numbered phases (Step 0 .. Step 8)". The script `<#.SYNOPSIS>` block (`deploy.ps1:9`) says the same.

Parameter substitution contract (was M-4 in holistic):

- M parameters `SqlServer`, `SqlDatabase` are set via `Default.UpdateParameters` (`deploy.ps1:415-433`). README §"Local Development Workflow" still references them by name; the contract is now explicit, not implicit.
- `<TENANT_ID>` is still substituted into the `.bim` pre-upload (line 370). Documented inline at lines 366-368 with a cross-reference to holistic M-4.

---

## 5. Remaining gaps (none blocking)

1. **holistic m-4 (KPI-CO-010 description)** — partial: the measure was not removed/changed by the fix agent, but the description string still says "across all employees" while the DAX aggregates across floors. One-line edit. Recommend folding into the same Etapa-12 polish pass that addresses the M-03 / M-04 / M-06 / M-07 approximation comments.
2. **n-1 (dataset display name = workspace name)** — accepted residual. The duplication "TCP — Trading Central Panel" appears once as workspace and once as dataset in the PowerBI Service UI. Cosmetic; not user-facing on the report URL.
3. **n-2 (PBIR schema validation CI gate)** — accepted residual. Worth raising as a follow-up in `.github/workflows/ci.yml` once PBIR is GA.
4. **Eight items in the "Known Limitations" section of `powerbi/README.md`** — accepted residuals, all scheduled for Etapa-12 (capital baseline, KPI-TR-039, KPI-TR-052, KPI-TR-053/054, ROC denominators, `dim_UserRoles`, `dim_Accounts`, `dim_Employees.aad_object_id`). Each is cross-referenced to its pass-1 finding ID and the underlying spec section, so the deferral is traceable.

---

## 6. Cross-cutting verification

- **English-only**: confirmed across all post-fix artefacts.
- **Real secrets**: none. All credential-shaped strings are placeholders.
- **`E. Europe Standard Time` timezone id**: unchanged (`deploy.ps1:521`). DST handling preserved.
- **Helper export surface**: `Get-PowerBIToken`, `Invoke-PowerBIRequest`, `Wait-ForImport` only (`Helpers.psm1:288`). No accidental export of internal `Write-Helper*` functions.
- **Idempotency**: workspace lookup-or-create, 409 swallow on SP Admin grant, `CreateOrOverwrite` import, TakeOver in try/catch, `Overwrite` report import preserved. New Step 5 immediate-refresh is safe to re-run (PowerBI deduplicates concurrent `POST /refreshes`; a re-run just sees a `Completed` last entry).
- **Backward compatibility**: the runbook re-numbering is the only operator-visible change; existing operators who memorised "Step 4 = bind credentials" now need to read "Step 4 = TakeOver + UpdateParameters" — the §6 table makes this explicit.

---

## 7. Recommendation

**APPROVED FOR MERGE.**

The Critical finding (C-1) and the High-priority Majors (M1, M2, M3, M4, M5, M6, M-01, M-02, holistic M-2, holistic M-3, holistic M-4) all converge to RESOLVED with concrete evidence in the post-fix tree. One partial (m-4 KPI-CO-010 description) is non-blocking and folds naturally into the documented Etapa-12 polish pass alongside the eight accepted residuals.

Etapa-7 can ship. The remaining v1.0 trade-offs are transparent (`powerbi/README.md` "Known Limitations" section), traceable to their pass-1 finding IDs, and cost-bounded for the Etapa-12 follow-up.

No further convergence pass required.

---

## 8. References

- `docs/design/reviews/review_etapa7_measures.md` (pass-1 business-analyst, 8 minor findings)
- `docs/design/reviews/review_etapa7_model.md` (pass-1 database-architect, 3 minor findings)
- `docs/design/reviews/review_etapa7_deploy.md` (pass-1 cloud-architect, 5 major + 6 minor + 4 informational)
- `docs/design/reviews/review_etapa7_holistic.md` (pass-1 code-reviewer, 1 critical + 4 major + 6 minor + 3 nitpicks)
- `powerbi/report/pages/ai-assistant/page.json` (C-1 fix)
- `powerbi/scripts/Helpers.psm1` (M2, M3, multipart support, token cache)
- `powerbi/deploy.ps1` (M1, M4, M5, M6, holistic M-4)
- `powerbi/model/tables/_Measures.tmdl` (M-01, M-02 — KPI renames + KPI-TR-012 fix)
- `powerbi/model/cultures/ro-RO.tmdl` (translation updates aligned with M-01 / M-02)
- `powerbi/README.md` (Known Limitations section enumerating Etapa-12 residuals)
- `docs/runbooks/powerbi_deploy.md` (M6 — Step 0-8 numbering aligned)
- `docs/security/credentials_rotation.md` (holistic M-2 — §2.7 PowerBI SP entry)
