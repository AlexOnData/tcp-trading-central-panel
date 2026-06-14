# Threat Model — TCP Trading Central Panel

**Version**: 1.0
**Date**: 2026-05-16
**Status**: Active
**Scope**: Production deployment on Azure free tier; thesis / academic posture

---

## 1. System Overview

TCP — Trading Central Panel is an employee performance analytics platform for TCP Capital Management SRL. It models a two-floor, six-team, 32-employee organisational hierarchy, generates synthetic daily trading activity via a weekday timer, and exposes the data through PowerBI dashboards (Import mode, 07:30 RO scheduled refresh) and an Anthropic Claude `claude-haiku-4-5`-powered AI assistant. The assistant accepts natural-language questions from AAD-authenticated employees, translates them into SQL via a single Anthropic call with prompt caching, validates the generated SQL through a three-layer AST pipeline (`safe_query.py`), executes it under per-user row-level security enforced by SQL Server SESSION_CONTEXT, and returns results as JSON. A weekly Sunday BACPAC export trigger provides a durable snapshot in Azure Blob Storage. All operations run at zero recurring cost on Azure free tiers: Azure SQL Free Offer (serverless), Azure Functions Consumption Y1, Azure Static Web Apps Free, Azure Key Vault Standard, and Application Insights / Log Analytics.

---

## 2. Trust Boundaries

The diagram reference is `docs/diagrams/architecture.mmd`. Each boundary is listed below with its enforcement mechanism.

### TB-1: Internet ↔ Static Web App

**Direction**: inbound user traffic.
**Enforcement**: Azure Static Web Apps platform handles TLS termination (managed certificate) and AAD interactive sign-in. All routes under `/api/*` require the `authenticated` role (`staticwebapp.config.json`). Unauthenticated browsers are redirected to `/.auth/login/aad` before any backend call is made.

### TB-2: Static Web App ↔ Function App (linked backend)

**Direction**: inbound to Function App from SWA platform.
**Enforcement**: SWA `forwardingGateway.requiredHeaders` injects the `X-SWA-Forwarded` header carrying a secret value sourced from KV (`SWA-FORWARDED-SECRET`). The Function App validates this header via `hmac.compare_digest` at the first line of the request handler before processing any other input. Requests missing or carrying the wrong value receive HTTP 403. SWA also injects `x-ms-client-principal` (base64-encoded AAD claims); this header cannot be forged by the browser because the SWA platform injects it server-side on the Microsoft backbone. Traffic between SWA and the Function App does not traverse the public internet.

### TB-3: Function App ↔ Key Vault

**Direction**: outbound from Function App.
**Enforcement**: Function App system-assigned Managed Identity; RBAC role `Key Vault Secrets User` (read-only) on `kv-tcp-prod-weu`. No static credentials; no client secret. KV uses `enableRbacAuthorization: true`. Secrets are surfaced as Key Vault references in Function App settings (resolved at startup/restart, not per request).

### TB-4: Function App ↔ SQL Database

**Direction**: outbound from Function App.
**Enforcement**: MI-based AAD token authentication (`DefaultAzureCredential`); TDS over TLS 1.2. SQL Server has AAD-only authentication enabled post-bootstrap (`azureADOnlyAuthentication: true`). Per-request identity forwarding via `EXEC sp_set_session_context @key=N'aad_object_id', @value=@oid, @read_only=1` immediately after connection check-out. RLS BLOCK predicate on `fact_Trades` joins `dim_UserRoles` on the session context value. Connection pool hygiene: context cleared (`@value=NULL, @read_only=0`) on check-in. See `docs/decisions/ADR-003-rls-session-context.md`.

### TB-5: Function App ↔ Anthropic API

**Direction**: outbound HTTPS to `api.anthropic.com`.
**Enforcement**: `ANTHROPIC_API_KEY` loaded from KV reference at startup (not per-request KV hit). Key stored as `SecretStr` in `AnthropicConfig`; `get_secret_value()` called exactly once per Anthropic SDK invocation. Outbound HTTPS only; no user-controlled URL components.

### TB-6: Function App ↔ Azure Management API

**Direction**: outbound HTTPS (REST) from BACPAC export trigger.
**Enforcement**: `DefaultAzureCredential` (MI bearer token); scope `https://management.azure.com/`. The `SQL DB Contributor` role on the database resource authorises the `New-AzSqlDatabaseExport` control-plane action. No user-supplied input reaches this code path.

### TB-7: GitHub Actions ↔ Azure

**Direction**: outbound from GitHub Actions runners.
**Enforcement**: OIDC federated credentials — no static secrets stored in GitHub. `permissions: { id-token: write, contents: read }` per workflow. Federated credential subject claims scoped to specific `repo:<owner>/<name>:ref:refs/heads/<branch>` and `environment:` patterns. Third-party actions pinned to full commit SHAs with version comments.

---

## 3. Assets

The following are the assets the threat model protects, ranked by actual value:

| Asset | Location | Sensitivity | Why it matters |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | KV `ANTHROPIC-API-KEY`; Function App KV reference | **High** | Real monetary cost if leaked — every API call is billed. Exfiltration could run up charges before detection. |
| OIDC SP identity (`sp-tcp-github-cicd`) | Azure Entra ID app registration | **High** | Carries `Contributor` on `rg-tcp-prod-weu`; a token issued to this SP could modify or delete all project resources. Short-lived OIDC tokens mitigate blast radius but token exfiltration from a compromised runner remains the primary risk. |
| `SWA-FORWARDED-SECRET` | KV `SWA-FORWARDED-SECRET` | **Medium** | Exfiltration allows forging requests directly to the raw Function App URL, bypassing the SWA AAD gate. Combines with a forged `x-ms-client-principal` to impersonate any registered user. |
| `SQL-ADMIN-PASSWORD-EXPORT` | KV `SQL-ADMIN-PASSWORD-EXPORT` | **Medium** | Used only by the Azure Management API BACPAC export action; not usable for interactive SQL login post-AAD-only flip. Leakage allows only control-plane export operations, not data-plane SQL access. |
| `STORAGE-CONNECTION-STRING` | KV `STORAGE-CONNECTION-STRING`; `AzureWebJobsStorage` setting | **Medium** | Account-key access to the entire storage account, including Functions internal containers (`azure-webjobs-hosts`, `azure-webjobs-secrets`). Exfiltration could expose Function host keys. See residual RR-01. |
| Synthetic employee + trade dataset | Azure SQL Database `sqldb-tcp-prod-weu` | **Low** | Data is 100% Faker-generated; no real PII. Value is academic (thesis demo). Cross-tenant exposure (an employee querying another employee's trades) is the relevant privacy concern within the system, not external exfiltration. |
| SQL schema and view definitions | Azure SQL Database | **Low** | Schema is version-controlled in `db/migrations/` and therefore public within the repo. No proprietary algorithms. |

---

## 4. Adversaries

### A1 — Opportunistic Web Scanner

A bot or automated scanner that discovers the SWA URL or the raw Function App URL and probes for common vulnerabilities (unauthenticated endpoints, path traversal, default credentials, open CORS).

**Capability**: low. No Azure tenant membership. No access to KV secrets. No knowledge of the system design.
**Motivation**: volume-based exploitation (credential harvesting, compute abuse).
**Mitigations**: AAD on all non-ping routes; shared-secret header rejects raw Function URL access; parameterised SQL; no path traversal surface; CORS denied by default.

### A2 — Malicious Authenticated User

A valid AAD account holder (e.g., an employee with `trader` scope) who is curious about other employees' data or attempts to abuse the AI assistant to exfiltrate or modify data.

**Capability**: medium. Holds a valid AAD token. Can craft arbitrary question strings. Cannot modify the SWA-injected headers.
**Motivation**: data curiosity, competitive advantage, vindictiveness.
**Mitigations**: RLS block predicate enforces scope; `safe_query.py` rejects non-SELECT and disallowed table references; `SESSION_CONTEXT` is locked read-only after set; rate limit (10/min/OID); `dim_UserRoles` excluded from AI allowlist.

### A3 — Leaked OIDC Token

A GitHub Actions third-party action (pinned to SHA but potentially compromised via a supply-chain attack) that exfiltrates the OIDC token minted during a workflow run.

**Capability**: high but time-limited. The OIDC token's lifetime is minutes; the federated credential's subject claim is scoped to a specific branch/environment. A leaked token provides `Contributor` access on the resource group for its remaining lifetime.
**Motivation**: supply-chain attack for lateral movement into Azure tenants.
**Mitigations**: SHA-pinned third-party actions; `Contributor` scoped to single RG (not subscription); OIDC subject claim restricts token to specific branch; short token TTL; Azure activity log captures all resource-group mutations.

### A4 — Leaked KV Secret

A compromised Function App process (e.g., via a dependency vulnerability in `anthropic`, `pyodbc`, or `sqlglot`) that exfiltrates one or more secrets from the process environment.

**Capability**: medium. Secrets are available in the process environment via KV references. A memory-read or env-dump exploit could surface `ANTHROPIC_API_KEY`, `SWA-FORWARDED-SECRET`, or `STORAGE-CONNECTION-STRING`.
**Motivation**: API key abuse (Anthropic cost); access to storage (Function host keys); downstream lateral movement.
**Mitigations**: `SecretStr` prevents accidental logging; `pip-audit --strict` in CI; `bandit` SAST; dependency lower-bound pins; App Insights alerting on spend anomalies.

### A5 — Nation-State Attackers

**OUT OF SCOPE.** Advanced persistent threats with resources for zero-day exploitation, physical access to Azure datacenters, or cryptographic attacks on TLS are explicitly out of scope for this academic project.

### A6 — Insider Threats with Physical Access

**OUT OF SCOPE.** Malicious actors with physical access to the developer's workstation or Azure admin console are out of scope. The developer's owner-scoped AAD account is the highest-privilege identity; its compromise is a domain-admin equivalent event beyond the thesis threat model.

---

## 5. STRIDE Matrix per Surface

The 11 surfaces from the security-auditor's report are modelled below. Cells contain the specific threat and its primary mitigation.

### Surface 1: `POST /api/ask` (SWA-fronted)

| STRIDE | Threat | Mitigation |
|---|---|---|
| **Spoofing** | Attacker forges `x-ms-client-principal` to impersonate a different user. | Shared-secret `X-SWA-Forwarded` checked via `hmac.compare_digest` before principal parse; forged principal alone is insufficient. |
| **Tampering** | Attacker modifies the question in transit to inject SQL. | TLS on SWA → browser and SWA → Function (backbone). `safe_query.py` deny-list + AST + re-serialisation on LLM output. |
| **Repudiation** | User denies issuing a question. | `tcp.func.ask.metrics` event with `oid[-8:]` suffix logged to App Insights; SQL audit log captures SESSION_CONTEXT. |
| **Information disclosure** | LLM returns another user's trade data via a crafted query. | RLS BLOCK predicate; SESSION_CONTEXT locked read-only; `dim_UserRoles` excluded from AI allowlist; table allowlist in `safe_query.py`. |
| **DoS** | User floods the endpoint to exhaust Anthropic budget. | In-process rate limit 10/60 s per OID under `threading.Lock`; `max_output_tokens=600`; prompt cache reduces per-request cost. Missing `Retry-After` header is MJ-01 residual. |
| **Elevation** | User with `trader` scope queries `admin`-scope data or invokes write operations. | Scope validated against `_ALLOWED_SCOPES` post-query; `tcp_ai_assistant` SQL role is SELECT-only on `v_*` views; `fact_Trades` INSERT path is a separate DB role not granted to the AI connection. |

### Surface 2: `GET /api/ping` (anonymous)

| STRIDE | Threat | Mitigation |
|---|---|---|
| **Spoofing** | N/A — endpoint is intentionally anonymous. | No identity claim expected or parsed. |
| **Tampering** | N/A — no body or query param consumed (`req` deleted at line 47). | Static response. |
| **Repudiation** | N/A — no user-scoped action. | `tcp.func.ping.complete` / `failed` events logged. |
| **Information disclosure** | `SELECT @@VERSION` response leaks SQL Server build string to enable version-targeted exploit selection. | MN-01: recommended fix is `SELECT 1`; `@@VERSION` logged server-side only. Currently a minor residual. |
| **DoS** | Repeated anonymous calls resume the auto-paused SQL instance, consuming vCore-seconds. | SQL Free Offer auto-pause and monthly vCore-second budget act as a natural throttle. SWA platform provides basic anti-abuse. |
| **Elevation** | N/A — no data access. | Static JSON only. |

### Surface 3: `TimerTrigger_BacpacExport`

| STRIDE | Threat | Mitigation |
|---|---|---|
| **Spoofing** | N/A — no external input; MI identity is non-forgeable by external parties. | `DefaultAzureCredential` resolves MI token from IMDS. |
| **Tampering** | BACPAC blob corrupted in storage between export and later restore. | Storage soft-delete (7 days); TLS on all storage REST calls; Blob MD5 integrity check by Storage service. |
| **Repudiation** | Export status uncertain. | `tcp.bacpac.{request,export_started,poll,complete,failed,skipped}` events to App Insights. |
| **Information disclosure** | `SQL-ADMIN-PASSWORD-EXPORT` or `STORAGE-CONNECTION-STRING` logged from Function App. | Both are `SecretStr`; DEBUG-level snippets only (256 chars, typically sampled out); `_redact()` covers connection strings. |
| **DoS** | Export job starved of vCore-seconds, causing weekly backup miss. | 30-minute poll cap; `tcp.bacpac.failed` alert triggers operator notification. |
| **Elevation** | `SQL DB Contributor` RBAC used to modify schema or delete database. | Scope is export-only via Management API; no DDL path; `Storage Blob Data Contributor` is scoped to `bacpac-exports` container only. |

### Surface 4: `TimerTrigger_DailyGenerator`

| STRIDE | Threat | Mitigation |
|---|---|---|
| **Spoofing** | N/A — timer platform internal. | MI identity. |
| **Tampering** | Generated trade data modified post-insert. | INSERT-only via stored procedure; `tcp_generator` role has no DELETE/UPDATE on `fact_Trades`. |
| **Repudiation** | Generator run disputed. | `tcp.func.daily_generator.{complete,failed}` events; SQL audit log. |
| **Information disclosure** | N/A — no user-sourced input; internal data only. | Structured logs; no secrets in output. |
| **DoS** | Timer fires while SQL is paused; wait exceeds function timeout. | WarmupTrigger fires at 06:55 RO (5 minutes before generator at 07:00 RO) to pre-resume the database. |
| **Elevation** | N/A — admin scope is required by design for INSERT into `fact_Trades`. | RLS BLOCK AFTER INSERT predicate verified in CI. |

### Surface 5: `WarmupTrigger`

| STRIDE | Threat | Mitigation |
|---|---|---|
| **Spoofing** | N/A — internal timer. | MI identity; `bypass_session_context=True` documented in ADR-005. |
| **Tampering** | N/A — `SELECT 1`; no data modification. | Static query. |
| **Repudiation** | N/A — `tcp.func.warmup.{complete,failed}` events. | App Insights. |
| **Information disclosure** | N/A — returns no data. | — |
| **DoS** | N/A — bounded to one SQL round-trip per weekday. | — |
| **Elevation** | N/A. | — |

### Surface 6: Raw Function App URL (`func-tcp-prod-weu.azurewebsites.net/api/ask`)

| STRIDE | Threat | Mitigation |
|---|---|---|
| **Spoofing** | Attacker crafts a valid-looking request directly to the raw URL with a forged principal blob. | `X-SWA-Forwarded` shared-secret check at line 1 of handler; forged principal insufficient without the KV-resident secret. |
| **Tampering** | Same as `POST /api/ask`. | Same mitigations apply after the gate. |
| **Repudiation** | `forwarded_secret_mismatch` event logged per rejection. | App Insights. |
| **Information disclosure** | Raw URL exposure in error messages or documentation. | Raw URL omitted from all public-facing documentation; SWA-only path is the documented entry point. |
| **DoS** | Flood of cheap 403 responses (no Anthropic or SQL cost). | Stateless gate at the first line; no resource consumption beyond a constant-time HMAC compare. |
| **Elevation** | N/A — gate rejects before any elevated logic. | — |

### Surface 7: SQL Server public endpoint

| STRIDE | Threat | Mitigation |
|---|---|---|
| **Spoofing** | Attacker presents stolen MI token or forged AAD credential. | AAD-only auth; MI token not transmittable without the IMDS endpoint (internal to Azure). |
| **Tampering** | Attacker modifies rows after authentication. | `tcp_ai_assistant` is SELECT-only; `tcp_generator` INSERT-only on `fact_Trades`; no UPDATE/DELETE grants. |
| **Repudiation** | Query or insert disputed. | `SQLSecurityAuditEvents` to Log Analytics; SESSION_CONTEXT traces user OID. |
| **Information disclosure** | `AllowAllAzureServices` virtual firewall rule permits any Azure tenant's compute to reach the TLS+AAD endpoint. | AAD-only auth means no SQL-auth password attack surface post-flip; brute-force blocked by AAD. ATP / Defender for SQL absent (RR-07). |
| **DoS** | Repeated connections wake auto-paused instance, burning vCore-second budget. | Free Offer `freeLimitExhaustionBehavior: 'AutoPause'` ensures billing never exceeds $0; quota exhaustion pauses the DB, not charges the user. |
| **Elevation** | Cross-database reference or linked-server query. | `safe_query.py` catalog allowlist; `sqlglot` AST rejects cross-database references; DB-level roles are scoped to `sqldb-tcp-prod-weu` only. |

### Surface 8: Key Vault public endpoint

| STRIDE | Threat | Mitigation |
|---|---|---|
| **Spoofing** | Attacker presents forged AAD token to KV REST API. | RBAC + AAD; anonymous KV reads are impossible regardless of `defaultAction: 'Allow'`. |
| **Tampering** | Secret value overwritten by attacker with `Key Vault Secrets Officer` role. | Only the OIDC SP has `Secrets Officer`; Function MI has `Secrets User` (read-only). |
| **Repudiation** | Secret access disputed. | KV `AuditEvent` diagnostic settings to Log Analytics. |
| **Information disclosure** | Secret exfiltration via `Secrets User` role. | MI fetches secrets at app startup via KV references, not per-request; `SecretStr` prevents accidental log exposure. |
| **DoS** | KV rate-limit exhaustion (10 000 ops/month typical threshold). | Startup-resolved KV references (one fetch per restart, not per request). |
| **Elevation** | `defaultAction: 'Allow'` bypasses network ACLs. | No network ACL provides meaningful protection when `defaultAction` is `Allow`; RBAC is the sole gate. See residual RR-02. |

### Surface 9: Storage Account public endpoint

| STRIDE | Threat | Mitigation |
|---|---|---|
| **Spoofing** | Attacker uses stolen account key to access storage. | Account key held only in KV and in Functions runtime (KV reference); `allowBlobPublicAccess: false`; containers are private. |
| **Tampering** | BACPAC blob modified post-export. | Blob soft-delete (7 days); lifecycle management; TLS only. |
| **Repudiation** | Storage access disputed. | `StorageRead/Write/Delete` diagnostic settings to Log Analytics. |
| **Information disclosure** | `STORAGE-CONNECTION-STRING` (account key) exfiltrated from Function App environment. | Stored in KV; resolved as KV reference; `SecretStr` in `BacpacConfig`. `allowSharedKeyAccess: true` is residual RR-01. |
| **DoS** | N/A — storage is not on the critical latency path. | — |
| **Elevation** | Account key allows access to `azure-webjobs-secrets` container (Function host keys). | See residual RR-01; identity-based migration path documented in MJ-03. |

### Surface 10: GitHub Actions runners

| STRIDE | Threat | Mitigation |
|---|---|---|
| **Spoofing** | Compromised third-party action mints a token for a different workflow. | SHA-pinned third-party actions; OIDC subject claim restricts token to specific branch/environment. |
| **Tampering** | Workflow YAML modified via a malicious PR. | `permissions: contents: read` on PR workflows; `pull_request_target` not used. |
| **Repudiation** | Deployment disputed. | GitHub Actions audit log; `az activity-log` captures all ARM mutations. |
| **Information disclosure** | OIDC token exfiltrated mid-run by a compromised step. | Short token TTL (minutes); `id-token: write` permission granted only to deploy jobs. |
| **DoS** | Workflow spam deploys consuming GitHub Actions minutes. | `concurrency` groups prevent parallel runs on the same branch. |
| **Elevation** | Compromised OIDC token used to elevate to `Contributor` on RG. | Scoped to single RG; short TTL; federated credential subject claim. |

### Surface 11: Developer workstation

| STRIDE | Threat | Mitigation |
|---|---|---|
| **Spoofing** | N/A — local development only. | `.env` is gitignored; bootstrap credentials rotate post-build. |
| **Tampering** | Local `.env` contains real secrets that land in a commit. | `.gitignore` covers `.env*`; `gitleaks` runs on every PR; `gitleaks.toml` allowlists known test placeholders. |
| **Repudiation** | N/A. | — |
| **Information disclosure** | Bootstrap SQL admin password visible in the operator's shell during provision. | Password is `newGuid()`-derived in Bicep; stored in KV; only the postprovision hook reads it programmatically. |
| **DoS** | N/A. | — |
| **Elevation** | Developer's `Owner`-scoped AAD account compromised. | Out-of-scope adversary (A6). |

---

## 6. Bootstrap Window

The interval between `azd provision` completing and the postprovision script's Step 3 executing the AAD-only authentication flip is the **highest-residual security state** of any deployment.

### State during the window

- SQL Server public endpoint is active with `publicNetworkAccess: 'Enabled'` and `AllowAllAzureServices` virtual firewall rule.
- SQL authentication is **enabled** (the default before the flip).
- An admin login exists with a `newGuid()`-derived password (~120 bits of entropy from a cryptographic GUID).
- The password has been written to KV `SQL-ADMIN-PASSWORD-BOOTSTRAP` but not displayed to the operator.
- No interactive user has ever seen the password.
- The schema apply (`sqlcmd -i V001__init.sql` and `V002__synth_logic.sql`) runs in this window using the bootstrap credentials.

### Duration

Typically 3–8 minutes, covering the time between `azd provision` completion and the end of `postprovision.ps1` Step 3 (`Set-AzSqlServerActiveDirectoryOnlyAuthentication -Enable $true`).

### Why this is acceptable

The attack requires an adversary to:

1. Discover the SQL Server public FQDN (`sql-tcp-prod-weu.database.windows.net`) within the ~3–8 minute window — the server does not advertise itself.
2. Know or brute-force a 120-bit entropy password (GUID) — computationally infeasible in the window.
3. Successfully authenticate before the AAD-only flip closes the SQL-auth path.

The combination of the short window, the high-entropy password, and the absence of any public announcement of the FQDN reduces this to an acceptable residual for an academic deployment.

### Recommended operator behaviour

- Run `azd provision` from a network that is not shared with untrusted parties (home broadband or a corporate VPN is sufficient).
- Do not interrupt `postprovision.ps1` after it starts.
- Verify the flip succeeded before stepping away: `az sql server ad-only-auth show --resource-group rg-tcp-prod-weu --name sql-tcp-prod-weu --query azureAdOnlyAuthentication` should return `true`.
- If the window was interrupted or the flip did not complete, run the postprovision script again — it is idempotent.

This window is formally captured as residual risk **RR-08** in section 7 and referenced in the future `docs/security/bootstrap_window.md` runbook.

---

## 7. Residual Risks Accepted

Nine residual risks are accepted for the thesis / academic posture. Each is documented with a follow-up trigger defining when to re-evaluate.

| ID | Description | Justification | Follow-up trigger |
|---|---|---|---|
| RR-01 | `Storage allowSharedKeyAccess: true` retains an account-key path on the storage account. Exfiltration of `STORAGE-CONNECTION-STRING` from KV or the Function App environment grants read/write to all storage containers including `azure-webjobs-secrets`. | `AzureWebJobsStorage` boot path requires a connection string; identity-based connections (`AzureWebJobsStorage__accountName` + `credential=managedidentity`) require three additional RBAC grants and are a documented migration (`infra/modules/storage.bicep` comment, MJ-03). KV-only storage of the key and MI access model reduce the practical risk. | Migrate when moving to production traffic or when Azure Functions adds first-class free-tier support for identity-based storage. |
| RR-02 | KV `defaultAction: 'Allow'` — Key Vault network ACLs are open; RBAC is the sole gate. | Free tier: Y1 Consumption has dynamic outbound IPs and no VNet integration; `defaultAction: 'Deny'` + allowlist requires a stable IP or Private Endpoint (both require Premium plan, ~$170/month). `bypass: AzureServices` is a no-op when default is `Allow`. RBAC + AAD remain effective. | Re-evaluate when migrating to Azure Functions Flex Consumption (stable outbound IPs available). |
| RR-03 | KV `enablePurgeProtection: false` — the Key Vault can be immediately purged after soft-delete, allowing `azd down` to fully clean up. 7-day soft-delete provides minimal recovery. | Thesis lifecycle requires the ability to fully tear down and re-provision the environment. Purge protection would prevent `azd down` from reclaiming the KV name within the retention period. | Flip to `true` post-thesis defense. Tracked in `.claude/STATE.md` "flip post-defense" reminder. |
| RR-04 | `AllowAllAzureServices` SQL firewall virtual rule — any Azure tenant's compute can reach the TLS+AAD SQL endpoint. | Y1 Consumption has dynamic outbound IPs; no service-tag firewall rule for `FunctionApp.WestEurope` is available on the free tier. AAD-only auth means password attacks are blocked; the surface is TLS+AAD only. | Migrate to Flex Consumption with stable IPs and IP-allowlist firewall, or add a Private Endpoint when budget allows. |
| RR-05 | Single-instance rate-limit — the per-OID sliding-window ledger resets on worker cold start; a user can issue `10 × N` requests/minute across `N` worker instances on a scaled-out Y1 plan. | Y1 typically runs one worker for the expected traffic (≤ 30 users × ≤ 10 requests/hour during the demo). A cluster-wide ledger via Azure Table Storage adds per-request 10 ms RTT and an additional RBAC grant. The `tcp.func.ask.rate_limited` metric provides a cluster-wide alerting signal. See `docs/decisions/ADR-005-scope-resolution-rls-bypass.md`. | Migrate to Azure Table Storage ledger or APIM Consumption tier when scaling beyond one worker instance. |
| RR-06 | App Insights custom metrics deferred — `tcp.ask.latency_ms`, `tcp.ask.input_tokens`, etc. are emitted as structured log dimensions (`metric_*` prefix) rather than true `customMetrics` entries. | Wiring `azure-monitor-opentelemetry` SDK adds boot cost; structured-log dimensions cover the academic observability story. KQL against the `traces` table is sufficient for v1.0. | Wire true custom metrics in Etapa 8 production-readiness pass. |
| RR-07 | SQL Advanced Threat Protection / Defender for SQL absent — the Free Offer does not support ATP or vulnerability assessment. | ATP is a paid feature available only on paid SQL tiers. The free tier provides audit logging to Log Analytics as the sole automated threat detection signal. | Enable Defender for SQL Standard tier when migrating to a paid SQL tier. |
| RR-08 | Bootstrap window — 3–8 minutes between `azd provision` and the AAD-only authentication flip, during which SQL-auth is enabled on a public endpoint with a 120-bit entropy admin password. | The attack window is narrow, the password is high-entropy and never displayed, and the FQDN is not publicly announced. See section 6 for full analysis. | Document in `docs/security/bootstrap_window.md`; verify the flip at every reprovisioning. |
| RR-09 | ~~`schema_history.checksum` left as `'TODO-checksum-set-by-CI'`~~ — **CLOSED in Etapa 8.** Migrations now carry `__V<n>_CHECKSUM__` placeholders that `infra/scripts/postprovision.{ps1,sh}` Step 0 substitutes with the canonicalised SHA-256 (computed by `scripts/compute_migration_checksum.py`) before piping the file to sqlcmd. The CI gate (`ci.yml › sql-lint`) and the CD smoke job (`cd.yml › smoke`) both fail when an unsubstituted placeholder appears in `dbo.schema_history`. | n/a (resolved) | n/a (resolved) |

---

## 8. OWASP Top 10 Mapping

Full evidence matrix in `docs/design/reviews/review_etapa6_security_sweep.md` (OWASP Top 10 section).

| Item | Verdict | Summary |
|---|---|---|
| A01 Broken Access Control | **STRONG** | RLS BLOCK predicate enforced at DB level; deny-by-default when SESSION_CONTEXT unset; `dim_UserRoles` excluded from AI allowlist; `tcp_ai_assistant` is SELECT-only. |
| A02 Cryptographic Failures | **STRONG** | TLS 1.2 floor across all resources; `Encrypt=yes` in pyodbc; all secrets in KV via MI; `SecretStr` on all sensitive payloads. |
| A03 Injection | **STRONG** | All `cursor.execute` calls parameterised or static; `safe_query.py` deny-list + AST + re-serialisation (three independent gates). No injection vector found in the full walk-through. |
| A04 Insecure Design | **STRONG** | Single-instance rate limit accepted with documented residual (RR-05); generic error messages; unified response envelope; no internal detail in 422 bodies. |
| A05 Security Misconfiguration | **ADEQUATE** | CSP tight (no `unsafe-inline`); CORS denied; third-party Actions SHA-pinned; AAD-only flipped post-bootstrap. Missing HSTS and X-Frame-Options (MJ-02). |
| A06 Vulnerable and Outdated Components | **ADEQUATE** | `pip-audit --strict` on every PR; `bandit` SAST; `>=` lower-bound pins; lockfile not committed (Etapa 8 follow-up). |
| A07 Identification and Authentication Failures | **STRONG** | AAD-only post-bootstrap; bootstrap password deleted from KV; MI is the only data-plane identity; OIDC federation in CI. |
| A08 Software and Data Integrity | **STRONG** | `WEBSITE_RUN_FROM_PACKAGE=1`; `checkov` + `psrule-for-azure` in CI; SHA-pinned third-party actions. RR-09 closed in Etapa 8 — `schema_history.checksum` now carries the canonicalised SHA-256 substituted at apply time by `scripts/render_migration.py`, with CI + CD gates that reject any unsubstituted placeholder. |
| A09 Logging and Monitoring Failures | **STRONG** | App Insights + Log Analytics; SQL audit events; KV audit events; Storage diagnostic settings; OID logged as suffix only. Minor drift in OID suffix length (MN-02). |
| A10 SSRF | **STRONG** | No user-controlled URL paths anywhere. All outbound URLs constructed from validated Pydantic config. |

---

## 9. Compliance Posture

### GDPR

All data in `dim_Employees` and `fact_Trades` is Faker-generated under `locale="ro_RO"`. No real personal data is present. The `@tcp-capital.ro` email domain is enforced by `CK_dim_Employees_email_domain`. GDPR Article 17 (right to erasure) is not applicable to synthetic data; however, no `usp_DeleteEmployeeData` procedure exists (MN-03 from the security-auditor report). For a real deployment this would be a hard requirement.

### Data Residency

All Azure resources are deployed to `westeurope` (primary) or `northeurope` (fallback), both EU regions. The `@allowed()` decorator in `main.bicep` enforces this constraint at deployment time. PowerBI Service uses the organisation's EU tenant. Anthropic API calls leave the EU (Anthropic processes data in the US); for the synthetic-data thesis posture this is acceptable and documented. For a real production deployment, Anthropic's data residency agreements would need review.

### Audit Trail

- SQL `SQLSecurityAuditEvents` streamed to Log Analytics (`log-tcp-prod-weu`), 30-day retention.
- KV `AuditEvent` and `AzurePolicyEvaluationDetails` to Log Analytics.
- Storage `StorageRead`, `StorageWrite`, `StorageDelete` to Log Analytics.
- Functions `FunctionAppLogs`, `AppServiceConsoleLogs`, `AppServiceHTTPLogs` to Log Analytics.
- GitHub Actions audit log for all workflow runs.
- `gitleaks` on every PR (secret scan with `gitleaks.toml` allowlist for known test placeholders).

---

## 10. Change History

| Version | Date | Author | Notes |
|---|---|---|---|
| 1.0 | 2026-05-16 | TODO | Initial version — Etapa 6 security hardening pass. |
