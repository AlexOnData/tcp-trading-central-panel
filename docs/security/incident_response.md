# Incident Response Runbook — TCP Trading Central Panel

**Version**: 1.0
**Date**: 2026-05-16
**Status**: Active
**Audience**: Project operator (thesis author acting as sole system administrator)

This runbook covers the canonical break-the-glass scenarios for the TCP platform. Each scenario includes a severity rating, concrete Azure CLI and KQL commands, and a recovery verification checklist. Read `docs/security/credentials_rotation.md` before executing any rotation step.

---

## 1. Incident Severities

| Level | Definition | Examples | Response time target |
|---|---|---|---|
| **P0** | Data confidentiality breach or full identity compromise. | Cross-tenant RLS leak; Function App MI compromised; OIDC SP token exfiltrated and used. | Immediate — within the hour. |
| **P1** | Service availability significantly degraded or a high-value credential confirmed compromised. | Function App down for > 30 minutes; Anthropic API key confirmed leaked; SQL admin password leaked. | Same business day. |
| **P2** | Cost anomaly or non-critical degradation. | Anthropic spend spike; SQL vCore quota unexpectedly exhausted; BACPAC missed two Sundays in a row. | Next business day. |
| **P3** | Minor operational issue. | Single failed deploy; one transient HTTP 500; lint failure in CI on a non-main branch. | Best effort within one week. |

---

## 2. Detection Sources

| Source | What it catches | How to access |
|---|---|---|
| **App Insights alerts** | Rate-limit spikes (`tcp.func.ask.rate_limited`), Anthropic error bursts, Function failure counts, missing BACPAC event. | Azure Portal → `ai-tcp-prod-weu` → Alerts. KQL queries in `docs/design/reviews/review_etapa6_security_sweep.md §A09`. |
| **GitHub Actions failures** | CI gate failures (`gitleaks`, `bandit`, `pip-audit`, `pytest`, `ruff`, `mypy`), deployment failures. | GitHub → Actions tab → failed run → logs. |
| **Manual user reports** | Unexpected 403/500 on `/api/ask`; wrong data returned; suspicious activity from another user's perspective. | Direct report to the operator (thesis author). |
| **Anthropic console dashboard** | Spend spike, unusual call patterns, API key usage from unexpected IPs. | https://console.anthropic.com → Usage. |
| **Azure Cost Management** | Unexpected charges on the subscription. | Azure Portal → Cost Management → Cost Analysis. |
| **KV Audit Log** | Secret reads, secret writes, access from unexpected principals. | Log Analytics → `AzureDiagnostics | where ResourceType == "VAULTS" | where OperationName == "SecretGet"`. |

---

## 3. Containment Procedures

### Scenario A — Cross-Tenant RLS Leak (P0)

**Symptom**: A user reports seeing another employee's trade data. Confirmed by reviewing App Insights logs for the offending OID returning rows outside their scope.

**Immediate containment:**

```sql
-- Step 1: Revoke the offending user's dim_UserRoles entry.
-- Connect to sqldb-tcp-prod-weu as an AAD admin (developer account or break-glass).
UPDATE dbo.dim_UserRoles
SET    is_active = 0
WHERE  aad_object_id = '<offending-aad-object-id>';

-- Verify:
SELECT * FROM dbo.dim_UserRoles WHERE aad_object_id = '<offending-aad-object-id>';
-- Expect: is_active = 0.
```

```bash
# Step 2: Rotate SWA-FORWARDED-SECRET to force-invalidate all in-flight sessions.
# Any request in flight that passes the gate before the restart is harmless — the
# RLS predicate is already blocking the offending user via is_active = 0.
# Follow the full rotation procedure in docs/security/credentials_rotation.md §2.3.
NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
az keyvault secret set \
  --vault-name kv-tcp-prod-weu \
  --name SWA-FORWARDED-SECRET \
  --value "$NEW_SECRET"
azd deploy web
az functionapp restart \
  --resource-group rg-tcp-prod-weu \
  --name func-tcp-prod-weu
```

```kql
-- Step 3: Review SQL audit log for the past 30 days for the affected aad_object_id.
-- The SESSION_CONTEXT value is captured in the SQL audit event principal name.
AzureDiagnostics
| where ResourceType == "SERVERS/DATABASES"
| where Category == "SQLSecurityAuditEvents"
| where TimeGenerated > ago(30d)
| where action_name_s in ("SELECT", "EXECUTE")
| where session_server_principal_name_s contains "<offending-aad-object-id-last-8-chars>"
| project TimeGenerated, action_name_s, statement_s, succeeded_s
| order by TimeGenerated desc
```

**Step 4 — Post-mortem**: determine how the leak occurred. Possible root causes:
- `connection_for_user` did not call `sp_set_session_context` before the query (ADR-003 §4 contract violated).
- A pooled connection was reused with a stale SESSION_CONTEXT (check-in reset missing).
- The RLS predicate function was altered (check `schema_history` and SQL audit for DDL events).

File `docs/security/post_mortems/YYYYMMDD-rls-leak.md`.

---

### Scenario B — Compromised Anthropic API Key (P1)

**Symptom**: Anthropic console shows unexpected spend or API calls from unfamiliar IPs. Or `gitleaks` fires on a PR containing the key literal. Or a collaborator reports seeing the key in a shared channel.

```bash
# Step 1: Regenerate the key in the Anthropic console.
# Navigate to https://console.anthropic.com → API Keys → Revoke the old key.
# The old key is immediately invalid; no grace period.

# Step 2: Write the new key to Key Vault and restart.
az keyvault secret set \
  --vault-name kv-tcp-prod-weu \
  --name ANTHROPIC-API-KEY \
  --value "<new-key-from-anthropic-console>"

az functionapp restart \
  --resource-group rg-tcp-prod-weu \
  --name func-tcp-prod-weu
```

```kql
-- Step 3: Review App Insights for tcp.ask.* spikes in the past 7 days.
traces
| where timestamp > ago(7d)
| where message in ("tcp.func.ask.metrics", "tcp.func.ask.rate_limited")
| summarize count() by bin(timestamp, 1h), message, tostring(customDimensions.oid_suffix)
| order by timestamp desc
```

**Assessment**: if the spike is organic (many legitimate users), tighten the rate limit in `function_app/triggers/ask.py` (reduce `_RATE_LIMIT_MAX_REQUESTS` from 10 to a lower value) and redeploy. If the spike appears to originate from a single OID, revoke that user via `dim_UserRoles.is_active = 0`.

**Step 4 — Post-mortem**: file `docs/security/post_mortems/YYYYMMDD-anthropic-key-leak.md`. Check if the key appeared in an App Insights log event (which would indicate an `_redact`-style failure) or in a git commit (which would indicate a developer workflow error).

---

### Scenario C — Compromised OIDC SP / GitHub Actions Token (P0)

**Symptom**: Unexpected resource-group mutations visible in the Azure activity log. A third-party GitHub Actions action is found to have exfiltrated the OIDC token. `az activity-log list` shows API calls the operator did not make.

```bash
# Step 1: Immediately revoke ALL federated credentials for the app registration.
APP_ID="<AZURE_CLIENT_ID from GitHub repo variables>"
# List all federated credentials:
CRED_IDS=$(az ad app federated-credential list --id "$APP_ID" --query "[].id" -o tsv)
# Delete each:
for CRED_ID in $CRED_IDS; do
  az ad app federated-credential delete \
    --id "$APP_ID" \
    --federated-credential-id "$CRED_ID"
done
# After this, no new OIDC tokens can be minted for any GitHub Actions workflow.
# In-flight tokens expire within minutes (their TTL is set by the OIDC provider).
```

```bash
# Step 2: Rotate ALL other secrets as a precaution.
# A compromised CI runner may have had access to KV (via the OIDC SP's Secrets Officer role).
# Rotate in this order: ANTHROPIC-API-KEY, SWA-FORWARDED-SECRET, STORAGE-CONNECTION-STRING.
# Full procedures in docs/security/credentials_rotation.md §2.
```

```bash
# Step 3: Audit the activity log for the past 30 days.
az monitor activity-log list \
  --resource-group rg-tcp-prod-weu \
  --start-time $(date -d '30 days ago' +%Y-%m-%dT%H:%M:%SZ) \
  --query "[?caller=='<APP_ID>']" \
  --output table
# Look for: resource deletions, RBAC changes, KV secret reads, Function App updates.
```

```bash
# Step 4: Re-issue the federated credential on a clean app registration.
# Option A: create new federated credentials on the SAME app reg (if the app reg itself
#           was not modified).
az ad app federated-credential create \
  --id "$APP_ID" \
  --parameters '{
    "name": "gh-main",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:<owner>/<repo>:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'
# Repeat for gh-pr and gh-dev subjects as needed.

# Option B: if the app registration was modified by the attacker, create a new app reg
#           entirely and update AZURE_CLIENT_ID in the GitHub repo variables.
az ad app create --display-name sp-tcp-github-cicd-new
# Assign RBAC roles per docs/design/03_architecture.md §5.
# Update GitHub repo variable AZURE_CLIENT_ID to the new client ID.
```

**Step 5 — Post-mortem**: file `docs/security/post_mortems/YYYYMMDD-oidc-sp-compromise.md`. Determine the third-party action responsible and remove or upgrade it. Pin the replacement to a SHA. File a CVE report with the action's maintainer if appropriate.

---

### Scenario D — SQL Admin Password Leak (P1)

**Symptom**: `SQL-ADMIN-PASSWORD-EXPORT` found in a log, git commit, or shared channel. Or the Azure Management API BACPAC export is triggered from an unexpected IP visible in the activity log.

> Note: This password cannot be used for interactive SQL login because AAD-only authentication is active post-bootstrap. The risk is limited to control-plane BACPAC export operations. Nevertheless, rotate immediately.

```bash
# Step 1: Generate a new strong password.
NEW_PASSWORD=$(python3 -c "import secrets, string; \
  chars = string.ascii_letters + string.digits + '!@#'; \
  print(''.join(secrets.choice(chars) for _ in range(24)))")

# Step 2: Update the SQL Server admin password.
az sql server update \
  --resource-group rg-tcp-prod-weu \
  --name sql-tcp-prod-weu \
  --admin-password "$NEW_PASSWORD"

# Step 3: Write the new password to Key Vault.
az keyvault secret set \
  --vault-name kv-tcp-prod-weu \
  --name SQL-ADMIN-PASSWORD-EXPORT \
  --value "$NEW_PASSWORD"

# Step 4: Restart the Function App (BACPAC trigger reads the KV reference on next invocation).
az functionapp restart \
  --resource-group rg-tcp-prod-weu \
  --name func-tcp-prod-weu
```

```bash
# Step 5: Verify AAD-only authentication is still enabled.
az sql server ad-only-auth show \
  --resource-group rg-tcp-prod-weu \
  --name sql-tcp-prod-weu \
  --query azureAdOnlyAuthentication
# Must return: true
```

```bash
# Step 6: Review SQL audit log for unexpected Export operations in the past 30 days.
az monitor activity-log list \
  --resource /subscriptions/<sub>/resourceGroups/rg-tcp-prod-weu/providers/Microsoft.Sql/servers/sql-tcp-prod-weu \
  --start-time $(date -d '30 days ago' +%Y-%m-%dT%H:%M:%SZ) \
  --query "[?operationName.value=='Microsoft.Sql/servers/databases/export/action']" \
  --output table
```

**Step 7 — Post-mortem**: file `docs/security/post_mortems/YYYYMMDD-sql-admin-password-leak.md`.

---

### Scenario E — Function App Managed Identity Compromised (P0)

**Symptom**: An authenticated process outside the Function App is observed making KV reads or SQL queries using the MI's `aad_object_id`. This could appear as unexpected `SecretGet` entries in the KV audit log for calls not matching expected Function App execution patterns, or as SQL audit events at unexpected times.

```bash
# Step 1: Revoke the existing Managed Identity.
# WARNING: This immediately breaks the Function App (no more KV access, no SQL auth).
# Have the replacement steps ready before executing.
az functionapp identity remove \
  --resource-group rg-tcp-prod-weu \
  --name func-tcp-prod-weu \
  --identities "[system]"

# Step 2: Provision a new Managed Identity (system-assigned).
az functionapp identity assign \
  --resource-group rg-tcp-prod-weu \
  --name func-tcp-prod-weu
# Capture the new principal ID:
NEW_MI_PRINCIPAL_ID=$(az functionapp identity show \
  --resource-group rg-tcp-prod-weu \
  --name func-tcp-prod-weu \
  --query principalId \
  --output tsv)

# Step 3: Recreate all role assignments for the new MI.
# Key Vault Secrets User on kv-tcp-prod-weu:
KV_ID=$(az keyvault show --name kv-tcp-prod-weu --query id --output tsv)
az role assignment create \
  --assignee-object-id "$NEW_MI_PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Key Vault Secrets User" \
  --scope "$KV_ID"

# Storage Blob Data Contributor on bacpac-exports container:
STORAGE_CONTAINER_ID="/subscriptions/<sub>/resourceGroups/rg-tcp-prod-weu/providers/Microsoft.Storage/storageAccounts/sttcpprodweu/blobServices/default/containers/bacpac-exports"
az role assignment create \
  --assignee-object-id "$NEW_MI_PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" \
  --scope "$STORAGE_CONTAINER_ID"

# SQL DB Contributor on the database:
DB_ID=$(az sql db show \
  --resource-group rg-tcp-prod-weu \
  --server sql-tcp-prod-weu \
  --name sqldb-tcp-prod-weu \
  --query id \
  --output tsv)
az role assignment create \
  --assignee-object-id "$NEW_MI_PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "SQL DB Contributor" \
  --scope "$DB_ID"
```

```sql
-- Step 4: Update dim_UserRoles — mark the old MI as inactive and register the new one.
-- Connect to sqldb-tcp-prod-weu as an AAD admin.

-- Get the old MI's aad_object_id (from the revoked MI's former principal ID):
UPDATE dbo.dim_UserRoles
SET    is_active = 0
WHERE  aad_object_id = '<old-mi-aad-object-id>'
  AND  scope = 'admin'
  AND  employee_id IS NULL;

-- Register the new MI (scope='admin', employee_id=NULL per ADR-003 §5):
INSERT INTO dbo.dim_UserRoles (aad_object_id, employee_id, scope, is_active)
VALUES ('<new-mi-aad-object-id-from-step-2>', NULL, 'admin', 1);

-- Also recreate the contained SQL user for the new MI:
CREATE USER [func-tcp-prod-weu] FROM EXTERNAL PROVIDER;
ALTER ROLE tcp_ai_assistant ADD MEMBER [func-tcp-prod-weu];
ALTER ROLE tcp_generator    ADD MEMBER [func-tcp-prod-weu];
ALTER ROLE tcp_admin        ADD MEMBER [func-tcp-prod-weu];
```

```bash
# Step 5: Restart the Function App.
az functionapp restart \
  --resource-group rg-tcp-prod-weu \
  --name func-tcp-prod-weu
```

**Step 6 — Post-mortem**: file `docs/security/post_mortems/YYYYMMDD-mi-compromise.md`. Determine how the MI token was exfiltrated (memory dump, verbose logging, SSRF to IMDS). The IMDS endpoint `http://169.254.169.254` is reachable only from within the Azure compute context; exfiltration requires code execution inside the Function App process.

---

### Scenario F — Cost Spike — Anthropic Spend (P2)

**Symptom**: The Anthropic console shows a spend spike, or an App Insights alert fires on `tcp.func.ask.rate_limited` count exceeding a threshold.

```kql
-- Step 1: Examine the rate-limit events and identify the OID suffix responsible.
traces
| where timestamp > ago(24h)
| where message == "tcp.func.ask.rate_limited"
| summarize hit_count = count() by tostring(customDimensions.oid_suffix), bin(timestamp, 15m)
| order by hit_count desc
```

```kql
-- Correlate with actual ask events to confirm the volume:
traces
| where timestamp > ago(24h)
| where message == "tcp.func.ask.metrics"
| summarize request_count = count() by tostring(customDimensions.oid_suffix)
| order by request_count desc
```

```bash
# Step 2a — If the spike is organic (legitimate users at high volume), tighten the rate limit.
# Edit function_app/triggers/ask.py:
# - Reduce _RATE_LIMIT_MAX_REQUESTS from 10 to a lower value (e.g., 4).
# - Reduce _RATE_LIMIT_WINDOW_SECONDS from 60 to a tighter window if needed.
# Redeploy:
azd deploy func
```

```sql
-- Step 2b — If the spike is from a specific OID (abuse), revoke that user immediately.
UPDATE dbo.dim_UserRoles
SET    is_active = 0
WHERE  aad_object_id = '<full-aad-object-id-matching-the-8-char-suffix>';
```

```bash
# Step 3: Check the Anthropic dashboard for the usage trend and confirm spend is back to normal.
# https://console.anthropic.com → Usage → filter by time range.

# Step 4: If the spike was caused by a runaway loop in the client-side JS (unlikely given
# the rate limit), check the SWA access logs for excessive requests from a single session.
```

**Assessment**: the in-process rate limit (10/60 s per OID) bounds the maximum Anthropic spend per user per minute. For a worst case of 30 concurrent users each hitting the rate limit continuously, the spend cap per minute is `30 × 10 × cost_per_call`. With prompt caching at 90% hit rate and `max_output_tokens=600`, the per-call cost is approximately $0.001; the per-minute maximum is ~$0.30. A 24-hour runaway would cost ~$432 in the worst case — acceptable for an academic project to detect and contain within one business day.

---

## 4. Communication Template

### Internal incident notification (email / message to thesis advisor)

```
Subject: [TCP Platform] Security Incident — <Scenario> — <Date>

Severity: P<level>
Detected: <timestamp>
Scenario: <brief description>

Impact: <what was affected>
Containment status: <revoked / rotated / pending>
Recovery status: <operational / degraded / offline>

Actions taken so far:
1. ...
2. ...

Next steps:
- ...

Post-mortem ETA: <date>
```

### Post-Mortem Document Skeleton

Create at `docs/security/post_mortems/YYYYMMDD-<incident-slug>.md`:

```markdown
# Post-Mortem: <Title> — YYYY-MM-DD

**Severity**: P<level>
**Status**: <Resolved / In Progress>
**Author**: TODO (thesis author)

## Timeline

| Timestamp | Event |
|---|---|
| YYYY-MM-DD HH:MM | Incident detected |
| YYYY-MM-DD HH:MM | Containment started |
| YYYY-MM-DD HH:MM | Secret revoked |
| YYYY-MM-DD HH:MM | Service restored |

## Root Cause

...

## Blast Radius

...

## Actions Taken

...

## Preventive Measures

...

## Open Items

- [ ] ...
```

---

## 5. Recovery Validation Checklist

Run these checks after any P0 or P1 incident to confirm the system is fully healthy before considering the incident closed.

### 5.1 Authentication Layer

- [ ] `GET https://<swa-url>/api/ping` returns HTTP 200 with `{ status: "warm" | "resumed" }`.
- [ ] A direct `POST https://func-tcp-prod-weu.azurewebsites.net/api/ask` without `X-SWA-Forwarded` returns HTTP 403.
- [ ] An unauthenticated browser accessing `<swa-url>` is redirected to `/.auth/login/aad`.

### 5.2 Data Layer

- [ ] `POST <swa-url>/api/ask` with a valid AAD session and the question "How many traders are active today?" returns HTTP 200 with a non-empty result.
- [ ] A `trader`-scoped user cannot receive data from a different trader (spot-check: log the OID suffix and confirm rows returned match only that user's employee records).

### 5.3 Secrets

- [ ] `az keyvault secret show --vault-name kv-tcp-prod-weu --name ANTHROPIC-API-KEY` returns the new version timestamp (not the old one).
- [ ] `az keyvault secret show --vault-name kv-tcp-prod-weu --name SWA-FORWARDED-SECRET` returns the new version timestamp (if rotated).
- [ ] KV secret `SQL-ADMIN-PASSWORD-BOOTSTRAP` is absent: `az keyvault secret show --vault-name kv-tcp-prod-weu --name SQL-ADMIN-PASSWORD-BOOTSTRAP` returns a "SecretNotFound" error.

### 5.4 SQL Access Control

```bash
az sql server ad-only-auth show \
  --resource-group rg-tcp-prod-weu \
  --name sql-tcp-prod-weu \
  --query azureAdOnlyAuthentication
# Must return: true
```

### 5.5 Function App Health

- [ ] Function App status is `Running` in the Azure portal (`func-tcp-prod-weu` → Overview).
- [ ] All five triggers are listed as enabled: `TimerTrigger_DailyGenerator`, `WarmupTrigger`, `HttpTrigger_AskAssistant`, `TimerTrigger_BacpacExport`, `HttpTrigger_Ping`.

### 5.6 BACPAC Integrity (P0 / P1 involving data access)

- [ ] The most recent BACPAC blob exists in `bacpac-exports/` and is less than 8 days old (weekly schedule).

```bash
az storage blob list \
  --account-name sttcpprodweu \
  --container-name bacpac-exports \
  --auth-mode login \
  --query "sort_by([], &properties.lastModified)[-1]" \
  --output table
# Verify: last modified within the past 8 days.
```

---

## 6. Change History

| Version | Date | Author | Notes |
|---|---|---|---|
| 1.0 | 2026-05-16 | TODO | Initial version — Etapa 6 security hardening pass. |
