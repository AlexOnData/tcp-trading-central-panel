# Etapa 4 architecture review — pass 1

- **Reviewer**: cloud-architect
- **Date**: 2026-05-16
- **Scope**: `azure.yaml` + `infra/main.bicep` + `infra/main.parameters.prod.json` + `infra/modules/*.bicep` cross-checked against `docs/design/03_architecture.md` (§2, §4, §5, §6, §7, §8, §11, §16, §17), `ADR-003`, `ADR-004`, and `infra/scripts/postprovision.ps1`.
- **Verdict**: **ACCEPT_WITH_CHANGES**

## Summary

The IaC layout is structurally sound: modular Bicep, KV-references built deterministically, RBAC inlined per `MN-11`, Free Offer / Y1 / Free SWA all correctly pinned, and most security defaults (TLS 1.2, `httpsOnly`, `ftpsState: Disabled`, `allowBlobPublicAccess: false`, KV RBAC, narrow Storage container-scoped role) are right. However four blocking issues prevent a clean single-pass `azd provision`: (1) non-deterministic `newGuid()` defaults on `sqlAdminPassword` and `swaForwardedSecret` cause secret drift on every re-deploy and will rotate the SWA shared secret out from under `staticwebapp.config.json`; (2) the module ordering creates a hard cycle — `functions` is declared before `storage` and `keyvault`, yet its `siteConfig.appSettings` carries KV references that must resolve at deploy time for `AzureWebJobsStorage` (Functions runtime cannot start otherwise) and the Func-MI does not yet hold `Key Vault Secrets User` when those settings are first written; (3) `observability.bicep` types `dailyQuotaGb` as `int` with default `1`, breaking the spec's `0.5 GB` cap (§4.2); (4) `azureADOnlyAuthentication: false` baked into the SQL server block silently flips the post-provision AAD-only state back to false on every re-deploy. Plus a handful of major and minor findings (output names, `SCM_DO_BUILD_DURING_DEPLOYMENT` vs `WEBSITE_RUN_FROM_PACKAGE`, `tags.repo`, `tenantId` plumbing, server-level diagnostics).

---

## Critical (blocks `azd provision` or single-pass idempotency)

- [ ] **CR-01** | `infra/main.bicep:63` | `sqlAdminPassword string = 'P${uniqueString(...)}!${newGuid()}'` and `infra/main.bicep:67` `swaForwardedSecret string = newGuid()` use `newGuid()` in parameter defaults. | `newGuid()` re-evaluates on every deploy. The next `azd provision` rotates `SQL-ADMIN-PASSWORD-BOOTSTRAP`/`...-EXPORT` (breaks BACPAC export — ADR-004 — until the next successful Sunday) and rotates `SWA-FORWARDED-SECRET` (breaks SWA→Function `X-SWA-Forwarded` validation — §8.2 bullet 4 — until `staticwebapp.config.json` is redeployed). It also defeats `azd what-if` zero-diff (§17.1 acceptance bullet). | Move the GUID generation outside Bicep: generate the bootstrap password and SWA secret in `azd preprovision` (PowerShell) and pass them via `azd env set`. Inside Bicep, mark both parameters `@secure()` with no default; fail fast if the operator forgot to set them. Alternative: keep the default but switch to a deterministic expression that hashes a stable seed (`base64(uniqueString(subscription().id, environmentName, 'swa-fwd'))` + complexity decoration), and document that the seed is the source of rotation.

- [ ] **CR-02** | `infra/main.bicep:156-170` (`functions` declared before `keyvault`) + `infra/modules/functions.bicep:107-180` (KV-reference `appSettings`, including `AzureWebJobsStorage`) | The dependency graph in the file header comment is the inverse of the actual deploy order. `functions` is created before `storage` and `keyvault`, but `siteConfig.appSettings.AzureWebJobsStorage` is a KV reference (`@Microsoft.KeyVault(SecretUri=...)`). At the moment the site config is written, (a) the Key Vault does not yet exist, (b) the `STORAGE-CONNECTION-STRING` secret does not exist, (c) the Function-MI has no `Key Vault Secrets User` role assignment on the KV. The site config write does not technically fail (the platform stores the literal reference string), but the Functions runtime cannot start because `AzureWebJobsStorage` is the very first setting the host resolves — the package fetch (`WEBSITE_RUN_FROM_PACKAGE=1`), the host-locks container, and the host id all depend on it. Result: the first deploy needs a manual Function App restart after KV + secrets + RBAC land, contradicting the §17.1 acceptance bullet "no manual intervention beyond OIDC login". | Either (a) keep using a KV reference for `AzureWebJobsStorage` AND add an explicit `Microsoft.Web/sites/restart` operation as the final step of the `azd` deploy (e.g., via the `postprovision` script — already invoked) AND ensure `keyvault` + `storage` modules complete before the Function App app-settings update by re-ordering modules (storage and keyvault first, then a `functions` module that depends on both via explicit `dependsOn`); or (b) drop the KV reference for `AzureWebJobsStorage` only and inject the raw connection string from `storage.outputs.connectionStringSecretValue` directly into the Function App app setting (still `@secure()`, never in deployment history). Option (b) is the documented Microsoft pattern for Consumption-plan Functions because the runtime resolves this setting before KV-reference resolution is wired. Keep the rest of the KV references (Anthropic, SWA shared secret, SQL export password) — they are not on the runtime startup path and will resolve lazily after the Func-MI gets `Key Vault Secrets User`.

- [ ] **CR-03** | `infra/modules/observability.bicep:24` | `param dailyQuotaGb int = 1` | The spec (`03_arch §4.2 Application Insights + Log Analytics Workspace`) and `03_arch §4.2` per-resource table both pin the cap at **`0.5` GB**. `int` cannot represent `0.5`; the current default (`1`) doubles the spec cap. The §10 cost model's "60 % headroom" claim depends on the 0.5 ceiling. | Change the type to `string` and pass it through with `json(dailyQuotaGb)` into `workspaceCapping.dailyQuotaGb` (the underlying Azure property accepts a decimal), or hardcode `dailyQuotaGb: json('0.5')`. Verify the deployment by inspecting `properties.workspaceCapping.dailyQuotaGb == 0.5` after `azd provision`.

- [ ] **CR-04** | `infra/modules/sql.bicep:94` | `azureADOnlyAuthentication: false` hardcoded inside the `administrators` block. | `postprovision.ps1` flips this to `true` via `Set-AzSqlServerActiveDirectoryOnlyAuthentication`. The next `azd provision` (e.g., a routine schema migration) will reapply the Bicep template, the `administrators` block re-renders with `azureADOnlyAuthentication: false`, and the platform silently flips the server back to SQL-auth-enabled. This is a security regression and contradicts §6.5 + `A.8` acceptance criterion. It also re-enables a now-rotated bootstrap password path (already deleted from KV after first deploy). | Introduce a `param azureADOnlyAuthentication bool = true` (default `true` post-bootstrap), exposed via `main.parameters.prod.json`. On the very first deploy the postprovision script either (a) sets `azd env set AZURE_AAD_ONLY_AUTHENTICATION false` before deploy and `true` after, or (b) the parameter defaults to `false` for the first deploy only and the operator flips the param file to `true` for steady state. Cleaner alternative: drop the `administrators` block entirely from Bicep, set the AAD admin imperatively in `postprovision.ps1` (via `az sql server ad-admin create`) and the AAD-only flip in the same script. The trade-off is one less bicep-managed property; the upside is no drift after the post-deploy flip.

---

## Major

- [ ] **MJ-01** | `infra/main.bicep:262-275` (outputs) | Output names use the `AZURE_` prefix (azd convention) but the user-supplied review prompt asks for the unprefixed names (`FUNCTION_APP_NAME`, `KEY_VAULT_URI`, `SQL_SERVER_FQDN`, etc.). | The deployer/azd contract uses `AZURE_`-prefixed env vars; the `postprovision.ps1` script reads `AZURE_SQL_SERVER_NAME`, `AZURE_FUNCTION_APP_NAME`, etc. The bicep and the postprovision script are self-consistent. However, the spec doc (`03_arch §16`) shows unprefixed names, and the user prompt expected the unprefixed set. | Reconcile the spec: either update `03_arch §16` to standardise on `AZURE_*` (azd-native), or rename the bicep outputs. Recommendation: keep `AZURE_*` and update the spec; this matches every other azd template Microsoft ships.

- [ ] **MJ-02** | `infra/main.bicep:262-275` (outputs) | `FUNCTION_APP_CLIENT_ID` is missing from the outputs. The user prompt and §4.2 (`AZURE_CLIENT_ID = <system-MI client id>`) both expect it. | System-assigned MI does **not** expose `clientId` on the `Microsoft.Web/sites` resource symbol — only `principalId` and `tenantId` are available. The current comment in `functions.bicep:222-225` documents this honestly and defers to `az ad sp show --id <principalId> --query appId -o tsv` inside `postprovision.ps1`. However, the postprovision script does not actually resolve the clientId — it only writes `TCP_GENERATOR_OID` (which is the principalId, not the clientId). | Either (a) drop `AZURE_CLIENT_ID` from the appSettings and let `DefaultAzureCredential` pick the lone system-assigned MI automatically (this works on Functions because there's only one MI present), and remove the §4.2 mention; or (b) extend `postprovision.ps1` to resolve the clientId and `az functionapp config appsettings set --settings AZURE_CLIENT_ID=<clientId>` after first deploy. Recommend (a) — simpler, one fewer post-step.

- [ ] **MJ-03** | `infra/modules/functions.bicep:133-138` | `WEBSITE_RUN_FROM_PACKAGE=1` AND `SCM_DO_BUILD_DURING_DEPLOYMENT=1` set together. | These are conflicting deployment models. `WEBSITE_RUN_FROM_PACKAGE=1` means "mount a prebuilt zip from the WebJobs storage container as the read-only filesystem"; `SCM_DO_BUILD_DURING_DEPLOYMENT=1` means "run pip install on the SCM side at deploy time, then sync to wwwroot". On Y1 Consumption the package-mode wins, but the Kudu build step still runs (wasted compute, slower `azd deploy`, occasional confusion if the wwwroot snapshot drifts from the package). | Drop `SCM_DO_BUILD_DURING_DEPLOYMENT` (or set it to `0`). `azd deploy` builds the zip locally (`pip install -r requirements.txt --target ...`) and uploads via `WEBSITE_RUN_FROM_PACKAGE`. This is the documented azd+Y1 pattern.

- [ ] **MJ-04** | `infra/modules/keyvault.bicep:39` + `infra/main.bicep:229` | `oidcPrincipalId string = ''` is wired to `principalId` (the `AZURE_PRINCIPAL_ID`). | On an interactive `azd up` from a developer workstation, `principalId` is a **User** principal, not a Service Principal. The Bicep then hardcodes `principalType: 'ServicePrincipal'` on `oidcSecretsOfficer` (line 217). The role assignment is created with the wrong `principalType`, which (a) is a soft warning today but (b) is increasingly enforced by AAD; on some tenants the assignment silently fails to resolve at runtime, causing `azd up` to succeed but the developer to be unable to write secrets after deploy. | Plumb `principalType` from `main.bicep` into the keyvault module (already done for `deployerRgOwner` in main.bicep:129). Add `param oidcPrincipalType string` and substitute on line 217. Same pattern for SQL admin (already done correctly at sql.bicep:91 via `aadAdminPrincipalType`).

- [ ] **MJ-05** | `infra/main.bicep:208` | `aadAdminPrincipalType: principalType == 'User' ? 'User' : 'Application'` collapses `ServicePrincipal` to `Application`. | The SQL `administrators.principalType` allowed values are `User`, `Group`, `Application`. The mapping `ServicePrincipal → Application` is correct, but it is silent — if the deployer is a `Group` (the spec's `aad-tcp-sql-admins` future-mode), this falls through to `Application` and the AAD admin entry is wrong. The collapse is acceptable today (one SP / one user), but flag it. | Expand the allowed values of `principalType` in main.bicep to include `Group`, and forward an explicit `aadAdminPrincipalType` so the SQL-side and KV-side mappings can diverge.

- [ ] **MJ-06** | `infra/scripts/postprovision.ps1:88` | `$setupSql | sqlcmd -S $sqlServerFqdn -d $sqlDatabaseName -G -b -ErrorAction Stop` | Connects via AAD (`-G`) **after** `azd provision` finished. Two problems: (a) at this point the AAD admin set by Bicep is the **deployer** (`principalId`); on a CI run via OIDC that is the SP, which has no AAD admin entry on the SQL Server unless `principalType` is correctly threaded (see MJ-04/MJ-05); (b) the script runs before the SQL schema is applied (`tcp.synth` migrations from Etapa 2 land in Etapa 5's app), so `ALTER SECURITY POLICY rls.TradesAccessPolicy` and `dbo.dim_UserRoles` do not yet exist. The script already handles this with a `try/Write-Warning` swallow, but the warning is silent in CI logs — Etapa 4's `azd up` will green-light the deploy with RLS not yet wired, contradicting acceptance `A.1`. | Split the postprovision into two phases: phase A (Etapa 4) only does the AAD-only flip + bootstrap-secret delete; phase B (Etapa 5, after schema migration) registers the MI in `dim_UserRoles`. Or: gate the postprovision script on a `SELECT object_id('rls.TradesAccessPolicy')` probe and skip the RLS block if the schema isn't present yet, with a clear "deferred to first schema deploy" log line (not just a warning).

- [ ] **MJ-07** | `infra/modules/storage.bicep:191` | `output connectionStringSecretValue string = 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'` marked `@secure()`. | Two concerns: (a) `listKeys()` at the module output is invoked at deploy time and the result is recorded in the deployment object — `@secure()` strips it from history, but the value is still emitted as a module output and consumed by `keyvault.bicep`. Bicep linter `no-secrets-in-params` is satisfied because of `@secure()`. Verify this rendered correctly with `bicep build --emit-trim`. (b) The storage account-key path makes `STORAGE-CONNECTION-STRING` a *static* secret that does not rotate with key rotation — when `az storage account keys renew` runs (which it should, periodically), `STORAGE-CONNECTION-STRING` in KV is stale and the Function App's `AzureWebJobsStorage` breaks until the next `azd provision`. | Acceptable for thesis scope; document explicitly under `docs/security/credentials_rotation.md` (still TODO per CLAUDE.md). For a production path, switch `AzureWebJobsStorage` to identity-based (`AzureWebJobsStorage__accountName` + Function-MI granted `Storage Blob Data Owner` + `Storage Queue Data Contributor` + `Storage Table Data Contributor` on the account) and retire the secret entirely. This also resolves CR-02's runtime-startup concern.

- [ ] **MJ-08** | `azure.yaml:13-17` | `services` declares only `api: ./function_app`; no `web:` service for the SWA. | The spec example in `03_arch §16` shows `web: { project: ./static, language: html, host: staticwebapp }`. The omission is justified in the file comment (avoids redundant uploads). However, the consequence is that `azd deploy` will not push `static/` content to SWA — that must be done via a separate workflow step (`Azure/static-web-apps-deploy@v1`). Acceptance bullet "`azd up` succeeds … no manual intervention" needs to confirm whether `azd up` automatically triggers the SWA upload or whether a follow-up `gh workflow run` is required. | Either (a) add `web:` to `services` and let azd manage SWA uploads with a deployment token (azd 1.6+ supports this), or (b) keep the current setup and add a CI workflow step that runs immediately after `azd deploy` to push SWA content. Document the choice in `03_arch §16`.

---

## Minor / nits

- [ ] **MN-01** | `infra/main.bicep:70-76` | `tags` object lacks `repo` key (spec §4.1 includes `repo: 'github.com/TODO/tcp-trading-central-panel'`). | Cosmetic; cost-management filters fall back to `project` tag fine. Add `repo: 'TODO'` for parity with spec.

- [ ] **MN-02** | `infra/modules/keyvault.bicep:179-189` | Diagnostic `metrics.AllMetrics` is enabled but Key Vault publishes no useful metrics worth retaining inside the 5 GB workspace budget. | Drop the metrics block on KV to save ingestion headroom; keep the `AuditEvent` and `AzurePolicyEvaluationDetails` logs.

- [ ] **MN-03** | `infra/modules/sql.bicep:140-186` | Diagnostic settings emit `AutomaticTuning`, `DatabaseWaitStatistics`, `QueryStoreRuntimeStatistics`. | These are useful but verbose categories. With `dailyQuotaGb = 0.5`, they can push the workspace into "cap reached → drop" mode within 24 h on a chatty day. Recommend trimming to `SQLSecurityAuditEvents`, `Errors`, `Timeouts`, and `Basic` metrics only; re-enable on incident.

- [ ] **MN-04** | `infra/modules/functions.bicep:97-103` | `cors.allowedOrigins: []` is correct for SWA linked-backend (Microsoft backbone, no CORS hop). | Add an inline comment explicitly forbidding adding a `'*'` origin for "quick testing" — common foot-gun. Already partially there; expand.

- [ ] **MN-05** | `infra/main.bicep:165` | `sqlServerFqdn: '${names.sqlServer}${environment().suffixes.sqlServerHostname}'` constructs the FQDN before the SQL module runs. | Works (the FQDN is deterministic from the server name), but breaks if SQL ever ends up in a non-public cloud where the suffix differs. Switch to `sql.outputs.serverFqdn` once the module ordering is resolved (CR-02 fix may already require this).

- [ ] **MN-06** | `infra/modules/functions.bicep:177-179` | `TCP_SQL_ADMIN_PASSWORD = kvRef.sqlAdminExport` — exposes a SQL admin password as an env var to all Function App processes. | The Function App MI has `SQL DB Contributor` at DB scope. The Azure SQL Export API takes the SQL admin credential as a body parameter, which is technically a control-plane call — not a connection string. The current pattern is the documented ADR-004 trade-off. Verify in the Etapa 5 Function code that this env var is **only** read by `TimerTrigger_BacpacExport` and not logged.

- [ ] **MN-07** | `infra/modules/swa.bicep:39-65` | `linkedBackend.region: location` (passed through `functionAppRegion`). | Correct for SWA Free plan. Add `dependsOn: [linkedBackend]` or the equivalent in `main.bicep` for the SWA module on `functions` (already done implicitly via `functionAppResourceId: functions.outputs.functionAppId`). Implicit dep is fine; no change required.

- [ ] **MN-08** | `infra/modules/observability.bicep:27` | `retentionInDays = 30` matches the 31-day-free retention. | OK. Be aware: workspace-based App Insights now bills retention per-table after the 31-day grant. Leave as-is for thesis scope.

- [ ] **MN-09** | `infra/main.parameters.prod.json` | Only two parameters set. | Add `principalId` and `principalType` parameter placeholders here (commented-out) so the operator knows they exist; today they default to `''` and `'ServicePrincipal'` which is correct for CI but invisible. Also add an empty `anthropicApiKey` entry as a discoverability hint (`azd env set ANTHROPIC_API_KEY` is the documented path; redundant but helpful).

- [ ] **MN-10** | `infra/modules/sql.bicep:101-109` | Firewall rule `AllowAllAzureServices` uses `0.0.0.0/0.0.0.0`. | This is the documented "allow Azure services" virtual rule; correct.

- [ ] **MN-11** | `infra/modules/functions.bicep:148-152` | `ANTHROPIC_BASE_URL = https://api.anthropic.com`. | Hardcoded; for an internal proxy (e.g., regional cache) this would need to change. Make a parameter with default `https://api.anthropic.com`. Optional.

- [ ] **MN-12** | `infra/scripts/postprovision.ps1:34` | `azd env get-values | ConvertFrom-StringData` will fail on values that contain `=` characters (e.g., a base64 token with padding). The current outputs are all azure resource names — safe — but future outputs (instrumentation key, etc.) may break. | Use the documented `azd env get-values --output json` pipeline instead.

- [ ] **MN-13** | `infra/modules/keyvault.bicep:63-66` | `softDeleteRetentionInDays = 7` is the KV minimum. | Spec §15 R8 calls this out: `azd down` followed by `az keyvault purge` is documented. OK.

- [ ] **MN-14** | `infra/modules/functions.bicep:57-72` | `Microsoft.Web/serverfarms@2023-12-01` Y1 SKU block. | The `family: 'Y'` and `size: 'Y1'` properties are redundant with `name: 'Y1'` and `tier: 'Dynamic'`, but the Azure validator accepts them. Cosmetic; leave.

- [ ] **MN-15** | `infra/modules/observability.bicep:43-44` | `enableLogAccessUsingOnlyResourcePermissions: true` is best-practice. | Confirmed correct.

- [ ] **MN-16** | `infra/main.bicep:122-131` | `deployerRgOwner` grants Owner at RG scope. | Matches spec §5. After thesis hand-in, the operator must manually downgrade this to `Reader`. Add a `TODO post-defense` comment.

---

## Module dependency / chicken-and-egg analysis

The header comment in `main.bicep` claims the order is:

```
observability → functions → (storage, sql, swa) and observability → keyvault
```

But the actual module declaration order in `main.bicep` is:

1. `observability` (no deps).
2. `functions` (consumes `observability` outputs; uses **deterministic KV name** — known string — to embed KV references in `appSettings`).
3. `storage` (consumes `functions.outputs.principalId` for RBAC + `observability.outputs.workspaceId`).
4. `sql` (same shape as storage).
5. `keyvault` (consumes `functions.outputs.principalId`, `storage.outputs.connectionStringSecretValue`, plus `observability`).
6. `swa` (consumes `functions.outputs.functionAppId`).

**Validation of the "lazy resolution" claim:**

- KV references for `ANTHROPIC_API_KEY`, `SWA_FORWARDED_SECRET`, `TCP_SQL_ADMIN_PASSWORD` are read at *first use* by user code in the Function App. The Func-MI gets `Key Vault Secrets User` in step 5 (after the Function App exists with its MI in step 2). By the time the timer or HTTP trigger fires, KV + secrets + RBAC are all in place. ✅ Acceptable.
- KV reference for `AzureWebJobsStorage` is read by the **Functions host** at process start. The host blocks on this. The first invocation cannot fire until the host starts. The host writes its host-id to the storage account at boot. If the reference does not resolve, the host enters a crash-loop and Application Insights does not even register the Function App. **This is the CR-02 hard cycle.** ❌ Requires intervention.
- `storage.outputs.connectionStringSecretValue` is computed via `listKeys()` and passed `@secure()` into `keyvault.bicep`. This works in Bicep (storage exists before keyvault); secret is created with the storage key value. ✅ Correct.
- The Function App is declared with `kind: 'functionapp,linux'`, `identity: { type: 'SystemAssigned' }`, exposing `functions.outputs.principalId` to downstream modules. The principalId becomes resolvable as soon as the Function App resource provisions (the system-assigned MI is created synchronously with the site). ✅ Correct.

**The unresolved cycle:** Function App → needs `AzureWebJobsStorage` reference to resolve at *boot* → needs Func-MI to have `Key Vault Secrets User` on KV → needs KV to exist → needs `storage.outputs.connectionStringSecretValue` → needs Function App (for the `funcMiBacpacContributor` role assignment inside storage.bicep) → needs Function App to be created. The provisioning side completes (every resource lands), but the *runtime* side stalls until a manual Function App restart re-reads the resolved KV reference.

**Recommended fix path** (matches CR-02 + MJ-07):

1. Inject the storage connection string directly into the `AzureWebJobsStorage` app setting (raw `@secure()` value, not a KV reference). The setting writes once; the Function App boots successfully on first deploy.
2. Keep KV references for the three lazy secrets (Anthropic, SWA forwarded, SQL export password).
3. Add an explicit `dependsOn: [keyvault]` on the `functions` module IF azd does not already chain them via output consumption (it does — `functions` doesn't consume KV outputs today, so the implicit chain is absent).
4. Optional follow-up: migrate `AzureWebJobsStorage` to identity-based form (`AzureWebJobsStorage__accountName` + `Storage Blob/Queue/Table Data Owner` on the account). This removes the storage key from KV entirely.

---

## Bicep build mental walkthrough

- `main.bicep` — `targetScope = 'subscription'` is correct; RG creation + role assignment + cross-RG module calls all valid. `resourceGroupName` and `tags` are computed at compile time. The `regionShortMap[location]` indirection is sound (Bicep supports keyed object access). The `principalType` ternary at line 208 (`principalType == 'User' ? 'User' : 'Application'`) compiles — `principalType` is a const string. **No compile errors expected.**

- `observability.bicep` — `dailyQuotaGb int = 1` will compile but pass `1` to `workspaceCapping.dailyQuotaGb`, which expects a number; the JSON serialiser emits `1` (integer), which Azure accepts. Switching to `string` + `json()` per CR-03 is required. **One compile concern (type mismatch with spec).**

- `keyvault.bicep` — `enablePurgeProtection: enablePurgeProtection ? true : null` is the documented idiom for the one-way property. `softDeleteRetentionInDays` is parameterised correctly. The `diag` resource references `keyVault.id` via `scope: keyVault`, which is the right pattern. **No compile errors expected.**

- `storage.bicep` — `listKeys()` inside an `@secure()` output is valid since Bicep 0.10. `bacpacContainer` is a nested resource of `blobServices`; the `scope: bacpacContainer` in `funcMiBacpacContributor` resolves to a container-scoped role assignment. **No compile errors expected.**

- `sql.bicep` — `administrators` is an optional sub-block of `properties` per `2023-08-01-preview` schema; `null` is a valid value when omitted. `minCapacity: json('0.5')` is the correct way to feed a fractional capacity (string → number conversion). `useFreeLimit` and `freeLimitExhaustionBehavior` are the Free Offer flags — pinned api version exposes them. **No compile errors expected.** ⚠️ Bicep linter may warn `BCP037: unknown property` on `useFreeLimit` if the schema for the pinned api version is stale in the user's CLI cache; `az bicep upgrade` fixes it.

- `functions.bicep` — `linuxFxVersion: 'Python|3.12'` and `reserved: true` are correct for Linux Y1. `keyVaultReferenceIdentity: 'SystemAssigned'` is the documented value (since 2021). `kvRef` is a compile-time constant string built from `keyVaultName` (a passed-in parameter). **No compile errors expected.**

- `swa.bicep` — `repositoryUrl: ''`, `branch: ''`, `provider: 'None'`: all valid. `linkedBackend` is a separate resource; `parent: swa` is correct. **No compile errors expected.**

**Likely `bicep build` warnings (not errors):**

1. `BCP081: Resource type "Microsoft.Sql/servers/databases@2023-08-01-preview" does not have types available.` — known harmless warning for preview API versions. Can be silenced with `// bicep-linter disable-next-line use-stable-resource-identifiers`.
2. `BCP318: The value of type "..." can be null` — possible warning on `keyVault.properties.vaultUri` access if the linter version is conservative.
3. `secure-parameter-default` — Bicep linter rule `secure-parameter-default` will flag the `newGuid()` defaults on `sqlAdminPassword` and `swaForwardedSecret` (CR-01). This is a **warning by default but should be promoted to error** in `bicepconfig.json` for this project.

---

## Spec conformance matrix

| Spec item | File | Verdict | Notes |
|---|---|---|---|
| §4.1 RG name `rg-tcp-prod-weu` | `main.bicep:91-92` | ✅ | Composed from `tcp-${env}-${shortRegion}`. |
| §4.1 KV name `kv-tcp-prod-weu` | `main.bicep:99` | ✅ | |
| §4.1 SQL server name `sql-tcp-prod-weu` | `main.bicep:101` | ✅ | |
| §4.1 SQL DB name `sqldb-tcp-prod-weu` | `main.bicep:102` | ✅ | |
| §4.1 Storage `sttcpprodweu` (no hyphens) | `main.bicep:100` | ✅ | `'sttcp${env}${shortRegion}'` resolves to `sttcpprodweu`. |
| §4.1 Function App `func-tcp-prod-weu` | `main.bicep:104` | ✅ | |
| §4.1 SWA `swa-tcp-prod-weu` | `main.bicep:105` | ✅ | |
| §4.1 LA workspace `log-tcp-prod-weu` | `main.bicep:97` | ✅ | |
| §4.1 App Insights `ai-tcp-prod-weu` | `main.bicep:98` | ✅ | |
| §4.1 ASP `asp-tcp-prod-weu` | `main.bicep:103` | ✅ | |
| §4.1 tags `project/env/owner/costcenter/managedBy` | `main.bicep:70-76` | ✅ | Missing `repo` (MN-01). |
| §4.2 SQL `useFreeLimit: true` | `sql.bicep:133` | ✅ | |
| §4.2 SQL `freeLimitExhaustionBehavior: AutoPause` | `sql.bicep:134` | ✅ | |
| §4.2 SQL SKU `GP_S_Gen5_1` | `sql.bicep:118-122` | ✅ | |
| §4.2 SQL `autoPauseDelay: 60` | `sql.bicep:126` (param default) | ✅ | |
| §4.2 SQL `maxSizeBytes: 34359738368` | `sql.bicep:125` (param default) | ✅ | |
| §4.2 SQL `minCapacity: 0.5` | `sql.bicep:127` (`json('0.5')`) | ✅ | |
| §4.2 SQL collation `Latin1_General_100_CI_AS_SC_UTF8` | `sql.bicep:124` | ✅ | |
| §4.2 SQL `minimalTlsVersion: 1.2` | `sql.bicep:78` | ✅ | |
| §4.2 Storage `Standard_LRS` `StorageV2` | `storage.bicep:39-41` | ✅ | |
| §4.2 Storage `minimumTlsVersion: TLS1_2` | `storage.bicep:45` | ✅ | |
| §4.2 Storage `allowBlobPublicAccess: false` | `storage.bicep:47` | ✅ | |
| §4.2 BACPAC container + 28-day lifecycle | `storage.bicep:85-128` | ✅ | |
| §4.2 Function App `Y1` Linux Python 3.12 | `functions.bicep:62-72,90` | ✅ | |
| §4.2 Function App `httpsOnly: true` | `functions.bicep:85` | ✅ | |
| §4.2 Function App `ftpsState: Disabled` | `functions.bicep:91` | ✅ | |
| §4.2 Function App `minTlsVersion: 1.2` | `functions.bicep:92` | ✅ | |
| §4.2 Function App `alwaysOn: false` | `functions.bicep:94` | ✅ | |
| §4.2 Function App `WEBSITE_TIME_ZONE=E. Europe Standard Time` | `functions.bicep:124-126` | ✅ | |
| §4.2 Function App app setting `AZURE_CLIENT_ID` | (absent) | ⚠️ | MJ-02. |
| §4.2 SWA `Free` plan | `swa.bicep:43-48` | ✅ | |
| §4.2 KV `Standard` + `enableRbacAuthorization: true` | `keyvault.bicep:74-78` | ✅ | |
| §4.2 KV `softDelete=true`, retention 7d | `keyvault.bicep:79-80` | ✅ | |
| §4.2 KV `purgeProtection=false` (thesis cycle) | `keyvault.bicep:66,84` | ✅ | Intentional. |
| §4.2 App Insights workspace-based, `PerGB2018` | `observability.bicep:29-49` | ✅ | |
| §4.2 LA `dailyQuotaGb: 0.5` | `observability.bicep:24` | ❌ | CR-03 (default `1`, type `int`). |
| §5 RBAC matrix — Func MI → KV Secrets User | `keyvault.bicep:201-209` | ✅ | |
| §5 RBAC matrix — Func MI → Blob Data Contributor on `bacpac-exports` container | `storage.bicep:167-177` | ✅ | Correctly scoped to container, not account. |
| §5 RBAC matrix — Func MI → SQL DB Contributor at DB scope | `sql.bicep:198-206` | ✅ | |
| §5 RBAC matrix — OIDC SP → KV Secrets Officer | `keyvault.bicep:211-219` | ✅ | `principalType` issue (MJ-04). |
| §5 RBAC matrix — Deployer → Owner at RG | `main.bicep:121-131` | ✅ | |
| §6.5 AAD-only flip post-bootstrap | `postprovision.ps1:113-122` | ⚠️ | Works on first deploy; drift on re-deploy (CR-04). |
| §7 Secrets — ANTHROPIC-API-KEY | `keyvault.bicep:105-115` | ✅ | |
| §7 Secrets — SQL-ADMIN-PASSWORD-BOOTSTRAP | `keyvault.bicep:117-127` | ✅ | |
| §7 Secrets — SQL-ADMIN-PASSWORD-EXPORT | `keyvault.bicep:129-139` | ✅ | |
| §7 Secrets — STORAGE-CONNECTION-STRING | `keyvault.bicep:141-151` | ✅ | |
| §7 Secrets — SWA-FORWARDED-SECRET | `keyvault.bicep:153-163` | ✅ | |
| §7 Func App settings reference KV via `@Microsoft.KeyVault(SecretUri=...)` | `functions.bicep:48-55` | ✅ | |
| §7 No plaintext secrets in Function App settings | `functions.bicep:107-180` | ✅ | All secrets are KV refs; `APPLICATIONINSIGHTS_CONNECTION_STRING` is technically a credential but follows azd convention. |
| §8.1 `publicNetworkAccess: 'Enabled'` (free-tier honesty) | KV/Storage/SQL/Function all | ✅ | |
| §8.2 SWA→Function shared-secret `SWA-FORWARDED-SECRET` | `keyvault.bicep:153-163` + `functions.bicep:170-173` | ✅ | Bicep wires; `staticwebapp.config.json` enforcement is in SWA module (uploaded separately). |
| §11 BACPAC export Sunday 08:00 RO (Function timer) | (Function code in Etapa 5) | ⏳ | RBAC + container + lifecycle in place; timer trigger is Etapa 5 deliverable. |
| §12.1 SQL diagnostic settings → LA | `sql.bicep:140-186` | ✅ | All required categories present; possibly too verbose (MN-03). |
| §12.1 Func App diagnostic → LA | `functions.bicep:187-213` | ✅ | |
| §12.1 KV `AuditEvent` → LA | `keyvault.bicep:169-191` | ✅ | |
| §12.1 Storage `StorageRead/Write/Delete` → LA | `storage.bicep:132-158` | ✅ | |
| §16 Module map | `infra/modules/*.bicep` | ✅ | |
| §16 `identity.bicep` dropped, RBAC inlined per module | n/a | ✅ | MN-11 honoured. |
| §17 azure.yaml service `api` → `./function_app` | `azure.yaml:13-17` | ✅ | |
| §17 azure.yaml `hooks.postprovision` windows + posix | `azure.yaml:26-36` | ✅ | |
| §17 azure.yaml `infra.path: ./infra` | `azure.yaml:38-41` | ✅ | |
| Outputs: `AZURE_RESOURCE_GROUP`, `AZURE_LOCATION`, `AZURE_FUNCTION_APP_NAME`, `AZURE_FUNCTION_APP_PRINCIPAL_ID`, `AZURE_KEYVAULT_URI`, `AZURE_KEYVAULT_NAME`, `AZURE_SQL_SERVER_FQDN`, `AZURE_SQL_DATABASE_NAME`, `AZURE_STORAGE_ACCOUNT_NAME`, `AZURE_STATIC_WEB_APP_HOSTNAME`, `AZURE_APPLICATION_INSIGHTS_CONNECTION_STRING`, `AZURE_LOG_ANALYTICS_WORKSPACE_ID` | `main.bicep:260-275` | ✅ | All present (with `AZURE_` prefix per azd). `FUNCTION_APP_CLIENT_ID` absent (MJ-02). |
| Postprovision reads `AZURE_SQL_SERVER_NAME`, `AZURE_FUNCTION_APP_NAME`, `AZURE_KEYVAULT_NAME`, `AZURE_FUNCTION_APP_PRINCIPAL_ID`, `AZURE_RESOURCE_GROUP` | `postprovision.ps1:36-41` | ✅ | All consumed names match bicep outputs. |

---

## Recommendation

**ACCEPT_WITH_CHANGES.** The Bicep is structurally clean, follows the documented architectural decisions (RBAC inlining per MN-11, KV-reference indirection, Free Offer pinning, narrow Storage role scope, deny-by-default network ACLs justified by free-tier), and produces a deployable RG on first attempt. However, four critical findings must be addressed before this template is considered single-pass-idempotent:

- **CR-01** non-deterministic `newGuid()` defaults — must move out of Bicep.
- **CR-02** `AzureWebJobsStorage` KV-reference creates a runtime-startup cycle — switch to direct `@secure()` injection (or identity-based storage).
- **CR-03** `dailyQuotaGb` typing — switch to `string` + `json('0.5')`.
- **CR-04** `azureADOnlyAuthentication: false` re-applies on every redeploy — either parameterise or move the entire AAD admin block to imperative postprovision.

The eight major findings are deploy-time productivity / observability concerns (output naming alignment, `SCM_DO_BUILD_DURING_DEPLOYMENT` redundancy, `principalType` plumbing, SQL admin/principalType collapse, postprovision schema dependency, storage-key rotation gap, SWA service declaration). None block first deploy, all should be resolved before Etapa 4 is marked complete.

Sixteen minor / nit findings are quality-of-life improvements (tags `repo`, diagnostic-category trimming, postprovision JSON-parse, `ANTHROPIC_BASE_URL` parameterisation, comment hygiene).

**Next pass**: re-run this review after the CR/MJ fixes land; expect a convergence pass with security-auditor + deployment-engineer (per MEMORY rule "multi-agent verification ≥ 2 reviewers per major stage") before Etapa 4 is closed and `STATE.md` advances to Etapa 5.

---

*End of `review_etapa4_arch_pass1.md`.*
