# Etapa 9 — Tutorial Engineering Review (Learner-facing journey)

| Field | Value |
|---|---|
| **Reviewer** | tutorial-engineer (specialised pass) |
| **Date** | 2026-05-16 |
| **Scope** | `README.md` (top-level), `docs/setup.md`, `docs/troubleshooting.md`, `docs/glossary.md`, `docs/dev_setup.md`, `docs/runbooks/powerbi_deploy.md`, `infra/scripts/postprovision.{ps1,sh}` |
| **Branch** | `feat/azure-rewrite` |
| **Working tree** | clean at HEAD `2dc18aa` |
| **Audience model** | Thesis examiner on first cold-start run + future operator recovering from failure; not a junior Python developer |

---

## 1. Summary

The Etapa 9 documentation set is the strongest single-document body this project has produced. `docs/setup.md` has a clear two-track separation, a well-structured progressive flow, and a working eight-step postprovision summary. `docs/glossary.md` is comprehensive and well-organised. `docs/troubleshooting.md` covers the nine most credible first-deploy failure modes with working diagnostic commands. The README is a clean entry-point document.

That said, there are **two critical issues** that will stop a cold-start examiner before they reach the smoke test: the acceptance checklist commands require manually derived values (`<server>`, `<kv>`, `<rg>`) that are never resolved earlier in the walkthrough, and the Track A A.3 SQL apply commands use a POSIX-only `export` form with no PowerShell dual-track, creating a silent copy-paste hazard on the documented primary developer platform (Windows). Four major issues affect the Track B → Track A isolation, the step-count discrepancy between `setup.md` and the actual postprovision scripts, the bootstrap-window warning placement, and the PowerBI handoff completeness. The minor and nit items are polish; they do not block the journey.

**Verdict: CHANGES-REQUESTED.** Block the convergence pass on tut-CR-01 (acceptance checklist unresolved variables) and tut-CR-02 (Track A POSIX-only commands on Windows). The four Majors are short targeted repairs in the same convergence diff. Minors and Nits can defer to an Etapa-12 polish pass without blocking reviewer reproduction.

| Severity | Count |
|---|---:|
| Critical | 2 |
| Major | 4 |
| Minor | 5 |
| Nit | 4 |
| Strengths | 6 |

---

## 2. Critical findings (block convergence pass)

### tut-CR-01. Acceptance checklist commands contain unresolved `<server>`, `<kv>`, `<rg>` placeholders that a cold-start examiner cannot derive

**Location** — `docs/setup.md:255-264` (Acceptance checklist items 3, 4, 5)

```markdown
- [ ] `az sql server ad-only-auth list -s <server> -g <rg>` returns ...
- [ ] `az keyvault secret show --vault-name <kv> --name SQL-ADMIN-PASSWORD-BOOTSTRAP` ...
- [ ] `az keyvault secret show --vault-name <kv> --name SQL-ADMIN-PASSWORD-EXPORT` ...
```

**Why it matters**
The acceptance checklist is the most load-bearing part of the walkthrough — it is the single block an examiner copy-pastes at the end to confirm the deploy is green. Items 3, 4, and 5 use `<server>`, `<kv>`, and `<rg>` angle-bracket tokens that are never resolved anywhere in `setup.md`. The B.5 smoke-test section just above them (lines 211-231) demonstrates the correct pattern of deriving these values from `azd env get-value`, but the checklist does not apply the same pattern. An examiner who copies item 3 verbatim will run a command that Azure CLI rejects with a resource-not-found error, producing a false negative against a successful deploy.

Contrast with checklist items 1 and 6 — both are copy-pasteable as written: item 1 uses the hard-coded resource group name `rg-tcp-prod-weu` (which is deterministic from the Bicep naming convention), and item 6 uses `curl /api/ping` (symbolic, but the examiner has already seen the full command in B.5). Items 3-5 fall between these two approaches without committing to either.

**Suggested fix**
Either fully resolve the variable names with the same `azd env get-value` derivation pattern already used in B.5, or hard-code the deterministic names from the Bicep convention (`sql-tcp-prod-weu`, `kv-tcp-prod-weu`, `rg-tcp-prod-weu`). The latter is simpler and consistent with how item 1 resolves the resource group:

```markdown
- [ ] `az sql server ad-only-auth list -s sql-tcp-prod-weu -g rg-tcp-prod-weu` returns `azureADOnlyAuthentication: true`.
- [ ] `az keyvault secret show --vault-name kv-tcp-prod-weu --name SQL-ADMIN-PASSWORD-BOOTSTRAP` returns `SecretNotFound`.
- [ ] `az keyvault secret show --vault-name kv-tcp-prod-weu --name SQL-ADMIN-PASSWORD-EXPORT` returns a secret.
```

If the resource names are configurable and not guaranteed to match these patterns, add a one-time setup block:
```bash
SQL_SERVER=$(azd env get-value AZURE_SQL_SERVER_NAME)
KV=$(azd env get-value AZURE_KEYVAULT_NAME)
RG=$(azd env get-value AZURE_RESOURCE_GROUP)
```
...and use `$SQL_SERVER`, `$KV`, `$RG` in every subsequent checklist command.

---

### tut-CR-02. Track A A.3 SQL apply commands use POSIX-only `export` syntax with no PowerShell dual-track

**Location** — `docs/setup.md:48-64` (Track A A.3 Local SQL Server)

```bash
export TCP_SQL_DEV_PASSWORD='YourStrong!Passw0rd'
docker compose -f docker-compose.dev.yml up -d
...
docker exec tcp-sql-dev /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa \
  -P "$TCP_SQL_DEV_PASSWORD" -C -Q "CREATE DATABASE tcp_dev"
...
sqlcmd -S localhost,1433 -U sa -P "$TCP_SQL_DEV_PASSWORD" -d tcp_dev ...
```

**Why it matters**
The project's own `CLAUDE.md` declares Windows PowerShell as the primary shell and mandates a dual-track (bash + PowerShell) for every shell block. `docs/dev_setup.md` (the referenced reference for Track A) correctly follows this policy throughout — every block has a bash tab and a PowerShell tab. Track A A.3 in `setup.md` abandons the dual-track without warning, using `export VAR=value` (bash-only) and `"$TCP_SQL_DEV_PASSWORD"` in bare-bash form. On Windows PowerShell:

- `export TCP_SQL_DEV_PASSWORD=...` is not a recognised command — fails silently or errors.
- `$TCP_SQL_DEV_PASSWORD` in a double-quoted string expands to empty string (PowerShell expects `$env:TCP_SQL_DEV_PASSWORD`), so sqlcmd receives an empty password and returns a login-failure error with no obvious diagnosis.

A thesis examiner running on Windows (the documented developer machine per `CLAUDE.md` environment declaration) will hit a login failure against the Docker container before reaching a single schema migration. The Track A "15 minute" estimate entirely breaks down because the examiner must then debug why sqlcmd fails — the error message does not point to the env-var form as the cause.

Track A A.4 (the test suite block, lines 78-93) partially avoids this by using `export` but noting `# Anthropic key only required for ...`, which at least flags the bash context. A.3 has no such warning.

**Suggested fix**
Apply the same dual-track pattern as `docs/dev_setup.md`. The minimum fix is to add the PowerShell form as a clearly labelled alternative block immediately after each bash block, or to add a single callout at the start of A.3:

```markdown
> **Windows PowerShell users**: replace every `export VAR=value` with `$env:VAR = 'value'`
> and every `"$VAR"` with `"$env:VAR"`. See `docs/dev_setup.md` for full PowerShell equivalents.
```

The preferred fix is the full dual-track pattern already established in `dev_setup.md`, eliminating any guesswork.

---

## 3. Major findings (should fix before convergence)

### tut-MA-01. `setup.md` describes a 5-step postprovision sequence but the actual scripts run 8 logical steps (Steps 0, 1, 2, 2b, 2c, 3, 4, 5)

**Location** — `docs/setup.md:185-192` (B.3 Local `azd` deploy, postprovision bullet list) vs `infra/scripts/postprovision.sh:64-271` and `infra/scripts/postprovision.ps1:57-314`

The `setup.md` B.3 bullet list describes six bullets under "postprovision hooks":

```
- Step 0: Apply V001 + V002 ...
- Step 1: Register the Function App MI ...
- Step 2: Set TCP_GENERATOR_OID ...
- Step 2c: Substitute <TENANT_ID> + <value-set-by-postprovision> in swa/staticwebapp.config.json.
- Step 3: Flip the SQL server to azureADOnlyAuthentication = true.
- Step 4: Delete the bootstrap admin password secret ...
- Step 5: Verify the AAD-only flip + bootstrap-secret deletion.
```

The actual scripts include a **Step 2b** (Function App restart) that is absent from the walkthrough. This is not a minor omission: the restart is the step that actually applies `TCP_GENERATOR_OID` to the running Function App process — setting the app setting via Step 2 is a control-plane operation that does not interrupt the running worker until the restart. An examiner timing the bootstrap window (documented as 3-8 minutes) who counts the steps in `setup.md` and believes the bootstrap window closes after Step 3 may not realise that Step 2b adds latency between Step 2 and Step 3.

More subtly, the `setup.md` description of Step 2c states "Substitute `<TENANT_ID>` + `<value-set-by-postprovision>` in `swa/staticwebapp.config.json`" — but does not explain that this substitution edits the on-disk file that `azd deploy` will then upload, making the ordering of postprovision vs. `azd deploy` load-bearing. The `azd up` man page does not guarantee this ordering explicitly, and the walkthrough does not call it out.

**Why it matters**
An examiner who times the bootstrap window using the documented 3-8 minute estimate may step away during a 5-minute wait and miss a Step 2b restart failure that leaves `TCP_GENERATOR_OID` unset. The first 07:00 generator run would then silently skip RLS registration and produce no trades, which looks like a correct "empty day" outcome if that Monday morning happens to be a Romanian public holiday (holiday short-circuit). The failure mode is subtle and delayed.

**Suggested fix**
Add Step 2b to the bullet list in B.3 with a brief note on why the restart is needed:

```markdown
- Step 2b: Restart the Function App so the new `TCP_GENERATOR_OID` setting takes effect
  at the process level (the `appsettings set` call in Step 2 is a control-plane write;
  the worker process does not reload until restart).
```

Also add a note below the step list explaining that Step 2c must complete before `azd deploy` uploads `swa/`, which `azd up` guarantees because it runs postprovision hooks between `azd provision` and `azd deploy`.

---

### tut-MA-02. Bootstrap window warning appears inside B.3 but should precede the `azd up` command — a reader who starts `azd up` before reading the warning has already opened the window

**Location** — `docs/setup.md:194-195` (callout block inside B.3)

The bootstrap window warning reads:

> **The 3-8 minute bootstrap window**: between Step 0 (when SQL auth is alive on a public endpoint with a high-entropy password) and Step 3 (AAD-only flip), there is a narrow window where SQL-auth is enabled. [...] Stay attached to the terminal during this period and verify the Step 3 success message before stepping away.

This warning appears **after** the `azd up` command block at lines 168-172. A reader who follows the walkthrough linearly will read the `azd up` command, run it (a five-second keystroke), and only then read the warning. By that point the window is already open and the warning is retrospective advice.

**Why it matters**
The bootstrap window is documented as RR-08 in the threat model — the project explicitly accepts the risk and asks operators to stay attached. The warning is correct in substance, but its placement converts an "opt-in operator awareness" requirement into a post-hoc footnote that most readers will skim after already starting the deploy.

**Suggested fix**
Move the callout block to immediately before the `azd up` command (line 168), not after it. A natural location is after the `azd env set NOTIFICATION_EMAILS` line and before the `azd up` fence:

```markdown
> **Before running `azd up`**: read the bootstrap window note below so you know
> to stay attached to the terminal for the full duration.
```

Then place the full callout where it currently sits but anchor it with a forward pointer from before the command.

---

### tut-MA-03. Track A → Track B isolation: Track A leaves the Docker SQL container running and `TCP_SQL_DEV_PASSWORD` in the env, which silently collides with Track B if a reviewer does both in sequence

**Location** — `docs/setup.md:108` (Stop boundary after A.5) and B.1 onwards

The walkthrough says at the end of Track A:

> **Stop here if you only need a local reproduction.** Track A produces a fully-functional dev environment for editing, testing, and reviewing — no Azure account required.

This is a good boundary marker, but there is no teardown instruction. A reviewer who completes Track A and then immediately starts Track B has:

1. A live Docker container on port 1433 (`tcp-sql-dev`).
2. `TCP_SQL_DEV_PASSWORD` exported in the current shell.
3. `TCP_SQL_SERVER=localhost,1433` and `TCP_SQL_DATABASE=tcp_dev` exported from A.4.

The Track B smoke test in B.5 calls `sqlcmd -S "${SQL_FQDN}" -d "${SQL_DB}" -G` using `SQL_FQDN` and `SQL_DB` derived from `azd env get-value`. If those variables are not set (the examiner skipped that derivation) and the shell still has `TCP_SQL_SERVER=localhost,1433` from Track A, some scripts will silently target the local dev database instead of the Azure database. The smoke test command in B.5 is explicit about the Azure FQDN, so the actual B.5 commands are not affected. However, the acceptance checklist sqlcmd item (line 257) does NOT set `SQL_SERVER` or `SQL_DB` from `azd env get-value` — it expects the examiner to have those variables set from somewhere. An examiner running B directly after A will have them set to the dev values.

**Why it matters**
This is a silent failure mode: the acceptance checklist `schema_history` query (checklist item 2) succeeds against the local dev database showing the sentinel checksum (which is the documented Track A outcome, not a production green state), and the examiner incorrectly concludes the production deploy is green.

**Suggested fix**
Add a teardown note at the Track A stop boundary:

```markdown
> **Switching to Track B**: if you plan to proceed to an Azure deploy in the same shell
> session, first unset the local SQL variables to prevent accidental targeting of the
> dev container during Track B smoke tests:
> ```bash
> unset TCP_SQL_SERVER TCP_SQL_DATABASE TCP_SQL_DEV_USER TCP_SQL_DEV_PASSWORD
> # PowerShell: Remove-Item Env:TCP_SQL_SERVER, Env:TCP_SQL_DATABASE, Env:TCP_SQL_DEV_USER, Env:TCP_SQL_DEV_PASSWORD
> docker compose -f docker-compose.dev.yml down   # optional; stop the dev container
> ```
```

Also update the acceptance checklist sqlcmd item (line 257) to explicitly source `SQL_SERVER` from `azd env get-value AZURE_SQL_SERVER_NAME`, not from ambient env.

---

### tut-MA-04. PowerBI handoff (`setup.md §B.4`) under-specifies the time cost and the sequential dependency on B.3 — a reviewer who parallelises B.3 + B.4 will corrupt the SWA config

**Location** — `docs/setup.md:197-206` (B.4 PowerBI deploy)

The B.4 section states:

> Follow `docs/runbooks/powerbi_deploy.md` — the runbook walks through: [...] `powerbi/deploy.ps1` invocation (idempotent; 9 steps with TE3 + .bim fallbacks).

Two issues:

1. **Time cost is absent.** The `powerbi_deploy.md` runbook header says "~30-45 minutes (first deploy)" but `setup.md` B.4 does not mention this, and it is not reflected in the Track B "45 minutes" estimate at the top of the page. The actual end-to-end time for Track B is therefore: `azd up` (~10-20 min including ARM + postprovision) + PowerBI deploy (~30-45 min) = 40-65 minutes, not 45. A reviewer who plans a 45-minute slot and hits the PowerBI step will run out of time before the acceptance checklist.

2. **Sequential dependency on B.3 Step 2c is not stated.** The postprovision Step 2c substitutes `<TENANT_ID>` and `<value-set-by-postprovision>` into `swa/staticwebapp.config.json`. The PowerBI deploy script Step 7 reads `AZURE_STATIC_WEB_APP_HOSTNAME` (from `azd env get-value`) to substitute the AI Assistant hyperlink in the PBIR report. These are independent — B.4 does not depend on B.3 Step 2c. However, the converse is true: if a reviewer re-runs `powerbi/deploy.ps1` before `azd deploy` has uploaded the substituted `swa/staticwebapp.config.json`, the SWA config in Azure will still contain the placeholder. The `setup.md` ordering (B.3 → B.4) is correct but the reason is not stated, so a reviewer who is "just redeploying PowerBI" after a B.3 partial failure may inadvertently run B.4 out of order.

**Suggested fix**
Add a time note to B.4:

```markdown
> **Time**: allow an additional 30-45 minutes for the first PowerBI deploy (see `runbook §2` for the estimate). The Track B total is therefore 75-90 minutes, not 45.
```

And add a sequencing note:

```markdown
> **Ordering**: B.4 must run after `azd deploy` (the final phase of `azd up` in B.3) has completed. The SWA config substituted by postprovision Step 2c must already be uploaded before the PowerBI report's AI Assistant hyperlink is meaningful.
```

---

## 4. Minor findings

### tut-mi-01. Troubleshooting scenario 8 diagnostic uses `azd hooks list` which does not emit postprovision output

**Location** — `docs/troubleshooting.md:211-213` (scenario 8, Diagnostic block)

```bash
grep -A3 "Step 3" <(azd hooks list)
```

`azd hooks list` outputs the hook *configuration* (which hooks are defined, their path, and their timeout) — it is not a log of hook execution output. The command will print a line like `postprovision: infra/scripts/postprovision.sh` and nothing resembling "Step 3". An examiner who hit the bootstrap-window failure and runs this diagnostic will get useless output, conclude the diagnostic is broken, and stop following the troubleshooting guide.

The correct diagnostic for "did Step 3 complete?" is to check whether AAD-only auth is enabled (which the setup guide's acceptance checklist item 3 already does) or to re-read the deployment log from the `azd up` terminal output (which is not captured in any file by default).

**Suggested fix**
Replace the `azd hooks list` command with a direct AAD-auth-status check and a note about where to find the hook log:

```bash
# Check the current AAD-only state directly:
az sql server ad-only-auth list -s sql-tcp-prod-weu -g rg-tcp-prod-weu -o json

# If you need the postprovision execution log, re-run idempotently and watch stdout:
bash infra/scripts/postprovision.sh 2>&1 | tee /tmp/postprovision.log
```

---

### tut-mi-02. `setup.md §B.5` smoke test item 4 (browser-based `/api/ask` test) is not a command — it breaks the reviewable binary-pass/fail pattern of items 1-3

**Location** — `docs/setup.md:228-231` (B.5 smoke test, item 4)

```bash
# 4. Open the SWA URL in a browser, sign in, ask "How many trades did the
#    Cluj-Napoca floor close yesterday?". Expected: answer paragraph + a small
#    table of row data formatted with the ro-RO locale.
echo "Open: https://${SWA_HOST}"
```

Items 1-3 are CLI commands that return a deterministic exit code — they can be copy-pasted, run, and verified without opening a browser or needing a user account. Item 4 is inherently manual (sign-in, type a question, read the response format). This breaks the mechanical "run this, see that" pattern established by items 1-3 and cannot be automated or scripted for a CI-style review.

This is partially unavoidable (the AAD sign-in cannot be automated in a smoke test without a test user credential), but the current presentation implies it is parallel to items 1-3.

**Suggested fix**
Separate item 4 into a labelled "Manual verification" subsection after the three automated checks, with explicit "Expected" and "Fail indicators" so a reviewer knows what to look for:

```markdown
#### Manual verification (browser)
Open `https://${SWA_HOST}`. Sign in with your AAD account. Type: "How many trades did the Cluj-Napoca floor close yesterday?"

**Pass indicators**: the response contains a prose paragraph + a data table with Romanian-locale formatting (decimal comma, EUR symbol).
**Fail indicators**: 404 "account not registered" → see `docs/troubleshooting.md` §2. 500 "anthropic_unavailable" → see §4.
```

---

### tut-mi-03. Glossary missing "Bootstrap window" link at first mention in `setup.md`

**Location** — `docs/setup.md:195` (B.3 callout) and `docs/glossary.md:140`

The phrase "Bootstrap window" appears in `setup.md` at line 194-195 inside the callout. The glossary defines "Bootstrap window" at line 140 with a link to `docs/security/bootstrap_window.md`. The first mention in `setup.md` has no inline link to the glossary. Per the review spec criterion #8, the link should be present at the first mention.

The same issue applies to "MERGE upsert" (used in the scenario 3 resolution at `troubleshooting.md:79`) and "Free Offer" (used in `setup.md:279` "What gets deployed" table) — both are defined in the glossary but linked nowhere in `setup.md`.

**Suggested fix**
At the first occurrence of "Bootstrap window" in `setup.md`, add: `([glossary](glossary.md#security--compliance))`. Similarly for "Free Offer" at the "What gets deployed" table row.

---

### tut-mi-04. README "Operations" table says "8 common failure scenarios" but `docs/troubleshooting.md` has 9 scenarios

**Location** — `README.md:223` vs `docs/troubleshooting.md:1`

```markdown
# README.md:223
| Something is broken | `docs/troubleshooting.md` — 8 common failure scenarios with diagnostic commands |
```

```markdown
# docs/troubleshooting.md:1
The 9 most common failure modes a TCP operator hits, ...
```

The count is off by one. `docs/troubleshooting.md` consistently self-describes as containing 9 scenarios, and counting the numbered sections confirms: 9 entries (1 through 9).

**Suggested fix**
Update `README.md` line 223 to read "9 common failure scenarios".

---

### tut-mi-05. `setup.md §B.7` Roll-back recommends `azd down --purge` without warning that the Key Vault purge is irreversible for 90 days in some regions

**Location** — `docs/setup.md:246-247` (B.7 Roll back)

```markdown
- **Tear down everything**: `azd down --purge` — drops every resource and purges Key Vault soft-delete. Note this destroys data; use only for clean re-bootstraps.
```

The note says "destroys data" but does not clarify that Key Vault purge removes the ability to recover secrets within the soft-delete retention window (7-90 days, configurable). More importantly, `--purge` on `azd down` also purges all **soft-deleted Key Vaults**, meaning if the KV name `kv-tcp-prod-weu` is held in the soft-delete state, the next `azd up` with the same environment name will either collide (if the KV is still in soft-delete) or succeed with a fresh KV that has lost the `ANTHROPIC-API-KEY` and `SQL-ADMIN-PASSWORD-EXPORT` secrets. The reviewer must then re-set these before the next `azd up`, which `setup.md` does not mention.

**Suggested fix**
Expand the B.7 note:

```markdown
- **Tear down everything**: `azd down --purge` — drops every resource and purges Key Vault soft-delete.
  WARNING: `--purge` is irreversible. The KV name `kv-tcp-prod-weu` is freed immediately,
  but all secrets (including `ANTHROPIC-API-KEY`) are lost. Before your next `azd up`,
  re-run step B.3 `azd env set ANTHROPIC_API_KEY ...` — the KV will be recreated empty.
```

---

## 5. Nit items

### tut-n-01. `setup.md` Track A time estimate ("15 minutes") does not account for Docker image pull

**Location** — `docs/setup.md:12` (Track A heading)

Docker's `mcr.microsoft.com/mssql/server:2022-latest` image is approximately 1.4 GB. On a fresh machine with a 50 Mbps connection, the pull alone takes 3-5 minutes, not counting the healthcheck 2.5 minute window. On a cold machine with a slow ISP, Track A can take 30+ minutes. The "15 minutes" estimate is only accurate on a machine that has already pulled the SQL Server image.

**Suggested fix**: Add "(excluding initial Docker image pull: ~5 min on a fast connection)" to the Track A heading.

---

### tut-n-02. `setup.md §B.3` refers to "`scripts/render_migration.py`" with a relative link that resolves incorrectly from `docs/`

**Location** — `docs/setup.md:187` (footnote link)

```markdown
[`scripts/render_migration.py`](../scripts/render_migration.py)
```

The link uses `../scripts/`, which from `docs/setup.md` resolves to `scripts/render_migration.py` at the repo root — the correct file exists at that path per the repo layout. The link is technically correct. However, the same section later links `infra/scripts/postprovision.{ps1,sh}` as `(../infra/scripts/)` which resolves to a directory, not a file — and markdown link-checkers will follow the directory link and may fail to resolve it. Minor but worth a polish pass with an explicit file extension.

**Suggested fix**: Change the `infra/scripts/` directory link to link to both files explicitly: `[`infra/scripts/postprovision.sh`](../infra/scripts/postprovision.sh) + [`.ps1`](../infra/scripts/postprovision.ps1)`.

---

### tut-n-03. Troubleshooting scenario 5 diagnostic uses `az functionapp function show ... --query "config.bindings[].schedule"` — this field path is not valid for the Functions v2 programming model

**Location** — `docs/troubleshooting.md:128-130` (scenario 5, first diagnostic block)

```bash
az functionapp function show --resource-group "$RG" --name "$FUNC_APP_NAME" \
  --function-name daily_generator --query "config.bindings[].schedule" -o tsv
```

Azure Functions Python v2 uses the programming model where bindings are declared in code (via `@app.timer_trigger()` decorator) rather than in a `function.json` file. The `az functionapp function show` command reads the ARM-side function metadata, which for Python v2 functions does not populate `config.bindings[].schedule` from the in-code decorator — it returns `null`. The correct way to verify the cron schedule is to check the `NCRONTAB_SCHEDULE` app setting or to inspect the app-level host.json, not the function-level ARM metadata.

**Suggested fix**
Replace the diagnostic with:
```bash
az functionapp config appsettings list --name "$FUNC_APP_NAME" -g "$RG" \
  --query "[?name=='WEBSITE_TIME_ZONE'].value" -o tsv
# Expected: E. Europe Standard Time
```
And note that the cron expression itself is hardcoded in `function_app/triggers/daily_generator.py`.

---

### tut-n-04. `setup.md §B.2` OIDC federated credential JSON uses `environment:prod` as the subject — but `cd.yml` must declare an `environment: prod` in the workflow for the token exchange to succeed, and `setup.md` does not mention this requirement

**Location** — `docs/setup.md:140-147` (B.2 OIDC federated credential JSON)

```json
{
  "subject": "repo:<your-gh-org>/tcp-trading-central-panel:environment:prod",
  ...
}
```

The `environment:prod` subject in a GitHub OIDC token is only issued if the workflow job is configured with `environment: prod`. If the examiner creates the federated credential with this subject but the `cd.yml` job does not declare `environment: prod`, the token exchange will return a 401 with the message "subject does not match" — a non-obvious error that is easy to diagnose once you know the pattern, but very opaque to a first-time reader. `setup.md` does not mention that the `cd.yml` job must already have `environment: prod` for the exchange to succeed.

**Suggested fix**
Add a note after the federated credential JSON block:
```markdown
> The `environment:prod` subject requires the GitHub Actions job in `.github/workflows/cd.yml`
> to declare `environment: prod`. Verify this before running the CD pipeline.
> Alternatively, use `ref:refs/heads/main` as the subject (branch-based, no environment required):
> `"subject": "repo:<your-gh-org>/tcp-trading-central-panel:ref:refs/heads/main"`
```

---

## 6. Strengths

1. **Clean two-track separation with an explicit stop boundary.** The "Stop here if you only need a local reproduction" marker at the end of A.5 is exactly the right pedagogical affordance — it lets a reviewer exit Track A cleanly without even reading Track B. Most project walkthrough documents blend the two tracks and force readers to mentally filter what applies to them.

2. **B.5 smoke test is a genuine binary-pass/fail sequence.** Items 1-3 of the smoke test (deployment state, schema_history checksum, AAD-only auth) are each a single command with an unambiguous expected output. This is the correct format for a thesis-defence walkthrough: the examiner can run three commands in sequence and independently confirm the deploy is green without interpretation.

3. **Troubleshooting scenarios follow a consistent template.** Every entry uses the Symptom → Diagnostic → Resolution → Reference pattern, and each Diagnostic block contains copy-pasteable commands. Scenarios 3, 5, and 8 are particularly load-bearing and all three handle the failure-to-fix loop correctly: scenario 3 explains the sentinel vs. production checksum distinction, scenario 5 identifies the three distinct root causes (timezone, exception, holiday short-circuit), and scenario 8 provides the Step 3 re-run path with the correct CLI command.

4. **Glossary is a genuine consolidated single source of truth.** The explicit statement "This page supersedes the per-document glossaries that appeared in earlier stages" (line 14-19) and the back-references in the group descriptions give the document authority. The six-group organisation (trading domain / DB / Azure / observability / security / conventions) maps exactly to the mental model a reader builds while working through `setup.md` and `troubleshooting.md` in order.

5. **README Quickstart is honest about what is automated and what is not.** The three `azd up` bullets (lines 58-63) accurately describe what each phase does at the right level of detail — not a black box ("run this and magic happens") nor a full step-by-step repeat of `setup.md`. The "What gets deployed" table with per-resource cost bands is a strong closing affordance for an examiner evaluating the €0/month claim.

6. **`dev_setup.md` cross-link from Track A is well-structured.** The opening paragraph of `setup.md` links to `dev_setup.md` as "the detailed reference" for Track A, and `dev_setup.md` correctly dual-tracks every command (bash + PowerShell). The relationship is clear: `setup.md` is the happy-path summary, `dev_setup.md` is the reference. This is the correct separation between a walkthrough and a reference guide.

---

## 7. Troubleshooting scenario walkthrough (scenarios 3, 5, 8)

### Scenario 3 — Sentinel checksum in `schema_history`

Starting point: the CD smoke job fails with `ERROR: schema_history contains an unsubstituted checksum placeholder`.

1. Run the diagnostic: `sqlcmd -S "${SQL_FQDN}" -d "${SQL_DB}" -G -Q "SELECT script_name, checksum FROM dbo.schema_history"`. The two variables `SQL_FQDN` and `SQL_DB` are not defined in the troubleshooting doc — the examiner must have them from a previous step or know to derive them from `azd env get-value`. **BLOCKER** if the examiner landed here from the acceptance checklist without a prior shell session.
2. The resolution for "In production" correctly identifies the postprovision re-run path and notes idempotency. The `bash infra/scripts/postprovision.sh` command works on POSIX. The PowerShell equivalent is `pwsh -c "./infra/scripts/postprovision.ps1"` — correctly provided.
3. The "In local dev: harmless" note is accurate and well-explained.

**Gap**: the `SQL_FQDN` and `SQL_DB` variables in the diagnostic are never defined in `troubleshooting.md`. Add a one-line derivation note at the top of the troubleshooting index or in a "shortcut variables" box:
```bash
SQL_FQDN=$(azd env get-value AZURE_SQL_SERVER_NAME).database.windows.net
SQL_DB=$(azd env get-value AZURE_SQL_DATABASE_NAME)
```

### Scenario 5 — Daily generator did not run

Starting point: the workbook "Daily generator runs vs failures" tile is empty for today.

1. First diagnostic: `az functionapp function show ... --query "config.bindings[].schedule"` — this returns null for Python v2 (see tut-n-03). The examiner gets empty output and cannot determine whether the schedule is correct.
2. Second diagnostic: `az monitor app-insights query` is the correct command. The `--analytics-query` argument is documented with backtick usage in the string — this works on bash but requires special quoting on PowerShell (`--analytics-query "..."` with double-quotes and no pipe operator inside the string, or a here-string). No PowerShell equivalent is given.
3. Third diagnostic: `az functionapp config appsettings list ... --query "[?name=='TCP_GENERATOR_OID'].value"` is correct and returns an unambiguous result.
4. Resolution blocks are complete and accurate. The holiday short-circuit explanation is a valuable note.

**Gap**: item 1 in the diagnostic produces misleading output (see tut-n-03). Item 2 has a PowerShell compatibility gap (minor; the App Insights query string contains pipes which are command separators in PowerShell).

### Scenario 8 — Bootstrap window slipped

Starting point: AAD-only auth reports `false` after `azd up` completed.

1. First diagnostic (`azd hooks list`) returns hook configuration, not execution log. Useless for this diagnosis (see tut-mi-01).
2. Second diagnostic (`azd env get-values`) returns all env variables — this can confirm that `AZURE_SQL_SERVER_NAME` is set, but it does not tell the examiner whether Step 3 ran.
3. Resolution: re-run `bash infra/scripts/postprovision.sh` — correct. The script is idempotent; Step 3 re-executes `az sql server ad-only-auth enable`, which is safe.
4. The "if Step 3 still fails" path — re-register the AAD admin and re-run `az sql server update --name ... --enable-ad-only-auth true` — is correct but uses `az sql server update` whereas `postprovision.sh` uses `az sql server ad-only-auth enable`. Both commands converge to the same state, but the examiner who has already tried `az sql server ad-only-auth enable` and is now trying the alternative may be confused. A note clarifying that both achieve the same result would help.

---

## 8. Time estimate realism assessment

| Phase | `setup.md` claim | Realistic estimate (cold machine, fast connection) |
|---|---|---|
| Track A (uv sync + Docker pull + healthcheck + schema apply + test suite) | 15 min | 25-35 min (Docker image pull alone is 5-10 min cold) |
| Track B `azd up` (ARM compilation + module deploys + postprovision 8 steps) | Part of 45 min | 15-25 min (ARM can take 10-15 min; postprovision adds 3-5 min) |
| Track B PowerBI deploy (pbi-tools compile + REST API calls + refresh poll) | Absorbed in 45 min | 30-45 min per `powerbi_deploy.md §2` |
| Track B acceptance checklist | — | 5-10 min |

**Total Track A + B**: 55-110 minutes on a cold first run. The 45-minute claim for Track B is not achievable if PowerBI is included. The gap is largest when `pbi-tools` is not pre-installed (adds `dotnet tool install --global pbi-tools` download time). The Track A 15-minute claim is achievable only on a machine with the Docker image already cached.

**Recommendation**: revise Track B to "75-90 minutes (including PowerBI)" and Track A to "15 minutes (excluding Docker image pull on first run)".

---

## 9. Postprovision sequence audit (against actual scripts)

| Step in `setup.md` | Bash script | PS script | Notes |
|---|---|---|---|
| Step 0: Apply V001 + V002 | `sh:64-117` | `ps1:57-116` | Symmetric. SHA-256 substitution via `render_migration.py`. Correct. |
| Step 1: Register MI in RLS | `sh:120-163` | `ps1:118-170` | Symmetric. RLS disable/enable in try/finally. Correct. |
| Step 2: Set TCP_GENERATOR_OID | `sh:166-175` | `ps1:172-185` | Symmetric. |
| **Step 2b (missing from setup.md)** | `sh:177-185` | `ps1:187-199` | Function App restart. Present in scripts, absent from `setup.md` description. |
| Step 2c: Substitute SWA config | `sh:187-217` | `ps1:201-229` | Symmetric. Python-based replacement on bash; PowerShell `Set-Content -NoNewline` on PS. |
| Step 3: Enable AAD-only auth | `sh:219-227` | `ps1:231-244` | **Bash uses `az sql server ad-only-auth enable`; PS uses `Set-AzSqlServerActiveDirectoryOnlyAuthentication`.** These are two different CLI paths to the same ARM operation. Not a bug but worth noting for troubleshooting parity (scenario 8 uses `az sql server update --enable-ad-only-auth` — a third path). |
| Step 4: Delete bootstrap password | `sh:229-242` | `ps1:246-266` | Symmetric. |
| Step 5: Verify AAD-only + bootstrap-secret deletion | `sh:244-267` | `ps1:268-304` | Symmetric. **PS uses `azureAdOnlyAuthentication` (camelCase) vs bash `azureADOnlyAuthentication` (mixed) — both come from `az sql server ad-only-auth list -o json`; the actual field name is `azureADOnlyAuthentication`. The PS comparison `$aadOnlyStatus.azureAdOnlyAuthentication -eq $true` will evaluate `$null -eq $true` = `$false` and throw, because PowerShell JSON deserialization of Azure CLI output is case-sensitive on property names.** |

The Step 5 PowerShell field-name mismatch (`azureAdOnlyAuthentication` vs `azureADOnlyAuthentication`) is a latent runtime error that will cause the Step 5 verification to fail even when Step 3 succeeded. This is a bug in `postprovision.ps1` (not in the documentation), but it directly affects the troubleshooting story for scenario 8: an examiner who successfully flips AAD-only auth in Step 3 but hits the Step 5 PS exception will conclude the bootstrap failed and re-run the postprovision unnecessarily.

**Note for the convergence pass**: this PS field-name issue is an incidental finding during the postprovision audit. It is not strictly in the tutorial-review scope but is called out here because it affects the troubleshooting story documented in `troubleshooting.md` scenario 8.

---

## 10. Recommendation

**CHANGES-REQUESTED.**

Block the convergence pass on **tut-CR-01** (acceptance checklist unresolved variables) and **tut-CR-02** (Track A POSIX-only commands without PowerShell dual-track). Both are direct barriers for an examiner on a cold start.

The four Majors are tightly scoped and can land in the same diff:
- **tut-MA-01**: Add Step 2b to the B.3 bullet list and the ordering note for Step 2c vs `azd deploy` (< 3 lines).
- **tut-MA-02**: Move the bootstrap window callout before the `azd up` command fence (< 5-line diff).
- **tut-MA-03**: Add teardown note at Track A stop boundary + fix acceptance checklist sqlcmd to derive SQL_SERVER from `azd env get-value` (< 8 lines).
- **tut-MA-04**: Add time note and sequencing note to B.4 (< 4 lines).

The five Minors (tut-mi-01 through tut-mi-05) and four Nits (tut-n-01 through tut-n-04) can defer to an Etapa-12 polish pass without blocking reviewer reproduction. The latent `postprovision.ps1` Step 5 PS field-name mismatch (section 9) should be assigned to the code-reviewer for the convergence pass — it is not a documentation issue but it undermines the troubleshooting story.

After CR + MA land, the walkthrough should be retested on a Windows PowerShell session against a clean subscription to validate the Track A 15-minute claim with the image-pull caveat and the Track B sequencing.

---

*End of `review_etapa9_tutorial.md`.*
