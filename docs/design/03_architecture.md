# 03 — Azure Cloud Architecture (TCP — Trading Central Panel)

Etapa 1, deliverable 3 of 3. Companion to `01_business_requirements.md` and `02_database_design.md`.
Audience: the IaC engineer who implements Etapa 4 (Bicep + `azd`) and the security reviewer of Etapa 6.

---

## 1. Executive summary

TCP — Trading Central Panel runs entirely on Azure free tiers in a single resource group in **West Europe**. A serverless Azure SQL Database (Free Offer, auto-pause after 60 min idle) holds the star schema and synthetic trades. A single Python 3.12 Azure Function App on the Consumption plan hosts both the daily generator (NCRONTAB timer at 07:00 Europe/Bucharest, Mon–Fri) and the AI assistant HTTP endpoint (`POST /api/ask`). A vanilla HTML+JS Azure Static Web App on the Free plan serves the chat UI and proxies authenticated calls to the Function App via its `linked backend`. Secrets (`ANTHROPIC_API_KEY`, the bootstrap SQL admin password) live in Azure Key Vault and are surfaced to the Function App via Key Vault references and a system-assigned managed identity. Application Insights + Log Analytics provide observability under the 5 GB/month free ingestion cap. GitHub Actions deploys via OIDC federated credentials — no static secrets in the repo. The recurring bill at steady state is **$0/month**.

The security posture is "academic / portfolio grade": SQL is reachable via the public endpoint with `Allow Azure services` ON (mandatory trade-off for the free tier — see §8), but every other surface is locked down: AAD-only SQL authentication, RBAC-only Key Vault, AAD-protected `/api/ask` route via SWA `linked backend`, and a clearly bounded threat model that excludes determined attackers.

---

## 2. Architecture overview

| # | Azure resource | Role (one line) |
|---|---|---|
| 1 | Resource Group (`rg-tcp-prod-weu`) | Single container for all production resources; the unit of `azd up` / `azd down` and of RBAC scoping. |
| 2 | Azure SQL Server (`sql-tcp-prod-weu`) | Logical server hosting the Free Offer database; AAD-only auth, firewall pinned to "Allow Azure services" + dev IP at bootstrap. |
| 3 | Azure SQL Database (`sqldb-tcp-prod-weu`) | Free Offer Serverless GP, 1 vCore, auto-pause 60 min, 32 GB. Holds the star schema (`dim_*`, `fact_*`, `config_*`, views `v_*`). |
| 4 | Storage Account (`sttcpprodweu`) | Functions backing storage, BACPAC export container, App Insights diagnostic archive. |
| 5 | App Service Plan (`asp-tcp-prod-weu`, Consumption Y1) | Required wrapper for the Function App on Consumption; charged per-execution after free grant. |
| 6 | Function App (`func-tcp-prod-weu`) | Python 3.12, single app, **five triggers** (after MJ-05 + MJ-06): `TimerTrigger_DailyGenerator`, `WarmupTrigger`, `HttpTrigger_AskAssistant`, `TimerTrigger_BacpacExport`, `HttpTrigger_Ping`. |
| 7 | Static Web App (`swa-tcp-prod-weu`) | Free plan; vanilla HTML/JS chat UI; AAD auth; `linked backend` to Function App. |
| 8 | Key Vault (`kv-tcp-prod-weu`) | Standard SKU, RBAC mode. Stores `ANTHROPIC_API_KEY`, bootstrap SQL admin password, any future tokens. |
| 9 | Managed Identity (system-assigned on Function App) | The only identity that reads Key Vault secrets and writes to SQL at runtime. |
| 10 | Application Insights (`ai-tcp-prod-weu`) | Distributed traces, custom metrics (Anthropic token usage), exceptions. Connected to the Log Analytics workspace. |
| 11 | Log Analytics Workspace (`log-tcp-prod-weu`) | Backend for App Insights; 5 GB/month free; 30-day retention. |
| 12 | (External) PowerBI Service workspace `TCP-Prod` | Out-of-Azure dependency, .edu free license; consumes `sqldb-tcp-prod-weu` via **Scheduled Refresh (Import mode)** at 07:30 RO. |

Region: **West Europe** (`westeurope`) for all resources. Fallback **North Europe** (`northeurope`) only if the SQL Free Offer is unavailable in `westeurope` at provision time (verified at deploy with `az sql db list-editions --location westeurope --query "[?name=='Free']"`). Justification: București → Amsterdam (`westeurope`) RTT is ~30–45 ms, ideal for synchronous Function → SQL hops; the fallback Dublin (`northeurope`) RTT is ~50–70 ms — still within performance budgets.

---

## 3. Logical architecture (prose description for the diagram authors)

Two independent request paths share the same Function App and the same SQL database. The Mermaid diagram in `docs/diagrams/architecture.mmd` is rendered separately by `mermaid-expert`; the prose below is the source-of-truth specification for it.

### 3.1 Timer path — daily synthetic-trade generation

1. **Azure Functions runtime** internally fires `TimerTrigger_DailyGenerator` at the NCRONTAB time `0 0 7 * * 1-5`. Because the Function App is configured with `WEBSITE_TIME_ZONE=E. Europe Standard Time`, the cron is interpreted in Europe/Bucharest (DST-safe).
2. The function calls `tcp.synth.previous_business_day()` to compute the target trade date (Monday produces Friday's trades; Tue–Fri produce the prior day).
3. It pulls the **AAD access token** for SQL from the Function App's **system-assigned managed identity** via `DefaultAzureCredential`.
4. It opens a pooled `pyodbc`/`aioodbc` connection to `sqldb-tcp-prod-weu`. The SQL serverless instance auto-resumes if paused (cold-start adds ~30–60 s — within budget).
5. It writes ~150–250 rows into `fact_Trades` in a single transaction (idempotent on `(trade_uid)`).
6. Telemetry: rows inserted, target date, generator wall-time, and SQL resume duration are emitted as custom metrics (`tcp.generator.rows_inserted`, `tcp.generator.duration_ms`, `tcp.sql.resume_ms`) to Application Insights.

### 3.2 User-question path — AI assistant

1. The user opens the Static Web App URL in a browser. SWA serves the static HTML/JS bundle and challenges for AAD sign-in via the platform's built-in auth (no custom OIDC code in the JS).
2. After sign-in, the JS submits a question to `POST /api/ask` — but the request is sent to the SWA origin, not to the Function App. SWA's **linked backend** transparently forwards it to `func-tcp-prod-weu`, injecting an `x-ms-client-principal` header containing the AAD identity claims.
3. `HttpTrigger_AskAssistant` validates the principal header (rejects anonymous) and the SWA shared-secret header `X-SWA-Forwarded` (rejects with 403 if the value does not match the KV-stored secret — see §8.2), then:
   1. Fetches `ANTHROPIC_API_KEY` from Key Vault — but cached via the Function App setting `ANTHROPIC_API_KEY=@Microsoft.KeyVault(SecretUri=https://kv-tcp-prod-weu.vault.azure.net/secrets/ANTHROPIC-API-KEY/)`. The platform refreshes the value on app restart; no per-request Key Vault hit.
   2. Loads the read-only schema context (system prompt, pre-built and cached via Anthropic prompt caching with the `cache_control: ephemeral` block — 90 % cache hit rate target).
   3. Calls Anthropic `claude-haiku-4-5` **once** with the user message + schema context. The model returns a JSON envelope `{ sql: "...", answer_template: "..." }` — a single call covering both the SQL query and the natural-language template (or a refusal). No second Anthropic call is made per request.
   4. The returned SQL is validated by `safe_query.py`. The LLM's output is treated as untrusted SQL: `safe_query.py` parses it with `sqlglot`, allowlists tables (`v_*` views) and read-only procs (`usp_GetEmployeePerformance`, `usp_GetTopPerformers`), rejects any non-`SELECT` statement, forbids `UNION` / `INTO` / `WAITFOR` / `xp_*` / `OPENROWSET` / SQL comments, enforces `TOP <= 1000`, and refuses unparseable input. An adversarial CI fixture (≥ 20 prompts) gates the contract (Etapa 5). See also §6.
   5. **Per-user RLS contract** (see `02_DB §9` for the SQL-side predicate). Before executing the validated SQL, the function:
      - (a) parses `x-ms-client-principal` (base64-decoded JSON) and extracts the caller's AAD `oid` claim;
      - (b) verifies the caller exists in `dim_UserRoles` with `is_active = 1` (rejects with HTTP 403 if missing — no implicit onboarding);
      - (c) on the freshly-checked-out connection, runs `EXEC sp_set_session_context @key=N'aad_object_id', @value=@oid, @read_only=1` **before** the LLM-generated SQL;
      - (d) executes the validated SQL against `sqldb-tcp-prod-weu` using the MI-derived AAD token. The MI is mapped to the `tcp_ai_assistant` DB role (SELECT-only on `v_*` views and `dim_*`, no access to `fact_Trades` raw or `config_Capital` writes). Row filtering for trader / team_lead / floor_manager / admin scopes is enforced by the RLS policy `rls.TradesAccessPolicy` on `fact_Trades`, which reads `SESSION_CONTEXT(N'aad_object_id')` and joins to `dim_UserRoles` to derive the caller's scope.
   6. Results (truncated to 1000 rows) are returned to the browser as JSON. The JS renders a table; the natural-language paragraph is rendered from the `answer_template` returned in step 3 (no additional Anthropic call).
4. Every step is wrapped in a distributed-trace span. Anthropic token usage is parsed from the SDK response and emitted as `tcp.anthropic.input_tokens` / `tcp.anthropic.output_tokens` / `tcp.anthropic.cache_read_tokens` custom metrics. A `tcp.rls.session_context_set=true` event is emitted per request to evidence the RLS contract.

### 3.3 BI path — PowerBI

PowerBI Service connects to `sqldb-tcp-prod-weu` using a service principal (AAD app registration) granted the `tcp_bi_reader` SQL role (SELECT-only on the same `v_*` views). **The dataset is configured in Import mode**, not DirectQuery: Scheduled Refresh runs at 07:30 Europe/Bucharest, 30 minutes after the generator completes, and the dataset caches a full snapshot in the PowerBI Service. At 60–75k total rows, Import mode is the right fit for the free tier; DirectQuery would resume the auto-paused database on every visual render, burning the 100 000 vCore-second monthly budget during a single demo session. This is out-of-Azure infrastructure but in-scope for the design.

### 3.5 Cold-start mitigation — `/api/ping`

To make the cold-start path explicit and demonstrable, the SWA frontend exposes a "Wake up the database" button which calls a lightweight `HttpTrigger_Ping` endpoint at `/api/ping`. The endpoint is HTTP-only (no AAD required — it touches no row data) and returns `{ sql_resume_ms: <int>, status: "warm" | "resumed" }`. The function issues `SELECT 1` against `sqldb-tcp-prod-weu`, which resumes the auto-paused serverless instance. The user clicks "Wake up" before the first real question; subsequent `/api/ask` calls hit the warm path. The endpoint also emits a `tcp.sql.resume_ms` metric. Selected over a perpetual keep-alive timer because the latter would consume vCore-seconds 24/7.

### 3.4 Trust boundaries (edges)

- Browser ↔ SWA: TLS, SWA-managed cert, AAD interactive sign-in.
- SWA ↔ Function App: SWA platform-internal (Microsoft backbone), bearer principal forwarded; the SWA platform also injects the `X-SWA-Forwarded` shared-secret header (value from KV) — the function rejects any request lacking the matching value with HTTP 403.
- Function App ↔ Key Vault: managed-identity AAD token, RBAC role `Key Vault Secrets User`, public endpoint over TLS.
- Function App ↔ SQL: managed-identity AAD token, TDS over TLS, `Allow Azure services` firewall path. **Per-request binding**: the AAD `oid` claim parsed from `x-ms-client-principal` is the binding between the SWA-authenticated principal and the SQL row filter — it is forwarded via `SESSION_CONTEXT(N'aad_object_id')` and joined to `dim_UserRoles` by the RLS predicate. The Function App MI itself authenticates under a single `tcp_ai_assistant` DB role; row scoping is RLS-driven, not connection-string-driven.
- Function App ↔ Anthropic API: outbound HTTPS to `api.anthropic.com`, bearer key from KV reference.

---

## 4. Resource topology

### 4.1 Naming convention

Pattern: `<prefix>-<project>-<env>-<region>` (lowercase, hyphens). Storage accounts drop hyphens to comply with the Azure naming rules.

| Resource | Convention | Production name |
|---|---|---|
| Resource Group | `rg-<proj>-<env>-<region>` | `rg-tcp-prod-weu` |
| SQL Server | `sql-<proj>-<env>-<region>` | `sql-tcp-prod-weu` |
| SQL Database | `sqldb-<proj>-<env>-<region>` | `sqldb-tcp-prod-weu` |
| Storage Account | `st<proj><env><region>` (no hyphens, ≤24 chars) | `sttcpprodweu` |
| App Service Plan | `asp-<proj>-<env>-<region>` | `asp-tcp-prod-weu` |
| Function App | `func-<proj>-<env>-<region>` | `func-tcp-prod-weu` |
| Static Web App | `swa-<proj>-<env>-<region>` | `swa-tcp-prod-weu` |
| Key Vault | `kv-<proj>-<env>-<region>` | `kv-tcp-prod-weu` |
| App Insights | `ai-<proj>-<env>-<region>` | `ai-tcp-prod-weu` |
| Log Analytics Workspace | `log-<proj>-<env>-<region>` | `log-tcp-prod-weu` |
| Managed Identity (system) | `<resource>-mi` (implicit, no separate name) | n/a (system-assigned) |
| User-assigned MI (if needed) | `id-<proj>-<env>-<role>` | `id-tcp-prod-deploy` (only for CI; see §6) |

Tags applied to every resource (Bicep `tags: {...}`):

```bicep
tags: {
  project: 'tcp'
  env: 'prod'
  owner: 'TODO'                    // student email, filled in PLACEHOLDERS phase
  costcenter: 'thesis'
  managedBy: 'azd'
  repo: 'github.com/TODO/tcp-trading-central-panel'
}
```

### 4.2 Per-resource configuration

#### Azure SQL Database — Free Offer

- **API version**: `Microsoft.Sql/servers/databases@2023-08-01-preview` (pinned — the `useFreeLimit` and `freeLimitExhaustionBehavior` properties exist only on this version or newer GA).
- **SKU**: `GP_S_Gen5_1` (General Purpose, Serverless, Gen5, 1 vCore).
- **Edition**: `GeneralPurpose`, `family: Gen5`, `capacity: 1`.
- **Free Offer flag**: `useFreeLimit: true`, `freeLimitExhaustionBehavior: 'AutoPause'` (database auto-pauses if monthly quota exhausted, rather than billing — non-negotiable for the $0/month target).
- **Auto-pause delay**: `autoPauseDelay: 60` (minutes).
- **Min capacity**: `minCapacity: 0.5` vCore.
- **Max size**: `maxSizeBytes: 34359738368` (32 GiB — the Free Offer cap).
- **Collation**: `Latin1_General_100_CI_AS_SC_UTF8` (UTF-8, Romanian-safe, case-insensitive).
- **Backup**: PITR 7 days included (Free Offer); LTR not available on Free.
- **Identity**: AAD admin set to the deployer (Etapa 2 bootstrap) → switched to a dedicated AAD group `aad-tcp-sql-admins` post-bootstrap.
- **Expected steady-state**: ~3 000 vCore-seconds/day (15 min active for daily generator + ~50 short `/api/ask` queries × ~3 s each warm) ≈ 60 000–90 000 vCore-seconds/month. Headroom: 10–40 %.

#### Storage Account

- **SKU**: `Standard_LRS` (locally redundant).
- **Kind**: `StorageV2`.
- **Access tier**: `Hot`.
- **TLS**: `minimumTlsVersion: 'TLS1_2'`, `supportsHttpsTrafficOnly: true`.
- **Containers**:
  - `azure-webjobs-hosts` (auto-created by Functions runtime).
  - `azure-webjobs-secrets` (auto-created).
  - `bacpac-exports` — weekly BACPAC dumps with 28-day lifecycle rule.
- **Identity**: Function App MI granted `Storage Blob Data Contributor` on `bacpac-exports`; Functions runtime uses connection string from a Key Vault reference.
- **Expected steady-state**: <500 MB (well under the 5 GB / 12-month free allowance).
- **Footnote on free pricing**: Storage Account is free for **12 months from subscription creation**; ~$0.05/month thereafter for 500 MB hot LRS — absorbed by Azure-for-Students renewal at the user's discretion (see §10).

#### Function App + App Service Plan

- **Plan SKU**: `Y1` (Consumption), tier `Dynamic`. Linux. `reserved: true`.
- **Runtime**: `python|3.12`.
- **App settings (critical)**:
  - `WEBSITE_TIME_ZONE = E. Europe Standard Time`
  - `FUNCTIONS_WORKER_RUNTIME = python`
  - `FUNCTIONS_EXTENSION_VERSION = ~4`
  - `AzureWebJobsStorage = @Microsoft.KeyVault(SecretUri=https://kv-tcp-prod-weu.vault.azure.net/secrets/STORAGE-CONNECTION-STRING/)`
  - `APPLICATIONINSIGHTS_CONNECTION_STRING = <from output of ai module>`
  - `ANTHROPIC_API_KEY = @Microsoft.KeyVault(SecretUri=https://kv-tcp-prod-weu.vault.azure.net/secrets/ANTHROPIC-API-KEY/)`
  - `SQL_SERVER = sql-tcp-prod-weu.database.windows.net`
  - `SQL_DATABASE = sqldb-tcp-prod-weu`
  - `AZURE_CLIENT_ID = <system-MI client id>` (so `DefaultAzureCredential` picks the right identity)
  - `PYTHON_ENABLE_WORKER_EXTENSIONS = 1`
  - `SCM_DO_BUILD_DURING_DEPLOYMENT = 1`
- **HTTPS only**: `httpsOnly: true`.
- **Client affinity**: `clientAffinityEnabled: false`.
- **Identity**: `identity: { type: 'SystemAssigned' }`.
- **CORS**: allowed origins = SWA URL only.
- **Authentication**: anonymous on the function endpoint (SWA enforces AAD at the linked-backend layer; the function rejects requests missing the `x-ms-client-principal` header — defense in depth).
- **Expected steady-state**: ~22 timer invocations/month + ~1 500 HTTP invocations/month (50 questions/day × 30 days). All within the 1M-executions / 400 000 GB-s free grant.

#### Static Web App

- **SKU**: `Free`.
- **Repository**: linked at deploy via `azd` to the `static/` folder in the GitHub repo.
- **Build**: `app_location: 'static'`, no API code in SWA (we use linked backend, not SWA-hosted API).
- **Auth**: `auth: { identityProviders: { azureActiveDirectory: { ... } } }` — registration provided via the SWA `staticwebapp.config.json` route protection (`/api/* → role: authenticated`).
- **Linked backend**: `Microsoft.Web/staticSites/linkedBackends` pointing to `func-tcp-prod-weu`.
- **Custom domain**: TODO (out of scope; SWA Free includes two custom domains, but the thesis project uses the auto-generated `*.azurestaticapps.net` URL).
- **Expected steady-state**: ~100 page loads/month, <1 GB bandwidth/month. Free tier includes 100 GB/month.

#### Key Vault

- **SKU**: `Standard`.
- **Access model**: `enableRbacAuthorization: true` (no access policies).
- **Soft delete**: `enableSoftDelete: true`, `softDeleteRetentionInDays: 7` (minimum for KV).
- **Purge protection**: `false` (allows `azd down` to fully delete during the thesis cycle; flip to `true` post-defense).
- **Network ACLs**: `defaultAction: 'Allow'` (free tier; private endpoints would require Premium + VNet integration on Functions, both paid).
- **Secrets stored** (see §7 for the full table).
- **Expected steady-state**: <100 operations/month, far below the 10 000-op/month free indicative threshold (KV is billed per 10 000 ops, but at this volume the bill rounds to $0).

#### Application Insights + Log Analytics Workspace

- **Workspace SKU**: `PerGB2018` with `dailyQuotaGb: 0.5` (enforced cap → cannot exceed 15 GB/month, well under the 5 GB free grant; the cap is conservative for safety).
- **Workspace retention**: `retentionInDays: 30`.
- **App Insights kind**: `workspace`-based, linked to `log-tcp-prod-weu`.
- **Sampling**: adaptive sampling on by default; lowered to fixed 10 % via `applicationinsights.json` if ingestion approaches 4 GB/month (alert at §10 mitigation).
- **Expected steady-state**: ~1–2 GB/month.

---

## 5. Identity & access — RBAC matrix

All RBAC scopes are at the resource-group level unless otherwise noted. Subscription-level role assignments are forbidden by policy.

| Identity | Scope | Role(s) | Justification |
|---|---|---|---|
| **GitHub Actions OIDC SP** (`sp-tcp-github-cicd`) | `rg-tcp-prod-weu` | `Contributor` | `azd provision` / `azd deploy` needs to create/update every resource in the RG. Trade-off: a single RG-scoped `Contributor` is accepted for thesis-scale because it keeps role-assignment count and bootstrap complexity manageable; in a production deployment we would split this into `Website Contributor` + `SQL DB Contributor` + `Storage Account Contributor` + `Monitoring Contributor` + a custom `KeyVaultDeployer` role (TODO: file ADR-XXX-cicd-role-split post-thesis). The previously-listed `Key Vault Secrets Officer` and `SQL Server Contributor` rows are subsumed by RG `Contributor` and have been removed. |
| GitHub Actions OIDC SP | Subscription | `Reader` | Required by `azd` to list locations/SKUs during what-if. Read-only at subscription scope is acceptable. |
| **Function App MI** (system-assigned on `func-tcp-prod-weu`) | `kv-tcp-prod-weu` | `Key Vault Secrets User` | Read-only access to the four secrets it needs (Anthropic key, storage conn string, etc.) via Key Vault references. |
| Function App MI | `sttcpprodweu/blobServices/containers/bacpac-exports` | `Storage Blob Data Contributor` | Weekly BACPAC export from `TimerTrigger_BacpacExport` (writes BACPAC blobs). |
| Function App MI | `sqldb-tcp-prod-weu` (database scope) | `SQL DB Contributor` | Required by `TimerTrigger_BacpacExport` to invoke `New-AzSqlDatabaseExport` and poll the async export operation. Scoped to the single database, not the server. |
| Function App MI | `sqldb-tcp-prod-weu` | (AAD-mapped SQL user, not Azure RBAC) → DB roles `tcp_generator` (write to `fact_Trades`) + `tcp_ai_assistant` (read views only) | The MI is created as a contained DB user via `CREATE USER [func-tcp-prod-weu] FROM EXTERNAL PROVIDER` and granted the two app roles defined in `02_database_design.md`. **RLS contract**: views must execute with the caller's rights (no `EXECUTE AS OWNER`); the Function App MUST set `SESSION_CONTEXT(N'aad_object_id')` per request — see `02_DB §9` and §3.2. |
| **Static Web App MI** | (none) | — | SWA's linked-backend auth uses platform-native token forwarding (`x-ms-client-principal`); no Azure RBAC needed. The Function App validates the forwarded principal directly. |
| **Developer (user)** | `rg-tcp-prod-weu` | `Owner` (time-bound for the duration of the thesis) | Enables interactive `az` debugging and `azd` runs from the workstation. Removed at hand-in; replaced with a `Reader`-bound break-glass account. |
| Developer (user) | `sql-tcp-prod-weu` | AAD admin (server-level) | Required for the initial schema apply and for break-glass DBA actions. |
| **PowerBI Service Principal** (`sp-tcp-powerbi`) | `sqldb-tcp-prod-weu` | (AAD-mapped SQL user) → DB role `tcp_bi_reader` (SELECT on `v_*` views) | Scheduled Refresh (Import mode) from PowerBI Service. Created during Etapa 7. |

Notes:

- The system-assigned MI on the Function App is preferred over a user-assigned MI to keep the identity lifecycle tied to the function (one fewer resource, one fewer cleanup step). If we ever split timer + http into two function apps, switch to a single **user-assigned MI** shared between them.
- Role assignments are declared inside Bicep modules (`modules/identity.bicep` produces the `roleAssignments` array consumed by `modules/keyvault.bicep`, `modules/sql.bicep`, etc.).
- No AAD groups are created in this design; the developer's user account is the AAD admin. For multi-engineer scenarios, replace with `aad-tcp-sql-admins` group (TODO post-thesis).

---

## 6. OIDC federation flow (GitHub Actions → Azure)

**Single app registration** is sufficient for a one-user thesis project; multi-environment is a non-goal. The single SP carries multiple federated credentials, each scoped to a different `subject`.

### 6.1 One-time setup (manual, by the user)

**Step 1.** In Entra ID, create an App Registration: **`sp-tcp-github-cicd`**. Record `clientId`, `tenantId`.

**Step 2.** Under the new app → **Certificates & secrets → Federated credentials → Add credential**, add three entries (issuer always `https://token.actions.githubusercontent.com`, audience always `api://AzureADTokenExchange`). GitHub OIDC subject claims do **not** support wildcards in `ref:refs/heads/*`; we use the `environment:` form instead so the matching workflow must declare the corresponding `environment:` block:

| Name | Subject |
| --- | --- |
| gh-main | `repo:<owner>/tcp-trading-central-panel:ref:refs/heads/main` |
| gh-pr | `repo:<owner>/tcp-trading-central-panel:pull_request` |
| gh-dev | `repo:<owner>/tcp-trading-central-panel:environment:dev` |

The `gh-dev` credential matches any workflow run that sets `environment: dev` at the job level — feature-branch deployments declare this in their job spec.

**Step 3.** Assign RBAC roles to the SP per §5.

**Step 4.** Store **only non-secret** values as GitHub repo variables (not secrets):

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

Nothing else is stored in GitHub. No `AZURE_CREDENTIALS` JSON, no service principal password. Tenant id and subscription id are not secrets but are low-sensitivity identifiers — they enable enumeration of resources if combined with a leaked OIDC token, so treat them with care even though they are stored as variables.

### 6.2 Workflow boilerplate

Every workflow that touches Azure includes this block:

```yaml
permissions:
  id-token: write    # required to mint the OIDC token for Azure exchange
  contents: read     # default for actions/checkout

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: dev  # required for the gh-dev federated credential (subject: environment:dev)
    steps:
      - uses: actions/checkout@v4
      - uses: Azure/setup-azd@v2
      # azd auth login is the canonical OIDC entrypoint in azd 1.6+; it powers both
      # `azd` and the embedded `az` CLI. `azure/login@v2` is intentionally omitted
      # to avoid duplicate token exchange (azd handles both).
      - run: azd auth login --client-id ${{ vars.AZURE_CLIENT_ID }} --tenant-id ${{ vars.AZURE_TENANT_ID }} --federated-credential-provider github
      - run: azd provision --no-prompt
      - run: azd deploy --no-prompt
```

### 6.3 Verification (Etapa 4 acceptance criterion)

```bash
# From a CI run log, the access token exchange must show:
# "Logging in using OIDC token..."
# "AAD Token successfully retrieved."
# and NO occurrence of "client-secret" or "AZURE_CREDENTIALS".
```

### 6.4 `safe_query.py` contract (LLM-emitted SQL validation)

The Anthropic call returns raw SQL inside the JSON envelope `{ sql, answer_template }`. That SQL is **untrusted input**. `safe_query.py` is the single mandatory validation layer between the LLM and `sqldb-tcp-prod-weu`:

- Parse the SQL with `sqlglot` (T-SQL dialect). Unparseable input → reject (HTTP 400).
- Allowlist objects: tables `v_*` (the five reporting views) and read-only procs (`usp_GetEmployeePerformance`, `usp_GetTopPerformers`). Any reference outside the allowlist → reject.
- Allowlist statement types: `SELECT` only. Any of `INSERT`/`UPDATE`/`DELETE`/`MERGE`/`DROP`/`CREATE`/`ALTER`/`EXEC`/`EXECUTE`/`GRANT`/`REVOKE`/`TRUNCATE` → reject.
- Forbid tokens: `UNION` (and `UNION ALL`), `INTO`, `WAITFOR`, `xp_*`, `OPENROWSET`, SQL comments (`--`, `/* */`). Encoded-payload bypass attempts (Unicode escapes, char-by-char rebuilds) → reject via post-normalisation check.
- Enforce `TOP n` with `n <= 1000`. If the LLM omitted `TOP`, the validator injects `TOP 1000`.
- Refuse on any uncertainty: there is no "best-effort" path. The contract is fail-closed.

An adversarial CI fixture (≥ 20 prompts: `UNION SELECT`, `; DROP`, `xp_cmdshell`, `OPENROWSET`, comment-bypass, encoded payloads, RLS-bypass attempts via `SET SESSION_CONTEXT`) is required in Etapa 5 — every prompt must end in HTTP 400 / refusal.

### 6.5 AAD-only auth flip (post-bootstrap)

The first schema apply uses the bootstrap SQL admin password (KV secret `SQL-ADMIN-PASSWORD-BOOTSTRAP`). After the first successful schema apply, the `postprovision` hook (`infra/scripts/postprovision.ps1`) runs:

```powershell
Set-AzSqlServerActiveDirectoryOnlyAuthentication `
    -ResourceGroupName 'rg-tcp-prod-weu' `
    -ServerName 'sql-tcp-prod-weu' `
    -Enable $true

Remove-AzKeyVaultSecret `
    -VaultName 'kv-tcp-prod-weu' `
    -Name 'SQL-ADMIN-PASSWORD-BOOTSTRAP' `
    -Force
```

CI verifies the secret is **absent** from KV before marking the deploy green (Etapa 4 acceptance bullet). Once flipped, SQL Server accepts only AAD-issued tokens; the bootstrap password is unrecoverable and only the AAD admin + MI principals can authenticate.

---

## 7. Secrets management

All secrets live in `kv-tcp-prod-weu`. None are in app settings as plaintext; every reference uses the Key Vault reference syntax. Rotation policy: annual or on-incident, documented in the future `docs/security/credentials_rotation.md`.

| Secret name (KV) | Function App setting | Source | Rotation | Used by |
|---|---|---|---|---|
| `ANTHROPIC-API-KEY` | `ANTHROPIC_API_KEY` | User-provided at Etapa 5 bootstrap | Annual; on-incident if leaked | `HttpTrigger_AskAssistant` |
| `STORAGE-CONNECTION-STRING` | `AzureWebJobsStorage` | Auto-generated from Storage Account primary key during `azd provision` | On storage-account regen (rare) | Functions runtime |
| `SQL-ADMIN-PASSWORD-BOOTSTRAP` | (not bound; consumed once via `sqlcmd` in Etapa 2) | Generated at provision time via Bicep `newGuid()` | Single-use; deleted after AAD-only flip in Etapa 2 | One-time schema apply |
| `SWA-FORWARDED-SECRET` | (not bound to Functions directly; injected by SWA platform as `X-SWA-Forwarded` header value via `staticwebapp.config.json` `forwardingGateway.requiredHeaders`) | Generated at provision time via Bicep `newGuid()` | Annual; on-incident rotation requires updating both the KV secret and the SWA `linked backend` config | SWA → Function App `linked backend` shared-secret hardening (see §8.2 bullet 4 and §8.3 threat row) |
| `POWERBI-SP-CLIENT-SECRET` | (not bound to Functions; consumed by GitHub Actions PBI deploy workflow) | Created with PowerBI app registration (Etapa 7) | Annual | PowerBI dataset deploy job |

Key Vault reference syntax used in Function App settings (Bicep `properties.siteConfig.appSettings`):

```bicep
{
  name: 'ANTHROPIC_API_KEY'
  value: '@Microsoft.KeyVault(SecretUri=${kvUri}secrets/ANTHROPIC-API-KEY/)'
}
```

Function App MI must have `Key Vault Secrets User` before the first reference resolves — order of operations in `main.bicep`:

1. Create KV (RBAC enabled, no secrets yet).
2. Create Function App (MI auto-provisioned).
3. Create RBAC role assignment: Function MI principal id → `Key Vault Secrets User` on KV.
4. Set secrets in KV (CI step, post-`azd provision`).
5. Update Function App settings with KV references — these resolve once both (3) and (4) are in place. A `Restart` is triggered on the Function App as the final step to force resolution.

---

## 8. Network architecture (honest free-tier assessment)

The free tier forces a public-network design. We document this trade-off explicitly rather than hide it.

### 8.1 What we cannot do

- **VNet integration on Consumption Functions**: blocked — requires Functions Premium plan (~$170/month base). Therefore the Function App's outbound traffic to SQL, KV, and Anthropic uses public endpoints (TLS-only).
- **Private endpoint on SQL Server**: blocked — requires a peered VNet, which requires VNet-integrated Functions. Same root cause.
- **Private endpoint on Key Vault**: same; not feasible without VNet integration.

### 8.2 What we do instead

- **SQL Server firewall**: `publicNetworkAccess: 'Enabled'`, `allowAzureServices: true` (this is the `0.0.0.0` virtual-rule that permits all Azure-internal IPs — including the Function App's outbound IPs — without naming them). At bootstrap (Etapa 2) we add the developer's home IP for `sqlcmd`; the rule is removed at the end of Etapa 2.
- **SQL authentication**: AAD-only after bootstrap (`administrators.azureADOnlyAuthentication: true`). Even though the network surface is public, only Function MI + the AAD admin user + the PBI service principal can authenticate. SQL auth (username/password) is disabled.
- **Key Vault networking**: `defaultAction: 'Allow'`, but RBAC + AAD authentication required. Anonymous reads are impossible.
- **Function App `/api/ask`**: anonymous at the platform level (no Functions-host AAD auth, because that complicates SWA linked-backend pass-through). Defense in depth:
  1. SWA `staticwebapp.config.json` requires AAD on `/api/*` — unauthenticated browsers cannot proxy through.
  2. The function code validates the `x-ms-client-principal` header on every request; missing or unparseable → HTTP 401.
  3. SWA's linked-backend route is the **only** documented path; the raw Function URL is omitted from documentation and clients.
  4. **SWA linked-backend hardening (shared-secret header)**: `staticwebapp.config.json` declares a `forwardingGateway.requiredHeaders` block that injects `X-SWA-Forwarded` with a secret value resolved from `kv-tcp-prod-weu` (KV secret `SWA-FORWARDED-SECRET`). The function rejects any request lacking the matching value with HTTP 403, regardless of whether the `x-ms-client-principal` header is well-formed. This blocks the trivial-forgery path where an attacker who finds the raw `func-tcp-prod-weu.azurewebsites.net/api/ask` URL crafts a base64-encoded principal blob (SWA-injected principal headers are not signed). The shared secret rotates with the same cadence as `ANTHROPIC-API-KEY`. Cross-reference: the `staticwebapp.config.json` change is owned by the SWA module (not this document); this section is the contract.
- **SWA → Function**: traffic stays within the SWA + Function platform via the Microsoft backbone; no public hop between them after the SWA terminates the user TLS.

### 8.3 Threat model

This is an academic / portfolio project hosting synthetic data only. The threat model is intentionally bounded:

| Threat | In scope? | Mitigation |
|---|---|---|
| Casual scraping of the public Function URL | Yes | Client-principal validation → 401 without SWA. |
| AAD credential stuffing on the SWA login | Yes | Default AAD protections (MFA recommended at the tenant level — TODO). |
| Determined attacker bypassing SWA + forging principal header | Yes | **Mitigated by shared-secret header**: `staticwebapp.config.json` injects `X-SWA-Forwarded` (value from KV) on the linked-backend path; the function rejects requests lacking the matching value with HTTP 403. Forged `x-ms-client-principal` blobs alone are not enough — the attacker would need to exfiltrate the shared secret from KV first. See §8.2 bullet 4. |
| Data exfiltration via the AI assistant | Yes | `safe_query.py` allowlists tables/columns and read-only views; no `tcp_ai_assistant` write access. |
| Anthropic API key leak | Yes | KV-only, no plaintext anywhere; rotate annually. |
| SQL injection through generated queries | Yes | LLM output is treated as untrusted SQL. `safe_query.py` parses it with `sqlglot`, allowlists tables (`v_*`) and read-only procs, rejects non-`SELECT` statements, forbids `UNION`/`INTO`/`WAITFOR`/`xp_*`/`OPENROWSET`/comments, enforces `TOP <= 1000`, and refuses unparseable input. Adversarial fixture (≥ 20 prompts) gates the contract in CI. See §6.4. |
| DoS via cost overrun (Anthropic) | Yes | Per-session rate limit in the function (10 questions/min/user); circuit-breaker at 1 000 tokens/question. |
| Tenant compromise (subscription takeover) | Out of scope | Beyond the thesis security model. |

If the project graduates to a "real" deployment, the migration path is: add VNet → switch Functions to Flex Consumption (cheaper VNet support than Premium) → add private endpoints. This is explicitly out of scope for the $0/month target.

---

## 9. CI/CD pipeline

Two workflows, both OIDC-authenticated.

### 9.1 `ci.yml` — on PR open / sync

Triggers: `pull_request` on any branch. Goal: every PR is verifiably safe before merge.

| Stage | Tool | Failure threshold | Introduced in |
|---|---|---|---|
| Checkout | `actions/checkout@v4` | n/a | E0 |
| Python setup | `actions/setup-python@v5` (3.12) + `uv` | n/a | E0 |
| Lint | `ruff check .` | any rule violation → fail | E0 |
| Format check | `ruff format --check .` | any reformatting needed → fail | E0 |
| Type check | `mypy --strict tcp/` | any type error → fail | E3 |
| Security scan (SAST) | `bandit -r tcp/` | any High severity → fail; Medium → warn | E0 |
| Dependency vuln scan | `pip-audit` | any CVE with severity ≥ High → fail | E0 |
| Secret scan | `gitleaks detect` | any finding → fail | E0 |
| Unit tests | `pytest --cov=tcp --cov=function_app --cov-fail-under=90` | <90 % coverage on `tcp/` + `function_app/` → fail | E3 (raised to 90 in E5/E8) |
| IaC lint | `bicep build main.bicep` | any error → fail | E4 |
| IaC policy scan | `psrule-for-azure` (rule-set `Azure.MCSB.v1`) | any **High** or **Critical** finding → fail | E4 |
| IaC policy scan (parallel) | `checkov --framework bicep` | any **High** or **Critical** finding → fail | E4 |
| IaC what-if | `az deployment group what-if --resource-group rg-tcp-prod-weu --template-file infra/main.bicep` (read-only diff against the production RG; runs only on `pull_request`) | non-trivial diff posted as PR comment for human review | E4 |

Total runtime budget: ≤ 10 minutes per PR (acceptance criterion in §17).

### 9.2 `cd.yml` — on push to `main`

Triggers: `push` on `main`, manual `workflow_dispatch`. Goal: deploy the production environment idempotently.

```text
Stages (sequential):
  1. lint+test (same as ci.yml, abbreviated)
  2. provision (azd provision --no-prompt)
  3. deploy   (azd deploy --no-prompt)
  4. smoke    (see below)
  5. notify   (post deploy summary to GitHub release notes)
```

Smoke tests (step 4):

- `curl -sf https://swa-tcp-prod-weu.azurestaticapps.net/api/ask -X POST -H 'Content-Type: application/json' -H 'Authorization: Bearer <test token>' -d '{"q":"How many traders are active?"}'` → expect HTTP 200 and a JSON `{ answer: ..., rows: [...] }`.
- Kusto query against App Insights: confirm the `TimerTrigger_DailyGenerator` has at least one `Succeeded` run in the last 24 h:

```kusto
requests
| where operation_Name == "TimerTrigger_DailyGenerator"
| where timestamp > ago(24h)
| where success == true
| count
```

→ Expect `Count >= 1` on weekdays after 07:01 RO. (On Mon morning before the first run, the check is allowed to return 0 → workflow conditionally skips this assertion.)

Manual approval gate: GitHub `environment: prod` with `required reviewers: [user]` — required before step 2.

---

## 10. Cost model

All figures assume the steady-state described in §4. Prices are list-price West Europe as of 2026-05.

| Resource | SKU | Free allowance | Expected use | Headroom | Risk of overage |
|---|---|---|---|---|---|
| SQL Database | GP_S_Gen5_1, Free Offer | 100 000 vCore-seconds/month + 32 GB storage | ~70 000 vCore-s/month + ~2 GB storage | 30 % vCore, ~94 % storage | Medium — track via Kusto (§12). Generator throughput could spike if synthetic config is changed. |
| Function App | Y1 Consumption | 1 M executions/month + 400 000 GB-s/month | ~1 530 executions, ~12 000 GB-s | >99 % | Negligible. |
| Static Web App | Free | 100 GB egress/month, 2 custom domains, 0.5 GB app size | <1 GB egress, 0 custom domains, <10 MB app | >99 % | Negligible. |
| Key Vault | Standard | First 10 000 ops/month effectively trivial cost | <100 ops/month | >99 % | Negligible. |
| Storage Account | Standard_LRS | 5 GB + 20 000 R/W ops first 12 months | <500 MB + ~500 ops/month | >90 % | Low. After 12-month free period, ~$0.05/month — acceptable rounding. |
| Application Insights / Log Analytics | PerGB2018 | 5 GB ingestion/month + 31-day retention | 1–2 GB/month | 60 % | Medium — chatty Python loggers can balloon ingestion. |
| Egress | — | 100 GB free/month outbound | <2 GB/month | >98 % | Negligible. |
| **Total** | | | | | **$0/month at steady state.** |

Note (Y1 plan): App Service Plan Y1 (Consumption) carries no plan-level charge — charges are folded into the Function App row above. It is listed as a separate resource in §2 but is not a separate line in this cost table to avoid three "n/a" cells.

Anthropic API pricing — `claude-haiku-4-5`, USD per token, list price as of 2026-05 (publication date), convert to EUR via tenant FX:

| Token bucket | Rate (USD/token) | Comment |
| --- | --- | --- |
| Input | 1.0e-6 | Standard input tokens (system + user messages, no cache hit). |
| Output | 5.0e-6 | Generated tokens (SQL + answer_template). |
| Cache read | 0.1e-6 | Cached schema-context tokens (~10 % of input rate per Anthropic billing). |
| Cache write | 1.25e-6 | First-time write of a cache block (one-off per deploy). |

Cost projections in §12.2 query 3 use these rates as USD; the Kusto extends with `est_eur = … (converted via tenant FX)`. A future Anthropic price change will diverge from this table — re-pin on update.

### 10.1 Mitigation playbook

| Signal | Threshold | Action |
|---|---|---|
| SQL vCore-seconds projection at month-end | > 80 000 | Reduce generator trade volume by 30 %; switch some `/api/ask` queries to use a pre-aggregated `v_employee_performance` instead of the raw `fact_Trades` view. |
| SQL storage usage | > 28 GB | Run `usp_PurgeOldTrades(@cutoff_date)` to drop trades older than 18 months (synthetic data, no compliance hold). |
| App Insights ingestion (rolling 7-day projection) | > 4 GB/month | Lower adaptive sampling to fixed `samplingPercentage: 5` in `applicationinsights.json`; suppress verbose logger `azure.identity` to WARNING. |
| Function GB-s | > 300 000 | Increase function timeout cap from 5 min to 2 min; remove debug-mode tracing. |
| Anthropic input tokens / day | > 100 000 | Verify prompt caching is hitting; reduce schema-context size; tighten `safe_query` retry policy (no retries on `ToolUseException`). |
| Storage egress | > 4 GB / 30 days | Move BACPAC retention from 4 weeks to 2 weeks; compress with `gzip` before upload. |

All thresholds are encoded as Azure Monitor alerts in `modules/observability.bicep` (Etapa 4 deliverable), with email notifications to the user.

---

## 11. Disaster recovery & backups

| Layer | Mechanism | RPO | RTO | Cost |
|---|---|---|---|---|
| SQL data | Built-in PITR (Free Offer): full backup weekly + diff every 12 h + log every 5 min, 7-day window | ≤ 5 min | ≤ 30 min (restore to new database, swap connection string) | $0 |
| SQL data (long-term) | Weekly BACPAC export to `bacpac-exports` container via `TimerTrigger_BacpacExport` (`0 0 8 * * 0` — Sunday 08:00 RO). The Function App MI invokes `New-AzSqlDatabaseExport` (granted via `SQL DB Contributor` at DB scope — see §5), polls the async operation URL until completion, and emits `tcp.bacpac.duration_ms` / `tcp.bacpac.size_bytes` metrics. This is the **canonical** BACPAC path — the older "GitHub Actions Sunday 02:00 UTC" plan in `02_DB §12` is superseded; that document references this timer trigger going forward. See `02_DB §12` for the BACPAC restoration procedure. | 7 days | ≤ 60 min (import BACPAC) | $0 |
| Code | GitHub `main` branch; `azd up` recreates the full RG from scratch | 0 (everything in repo) | ≤ 20 min (cold deploy) | $0 |
| Secrets | KV soft-delete (7-day retention) + manual rotation playbook | 0 (regenerable) | ≤ 5 min | $0 |
| Static UI | GitHub `main` (deploy-on-push) | 0 | ≤ 2 min (rebuild SWA) | $0 |

**Restore drill** (one-time, before Etapa 10 hand-in):

1. From the Azure Portal, choose `sqldb-tcp-prod-weu` → **Restore** → PITR to 1 h ago → new DB name `sqldb-tcp-prod-weu-restore-drill`.
2. Verify row counts: `SELECT COUNT(*) FROM fact_Trades` on both DBs.
3. Drop the restore-drill DB.
4. Document the wall-clock time and any issues in `docs/runbooks/restore_drill.md`.

The drill is required to validate the RTO claim of ≤ 30 min.

---

## 12. Observability

### 12.1 Per-component emission

| Component | Logs | Metrics | Traces |
|---|---|---|---|
| Function App (TimerTrigger) | `info` start/finish; `warn` if SQL resume > 30 s; `error` on any exception | `tcp.generator.rows_inserted`, `tcp.generator.duration_ms`, `tcp.sql.resume_ms` | one root span per invocation; child spans for SQL ops |
| Function App (HttpTrigger) | `info` per request (without PII); `error` on Anthropic non-2xx | `tcp.anthropic.input_tokens`, `tcp.anthropic.output_tokens`, `tcp.anthropic.cache_read_tokens`, `tcp.assistant.latency_ms` | root span per request; child spans for KV fetch, Anthropic call, SQL exec |
| SQL Database | Auditing OFF by default (free tier; chatty) — limited to failed logins + `DBCC SHRINK` events | `cpu_percent`, `storage_percent`, `connection_failed_count` via diagnostic settings to LA | n/a (no app-level instrumentation in SQL) |
| Key Vault | Diagnostic logs `AuditEvent` to LA | none (rare ops) | n/a |
| Static Web App | Platform request logs to LA (sampled) | request count, latency, 4xx/5xx rate | n/a |
| Storage Account | Diagnostic logs for blob R/W to LA at `Verbose` only during incidents | egress bytes, transactions | n/a |

### 12.2 Kusto queries the user will keep

```kusto
// 1. /api/ask end-to-end latency p50/p95/p99 (last 7 days)
requests
| where operation_Name == "HttpTrigger_AskAssistant"
| where timestamp > ago(7d)
| summarize p50=percentile(duration, 50), p95=percentile(duration, 95), p99=percentile(duration, 99) by bin(timestamp, 1h)
| render timechart
```

```kusto
// 2. Daily generator outcomes (last 30 days)
requests
| where operation_Name == "TimerTrigger_DailyGenerator"
| where timestamp > ago(30d)
| summarize Runs=count(), Successes=countif(success == true), Failures=countif(success == false) by bin(timestamp, 1d)
| order by timestamp asc
```

```kusto
// 3. Anthropic token usage and cost projection (last 30 days, EUR estimate)
customMetrics
| where name in ("tcp.anthropic.input_tokens", "tcp.anthropic.output_tokens", "tcp.anthropic.cache_read_tokens")
| where timestamp > ago(30d)
| summarize total=sum(value) by name, bin(timestamp, 1d)
| evaluate pivot(name, sum(total))
| extend est_eur = ((1.0 * input_tokens) * 0.000001 + (5.0 * output_tokens) * 0.000001 + (0.1 * cache_read_tokens) * 0.000001)
| project timestamp, input_tokens, output_tokens, cache_read_tokens, est_eur
```

```kusto
// 4. Function cold-start frequency (Python worker init > 2 s)
traces
| where customDimensions.Category == "Host.Startup"
| where timestamp > ago(7d)
| where message has "Worker process started"
| extend startup_ms = todouble(customDimensions.StartupDurationMs)
| summarize cold_starts=countif(startup_ms > 2000), warm_starts=countif(startup_ms <= 2000) by bin(timestamp, 1h)
```

```kusto
// 5. SQL vCore-seconds consumption vs free quota
AzureMetrics
| where ResourceProvider == "MICROSOFT.SQL"
| where MetricName == "app_cpu_billed"
| where TimeGenerated > startofmonth(now())
| summarize used_vcore_seconds = sum(Total) by bin(TimeGenerated, 1d)
| extend remaining = 100000 - used_vcore_seconds
| order by TimeGenerated asc
```

```kusto
// 6. Error rate by operation (last 24 h)
requests
| where timestamp > ago(24h)
| summarize total=count(), failed=countif(success == false) by operation_Name
| extend error_rate_pct = round(100.0 * failed / total, 2)
| order by error_rate_pct desc
```

```kusto
// 7. Last 50 distinct /api/ask questions (PII-redacted; hash-of-question only).
// Proves the §15 R7 audit hook is wired and prompts are not silently dropped.
traces
| where customDimensions.Category == "tcp.assistant.audit"
| where timestamp > ago(30d)
| extend q_hash = tostring(customDimensions.question_sha256)
| summarize last_seen = max(timestamp), occurrences = count() by q_hash
| top 50 by last_seen desc
```

```kusto
// 8. BACPAC export weekly health — duration and size; alerts on missing run.
customMetrics
| where name in ("tcp.bacpac.duration_ms", "tcp.bacpac.size_bytes")
| where timestamp > ago(30d)
| summarize avg_value = avg(value), runs = count() by name, bin(timestamp, 7d)
| order by timestamp desc
// Alert rule: if no row for "tcp.bacpac.duration_ms" appears in the last 8 days,
// raise "BACPAC export missed scheduled run".
```

These queries are saved to the App Insights workspace as **Saved Queries** during Etapa 8, with names prefixed `tcp.` for discoverability.

---

## 13. Compliance & data residency

- **Data residency**: all data at rest is in `West Europe` (Amsterdam) Azure region, falling back to `North Europe` (Dublin) if necessary. Both are inside the EU.
- **Backups** (PITR, BACPAC) stay in the same region — Free Offer does not support geo-redundant backups.
- **PII**: zero real PII. All employee data is generated via `Faker(locale="ro_RO")` with a `@tcp-capital.ro` synthetic email domain. Documented in `02_database_design.md` §Synthetic data.
- **GDPR posture**: even though no real data subjects exist, the design includes a future hook for right-to-erasure:

  ```sql
  CREATE PROCEDURE usp_DeleteEmployeeData(@employee_id INT)
  AS BEGIN
    -- Stub for future GDPR compliance. Cascade-deletes dim_Employees + trades.
    -- Not invoked in the thesis project; documented for completeness.
    RAISERROR ('Not implemented in MVP; see docs/decisions/ADR-XXX-gdpr.md', 16, 1);
  END
  ```

- **Audit logs**: Key Vault `AuditEvent` and SQL `failed_login_event` are routed to LA and retained 30 days — covers basic forensic needs for the academic project.
- **Encryption**: at rest (SQL TDE on by default, Storage Account SSE on by default, KV HSM-backed keys), in transit (TLS 1.2 minimum on every endpoint).
- **Data classification**: all data is **synthetic / public** — no internal/restricted/confidential data exists in the system. Documented under `docs/decisions/ADR-XXX-data-classification.md` (future).

---

## 14. Performance budgets

| Budget | Target | How the infra meets it |
|---|---|---|
| `/api/ask` **warm path** | ≤ 1.5 s p95 | KV reference cached (no per-request KV hit), Anthropic prompt caching reduces first-token latency to ~400 ms, SQL query bounded to 1000 rows. This is the dominant scenario when the user clicked "Wake up the database" first or when the `WarmupTrigger` ran in-window. |
| `/api/ask` **cold path** (post-pause, no warmup) | ~35 s p95 | Python worker init ~1.5 s + SQL resume ~30–60 s + first-query overhead. Explicitly outside the warm-path budget; SWA exposes a "Wake up the database" affordance (`/api/ping`) so the user can pre-warm the DB before the first real question. |
| Timer generator end-to-end | ≤ 60 s including SQL auto-resume | One transaction, ~200 rows, indexed inserts. SQL resume budget ~45 s. |
| SWA initial page load | ≤ 2 s | Vanilla HTML/JS (no framework), ≤ 50 KB JS, CDN-fronted by SWA platform. |
| PowerBI Scheduled Refresh | ≤ 5 min | Refresh on aggregated `v_*` views (≤ 50 k rows per refresh) using **Import mode** — full snapshot pulled at 07:30 RO. |
| Bicep `azd up` cold deploy | ≤ 20 min on empty RG (TODO: measure on first deploy and update with empirical mean ± stddev across 3 deploys) | Sequential module deploys; KV + SQL are the slow ones (~5 min each). |
| CI workflow runtime | ≤ 10 min | Parallel jobs for lint/test/IaC; pre-built Python cache. |

If any budget regresses, the runbook in `docs/runbooks/perf_regression.md` (future deliverable, Etapa 8) is invoked.

---

## 15. Risks & open questions

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | SQL auto-pause cold-start (~30–60 s) stacking with Function cold-start (~1.5 s) makes the first interactive `/api/ask` slow | High | Medium (user-visible latency) | Two-layered mitigation: (1) `WarmupTrigger` at `0 55 6 * * 1-5` (06:55 RO weekdays) runs `SELECT 1` against SQL ahead of the daily generator and the 09:00 user load; (2) `HttpTrigger_Ping` at `/api/ping` exposes a "Wake up the database" button in the SWA — the user clicks before the first real question, returning `{ sql_resume_ms, status }`. Selected over a 24/7 keep-alive timer because perpetual pings would burn the 100 000 vCore-second monthly budget. Performance budget reflects this: warm path ≤ 1.5 s p95; cold path ~35 s p95 if the user did not click "wake up". |
| R2 | Anthropic API rate limits hit during demo | Medium | High (live demo failure) | Cache the daily demo questions and stub the API response if the rate limit is breached (`X-RateLimit-Remaining: 0`). |
| R3 | SQL Free Offer is not available in `westeurope` at provision time | Low | Low (mechanical fallback) | Bicep parameter `location` defaults to `westeurope`, auto-falls back to `northeurope` if the SQL Free Offer SKU is unavailable (probed via `az sql db list-editions`). |
| R4 | Static Web App custom domain not free if used aggressively | Low | Low | Stick with the auto-generated `*.azurestaticapps.net` URL; document the custom domain step as optional (Etapa 9). |
| R5 | PowerBI service principal scopes change between tenants | Medium | Medium (Etapa 7 blocked) | ADR-001 already locked in the REST API path; service principal needs `Tenant.Read.All` + workspace-scoped contributor — documented for the user to grant during Etapa 7. |
| R6 | Free Offer changes (e.g., quota reduction by Microsoft) | Low | High (cost overrun) | Monthly review of the Azure update RSS feed; pinned Bicep `apiVersion` so behavior is stable; alert on `useFreeLimit` resource property drift. |
| R7 | Anthropic prompts inadvertently include real-looking PII | Low | Medium (academic ethics) | All data is synthetic; `safe_query.py` validates that returned rows are from synthetic tables only; prompt audit log retained 30 days in App Insights. |
| R8 | `azd down` leaves orphaned resources due to soft-delete on KV | Medium | Low (annoying cleanup) | Documented workaround in `docs/runbooks/azd_down.md` (Etapa 4): `az keyvault purge --name kv-tcp-prod-weu` after `azd down`. |

Open questions deferred to Etapa 4:

- Should the BACPAC export use a separate user-assigned MI for blob writes, or can the system-assigned MI handle it? (Lean: system-assigned; revisit if cross-resource MI lifecycle becomes a pain point.)
- Should we adopt Azure Front Door in front of SWA? **No** — it is not on the free tier; SWA already includes a CDN.

---

## 16. azd / Bicep module map

| Module | Path | Owns resource(s) | Key params | Outputs consumed by |
|---|---|---|---|---|
| `main.bicep` (root) | `infra/main.bicep` | Resource Group (deploy target), tag baseline, module orchestration | `location`, `env`, `tags` | (root entrypoint; outputs consumed by `azure.yaml` for `azd env get-values`) |
| `keyvault.bicep` | `infra/modules/keyvault.bicep` | `kv-tcp-prod-weu`, secret resources (placeholder values), MI → `Key Vault Secrets User` role assignment | `name`, `location`, `tenantId`, `tags`, `miPrincipalId` | `functions.bicep` (vault URI + secret URIs) |
| `storage.bicep` | `infra/modules/storage.bicep` | `sttcpprodweu`, `bacpac-exports` container, lifecycle rule, MI → `Storage Blob Data Contributor` role assignment on `bacpac-exports` | `name`, `location`, `tags`, `miPrincipalId` | `functions.bicep` (connection string secret), `observability.bicep` |
| `sql.bicep` | `infra/modules/sql.bicep` | `sql-tcp-prod-weu`, `sqldb-tcp-prod-weu`, firewall rules, AAD admin assignment, MI → `SQL DB Contributor` role assignment (DB scope) for BACPAC export. **API version pinned**: `Microsoft.Sql/servers/databases@2023-08-01-preview`. | `serverName`, `dbName`, `location`, `tags`, `aadAdminObjectId`, `miPrincipalId` | `functions.bicep` (server FQDN + DB name) |
| `functions.bicep` | `infra/modules/functions.bicep` | `asp-tcp-prod-weu` (Y1), `func-tcp-prod-weu` + system-assigned MI, app settings with KV references | `name`, `planId`, `storageConnSecretUri`, `kvUri`, `sqlServer`, `sqlDb`, `aiConnectionString`, `miClientId` | `swa.bicep` (function hostname), `observability.bicep` |
| `swa.bicep` | `infra/modules/swa.bicep` | `swa-tcp-prod-weu`, linked-backend binding to `func-tcp-prod-weu`, `staticwebapp.config.json` baseline (including `forwardingGateway.requiredHeaders` for `X-SWA-Forwarded`) | `name`, `location`, `tags`, `functionAppResourceId`, `swaForwardedSecretUri` | (terminal — consumed by user via SWA URL) |
| `observability.bicep` | `infra/modules/observability.bicep` | `log-tcp-prod-weu`, `ai-tcp-prod-weu`, alert rules from §10.1 | `name`, `location`, `tags`, `dailyQuotaGb` | `functions.bicep` (instrumentation key / connection string) |

**`identity.bicep` decision (MN-11)**: dropped. Role assignments **must** live in the scope they target (KV / SQL / Storage), so a centralised `identity.bicep` would either become a god-module with `module` blocks per scope or remain a placeholder. We inline each role assignment inside its target module (see "owns" column above) and pass `miPrincipalId` as a parameter from `main.bicep`. This keeps each module self-contained and `azd what-if` diffs scoped to one file per resource concern.

`azure.yaml` wiring (Etapa 4 deliverable, shown here for completeness):

```yaml
name: tcp-trading-central-panel
metadata:
  template: tcp@1.0.0
services:
  api:
    project: ./functions
    language: python
    host: function
  web:
    project: ./static
    language: html
    host: staticwebapp
hooks:
  postprovision:
    shell: pwsh
    run: ./infra/scripts/postprovision.ps1   # seed AAD-only auth on SQL, set secret values
```

---

## 17. Acceptance checklist for Etapa 4

Each item is testable in CI. Etapa 4 is "done" when all CI-gated bullets are green; the manual Day-7 follow-ups in §17.2 are tracked separately.

### 17.1 CI-gated (must be green to merge)

- [ ] `azd up` succeeds in ≤ 20 min on a clean RG, no manual intervention beyond OIDC login.
- [ ] `azd down` cleanly removes all resources in the RG (including soft-deleted KV via documented purge step).
- [ ] All Bicep modules pass `bicep build` with zero warnings **and** zero `psrule-for-azure` (rule-set `Azure.MCSB.v1`) High/Critical findings **and** zero `checkov --framework bicep` High/Critical findings.
- [ ] `az deployment group what-if` on an idempotent re-deploy reports zero changes.
- [ ] The Function App's system-assigned MI reads `ANTHROPIC-API-KEY` from KV (Kusto: `traces | where message contains "Resolved Key Vault reference ANTHROPIC_API_KEY"`).
- [ ] The Function App's MI authenticates to SQL via AAD and runs `SELECT 1` successfully (smoke test in `cd.yml`).
- [ ] The `TimerTrigger_DailyGenerator` fires at the next 07:00 RO weekday and inserts ≥ 100 rows into `fact_Trades`.
- [ ] The `HttpTrigger_AskAssistant` returns HTTP 401 on a request lacking the `x-ms-client-principal` header.
- [ ] The `HttpTrigger_AskAssistant` returns HTTP 200 + JSON on a valid SWA-forwarded request.
- [ ] SWA AAD sign-in flow completes end-to-end in a private browser session.
- [ ] CI workflow (`ci.yml`) completes in ≤ 10 min on a representative PR.
- [ ] `gitleaks` reports zero findings on the full repo history.
- [ ] `bandit` reports zero High-severity findings on `tcp/`.
- [ ] `pip-audit` reports zero High-severity CVEs.
- [ ] `mypy --strict tcp/` exits 0.
- [ ] `pytest` coverage ≥ 80 % on `tcp/`.
- [ ] **A.1 (RLS round-trip)**: A logged-in user with `scope='trader'` issuing `SELECT COUNT(*) FROM v_trades_enriched` via `/api/ask` returns exactly the count of their own trades (verified against a known fixture employee). The Function App emits `tcp.rls.session_context_set=true` per request.
- [ ] **A.2 (RLS contract — no `EXECUTE AS OWNER`)**: A CI grep over the deployed DB definitions fails the build if any view contains `EXECUTE AS OWNER` or `WITH SCHEMABINDING` in combination with a pre-evaluated predicate on `fact_Trades`.
- [ ] **A.3 (OIDC subjects)**: `azd` deploy log shows successful OIDC exchange on a `main` push **and** on a PR run, using two different federated credentials (no overlap of subject claims).
- [ ] **A.4 (`safe_query.py` adversarial)**: Adversarial test suite (≥ 20 prompts including `UNION SELECT`, `; DROP`, `xp_cmdshell`, `OPENROWSET`, comment-bypass, encoded payloads, RLS-bypass attempts) all return HTTP 400 / refusal.
- [ ] **A.5 (BACPAC export)**: On the first Sunday post-deploy, a BACPAC file appears in `bacpac-exports/`; `tcp.bacpac.duration_ms` and `tcp.bacpac.size_bytes` metrics appear in App Insights.
- [ ] **A.6 (`/api/ping`)**: The endpoint returns HTTP 200 + `{ sql_resume_ms: <int>, status: "warm"|"resumed" }` for an unauthenticated caller; the SWA "Wake up the database" button invokes it and `tcp.sql.resume_ms` is emitted.
- [ ] **A.7 (SWA shared-secret forgery block)**: A `curl` against the raw `func-tcp-prod-weu.azurewebsites.net/api/ask` URL with a hand-crafted `x-ms-client-principal` header but missing the `X-SWA-Forwarded` shared secret returns HTTP 403 (not 401, not 200).
- [ ] **A.8 (AAD-only flip)**: After deploy, `SQL-ADMIN-PASSWORD-BOOTSTRAP` is absent from `kv-tcp-prod-weu` (Kusto: `AzureDiagnostics | where Category == "AuditEvent" | where OperationName == "SecretDelete"` shows the deletion event); `Set-AzSqlServerActiveDirectoryOnlyAuthentication` returned `IsEnabled = true` on the server.

### 17.2 Day-7 manual follow-ups (not CI-gated)

The following bullets cannot be verified in the CD pipeline because they require elapsed time after deploy. The user records the outcome in `docs/runbooks/day7_review.md` one week post-deploy.

- [ ] All Kusto queries from §12.2 return non-empty results within 24 h of first deploy.
- [ ] The cost dashboard (Azure Cost Management) shows projected MTD cost = $0 ± $0.01 by day 7 post-deploy.

When the CI-gated checklist (§17.1) is fully green, Etapa 4 is locked, ADR-XXX-etapa-4-iac.md is filed, and STATE.md advances to Etapa 5. The §17.2 follow-ups close out as part of Etapa 6 sign-off.

---

*End of `03_architecture.md`. Companion documents: `01_business_requirements.md`, `02_database_design.md`, `docs/diagrams/architecture.mmd` (produced by `mermaid-expert`).*
