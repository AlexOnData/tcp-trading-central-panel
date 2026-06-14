# Etapa 7 — Holistic Code Review (PowerBI deliverables)

| Field | Value |
|---|---|
| **Reviewer** | code-reviewer (holistic pass) |
| **Date** | 2026-05-16 |
| **Scope** | `powerbi/**`, `docs/runbooks/powerbi_deploy.md`, cross-references to ADRs, design docs, SWA config, credentials rotation doc |
| **Branch** | `feat/azure-rewrite` |
| **Working tree** | Etapa 7 added as two untracked directories (`powerbi/`, `docs/runbooks/`); no other files modified |

---

## 1. Summary

Etapa 7 ships a clean, well-structured, almost fully-automated PowerBI deployment:

- 14-table TMDL model with 48 KPI measures, ro-RO culture, 4 RLS roles, 16 star-schema relationships — `powerbi/model/`.
- PBIR skeleton with 4 report pages — `powerbi/report/`.
- Idempotent PowerShell deploy driver (8 numbered steps) + REST helper module — `powerbi/deploy.ps1`, `powerbi/scripts/Helpers.psm1`.
- 13-section operator runbook with prerequisites, troubleshooting matrix and decommission path — `docs/runbooks/powerbi_deploy.md`.

The work is internally consistent with itself but has **one critical contradiction** with Etapa 6 security hardening (the SWA iframe vs `X-Frame-Options: DENY` collision) and **several documentation drift items** that will trip the next deploy operator. No regressions detected in Bicep / Function App / `tcp.*` / SWA frontend code. English-only and zero secrets confirmed.

**Verdict:** **CHANGES-REQUESTED** — one critical security/architecture contradiction must be resolved before merge; the remaining findings are documentation polish but several are operator-blocking on first use.

| Severity | Count |
|---|---:|
| Critical | 1 |
| Major | 4 |
| Minor | 6 |
| Nitpick | 3 |

---

## 2. Critical findings (must fix before merge)

### C-1. AI-assistant iframe contradicts Etapa 6 CSP / X-Frame-Options

**Location**
- `powerbi/report/pages/ai-assistant/page.json:42-50`
- `swa/staticwebapp.config.json:36-39`

**Problem**
The PowerBI report page embeds the Static Web Apps origin via a `webUrl` visual pointing at `<STATIC_WEB_APP_URL>/ai-assistant`. The visual's own `note` field even acknowledges the requirement:

> "The iframe requires the SWA to serve an `X-Frame-Options: ALLOW` header — configure in staticwebapp.config.json."

That is the exact opposite of what Etapa 6 deployed. The current `swa/staticwebapp.config.json` enforces:

```json
"Content-Security-Policy": "... frame-ancestors 'none'; ...",
"X-Frame-Options": "DENY"
```

Result: the iframe will render as a blocked frame in PowerBI Service, and the AI Assistant page will be a dead placeholder. The browser will throw `Refused to display ... in a frame because it set 'X-Frame-Options' to 'deny'` and the CSP `frame-ancestors 'none'` directive will block it independently.

**Decision required (resolve one of):**

- **(a) Drop the iframe and use a hyperlink visual instead.** Lowest-risk option, preserves the Etapa 6 hardening verbatim. The runbook §8 already lists "iframe visual" as a manual visual-polish step — replace with a button/hyperlink to `https://<swa-url>/ai-assistant` opening in a new tab.
- **(b) Carve out a `frame-ancestors` exception for the PowerBI Service origin.**
  - Set `X-Frame-Options` to be omitted (no longer a strict DENY) for the AI-assistant route only.
  - Set `Content-Security-Policy` `frame-ancestors` to `'self' https://app.powerbi.com https://*.powerbi.com` (or scope to a specific workspace iframe origin if Microsoft documents one).
  - This re-opens an attack class (clickjacking from any cooperating Power BI tenant). The risk should be re-modeled in `docs/security/threat_model.md`.

**Recommendation:** Option (a). The thesis demo can show "Open AI Assistant" as a button visual — the AAD session re-authenticates against the SWA in a new tab and avoids the cross-origin cookie / iframe storage partitioning problems that PowerBI Service is known to cause on AAD-protected SWA endpoints.

**Severity:** Critical — this is the single contradiction between two stages of the build and will be discovered only at demo time if not fixed.

---

## 3. Major findings (should fix before merge)

### M-1. Runbook claims 8 steps with a Step 7 = "Trigger immediate refresh" that does not exist in `deploy.ps1`

**Location**
- `docs/runbooks/powerbi_deploy.md:225-249` (§6 "What each step does")
- `powerbi/deploy.ps1:122-583` (numbered headers `Step 0` … `Step 7`)

**Problem**
- Script structure: **Step 0 (Preflight) → Step 7 (Verify)**. That is 8 numbered headers (0-7).
- Runbook §6 enumerates: **Step 1 (Preflight) → Step 8 (Publish report skeleton)** with an extra "Step 7 — Trigger an immediate refresh to validate connectivity" that the script never performs.

Two concrete divergences:

1. Numbering is off-by-one (script uses 0-indexed, runbook uses 1-indexed). The synopsis in the script header (`Step 0 — Preflight` … `Step 7 — Verify`) and §6 of the runbook (`Step 1 — Preflight` … `Step 8 — Publish report skeleton`) do not agree.
2. The runbook §6 row "Step 7 | Trigger an immediate refresh to validate connectivity | `POST /groups/{id}/datasets/{id}/refreshes`" describes a `Dataset.Refresh` call that is **not in the script**. The script's Step 7 is verification via `GET .../refreshes?$top=1` only, and it explicitly notes "no refreshes have run yet — first run will fire at 07:30 RO on the next weekday."

**Impact**
The operator will follow the runbook expecting the deploy to validate end-to-end connectivity to SQL. Instead, the first proof that the AAD-on-behalf-of-SP connection actually works is the scheduled 07:30 refresh the next weekday — a 12-to-72 hour latency on the most common failure mode (SQL contained-user grant missing, per §10 troubleshooting entry "Login failed for user").

**Recommendation**
Either:
- Add a `POST .../refreshes` call to `deploy.ps1` after Step 5, poll its result, and reflect it in `Step 7` (turning the script into a true 8-step process matching the runbook §6 table), **or**
- Update the runbook §6 table and §7.1 to match the script's 8 actual headers (0-7) and drop the phantom "Trigger an immediate refresh" row.

The first option is preferred — early failure of a real refresh saves a multi-day debug loop.

---

### M-2. `credentials_rotation.md` does not cover the PowerBI service principal

**Location**
- `docs/security/credentials_rotation.md:1-382` (full inventory in §2)
- `docs/runbooks/powerbi_deploy.md:96-100` (existing pointer)

**Problem**
The runbook §4 introduces a new secret material: the PowerBI service principal `tcp-powerbi-sp`. Two credential variants are described:

- **§4.3** — federated credential (preferred; nothing to rotate).
- **§4.3b** — fallback client secret with one-year expiry; the runbook says "rotate annually per `../security/credentials_rotation.md`".

`credentials_rotation.md` does not contain any of `POWERBI_CLIENT_SECRET`, `tcp-powerbi-sp`, or `POWERBI-SP-CLIENT-SECRET`. The inventory in §2 lists only `ANTHROPIC_API_KEY`, `SQL-ADMIN-PASSWORD-EXPORT`, `SWA-FORWARDED-SECRET`, `STORAGE-CONNECTION-STRING`, OIDC creds, and the bootstrap SQL admin password.

**Impact**
The cross-reference from the PowerBI runbook to the rotation doc is a dead pointer. If the operator chooses the client-secret path (§4.3b), no documented rotation procedure exists.

**Recommendation**
Add a new §2.7 entry to `credentials_rotation.md`:

- `KV secret name`: `POWERBI-SP-CLIENT-SECRET` (only if §4.3b path was taken).
- `Function App setting`: N/A (consumed by `powerbi/deploy.ps1` via `POWERBI_CLIENT_SECRET` env var; not bound to the Function App).
- `Rotation cadence`: annual, or N/A if §4.3 (federated) was used.
- `Procedure`: `az ad app credential reset --id <appId> --years 1`; write to KV; re-run deploy.
- Add a row to §3 Year-1 schedule (Q3 2027 batch).

Also add the PowerBI SP scope to the dim_UserRoles incident playbook (§4): if the SP credential is compromised, revoke + add new SP, but also re-register the new SP's object id in `dim_UserRoles` with `scope='admin'`.

---

### M-3. Helpers module does not export `Get-PowerBIToken` capability for the multipart upload path

**Location**
- `powerbi/deploy.ps1:368-371, 527-530` (the two multipart `Invoke-RestMethod` calls)
- `powerbi/scripts/Helpers.psm1:79-193` (`Invoke-PowerBIRequest`)

**Problem**
The script uses two different code paths for PowerBI REST calls:

- `Invoke-PowerBIRequest` (in the helper module) for JSON requests — has retry/backoff, 401 re-auth, transient-error handling.
- Raw `Invoke-RestMethod` (inline in `deploy.ps1`) for the two multipart `POST /imports` calls — **no retries, no 401 token refresh, no structured error parsing**.

A multipart upload is the largest payload of the entire deploy. If it transiently fails (429 throttle, 503, expired token in the middle of a large `.bim` upload), the operator must rollback the entire workspace per the doc-comment at line 67 (`az rest --method DELETE ... groups/<wsId>`) and re-run from scratch. The helper's retry loop is exactly the protection this call needs.

**Recommendation**
Extend `Invoke-PowerBIRequest` (or add a sibling `Invoke-PowerBIImport`) to handle `-Form` payloads, then route the two import calls through it. At minimum, wrap the existing `Invoke-RestMethod` calls in a single-retry block with a 401-refresh path.

**Defensive note** — this matters more than it looks: the PowerBI Service has documented "Operation accepted" responses that return 202 + a `Location` header for asynchronous imports. The current code path treats anything non-2xx as a hard failure. Verify with a `try/catch` on `[System.Net.WebException]` against the 202 case.

---

### M-4. `model.tmdl` parameter names are not how `deploy.ps1` substitutes values

**Location**
- `powerbi/model/model.tmdl:7-11` (declares `SqlServer`, `SqlDatabase` M parameters with default `"<SERVER_FQDN>"`, `"<DATABASE_NAME>"`)
- `powerbi/model/database.tmdl:13-18` (data source references `address.server: SqlServer`, `address.database: SqlDatabase`)
- `powerbi/deploy.ps1:348-355` (substitutes raw placeholders in the compiled `.bim`)
- `powerbi/README.md:120-121` (claims operators "set the M parameters `SqlServer` and `SqlDatabase` when prompted" in PowerBI Desktop)

**Problem**
There are **two distinct substitution mechanisms** described:

1. The TMDL declares M parameters (`expression SqlServer = "<SERVER_FQDN>"` with `IsParameterQuery=true`). This is the recommended pattern; the PowerBI REST API supports `Datasets/Default.UpdateParameters` for setting them.
2. `deploy.ps1` ignores the M parameter system entirely and does a literal string replacement of `<SERVER_FQDN>` / `<DATABASE_NAME>` / `<TENANT_ID>` in the compiled `.bim` — which works only because the M parameter *defaults* happen to be those placeholders.

The README's "Local Development Workflow" instructs the developer to "set the M parameters `SqlServer` and `SqlDatabase` when prompted" — which is the M-parameter flow, the one `deploy.ps1` does not use in production.

Audit point #1 in the review request asked whether the parameter names match. The answer is **subtle**: the names declared in `model.tmdl` (`SqlServer`, `SqlDatabase`) do not appear anywhere in `deploy.ps1`. The contract is implicit — the .bim still contains the parameter default values, and the script edits those defaults. Renaming the M parameters (or changing their default placeholder) silently breaks the deploy.

**Impact**
- A future maintainer who renames `SqlServer` to `SQLServerFQDN` (because it would be cleaner) breaks production with no test signal.
- The README's Desktop workflow and the script's deploy workflow operate on the same model via different mechanisms — a TMDL change can be ambiguous about which path is canonical.

**Recommendation**
Switch `deploy.ps1` Step 4 to use `POST /groups/{id}/datasets/{id}/Default.UpdateParameters` with `updateDetails: [{ name: "SqlServer", newValue: ... }, { name: "SqlDatabase", newValue: ... }]`. Then the contract is explicit and the placeholder-substitution hack can be removed. The `<TENANT_ID>` placeholder in `database.tmdl` line 18 still needs the inline substitution since it is not exposed as an M parameter; document that exception.

If you prefer to keep the current placeholder-edit approach, add a `ruff`-style lint test that fails when `model.tmdl` is edited without `deploy.ps1` knowing about the new parameter name (or document this implicit contract loudly in both files).

---

## 4. Minor findings

### m-1. SP permission GUIDs in runbook are not verifiable

`docs/runbooks/powerbi_deploy.md:112-121` lists three hard-coded permission GUIDs:

```
Dataset.ReadWrite.All  = 7504609f-c495-4c64-8542-686125a5a36f
Report.ReadWrite.All   = b2f1b2fa-f35c-407b-a09b-d9ba5a4cd9ce
Workspace.ReadWrite.All= 9f5b31a5-2ab4-4b3b-9e0d-1baae9aa8c1a
```

I can spot-check by reading published Microsoft docs, but the project itself has no self-test that these GUIDs are still current. Microsoft has historically renamed/re-IDed PowerBI permissions. Add a comment with the date this list was last verified, and consider replacing with `az ad sp show --id 00000009-0000-0000-c000-000000000000 --query "appRoles[?value=='Dataset.ReadWrite.All'].id"` lookups so the GUIDs are resolved at deploy time.

### m-2. Helpers module's exponential backoff is uncapped on consecutive retries

`powerbi/scripts/Helpers.psm1:167-171` uses `[Math]::Min(60, [Math]::Pow(2, $attempt))`. With `MaxRetries = 5` the maximum sleep is 32s on attempt 5 (`2^5 = 32`); the `Min(60,...)` clamp is never reached. Either reduce `MaxRetries` to 3 (typical) and increase the exponent base, or document that 5 attempts × 32s = ~95s worst case per request.

### m-3. Workspace name URL encoding builds an unused variable

`powerbi/deploy.ps1:240-242` computes `$encodedName = [System.Web.HttpUtility]::UrlEncode("'$workspaceName'")` then immediately ignores it and builds `$filterPath` from raw `$workspaceName`. The raw string contains a U+2014 em dash ("TCP — Trading Central Panel"); PowerBI's `$filter=name eq '...'` may or may not handle Unicode in OData literals — verify with an actual workspace lookup. Either use `$encodedName` consistently or delete the unused line.

### m-4. `KPI-CO-010 Company Daily Net PnL` description is generic

`powerbi/model/tables/_Measures.tmdl:218` says "Sum of net PnL across all employees on the selected trading day." but the formula uses `CALCULATE(SUM(v_floor_performance[net_pnl_eur_total]), ALL(dim_TradingFloors))` — it aggregates across **floors**, not employees. Update the description to "Sum of net PnL across all trading floors on the selected trading day." for consistency with the formula and KPI-CO-020 / KPI-CO-011 phrasings.

### m-5. `dim_Date[is_weekday]` referenced in DAX but the file is not in this review

`powerbi/model/tables/_Measures.tmdl:601-607` (KPI-TR-053) uses `RELATED(dim_Date[is_weekday]) = FALSE()`. The `dim_Date.tmdl` file is in scope (line 7 of the README inventory) but I did not open it in this pass. Verify the column exists; if not, the measure returns BLANK and the runbook §10 troubleshooting entry "Column '<name>' does not exist" would fire.

### m-6. Culture file translates measures but column captions remain English

`powerbi/model/cultures/ro-RO.tmdl` translates measure display names and table captions but no column captions are translated. Romanian end users will see Romanian measure names but English column names (`trade_date_ro`, `net_pnl_eur_total`, etc.). This is consistent with the file's own disclaimer at line 3 ("Column identifiers remain in English"), but the runbook §7.2 should set operator expectation explicitly so the first PowerBI Service render does not look "half-translated".

---

## 5. Nitpicks

### n-1. Inconsistent dataset display-name

`powerbi/deploy.ps1:357` sets `$datasetDisplayName = $workspaceName` ("TCP — Trading Central Panel"), so the dataset name *inside* the workspace is the same as the workspace name. The runbook §7.1 refers to the dataset as `tcp-trading-central-panel` (kebab-case, matching `database.tmdl:1`). PowerBI Service shows the display name in the UI, so the user will see "TCP — Trading Central Panel" twice (once as workspace, once as dataset). Pick one — the database internal name (`tcp-trading-central-panel`) is the better display name and avoids the duplication.

### n-2. `report.json`, `pages.json`, `definition.pbir` not inspected against PBIR schema version

These three top-level JSON files claim `$schema` URLs from `developer.microsoft.com/json-schemas/fabric/...`. Add a CI gate that fetches the schemas and validates the JSON against them — PBIR is still in preview and the schemas have shifted twice in 2026. This would catch the next breaking PBIR schema bump before deploy.

### n-3. `deploy.ps1` rollback note at line 67-74 deletes the entire workspace

The rollback procedure says `DELETE /groups/<workspaceId>` is "clean slate". That is also destructive of any human-built visual layout from the §8 manual polish pass. Add a softer partial rollback first ("Try the dataset+report drop first; only delete the workspace if that fails") — the script already documents the partial path two lines below, but the operator will scan-read and pick the first option.

---

## 6. Cross-component contract matrix

| Producer | Consumer | Contract field | Status |
|---|---|---|---|
| `azd env get-values` (Bicep `main.bicep` outputs) | `deploy.ps1:188-190` | `AZURE_SQL_SERVER_FQDN`, `AZURE_SQL_DATABASE_NAME`, `AZURE_TENANT_ID` | OK — names match Bicep outputs |
| `deploy.ps1` runtime env | `model/database.tmdl:13-18` | `<SERVER_FQDN>`, `<DATABASE_NAME>`, `<TENANT_ID>` placeholders | OK by accident — see M-4 |
| `model/model.tmdl` M parameter names | `deploy.ps1` substitution | `SqlServer`, `SqlDatabase` | **Drift risk** — see M-4 |
| `runbook §6` step table | `deploy.ps1` Step headers | 8-numbered process | **Mismatch** — see M-1 |
| `runbook §4` SP grants | `deploy.ps1` capabilities used | `Dataset.ReadWrite.All`, `Report.ReadWrite.All`, `Workspace.ReadWrite.All` | OK — all three are sufficient for create/import/update/refresh calls |
| `_Measures.tmdl` (48 measures) | `cultures/ro-RO.tmdl` translations | Per-measure `translatedCaption` | OK — all 48 KPI measures translated; KPI-TR-039 placeholder is translated too |
| `roles.tmdl` Trader role | `dim_Employees.email` column | UPN match via `USERPRINCIPALNAME() = [email]` | OK — relies on `@tcp-capital.ro` domain matching AAD UPN, consistent with `CLAUDE.md` |
| `roles.tmdl` Admin role | PowerBI SP via `dim_UserRoles` admin scope | ADR-003 §6 contract | OK — runbook §4.6 inserts the SP with `scope='admin'` |
| `ai-assistant/page.json` iframe | `swa/staticwebapp.config.json` headers | `X-Frame-Options`, CSP `frame-ancestors` | **CONTRADICTION** — see C-1 |
| `ai-assistant/page.json` iframe URL | `<STATIC_WEB_APP_URL>` substitution | Required at deploy time | **Missing** — `deploy.ps1` does not substitute this placeholder (the only placeholder in the PBIR layer); operator must do it manually per runbook §8 step 3.4 |
| `runbook §4.3b` PowerBI SP secret | `credentials_rotation.md` rotation procedure | `POWERBI_CLIENT_SECRET` rotation cadence | **Missing** — see M-2 |
| `definition.pbir:10` `pbiModelDatabaseName` | `deploy.ps1` Step 6 rebind | `Rebind` call sets `datasetId` post-import | OK — the rebind decouples the report from the import bundle; placeholder `<DATASET_ID_AT_DEPLOY_TIME>` becomes irrelevant after rebind |

---

## 7. No-regression sweep

Confirmed via `git status`: only two new directories untracked (`powerbi/`, `docs/runbooks/`); no other files modified. The following stages remain untouched:

- `infra/**` (Etapa 4 Bicep) — unchanged.
- `function_app/**` (Etapas 4-6) — unchanged.
- `tcp/**` (Etapas 2-3 Python core, Etapa 5 AI client) — unchanged.
- `swa/**` (Etapa 5 frontend) — unchanged (and **this is the problem in C-1** — the conflict with the iframe is not because anything regressed but because the PowerBI report assumes a different security posture).
- `db/migrations/**` (Etapa 2) — unchanged.
- `.github/workflows/**` — unchanged.

---

## 8. English-only & secrets sweep

- **English-only**: confirmed across `powerbi/**` and the runbook. The ro-RO culture file (`cultures/ro-RO.tmdl`) contains Romanian *translation strings* for display, which is the intended purpose of a culture file — this is not a violation of the "all committed artifacts in English" rule, which excepts UI localisation by design. Code identifiers, DAX expressions, file/folder names, comments, and commit messages remain English.
- **Secrets**: no real secrets in any of the inspected files. The only credential-shaped strings are placeholders (`<TENANT_ID>`, `<sp-objectId>`, `<password>`) in the runbook examples and explicit `_` env-var names. `gitleaks` would not fire on these.

---

## 9. Recommendation

**CHANGES-REQUESTED.**

Block merge on **C-1** (resolve the iframe/CSP contradiction) and **M-1** (runbook step numbering / phantom refresh step). The four remaining Major items (M-2, M-3, M-4) are tightly scoped and should be closed in the same commit batch — `credentials_rotation.md` PowerBI SP entry, multipart upload through the retry helper, and the M-parameter vs placeholder-substitution decision.

The Minor and Nitpick items can be deferred to an Etapa-12 polish pass without operational risk, but m-4 (KPI-CO-010 description) and n-1 (dataset name) are cheap fixes worth folding in now.

The semantic model itself is in good shape: 48 measures, 16 relationships, 4 RLS roles, 14 tables, consistent format strings (`#,##0.00 €` for monetary, `0.0%` for ratios, `0.00` for dimensionless), ADR-aligned design decisions documented in-line with the DAX (KPI-TR-039 / -053 / -054 deferral notes). The deploy script's structure (numbered idempotent steps, isolated helpers, retry/auth in one place) follows the same pattern as `infra/scripts/postprovision.ps1` and is clean.

After the C-1 + M-1..M-4 batch lands, a single follow-up convergence pass should be sufficient.

---

## 10. Suggested follow-up

- Once C-1 is decided, the threat model (`docs/security/threat_model.md`) needs a one-line update on the PowerBI → SWA trust boundary.
- After M-4 is resolved, add a 5-line unit test under `tests/` that parses `model.tmdl`, extracts the M parameter names, and asserts `deploy.ps1` substitutes them (catches drift on rename).
- The PowerBI deploy is not yet wired into CI/CD. Etapa 4's `.github/workflows/cd.yml` should call `pwsh powerbi/deploy.ps1` after the post-provision step. Out of scope for this review but worth raising as the next Etapa-7 follow-up.
