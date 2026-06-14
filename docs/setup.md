# Setup walkthrough — clone to live deploy

This is the canonical end-to-end walkthrough for taking the TCP — Trading Central Panel project from a fresh `git clone` to a working `azd up` deploy with green smoke tests. It is split into **two tracks** because the project has two distinct lifecycles:

- **Track A — local development** (loop on tests, run the synth generator against Docker SQL, no Azure costs). For day-to-day work and reviewer reproductions. The detailed reference is [`dev_setup.md`](dev_setup.md); this page summarises the happy path.
- **Track B — Azure deploy** (provision the production-shaped environment via `azd`, run the postprovision sequence, verify the assistant end-to-end). Required to validate the deploy story before the thesis defence; runs on the free tier and stays at €0/month with the academic-load profile.

If you only need to demo the dashboard + assistant, do **Track A → Track B** in order. If you only need to run the test suite, **Track A** is sufficient.

---

## Track A — local development (15 minutes)

### A.1 Prerequisites

| Tool | Minimum version | Install |
|---|---|---|
| Python | 3.12 | [python.org](https://www.python.org/downloads/) or OS package manager |
| `uv` | 0.6+ | `curl -LsSf https://astral.sh/uv/install.sh | sh` (POSIX) or `irm https://astral.sh/uv/install.ps1 | iex` (Windows) |
| Docker | 24+ | Docker Desktop or `apt install docker-ce` |
| `sqlcmd` | 18+ | `brew install sqlcmd` / `choco install sqlcmd` / `apt install mssql-tools18` |
| Git | 2.40+ | OS package manager |

Verify everything is on PATH:

```bash
python --version    # Python 3.12.x
uv --version        # uv 0.6.x
docker --version
sqlcmd -?           # prints sqlcmd help
git --version
```

### A.2 Clone + dependencies

```bash
git clone <repo-url> tcp_trading_central_panel
cd tcp_trading_central_panel
uv sync --all-extras
```

`uv sync` creates a `.venv` in the repo root and installs every dependency declared in `pyproject.toml` (production + dev extras). The lockfile is `uv.lock`.

### A.3 Local SQL Server

Each command block below is provided in both POSIX (bash / zsh) and PowerShell flavours per the project's dual-track convention (`CLAUDE.md` § Communication language). Pick one and stay consistent — mixing `export` and `$env:` in the same shell session breaks variable resolution.

**POSIX (bash / zsh)**

```bash
# 1. Start the container (background)
export TCP_SQL_DEV_PASSWORD='YourStrong!Passw0rd'
docker compose -f docker-compose.dev.yml up -d

# 2. Wait for healthcheck — ~30 s
docker ps    # status should reach `healthy`

# 3. Create the dev database
docker exec tcp-sql-dev /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa \
  -P "$TCP_SQL_DEV_PASSWORD" -C -Q "CREATE DATABASE tcp_dev"

# 4. Apply V001 + V002 (placeholder substitution not required for local apply —
#    the schema_history row records a sentinel that is unfit for production
#    but harmless in dev)
sqlcmd -S localhost,1433 -U sa -P "$TCP_SQL_DEV_PASSWORD" -d tcp_dev \
  -i db/migrations/V001__init.sql -b -C
sqlcmd -S localhost,1433 -U sa -P "$TCP_SQL_DEV_PASSWORD" -d tcp_dev \
  -i db/migrations/V002__synth_logic.sql -b -C
```

**PowerShell**

```powershell
# 1. Start the container (background)
$env:TCP_SQL_DEV_PASSWORD = 'YourStrong!Passw0rd'
docker compose -f docker-compose.dev.yml up -d

# 2. Wait for healthcheck — ~30 s
docker ps    # status should reach `healthy`

# 3. Create the dev database
docker exec tcp-sql-dev /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa `
  -P "$env:TCP_SQL_DEV_PASSWORD" -C -Q "CREATE DATABASE tcp_dev"

# 4. Apply V001 + V002 (placeholder substitution not required for local apply)
sqlcmd -S localhost,1433 -U sa -P "$env:TCP_SQL_DEV_PASSWORD" -d tcp_dev `
  -i db/migrations/V001__init.sql -b -C
sqlcmd -S localhost,1433 -U sa -P "$env:TCP_SQL_DEV_PASSWORD" -d tcp_dev `
  -i db/migrations/V002__synth_logic.sql -b -C
```

> **Why the sentinel is harmless locally**: the CD smoke job in [`.github/workflows/cd.yml`](../.github/workflows/cd.yml) asserts the production `schema_history.checksum` row is *not* the sentinel; the local apply never goes through that path. Production goes through `infra/scripts/postprovision.{ps1,sh}` Step 0, which invokes [`scripts/render_migration.py`](../scripts/render_migration.py) to substitute the real SHA-256 before piping to sqlcmd. See [ADR-related notes](../docs/security/threat_model.md) RR-09 for the full integrity chain.

### A.4 Run the test suite

```bash
# Unit tests — fast, no env required
uv run pytest tests/unit -v

# PII redaction sanity test — lives under tests/integration/ but has no live-env deps
uv run pytest tests/integration/test_telemetry_no_pii.py -v

# Live integration tests — opt-in
export TCP_SQL_SERVER='localhost,1433'
export TCP_SQL_DATABASE='tcp_dev'
export TCP_SQL_DEV_USER='sa'
# Anthropic key only required for tests/integration/test_ask_endpoint.py
export ANTHROPIC_API_KEY='sk-ant-...'
uv run pytest tests/integration -m integration -v

# SQL schema tests
sqlcmd -S localhost,1433 -U sa -P "$TCP_SQL_DEV_PASSWORD" -d tcp_dev \
  -i tests/sql/test_naming_convention.sql -b -C
sqlcmd -S localhost,1433 -U sa -P "$TCP_SQL_DEV_PASSWORD" -d tcp_dev \
  -i tests/sql/test_rls_smoke.sql -b -C
sqlcmd -S localhost,1433 -U sa -P "$TCP_SQL_DEV_PASSWORD" -d tcp_dev \
  -i tests/sql/test_fx_rate_completeness.sql -b -C
```

Expected outcome: every test passes. The 1 pre-existing safe_query failure (`test_proc_invoked_as_function_is_rejected`) and 14 pre-existing test_seed_employees errors are tracked separately for Etapa-12 polish — they are *not* introduced by Track A and can be ignored while reproducing the academic build.

### A.5 Lint + type-check

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy tcp tests
uv run sqlfluff lint db/ tests/sql/ --dialect tsql
```

All four should exit clean.

> **Stop here if you only need a local reproduction.** Track A produces a fully-functional dev environment for editing, testing, and reviewing — no Azure account required.

> **Track A → Track B handoff (tut-MA-03)**: before starting Track B in the *same* shell, unset the Track A connection variables so the Track B smoke step does not accidentally point at the local Docker SQL:
> ```bash
> # POSIX
> unset TCP_SQL_SERVER TCP_SQL_DATABASE TCP_SQL_DEV_USER TCP_SQL_DEV_PASSWORD
> ```
> ```powershell
> # PowerShell
> Remove-Item Env:TCP_SQL_SERVER, Env:TCP_SQL_DATABASE, Env:TCP_SQL_DEV_USER, Env:TCP_SQL_DEV_PASSWORD -ErrorAction SilentlyContinue
> ```

---

## Track B — Azure deploy (60–90 minutes end-to-end)

**Time breakdown** (single-operator first run, no prior cached state):

| Phase | Wall time |
|---|---|
| B.1 prerequisites (assume `az` + `azd` already installed) | 5 min |
| B.2 one-time OIDC + RBAC setup | 5 min |
| B.3 `azd up` (provision + postprovision + deploy) | 25–35 min |
| B.4 PowerBI runbook (separate path) | 30–45 min (see [`runbooks/powerbi_deploy.md`](runbooks/powerbi_deploy.md)) |
| B.5 smoke test + B.6 dashboard sanity check | 5 min |

Skip B.4 for the deploy-only path. The PowerBI deploy is **not** required for the assistant or the operations workbook to work — it is required only for the dashboard chapter of the thesis demo.

### B.1 Azure prerequisites

| Resource | Required for | Where it's provisioned |
|---|---|---|
| Azure subscription with `Owner` at subscription scope | Resource group creation, role assignments | Your Azure tenant |
| AAD app registration with OIDC federated credential | GitHub Actions `cd.yml` workflow | One-time manual setup (see [`docs/design/03_architecture.md`](design/03_architecture.md) §6.1) |
| Anthropic API key | `/api/ask` LLM calls | [console.anthropic.com](https://console.anthropic.com/) |

Install `azd`:

```bash
curl -fsSL https://aka.ms/install-azd.sh | bash       # POSIX
powershell -ex AllSigned -c "Invoke-RestMethod 'https://aka.ms/install-azd.ps1' | Invoke-Expression"   # Windows
azd version
```

### B.2 OIDC federated credential (one-time setup)

```bash
# Create the AAD app + service principal
APP_ID=$(az ad app create --display-name "tcp-cd" --query appId -o tsv)
az ad sp create --id "$APP_ID"

# Capture the SP object id — `az role assignment create` accepts an appId
# for --assignee in many tenants but the documented contract is the SP
# object id (docs-MA-03). Pinning to the object id removes the tenant-
# dependent auto-resolution and prevents the role assignment from silently
# targeting the wrong principal.
SP_OID=$(az ad sp show --id "$APP_ID" --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
SUB_ID=$(az account show --query id -o tsv)

# Federated credential bound to the `prod` environment on this repo
cat > federated-credential.json <<EOF
{
  "name": "tcp-cd-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<your-gh-org>/tcp-trading-central-panel:environment:prod",
  "audiences": ["api://AzureADTokenExchange"]
}
EOF
az ad app federated-credential create --id "$APP_ID" --parameters @federated-credential.json

# Grant the SP Owner at subscription scope (academic phase; tighten in
# production). `--assignee-object-id` is the unambiguous form.
az role assignment create \
  --assignee-object-id "$SP_OID" \
  --assignee-principal-type ServicePrincipal \
  --role "Owner" \
  --scope "/subscriptions/$SUB_ID"
```

Store `APP_ID`, `SUB_ID`, and the tenant id in the GitHub repository's Actions variables: `AZURE_CLIENT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`. These are read by [`.github/workflows/cd.yml`](../.github/workflows/cd.yml). Also store the `ANTHROPIC_API_KEY` as a repository secret.

### B.3 Local `azd` deploy (the canonical path)

> **⚠ READ BEFORE RUNNING `azd up`. The bootstrap window (RR-08).**
> Between Step 0 of postprovision (when SQL auth is alive on a public endpoint with a high-entropy password) and Step 3 (AAD-only flip), there is a 3–8 minute window where SQL auth is enabled. The window is documented in [`docs/security/threat_model.md`](security/threat_model.md) RR-08 and [`docs/security/bootstrap_window.md`](security/bootstrap_window.md). Stay attached to the terminal until the postprovision output confirms Step 3 + Step 5 succeeded — only then is it safe to step away.

Re-use the same OIDC SP locally for parity with CI:

```bash
azd auth login
azd env new tcp-prod --location westeurope

# Required secrets
azd env set ANTHROPIC_API_KEY 'sk-ant-...'

# Optional: enable email paging on alerts
azd env set NOTIFICATION_EMAILS '["operator@example.com"]'

# Provision + deploy + postprovision in one go
azd up
```

`azd up` runs three phases in order:

1. **`azd provision`** — compiles `infra/main.bicep`, executes a subscription-scope deployment that creates the resource group + every module:
   - `observability` (Log Analytics + App Insights)
   - `storage` (BACPAC container + lifecycle policy)
   - `functions` (Y1 plan + Function App + system MI)
   - `storage_rbac` (Function MI → Storage Blob Data Contributor)
   - `sql` (SQL server + Free Offer database)
   - `keyvault` (KV + secrets + RBAC for Function MI + OIDC SP)
   - `swa` (Static Web App + linked backend)
   - `workbook` (Operations dashboard)
   - `alerts` (8 Azure Monitor alert rules; conditional action group)
2. **postprovision hooks** ([`infra/scripts/postprovision.{ps1,sh}`](../infra/scripts/)) — the eight-step idempotent bootstrap. Each step's authoritative implementation lives in the script; the list below is the canonical reading order:
   - **Step 0**: Apply V001 + V002 with SHA-256 placeholders substituted by [`scripts/render_migration.py`](../scripts/render_migration.py); `MERGE … WITH (HOLDLOCK)` upserts the `schema_history` row.
   - **Step 1**: Register the Function App MI in `dim_UserRoles` with `scope='admin'` (RLS temporarily disabled, re-enabled in the `finally` block + a final defensive ALTER).
   - **Step 2**: Set the `TCP_GENERATOR_OID` Function App setting (the AAD `oid` the timer trigger writes into SESSION_CONTEXT on every connection).
   - **Step 2b**: Restart the Function App so the worker pool reloads with the new setting.
   - **Step 2c**: Substitute `<TENANT_ID>` + `<value-set-by-postprovision>` placeholders in [`swa/staticwebapp.config.json`](../swa/staticwebapp.config.json) so the SWA AAD provider + forwarded-secret header match the deployed environment.
   - **Step 3**: Flip the SQL server to `azureADOnlyAuthentication = true`. **This is the end of the bootstrap window.**
   - **Step 4**: Delete the bootstrap admin password secret (`SQL-ADMIN-PASSWORD-BOOTSTRAP`); retain the export password (`SQL-ADMIN-PASSWORD-EXPORT`) per [ADR-004](decisions/ADR-004-bacpac-export-schedule.md).
   - **Step 5**: Verify the AAD-only flip is `true` AND the bootstrap secret is gone. Either failure aborts postprovision with a non-zero exit.
3. **`azd deploy`** — packages `function_app/` + `swa/` and pushes to the running Function App + Static Web App. Uses `WEBSITE_RUN_FROM_PACKAGE=1` so the deploy is an atomic blob swap.

### B.4 PowerBI deploy (separate path)

PowerBI is provisioned + deployed outside `azd` because it lives in a different Azure service plane (PowerBI Service, not Azure RM). Follow [`docs/runbooks/powerbi_deploy.md`](runbooks/powerbi_deploy.md) — the runbook walks through:

1. Service Principal registration in your PowerBI tenant.
2. Tenant-level toggle: "Allow service principals to use Power BI APIs" (manual portal step).
3. Pre-deploy env-var setup.
4. `powerbi/deploy.ps1` invocation (idempotent; 9 steps with TE3 + .bim fallbacks).
5. PowerBI Desktop finalisation pass to lay out visuals (the .pbir report skeleton is intentionally minimal — see [`powerbi/README.md`](../powerbi/README.md) Known Limitations §1).

### B.5 Smoke test

```bash
# 1. Grab the deployed hostnames
SWA_HOST=$(azd env get-value AZURE_STATIC_WEB_APP_HOSTNAME)
FUNC_HOST=$(azd env get-value AZURE_FUNCTION_APP_DEFAULT_HOSTNAME)

# 2. Verify /api/ping warms the database
curl -fsS "https://${FUNC_HOST}/api/ping" | jq .
# Expected envelope:
#   {"status": "warm" | "resumed", "sql_resume_ms": <int>, "db_version": "<sql version banner>"}
# The 'sql_resume_ms' field — NOT 'latency_ms' — is the resume-cost signal.

# 3. Verify schema_history is populated with REAL checksums (not the sentinel)
RG=$(azd env get-value AZURE_RESOURCE_GROUP)
SQL_SERVER=$(az sql server list -g "$RG" --query "[0].name" -o tsv)
SQL_DB=$(az sql db list -g "$RG" -s "$SQL_SERVER" --query "[0].name" -o tsv)
sqlcmd -S "${SQL_SERVER}.database.windows.net" -d "$SQL_DB" -G \
  -Q "SELECT script_name, applied_at_utc, checksum FROM dbo.schema_history;"
# Expected: V001__init.sql + V002__synth_logic.sql rows with 64-char hex
# checksums. Anything else (sentinel, placeholder) is a deploy failure.

# 4. Open the SWA URL in a browser, sign in, ask "How many trades did the
#    Cluj-Napoca floor close yesterday?". Expected: answer paragraph + a small
#    table of row data formatted with the ro-RO locale.
echo "Open: https://${SWA_HOST}"
```

### B.6 Open the Operations dashboard

In the Azure portal, navigate to:

```
Monitor → Workbooks → Recent → TCP — Operations dashboard
```

The 9 panels should be empty for the first 5-15 minutes (telemetry needs to land). After the first `/api/ask` round-trip, the latency tile + token tile populate; the daily-generator tile populates after the first 07:00 RO run.

### B.7 Roll back if anything breaks

- **Stop SQL costs immediately**: `az sql db update --name <db> --server <server> -g <rg> --capacity 0` (pauses serverless).
- **Tear down everything**: `azd down --purge` — drops every resource and purges Key Vault soft-delete. Note this destroys data; use only for clean re-bootstraps.
- **Restore from PITR**: see [`docs/security/incident_response.md`](security/incident_response.md) scenario A.

---

## Acceptance checklist

A deploy is "green" when every item below holds.

> **Derive the variables first** so every checklist command runs unmodified (tut-CR-01 fix). Source the same preamble as in [`troubleshooting.md`](troubleshooting.md):
>
> **POSIX**
> ```bash
> export RG=$(azd env get-value AZURE_RESOURCE_GROUP)
> export SQL_SERVER=$(azd env get-value AZURE_SQL_SERVER_NAME)
> export SQL_DB=$(azd env get-value AZURE_SQL_DATABASE_NAME)
> export SQL_FQDN="${SQL_SERVER}.database.windows.net"
> export KV_NAME=$(azd env get-value AZURE_KEYVAULT_NAME)
> export FUNC_HOST=$(azd env get-value AZURE_FUNCTION_APP_DEFAULT_HOSTNAME)
> ```
>
> **PowerShell**
> ```powershell
> $env:RG          = (azd env get-value AZURE_RESOURCE_GROUP)
> $env:SQL_SERVER  = (azd env get-value AZURE_SQL_SERVER_NAME)
> $env:SQL_DB      = (azd env get-value AZURE_SQL_DATABASE_NAME)
> $env:SQL_FQDN    = "$env:SQL_SERVER.database.windows.net"
> $env:KV_NAME     = (azd env get-value AZURE_KEYVAULT_NAME)
> $env:FUNC_HOST   = (azd env get-value AZURE_FUNCTION_APP_DEFAULT_HOSTNAME)
> ```

- [ ] `az deployment sub list --query "[?name=='tcp-prod'].properties.provisioningState" -o tsv` returns `Succeeded`.
- [ ] `sqlcmd -S "$SQL_FQDN" -d "$SQL_DB" -G -Q "SELECT script_name, checksum FROM dbo.schema_history"` shows two rows with 64-char hex checksums (no `__V*_CHECKSUM__`, no `TODO-checksum-set-by-CI`, no `sentinel-no-checksum-supplied`).
- [ ] `az sql server ad-only-auth list -s "$SQL_SERVER" -g "$RG"` returns `azureADOnlyAuthentication: true`.
- [ ] `az keyvault secret show --vault-name "$KV_NAME" --name SQL-ADMIN-PASSWORD-BOOTSTRAP` returns `SecretNotFound` (the bootstrap password was deleted in Step 4).
- [ ] `az keyvault secret show --vault-name "$KV_NAME" --name SQL-ADMIN-PASSWORD-EXPORT` returns a secret (ADR-004; retained for the BACPAC export).
- [ ] `curl -fsS "https://${FUNC_HOST}/api/ping"` returns 200 with `status ∈ {warm, resumed}` and an `sql_resume_ms` field.
- [ ] A signed-in `/api/ask` request returns a 200 envelope with `rows[]` (after the first cold-start).
- [ ] Monitor → Workbooks → TCP — Operations dashboard renders without query errors.
- [ ] Monitor → Alerts → Alert rules shows 8 rules in `Enabled` state.

If any item fails, jump to [`docs/troubleshooting.md`](troubleshooting.md) — the 9 documented failure modes list the diagnostic command for each acceptance check.

---

## What gets deployed

For the curious examiner: a single `azd up` against an empty subscription provisions roughly the following set, all in `rg-tcp-prod-weu`:

| Resource | Cost band (academic load) |
|---|---|
| `log-tcp-prod-weu` (Log Analytics) | Free 5 GB/month ingestion |
| `ai-tcp-prod-weu` (App Insights) | Free (workspace-based, follows the LA grant) |
| `sttcpprodweu` (Storage account) | <€0.50/month (LRS + lifecycle pruning) |
| `kv-tcp-prod-weu` (Key Vault) | <€0.05/month (Standard SKU, sparse ops) |
| `sql-tcp-prod-weu` + `sqldb-tcp-prod-weu` (Azure SQL Free Offer) | €0 (100 000 vCore-s + 32 GB free) |
| `asp-tcp-prod-weu` + `func-tcp-prod-weu` (Function App Y1) | €0 (1M executions/month free) |
| `swa-tcp-prod-weu` (Static Web App Free) | €0 |
| Diagnostic settings + role assignments | €0 |
| App Insights workbook + 8 alert rules | €0 (log query cost ≈ 550 MB/month within free grant) |

Total recurring cost target: **€0/month** at the documented load profile. See [`docs/design/03_architecture.md`](design/03_architecture.md) §10 for the cost model and mitigation playbook.
