# PowerBI Deploy — Operator Runbook

| Field | Value |
|---|---|
| **Version** | 1.0 |
| **Date** | 2026-05-16 |
| **Audience** | Deploy operator (the developer running `azd up` for Etapa 7) |
| **Estimated time** | ~30–45 minutes (first deploy); ~5 minutes (re-deploys) |
| **Cross-references** | `../decisions/ADR-001-powerbi-deployment.md`, `../decisions/ADR-003-rls-session-context.md`, `../design/03_architecture.md` §3.3 §4.2 §5, `../security/credentials_rotation.md` |

---

## 1. Overview

This runbook guides the operator through publishing the TCP semantic model and report to the PowerBI Service workspace `TCP — Trading Central Panel`. Running `pwsh -File powerbi/deploy.ps1` executes nine numbered phases (Step 0 .. Step 8) — preflight checks, workspace creation or resolution, TMDL model compilation, dataset import, ownership take-over and dataset-parameter binding to `sqldb-tcp-prod-weu`, an immediate end-to-end refresh, scheduled-refresh configuration (Mon–Fri 07:30 Europe/Bucharest), report publication with the AI Assistant hyperlink substitution, and a smoke-verification against the live refresh API. The dataset uses Import mode (not DirectQuery) so that the auto-paused Azure SQL Serverless instance is woken only by the 07:00 timer trigger, not by every visual render — see `../design/03_architecture.md` §3.3 for the rationale.

---

## 2. Prerequisites

Complete all items before running the deploy script. Items marked **(one-time)** are skipped on re-deploys.

1. **Azure SQL Database deployed** — `azd up` (Etapa 4) completed without error. The database `sqldb-tcp-prod-weu` is reachable.
2. **V001 + V002 migrations applied** — the Etapa 4 post-provision script (`infra/scripts/postprovision.ps1`) ran successfully. Both migration scripts are idempotent; re-running them on a clean DB is safe.
3. **`tcp_bi_reader` role exists** — created by V001 (`CREATE ROLE tcp_bi_reader`). Verify with:
   ```bash
   sqlcmd -S sql-tcp-prod-weu.database.windows.net -d sqldb-tcp-prod-weu -G \
     -Q "SELECT name FROM sys.database_principals WHERE type='R' AND name='tcp_bi_reader';"
   ```
   Expect one row. If empty, re-run V001 via `infra/scripts/postprovision.ps1 --step 0`.
4. **PowerBI Service tenant available** — any `.edu` or paid license. PowerBI Free (personal) is sufficient for a single workspace with one dataset + one report; the Pro license is not required unless you need scheduled refresh from the REST API with a service principal (see §4 below).
5. **PowerBI service-principal registered** **(one-time)** — see §4 for the exact steps. The SP's `appId` and `objectId` are needed for §5.
6. **Local tooling installed**:
   - **PowerShell 7+ required** — verify: `pwsh --version`. The deploy script uses
     `Invoke-RestMethod -Form` for multipart uploads, which is PS7-only; launching the
     script with Windows PowerShell 5.1 will fail opaquely. The script has a
     `#Requires -Version 7.0` directive at line 1 that aborts early on the wrong host.
   - Azure CLI 2.50+ — verify: `az --version`
   - `pbi-tools` 1.x (recommended) — verify: `pbi-tools info`. Install if missing:
     ```bash
     dotnet tool install --global pbi-tools
     ```
     The script invokes `pbi-tools compile <project-dir> -outPath <bim-or-pbix>` — the
     `pbi-tools 1.x` verb (`compile-model` / `compile-report` are not real verbs).
     Alternative: Tabular Editor 3 (commercial; 30-day free trial) or a manual PowerBI
     Desktop compile (§8 describes the manual path as a fallback). If neither
     `pbi-tools` nor `TabularEditor.exe` is on PATH, the script aborts with the
     message: "TMDL compile requires pbi-tools 1.x (`dotnet tool install --global pbi-tools`) OR Tabular Editor 3 (`TabularEditor.exe`). Neither was found on PATH."
7. **SQL AAD admin identity available** — the developer running this runbook must be the SQL AAD admin. Bicep set this to the OIDC service principal during Etapa 4 provisioning (`az sql server ad-admin create --object-id <oidc-sp-oid>`). Confirm:
   ```bash
   az sql server ad-admin show \
     --resource-group rg-tcp-prod-weu \
     --server sql-tcp-prod-weu \
     --query login
   ```

---

## 3. Architecture context

The following diagram describes the identity path this runbook establishes:

```
PowerBI Service
  └─ Workspace "TCP — Trading Central Panel"
       ├─ Dataset (Import mode, TMDL source)
       │    └─ Scheduled Refresh (07:30 RO, Mon–Fri)
       │         └─ Connects as tcp-powerbi-sp
       │              └─ tcp_bi_reader role in sqldb-tcp-prod-weu
       │                   └─ SELECT on v_* views only
       └─ Report (PBIR skeleton, 4 pages)
```

The service principal `tcp-powerbi-sp` is also registered in `dbo.dim_UserRoles` with `scope='admin'` (ADR-003 §6). This means the RLS predicate returns TRUE for all rows when the SP performs a refresh, so the dataset snapshot contains the full fact table unfiltered — that is the correct behaviour for a BI import.

---

## 4. PowerBI service-principal registration (one-time setup)

Run all commands in a shell authenticated with `az login` as a user who holds:
- **Azure AD Application Administrator** (to create the app registration), and
- **PowerBI tenant administrator** (for step 4.5 — can be delegated to an IT admin if needed).

```bash
# Step 4.1 — Create the app registration.
az ad app create \
  --display-name "tcp-powerbi-sp" \
  --sign-in-audience AzureADMyOrg \
  --query "{appId:appId, objectId:id}" -o json
# Capture appId and objectId from the output. They are needed in steps 4.2–4.7 and §5.

# Step 4.2 — Create the service principal object.
az ad sp create --id <appId>

# Step 4.3 — Add a federated credential (preferred over client secret; no secret to rotate).
# Replace <owner> and <repo> with the actual GitHub org/repository.
az ad app federated-credential create --id <appId> --parameters '{
  "name": "github-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<owner>/<repo>:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'
```

If OIDC is not available in your GitHub plan, use a client secret instead.
Store it in Key Vault under the name `POWERBI-SP-CLIENT-SECRET` and
reference it from the deploy script via `az keyvault secret show`.
Rotate annually per `../security/credentials_rotation.md` §2.7.

```bash
# Step 4.3b (alternative to federated) — Client secret. Less preferred; rotate annually.
az ad app credential reset --id <appId> --years 1
# Capture the displayed password immediately — it is shown only once.
```

```bash
# Step 4.4 — Grant PowerBI API application permissions.
# The GUIDs below are the well-known permission IDs for the Power BI Service API
# (resource appId 00000009-0000-0000-c000-000000000000).
# Dataset.ReadWrite.All  = 7504609f-c495-4c64-8542-686125a5a36f
# Report.ReadWrite.All   = b2f1b2fa-f35c-407b-a09b-d9ba5a4cd9ce
# Workspace.ReadWrite.All= 9f5b31a5-2ab4-4b3b-9e0d-1baae9aa8c1a
az ad app permission add \
  --id <appId> \
  --api 00000009-0000-0000-c000-000000000000 \
  --api-permissions \
    7504609f-c495-4c64-8542-686125a5a36f=Role \
    b2f1b2fa-f35c-407b-a09b-d9ba5a4cd9ce=Role \
    9f5b31a5-2ab4-4b3b-9e0d-1baae9aa8c1a=Role

# Admin consent must be granted by a tenant administrator.
az ad app permission admin-consent --id <appId>
```

> **Step 4.5 — PowerBI tenant setting (portal only).**
> Navigate to: PowerBI Admin Portal → Tenant settings → Developer settings →
> "Allow service principals to use Power BI APIs" → Enable → restrict to a security
> group that contains `tcp-powerbi-sp`. This setting has no CLI equivalent and requires
> a PowerBI administrator to act. Without it, all REST API calls from the SP return HTTP 403
> even with correct AAD permissions (see §10 troubleshooting entry).

```bash
# Step 4.6 — Register the SP in dim_UserRoles with scope='admin'.
# This satisfies ADR-003 §6: the RLS predicate returns TRUE for all rows when the
# SP refreshes the dataset, so the import snapshot is complete.
# Replace <sp-objectId> with the objectId captured in step 4.1.
sqlcmd -S sql-tcp-prod-weu.database.windows.net \
       -d sqldb-tcp-prod-weu \
       -G \
       -Q "
  INSERT INTO dbo.dim_UserRoles (aad_object_id, employee_id, scope, is_active, created_at)
  VALUES (
    CAST('<sp-objectId>' AS UNIQUEIDENTIFIER),
    NULL,
    'admin',
    1,
    SYSDATETIMEOFFSET()
  );
"
# employee_id is NULL for service-principal rows — this is intentional.
# The schema CHECK constraint CK_dim_UserRoles_scope_employee allows NULL employee_id
# only for scope='admin', so the constraint is satisfied.

# Step 4.7 — Create a SQL contained user for the SP and grant tcp_bi_reader.
# The SP authenticates to SQL via AAD token (no password), so "FROM EXTERNAL PROVIDER"
# is the correct syntax — it maps the AAD object to a SQL principal without a password.
sqlcmd -S sql-tcp-prod-weu.database.windows.net \
       -d sqldb-tcp-prod-weu \
       -G \
       -Q "
  CREATE USER [tcp-powerbi-sp] FROM EXTERNAL PROVIDER;
  ALTER ROLE tcp_bi_reader ADD MEMBER [tcp-powerbi-sp];
"
```

Steps 4.4 + 4.5 require a **tenant administrator**. Steps 4.6 + 4.7 require the **SQL AAD admin** (the developer running this runbook, or the OIDC CI service principal).

---

## 5. Setting environment variables

These variables are consumed by `powerbi/deploy.ps1`. Set them in the shell session before running the script. Do not commit them — they include the SP credentials.

**Bash / Linux / macOS:**

```bash
export POWERBI_TENANT_ID="<tenant-id>"
export POWERBI_CLIENT_ID="<sp-appId>"
export POWERBI_CLIENT_SECRET="<password>"        # omit if using federated credential
export POWERBI_WORKSPACE_NAME="TCP — Trading Central Panel"
export AZURE_SQL_SERVER_FQDN="sql-tcp-prod-weu.database.windows.net"
export AZURE_SQL_DATABASE_NAME="sqldb-tcp-prod-weu"
```

Retrieve the `AZURE_SQL_*` values plus `AZURE_STATIC_WEB_APP_HOSTNAME` (consumed by Step 7 of the deploy to substitute the AI Assistant page hyperlink) from `azd`:

```bash
azd env get-values | grep -E "AZURE_(SQL_(SERVER|DATABASE)|STATIC_WEB_APP_HOSTNAME)"
```

**PowerShell (Windows):**

```powershell
$env:POWERBI_TENANT_ID         = "<tenant-id>"
$env:POWERBI_CLIENT_ID         = "<sp-appId>"
$env:POWERBI_CLIENT_SECRET     = "<password>"
$env:POWERBI_WORKSPACE_NAME    = "TCP — Trading Central Panel"
$env:AZURE_SQL_SERVER_FQDN     = "sql-tcp-prod-weu.database.windows.net"
$env:AZURE_SQL_DATABASE_NAME   = "sqldb-tcp-prod-weu"
```

Retrieve from `azd` on Windows:

```powershell
azd env get-values | Select-String "AZURE_(SQL|STATIC_WEB_APP_HOSTNAME)"
```

> If using a federated credential (step 4.3), the deploy script obtains a token via
> `az account get-access-token --resource https://analysis.windows.net/powerbi/api`
> rather than exchanging a client secret. In that case, `POWERBI_CLIENT_SECRET` can be
> omitted, but the running shell must be `az login`-authenticated as the SP or via OIDC.

---

## 6. Running the deploy

```bash
cd <repo-root>
pwsh -File powerbi/deploy.ps1
```

The script executes nine numbered phases (Step 0 .. Step 8) and writes `[INFO]` lines for progress and `[SUCCESS]` on completion of each step. A successful run ends with:

```
[SUCCESS] PowerBI deployment complete.
  Workspace : TCP — Trading Central Panel (id=<workspaceId>)
  Dataset   : id=<datasetId>
  Report    : id=<reportId>
  URL       : https://app.powerbi.com/groups/<workspaceId>/reports/<reportId>
```

Capture the report URL and dataset ID — they are needed for §7 verification and for any future `PATCH /refreshSchedule` calls.

**What each step does:**

| Step | Action | Key API call |
|---|---|---|
| 0 | Preflight — `az` login, azd env load, PowerBI bearer, SP object id, TCP 1433 reachability probe | `az account show`, `Test-NetConnection -Port 1433` |
| 1 | Resolve or create workspace, grant deploy SP Admin | `GET /groups`, `POST /groups?workspaceV2=true`, `POST /groups/{id}/users` |
| 2 | Compile TMDL to a deployable `.bim` | `pbi-tools compile powerbi/model -outPath build/dataset.bim` |
| 3 | Import dataset (`CreateOrOverwrite`) via multipart upload | `POST /groups/{id}/imports?nameConflict=CreateOrOverwrite` |
| 4 | Take dataset ownership + set `SqlServer` / `SqlDatabase` M parameters | `POST .../Default.TakeOver`, `POST .../Default.UpdateParameters` |
| 5 | Trigger an immediate refresh to surface credential / connectivity errors now (not at 07:30 RO the next weekday) | `POST /groups/{id}/datasets/{id}/refreshes` + poll `GET .../refreshes?$top=1` |
| 6 | Configure scheduled refresh (Mon–Fri 07:30 `E. Europe Standard Time`) | `PATCH /groups/{id}/datasets/{id}/refreshSchedule` |
| 7 | Stage report dir, substitute `<SWA_HOSTNAME>` into the AI Assistant page, compile and publish PBIR, rebind to dataset | `pbi-tools compile <staging>`, `POST /groups/{id}/imports`, `POST /reports/{id}/Rebind` |
| 8 | Verify — read latest refresh status, print final report URL | `GET /groups/{id}/datasets/{id}/refreshes?$top=1` |

> `nameConflict=CreateOrOverwrite` in Step 3 preserves the dataset ID across re-deploys.
> Subsequent publishes update the model in-place without breaking existing bookmarks
> or scheduled-refresh configuration that references the same dataset ID.
>
> **Dataset parameter binding (Step 4) vs raw placeholder substitution.** The
> `SqlServer` and `SqlDatabase` parameters in `powerbi/model/model.tmdl` are first-class
> M parameters. The deploy script sets them via `POST .../Default.UpdateParameters`
> rather than editing the compiled `.bim` payload. The `<TENANT_ID>` placeholder is the
> exception — it is substituted into the `.bim` in Step 3 because the OAuth audience is
> not exposed as a queryable M parameter.

---

## 7. Verification

Perform all three checks before closing the deploy session.

### 7.1 Dataset refresh succeeded

Open PowerBI Service → workspace `TCP — Trading Central Panel` → Datasets → `tcp-trading-central-panel` → Refresh history.

The most recent entry must show **Succeeded**. If it shows **Failed**, see §10 for diagnosis.

Alternatively, poll the refresh status via REST:

```bash
# Acquire a bearer token for the PowerBI API.
TOKEN=$(az account get-access-token \
  --resource https://analysis.windows.net/powerbi/api \
  --query accessToken -o tsv)

# List the last five refresh entries for the dataset.
az rest --method GET \
  --uri "https://api.powerbi.com/v1.0/myorg/groups/<workspaceId>/datasets/<datasetId>/refreshes?\$top=5" \
  --headers "Authorization=Bearer $TOKEN" \
  --query "value[0].{status:status, startTime:startTime, endTime:endTime}" \
  -o json
# Expect: { "status": "Completed", "startTime": "...", "endTime": "..." }
```

### 7.2 Report renders

Open the report URL printed by the deploy script. Confirm the four default pages are present:

- **Floor Performance** — aggregated PnL and headcount by trading floor.
- **Team Performance** — team-vs-team comparison with drill-through.
- **Trader Detail** — KPI cards (Sharpe, Sortino, win rate, max drawdown) and PnL line chart.
- **AI Assistant** — landing page with a "Open the AI Assistant in a new tab →" hyperlink button. The destination URL is substituted from `AZURE_STATIC_WEB_APP_HOSTNAME` by `deploy.ps1` Step 7. The page intentionally does NOT embed the SWA in an iframe, because the SWA serves `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'` per the Etapa 6 clickjacking hardening — see §8.

At this stage each page shows one canonical visual. The full layout is built in §8.

### 7.3 Scheduled refresh is active

PowerBI Service → workspace → Datasets → `tcp-trading-central-panel` → Settings → Scheduled refresh.

Confirm:
- **Keep your data up to date**: On.
- **Time zone**: GMT+02:00 Bucharest (PowerBI's label for Europe/Bucharest, including DST).
- **Days**: Monday, Tuesday, Wednesday, Thursday, Friday.
- **Times**: 07:30.

The 07:30 slot is 30 minutes after the Azure Functions timer trigger at 07:00, which ensures the daily synthetic-trade batch has committed before PowerBI imports the snapshot.

---

## 8. Finalising visuals in PowerBI Desktop (manual step)

The deploy script ships a **minimal PBIR skeleton** — one canonical placeholder visual per page. Visual polish requires PowerBI Desktop, which cannot be automated via the REST API (PBIR visual fidelity is still maturing as of v1.0).

1. **Download the published `.pbix`** — PowerBI Service → workspace → Reports → `tcp-trading-central-panel` → `...` → Download this file.
2. **Open in PowerBI Desktop**.
3. **Build page layouts** per the use-case specification in `../design/01_business_requirements.md` §6:
   - **Floor Performance**: floor leaderboard table, last-30-day PnL trend line, asset-class split donut.
   - **Team Performance**: team-vs-team comparison bar chart, drill-through to Trader Detail page.
   - **Trader Detail**: KPI cards for `[Sharpe Ratio]`, `[Sortino Ratio]`, `[Win Rate %]`, `[Max Drawdown EUR]`; daily PnL line chart; trade list table with conditional formatting.
   - **AI Assistant**: the deploy script already places a hyperlink button on this page that opens the SWA URL in a new tab. Do NOT replace it with an iframe — the SWA's Etapa 6 hardening (`X-Frame-Options: DENY` + `Content-Security-Policy: frame-ancestors 'none'`) blocks embedding inside PowerBI by design. The new-tab redirect preserves the AAD session integrity and the clickjacking defence. If the placeholder `<SWA_HOSTNAME>` is still visible in the URL, re-run `pwsh -File powerbi/deploy.ps1` after setting `AZURE_STATIC_WEB_APP_HOSTNAME`.
4. **Save the `.pbix` locally only** — do NOT commit it to the repository. Binary `.pbix` files are excluded by `.gitignore`. The TMDL files under `powerbi/model/` are the text-based source of truth for the semantic model; the visual layout lives only in the downloaded file.
5. **Re-publish** — PowerBI Desktop → Home → Publish → select workspace `TCP — Trading Central Panel` → replace the existing report.

This manual step is a documented v1.0 trade-off. The deploy script handles the deterministic, machine-readable components (model + credentials + RLS + schedule); visual aesthetics are finalised once by a human and the resulting `.pbix` is intentionally kept outside the repo boundary.

---

## 9. Updating the dataset

When `../design/02_database_design.md` views change or new KPI measures are added to `powerbi/model/measures.tmdl`:

1. Edit the relevant TMDL file(s) under `powerbi/model/`.
2. Re-run:
   ```bash
   pwsh -File powerbi/deploy.ps1
   ```
   The script is **idempotent**. The `nameConflict=CreateOrOverwrite` import in step 4 replaces the model while preserving the dataset ID, scheduled-refresh configuration, and the manually-built visual layout in the published report.
3. Verify via §7 — run a manual refresh and confirm Succeeded.

> When view columns are renamed or dropped, update the corresponding TMDL table file
> (`powerbi/model/tables/<table>.tmdl`) before re-running the script. A mismatch
> between the TMDL column reference and the actual view output causes a refresh error
> with the message "Column '<name>' does not exist".

---

## 10. Troubleshooting

### HTTP 401 on `GET /groups`

**Symptom**: The deploy script fails at step 2 with `HTTP 401 Unauthorized`.

**Diagnosis**: The bearer token was acquired for the wrong resource scope, or the SP has no PowerBI API permissions at all.

**Fix**:
1. Confirm the token scope: `az account get-access-token --resource https://analysis.windows.net/powerbi/api` — if this fails, you are not logged in as the SP.
2. Re-check step 4.4 — ensure `Dataset.ReadWrite.All`, `Report.ReadWrite.All`, and `Workspace.ReadWrite.All` appear in the app registration under "API permissions" with status "Granted".
3. If admin consent has not been granted, have a tenant admin run: `az ad app permission admin-consent --id <appId>`.

---

### HTTP 403 on workspace operations

**Symptom**: Token is valid (step 2 passes) but step 2 or step 4 returns `HTTP 403 Forbidden`.

**Diagnosis**: The PowerBI tenant setting "Allow service principals to use Power BI APIs" is disabled or the SP is not in the allowed security group.

**Fix**: Re-check step 4.5 — a PowerBI administrator must enable the tenant setting in the PowerBI Admin Portal. This cannot be done via CLI. Allow up to 15 minutes for the setting to propagate after being saved.

---

### Dataset refresh fails — "Login failed for user"

**Symptom**: Step 7 (or the scheduled refresh in §7.1) shows a failed refresh with error message containing "Login failed for user 'tcp-powerbi-sp'".

**Diagnosis**: The SP does not exist as a SQL contained user in `sqldb-tcp-prod-weu`, or the `tcp_bi_reader` role grant is missing.

**Fix**: Re-run step 4.7:
```bash
sqlcmd -S sql-tcp-prod-weu.database.windows.net -d sqldb-tcp-prod-weu -G -Q "
  IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name='tcp-powerbi-sp')
    CREATE USER [tcp-powerbi-sp] FROM EXTERNAL PROVIDER;
  ALTER ROLE tcp_bi_reader ADD MEMBER [tcp-powerbi-sp];
"
```

---

### Dataset refresh fails — "Couldn't connect to data source"

**Symptom**: Refresh fails with "Couldn't connect to data source" or a network timeout.

**Diagnosis**: The Azure SQL firewall is blocking the PowerBI service IP ranges.

**Fix**: Verify that the `AllowAllAzureServices` firewall rule is set on the SQL server (Bicep created this in Etapa 4). PowerBI Service uses IP ranges within the Azure backbone and requires this rule to be on. Confirm:
```bash
az sql server firewall-rule list \
  --resource-group rg-tcp-prod-weu \
  --server sql-tcp-prod-weu \
  --query "[?name=='AllowAllAzureServices']" \
  -o table
# Expected: one row with startIpAddress=0.0.0.0, endIpAddress=0.0.0.0
```
If missing, re-apply the Bicep module or add the rule manually:
```bash
az sql server firewall-rule create \
  --resource-group rg-tcp-prod-weu \
  --server sql-tcp-prod-weu \
  --name AllowAllAzureServices \
  --start-ip-address 0.0.0.0 \
  --end-ip-address 0.0.0.0
```

---

### `pbi-tools` not found

**Symptom**: Step 3 fails with "`pbi-tools`: command not found" or "The term 'pbi-tools' is not recognized".

**Diagnosis**: The `pbi-tools` .NET global tool is not installed, or the .NET tools path is not in `PATH`.

**Fix**:
```bash
dotnet tool install --global pbi-tools
# Then reload your shell PATH:
# Bash: source ~/.bashrc  (or open a new terminal)
# PowerShell: $env:PATH += ";$env:USERPROFILE\.dotnet\tools"
```
If .NET is not available, use the manual PowerBI Desktop compile path described in §8: open `powerbi/model/database.tmdl` from the repo directly in PowerBI Desktop via the TMDL import feature, publish to the workspace, then run steps 5 and 6 of the deploy script independently using `az rest`.

---

### TMDL compile fails — "Cannot find table v_X"

**Symptom**: Step 3 fails with a compile error referencing a view name that does not exist.

**Diagnosis**: V001 or V002 migrations were not applied, so the views `v_trades_enriched`, `v_employee_performance`, `v_team_performance`, `v_floor_performance`, or `v_daily_pnl` are absent in the database.

**Fix**: Re-apply the post-provision migrations:
```bash
pwsh infra/scripts/postprovision.ps1 --step 0
```
Then re-run `pwsh -File powerbi/deploy.ps1`.

---

### Scheduled refresh missing from dataset settings

**Symptom**: Step 6 reported `[SUCCESS]` but the PowerBI Service UI shows "Scheduled refresh: Off".

**Diagnosis**: The `PATCH /refreshSchedule` call succeeded but the dataset still shows as not configured — this can happen if the dataset's gateway connection was not yet bound when step 6 ran (step 5 and step 6 run sequentially, but the gateway binding can take a few seconds to propagate).

**Fix**: Wait 60 seconds, then manually trigger a refresh from the PowerBI Service UI (Dataset → Refresh now). If it succeeds, the scheduled-refresh configuration is preserved; toggle the switch to On if it shows Off.

---

## 11. Decommission and rollback

If the deployed workspace must be removed entirely (e.g., re-provisioning a clean environment):

```bash
# Step 1 — Acquire a bearer token.
TOKEN=$(az account get-access-token \
  --resource https://analysis.windows.net/powerbi/api \
  --query accessToken -o tsv)

# Step 2 — Retrieve the workspace ID.
az rest --method GET \
  --uri "https://api.powerbi.com/v1.0/myorg/groups" \
  --headers "Authorization=Bearer $TOKEN" \
  --query "value[?name=='TCP — Trading Central Panel'].id" \
  -o tsv

# Step 3 — Delete the workspace (also deletes all datasets and reports inside it).
# WARNING: this is irreversible. The workspace goes to a "deleted" state in PowerBI
# Service but cannot be recovered via REST API — it must be restored within 90 days
# via the PowerBI Admin Portal if needed.
az rest --method DELETE \
  --uri "https://api.powerbi.com/v1.0/myorg/groups/<workspaceId>" \
  --headers "Authorization=Bearer $TOKEN"
```

After deletion, re-run `pwsh -File powerbi/deploy.ps1` to recreate from source. The full first-deploy path (§6) applies, including the ~30-minute time estimate.

---

## 12. Cross-references

| Document | Relevance |
|---|---|
| `../decisions/ADR-001-powerbi-deployment.md` | Deployment strategy: REST API primary path, MCP placeholder, final visual polish rationale. |
| `../design/03_architecture.md` §3.3 | BI path: Import mode rationale, scheduled-refresh timing relative to the timer trigger. |
| `../design/03_architecture.md` §4.2 | Per-resource configuration including the SQL Free Offer `AllowAllAzureServices` firewall rule. |
| `../design/03_architecture.md` §5 | RBAC matrix: `tcp_bi_reader` role grants, Function MI vs SP identity separation. |
| `../decisions/ADR-003-rls-session-context.md` | RLS contract: PowerBI SP must be registered in `dim_UserRoles` with `scope='admin'` (§6 of that ADR). |
| `../security/credentials_rotation.md` §2.7 | PowerBI SP rotation procedure: federated-credential re-issue (preferred) or client-secret rotation (fallback). |
| `powerbi/README.md` | TMDL + PBIR source structure, measure catalogue, and relationship overview. |
| `../design/01_business_requirements.md` §6 | Use-case specifications driving the four report-page layouts. |

---

## 13. Change history

| Version | Date | Notes |
|---|---|---|
| 1.0 | 2026-05-16 | Initial version — Etapa 7. |
