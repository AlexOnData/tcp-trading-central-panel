# Etapa 4 CD + Function App review — pass 1

**Reviewer**: deployment-engineer (cross-checking with security and compliance lens)  
**Date**: 2026-05-16  
**Verdict**: **ACCEPT_WITH_CHANGES** (2 critical findings block production deploy; 3 major findings require before-merge fix; 2 minor nits are optional polish)

---

## Summary

The Etapa 4 CD pipeline, post-provision scripts, and Function App scaffolding are architecturally sound and follow the Etapa 1 design specification closely. OIDC federation is correctly implemented; Function App triggers are cleanly separated; the post-provision idempotency is solid. However, two critical issues block production deploy: (1) the CD `provision` job reuses the azd environment on every push, causing potential leftover state and cost overruns if infrastructure code changes; (2) the smoke test's schema_history query lacks proper error handling for the pre-V001 case, potentially masking deployment failures. Three major findings must be fixed before merge. The codebase is English-only; all permissions are minimal; secrets are correctly omitted.

---

## Critical (blocks production deploy)

- [ ] **CR-01** | `.github/workflows/cd.yml:47–51` | `azd env new tcp-prod ... || true` masks failure and reuses stale environment across pushes | **Why**: Repeated `azd env new` against the same environment name (line 49) is idempotent by design in azd 1.6+, but does not detect when the environment **on disk** belongs to a different resource group (e.g., after manual `az group delete`). This allows subsequent `azd provision` to fail silently or target the wrong RG. The `|| true` on line 51 further masks errors. | **Fix**: (a) Fail-fast on env creation error (`azd env new` without `|| true` unless it explicitly returns exit code 0 for "already exists"); (b) add a validation step that queries the Resource Group name from azd config and compares it to the variable `AZURE_RESOURCE_GROUP`. Suggested:
  ```bash
  azd env new tcp-prod --location westeurope --no-prompt
  RG_ACTUAL=$(azd env get-value AZURE_RESOURCE_GROUP)
  if [ "$RG_ACTUAL" != "rg-tcp-prod-weu" ]; then
    echo "ERROR: Env points to RG '$RG_ACTUAL', expected 'rg-tcp-prod-weu'"
    exit 1
  fi
  ```

- [ ] **CR-02** | `.github/workflows/cd.yml:145–162` | Schema history smoke test silently succeeds on failure (pre-V001 case), masking real database problems | **Why**: Lines 159–162 catch the entire query failure with `|| { echo "WARNING: ..."; exit 0; }`. This converts a real failure (SQL auth error, network partition, OID mismatch) into a soft warning, allowing the workflow to report success when the deployment is actually broken. The comment "this may be pre-V001" is defensive but too broad — it catches all failures indiscriminately. | **Fix**: Distinguish two cases: (a) if the schema_history table does not exist (expected in V000), emit a specific info message and exit 0 cleanly; (b) if the query fails for any other reason, fail the workflow. Use sqlcmd's `-w 200` flag to detect object-not-found errors:
  ```bash
  sqlcmd ... -Q "SELECT ... FROM dbo.schema_history ..." -w 200 2>&1 | tee query_out.txt
  if grep -q "object_id.*schema_history.*not found\|Invalid object name" query_out.txt; then
    echo "INFO: schema_history not yet deployed (pre-V001); skipping smoke test"
    exit 0
  elif [ "${PIPESTATUS[0]}" -ne 0 ]; then
    echo "ERROR: schema_history query failed unexpectedly"
    exit 1
  fi
  ```

---

## Major

- [ ] **MA-01** | `.github/workflows/ci.yml:158` | `bicep build` output not validated — invalid Bicep is not caught until `azd provision` | **Why**: The `iac-validate` job runs `bicep build infra/main.bicep` but does not check its exit code or inspect stderr for validation warnings. A Bicep file with broken decorators (e.g., missing `@metadata()` annotations) will build but fail during `azd provision` (line 58 of cd.yml), delaying feedback by ~1–2 hours (the time from PR open to CD run). | **Fix**: Add an explicit check after bicep build:
  ```bash
  bicep build infra/main.bicep -o /tmp/main.json
  if [ $? -ne 0 ]; then
    echo "ERROR: Bicep build failed"
    exit 1
  fi
  # Optional: validate the JSON schema
  az deployment sub validate --location westeurope --template-file /tmp/main.json --parameters @infra/main.parameters.prod.json --no-pretty-print || exit 1
  ```
  This performs a dry-run on the built template without consuming a resource group slot, catching decorator/parameter mismatches in CI.

- [ ] **MA-02** | `.github/workflows/cd.yml:54–55` | `ANTHROPIC_API_KEY` is passed as plaintext via `azd env set` | **Why**: Line 55 runs `azd env set ANTHROPIC_API_KEY ${{ secrets.ANTHROPIC_API_KEY }}`, which writes the secret into the azd environment file (stored in `.azure/tcp-prod/.env` by default). The file is then available to subsequent steps in plaintext and is visible in the workflow log if any step echo's the environment. Although the file is not committed and the log redaction is attempted, this pattern relies on GitHub's log sanitization, which is not guaranteed if the secret is interpolated in an unexpected way. | **Fix**: Rely on `ANTHROPIC_API_KEY` already being in the environment (already set by the CI trigger-step secrets) and skip the explicit `azd env set`. Instead, ensure that `azd provision` and `azd deploy` automatically pick up the env var:
  ```bash
  export ANTHROPIC_API_KEY=${{ secrets.ANTHROPIC_API_KEY }}
  azd provision --no-prompt
  azd deploy api --no-prompt
  ```
  The `azd` CLI exports all env vars as app settings by default (unless the Bicep explicitly filters them). Alternatively, keep the `azd env set` but immediately delete the `.env` file after `azd deploy` completes (not before, as subsequent steps may need it):
  ```bash
  azd deploy api --no-prompt
  rm -f .azure/tcp-prod/.env
  ```

- [ ] **MA-03** | `infra/scripts/postprovision.sh:70–90` | Bash heredoc with variable substitution is fragile | **Why**: The script defines a SQL batch as a here-document with `'...'` quoting (line 70, `read -r -d '' SQL_SETUP << 'EOF'`), then manually substitutes the function-app principal ID on line 93. If the principal ID contains a regex metacharacter (unlikely but not impossible in future), the substitution breaks. Additionally, the here-document ends with `|| true` (line 90) to suppress errors from the read command, but this masks real I/O failures on bash versions where the read fails. | **Fix**: Use a safer substitution approach or pass the variable directly to sqlcmd without string interpolation:
  ```bash
  sqlcmd -S "$SQL_SERVER_FQDN" -d "$SQL_DATABASE_NAME" -G -b 2>/dev/null <<EOF
  ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = OFF);
  IF NOT EXISTS (
    SELECT 1 FROM dbo.dim_UserRoles
    WHERE aad_object_id = CAST('$FUNCTION_APP_PRINCIPAL_ID' AS UNIQUEIDENTIFIER)
      AND scope = 'admin'
      AND is_active = 1
  )
  BEGIN
    INSERT INTO dbo.dim_UserRoles (aad_object_id, employee_id, scope, is_active, created_at)
    VALUES (CAST('$FUNCTION_APP_PRINCIPAL_ID' AS UNIQUEIDENTIFIER), NULL, 'admin', 1, SYSDATETIMEOFFSET());
    PRINT 'Registered Function App MI as admin.';
  END
  ELSE
  BEGIN
    PRINT 'Function App MI already registered.';
  END;
  ALTER SECURITY POLICY rls.TradesAccessPolicy WITH (STATE = ON);
  EOF
  ```
  This relies on bash variable expansion in the here-document (not `'EOF'`, but `EOF`), which is standard and safe for GUIDs. Alternatively, use `envsubst`:
  ```bash
  envsubst < /tmp/setup.sql.template | sqlcmd ...
  ```
  where the template is a separate file.

---

## Minor / nits

- [ ] **MN-01** | `.github/workflows/cd.yml:29–35` & lines 84–91 | OIDC login is duplicated (both provision and deploy jobs) | **Why**: Both `provision` and `deploy` jobs redundantly login via `azure/login@v2`, but they have already authenticated via `azd auth login` at lines 40–45 and 96–101. The second login consumes an additional OIDC token and is not needed. | **Fix**: Remove the `azure/login@v2` step from both jobs and rely solely on `azd auth login`. If a subsequent step needs the Azure CLI directly (not via `azd`), login once and reuse the token. Lines 120–125 (smoke job) can keep the login since it runs `az` commands directly, but prefer `azd auth login` there too for consistency.

- [ ] **MN-02** | `function_app/host.json:8` | Route prefix `"api"` is implicit, not declared in the triggers | **Why**: Line 8 sets `"routePrefix": "api"`, so all `@app.route()` decorators in the triggers are automatically prefixed with `/api/`. The triggers (e.g., `ping.py:49–50`, `ask.py:49–50`) declare `route="ping"` and `route="ask"`, intending `/api/ping` and `/api/ask`. This is correct, but the `host.json` setting is not documented in the `function_app.py` docstring or the README. Future maintainers may not realize the prefix is applied, leading to incorrect route specifications. | **Fix**: Add a one-line comment to `host.json` or update the `function_app.py` docstring to note: `All HTTP routes are automatically prefixed with "/api" per host.json configuration.`

---

## CD workflow mental walkthrough

**Successful deployment run:**

1. **Trigger**: Push to `main` or manual `workflow_dispatch`.
2. **OIDC exchange**: GitHub Actions mints an OIDC token, exchanges it via `azure/login@v2` (provision job, lines 30–35) for an access token scoped to `AZURE_SUBSCRIPTION_ID`.
3. **`provision` job**:
   - Checkout code (line 28).
   - Authenticate: `azure/login@v2` → `azd auth login` (lines 30–45).
   - `azd env new tcp-prod` (line 49): creates or updates the local azd environment config (stored in `.azure/tcp-prod/.env`). **[ISSUE CR-01: reuses stale env if RG was deleted manually]**.
   - `azd env set ANTHROPIC_API_KEY` (line 55): writes the secret to `.env` **[ISSUE MA-02: plaintext in workflow log]**.
   - `azd provision --no-prompt` (line 58): builds Bicep, validates against Azure, creates/updates resources in the target RG. Output lines include resource IDs.
   - `azd env get-values` (lines 63–69): parses the provisioned resource names and exports them as job outputs.
4. **`deploy` job** (depends on `provision`):
   - Repeats OIDC login and `azd auth login`.
   - `azd env set ANTHROPIC_API_KEY` (line 105): redundantly updates the env.
   - `azd deploy api --no-prompt` (line 108): packages the `function_app/` directory, deploys the Python 3.12 code to the Function App, restarts the runtime. Triggers are registered via the decorators in `function_app.py` (now that imports execute).
5. **`smoke` job** (depends on `provision` and `deploy`):
   - Waits 30 s for the Function App to stabilize (line 133).
   - Calls `curl https://{hostname}/api/ping` (line 138): Function App runs `ping.py`, opens a connection, issues `SELECT @@VERSION`, returns 200 + JSON with resume time. **[ISSUE CR-02: the schema_history query below treats all failures as pre-V001]**.
   - Queries `dbo.schema_history` via OIDC-authenticated sqlcmd (line 158): verifies schema migrations have been applied. **[This is where CR-02 manifests: silent success even if the query fails for auth reasons]**.
   - Posts a summary to the workflow run (lines 164–176).

On success, the infrastructure and code are live in `rg-tcp-prod-weu`, ready for user traffic.

---

## Function App trigger map

| Trigger | Type | Auth | Status | Notes |
|---------|------|------|--------|-------|
| `TimerTrigger_DailyGenerator` | Timer | MI (admin) | Ready | Calls `tcp.synth.run_daily()`; runs 07:00 RO weekdays (Mon–Fri). Honors ADR-003 SESSION_CONTEXT contract. |
| `WarmupTrigger` | Timer | MI (admin) | Ready | `SELECT 1` to resume SQL from auto-pause; runs 06:55 RO weekdays. Skips SESSION_CONTEXT (bypass_session_context=True). |
| `TimerTrigger_BacpacExport` | Timer | MI (admin) | Placeholder Etapa 4 | Scheduled for 08:00 RO Sundays. Logs configuration; real REST call (ADR-004) deferred to Etapa 5. |
| `HttpTrigger_Ping` | HTTP GET | Anonymous | Ready | Route `/api/ping`. Returns `{status, sql_resume_ms, db_version}`. 200 on success, 503 on SQL failure. |
| `HttpTrigger_AskAssistant` | HTTP POST | Header-validated | Stub Etapa 4 | Route `/api/ask`. Validates `x-ms-client-principal` (401 if missing) and `X-SWA-Forwarded` shared secret (403 if mismatch). Returns 501 stub; full LLM pipeline in Etapa 5. |

---

## Post-provision idempotency analysis

### PowerShell script (`postprovision.ps1`)

✓ **Step 1 (RLS setup)**: `IF NOT EXISTS` guard ensures the Function App MI row is inserted only once. `ALTER SECURITY POLICY ... WITH (STATE = OFF/ON)` is idempotent (no error if already in that state).

✓ **Step 2 (TCP_GENERATOR_OID setting)**: `az functionapp config appsettings set` is idempotent — setting the same key again overwrites with the same value.

✓ **Step 3 (AAD-only flip)**: `Set-AzSqlServerActiveDirectoryOnlyAuthentication -Enable $true` is idempotent (no error if already enabled). 10s sleep is conservative.

⚠ **Step 4 (Secret deletion)**: Wraps the delete in a pre-check (`secret show`) and ignores "not found" errors. Idempotent, but the error handling catches only `$secretExists` truthy/falsy — if the show command fails for a reason other than "not found" (e.g., network timeout, RBAC denial), the script continues anyway (line 143, `catch` block emits a warning and continues). This is acceptable for a bootstrap script but could be tighter.

✓ **Step 5 (Verification)**: Queries AAD-only status and bootstrap-secret absence; fails if either is false. On re-run, both should already be true, so the script exits with success.

### Bash script (`postprovision.sh`)

✓ **Step 1–5**: Same logic as PowerShell, with `set -euo pipefail` for fail-fast semantics.

⚠ **SQL error handling** (line 61): `execute_sql() { ... } 2>/dev/null || { warn ...; return 0; }`. The function silently swallows all stderr and treats any failure (exit code non-zero) as "maybe pre-V001". This is overly permissive — it conflates "table doesn't exist" (OK for idempotency) with "auth failed" (a real error). **[ISSUE MA-03: fragile variable substitution in the here-doc also applies here]**.

⚠ **Step 4 (Secret deletion)** (lines 122–132): Uses `if az keyvault secret show ... --output none 2>/dev/null; then ...`. If `show` fails for reasons other than "not found", the delete is skipped silently. This is acceptable (the post-provision is defensive and can be re-run), but a clearer pattern would be:
```bash
if az keyvault secret show ... --output none 2>/dev/null; then
  az keyvault secret delete ... || error "Failed to delete secret"
fi
```

**Overall**: Both scripts are idempotent and safe to re-run. The error handling is defensive (warnings rather than hard failures), which is appropriate for a bootstrap hook. However, the SQL and secret deletion steps would benefit from tighter error classification (distinguish "expected absence" from "real failure") before merging to production.

---

## Code quality & compliance audit

### English-only ✓
- All code comments, docstrings, function/variable names are English.
- No Romanian or placeholder text in committed artifacts.

### Secrets & credentials ✓
- No hardcoded API keys, passwords, or connection strings in code.
- `ANTHROPIC_API_KEY` is pulled from `secrets.ANTHROPIC_API_KEY` (GitHub Secrets).
- `SWA_FORWARDED_SECRET` is sourced from Key Vault reference (not in repo).
- `local.settings.json.template` is a placeholder; the real `local.settings.json` is in `.gitignore`.

### RBAC & permissions ✓
- CD workflow: minimal permissions (`contents: read`, `id-token: write`).
- Each job has explicit per-job permissions (no overly broad global scope).
- GitHub OIDC SP is bound to specific subjects (`ref:refs/heads/main`, `pull_request`, `environment:dev`).

### Type safety & linting ✓
- Function App code includes type hints (e.g., `HttpResponse`, `TimerRequest`, `Optional[...]`).
- Docstrings are present on every public function.
- Import organization (`from __future__` first, then stdlib, then third-party, then local).

### Error handling ✓
- Timer triggers catch all exceptions and re-raise (so alerts fire).
- HTTP triggers return explicit status codes (401, 403, 501, 200, 503).
- Connection cleanup is wrapped in try/finally; `pyodbc.Error` is caught.

### Coverage gaps
- No integration tests in this deliverable (they land in Etapa 5 after smoke tests).
- `bacpac_export.py` is a stub — real async polling deferred to Etapa 5.

---

## Recommendation

**Verdict**: **ACCEPT_WITH_CHANGES**

**Merge blockers**:
1. Fix CR-01 (azd env validation) to prevent stale environment reuse.
2. Fix CR-02 (smoke test error classification) to distinguish pre-V001 absence from real SQL failures.

**Pre-production checklist** (fix before the first `azd up` on Azure):
3. Fix MA-01 (bicep build validation in CI) to catch decorator mismatches before `azd provision`.
4. Fix MA-02 (ANTHROPIC_API_KEY plaintext in log) by using env-var passthrough instead of `azd env set`.
5. Fix MA-03 (bash heredoc fragility) by using direct variable expansion or `envsubst`.

**Optional polish** (can merge as follow-up):
6. MN-01 (remove redundant `azure/login@v2` from provision/deploy).
7. MN-02 (document the route prefix in host.json or function_app.py docstring).

Once the two critical issues are resolved, the pipeline is production-ready. The architecture is sound; the Function App is correctly structured; and the post-provision hooks will establish the RLS contract and AAD-only authentication without errors.

---

## Appendix: Acronym expansion

| Acronym | Meaning |
|---------|---------|
| OIDC | OpenID Connect (federated credential token exchange) |
| AAD | Azure Active Directory (now Entra ID) |
| MI | Managed Identity (system-assigned on Function App) |
| RLS | Row-Level Security (SQL predicate on fact tables) |
| RBAC | Role-Based Access Control (Azure IAM) |
| SWA | Static Web App (Azure platform) |
| KV | Key Vault (secret store) |
| BACPAC | SQL Database backup format (portable export) |
| PITR | Point-in-Time Restore (automated SQL backups) |
| ADR | Architecture Decision Record (design doc) |
| CI/CD | Continuous Integration / Continuous Deployment |
