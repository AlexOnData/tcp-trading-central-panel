# Etapa 4 convergence review — pass 2

**Reviewer**: code-reviewer (verification pass)
**Date**: 2026-05-16
**Verdict**: ACCEPT_WITH_MINOR_CHANGES

## Summary

The four critical findings from each pass-1 review have largely been addressed:
the `newGuid()` rotation trap, the `AzureWebJobsStorage` boot-time deadlock,
the `dailyQuotaGb` typing bug, the `azureADOnlyAuthentication` drift, the smoke
hostname/output mismatch, the smoke schema-history error classification, the
`AzureWebJobsStorage` storage-RBAC split, and the RLS-disable rollback guard
are all in place and traceable in code. However two pass-1 items remain only
partially resolved: (a) the `staticwebapp.config.json` file was added with
`<TENANT_ID>` and `<value-set-by-postprovision>` placeholders, but neither
`postprovision.ps1` nor `postprovision.sh` performs the substitution — the file
would deploy verbatim with literal placeholders, breaking SWA auth and the
shared-secret header; (b) the `bacpac_export.py` env-var alignment is only
half-done — `_ENV_SQL_ADMIN_PASSWORD` is still `"TCP_SQL_ADMIN_PASSWORD"` while
the Bicep app setting was renamed to `SQL_ADMIN_PASSWORD_EXPORT`, so the
Python and IaC sides have drifted in the opposite direction of the original
fix intent. One pre-existing `|| true` mask survived in `ci.yml:231`
(`iac-whatif`), which the security review flagged in MN-14. No regressions
introduced by the storage-RBAC split or the AAD-admin removal; the post-
provision script does not need anything the new `sql.bicep` no longer emits.

## Pass-1 ID status table

| ID | Source review | Severity | Status | Evidence |
|---|---|---|---|---|
| CR-01 | arch | Critical | RESOLVED | `infra/main.bicep:90-91` introduce `resolvedSqlAdminPassword` / `resolvedSwaForwardedSecret` as `var`s using `empty(param) ? newGuid() : param`. Both params default to `''` (lines 63, 67). Comments on lines 82-91 explain the contract. The `resolved*` vars are threaded to `sql.bicep` and `keyvault.bicep`. |
| CR-02 | arch | Critical | RESOLVED | `infra/main.bicep:171-227` reorders the modules so `storage` provisions BEFORE `functions`. `functions.bicep:51-53,134-137` consumes `azureWebJobsStorageConnectionString` as a `@secure() param` and writes the raw connection string into `AzureWebJobsStorage` (NOT a KV reference). The Function MI → container RBAC is handed off to `storage_rbac.bicep`, which runs AFTER the Function App so the principal id is known. |
| CR-03 | arch | Critical | RESOLVED | `infra/modules/observability.bicep:24` types `dailyQuotaGb` as `string = '0.5'`; line 41 wraps it in `json(dailyQuotaGb)` so the underlying property receives `0.5` as a number. Inline comment on line 23 documents the CR-03 fix. |
| CR-04 | arch | Critical | RESOLVED | `infra/modules/sql.bicep:53-73` removes the `administrators` sub-block entirely; comment on lines 64-71 references the imperative AAD-admin flip in postprovision. `postprovision.ps1:139-152` and `postprovision.sh:130-138` call `Set-AzSqlServerActiveDirectoryOnlyAuthentication`/`az sql server ad-only-auth enable`. No `azureADOnlyAuthentication: false` literal remains in any Bicep file. |
| CR-01 | deploy | Critical | RESOLVED | `cd.yml:54-61` validates `RG_ACTUAL == "rg-tcp-prod-weu"` after `azd env new` and exits 1 on mismatch. The `\|\| true` mask on `azd env new` is gone. |
| CR-02 | deploy | Critical | RESOLVED | `cd.yml:159-183` rewrites the schema-history smoke step: captures `SQLCMD_EXIT`, greps `query_out.txt` for `Invalid object name.*schema_history\|object_id.*schema_history.*not found`, exits 0 only on that specific pattern, and exits 1 for any other non-zero `SQLCMD_EXIT` after printing the captured output. |
| CR-01 | security | Critical | RESOLVED | `cd.yml:73,77` reads `AZURE_FUNCTION_APP_DEFAULT_HOSTNAME` (matching `infra/main.bicep:309`). Non-empty assertion is enforced at `cd.yml:76-79` before the curl. The downstream smoke step on line 151 consumes `needs.provision.outputs.function_app_hostname`. |
| CR-02 | security | RESOLVED | Both scripts wrap the RLS-disable + INSERT in a guard. `postprovision.ps1:60-91` uses T-SQL `BEGIN TRY / BEGIN CATCH ALTER SECURITY POLICY ... STATE = ON / THROW`, plus an outer PowerShell `try / catch / finally` (lines 93-108) that re-issues `ALTER SECURITY POLICY ... STATE = ON` via a second `sqlcmd` invocation. `postprovision.sh:68-105` mirrors the T-SQL TRY/CATCH and installs a `trap '... STATE = ON' EXIT`. **Caveat**: the bash `trap` is installed AFTER the first `execute_sql` call (line 102), so a SIGKILL during that first call would not fire the trap. The T-SQL TRY/CATCH still re-enables the policy inside `sqlcmd` itself, so the data-plane invariant holds; consider hoisting the `trap` line above the first `execute_sql` for full belt-and-braces coverage. |
| CR-03 | security | PARTIALLY RESOLVED | `function_app/staticwebapp.config.json` exists with `routes`, `forwardingGateway.requiredHeaders["X-SWA-Forwarded"]`, `auth.identityProviders.azureActiveDirectory` block, and `globalHeaders.Content-Security-Policy`. However the placeholders `<TENANT_ID>` (line 8) and `<value-set-by-postprovision>` (line 32) are still literal — neither `postprovision.ps1` nor `postprovision.sh` performs the substitution (grep for `TENANT_ID\|value-set-by-postprovision\|staticwebapp` in `infra/scripts/` returns zero hits). The SWA upload step would deploy the file with placeholders intact, breaking AAD redirects and the shared-secret header injection. |
| CR-04 | security | PARTIALLY RESOLVED | Five of the six env-var pairs align (`_ENV_SUBSCRIPTION_ID`, `_ENV_RESOURCE_GROUP`, `_ENV_SQL_SERVER_NAME`, `_ENV_SQL_DATABASE_NAME`, `_ENV_BACPAC_CONTAINER_URI`, `_ENV_SQL_ADMIN_LOGIN` all match Bicep). **Two mismatches remain**: (1) `bacpac_export.py:50` declares `_ENV_SQL_ADMIN_PASSWORD: Final[str] = "TCP_SQL_ADMIN_PASSWORD"`, but `functions.bicep:239` emits the setting as `SQL_ADMIN_PASSWORD_EXPORT`. The comment in `functions.bicep:235-238` claims the rename matches Python, but the Python constant was never updated. (2) `bacpac_export.py:48` declares `_ENV_BACPAC_STORAGE_KEY: Final[str] = "TCP_BACPAC_STORAGE_KEY"` but Bicep emits no setting of either name. When Etapa 5 wires the real Export call, both `os.environ.get(...)` lookups return `""` and the call will fail (or invite a quick-fix that bypasses KV). |
| MA-02 | deploy | RESOLVED | `cd.yml:63-66, 118-121` pass `ANTHROPIC_API_KEY` via job-scoped `env:` block on each `azd` step. The earlier `azd env set ANTHROPIC_API_KEY ${{ secrets.… }}` line is gone. The secret is now masked by GitHub's log redactor (env vars are scrubbed) and never lands in `.azure/tcp-prod/.env`. |
| MA-07 | security | RESOLVED | `postprovision.ps1:110-137` and `postprovision.sh:109-128` both (a) run `az functionapp config appsettings set --settings TCP_GENERATOR_OID=$pid` and (b) immediately follow with `az functionapp restart`. The race window between provision and first timer fire is closed. Additionally `tcp/synth/runner.py:48,208-213` raises `RuntimeError` when the OID env var is unset or malformed, which surfaces the race as a loud failure in App Insights rather than silent RLS-deny rows. |
| MA-04 | arch | RESOLVED | `infra/main.bicep:271` threads `oidcPrincipalType: principalType` into `keyvault.bicep`. `keyvault.bicep:39-47` adds `param oidcPrincipalType string = 'ServicePrincipal'` with `@allowed(['User','ServicePrincipal','Group'])`. Line 236 forwards it to `properties.principalType`. The SQL admin RA already had this pattern (`sql.bicep` no longer manages the AAD admin per CR-04, so the SQL principalType plumbing is moot). |
| MA-02 | security | RESOLVED | `.github/workflows/ci.yml:144` reads `uv run bandit -r tcp function_app -lll -iii` — scope now covers both packages. |
| Cross-cutting | postprovision schema-apply order | NOT RESOLVED | Pass-1 cross-cutting expected V001 + V002 to be applied BEFORE the RLS-disable + INSERT step. Neither `postprovision.ps1` nor `postprovision.sh` runs schema migrations; both still rely on the `try/catch` to swallow a "schema not yet applied" failure with a warning (`postprovision.ps1:97-98`, `postprovision.sh:97-99`). The defensive comment "may be expected if schema migrations have not yet run (pre-V001)" remains. On a clean first deploy this means the postprovision will green-light despite RLS not being wired — Etapa 4 acceptance bullet `A.1` is satisfied only if Etapa 5 lands the migration step. Document the deferral or gate the postprovision on a `SELECT object_id('rls.TradesAccessPolicy')` probe. |
| Cross-cutting | staticwebapp.config.json placeholder substitution | NOT RESOLVED | Same evidence as CR-03 security. The file ships with literal `<TENANT_ID>` / `<value-set-by-postprovision>` strings; the postprovision scripts do not perform `sed`/`-replace` substitutions. The SWA upload pipeline must either substitute these out-of-band or the file must be split into a static portion + a templated portion that is rendered at deploy time. |
| Cross-cutting | English-only | RESOLVED | All new artifacts (`storage_rbac.bicep`, `staticwebapp.config.json`, updated postprovision scripts, `cd.yml`, `ci.yml`) are English-only. No Romanian found in committed code. |
| Cross-cutting | no new `\|\| true` masks | PARTIALLY RESOLVED | `cd.yml` is clean. `ci.yml:231` retains `2>&1 \| tee whatif-output.txt \|\| true` on the `iac-whatif` step (pre-existing per security MN-14 follow-up). No new masks introduced by the fix pass. |
| Cross-cutting | no new `TODO: pin to full SHA` markers | RESOLVED | Grep against the workflow files returns zero hits. Existing third-party action pins (`gitleaks/gitleaks-action@ff98106e…`, `astral-sh/setup-uv@08807647…`) carry full SHAs. |

## Regressions

None of the substantive fixes introduced a regression in the verified surface.
Specifically:

1. **Storage-RBAC split**: `storage_rbac.bicep` correctly references the
   storage account and container via `existing` resources; the `funcMiPrincipalId`
   parameter is consumed only when non-empty; the role assignment is scoped to
   the container, preserving the §5 RBAC matrix narrow grant. The original
   `storage.bicep:175-185` role-assignment block still exists as a no-op when
   `funcMiPrincipalId` is empty (which it always is on the first pass per
   `main.bicep:187`), so there is no double-binding risk.
2. **AAD-admin removal from `sql.bicep`**: the postprovision scripts use
   `Set-AzSqlServerActiveDirectoryOnlyAuthentication` (PS) and
   `az sql server ad-only-auth enable` (sh), neither of which requires the
   Bicep-side `administrators` block to be present. The verification step
   (`postprovision.ps1:176-192`, `postprovision.sh:155-168`) reads
   `az sql server ad-only-auth list/get` to confirm — no dependency on the
   removed Bicep state.
3. **`AzureWebJobsStorage` direct injection**: `functions.bicep:135-137` writes
   the raw `@secure()` value; the value still lands in app settings (per Azure
   Functions runtime requirements) but is omitted from deployment history via
   the `@secure()` param chain. The KV `STORAGE-CONNECTION-STRING` secret is
   still populated by `keyvault.bicep:157-167` for ADR-004 BACPAC export
   parity; both consumers receive the same value sourced from the single
   `listKeys()` call in `storage.bicep:199`.
4. **Module ordering in `main.bicep`**: `storage` precedes `functions`;
   `functions` precedes `storageRbac`, `sql`, `keyvault`, `swa`. All output
   consumption resolves before use. No circular references; `bicep build`
   should succeed.

One **minor regression risk** (not a hard regression yet, but worth flagging):
the `cd.yml` smoke step (line 162-164) discovers the SQL server / database via
`az sql server list -g "${RG}" --query "[0].name" -o tsv` — this picks the
first server alphabetically and works today because there is only one. If a
future fix adds a second server in the RG (e.g., a replica), this becomes
fragile. Consider switching to `needs.provision.outputs.sql_server_name` once
the output is plumbed through the `provision` job.

## Remaining gaps

1. **staticwebapp.config.json substitution** (security CR-03 follow-up).
   Either (a) add a substitution step to both postprovision scripts that does
   `sed -i "s|<TENANT_ID>|$AZURE_TENANT_ID|; s|<value-set-by-postprovision>|@Microsoft.KeyVault(SecretUri=${KV_URI}secrets/SWA-FORWARDED-SECRET/)|"` against the file in the SWA upload staging dir, OR (b) move the file under `static/` with a `staticwebapp.config.template.json` + a render step in the SWA deploy workflow, OR (c) configure the SWA app settings (`AZURE_CLIENT_ID`, plus a `SWA_FORWARDED_SECRET` KV reference) and rely on SWA's built-in `@Microsoft.KeyVault(...)` resolver — verify the SWA platform supports KV references inside `forwardingGateway.requiredHeaders` values (per Microsoft docs, this support was GA in early 2024). Until one of these is wired, the SWA frontend's auth + linked-backend handshake will fail at runtime.

2. **`bacpac_export.py` env-var realignment** (security CR-04 residual). Align
   the Python constants with the Bicep names. Recommended direction: rename
   `_ENV_SQL_ADMIN_PASSWORD = "SQL_ADMIN_PASSWORD_EXPORT"` (matching ADR-004
   and the current `functions.bicep:239`). Add a new Bicep setting
   `TCP_BACPAC_STORAGE_KEY` sourced from the KV reference for
   `STORAGE-CONNECTION-STRING` (extracted via Python at runtime or split into
   a dedicated `STORAGE-ACCOUNT-KEY` secret). Etapa 5 cannot wire the real
   Export call without this alignment.

3. **Postprovision schema-apply ordering** (cross-cutting). The current
   scripts assume V001/V002 are applied by some other process before the
   RLS-disable + INSERT step. Either (a) inline the schema apply at the top of
   each postprovision script (read `db/migrations/V*.sql`, run them via
   `sqlcmd -G`, then proceed), or (b) gate the RLS block on a
   `SELECT OBJECT_ID('rls.TradesAccessPolicy')` probe and log a clear
   `deferred-to-first-schema-deploy` message instead of warning-and-continue.
   Confirm in Etapa 5 that the schema migration lands before the first
   re-provision under steady state.

4. **`ci.yml` `iac-whatif` `|| true`** (security MN-14 carry-over, NOT a
   regression). The `tee whatif-output.txt || true` allows the `github-script`
   step to post a comment even when `what-if` fails; pass-1 flagged that the
   failure mode posts the auth-failure log to the PR. Acceptable as a
   follow-up; consider capturing only the resource-diff portion of stdout and
   failing the job on non-zero `what-if` exit.

5. **Bash `trap` placement** (security CR-02 residual). The bash `trap '...
   STATE = ON' EXIT` is installed AFTER the first `execute_sql` call. Move it
   above the call so a SIGKILL during the first `execute_sql` still fires the
   re-enable. The T-SQL `TRY/CATCH` still covers the same invariant inside
   `sqlcmd`, so this is belt-and-braces.

6. **Pass-1 minor / nit items**: arch MJ-01..MJ-08 (output names, `WEBSITE_RUN_FROM_PACKAGE`/`SCM_DO_BUILD_DURING_DEPLOYMENT` conflict — note `SCM_DO_BUILD_DURING_DEPLOYMENT` IS now absent from `functions.bicep` per MA-03 fix), security MJ-01..MJ-08, deploy MA-01/MA-03, plus all MN items, are out of scope for this convergence pass and should be tracked in STATE.md for the Etapa-5 hardening pass.

## Recommendation

**ACCEPT_WITH_MINOR_CHANGES.** All four pass-1 critical items per review (12
total CR badges) are resolved or partially resolved. The two PARTIAL items
(security CR-03 placeholder substitution, security CR-04 env-var residual)
are non-trivial but **do not block the first `azd up`** — they manifest at
runtime in the SWA frontend (auth + shared-secret header) and in the
Etapa-5-deferred BACPAC export call. Recommended path forward:

1. Address security CR-03 substitution and CR-04 Python-side env-var rename
   in a follow-up commit before merging to `main` (one-line change each,
   plus a `sed`/`-replace` step in both postprovision scripts).
2. File the postprovision schema-apply gap as an Etapa-5 deliverable in
   STATE.md (it is genuinely an Etapa-5 dependency).
3. Tag `v1.0-etapa4` after the two follow-up commits land; advance STATE.md
   to Etapa 5.
4. Re-run multi-agent verification on the follow-up commit (cloud-architect
   + security-auditor focused passes; deployment-engineer not needed unless
   `cd.yml` changes).

No structural rework required. The Bicep is structurally clean, the post-
provision scripts are idempotent, the CD pipeline is fail-fast on every
gate that pass-1 flagged, and no regressions were introduced by the two
specialist agents' fix passes.

---

*End of `review_etapa4_convergence_pass2.md`.*
