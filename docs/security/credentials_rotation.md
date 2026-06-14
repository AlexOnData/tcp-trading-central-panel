# Credentials Rotation — TCP Trading Central Panel

**Version**: 1.0
**Date**: 2026-05-16
**Status**: Active
**Audience**: Project operator (the thesis author acting as sole system administrator)

---

## 1. Overview

This document covers every secret the TCP system holds, where it lives, how to rotate it, and the blast radius of exposure. It also provides a Year-1 rotation schedule and a break-glass playbook for compromised secrets.

Rotation here means replacing the secret value with a newly generated one and propagating it to all consumers without service interruption beyond the documented downtime windows. For secrets with zero documented consumers (e.g., the bootstrap SQL admin password that was deleted post-flip), rotation means re-issuance only if the AAD-only authentication is temporarily disabled.

All secret storage is in `kv-tcp-prod-weu` (Azure Key Vault, Standard SKU, RBAC mode). No secret appears in plaintext in code, application settings, or git history.

---

## 2. Secret Inventory and Rotation Procedures

### 2.1 `ANTHROPIC_API_KEY`

| Property | Value |
|---|---|
| **KV secret name** | `ANTHROPIC-API-KEY` |
| **Function App setting** | `ANTHROPIC_API_KEY` (KV reference: `@Microsoft.KeyVault(SecretUri=https://kv-tcp-prod-weu.vault.azure.net/secrets/ANTHROPIC-API-KEY/)`) |
| **Rotation cadence** | Annual, or immediately on incident (suspected leak or anomalous Anthropic dashboard spend). |
| **Impact of exposure** | Every API call is billed to the Anthropic account. A leaked key allows an attacker to run up charges before detection. No Azure resource access is granted by this key. |
| **Impact of rotation downtime** | Approximately 30 seconds: the Function App must be restarted so the KV reference resolves the new secret value. During the restart window, `/api/ask` returns 503. |
| **Verification** | Issue a `POST /api/ask` with a valid question and confirm HTTP 200 with a non-empty `answer` field. |

**Rotation procedure:**

```bash
# Step 1 — Regenerate the key in the Anthropic console.
# Navigate to https://console.anthropic.com → API Keys → Revoke old → Create new.
# Copy the new key value (it is shown only once).

# Step 2 — Write the new value to Key Vault.
az keyvault secret set \
  --vault-name kv-tcp-prod-weu \
  --name ANTHROPIC-API-KEY \
  --value "<new-key-value>"

# Step 3 — Restart the Function App to force KV reference resolution.
az functionapp restart \
  --resource-group rg-tcp-prod-weu \
  --name func-tcp-prod-weu

# Step 4 — Verify.
curl -X POST https://<swa-url>/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "How many traders are active today?"}' \
  --cookie ".auth_cookie=<valid-session-cookie>"
# Expect: HTTP 200 with a valid answer envelope.

# Step 5 — Confirm the old key is no longer accepted by Anthropic
# (done automatically when you revoke it in the console in Step 1).
```

---

### 2.2 `SQL-ADMIN-PASSWORD-EXPORT`

| Property | Value |
|---|---|
| **KV secret name** | `SQL-ADMIN-PASSWORD-EXPORT` |
| **Function App setting** | Not bound as a setting; read via KV reference inside `TimerTrigger_BacpacExport` as `BacpacConfig.sql_admin_password` (`SecretStr`). |
| **Rotation cadence** | Annual. The Bicep `newGuid()` parameter regenerates the value automatically if the parameter is left empty on a full re-provision. For in-place rotation (without re-provisioning), follow the procedure below. |
| **Impact of exposure** | The password is accepted only by the Azure Management API `Export` action (control-plane). SQL Server has AAD-only authentication enabled post-bootstrap, so the password cannot be used for interactive SQL login. Exposure allows an attacker to trigger a BACPAC export to a storage URI they control, exfiltrating the synthetic dataset. |
| **Impact of rotation downtime** | Zero runtime downtime. The BACPAC trigger reads the new KV value at its next invocation (Sunday 08:00 RO). If rotation happens mid-export-poll, the current export operation continues using the cached `BacpacConfig` object (loaded at trigger start); the new value takes effect from the next trigger fire. |
| **Verification** | Wait for the next Sunday 08:00 RO trigger and confirm a `tcp.bacpac.status='succeeded'` event appears in App Insights within 30 minutes. Alternatively, trigger the function manually via the Azure portal and verify the same event. |

**Rotation procedure:**

```bash
# Step 1 — Generate a new strong password (at least 16 chars, upper+lower+digit+special).
NEW_PASSWORD=$(python3 -c "import secrets, string; \
  chars = string.ascii_letters + string.digits + '!@#'; \
  print(''.join(secrets.choice(chars) for _ in range(24)))")

# Step 2 — Update the SQL Server admin password via Azure CLI.
az sql server update \
  --resource-group rg-tcp-prod-weu \
  --name sql-tcp-prod-weu \
  --admin-password "$NEW_PASSWORD"

# Step 3 — Write the new password to Key Vault.
az keyvault secret set \
  --vault-name kv-tcp-prod-weu \
  --name SQL-ADMIN-PASSWORD-EXPORT \
  --value "$NEW_PASSWORD"

# Step 4 — Verify.
# Trigger BacpacExport manually (portal: Function App → Functions → TimerTrigger_BacpacExport → Test/Run)
# or wait for the next Sunday slot.
# Check App Insights for: tcp.bacpac.status='succeeded'
```

---

### 2.3 `SWA-FORWARDED-SECRET`

| Property | Value |
|---|---|
| **KV secret name** | `SWA-FORWARDED-SECRET` |
| **Function App setting** | Not a Function App setting; injected by SWA platform as `X-SWA-Forwarded` header via `staticwebapp.config.json` `forwardingGateway.requiredHeaders`. The Function App reads the expected value from `SWA_FORWARDED_SECRET` environment variable (set by postprovision Step 2c via KV reference). |
| **Rotation cadence** | Annual, or immediately if the shared secret is suspected to be known to a third party. |
| **Impact of exposure** | An attacker who knows this secret can forge requests directly to the raw Function App URL (`func-tcp-prod-weu.azurewebsites.net/api/ask`), bypassing the SWA AAD gate. They would still need a valid AAD principal (registered in `dim_UserRoles`) to receive non-403 responses, but the AAD gate is the primary authentication layer. |
| **Impact of rotation downtime** | Approximately 60–90 seconds total. After the new secret is written to KV and propagated to the Function App (restart), existing SWA clients with a stale `forwardingGateway` config will receive 403 until the SWA deployment propagates. In practice, SWA reloads the config during re-deployment; the window is bounded by the `azd deploy web` duration. |
| **Verification** | Issue a `POST /api/ask` via the SWA URL (not the raw Function URL) and confirm HTTP 200. Then confirm that a direct `POST` to `https://func-tcp-prod-weu.azurewebsites.net/api/ask` without the `X-SWA-Forwarded` header returns 403. |

**Rotation procedure:**

```bash
# Step 1 — Generate a new secret (at least 32 random hex chars).
NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Step 2 — Write the new secret to Key Vault.
az keyvault secret set \
  --vault-name kv-tcp-prod-weu \
  --name SWA-FORWARDED-SECRET \
  --value "$NEW_SECRET"

# Step 3 — Re-run postprovision Step 2c to substitute the new value into
# swa/staticwebapp.config.json (the forwardingGateway.requiredHeaders block).
# This is the same substitution performed by infra/scripts/postprovision.ps1.
# If you have the postprovision script available:
pwsh infra/scripts/postprovision.ps1 --step 2c

# Step 4 — Redeploy the SWA to pick up the updated staticwebapp.config.json.
azd deploy web

# Step 5 — Restart the Function App so the SWA_FORWARDED_SECRET env var
# (bound via KV reference) resolves the new value.
az functionapp restart \
  --resource-group rg-tcp-prod-weu \
  --name func-tcp-prod-weu

# Step 6 — Verify (see above).
```

---

### 2.4 `STORAGE-CONNECTION-STRING` (Storage Account Key)

| Property | Value |
|---|---|
| **KV secret name** | `STORAGE-CONNECTION-STRING` + paired bare-key secret `STORAGE-ACCOUNT-KEY` (introduced in Etapa 10 — both surface the same underlying storage `key1`; rotating `key1` invalidates both at once, and the procedure below refreshes both KV secrets together). |
| **Function App setting** | `AzureWebJobsStorage` (KV reference to `STORAGE-CONNECTION-STRING`) + `STORAGE_ACCOUNT_KEY` (KV reference to `STORAGE-ACCOUNT-KEY`; consumed by the BACPAC Export trigger per ADR-004). |
| **Rotation cadence** | Annual, or on incident (suspected key exfiltration). Regeneration rotates `key1`; `key2` is available as a zero-downtime swap option. |
| **Impact of exposure** | Account-key access to the full storage account, including `azure-webjobs-hosts` and `azure-webjobs-secrets` containers (which hold the Function App's own host keys). An attacker with the account key can read or overwrite Function secrets. This is residual risk RR-01; the key is stored only in KV and is never in plaintext application settings. |
| **Impact of rotation downtime** | Approximately 30 seconds. The Function App must be restarted to resolve the updated KV reference. During restart, all triggers (timer and HTTP) are unavailable. |
| **Verification** | Confirm the Function App status is `Running` in the Azure portal after restart, and that `GET /api/ping` returns HTTP 200. |

**Rotation procedure:**

```bash
# Step 1 — Regenerate storage account key1.
# (key2 remains valid during the window, providing zero-downtime if both are used — but
# the Functions runtime uses only the connection string, so there is a brief unavailability
# window during the Functions restart in Step 4.)
az storage account keys renew \
  --account-name sttcpprodweu \
  --resource-group rg-tcp-prod-weu \
  --key key1

# Step 2 — Retrieve the new connection string.
NEW_CONN_STR=$(az storage account show-connection-string \
  --name sttcpprodweu \
  --resource-group rg-tcp-prod-weu \
  --query connectionString \
  --output tsv)

# Step 3 — Write the new connection string to Key Vault.
az keyvault secret set \
  --vault-name kv-tcp-prod-weu \
  --name STORAGE-CONNECTION-STRING \
  --value "$NEW_CONN_STR"

# Step 3b — Refresh the paired bare-key secret consumed by bacpac_export.py
# (Etapa-10 fix: STORAGE-ACCOUNT-KEY is a sibling to STORAGE-CONNECTION-STRING
# and rotates on the same cadence; both surface the same underlying key1).
NEW_KEY=$(az storage account keys list \
  --account-name sttcpprodweu \
  --resource-group rg-tcp-prod-weu \
  --query "[?keyName=='key1'].value" -o tsv)
az keyvault secret set \
  --vault-name kv-tcp-prod-weu \
  --name STORAGE-ACCOUNT-KEY \
  --value "$NEW_KEY"

# Step 4 — Restart the Function App.
az functionapp restart \
  --resource-group rg-tcp-prod-weu \
  --name func-tcp-prod-weu

# Step 5 — Verify.
# GET https://<swa-url>/api/ping → expect HTTP 200 { status: "warm" | "resumed" }
```

---

### 2.5 OIDC Federated Credentials (GitHub Actions SP)

| Property | Value |
|---|---|
| **Azure resource** | Entra ID App Registration `sp-tcp-github-cicd` |
| **Rotation cadence** | Not applicable in the traditional sense. OIDC federated credentials have no secret to rotate — the trust is established by the `subject` claim, not by a stored credential. |
| **When rotation is needed** | If the GitHub repository is renamed, transferred, or the branch/environment model changes (e.g., the `main` branch is renamed), the federated credential's `subject` claim must be updated. If the Entra ID tenant is compromised, the app registration must be re-created. |
| **Impact of exposure** | An OIDC token is short-lived (minutes). There is no static secret to leak. A compromised token expires on its own. The risk is a malicious workflow run, not a long-lived credential compromise. |
| **Verification** | After updating the federated credential, trigger the `ci.yml` or `cd.yml` workflow on the affected branch and confirm `azd auth login` succeeds with "Logging in using OIDC token". |

**Procedure to update the federated credential subject claim:**

```bash
# Step 1 — List existing federated credentials for the app registration.
APP_ID="<AZURE_CLIENT_ID from GitHub repo variables>"
az ad app federated-credential list --id "$APP_ID"

# Step 2 — Delete the stale credential.
CREDENTIAL_ID="<id from the list output>"
az ad app federated-credential delete \
  --id "$APP_ID" \
  --federated-credential-id "$CREDENTIAL_ID"

# Step 3 — Re-create with the new subject claim.
az ad app federated-credential create \
  --id "$APP_ID" \
  --parameters '{
    "name": "gh-main",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:<new-owner>/<new-repo-name>:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

---

### 2.6 Bootstrap SQL Admin Password (Post-Flip)

| Property | Value |
|---|---|
| **KV secret name** | `SQL-ADMIN-PASSWORD-BOOTSTRAP` — **deleted** after postprovision Step 3 (AAD-only flip). |
| **Rotation cadence** | Not rotatable in the traditional sense. The secret is single-use: it is generated by Bicep `newGuid()` at provision time, used once for the initial schema apply, and deleted from KV by `postprovision.ps1`. |
| **When re-issuance is needed** | Only if the SQL Server must temporarily have SQL authentication re-enabled (e.g., a catastrophic AAD tenant incident that prevents MI authentication). In that case, re-provision the secret via `az sql server update --admin-password <new>` and re-run the schema apply, then immediately re-flip AAD-only auth. |

This secret is not part of the ongoing rotation schedule. Its lifecycle ends at the first successful postprovision run.

---

### 2.7 PowerBI Service Principal (`tcp-powerbi-sp`)

| Property | Value |
|---|---|
| **Azure resource** | Entra ID App Registration `tcp-powerbi-sp` (registered in `../runbooks/powerbi_deploy.md` §4). |
| **KV secret name** | `POWERBI-SP-CLIENT-SECRET` — present only when the federated-credential path (`../runbooks/powerbi_deploy.md` §4.3) is not used and the operator fell back to the client-secret path (§4.3b). The federated path has nothing to rotate. |
| **Function App setting** | Not bound to the Function App. Consumed exclusively by `powerbi/deploy.ps1` via the `POWERBI_CLIENT_SECRET` environment variable (or, in CI, via OIDC federated credential — preferred). |
| **Rotation cadence** | **Federated credential**: re-issued annually (the audit confirms the `subject` claim still matches the repo and branch). No secret to rotate; the trust is established by the OIDC claim, not by a stored value. **Client secret (fallback)**: annual, or immediately on incident. The `--years 1` flag on `az ad app credential reset` issues a one-year secret. |
| **Impact of exposure** | The SP has `Dataset.ReadWrite.All`, `Report.ReadWrite.All`, `Workspace.ReadWrite.All` on the PowerBI tenant and is a `tcp_bi_reader` member in SQL. An attacker with the client secret can read every published dataset, overwrite the report, trigger refreshes, and read every view exposed through `tcp_bi_reader` (no write access to base tables). The SP cannot reach Azure resources outside the PowerBI tenant — it has no Azure RBAC role on the resource group. |
| **Impact of rotation downtime** | **Federated**: zero downtime; the GitHub Action exchanges a fresh OIDC token on every run. **Client secret**: approximately 5 minutes — the duration of `pwsh -File powerbi/deploy.ps1` after setting the new value in the deploy shell. Scheduled refreshes that overlap the rotation window will fail once; the next 07:30 RO slot picks up the new secret automatically. |
| **Verification** | After rotation, re-run `pwsh -File powerbi/deploy.ps1`. Step 0c (`Get-PowerBIToken`) succeeds → token acquisition works. Step 5 (immediate refresh) succeeds → credential binding still works. |

**Rotation procedure (federated credential — preferred):**

```bash
# Step 1 — Locate the existing federated credential id.
APP_ID="<tcp-powerbi-sp appId from powerbi_deploy.md §4.1>"
az ad app federated-credential list --id "$APP_ID"

# Step 2 — If the subject claim (repo / branch) is still correct, re-issue is not
# required. Annual hygiene step: delete and recreate the credential with the same
# subject to confirm the trust chain still works end-to-end.
CRED_ID="<id from list output>"
az ad app federated-credential delete --id "$APP_ID" --federated-credential-id "$CRED_ID"
az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "github-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<owner>/<repo>:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'

# Step 3 — Verify by re-running the deploy via CI (the GitHub Actions workflow
# trades a fresh OIDC token on every run; a successful run confirms the trust).
```

**Rotation procedure (client secret — fallback):**

```bash
# Step 1 — Regenerate the client secret (revokes the old value).
APP_ID="<tcp-powerbi-sp appId>"
NEW_SECRET=$(az ad app credential reset --id "$APP_ID" --years 1 \
  --query password --output tsv)

# Step 2 — Write the new secret to Key Vault.
az keyvault secret set \
  --vault-name kv-tcp-prod-weu \
  --name POWERBI-SP-CLIENT-SECRET \
  --value "$NEW_SECRET"

# Step 3 — Re-run the deploy with the new secret in the shell environment so the
# next scheduled refresh has a valid credential pre-bound.
export POWERBI_CLIENT_SECRET="$NEW_SECRET"
pwsh -File powerbi/deploy.ps1

# Step 4 — Verify: open PowerBI Service → workspace → Datasets → Refresh history.
# The Step 5 refresh enqueued by deploy.ps1 must show "Completed".
```

**Compromised-credential playbook addition.** If the PowerBI SP is suspected to be compromised:

1. Run Step 1 above to revoke (federated) or Step 1 of the client-secret procedure (revokes the old secret).
2. Re-run the deploy. Step 1 of the script (workspace bootstrap) re-grants the SP Admin if needed.
3. Re-register the SP in `dbo.dim_UserRoles` only if the AAD object id changed (it does not change on credential reset — the appId is stable). On full app re-creation, `INSERT` a new admin row per `../runbooks/powerbi_deploy.md` §4.6 and `DELETE` the old row.
4. Review the PowerBI activity log (PowerBI Admin Portal → Audit logs) for the last 30 days, filtered by `UserId == <tcp-powerbi-sp appId>`. Look for unexpected workspace or dataset operations.

---

## 3. Year-1 Rotation Schedule

| Quarter | Date (target) | Action |
|---|---|---|
| Q1 (Jan–Mar 2027) | 2027-01-15 | Review — no rotation action expected. Confirm secrets are still in KV and not in git history. Run `gitleaks detect --source .` locally. |
| Q2 (Apr–Jun 2027) | 2027-04-15 | Review — confirm Anthropic dashboard shows no anomalous spend. Check App Insights `tcp.func.ask.rate_limited` trend. No rotation unless triggered by incident. |
| Q3 (Jul–Sep 2027) | 2027-07-15 | **Full rotation drill (recommended)**. Rotate `ANTHROPIC_API_KEY`, `SWA-FORWARDED-SECRET`, and `STORAGE-CONNECTION-STRING` using the procedures in section 2. Re-issue the PowerBI SP federated credential (§2.7) or rotate `POWERBI-SP-CLIENT-SECRET` if the fallback path is in use. Test the BACPAC export by triggering it manually on the same day. |
| Q4 (Oct–Dec 2027) | 2027-10-15 | Rotation drill or after-incident rotation. Rotate `SQL-ADMIN-PASSWORD-EXPORT` to complete the annual cycle for that credential. If the PowerBI SP fallback (§2.7) is in use with a client-secret, also rotate `POWERBI-SP-CLIENT-SECRET` on this date so the annual cadence applies to it as well (sec10-MN-01). Also rotate the new `STORAGE-ACCOUNT-KEY` (introduced in Etapa 10 for the BACPAC Export path) — same cadence as `STORAGE-CONNECTION-STRING`. |

> After-thesis note: if the project is archived (no active traffic), the Year-1 schedule still applies to prevent silent credential expiry. An archived project should have all secrets rotated once before archiving and the Anthropic key revoked entirely if no AI calls are expected.

---

## 4. Lost or Compromised Secret Playbook

Use this playbook when a secret is suspected to have been exposed (e.g., found in a public commit, exfiltrated from an App Insights log, or reported by a third party).

### Step 1 — Scope Assessment

Before rotating, determine the blast radius:

- Which secret was exposed?
- How was it exposed? (git commit, log, screenshot, email)
- When was it first available in the exposure channel? (git blame, commit timestamp)
- Who could have accessed it? (public repo: anyone; App Insights: anyone with App Insights Reader role)
- What actions can an attacker take with this secret? (see section 2 Impact rows)

Document the answers in a post-mortem skeleton (see section 5).

### Step 2 — Revoke

Revoke the secret immediately in its authoritative source. Do not wait for an impact assessment — revoke first, investigate second.

```bash
# For ANTHROPIC_API_KEY: revoke in the Anthropic console (Key → Revoke).
# The key is invalidated immediately for all callers.

# For STORAGE-CONNECTION-STRING: regenerate the storage key (this revokes the old key).
az storage account keys renew \
  --account-name sttcpprodweu \
  --resource-group rg-tcp-prod-weu \
  --key key1

# For SWA-FORWARDED-SECRET or SQL-ADMIN-PASSWORD-EXPORT:
az keyvault secret set \
  --vault-name kv-tcp-prod-weu \
  --name <SECRET-NAME> \
  --value "<new-generated-value>"
# The old value remains in KV version history but is no longer the current version.
```

### Step 3 — Rotate Downstream Consumers

After revoking the secret at source, propagate the new value:

```bash
# For ANTHROPIC_API_KEY, SWA-FORWARDED-SECRET, or STORAGE-CONNECTION-STRING:
az functionapp restart \
  --resource-group rg-tcp-prod-weu \
  --name func-tcp-prod-weu
# KV references resolve on restart.

# For SWA-FORWARDED-SECRET additionally: re-deploy SWA.
azd deploy web
```

### Step 4 — Verify Smoke Tests

Confirm the system is healthy after rotation:

```bash
# 1. Ping endpoint (no auth required):
curl https://<swa-url>/api/ping
# Expect: HTTP 200 { status: "warm" | "resumed", sql_resume_ms: <int> }

# 2. Ask endpoint (requires valid AAD session in browser):
# Open the SWA URL in a browser, authenticate, and ask "How many traders are active?"
# Expect: HTTP 200 with a valid answer table.

# 3. Confirm old secret is rejected (for SWA-FORWARDED-SECRET):
curl -X POST https://func-tcp-prod-weu.azurewebsites.net/api/ask \
  -H "X-SWA-Forwarded: <old-secret-value>" \
  -H "Content-Type: application/json" \
  -d '{"question": "test"}'
# Expect: HTTP 403 (old secret rejected).
```

### Step 5 — Audit Log Review

Review the last 30 days of activity for the affected secret's consumers:

```kql
-- App Insights KQL — all ask requests by OID suffix (last 30 days)
traces
| where timestamp > ago(30d)
| where message == "tcp.func.ask.metrics"
| project timestamp, customDimensions.oid_suffix, customDimensions.status
| order by timestamp desc
```

```bash
# Key Vault audit log (last 30 days for the affected secret):
az monitor activity-log list \
  --resource /subscriptions/<sub>/resourceGroups/rg-tcp-prod-weu/providers/Microsoft.KeyVault/vaults/kv-tcp-prod-weu \
  --start-time $(date -d '30 days ago' +%Y-%m-%dT%H:%M:%SZ) \
  --query "[?operationName.value=='Microsoft.KeyVault/vaults/secrets/read']" \
  --output table
```

### Step 6 — Post-Mortem Document

Create `docs/security/post_mortems/YYYYMMDD-<secret-name>-exposure.md` with:

```markdown
# Post-Mortem: <Secret Name> Exposure — YYYY-MM-DD

## Timeline
- Discovery: ...
- Revocation: ...
- Consumer rotation: ...
- Verification: ...

## Root Cause
...

## Blast Radius Assessment
...

## Actions Taken
...

## Preventive Measures
...
```

---

## 5. Change History

| Version | Date | Author | Notes |
|---|---|---|---|
| 1.0 | 2026-05-16 | TODO | Initial version — Etapa 6 security hardening pass. |
