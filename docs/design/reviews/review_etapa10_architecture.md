# Etapa 10 — Final cross-cutting architectural review

| Field | Value |
|---|---|
| **Date** | 2026-05-16 |
| **Reviewer role** | Master software architect (paper-driven; no live Azure access) |
| **Scope** | Cross-cutting architectural integrity pass over E0–E9 deliverables before the `v1.0-mvp` tag |
| **Methodology** | Trace every flow end-to-end against `01_business_requirements.md`, `02_database_design.md`, `03_architecture.md`, ADR-001..005; compare to actual repo state |
| **Out of scope** | Live `azd up` execution; re-litigation of prior `ACCEPTED RESIDUAL` items; markdown-lint style |
| **Inputs read** | `docs/design/0{1,2,3}*.md`, all 5 ADRs, `db/migrations/V00{1,2}__*.sql`, `infra/main.bicep` + 9 modules, `infra/scripts/postprovision.{ps1,sh}`, `function_app/triggers/*.py`, `tcp/{db,safe_query}.py`, `tcp/synth/runner.py`, all 9 `infra/observability/kusto/*.kql`, `swa/staticwebapp.config.json`, `azure.yaml`, `.github/workflows/cd.yml`, `README.md`, `docs/{setup,troubleshooting,observability/slo,security/threat_model}.md`, last 4 convergence reports |

## Verdict

**CHANGES-REQUESTED** before tagging `v1.0-mvp`.

The architecture is coherent, the design docs are unusually well-aligned with the code, and the five ADRs each have direct, traceable enforcement in the codebase. RLS / SESSION_CONTEXT (ADR-003) survives end-to-end paper-tracing on both the user and generator paths. The deny-by-default failure mode is real. The deployment graph in `main.bicep` is dependency-clean. The convergence discipline across nine etape has paid off — the residual surface is small.

That said, three Critical-class integration gaps would cause a first-time `azd up` deploy to land in a partially-functional state. Each is a single-screen fix; none requires architectural rework. With these three fixes the verdict flips to **APPROVED**.

- **arch10-CR-01**: `STORAGE_ACCOUNT_KEY` env var is consumed by `bacpac_export.py` but is wired nowhere in the deploy chain. The Sunday BACPAC export will fail on first invocation with `BacpacConfig missing required env vars: STORAGE_ACCOUNT_KEY`.
- **arch10-CR-02**: SWA `staticwebapp.config.json` references an `AZURE_CLIENT_ID` SWA app setting that is never created or populated. AAD sign-in on `/api/ask` cannot complete because the SWA platform cannot resolve the OpenID client id.
- **arch10-CR-03**: `cd.yml` deploys only the `api` service (`azd deploy api --no-prompt`). The `web` (SWA) service is declared in `azure.yaml` but never pushed by CD — the SWA stays empty after every CI deploy.

The rest of the findings are Major / Minor and do not block the tag, but several should be triaged before defense.

---

## Tally

| Severity | Count |
|---|---|
| Critical | 3 |
| Major    | 5 |
| Minor    | 6 |
| Strength | 9 |

---

## Critical

### arch10-CR-01 — `STORAGE_ACCOUNT_KEY` env var is required by code but never set

**Files**:
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\triggers\bacpac_export.py:52` (declares the constant)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\triggers\bacpac_export.py:107-119` (`BacpacConfig.from_env` lists it as required and fails closed if missing)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\modules\functions.bicep:129-242` (the `appSettings` array — `STORAGE_ACCOUNT_KEY` is absent)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\scripts\postprovision.{ps1,sh}` (no `az functionapp config appsettings set` for this name)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\modules\keyvault.bicep:121-179` (no `STORAGE-ACCOUNT-KEY` KV secret created)

**Summary**: `bacpac_export._load_config()` calls `BacpacConfig.from_env()` which builds a missing-vars list and raises if `STORAGE_ACCOUNT_KEY` is empty. The Bicep `functions` module wires every other env var the trigger needs (`AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `TCP_SQL_SERVER_NAME`, `TCP_SQL_DATABASE_NAME`, `TCP_SQL_ADMIN_LOGIN`, `TCP_BACPAC_CONTAINER_URI`, `SQL_ADMIN_PASSWORD_EXPORT`) but not the storage account key. Only `STORAGE-CONNECTION-STRING` (the full connection string consumed by `AzureWebJobsStorage`) is in KV — the BACPAC code wants the key alone.

**Why it matters**: The first Sunday 08:00 RO BACPAC export will throw `ValueError: BacpacConfig missing required env vars: STORAGE_ACCOUNT_KEY`. The Function App's retry policy will fire, then the alert "BACPAC missed last Sunday" (`infra/modules/alerts.bicep`, query 08) will eventually trip. ADR-004 §"Implementation contract" step 4 explicitly says the storage account key flows through the call; the deployment never closes that wiring.

This was flagged in `review_etapa4_convergence_pass2.md` §Remaining gaps item 2 as "security CR-04 residual" and assigned to "Etapa 5 cannot wire the real Export call without this alignment." It was never closed.

**Suggested fix** (single screen):

1. In `infra/modules/keyvault.bicep`, add a new `Microsoft.KeyVault/vaults/secrets@2023-07-01` resource named `STORAGE-ACCOUNT-KEY` whose `value` is `storage.listKeys().keys[0].value` — wire `storage` as a new `existing` symbol on the keyvault module input, or thread the key as a `@secure()` param from `main.bicep` (which already has `storage.outputs.connectionStringSecretValue` and can extract the key alongside it). Cleanest is the param path.
2. In `infra/modules/functions.bicep:129-242`, add a new appSetting:
   ```bicep
   { name: 'STORAGE_ACCOUNT_KEY', value: '@Microsoft.KeyVault(SecretUri=${kvUriRoot}/secrets/STORAGE-ACCOUNT-KEY/)' }
   ```
3. The Function MI already has `Key Vault Secrets User` on the vault — no new RBAC.

Alternative: refactor `bacpac_export.py` to derive the account key from the existing `AzureWebJobsStorage` connection string at startup (parse `AccountKey=...` out of the conn string). This avoids adding a sixth secret but couples the BACPAC code to the Functions-runtime backing storage. The cleaner separation is to give BACPAC its own secret.

---

### arch10-CR-02 — SWA AAD sign-in: `AZURE_CLIENT_ID` setting referenced but never created

**Files**:
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\swa\staticwebapp.config.json:9` (`"clientIdSettingName": "AZURE_CLIENT_ID"`)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\modules\swa.bicep:39-65` (the SWA resource has no `appSettings`; the linked-backend block is the only configuration)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\scripts\postprovision.{ps1,sh}` (no `az staticwebapp appsettings set` step)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\docs\setup.md:177-213` (covers only the `tcp-cd` AAD app for OIDC federation; no separate SWA-facing AAD app registration is documented)

**Summary**: `staticwebapp.config.json` declares a custom AAD identity provider with `"registration.openIdIssuer": "https://login.microsoftonline.com/<TENANT_ID>/v2.0"` (the `<TENANT_ID>` placeholder is substituted by postprovision Step 2c — good) and `"clientIdSettingName": "AZURE_CLIENT_ID"` (SWA reads the AAD app's client id from a SWA-scoped app setting named `AZURE_CLIENT_ID`). That setting is never created.

There are two halves to the gap:
1. No separate AAD app registration is documented for the SWA sign-in surface. The `tcp-cd` OIDC SP is for Azure deploy from GitHub Actions — its client id is correct for `cd.yml` `vars.AZURE_CLIENT_ID` but is wrong for SWA browser sign-in (different audience: SWA needs an AAD app with the SWA's `*.azurestaticapps.net` reply URL registered).
2. Even if the user manually creates a second AAD app registration for SWA, the Bicep `swa.bicep` module does not surface a `properties.appSettings` block — SWA app settings have to be set via `az staticwebapp appsettings set` (postprovision) or via a sub-resource `Microsoft.Web/staticSites/config@2023-12-01`.

**Why it matters**: Without the `AZURE_CLIENT_ID` SWA app setting, the SWA AAD provider configuration is incomplete and the platform falls back to "auth provider not configured" — every `/api/ask` browser call returns HTTP 401 from the SWA platform before it even reaches the Function App. ADR-003 acceptance bullet A.5 of `03_architecture.md §17.1` ("SWA AAD sign-in flow completes end-to-end in a private browser session") cannot pass.

**Suggested fix**:

1. **Documentation**: Add a `docs/setup.md §B.0` step before `azd up`: "Create an AAD app registration for SWA browser sign-in (display name `tcp-swa-aad`), set the reply URL to `https://<swa-hostname>/.auth/login/aad/callback`, capture the `appId`, and run `azd env set SWA_AAD_CLIENT_ID <appId>` before `azd up`." Either reuse the existing `tcp-cd` app (adding the SWA reply URL as a second redirect — simplest) or create a dedicated app.
2. **Bicep**: Pass `swaAadClientId` from `main.bicep` into `swa.bicep` and add a `Microsoft.Web/staticSites/config@2023-12-01` child resource that sets `properties.appSettings.AZURE_CLIENT_ID = swaAadClientId`. Alternatively, add a postprovision Step 2d that runs `az staticwebapp appsettings set --setting-names AZURE_CLIENT_ID=<value>`.
3. **Alternative**: drop the custom identity provider entirely and rely on the SWA's built-in AAD provider (omit the `registration` block). This removes the need for a separate app registration but removes tenant pinning — acceptable trade-off for an academic build. The `staticwebapp.config.json` `registration` block was added because the previous review wanted explicit tenant pinning; revisit.

This was partially called out in `review_etapa4_convergence_pass2.md` §Remaining gaps item 1 (option (c) in the three-way fork) but the chosen option (a) — substitute `<TENANT_ID>` in postprovision — only addresses the issuer, not the client id.

---

### arch10-CR-03 — `cd.yml` never deploys the `web` (SWA) service

**Files**:
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\azure.yaml:13-21` (declares two services: `api` and `web`)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\.github\workflows\cd.yml:118-121` (only `azd deploy api --no-prompt`)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\docs\setup.md:257` ("`azd deploy` — packages `function_app/` + `swa/` and pushes to the running Function App + Static Web App.")

**Summary**: `azure.yaml` declares the `web` service pointing at `./swa`. The CD pipeline only runs `azd deploy api` — never `azd deploy web` or the unscoped `azd deploy`. After every successful CD run, the SWA resource is provisioned but its container is empty: `index.html`, `app.js`, and `style.css` are never uploaded. `setup.md §B.3` step 3 documents `azd deploy` (no service argument, which deploys both); the CI/CD pipeline contradicts it.

**Why it matters**: After `cd.yml` succeeds against the `prod` environment, a user visiting `https://<swa-hostname>.azurestaticapps.net/` gets the SWA default landing page, not the TCP chat UI. The smoke step (`cd.yml:149-157`) only probes `/api/ping` directly against `func-tcp-prod-weu.azurewebsites.net`, never via the SWA, so the missing frontend is not detected.

This also defeats the documented SWA → Function linked-backend flow: even if the user knew to upload `swa/` manually, the `forwardingGateway.requiredHeaders` block (with the substituted secret) only takes effect once the SWA has content + config to serve.

**Suggested fix**:

In `.github/workflows/cd.yml:121`, change to:

```yaml
- name: azd deploy api
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
  run: azd deploy api --no-prompt

- name: azd deploy web
  run: azd deploy web --no-prompt
```

Or use the unscoped `azd deploy --no-prompt` which deploys all services in one call. Either way the SWA gets the rendered `staticwebapp.config.json` from postprovision Step 2c and the static assets.

Note this gap also has a temporal coupling with arch10-CR-02: the postprovision script substitutes the placeholder in `swa/staticwebapp.config.json` *in-place* on the runner's working copy, then the runner exits. If `azd deploy web` is added later as a separate job, the working copy will not survive between jobs unless the substituted file is uploaded as an artifact or the deploy step runs in the same job. Recommended: keep `azd deploy api` and `azd deploy web` in the **same `deploy` job** (single runner, single working tree, postprovision substitutions intact).

---

## Major

### arch10-MJ-01 — `structlog.configure()` never called; `customDimensions["event"]` matching is fragile

**Files**:
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\triggers\ask.py:649` (`log.info("tcp.ask.audit", question_sha256=...)`)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\triggers\ask.py:481-491` (`log.info("tcp.ask.metrics", ...)`)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\triggers\bacpac_export.py:447-453` (`log.info("tcp.bacpac.complete", ...)`)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\observability\kusto\07_ask_question_audit.kql:20` (`where customDimensions["event"] == "tcp.ask.audit"`)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\observability\kusto\03_anthropic_tokens_and_cost.kql:32` (`where customDimensions["event"] == "tcp.ask.metrics"`)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\observability\kusto\08_bacpac_export_health.kql:17` (`where customDimensions["event"] in ("tcp.bacpac.complete", ...)`)

**Summary**: Three KQL queries filter on `customDimensions["event"] == "<name>"`, assuming the App Insights ingestion path maps the structlog "event" positional argument to a `customDimensions["event"]` field. But the repo never calls `structlog.configure()` (this was flagged inside the Etapa-8 code review as `code-mi-06` and the Etapa-8 convergence accepted it as residual). Without `structlog.configure()`, `structlog.get_logger()` returns a `BoundLoggerLazyProxy` whose `.info(name, **kwargs)` call falls back to the stdlib `logging` integration. In that path, the first positional argument becomes the `message` text, not a `customDimensions["event"]` key.

In practice, the App Insights Python SDK *does* pick up logger.extra fields into `customDimensions`, and Functions Python v2 maps logger names + structured kwargs into the trace `customDimensions` block — but the exact field name shows up as `message` (for the positional) plus each kwarg as its own custom dimension. There is no automatic "event" key.

**Why it matters**: Once live in production, queries 03, 07, and 08 will return zero rows for the trace-based arm, even though the events are being emitted. The metrics-based arms (queries 03 and 08) might still work if `customMetrics` are ever emitted — but ADR-005 §3 explicitly defers `azure-monitor-opentelemetry` to Etapa 8, and Etapa 8 confirmed the deferral was not closed (the Etapa-8 review made this an `ACCEPTED RESIDUAL` and pushed it to Etapa-12). Net effect: workbook tiles "Anthropic token spend", "Recent question fingerprints", and "BACPAC weekly health" will look empty even when the underlying telemetry is flowing.

This is a Major, not a Critical, because the production-readiness gap of `customMetrics` was already accepted as residual in `review_etapa8_convergence_pass2.md`. The KQL drift specifically — referencing a field name that doesn't exist in the actual ingest shape — is a separate issue that piggybacks on the same root cause.

**Suggested fix** (two options, pick one):

1. **Configure structlog** — at the top of `function_app/function_app.py`, add a one-time `structlog.configure(processors=[structlog.contextvars.merge_contextvars, structlog.processors.add_log_level, structlog.processors.EventRenamer("event"), structlog.processors.JSONRenderer()], logger_factory=structlog.stdlib.LoggerFactory(), wrapper_class=structlog.stdlib.BoundLogger)`. The `EventRenamer("event")` step explicitly carries the positional name into a `customDimensions["event"]` field after stdlib bridging — fixes the KQL contract.
2. **Change the KQL** — replace `customDimensions["event"] == "tcp.ask.audit"` with `message has "tcp.ask.audit"` in queries 03, 07, 08 (and the matching workbook tiles). This costs nothing and works against the current emission shape, at the price of looser matching.

Option 1 is the architecturally cleaner answer; option 2 is the smaller surface change for `v1.0-mvp`. Both close the drift.

---

### arch10-MJ-02 — README "Project status" claims Etapa 9 in progress (now stale)

**Files**:
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\README.md:277-292` (claims Etapa 9 is the current stage)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\.claude\STATE.md:10` (Etapa 9 COMPLETE; Etapa 10 in progress)

**Summary**: The top-level README's "Project status" table marks Etapa 9 as the current stage with a `[ ]` checkbox and "this stage" label, while `.claude/STATE.md` records Etapa 9 as COMPLETE and Etapa 10 as the active stage. README is the user-facing entry point that thesis examiners will read first.

**Why it matters**: First impression of the project status is wrong. Easy fix; cheap to do before tagging.

**Suggested fix**: In `README.md:288-289`, flip the checkbox from `[ ]` to `[x]` on Etapa 9 and add the standard suffix used elsewhere ("(3-reviewer pass + convergence APPROVED FOR MERGE)"). Drop the "this stage" annotation. Mark Etapa 10 with the current `[ ]` and a "this stage" note if you want a moving pointer, or remove the pointer entirely now that the tag is imminent.

---

### arch10-MJ-03 — SWA `staticwebapp.config.json` substitution is destructive on the working copy

**Files**:
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\scripts\postprovision.sh:208-216` (in-place `python` replace on `$SWA_CONFIG_PATH`)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\scripts\postprovision.ps1:218-225` (`Get-Content -Raw` … `Set-Content -Path $swaConfigPath -NoNewline`)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\swa\staticwebapp.config.json:8,32` (the placeholders)

**Summary**: Both postprovision scripts modify the on-disk `swa/staticwebapp.config.json` in-place, replacing `<TENANT_ID>` and `<value-set-by-postprovision>` with real values. In CI this is harmless — the runner is ephemeral — but on a developer workstation running `azd up` interactively, the destructive substitution leaves the secret value committed to the local working tree until the developer remembers to revert it. The `.gitignore` does not exclude this file (it is checked in).

**Why it matters**:
1. A developer who runs `azd up` locally and then immediately stages with `git add -A` or `git commit -a` will commit the SWA shared secret into git history. Combined with a public repo, that is an instant credential leak.
2. Re-running `azd up` after a manual `git restore` revert works fine, but a forgetful pattern is the kind of operational footgun a thesis-grade repo should not ship with.

**Suggested fix**:

Move the substituted config to a build-artifact path that is gitignored:

1. Copy `swa/staticwebapp.config.json` to `swa/.build/staticwebapp.config.json` first; substitute there; teach `azd deploy web` to upload from `swa/.build/` (via an `azure.yaml` `dist` field or a small `swa/.azuredeploy/` staging convention).
2. Add `swa/.build/` to `.gitignore`.

Alternative (less invasive): commit the placeholders, leave the substitution destructive, and add a `pre-commit` hook (and a CI step) that hard-fails if `staticwebapp.config.json` contains anything other than the literal placeholders. This catches a forgetful commit but doesn't remove the footgun.

The Etapa-4 convergence flagged three options to close CR-03 (sed-in-place was option a; the template path is option b; KV-reference passthrough is option c). The team picked option (a), which works for CI but introduces this footgun. Either option (b) or (c) closes it; (b) is the smallest delta.

---

### arch10-MJ-04 — `bypass: 'AzureServices'` on KV networkAcls is a no-op when `defaultAction: 'Allow'`, but the comment is honest about it

**Files**:
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\modules\keyvault.bicep:97-110`

**Summary**: The KV network ACL has `defaultAction: 'Allow'` and `bypass: 'AzureServices'`. The inline comment (lines 98-107) correctly notes that `bypass` is honored only when `defaultAction = 'Deny'`, so it is currently inert. Functionally, this is fine for the free-tier posture: RBAC + AAD is the auth boundary, and a future `Deny` flip will activate the existing `bypass` so Function MI traffic from `AzureServices` keeps working.

**Why it matters**: The architecture honestly documents the trade-off, but the inert `bypass` line means any auditor reading the module without the comment will think the firewall has bypass-class behavior that does not exist. Low-risk, but worth a one-line tweak for clarity.

**Suggested fix**: Wrap `bypass` in a conditional: `bypass: defaultAction == 'Deny' ? 'AzureServices' : 'None'`. This makes the dependency explicit in the rendered ARM JSON and the future Deny flip needs no further code change. Pure documentation move; no behavior change today.

(Same observation applies to `infra/modules/storage.bicep:58-63` — same idiom, same inert bypass.)

---

### arch10-MJ-05 — `01_business_requirements.md` lists 48 KPIs; the AI assistant's safe_query allowlist is narrower than the BI surface

**Files**:
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\docs\design\01_business_requirements.md` (48 KPIs total)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tcp\safe_query.py:121-176` (allowlist: 5 `v_*` views, 9 `dim_*` tables, 2 procs, 5 TVFs/scalar functions)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\powerbi\` (TMDL model: 69 DAX measures covering all 48 KPI families per `README.md`)

**Summary**: PowerBI dashboards cover the full 48-KPI surface via 69 DAX measures. The AI assistant can only see what `safe_query.ALLOWED_VIEWS` + `ALLOWED_DIMS` + `ALLOWED_PROCS` + `ALLOWED_FUNCTIONS` expose. The five reporting views (`v_trades_enriched`, `v_employee_performance`, `v_team_performance`, `v_floor_performance`, `v_daily_pnl`) plus the two risk procs cover the activity/performance/team/floor/risk families well — but the AI cannot answer questions about *KPI families* that are computed purely in DAX (e.g., year-on-year growth measures, drawdown windows that span multiple `v_daily_pnl` rows in patterns the LLM might struggle to encode in pure T-SQL).

**Why it matters**: This is by design (the safe_query allowlist is the security boundary), but it is not surfaced anywhere in `01_business_requirements.md` or the `ai_prompt_cache_contents.md`. A thesis examiner reading "48 KPIs" and then asking the chatbot a question outside the assistant's coverage will get a refusal envelope and may interpret that as a bug.

**Suggested fix**: Add a one-paragraph "AI assistant scope vs PowerBI scope" note in `01_business_requirements.md` (or `ai_prompt_cache_contents.md`) explicitly listing which KPI families the assistant can answer directly (Activity, Performance, Team-aggregate, Floor-aggregate, Risk-via-TVF) and which require PowerBI (cross-period growth, complex drawdown analytics, calculation-group time intelligence). This is a documentation cohesion fix; no code change.

---

## Minor

### arch10-MI-01 — `01_business_requirements.md` claims placeholder; verify it has been updated to reflect E5 + E7 outputs

**File**: `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\docs\design\01_business_requirements.md`

**Summary**: The document is from Etapa 1 and lists the KPI catalog. The downstream `safe_query.py` allowlist (Etapa 5) and the PowerBI TMDL measures (Etapa 7) are the actual implementations. The business-requirements doc was not updated post-implementation to record which KPIs were realized and where. This is fine for a frozen design doc, but a one-line "Implementation status: see `tcp/safe_query.py` for the AI subset; `powerbi/model/measures.tmdl` for the BI subset" header would help future readers. (Not blocking.)

---

### arch10-MI-02 — `principalId` in `main.bicep` is optional but Owner role assignment is conditional on it

**File**: `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\main.bicep:44-149`

**Summary**: `principalId` defaults to empty (`= ''`) and the `deployerRgOwner` role assignment is gated on `!empty(principalId)`. In the CI/OIDC path, `azd` injects the deployer's principal id automatically. On an interactive `azd up` against a fresh tenant, if the developer forgets to set it, no Owner role assignment is made and subsequent `azd provision` calls will fail with permission errors. The condition is defensible (allows re-deploys after the developer's RBAC has been granted out-of-band), but a comment explaining the design choice would help.

**Suggested fix**: Add a one-line comment near line 139 explaining the `!empty(principalId)` gate ("we tolerate an empty principalId so re-applies from CI work without re-asserting an Owner role assignment that was already created on first deploy").

---

### arch10-MI-03 — `tcp/synth/runner.py` `previous_business_day` is documented but never called by `run_daily`

**File**: `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tcp\synth\runner.py:101-125,193-204`

**Summary**: `previous_business_day` is a documented public function (in CLAUDE.md as `tcp.synth.previous_business_day`) but the production path uses `_resolve_target_date` (which queries `dim_Date` and respects RO holidays). `previous_business_day` exists only for unit-test convenience and never gets exercised in production. The CLAUDE.md reference makes it sound load-bearing; in fact the SQL-side `dim_Date` lookup is the canonical resolver.

**Suggested fix**: Either delete `previous_business_day` and move its tests to a fixture-based test that exercises `_resolve_target_date` directly, or update CLAUDE.md to clarify that the Python helper is a test-only convenience and the production path is SQL-side.

---

### arch10-MI-04 — `host.json` `Host.Aggregator: Trace` is verbose; may surprise the App Insights bill

**File**: `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\host.json:14`

**Summary**: `logLevel.Host.Aggregator = "Trace"` emits per-aggregation events from the Functions host. Adaptive sampling is on (line 5-9) with `maxTelemetryItemsPerSecond: 5`, so the volume is capped — but the combination of `Trace` and a 0.5 GB/day workspace cap is one Function-host noisy upgrade away from the daily ingestion limit. Acceptance of this is fine for the academic posture; flag for awareness.

**Suggested fix**: Consider dropping to `Host.Aggregator: Information`. Pure operational hygiene; not a defect.

---

### arch10-MI-05 — `infra/observability/kusto/02_daily_generator_outcomes.kql` treats RO-holiday short-circuits as Successes correctly, but the comment lies about how the runtime emits them

**File**: `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\observability\kusto\02_daily_generator_outcomes.kql:14`

**Summary**: The comment says "RO public holidays still emit a request row but the runner short-circuits with status='skipped_holiday'; treat those as Successes." That's correct — the runner returns the status dict and the trigger logs success. But the file doesn't filter on `success == true` or on the status field, so a holiday short-circuit is counted in `Runs` but contributes 0 to `Failures` (because the request succeeded). The math is fine; the comment just oversells the explicit handling. Cosmetic.

**Suggested fix**: None required. If you want strict purity, add a `status == "ok"` filter in the customDimensions and split the summary into `Successes_ok` vs `Successes_skipped`. Not necessary for v1.0.

---

### arch10-MI-06 — `_resolve_scope` opens an admin-bypass connection per request; no caching

**File**: `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\triggers\ask.py:327-368`

**Summary**: ADR-005 documents the per-request admin-bypass connection for scope resolution. The implementation is correct and minimal (single parameterised SELECT, immediate close). At the expected demo traffic (~30 users, ~10 requests/h/user), the per-request open-close pattern adds ~20-50 ms warm to every `/api/ask` call and contributes connection-churn to the SQL Server's auth audit log. A per-process scope cache keyed on `oid` with a short TTL (e.g., 60 s) would cut both costs at no security cost — the cache is per-process and a worker restart re-validates. This is exactly the kind of optimization Etapa 12 should pick up; flag here so it does not get lost.

**Suggested fix**: Defer to Etapa 12. ADR-005 already documents the trade-off; mention this here purely so the Etapa-12 backlog has the citation.

---

## Strengths

### arch10-ST-01 — ADR-003 RLS contract is enforced end-to-end with no observable gaps

The user-question path (`ask.py` → `_resolve_scope` → `_execute_validated_sql` → `connection_for_user` → `_SQL_SET_CONTEXT`) and the generator path (`daily_generator.py` → `runner.run_daily` → `_open_raw_connection` → `set_admin_session_context(generator_oid)`) both honor the SESSION_CONTEXT contract. The `Pooling=False` on the AAD connection strings (line `tcp/db.py:176`) closes the pooled-leakage hole. The `@read_only=1` lock-after-set prevents LLM-emitted SQL from overwriting the identity binding. The deny-by-default failure mode is verified by the `rls.fn_TradesPredicate` CROSS APPLY at `V001__init.sql:1182-1205`. This is the single most load-bearing security contract in the system and it survives paper-tracing in both directions.

### arch10-ST-02 — Naming convention enforcement is complete

Every table I located in `V001__init.sql` complies with `(fact|dim|config)_[A-Z][a-zA-Z0-9]*`: `dim_Companies`, `dim_TradingFloors`, `dim_Teams`, `dim_Employees`, `dim_Accounts`, `dim_Markets`, `dim_Sessions`, `dim_OrderType`, `dim_Date`, `dim_UserRoles`, `config_Capital`, `fact_Trades`, `fact_DailyTraderPnL`. Views consistently use `v_*` snake_case (`v_trades_enriched`, `v_employee_performance`, `v_team_performance`, `v_floor_performance`, `v_daily_pnl`). RLS lives in the `rls` schema (`rls.fn_TradesPredicate`, `rls.TradesAccessPolicy`). The convention is mechanically verifiable.

### arch10-ST-03 — Idempotency contract on `usp_GenerateDailyTrades` is rigorous

`V002__synth_logic.sql` short-circuits on non-trading-day BEFORE opening a transaction (line 72-78), on already-generated-day BEFORE parsing JSON (line 82-89), validates the JSON payload outside the transaction (line 92-95), and only opens the transaction once the data is staged in a table variable. The MERGE into `fact_DailyTraderPnL` (line 207-247) is in the same transaction as the INSERT into `fact_Trades`, so a partial failure rolls both back. The cross-row invariant check (line 179-184) ensures every row's `time_entry` projects to the requested `@trade_date` in Europe/Bucharest, catching DST off-by-one bugs at the source.

### arch10-ST-04 — `safe_query.py` defense-in-depth is the textbook pattern

Deny-list tokens run BEFORE sqlglot parsing (so OPENROWSET-style payloads that fail to parse surface as a precise reason, not a generic parse error). The allowlist tables (`ALLOWED_VIEWS`, `ALLOWED_DIMS`, `ALLOWED_PROCS`, `ALLOWED_FUNCTIONS`) intersect explicitly with the LLM's referenced objects — there is no "best-effort" path. `dim_UserRoles` is intentionally excluded from the allowlist (line 147-149) so the LLM cannot enumerate the RLS scope map. The validator injects `TOP MAX_ROW_LIMIT` when missing and coerces oversize requests down to the cap. The contract is fail-closed.

### arch10-ST-05 — Postprovision script's RLS-disable + try/catch + finally re-enable is correct

Both `postprovision.{ps1,sh}` disable `rls.TradesAccessPolicy`, INSERT the Function MI's row into `dim_UserRoles`, re-enable the policy inside the TRY, and have a defensive ALTER (finally / trap) that re-enables the policy if anything went wrong. The temporary RLS disable is the only way to insert the initial admin row before any principal exists — and the re-enable path is belt-and-braces.

### arch10-ST-06 — ADR-004 BACPAC ownership conflict resolution is fully realized in code

The conflict between `02_database_design.md §12` (GitHub Actions cron) and `03_architecture.md §11` (Function App timer) was resolved in ADR-004; the resulting implementation lives in `function_app/triggers/bacpac_export.py` (Function App timer at `0 0 8 * * 0`) with the correct identity (MI), the correct endpoint (Management REST API), the correct polling cadence (10 s × 30 min), the correct lifecycle (28-day Storage management policy in `storage.bicep:104-136`), and the correct retained-secret (`SQL-ADMIN-PASSWORD-EXPORT` in `keyvault.bicep:145-155`). One ADR; one canonical implementation.

### arch10-ST-07 — Module dependency graph in `main.bicep` is dependency-clean

The post-CR-02 ordering (storage before functions so `AzureWebJobsStorage` resolves at host startup) is preserved with documentation. KV references are deterministic strings built from the KV name without circular dependencies. The explicit `dependsOn: [observability, sql]` on the `alerts` module (line 327) closes the no-op-re-deploy race surfaced by arch-MA-02 in Etapa-4. Re-applies are idempotent: `resolvedSqlAdminPassword` and `resolvedSwaForwardedSecret` use `empty(input) ? generate : input`, so a re-apply with the captured value preserves the BACPAC export password.

### arch10-ST-08 — Observability surface coherence held after Etapa-8 convergence

The single-source KQL discipline (workbook tiles mirror `.kql` files) was the explicit fix in `review_etapa8_convergence_pass2.md` obs-MA-01/02/03. Spot-check: `01_ask_latency_percentiles.kql` has `success == true` + `samples > 5`; query 03 unions traces + customMetrics with USD-EUR conversion; query 08 unions both BACPAC emission shapes for transition. The drift surfaced by obs-CR-01 (`tcp.ask.audit` event not emitted) is properly closed: `ask.py:649` emits it before the Anthropic call.

### arch10-ST-09 — Threat-model coverage is honest about the free-tier trade-offs

`docs/security/threat_model.md` does not claim perfection. It documents 7 trust boundaries with their actual enforcement mechanisms, ranks assets by real monetary or operational value, names 4 specific adversaries with capability tiers, and tracks 9 residual risks (RR-01..RR-09 with RR-09 closed in Etapa 8). The `bypass: 'AzureServices'` no-op (the cosmetic flag in keyvault.bicep / storage.bicep), the SQL admin password retention for BACPAC (ADR-004 §"Open caveat"), the in-process rate limit, the public storage endpoint, the AAD-only-flip bootstrap window — every honest trade-off is documented either in the threat model, an ADR, or a residual-risk row. This is the right posture for an academic-grade build that has to be defensible without overclaiming.

---

## Cross-reference summary

| Flow | Result |
|---|---|
| Design ↔ schema | V001 + V002 realize every table + view + proc + RLS predicate the design specifies. |
| Design ↔ infra | `main.bicep` provisions every resource `03_architecture.md §4` specifies. One missing app setting (`STORAGE_ACCOUNT_KEY`) and one missing SWA appsetting (`AZURE_CLIENT_ID`) are flagged Critical. |
| ADR-001 (PowerBI REST) | `powerbi/` directory + REST-based deploy; consistent with ADR. |
| ADR-002 (`fact_DailyTraderPnL`) | Schema + `usp_GenerateDailyTrades` MERGE in same transaction; consistent. |
| ADR-003 (RLS via SESSION_CONTEXT) | Enforced from `ask.py` through `tcp/db.py` to the RLS predicate. Pooling disabled on AAD connections. Deny-by-default verified. |
| ADR-004 (BACPAC export from Function App, Sunday 08:00 RO) | Implemented in `bacpac_export.py`; `SQL-ADMIN-PASSWORD-EXPORT` retained; alert wired. The `STORAGE_ACCOUNT_KEY` gap (arch10-CR-01) blocks runtime. |
| ADR-005 (RLS-bypass scope resolution + in-process rate limit) | Implemented; ADR-005 §"Custom-metrics deferral" matches the residual on `customMetrics` and ties into arch10-MJ-01. |
| Daily generator flow | Holiday short-circuit + idempotency + transactional MERGE all hold; admin SESSION_CONTEXT is set before the first DML. |
| `/api/ask` request flow | Header validation → AAD principal parse → scope resolution → rate limit → Anthropic → safe_query → RLS-scoped exec → envelope. Each layer is the documented one; no surface mismatches at the function-app/SQL boundary. The frontend boundary has the two gaps (arch10-CR-02 SWA `AZURE_CLIENT_ID` and arch10-CR-03 SWA never deployed). |
| Observability surface | Coherent in shape (KQL tiles ↔ .kql files); the `customDimensions["event"]` field-name drift (arch10-MJ-01) prevents three queries from returning rows in production. |
| Documentation cohesion | README, setup.md, troubleshooting.md, slo.md, threat_model.md, ADRs all cross-link consistently. One stale-status row in README (arch10-MJ-02). |
| Naming convention | Compliant on every table found in V001 + V002. |

---

## Recommendation

**CHANGES-REQUESTED** before tagging `v1.0-mvp`. Fix the three Criticals (single-screen changes each), then re-tag. The five Majors are tag-able-as-is (with arch10-MJ-02 trivially fixed for README hygiene) but should be triaged for the Etapa 11 / 12 cleanup pass:

| Pre-tag (Critical) | Post-tag (Major) | Post-defense (Minor) |
|---|---|---|
| arch10-CR-01 (`STORAGE_ACCOUNT_KEY`) | arch10-MJ-01 (`customDimensions["event"]` / structlog config) | arch10-MI-01 .. MI-06 (small polish items) |
| arch10-CR-02 (SWA `AZURE_CLIENT_ID`) | arch10-MJ-02 (README staleness — fix before tag if convenient) |  |
| arch10-CR-03 (`cd.yml` deploy web) | arch10-MJ-03 (SWA config substitution destructive on working tree) |  |
|  | arch10-MJ-04 (KV `bypass` inert with `Allow` — cosmetic) |  |
|  | arch10-MJ-05 (KPI scope mismatch BI vs AI — doc-only) |  |

The build has reached a state where every load-bearing contract (ADR-003 RLS, ADR-002 daily MERGE, ADR-004 BACPAC ownership, ADR-005 scope-resolution bypass) survives end-to-end paper-tracing. The convergence-driven Etapa-4 through Etapa-9 discipline is visible in the code: deny-by-default, idempotent postprovision, single-source KQL, fail-closed validators, redacted telemetry. The three Criticals are integration-wire gaps, not architectural defects — none requires re-thinking a decision, and each is a one-day fix.

Once Critical-class items are closed, `v1.0-mvp` is unblocked.

---

*End of `review_etapa10_architecture.md`.*
