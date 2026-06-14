# Etapa 6 cross-cutting security sweep

**Reviewer**: security-auditor
**Date**: 2026-05-16
**Verdict**: ACCEPT_WITH_CHANGES
**Posture**: thesis-grade — strong defence-in-depth on the AI/SQL surface; a handful of HTTP-header and IaC hardening items remain before production traffic.

---

## Summary

The TCP repository post-Etapa-5 reaches a maturity level uncommon for a thesis project: every `cursor.execute` call is parameterised or static; `tcp.safe_query` implements a three-layer fail-closed defence (NFKC normalisation + literal-masked deny-list + sqlglot AST re-serialisation) against LLM-emitted SQL; the RLS contract (ADR-003) is honoured with explicit `bypass_session_context=True` escape hatches and a per-request reset on check-in; OIDC federation eliminates static secrets from CI/CD; the entire secret surface is in Key Vault, fetched via Managed Identity and Pydantic `SecretStr`; CSP is correctly tight (no `unsafe-inline`). No critical findings. Four majors block the `v1.0` tag for a "production-grade" claim — three are HTTP-header polish (Retry-After, HSTS, X-Frame-Options) and one is the documented `allowSharedKeyAccess: true` on the Storage Account. For the thesis posture, the deferred-residual posture is acceptable. Single biggest concern: the **`AllowAllAzureServices` SQL firewall rule + AAD-only auth + audit-logging-to-LA** is the right trade-off for free-tier but produces a public TLS+AAD surface every Azure tenant can reach; the bootstrap window before AAD-only flip is the highest-residual moment of the deployment and is the right thing for the operator to time carefully (already documented).

---

## OWASP Top 10 (2021) matrix

| Item | Status | Evidence | Findings |
|---|---|---|---|
| A01 Broken Access Control | STRONG | RLS predicate joins `dim_UserRoles` on `SESSION_CONTEXT('aad_object_id')` (V001 §11, ADR-003); deny-by-default when context unset; `@read_only=1` lock prevents overwrite; `connection_for_user` resets on check-in (`tcp/db.py:269`); admin-bypass scoped to one parameterised single-row SELECT (`ask.py:341`); scope post-validated against `_ALLOWED_SCOPES` allowlist; `dim_UserRoles` excluded from AI allowlist; `tcp_ai_assistant` SQL role is SELECT-only. | None additional beyond ADR-005's documented single-instance rate-limit residual. |
| A02 Cryptographic Failures | STRONG | `Encrypt=yes` in pyodbc conn-string (`tcp/db.py:155`); `minimalTlsVersion: '1.2'` on SQL, Storage, KV, Functions (Bicep); `httpsOnly: true` on Function App; `supportsHttpsTrafficOnly: true` on Storage; `allowBlobPublicAccess: false`; all secrets in KV via @Microsoft.KeyVault(SecretUri=...) references; `Pooling=False` on AAD connections (ADR-003 §4 hygiene). | TLS 1.3 not enforced (1.2 floor only) — acceptable; Azure's frontends negotiate ≥ 1.2 and modern clients prefer 1.3. No SSL/TLS pinning (not feasible for HTTP clients). |
| A03 Injection | STRONG | Every `cursor.execute` call walked (SQL injection re-audit below): all parameterised or static. `safe_query.validate()` enforces deny-list → sqlglot AST → allowlist → re-serialise; closes ai MA-01..04 (NFKC, masked-literal scan, CTE row-cap, proc-as-function rejection). `validate_proc_call` returns a `ProcCallResult` with ordered tuple (closes MJ-03). No `OPENJSON` extraction code paths use untrusted strings — `usp_GenerateDailyTrades` receives `json.dumps(...)` of Python-internal data only. | The deny-list `INTO` regex matches `\bINTO\b` even though `INSERT` is denied first; `INSERT INTO` test pins ordering. Acceptable. |
| A04 Insecure Design | STRONG | Single-instance in-process rate limit (10/min/OID, sliding window) acknowledged in ADR-005; unified envelope shape across all HTTP status codes (`_envelope`); generic 422 message ("rejected by safety validator") never echoes internal detail (closes ai MA-06). Refusal envelope routed through same helper. | Single-instance counter resets on cold start (ADR-005 documents this). |
| A05 Security Misconfiguration | ADEQUATE | CSP `default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'`; CORS denied by default on Function App (`allowedOrigins: []`); third-party Actions pinned to full SHAs (`gitleaks-action@ff98106e…`, `setup-uv@08807647…`); `azure/login@v2` and `actions/checkout@v4` are first-party (Microsoft / GitHub) and traditionally pinned by tag; `azureADOnlyAuthentication` flipped on by postprovision; KV soft-delete=7 days. | Missing `Strict-Transport-Security` and `X-Frame-Options` in `globalHeaders` (MJ-02 below). KV `defaultAction='Allow'` documented trade-off; `Storage allowSharedKeyAccess: true` documented trade-off (MJ-03 below). |
| A06 Vulnerable & Outdated Components | ADEQUATE | `pyproject.toml` + `function_app/requirements.txt` use `>=` lower-bound pins on every dependency; `pip-audit --strict` runs in CI on every PR; `bandit -r tcp function_app -lll -iii` covers Python SAST. Dependencies: pyodbc≥5.1, azure-identity≥1.17, pydantic≥2.7, polars≥1.0, structlog≥24.1, faker≥26.0, anthropic≥0.40, sqlglot≥23.0, httpx≥0.27. | No upper-bound pins — acceptable for the thesis posture (CI catches regressions). Lockfile (`uv.lock`) not committed; rebuilding from `pyproject.toml` on each CI run is the documented strategy. |
| A07 Identification & Authentication Failures | STRONG | AAD-only post-bootstrap on Azure SQL (postprovision Step 3); SQL admin password deleted from KV post-flip (Step 4); Function MI is the only data-plane identity; KV uses RBAC mode (no access policies); OIDC federation in GH Actions; dev SQL-auth path is gated by both `TCP_SQL_DEV_USER` and `TCP_SQL_DEV_PASSWORD` env vars (`AuthMode.from_env`). | MFA at the AAD tenant level is a TODO (documented in `03_arch §8.3` threat row). |
| A08 Software & Data Integrity | ADEQUATE | OIDC federated credentials with `id-token: write` permission; `WEBSITE_RUN_FROM_PACKAGE=1` ensures Functions execute the deployed package, not Kudu builds; `actions/checkout@v4` clones at a verified commit; `checkov` + `psrule-for-azure` (Azure.MCSB.v1) run in CI (fail on Medium/High/Critical). | **`schema_history.checksum` left as `'TODO-checksum-set-by-CI'`** in V001 and V002 (MN-01 below). CI does not currently populate the checksum; the schema_history row therefore cannot detect mutation. |
| A09 Logging & Monitoring Failures | STRONG | App Insights connection string set on Function App; Log Analytics diagnostic settings on SQL (`SQLSecurityAuditEvents`, `Errors`, `Timeouts`), Storage (`StorageRead/Write/Delete`), KV (`AuditEvent`, `AzurePolicyEvaluationDetails`), Functions (`FunctionAppLogs`, `AppServiceConsoleLogs`, `AppServiceHTTPLogs`). OID logged as suffix only (`oid.hex[-8:]` in `ask.py:542`, `oid_str[-4:]` in `db.py:252`). `SecretStr` shields api-key/password from `repr`. Daily quota cap set to 0.5 GB. | OID-suffix length differs between two call sites (8 hex chars vs 4 chars) — cosmetic drift (MN-02 below). |
| A10 SSRF | STRONG | No user-controlled URL paths anywhere in the codebase. BACPAC export, Storage HEAD probes, and Anthropic client URLs are all constructed from validated Pydantic config sourced from env vars; no field accepts a user-supplied URL. The Function App's outbound traffic targets only Anthropic, Azure Management, Azure Storage, and Azure SQL — no `httpx.get(user_input)` pattern exists. | None. |

---

## Azure CIS / Microsoft Cloud Security Benchmark v1 pass

| Control area | Status | Evidence | Findings |
|---|---|---|---|
| App Service / Functions | STRONG | `httpsOnly: true`, `minTlsVersion: '1.2'`, `ftpsState: 'Disabled'`, `WEBSITE_RUN_FROM_PACKAGE=1`, `clientAffinityEnabled: false`, `keyVaultReferenceIdentity: 'SystemAssigned'`, MI is system-assigned, CORS denied by default. | `AzureWebJobsStorage` is an account-key connection string, not identity-based (documented trade-off — see MJ-03). |
| Storage | ADEQUATE | `minimumTlsVersion: 'TLS1_2'`, `supportsHttpsTrafficOnly: true`, `allowBlobPublicAccess: false`, blob soft-delete=7 d, container soft-delete=7 d, `bacpac-exports` lifecycle policy=28 d, MI scoped to BACPAC container only (Storage Blob Data Contributor), `publicAccess: 'None'` on container. | **`allowSharedKeyAccess: true`** required for `AzureWebJobsStorage` boot path (CR-02 trade-off); documented in `storage.bicep` comment. MJ-03 below. |
| Azure SQL | ADEQUATE | `minimalTlsVersion: '1.2'`, AAD-only flipped on by postprovision Step 3, `SQLSecurityAuditEvents` to LA, TDE on by default for Free Offer (Azure-managed), GP_S Free Offer auto-pause. | **Advanced Threat Protection / Defender for SQL not enabled** — paid feature, unavailable on Free Offer. Documented deficit. **`AllowAllAzureServices` firewall rule** — documented trade-off (`sql.bicep` security MJ-04 annotation); Y1 Consumption cannot present a static outbound IP. |
| Key Vault | ADEQUATE | RBAC mode, `enableSoftDelete: true`, `softDeleteRetentionInDays: 7`, diagnostic settings (`AuditEvent`, `AzurePolicyEvaluationDetails`), MI granted `Key Vault Secrets User` only, OIDC SP granted `Key Vault Secrets Officer` only (narrower than Contributor). | `enablePurgeProtection: false` — documented thesis trade-off so `azd down` can fully purge; STATE.md tracks the "flip post-defense" reminder. `defaultAction: 'Allow'` — documented trade-off (free-tier; bypass:AzureServices is a no-op when default=Allow). |
| GitHub Actions | STRONG | OIDC federated credential, `permissions: contents: read, id-token: write, pull-requests: read` (least-privilege per workflow); third-party actions pinned to full commit SHAs with version comments (`gitleaks-action@ff98106e…  # v2.3.9`, `setup-uv@08807647…  # v8.1.0`); no `pull_request_target`; no static secrets in repo variables (`AZURE_CLIENT_ID/TENANT_ID/SUBSCRIPTION_ID` are non-secret identifiers); explicit `concurrency` groups; `||true` mask scoped only to informational `bicep what-if` with a justifying comment. | `actions/checkout@v4`, `actions/setup-python@v5`, `actions/upload-artifact@v4`, `azure/login@v2`, `actions/github-script@v7`, `azure/setup-bicep@v1`, `Azure/setup-azd@v1.0.0` are first-party Microsoft/GitHub actions and pinned to major-version tags. CIS guidance for highest assurance is SHA-pinning on those too; thesis posture acceptable. |

---

## Critical (blocks v1.0-mvp tag)

None.

---

## Major (production-readiness, do not block thesis v1.0)

### MJ-01 | `function_app/triggers/ask.py:609-615` | 429 response omits `Retry-After` header

- **Threat model**: Naïve client retry loops or buggy SWA edge cache can hammer the endpoint at network speed; RFC 6585 §4 mandates `Retry-After` on every 429.
- **Suggested fix**: Extend `_envelope` to accept an optional `extra_headers: dict[str, str] | None` and forward to `func.HttpResponse(headers=...)`. At the 429 return site, pass `extra_headers={"Retry-After": str(int(_RATE_LIMIT_WINDOW_SECONDS))}`.
- **Acceptable for thesis posture**; **would harden as follows for production**: emit `Retry-After` on every 429, plus a `RateLimit-*` (draft-ietf-httpapi-ratelimit-headers) header trio so well-behaved clients self-throttle.
- **Status**: Confirmed by the parallel `review_etapa6_backend_security.md` MJ-01 finding.

### MJ-02 | `swa/staticwebapp.config.json:35-39` | `globalHeaders` missing `Strict-Transport-Security`, `X-Frame-Options`, `Permissions-Policy`

- **Threat model**: HSTS absent means a downgrade attacker on the user's first hop can strip TLS during the initial DNS-to-redirect window; `X-Frame-Options` covers the IE11/legacy-proxy clickjacking surface that `frame-ancestors 'none'` (already present) does not reach; `Permissions-Policy` controls camera/mic/geolocation defaults.
- **Suggested fix**:
  - `"Strict-Transport-Security": "max-age=63072000; includeSubDomains"` (2 years; preload-ready)
  - `"X-Frame-Options": "DENY"`
  - `"Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()"`
- **Acceptable for thesis posture** (CSP `frame-ancestors 'none'` covers modern browsers); **would harden as follows for production**: emit the full standard header set including `Cross-Origin-Resource-Policy: same-origin` and `Cross-Origin-Opener-Policy: same-origin`.

### MJ-03 | `infra/modules/storage.bicep:56` | `allowSharedKeyAccess: true` retains an account-key path on the storage account

- **Threat model**: An attacker who exfiltrates the connection string from Function App settings (or KV `STORAGE-CONNECTION-STRING`) gains read+write to Functions internal containers (`azure-webjobs-hosts`, `azure-webjobs-secrets`) — which would include the Functions host's own master/system keys. The MI-scoped narrow grant on `bacpac-exports` is correctly the only data-plane RBAC, but the account-key remains a parallel path.
- **Suggested fix**: Migrate `AzureWebJobsStorage` to identity-based connections (`AzureWebJobsStorage__accountName`, `AzureWebJobsStorage__credential=managedidentity`) and grant the Function MI `Storage Blob Data Owner` + `Storage Queue Data Contributor` + `Storage Table Data Contributor` on the storage account; then flip `allowSharedKeyAccess: false`.
- **Acceptable for thesis posture** (KV-scoped, MI-fetched, never in plaintext app settings); **would harden as follows for production**: identity-based runtime backing per Azure Functions identity-based connections guidance.

### MJ-04 | `db/migrations/V001__init.sql:1293` and `V002__synth_logic.sql:280` | `schema_history.checksum` left as `'TODO-checksum-set-by-CI'`

- **Threat model**: Without a real checksum, a future migration mutation (e.g., a re-numbered V001 with altered DDL) cannot be detected; the schema ledger is informational, not load-bearing. A8 (Software & Data Integrity).
- **Suggested fix**: In the CD pipeline, compute `sha256(file_content)` before `sqlcmd -i`, then update the row via `UPDATE dbo.schema_history SET checksum = ? WHERE script_name = ?`. Alternatively, embed `:setvar V001_CHECKSUM '<sha256>'` and reference `'$(V001_CHECKSUM)'` in the `INSERT` statement.
- **Acceptable for thesis posture** (synthetic data; manual schema control); **would harden as follows for production**: CI-computed SHA-256 in `schema_history.checksum`, plus a startup sanity check that recomputes and compares.

---

## Minor / nits

### MN-01 | `function_app/triggers/ping.py:54` | `SELECT @@VERSION` leaks SQL Server build string to anonymous callers

- **Threat**: Reconnaissance / version-targeted exploit selection.
- **Fix**: Use `SELECT 1` like the warmup trigger; if the version is genuinely needed for the SWA "Wake up" diagnostic, log it server-side only and return only `{status, sql_resume_ms}` in the body.

### MN-02 | `function_app/triggers/ask.py:542` vs `tcp/db.py:252` | OID-suffix logging length differs (`oid.hex[-8:]` = 8 hex chars = 4 bytes vs `oid_str[-4:]` = 4 chars = 2 hex bytes)

- **Threat**: Documentation/implementation drift; non-material privacy risk.
- **Fix**: Pick one (recommend `oid.hex[-8:]` to match `ask.py` — 4 bytes of suffix is still unidentifiable from 16 byte OID space) and update ADR-003 §3 wording + the other call site.

### MN-03 | `db/migrations/V001__init.sql` | `dim_Employees` table omits a `usp_DeleteEmployeeData` GDPR right-to-erasure procedure

- **Threat**: GDPR data-subject erasure cannot be exercised through SQL today. Informational for the thesis posture because the data is fully synthetic.
- **Fix**: Either add an `usp_DeleteEmployeeData(@employee_id INT)` stored procedure that cascades anonymisation/erasure across `dim_Employees`, `dim_Accounts`, and pseudonymises `fact_Trades.trader_id` (set to a tombstone), or document in `03_architecture.md §13` that the schema's synthetic-only data scope means GDPR Article 17 is not in scope. The architecture doc already notes synthetic-data; tightening the wording closes the audit point.

### MN-04 | `tcp/db.py:194-200` | `_open_raw_connection` logs the redacted connection string at INFO level on every connection open

- **Threat**: Log verbosity; if `_redact` ever misses a non-standard ODBC key, partial credentials could surface in App Insights.
- **Fix**: Demote `conn_str` field to DEBUG level (Function App default LogLevel is `Information`); keep `server`, `database`, `auth_mode` at INFO.

### MN-05 | `swa/staticwebapp.config.json:4` | `rolesSource: "/api/auth/roles"` references a Function endpoint that does not exist

- **Threat**: SWA falls back to anonymous role assignment if the endpoint 404s; the `authenticated` gate on `/api/ask` could silently degrade.
- **Fix**: Either remove the `rolesSource` key (default behaviour assigns `authenticated` + `anonymous` only — sufficient for the current route table) or implement a stub Function that returns `{"roles": ["authenticated"]}` for any authenticated principal.

### MN-06 | `tcp/synth/seed_employees.py:333` | `assert conn is not None` in production code path

- **Threat**: `assert` is stripped under `python -O`. The Functions Linux runtime does not currently run with `-O`, but defensive coding should not rely on this.
- **Fix**: Replace with `if conn is None: raise RuntimeError("seed_employees: injected connection must not be None")`.

### MN-07 | `infra/modules/sql.bicep:88-94` | `AllowAllAzureServices` firewall rule + AAD-only flip race

- **Threat**: Bootstrap window between `azd provision` and Step 3 of postprovision (AAD-only flip) is a moment when SQL auth + `0.0.0.0` virtual rule + `newGuid()` admin password coexist on a public TLS endpoint. The window is ~minutes; password is 120+ bits of entropy; nevertheless, public-network + SQL-auth + Azure-tenant-wide source IP is the highest-residual moment in the deployment.
- **Fix**: Documented in `docs/security/bootstrap_window.md` per the inline comment. Acceptable; tighten by running the postprovision flip immediately after the schema apply.

### MN-08 | `tcp/safe_query.py:628` | Catalog allowlist accepts `""`, `"tcp"`, `"tcp_dev"` — wider than strictly necessary

- **Threat**: A cross-database reference like `tcp.dbo.dim_Employees` would pass the catalog check and rely on the table allowlist alone. Not a vulnerability; defence-in-depth point.
- **Fix**: Tighten to `{""}` (unqualified only). The default `dbo` schema is enforced by `schema and schema.lower() not in {"", "dbo"}` check just below.

### MN-09 | `swa/index.html:33` | `<a href="/.auth/logout">` lacks `rel="noopener noreferrer"` for the user-area sign-out

- **Threat**: Marginal; the link is same-origin and to a SWA platform endpoint. Modern browsers default to `noopener` on `target="_blank"` anyway, and this link has no target. Informational only.

### MN-10 | `swa/staticwebapp.config.json` | No `responseOverrides` section to standardise 401/403/404 error pages

- **Threat**: Default platform error pages on the auth boundary leak SWA internals.
- **Fix**: Add `responseOverrides: { "401": { "redirect": "/.auth/login/aad", "statusCode": 302 } }` and explicit branded HTML for 403/404.

---

## Public surface threat-model (per endpoint)

| Surface | Auth | AuthZ | Input validation | Rate limit | Audit trail | Residual risk |
|---|---|---|---|---|---|---|
| `POST /api/ask` (SWA-fronted) | `X-SWA-Forwarded` shared secret (HMAC-compare-digest) + `x-ms-client-principal` (AAD OID parsed as UUID; SWA-platform-injected) | `dim_UserRoles.scope` lookup via parameterised admin-bypass SELECT; `_ALLOWED_SCOPES` post-validation; RLS BLOCK predicate on `fact_Trades` joins SESSION_CONTEXT → dim_UserRoles | JSON parsed via `req.get_json()` (400 on invalid); `question` is `str`, ≤ 500 chars, non-empty; `_TEMPLATE_VALUE_RE` bounded; LLM-emitted SQL goes through `safe_query.validate()` deny-list + AST + re-serialise | 10 requests/60 s per OID (in-process sliding window, threading.Lock-protected) | `tcp.func.ask.metrics` event with `metric_*` dimensions; `tcp.func.ask.rate_limited` / `sql_validation_failed` / `refused` / `unparseable_principal` / `forwarded_secret_mismatch` events; Function diagnostic settings → LA | Single-instance rate-limit (ADR-005 residual); missing `Retry-After` (MJ-01) |
| `GET /api/ping` | Anonymous (route declared `allowedRoles: ["anonymous"]` in `staticwebapp.config.json`) | None (touches no row-scoped data) | None — `req` is explicitly deleted at `ping.py:47` | Delegated to SWA platform anti-abuse | `tcp.func.ping.complete` / `failed` events | `@@VERSION` reconnaissance surface (MN-01); anonymous DoS via repeated SQL resume — bounded by SQL Free Offer 60-min auto-pause and SQL Free vCore budget |
| `TimerTrigger_BacpacExport` | MI-only (`DefaultAzureCredential` for Mgmt + Storage scopes) | SQL DB Contributor on database (Bicep `sql.bicep:184`) + Storage Blob Data Contributor on `bacpac-exports` container (Bicep `storage.bicep:175`) | No external input; Pydantic `BacpacConfig.from_env` with `SecretStr` for both secrets; URI built from validated config | Timer platform (once weekly Sunday 08:00 RO); 30-min poll cap | `tcp.bacpac.{request,export_started,poll,complete,failed,skipped}` events | `STORAGE_ACCOUNT_KEY` and `SQL_ADMIN_PASSWORD_EXPORT` in App Setting via KV reference — see MJ-03 (storage key path) |
| `TimerTrigger_DailyGenerator` | MI-only; `set_admin_session_context(conn, TCP_GENERATOR_OID)` | MI is registered in `dim_UserRoles` with `scope='admin'` by postprovision Step 1; RLS BLOCK on INSERT joins `dim_UserRoles` | No external input; `tcp.synth` reads `dim_*` rows via parameterised SQL; generator output is internal | Timer platform (weekdays 07:00 RO); idempotent via `usp_GenerateDailyTrades` returning `already_generated` | `tcp.func.daily_generator.{complete,failed}` events | None |
| `WarmupTrigger` | MI-only; `bypass_session_context=True` (no SESSION_CONTEXT needed for `SELECT 1`) | `tcp_admin` / control-plane SQL DB Contributor (MI role) | No external input | Timer platform (weekdays 06:55 RO) | `tcp.func.warmup.{complete,failed}` events | None |
| Raw Function App URL (`func-tcp-prod-weu.azurewebsites.net/api/ask`) — forgery threat | `X-SWA-Forwarded` shared secret rejects anything not gated through SWA `forwardingGateway.requiredHeaders` (`hmac.compare_digest` constant-time); checked FIRST at `ask.py:508-517` | Same as `POST /api/ask` after the gate | Same | Same | Same | Mitigated; the only way to bypass is to exfiltrate `SWA-FORWARDED-SECRET` from KV first |
| SQL Server public endpoint | AAD-only (post-flip); audit logs to LA | `tcp_ai_assistant` / `tcp_generator` / `tcp_bi_reader` / `tcp_admin` DB roles | TDS over TLS 1.2; firewall = AllowAllAzureServices virtual rule | Free Offer auto-pause after 60 min idle bounds compute spend | `SQLSecurityAuditEvents` to LA | `0.0.0.0` virtual rule documented (MN-07 / sql.bicep MJ-04 annotation); ATP / Defender for SQL not available on Free Offer |
| KV public endpoint | AAD + RBAC; MI = Secrets User, OIDC SP = Secrets Officer | KV `defaultAction: 'Allow'` (documented trade-off) | KV REST API enforces AAD bearer tokens | KV platform rate limits | KV `AuditEvent` to LA | `defaultAction: 'Allow'` + purge-protection off (thesis trade-offs, documented) |
| Storage Account public endpoint | MI for runtime + BACPAC writes; account-key for `AzureWebJobsStorage` (MJ-03 trade-off) | Function MI → Storage Blob Data Contributor on `bacpac-exports` container only (narrow grant) | Storage REST API; SAS not generated by code | Storage platform rate limits | `StorageRead/Write/Delete` to LA | Account-key path retained — MJ-03 |
| GitHub Actions runners | OIDC federated credential (no static secrets) | `permissions: contents: read; id-token: write` per workflow | `gitleaks` + `bandit` + `pip-audit` on every PR | n/a | n/a | OIDC trust subject scoped to `repo:<owner>/tcp:...`; rotation = re-issue federated credential on tenant compromise |
| Developer workstation | Local `.env` (gitignored); `azd env set ANTHROPIC_API_KEY ...` for provisioning | n/a | `local.settings.json.template` is the source of truth for shape only | n/a | Local pytest / mypy / ruff | Bootstrap window admin password exposure for the operator's session only |

---

## SQL injection re-audit (walk-through of every `cursor.execute` call)

| File:line | SQL | Binding | Disposition |
|---|---|---|---|
| `tcp/db.py:264` | `EXEC sp_set_session_context @key=N'aad_object_id', @value=?, @read_only=1` | `oid_str` (str-coerced UUID) | SAFE — parameterised; UUID type-bound by `SessionContext` pydantic model |
| `tcp/db.py:270` | `EXEC sp_set_session_context ... @value=NULL, @read_only=0` | none | SAFE — static literal |
| `tcp/db.py:310` | `EXEC sp_set_session_context @value=?` | `str(mi_object_id)` (UUID) | SAFE — parameterised |
| `tcp/db.py:329` | `SELECT CAST(SESSION_CONTEXT(N'aad_object_id') AS UNIQUEIDENTIFIER)` | none | SAFE — static literal |
| `function_app/triggers/ping.py:54` | `SELECT @@VERSION` | none | SAFE injection-wise; see MN-01 for info-disclosure |
| `function_app/triggers/warmup.py:51` | `SELECT 1` | none | SAFE |
| `function_app/triggers/ask.py:341-345` | `SELECT TOP 1 scope FROM dbo.dim_UserRoles WHERE aad_object_id=? AND is_active=1` | `str(oid)` (UUID) | SAFE — single bound param, UUID type-bound; SELECT-only; result is single-row; connection closed immediately (ADR-005). The SQL string is a constant literal. |
| `function_app/triggers/ask.py:380` | `validated.sanitized_sql` (re-serialised by sqlglot) | none | SAFE — the executed string is sqlglot's re-serialised IR, not the LLM's raw text; passed through deny-list + AST allowlist + per-CTE row-cap + statement-type check. See safe_query deep-walk. |
| `function_app/triggers/bacpac_export.py` | All HTTP requests; no `cursor.execute` | n/a | n/a (REST against Management/Storage APIs) |
| `tcp/synth/runner.py:130` | `_SQL_SELECT_ACTIVE_TRADERS` (static) | none | SAFE |
| `tcp/synth/runner.py:139` | `_SQL_SELECT_MARKETS` (static) | none | SAFE |
| `tcp/synth/runner.py:169` | `_SQL_SELECT_SESSIONS` (static) | none | SAFE |
| `tcp/synth/runner.py:183` | `_SQL_SELECT_ORDER_TYPES` (static) | none | SAFE |
| `tcp/synth/runner.py:195` | `SELECT TOP 1 calendar_date FROM dbo.dim_Date WHERE calendar_date < ? AND is_weekday=1 AND is_ro_holiday=0` | `today` (Python `date`) | SAFE — parameterised; date type |
| `tcp/synth/runner.py:331` | `EXEC dbo.usp_GenerateDailyTrades @trade_date=?, @trades=?` | `target` (date), `payload_json` (str from `json.dumps`) | SAFE — parameterised. `payload_json` is internal JSON-serialised data, never user input. The SP accepts NVARCHAR(MAX). |
| `tcp/synth/seed_employees.py:339,344,349` | Static `SELECT COUNT(*)` queries | `_COMPANY_ID` (int constant) or none | SAFE |
| `tcp/synth/seed_employees.py:357-367` | `MERGE dbo.dim_Employees USING (SELECT ? AS company_id, ? AS floor_id, ...) AS src ON ...` | 8 positional `?` placeholders (Faker-generated `_EmployeeRow` fields) | SAFE — all parameterised, all type-bound, all Faker-generated (never external HTTP input) |
| `tcp/synth/seed_employees.py:372` | `SELECT employee_id FROM dbo.dim_Employees WHERE email = ?` | `emp.email` (Faker) | SAFE |
| `tcp/synth/seed_employees.py:402` | `UPDATE dbo.dim_Employees SET manager_employee_id=? WHERE employee_id=?` | `manager_id`, `email_to_id[...]` (ints from DB) | SAFE |
| `tcp/synth/seed_employees.py:409-416` | `MERGE dbo.dim_Accounts USING ...` | 5 positional `?` placeholders (ints and string constants) | SAFE |

**Conclusion**: No injection vector found through any production code path. Every dynamic SQL value is parameterised; every static SQL is a Python `Final[str]` constant that does not interpolate runtime values. The only place where dynamic strings are *constructed* is `safe_query.validate_proc_call` (`f"EXEC dbo.{proc_name} {placeholders}"`), where `proc_name` is allowlist-validated upstream and `placeholders` is a join of constant-key tokens from `_PROC_SIGNATURES` — both load-bearing constraints.

---

## Secret-management audit (every potentially-sensitive literal in the repo with disposition)

| Pattern matched | Files | Disposition |
|---|---|---|
| `YourStrong!Passw0rd` | `docker-compose.dev.yml`, `docs/dev_setup.md`, `db/README.md`, `function_app/README.md` | DEV-ONLY placeholder. Allowlisted in `.gitleaks.toml`. Local-loopback-only via `127.0.0.1:1433:1433` binding in `docker-compose.dev.yml`. Documented as "DO NOT use in any shared environment." |
| `p@ssw0rd!` | `tcp/README.md`, `tests/unit/test_db.py` | DEV-ONLY illustrative literal. Allowlisted in `.gitleaks.toml`. |
| `sk-ant-test` | `tests/unit/test_ai_anthropic_client.py:106,112` | TEST PLACEHOLDER. Format `sk-ant-...` is the Anthropic key prefix; `sk-ant-test` is intentionally invalid. |
| `sk-ant-...` | `tests/integration/test_ask_endpoint.py:20` | DOCSTRING runbook reference — instructs the operator to `export ANTHROPIC_API_KEY=sk-ant-...` (the ellipsis is the secret). Not a literal secret. |
| `REPLACE_WITH_LOCAL_PASSWORD` | `function_app/local.settings.json.template` | TEMPLATE PLACEHOLDER. The real `local.settings.json` is `.gitignore`-d (root `.gitignore:32`). |
| `dev-shared-secret` | `function_app/local.settings.json.template:12` | TEMPLATE PLACEHOLDER for `SWA_FORWARDED_SECRET`. Local-only. |
| `set-real-key-for-Etapa-5` | `function_app/local.settings.json.template:13` | TEMPLATE PLACEHOLDER for `ANTHROPIC_API_KEY`. |
| `00000000-0000-0000-0000-000000000000` | `function_app/local.settings.json.template:11` | All-zeros UUID placeholder for `TCP_GENERATOR_OID`. |
| `TODO author name`, `TODO`, `'TODO'` | `pyproject.toml`, `infra/main.bicep` (owner/repo tags), `docs/design/02_database_design.md` (Author/Advisor) | INTENTIONAL TODO per CLAUDE.md "Placeholders for author/screenshots". |
| `TODO-checksum-set-by-CI` | `db/migrations/V001__init.sql:1293`, `V002__synth_logic.sql:280` | TODO — see MJ-04 above; checksum should be computed by CI on apply. |
| `<TENANT_ID>`, `<value-set-by-postprovision>` | `swa/staticwebapp.config.json:8,33` | RUNTIME PLACEHOLDERS substituted by `postprovision.{sh,ps1}` Step 2c. |

**`.gitignore` coverage verified**: `.env*`, `function_app/local.settings.json`, `swa/local.settings.json`, `infra/parameters.local.json`, `infra/.azd/`, `azd.env`, `.azure/`, `.claude/settings.local.json`. No untracked `.env` files.

**Pydantic `SecretStr` consistency**: `AnthropicConfig.api_key`, `BacpacConfig.sql_admin_password`, `BacpacConfig.storage_account_key` — all three sensitive payloads use `SecretStr`. `repr(BacpacConfig)` produces `SecretStr('**********')` for all three; the secret value is only unwrapped at the actual API-call site (`get_secret_value()` once for each, immediately before the HTTP body construction). The SQL admin export password is read from KV only by the BACPAC trigger; it is never logged.

**`_redact()` coverage**: `tcp/db.py:30-32` regex `((?:PWD|Password)=)([^;]*)` is case-insensitive (`re.IGNORECASE`) and covers both forms. Tested in `tests/unit/test_db.py`. Acceptable; would not catch a non-standard ODBC key (e.g., `Authentication=...` if it ever held a secret) — `Authentication=` does not carry a secret in this design (AAD modes use bearer tokens fetched separately), so the regex is sufficient.

---

## Dependency audit

| Dep | Pin (`pyproject.toml`) | Pin (`function_app/requirements.txt`) | CVE check (May 2026 advisory knowledge) | Verdict |
|---|---|---|---|---|
| `pyodbc` | `>=5.1` | `>=5.1` | No known criticals; 5.1.x line is current | OK |
| `azure-identity` | `>=1.17` | `>=1.17` | 1.17.x is recent; no critical CVEs against the auth flows used | OK |
| `pydantic` | `>=2.7` | `>=2.7` | Pydantic 2.x line; no known critical CVEs | OK |
| `polars` | `>=1.0` | (not used in Function App runtime) | 1.x stable; no known critical CVEs | OK |
| `structlog` | `>=24.1` | `>=24.1` | No known CVEs | OK |
| `faker` | `>=26.0` | `>=26.0` | No known CVEs | OK |
| `httpx` | `>=0.27` | `>=0.27` | 0.27.x line; no known critical CVEs against `httpx.Client` usage | OK |
| `anthropic` | `>=0.40` | `>=0.40` | Anthropic SDK; SDK-level patches surface via Dependabot | OK |
| `sqlglot` | `>=23.0` | `>=23.0` | 23.x stable; the only validator dependency that matters — `pip-audit` strict in CI | OK |
| `azure-functions` | (transitive via function_app) | `>=1.21` | Functions Python worker; tracked by Azure | OK |
| `pip-audit` (dev) | `>=2.7` | n/a | Tool itself; runs in CI | OK |
| `bandit` (dev) | `>=1.7` | n/a | Tool itself; SAST | OK |

**Verdict**: All dependencies use lower-bound `>=` pins, acceptable for the thesis posture. `pip-audit --strict` in `secret-scan` CI job will fail on any new CVE that breaches the floor. Lockfile (`uv.lock`) is regenerated on every CI run from `pyproject.toml`; for stronger supply-chain assurance, commit `uv.lock` (Etapa 8 follow-up).

---

## Deferred / accepted residual risks

| ID | Description | Decision rationale | Tracking |
|---|---|---|---|
| RR-01 | `Storage allowSharedKeyAccess: true` | `AzureWebJobsStorage` boot path requires a connection string; identity-based path adds RBAC and a documented migration | `docs/security/credentials_rotation.md` (per `storage.bicep:50-55` comment) |
| RR-02 | KV `defaultAction: 'Allow'` | Free-tier — no static IP for OIDC runners; bypass:AzureServices is a no-op when default=Allow; RBAC + AAD remain | `keyvault.bicep:98-110` annotation; would flip on private endpoint |
| RR-03 | KV `enablePurgeProtection: false` | Thesis cycle allows `azd down` full purge; 7-day soft-delete provides minimal recovery | `keyvault.bicep:73` annotation; STATE.md "flip post-defense" reminder |
| RR-04 | `AllowAllAzureServices` SQL firewall | Y1 Consumption has dynamic outbound IPs; no service-tag firewall rule available | `sql.bicep:75-87` annotation; documented migration to Flex Consumption |
| RR-05 | Single-instance rate-limit | Y1 typically runs one worker; cluster-wide ledger requires Table Storage + RBAC + per-request 10ms RTT | ADR-005; would migrate to Azure Table Storage ledger for production |
| RR-06 | App Insights custom-metrics deferred | `azure-monitor-opentelemetry` SDK pull adds boot cost; structured-log dimensions cover the academic story | ADR-005 §3; Etapa-8 work item |
| RR-07 | SQL Advanced Threat Protection / Defender for SQL absent | Paid feature, unavailable on Free Offer | Documented in posture; production migration would enable Defender for SQL Standard tier |
| RR-08 | Bootstrap window for SQL-auth + `AllowAllAzureServices` | Minutes between provision and AAD-only flip; `newGuid()`-derived 120-bit password limits exposure | `docs/security/bootstrap_window.md` per MN-07; postprovision Step 3 runs immediately after schema apply |
| RR-09 | `schema_history.checksum` TODO | Synthetic data; manual schema control | MJ-04 above |

---

## Frontend XSS / CSRF re-audit

- `swa/app.js`: every render path uses `el(tag, attrs, textContent)` which calls `node.textContent = ...`. No `innerHTML` with user-controlled data anywhere. No `eval()`, no `Function('...')`, no `document.write`. Confirmed by literal grep.
- CSP: `default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'`. No `unsafe-inline`. No `unsafe-eval`. The `<script src="./app.js" defer>` and `<link rel="stylesheet" href="./style.css">` are same-origin and satisfy `'self'`.
- CSRF: `/api/ask` is authenticated by the server-injected `x-ms-client-principal` (browser cannot forge) plus the `X-SWA-Forwarded` shared secret (also server-injected by `forwardingGateway`). The browser sends neither manually. No additional CSRF token required.
- `<form>` uses `requestSubmit()` not `submit()` — the submit handler always runs with input validation. `autocomplete="off"` on the form and `maxlength="500"` on the input mirror the server-side limit.
- Suggested-question buttons are hard-coded HTML in `index.html`; no server-driven content is injected.

---

## Prompt-injection re-audit

- The user's question is rendered into the messages array via `build_user_message(question, scope)` → `f"User scope: {scope}. Question: {question}"`. The scope is validated against `_VALID_SCOPES` (defence-in-depth even though the trigger layer enforces the same set upstream — closes sec MN-02).
- The system prompt (`SCHEMA_SYSTEM_PROMPT`) has an explicit "Do not emit any reference to SESSION_CONTEXT, sp_set_session_context, dim_UserRoles, or fact_Trades directly — the validator rejects all of these." This is informational for the model; the load-bearing defence is the SQL validator.
- The LLM output is treated as untrusted: `validate(answer.sql)` walks the AST and rejects anything outside the allowlist. The model cannot self-grant elevated SQL — even if it emits `EXEC sp_set_session_context @value='<admin-oid>'`, the deny-list catches `sp_set_session_context` before sqlglot parsing; even if it tries to obfuscate via comments, NFKC normalisation + comment-token rejection catches it; even if it tries to reference `dim_UserRoles`, the allowlist excludes it.
- The forced `tool_choice={"type": "tool", "name": "emit_sql"}` constrains the response to the structured envelope; free-form `text` blocks are not honoured by the parser.
- Token-budget guard: `PromptTooLargeError` fires when the wrapped user message exceeds `max_input_tokens` (rough 4-chars/token heuristic — see Etapa-6 backend review MN-04). Acceptable best-effort guard; a determined cost-DoS attacker would still hit the rate-limit gate first.

---

## Logging / telemetry side-channel re-audit

- `_TcpJsonEncoder.default()` raises `TypeError` on unknown types (including `SecretStr`) — no silent `str(value)` fallback. The `answer.usage.model_dump()` produces plain ints; `SecretStr` is never reached.
- OID logged as suffix only at every call site. Discrepancy in length flagged as MN-02.
- Stack traces never escape to HTTP response bodies — `ask.py` catches every typed exception and returns a `_envelope` with a static error message. Anthropic / pyodbc errors are logged with `error=str(exc)[:200]` truncation.
- 422 message is generic ("The generated query was rejected by the safety validator.") — closes ai MA-06. Test `test_ask_validation_failure_returns_generic_422` asserts `"DROP"`, `"fact_Trades"`, `"DisallowedTokenError"` are absent from the wire body.
- BACPAC API error bodies logged at DEBUG with 256-char snippet (`bacpac_export.py:253-257, 299-303`) — DEBUG is typically sampled out in production App Insights.
- `_redact()` covers `PWD=` and `Password=` (case-insensitive) on conn-string logging. MN-04 above suggests demoting the conn-string-log line to DEBUG for additional safety.

---

## GDPR-readiness (synthetic-data context)

- **No real PII**: `dim_Employees` rows are Faker-generated under `locale="ro_RO"`. The `email` column lands at `@tcp-capital.ro` domain. The check constraint `CK_dim_Employees_email_domain` (V001 line 150-151) enforces the domain.
- **Right-to-erasure**: `usp_DeleteEmployeeData` is not implemented (see MN-03 above). Informational for the thesis posture; would be a hard requirement for a real deployment.
- **Data residency**: Resource group region constrained to `westeurope` (primary) / `northeurope` (fallback) in `main.bicep` `@allowed()` list. Both are EU regions.
- **Data minimisation**: No `address`, `phone_number`, `nin`, `iban`, `salary` columns in `dim_Employees`. The schema-system-prompt refusal policy explicitly refuses any question about IBANs/salaries/personal data.
- **Auditability**: SQL `SQLSecurityAuditEvents` to LA; KV `AuditEvent` to LA; Functions `FunctionAppLogs` + `AppServiceHTTPLogs` to LA. 30-day retention on the free tier.

---

## Operational hardening recommendations (production-grade)

1. **Identity-based `AzureWebJobsStorage`** (MJ-03): grant Function MI `Storage Blob Data Owner` + `Storage Queue Data Contributor` + `Storage Table Data Contributor`, flip `allowSharedKeyAccess: false`, replace the conn-string app setting with `AzureWebJobsStorage__accountName` + `AzureWebJobsStorage__credential=managedidentity`.
2. **Full HTTP-header set** (MJ-02): add `Strict-Transport-Security`, `X-Frame-Options`, `Permissions-Policy`, `Cross-Origin-Resource-Policy`, `Cross-Origin-Opener-Policy` to `swa/staticwebapp.config.json globalHeaders`.
3. **`Retry-After` on 429** (MJ-01): one-line fix in `_envelope` or at the 429 return site.
4. **CI-computed `schema_history.checksum`** (MJ-04): SHA-256 the migration file content and write it into the history row via parameterised UPDATE post-apply.
5. **Cluster-wide rate limit** (RR-05): migrate to Azure Table Storage ledger for production traffic, or to APIM consumption tier.
6. **Defender for SQL Standard** (RR-07): once on a paid SQL tier, enable ATP / vulnerability assessment.
7. **`Deny` default on KV + Storage with explicit allowlist** (RR-02): on Flex Consumption with stable outbound IPs, flip to `defaultAction: 'Deny'` and allowlist Function App + GitHub-Actions egress IPs (or self-hosted runner).
8. **Lockfile** (`uv.lock`): commit to repo for deterministic CI installs.
9. **Pin first-party Actions to SHAs**: `actions/checkout`, `actions/setup-python`, `azure/login`, `Azure/setup-azd` — currently tag-pinned, which is acceptable for first-party Microsoft/GitHub Actions but SHA-pinning is the higher-assurance posture.
10. **AAD tenant MFA**: documented TODO in `03_arch §8.3`; required for any production deployment.

---

## Recommendation

**ACCEPT_WITH_CHANGES.** Four major findings (MJ-01..MJ-04) are documented production-readiness items that do not block the `v1.0-mvp` thesis tag. Ten minor findings cover hygiene polish. The defence-in-depth on the AI/SQL surface (deny-list + AST allowlist + RLS predicate + SESSION_CONTEXT contract + bypass escape hatch + parameterised everything + `SecretStr` everywhere) is substantially stronger than typical production AI-assistant backends. The single biggest residual concern is the SQL Server's `AllowAllAzureServices` virtual firewall rule: it is the right trade-off for a $0/month free-tier design (no service-tag firewall rule is available on Consumption Y1), but every Azure tenant's outbound traffic can reach the TLS+AAD endpoint, and the bootstrap window before the AAD-only flip is the highest-residual moment of the deployment. Pair it with the documented `docs/security/bootstrap_window.md` runbook and proceed.
