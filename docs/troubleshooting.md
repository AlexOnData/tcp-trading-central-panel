# Troubleshooting index

The 9 most common failure modes a TCP operator hits, with the diagnostic command for each. Ordered by likelihood at first deploy + day-to-day frequency.

Each entry follows the same template:

> **Symptom** (what you see) → **Diagnostic** (commands that confirm the diagnosis) → **Resolution** (the actual fix) → **Reference** (source docs for the deeper story).

---

## Diagnostic preamble — define the shell variables once

> **Read this before running any diagnostic below.** Every command in this document assumes the following environment variables are exported. Source this preamble once at the top of every troubleshooting session — most "diagnostic produces no output" mistakes come from an undefined `$RG` or `$SQL_FQDN` silently being expanded to empty (docs-CR-02 fix from the Etapa-9 review).

### POSIX (bash / zsh)

```bash
# Resolve every variable from the azd environment in one block.
export RG=$(azd env get-value AZURE_RESOURCE_GROUP)
export SUB_ID=$(azd env get-value AZURE_SUBSCRIPTION_ID)
export FUNC_APP_NAME=$(azd env get-value AZURE_FUNCTION_APP_NAME)
export SQL_SERVER=$(azd env get-value AZURE_SQL_SERVER_NAME)
export SQL_DB=$(azd env get-value AZURE_SQL_DATABASE_NAME)
export SQL_FQDN="${SQL_SERVER}.database.windows.net"
export KV_NAME=$(azd env get-value AZURE_KEYVAULT_NAME)
export STORAGE_ACCOUNT=$(azd env get-value AZURE_STORAGE_ACCOUNT_NAME)
# App Insights id — derive via az, not azd (azd doesn't export it)
export AI_APP_ID=$(az resource show -g "$RG" --resource-type Microsoft.Insights/components \
  -n "ai-tcp-prod-weu" --query properties.AppId -o tsv)
```

### PowerShell

```powershell
$env:RG             = (azd env get-value AZURE_RESOURCE_GROUP)
$env:SUB_ID         = (azd env get-value AZURE_SUBSCRIPTION_ID)
$env:FUNC_APP_NAME  = (azd env get-value AZURE_FUNCTION_APP_NAME)
$env:SQL_SERVER     = (azd env get-value AZURE_SQL_SERVER_NAME)
$env:SQL_DB         = (azd env get-value AZURE_SQL_DATABASE_NAME)
$env:SQL_FQDN       = "$env:SQL_SERVER.database.windows.net"
$env:KV_NAME        = (azd env get-value AZURE_KEYVAULT_NAME)
$env:STORAGE_ACCOUNT = (azd env get-value AZURE_STORAGE_ACCOUNT_NAME)
$env:AI_APP_ID      = (az resource show -g $env:RG --resource-type Microsoft.Insights/components `
                       -n "ai-tcp-prod-weu" --query properties.AppId -o tsv)
```

Verify the variables resolved (any empty value means `azd env new tcp-prod` was not run or the resource is missing — go back to [`setup.md`](setup.md) §B before continuing):

```bash
echo "$RG / $FUNC_APP_NAME / $SQL_FQDN / $KV_NAME"   # bash
# All four should be non-empty.
```

---

## 1. `azd up` fails: "principal does not have permission to create role assignments"

**Symptom**
`azd provision` errors with `AuthorizationFailed` mentioning `Microsoft.Authorization/roleAssignments/write`.

**Diagnostic**
```bash
az role assignment list --assignee "$AZURE_CLIENT_ID" --scope "/subscriptions/$SUB_ID" -o table
```

**Resolution**
The OIDC SP needs `Owner` (not `Contributor`) at subscription scope so it can assign roles to the Function App MI. Re-run the one-time setup from [`setup.md`](setup.md) §B.2 with the correct role.

**Reference**
- [`docs/design/03_architecture.md`](design/03_architecture.md) §5 RBAC matrix
- [`docs/design/03_architecture.md`](design/03_architecture.md) §6.1 OIDC federation setup

---

## 2. `/api/ask` returns 404 "Your account is not registered for this application"

**Symptom**
SWA login succeeds, but `/api/ask` returns the 404 envelope with `error.code = "principal_not_registered"`.

**Diagnostic**
```bash
# Grab your oid from the Azure portal: AAD → Users → <you> → Object ID
# Then confirm it is missing from dim_UserRoles
sqlcmd -S "$SQL_FQDN" -d "$SQL_DB" -G -Q "
  SELECT aad_object_id, scope, is_active
  FROM dbo.dim_UserRoles
  WHERE aad_object_id = CAST('<your-oid>' AS UNIQUEIDENTIFIER)"
```

**Resolution**
Add the row manually (the postprovision script only adds the Function MI; human users are not provisioned automatically in the academic phase):

```sql
INSERT INTO dbo.dim_UserRoles (aad_object_id, employee_id, scope, is_active, created_at)
VALUES (CAST('<your-oid>' AS UNIQUEIDENTIFIER), 1, 'admin', 1, SYSDATETIMEOFFSET());
```

For a thesis demo with real reviewers, register one `admin`-scoped row per examiner. For trader/team-lead/floor-manager testing, register the matching `employee_id` with the corresponding scope.

**Reference**
- [ADR-003](decisions/ADR-003-rls-session-context.md) — `aad_object_id` is the immutable identity binding
- [ADR-005](decisions/ADR-005-scope-resolution-rls-bypass.md) — scope-lookup path

---

## 3. `schema_history.checksum` contains `__V001_CHECKSUM__` or `sentinel-no-checksum-supplied`

**Symptom**
CD smoke job fails: `ERROR: schema_history contains an unsubstituted checksum placeholder`. Or you ran `Apply locally` from the dev guide and the dev database now shows the sentinel.

**Diagnostic**
```bash
sqlcmd -S "$SQL_FQDN" -d "$SQL_DB" -G \
  -Q "SELECT script_name, checksum FROM dbo.schema_history"
```

**Resolution**
- **In production**: the postprovision Step 0 failed to substitute. Re-run it manually:
  ```bash
  cd "$(git rev-parse --show-toplevel)"
  bash infra/scripts/postprovision.sh    # POSIX
  pwsh -c "./infra/scripts/postprovision.ps1"   # Windows
  ```
  Step 0 is idempotent — it re-renders the migration with the real SHA-256 and the `MERGE … WITH (HOLDLOCK)` upsert refreshes the `checksum` column.
- **In local dev**: harmless. The sentinel only matters when the CD smoke job inspects it. Local applies use `sqlcmd -i V001__init.sql` directly, which leaves the placeholder in place; that is the documented Track A behaviour.

**Reference**
- [`docs/security/threat_model.md`](security/threat_model.md) RR-09 closure
- [`scripts/compute_migration_checksum.py`](../scripts/compute_migration_checksum.py) module docstring
- [`scripts/render_migration.py`](../scripts/render_migration.py) module docstring

---

## 4. `/api/ask` returns 500 "Anthropic backend is currently unavailable"

**Symptom**
The envelope has `error.code = "anthropic_unavailable"`. The structured log shows `tcp.func.ask.anthropic_failed`.

**Diagnostic**
```bash
# Check the key is in Key Vault
az keyvault secret show --vault-name "$KV_NAME" --name 'ANTHROPIC-API-KEY' --query attributes.enabled

# Check the Function App can read it
az webapp config appsettings list --name "$FUNC_APP_NAME" -g "$RG" \
  --query "[?name=='ANTHROPIC_API_KEY']"
# The value should be `@Microsoft.KeyVault(SecretUri=...)` — a KV reference, not a literal.

# Check Anthropic status
curl -sI https://api.anthropic.com/v1/models
```

**Resolution**
- If the key was rotated and the KV entry is stale → follow [`docs/security/credentials_rotation.md`](security/credentials_rotation.md) §2.1.
- If the Function App lost RBAC to read the KV secret → re-run `azd provision` to re-apply the `keyvault.bicep` role assignments.
- If Anthropic is down → wait it out. The burn-rate alert `tcp-alert-ask-availability-burn` (severity 1) fires after 5 minutes of sustained 5xx.

**Reference**
- [`docs/observability/slo.md`](observability/slo.md) §4 — SLI-1 availability burn
- [`tcp/ai/anthropic_client.py`](../tcp/ai/anthropic_client.py) — error class hierarchy

---

## 5. Daily generator did not run at 07:00 RO

**Symptom**
The 07:30 RO PowerBI refresh shows yesterday's data instead of today's; the workbook "Daily generator runs vs failures" tile is empty for the current day.

**Diagnostic**
```bash
# Pull the last 24h of generator invocations from App Insights
az monitor app-insights query --apps "$AI_APP_ID" --analytics-query \
  "requests | where operation_Name == 'daily_generator' | where timestamp > ago(24h) | project timestamp, success, resultCode, duration"

# Check TCP_GENERATOR_OID is wired
az functionapp config appsettings list --name "$FUNC_APP_NAME" -g "$RG" \
  --query "[?name=='TCP_GENERATOR_OID'].value" -o tsv

# Confirm WEBSITE_TIME_ZONE is set so the NCRONTAB expression resolves in
# Europe/Bucharest (DST-safe).
az functionapp config appsettings list --name "$FUNC_APP_NAME" -g "$RG" \
  --query "[?name=='WEBSITE_TIME_ZONE'].value" -o tsv
```

**Resolution**
- **Empty result from App Insights**: the trigger never fired. Verify `WEBSITE_TIME_ZONE = E. Europe Standard Time` (not UTC); restart the Function App via `az functionapp restart -n "$FUNC_APP_NAME" -g "$RG"`.
- **Result shows `success: false`**: query the actual exception via App Insights → `traces | where message has "tcp.func.daily_generator.failed"`. Common causes: SQL paused beyond the 30 s warmup, RLS predicate denying the admin scope (TCP_GENERATOR_OID not set), or a synth-data invariant violation.
- **Holiday short-circuit**: on RO public holidays the proc returns `status='skipped_holiday'` — that is the **intended outcome** and counts as a success against SLI-2.
- **Manual fire**: trigger an immediate generator run by issuing an admin-scope HTTP request against the function's admin endpoint:
  ```bash
  FUNC_KEY=$(az functionapp keys list -n "$FUNC_APP_NAME" -g "$RG" --query masterKey -o tsv)
  curl -X POST "https://${FUNC_APP_NAME}.azurewebsites.net/admin/functions/daily_generator" \
    -H "x-functions-key: $FUNC_KEY" \
    -H "Content-Type: application/json" -d '{"input":""}'
  ```

**Reference**
- [ADR-003](decisions/ADR-003-rls-session-context.md) §3 — the admin-scope path used by `daily_generator`
- [`tcp/synth/runner.py`](../tcp/synth/runner.py) — the runner that the timer calls
- [`docs/observability/slo.md`](observability/slo.md) SLI-2 — the budget

---

## 6. BACPAC export missed Sunday 08:00 RO

**Symptom**
The `tcp-alert-bacpac-missed` alert fires (severity 2). No new `tcp-YYYYMMDD.bacpac` blob in the `bacpac-exports` container.

**Diagnostic**
```bash
# Check the last successful export
az storage blob list --container-name bacpac-exports \
  --account-name "$STORAGE_ACCOUNT" --auth-mode login \
  --query "sort_by([], &properties.lastModified)[-3:].{name:name, last:properties.lastModified}" -o table

# Pull bacpac trigger invocations from App Insights
az monitor app-insights query --apps "$AI_APP_ID" --analytics-query \
  "traces | where customDimensions['event'] startswith 'tcp.bacpac' | where timestamp > ago(14d) | project timestamp, message, customDimensions"
```

**Resolution**
- **SQL admin export password expired**: rotate via [`docs/security/credentials_rotation.md`](security/credentials_rotation.md) §2.2.
- **MI lost `SQL DB Contributor`**: re-run `azd provision` so `sql.bicep` re-creates the role assignment.
- **Lifecycle policy pruned a successful run**: if the only blobs in the container are >28 days old, the policy has already pruned this week's export AND the alert correctly fired. Re-fire the trigger manually via its admin endpoint:
  ```bash
  FUNC_KEY=$(az functionapp keys list -n "$FUNC_APP_NAME" -g "$RG" --query masterKey -o tsv)
  curl -X POST "https://${FUNC_APP_NAME}.azurewebsites.net/admin/functions/bacpac_export" \
    -H "x-functions-key: $FUNC_KEY" \
    -H "Content-Type: application/json" -d '{"input":""}'
  ```

**Reference**
- [ADR-004](decisions/ADR-004-bacpac-export-schedule.md) — the export contract
- [`function_app/triggers/bacpac_export.py`](../function_app/triggers/bacpac_export.py) — the trigger body

---

## 7. SQL Free Offer auto-paused mid-business-day

**Symptom**
The first `/api/ask` request of the day returns 500 with `error.code = "execution_failed"`; subsequent requests succeed. The `tcp.func.ping.complete` event shows `sql_resume_ms` in the 30 000-90 000 range.

**Diagnostic**
```bash
# Check the database auto-pause state
az sql db show --name "$SQL_DB" --server "$SQL_SERVER" -g "$RG" \
  --query "{status:status, autoPauseDelay:autoPauseDelay, currentSku:currentSku.name}"
```

**Resolution**
- This is the **intended** Free Offer behaviour: after 60 minutes of inactivity, the database pauses. The Function App's `WarmupTrigger` (06:55 RO Mon-Fri) keeps it hot before the 07:00 generator, but does *not* keep it hot all day.
- For a thesis-demo window where you need predictable warm state: hit `/api/ping` every 30 minutes (via `curl` in a loop, or by leaving the SWA open).
- **Don't** raise `autoPauseDelay` above 60 minutes — anything higher exits the Free Offer pricing.

**Reference**
- [`docs/design/03_architecture.md`](design/03_architecture.md) §4.2 — Free Offer config
- [`function_app/triggers/ping.py`](../function_app/triggers/ping.py) — the resume hook

---

## 8. The bootstrap window slipped: SQL-auth still alive after `azd up`

**Symptom**
`az sql server ad-only-auth list -s "$SQL_SERVER" -g "$RG"` returns `azureADOnlyAuthentication: false` long after `azd up` reported success.

**Diagnostic**
```bash
# Inspect the AAD-only state
az sql server ad-only-auth list --server "$SQL_SERVER" --resource-group "$RG" \
  --query "[].{enabled:azureADOnlyAuthentication}"

# Confirm the bootstrap admin password was deleted
az keyvault secret show --vault-name "$KV_NAME" --name SQL-ADMIN-PASSWORD-BOOTSTRAP \
  --query attributes.enabled 2>&1
# Expected: "SecretNotFound" or similar — anything else means the cleanup did not run.
```

**Resolution**
Step 3 of [`infra/scripts/postprovision.{ps1,sh}`](../infra/scripts/) may have errored mid-way. Re-run the postprovision idempotently:

```bash
bash infra/scripts/postprovision.sh    # POSIX
pwsh -c "./infra/scripts/postprovision.ps1"   # Windows
```

If Step 3 still fails, the AAD admin entry on the SQL server may be missing. Re-register:

```bash
USER_OID=$(az ad signed-in-user show --query id -o tsv)
USER_UPN=$(az ad signed-in-user show --query userPrincipalName -o tsv)
az sql server ad-admin create --server "$SQL_SERVER" -g "$RG" \
  --display-name "$USER_UPN" \
  --object-id "$USER_OID"
```

Then enable AAD-only auth (the CLI uses a dedicated subcommand, not a generic `--enable-ad-only-auth` flag):

```bash
az sql server ad-only-auth enable --server "$SQL_SERVER" --resource-group "$RG"
```

**Reference**
- [`docs/security/bootstrap_window.md`](security/bootstrap_window.md) — full operator runbook
- [`docs/security/threat_model.md`](security/threat_model.md) RR-08 — residual risk acceptance

---

## 9. PowerBI dataset refresh fails: "Cannot connect to SQL Server"

**Symptom**
PowerBI Service refresh history shows `DM_GWPipeline_Gateway_DataSourceAccessError`. The dataset's last successful refresh is stale.

**Diagnostic**
1. PowerBI Service → workspace → dataset → Settings → Data source credentials. Confirm the SQL data source is using a service principal credential, not a personal token.
2. Confirm the SP is registered in `dim_UserRoles` with `scope='admin'`:
   ```sql
   SELECT * FROM dbo.dim_UserRoles
   WHERE aad_object_id = CAST('<powerbi-sp-oid>' AS UNIQUEIDENTIFIER)
   ```

**Resolution**
- **Missing SP in `dim_UserRoles`**: insert one row with `scope='admin'`, `is_active=1`. Re-run the dataset refresh.
- **SP secret expired**: re-issue the federated credential per [`docs/security/credentials_rotation.md`](security/credentials_rotation.md) §2.7.
- **SQL server firewall blocking the PowerBI Service IPs**: the `AllowAllAzureServices` virtual rule should cover this; verify it exists via `az sql server firewall-rule list -g $RG -s $SQL_SERVER -o table`. If missing, re-run `azd provision` to restore.

**Reference**
- [`docs/runbooks/powerbi_deploy.md`](runbooks/powerbi_deploy.md) — full PowerBI deploy runbook
- [`powerbi/README.md`](../powerbi/README.md) — known limitations + manual finalisation steps

---

## Diagnostic shortcuts

| Need | Command (assuming the preamble was sourced) |
|---|---|
| Find the resource group | `echo "$RG"` (or `azd env get-value AZURE_RESOURCE_GROUP`) |
| Tail recent App Insights traces | `az monitor app-insights query --apps "$AI_APP_ID" --analytics-query "traces \| where timestamp > ago(15m) \| order by timestamp desc"` |
| Re-run postprovision (idempotent) | `bash infra/scripts/postprovision.sh` or `pwsh -c "./infra/scripts/postprovision.ps1"` |
| Manually invoke a timer trigger | Use the admin endpoint with the master function key: `curl -X POST "https://${FUNC_APP_NAME}.azurewebsites.net/admin/functions/<name>" -H "x-functions-key: $(az functionapp keys list -n "$FUNC_APP_NAME" -g "$RG" --query masterKey -o tsv)" -H "Content-Type: application/json" -d '{"input":""}'` |
| Open the workbook | Azure Portal → Monitor → Workbooks → Recent → *TCP — Operations dashboard* |

## When this guide does not have your symptom

The 9 scenarios above are the ones a single operator hits across the academic-build lifecycle. For genuinely novel failures:

1. Start with the **structured logs**: `traces | where customDimensions["event"] startswith "tcp."` filtered to the last 30 minutes — every TCP component emits a `tcp.*` event prefix.
2. Cross-reference the [SLO doc](observability/slo.md) §6 known issues — some failures are documented as ACCEPTED RESIDUAL from previous review passes.
3. Check the [threat model](security/threat_model.md) §7 residual risks (RR-01..09) — each lists the follow-up trigger that converts the risk into a real failure.
4. If still stuck, file a notes-only commit on a topic branch with the structured-log excerpt and the diagnostic commands you ran, and open an issue. The thesis-defence build is single-operator, so the issue tracker is also the post-mortem ledger.
