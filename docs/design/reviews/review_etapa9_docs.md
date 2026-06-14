# Etapa 9 — Documentation Architecture Review

| Field | Value |
|---|---|
| **Reviewer** | technical-documentation-architect (specialised pass) |
| **Date** | 2026-05-16 |
| **Scope** | `README.md` (top-level), `docs/decisions/INDEX.md`, `docs/setup.md`, `docs/troubleshooting.md`, `docs/glossary.md`, `docs/design/01_business_requirements.md` §11 (supersession note), six component READMEs (`tcp/`, `db/`, `function_app/`, `swa/`, `powerbi/`, `docs/observability/`) |
| **Branch** | `feat/azure-rewrite` |
| **Working tree** | clean at HEAD `2dc18aa` |
| **Companion review** | `review_etapa9_tutorial.md` (tutorial-engineer pass — covers Track A/B learner journey, PowerShell dual-track, acceptance checklist variables, bootstrap warning placement) |

---

## 1. Summary

The Etapa 9 documentation set represents a meaningful maturation of the project's information architecture. The top-level README answers all three audience questions (what is this, how do I run it, where do I find X) within a readable scan. The six-group glossary is the strongest single document in the batch: comprehensive, well-cross-referenced, and clearly authoritative as the consolidated source of truth. The ADR INDEX adds genuine value over reading each ADR individually. Component READMEs follow a disciplined one-line scope header that anchors a reader to the navigation graph immediately.

That said, **three critical issues** break the information graph at load-bearing junctions: a dead cross-reference from `docs/troubleshooting.md` scenario 1 to a non-existent ADR section, a missing shell-variable preamble that renders every diagnostic command in the troubleshooting doc a copy-paste failure on a cold shell, and a wrong DAX-measure count in the top-level README that contradicts two other sources. **Six major issues** cover factual errors in setup commands, a stale TMDL file-count, a non-existent CLI command in the diagnostic shortcuts, inconsistencies in the glossary's coverage of the `config_*` table prefix, and missing TMDL/PBIR terms that appear throughout the project docs. The minor and nit items are polish-pass material.

**Verdict: CHANGES-REQUESTED.** Block on docs-CR-01 (dead ADR reference), docs-CR-02 (undefined diagnostic variables), docs-CR-03 (wrong DAX measure count contradicts the authoritative source). The six Majors should land in the same convergence diff. Minors and Nits defer to an Etapa-12 polish pass without operational risk.

| Severity | Count |
|---|---:|
| Critical | 3 |
| Major | 6 |
| Minor | 6 |
| Nit | 4 |
| Strengths | 7 |

---

## 2. Critical findings (block convergence pass)

### docs-CR-01. `troubleshooting.md` scenario 1 links "ADR-001 §6.1" — ADR-001 has no §6.1; OIDC setup lives in `docs/design/03_architecture.md §6.1`

**Location** — `docs/troubleshooting.md:26`

```markdown
- [ADR-001 §6.1](decisions/ADR-001-powerbi-deployment.md) for the OIDC setup pattern
```

**Why it matters**
ADR-001 (`docs/decisions/ADR-001-powerbi-deployment.md`) has exactly four sections — Context, Decision, Consequences, References — with no §6.1 subsection. Clicking this link lands a reader at the top of the ADR, which covers PowerBI REST-API deployment strategy, not OIDC credential setup. The resolution instruction for scenario 1 ("Re-run the one-time setup from `setup.md §B.2`") is correct; the reference link is simply wrong and will confuse a reader who follows it looking for the OIDC pattern.

The OIDC one-time setup is correctly documented at `docs/design/03_architecture.md §6.1` ("One-time setup (manual, by the user)"), and `docs/setup.md:119` already links to that section correctly. The troubleshooting reference should point to the same place.

**Suggested fix**
Change line 26 of `docs/troubleshooting.md` to:

```markdown
- [`docs/design/03_architecture.md §6.1`](design/03_architecture.md) for the OIDC one-time setup pattern
```

---

### docs-CR-02. `troubleshooting.md` diagnostic commands reference seven undefined shell variables — the doc is not self-contained on a cold shell

**Location** — `docs/troubleshooting.md:38`, `:69`, `:98`, `:101`, `:128`, `:132`, `:136`, `:161`, `:165`, `:172`, `:188`, `:262`

The diagnostic commands throughout the nine scenarios use `$SQL_FQDN`, `$SQL_DB`, `$KV_NAME`, `$FUNC_APP_NAME`, `$AI_APP_ID`, `$STORAGE_ACCOUNT`, and `$SQL_SERVER`. None of these variables are defined anywhere in the document. Only `$RG` appears once in `docs/setup.md §B.5` (`RG=$(azd env get-value AZURE_RESOURCE_GROUP)`), but that shell session ends when the reviewer closes the terminal.

The document opening paragraph says each diagnostic follows "commands that confirm the diagnosis". The intent is that the commands are runnable. But a reviewer who opens `docs/troubleshooting.md` independently — the scenario where an alert fires at 09:00 and the reviewer has no prior shell session — must reverse-engineer the variable definitions themselves before any diagnostic is useful. Scenario 3 (sentinel checksum) and scenario 4 (Anthropic unavailable) are the most load-bearing recovery paths; both are blocked by undefined `$SQL_FQDN`/`$SQL_DB` and `$KV_NAME`.

The companion review (`review_etapa9_tutorial.md §7`, tut-mi-01 context) identified the `$SQL_FQDN` / `$SQL_DB` gap in the scenario 3 walkthrough specifically. This finding generalises: all seven undefined variables share the same root cause.

**Why it matters**
An operator hitting a production incident (scenario 4: Anthropic unavailable, scenario 5: generator silent failure) runs the diagnostic commands verbatim because the doc says they confirm the diagnosis. Empty output from `az keyvault secret show --vault-name "$KV_NAME"` when `$KV_NAME` is unset returns a silent error, not a meaningful diagnostic. On Azure CLI, `--vault-name ""` returns a resource-not-found error that looks like the vault is missing — not that the variable is undefined.

**Suggested fix**
Add a "Diagnostic preamble" section at the top of `docs/troubleshooting.md`, directly after the template description, that defines every variable from `azd env get-value`:

```bash
# Run once per shell session before using any diagnostic below:
RG=$(azd env get-value AZURE_RESOURCE_GROUP)
SQL_SERVER=$(azd env get-value AZURE_SQL_SERVER_NAME)
SQL_DB=$(azd env get-value AZURE_SQL_DATABASE_NAME)
SQL_FQDN="${SQL_SERVER}.database.windows.net"
KV_NAME=$(azd env get-value AZURE_KEYVAULT_NAME)
FUNC_APP_NAME=$(azd env get-value AZURE_FUNCTION_APP_NAME)
AI_APP_ID=$(azd env get-value AZURE_APPLICATION_INSIGHTS_NAME)
STORAGE_ACCOUNT=$(azd env get-value AZURE_STORAGE_ACCOUNT_NAME)
```

Add a note that these are the `azd` output variable names from `infra/main.bicep` and that the values are populated only after a successful `azd provision`. The "Diagnostic shortcuts" section at line 270 already has `azd env get-value AZURE_RESOURCE_GROUP` as an example — the preamble consolidates this into a single copy-pasteable block.

---

### docs-CR-03. Top-level `README.md` claims "67 DAX measures" but `powerbi/README.md` says 48, and the actual TMDL contains 69 `measure` definitions

**Location** — `README.md:78` vs `powerbi/README.md:9` vs `powerbi/model/tables/_Measures.tmdl` (counted: 69 `measure '...'` lines)

```markdown
# README.md:78
| **PowerBI** | TMDL semantic model + PBIR report; Import mode; 67 DAX measures; ...
```

```markdown
# powerbi/README.md:9
... 48 DAX measures covering all KPI families from 01_business_requirements.md §4 ...
```

Three separate documents give three different numbers: 67 (root README), 48 (powerbi README), and 69 (actual TMDL file count). `powerbi/README.md` is the authoritative component document and it self-consistently says 48 throughout (the KPI Coverage Summary table at the bottom of the file sums to 48 across eight families). The root README "67" is a stale value that does not match any other source. The actual TMDL grep (`grep -c "^\s*measure '" powerbi/model/tables/_Measures.tmdl`) returns 69, which includes measures that were added during convergence passes but whose KPI count in the business requirements doc was not updated.

**Why it matters**
The root README is the entry point for thesis examiners and code reviewers. The "67 DAX measures" claim is the first KPI surface they see and it contradicts the PowerBI component document they will reach next. An examiner who notices the discrepancy will ask which number is correct — an unresolved factual conflict in the thesis artefact set.

The 48-KPI count in `powerbi/README.md` matches `docs/design/01_business_requirements.md §4` (48 KPIs across 8 families), which is the business requirements baseline. That is the defensible number for the thesis. The 69 TMDL count includes measures added as computational helpers (e.g., `KPI-TR-009 Total Commission helper` and multi-level aggregate variants not individually named in the business requirements).

**Suggested fix**
Update `README.md:78` from "67 DAX measures" to "48 DAX measures (plus computational helpers)" to match `powerbi/README.md` and align with the business requirements baseline. Add a note in `powerbi/README.md` acknowledging the 69-measure TMDL count is the implementation total, with the KPI Coverage Summary (48) being the business-requirements-aligned subset.

---

## 3. Major findings (should fix before convergence)

### docs-MA-01. `docs/troubleshooting.md` "Diagnostic shortcuts" includes `az portal` — this command does not exist in the Azure CLI

**Location** — `docs/troubleshooting.md:278`

```markdown
| Open the workbook quickly | `az portal --query <subscription-id>` then navigate to *Monitor → Workbooks → TCP — Operations dashboard* |
```

`az portal` is not a valid Azure CLI command. The Azure CLI has no `portal` sub-command; the portal is a web browser destination, not a CLI resource. Running `az portal --query <subscription-id>` returns `'portal' is not in the 'az' command group`. An operator who copies this from the shortcuts table while chasing a live incident gets a CLI error that adds confusion at exactly the wrong moment.

The intended affordance is almost certainly a direct portal URL. The Operations workbook URL follows a stable pattern: `https://portal.azure.com/#resource/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.Insights/workbooks/<workbook-id>/workbook`. The `workbook-id` is the deterministic GUID from `infra/modules/workbook.bicep` (`guid(appInsightsId, 'tcp-ops-workbook')`), which is stable per deploy.

**Suggested fix**
Replace the `az portal` entry with an `echo` that prints the portal deep-link, or a note directing to Monitor → Workbooks in the browser:

```markdown
| Open the workbook quickly | Open [portal.azure.com](https://portal.azure.com) → Monitor → Workbooks → Recent → *TCP — Operations dashboard* |
```

---

### docs-MA-02. `README.md` "Architecture overview" claims "16 TMDL files" — actual count is 20

**Location** — `README.md:118`

```markdown
├── model/                         — 16 TMDL files (database, tables, relationships, roles, measures)
```

The actual `powerbi/model/` directory contains: 4 root TMDL files (`database.tmdl`, `model.tmdl`, `relationships.tmdl`, `roles.tmdl`), 15 table TMDL files (14 data tables + `_Measures`), and 1 culture file (`cultures/ro-RO.tmdl`) — 20 total. The "16" count is a stale artefact from an earlier stage of the model when fewer tables existed.

**Why it matters**
The repository layout section of the README is used by reviewers to orient themselves before diving into the model directory. A reviewer who counts 20 files and sees "16" in the README will either distrust the README or spend time wondering if some files are auto-generated or excluded.

**Suggested fix**
Update `README.md:118` to "20 TMDL files (database, model, relationships, roles, 15 tables + 1 culture)".

---

### docs-MA-03. `docs/setup.md §B.2` OIDC role-assignment command uses `APP_ID` (application client ID) as the `--assignee` argument — Azure CLI role assignment expects a service principal object ID or UPN

**Location** — `docs/setup.md:134-150`

```bash
APP_ID=$(az ad app create --display-name "tcp-cd" --query appId -o tsv)
az ad sp create --id "$APP_ID"
# ...
az role assignment create --assignee "$APP_ID" --role "Owner" --scope "/subscriptions/$SUB_ID"
```

`az role assignment create --assignee` accepts a service principal object ID, a user object ID, or a user principal name. It does not reliably accept an application client ID (`APP_ID` from `appId`). While Azure CLI does perform a graph lookup and may resolve an app client ID to its service principal in many tenants (via `--assignee` shorthand auto-resolution), this behaviour depends on the tenant's AAD permissions for the executing principal. In tenants where the operator does not have `Application.Read.All` or equivalent, the graph lookup fails silently and the role assignment targets the wrong object or returns `BadRequest`.

The correct pattern for assigning a role to a service principal created from an app registration is to capture the service principal's object ID separately:

```bash
SP_OID=$(az ad sp show --id "$APP_ID" --query id -o tsv)
az role assignment create --assignee-object-id "$SP_OID" --assignee-principal-type ServicePrincipal \
  --role "Owner" --scope "/subscriptions/$SUB_ID"
```

The `--assignee-object-id` + `--assignee-principal-type ServicePrincipal` form is unambiguous and avoids the graph-lookup dependency.

**Why it matters**
A failing role assignment produces a silent success — `az role assignment create` exits 0 but the SP has no `Owner` role. The failure manifests on the first `azd up` as an `AuthorizationFailed` error on `Microsoft.Authorization/roleAssignments/write`, which is exactly scenario 1 in `docs/troubleshooting.md`. An examiner who followed `setup.md §B.2` faithfully will then look at scenario 1, re-run `az role assignment list`, and conclude the SP does not have Owner — without realising that the problem was the `setup.md` role-assignment command itself.

**Suggested fix**
Replace `docs/setup.md:150` with the two-step form using `--assignee-object-id`:

```bash
SP_OID=$(az ad sp show --id "$APP_ID" --query id -o tsv)
az role assignment create --assignee-object-id "$SP_OID" \
  --assignee-principal-type ServicePrincipal \
  --role "Owner" --scope "/subscriptions/$SUB_ID"
```

Also update `docs/setup.md:153` to instruct storing `SP_OID` (the service principal object ID) as `AZURE_CLIENT_ID` in the GitHub Actions variables — the CD workflow uses `client-id: ${{ vars.AZURE_CLIENT_ID }}` in the `azure/login@v2` action, which in the OIDC flow takes the application's client ID (`APP_ID`), not the SP object ID. The two IDs serve different purposes: `AZURE_CLIENT_ID` in the GitHub Actions variables is indeed the app client ID (`APP_ID`), while the role assignment target is the SP object ID (`SP_OID`). Clarify this distinction explicitly so the examiner knows which ID goes where.

---

### docs-MA-04. `docs/glossary.md` §2 defines `dim_*` and `fact_*` table prefixes but omits `config_*` — the third naming-convention prefix documented in `CLAUDE.md`

**Location** — `docs/glossary.md:67-69` (missing entry after `fact_*`)

The database naming convention enforced by CI has three table prefixes: `fact_*`, `dim_*`, and `config_*` (per `CLAUDE.md` §DB naming convention and `docs/design/02_database_design.md`). The glossary defines entries for `dim_*` (line 67) and `fact_*` (line 69) but has no `config_*` entry. The only mention of `config_Capital` in the glossary is as a back-reference inside the "Capital Baseline" term definition (line 27), not as a first-class table-prefix entry.

**Why it matters**
A reviewer who reads the glossary to understand the DB naming convention gets an incomplete picture — two of three prefixes are explained, the third is not. The `config_Capital` table is load-bearing in the capital baseline logic for ROC, Sharpe normalisation, and the Capital Utilisation Ratio, and its effective-date semantics differ from dimension and fact tables. The glossary is the declared single source of truth for terminology; the omission undermines that claim for the DB layer.

**Suggested fix**
Add a `config_*` row between `fact_*` and `MERGE` in the §2 table:

```markdown
| **config_*** | Configuration table — rows carry effective-date semantics via `effective_from` and optional `trader_id` for per-trader overrides. Naming: `config_PascalCase`. Only instance in v1.0: `config_Capital` (capital baseline). |
```

---

### docs-MA-05. `docs/glossary.md` omits TMDL and PBIR — both terms appear in the root README, `powerbi/README.md`, and ADR-001, but are not defined anywhere accessible to a newcomer

**Location** — `docs/glossary.md` §3 (Azure & infrastructure) — gap

The glossary's §3 covers all Azure infrastructure terms (`azd`, `Bicep`, `DefaultAzureCredential`, `OIDC`, etc.) but does not define TMDL (Tabular Model Definition Language) or PBIR (Power BI Report Definition). Both terms:

- Appear in `README.md:78,117,118,277`
- Appear in `powerbi/README.md:9,21` extensively
- Are defined in `docs/design/01_business_requirements.md §11` (the Etapa-1 glossary that was superseded by this document)
- Are referenced as the deployment formats in ADR-001

The Etapa-1 glossary (line 633 of `01_business_requirements.md`) defines TMDL correctly: "A human-readable, Git-friendly format for defining Power BI / Analysis Services tabular models." PBIR is defined implicitly in the ADR-001 references section. The consolidated glossary supersedes the Etapa-1 glossary but does not carry these terms forward.

**Why it matters**
A thesis examiner who reads `README.md:78` ("TMDL semantic model + PBIR report") and turns to the consolidated glossary to understand these terms finds nothing. The Term-finding tip at the bottom of the glossary (lines 177-183) does not list `powerbi/README.md` as a fallback source. The reader is stranded.

**Suggested fix**
Add two entries to §3 (Azure & infrastructure):

```markdown
| **TMDL (Tabular Model Definition Language)** | Human-readable, Git-diffable format for PowerBI semantic models (tables, measures, relationships, RLS roles, culture translations). TCP's model lives under `powerbi/model/`. Deployed via XMLA endpoint per ADR-001. |
| **PBIR (Power BI Report Definition)** | JSON-based format for PowerBI report layout (pages, visuals, filters). Still in preview as of the project start date; visual polish requires a manual PowerBI Desktop pass (see `powerbi/README.md §Local Development Workflow`). TCP's report skeleton lives under `powerbi/report/`. |
```

Also update the Term-finding tip to add: "PowerBI model/report terms → `powerbi/README.md`".

---

### docs-MA-06. `function_app/README.md` "Running locally" section references `local.settings.json.template` and `scripts/apply_schema.py` — neither path matches the current repo layout

**Location** — `function_app/README.md:35-42`

The section instructs:

```powershell
# Copy the template and fill the dev values.
cp local.settings.json.template local.settings.json
```

And later references "the schema applied via `scripts/apply_schema.py` (Etapa 2 deliverable)". Two issues:

1. The copy command is PowerShell syntax (`cp` is `Copy-Item` on PowerShell), but the comment says "From the repo root" while the instruction `cd function_app` is above it — the template file's actual location is `function_app/local.settings.json.template`. The `cp` command as written, executed from `function_app/`, is correct. But combined with the PowerShell comment context and the bash-style `cp`, it is ambiguous.

2. `scripts/apply_schema.py` does not exist. The schema application path in the current codebase is `db/migrations/V001__init.sql` applied directly via `sqlcmd`, as documented in `db/README.md` and `docs/setup.md §A.3`. There is no Python helper script for schema application; this reference is a dead link to an Etapa-2-era scaffold that was never created or was removed.

**Why it matters**
The `function_app/README.md` is the local-dev entry point for a contributor working on the trigger code. A contributor who reaches the "Running locally" section and tries to apply the schema via `scripts/apply_schema.py` will get a `FileNotFoundError` with no path to recovery within that README. The correct path (sqlcmd + `db/migrations/`) is documented elsewhere but not linked.

**Suggested fix**
Remove the `scripts/apply_schema.py` reference and replace with a link to the current path:

```markdown
- The schema applied via `sqlcmd` per [`db/README.md §Apply locally`](../db/README.md).
```

For the `cp` command, add a PowerShell alternative:
```powershell
# PowerShell:
Copy-Item local.settings.json.template local.settings.json
```

---

## 4. Minor findings

### docs-mi-01. `docs/decisions/INDEX.md` forward-references "ADR-008 (future)" — a reader may attempt to locate ADR-008 which does not exist

**Location** — `docs/decisions/INDEX.md:27`

```markdown
- **ADR-002 ↔ ADR-008 (future)** — the materialisation choice in ADR-002 has cost-budget implications that the (not-yet-filed) error-budget policy ADR will reference...
```

The INDEX is the at-a-glance lookup for all filed ADRs. Including a forward reference to a not-yet-filed ADR mixes present state (five filed ADRs) with future intent, which erodes the index's reliability as a reference point. A reviewer who checks the `docs/decisions/` directory and finds no ADR-008 may wonder if it was lost or mis-numbered.

**Suggested fix**
Move the ADR-002 ↔ future-ADR cross-cutting note into ADR-002's own "Consequences" or "Status notes" section where future-state notes belong. Remove the forward reference from INDEX.md's "How to read an ADR" section entirely, or reword to: "ADR-002 has cost-budget implications for a future error-budget policy ADR (to be filed after 30 days of telemetry)."

---

### docs-mi-02. Glossary link text includes the `docs/` prefix while the href omits it — minor inconsistency that can confuse link-checkers

**Location** — `docs/glossary.md:16-17`

```markdown
- The Etapa-1 glossary in [`docs/design/01_business_requirements.md`](design/01_business_requirements.md) §11.
- Ad-hoc definitions in [`docs/security/threat_model.md`](security/threat_model.md), ...
```

The link text says `docs/design/01_business_requirements.md` (with the `docs/` prefix) but the href says `design/01_business_requirements.md` (without). From `docs/glossary.md`, the relative href `design/01_business_requirements.md` correctly resolves to `docs/design/01_business_requirements.md`. The link is not broken, but the display text and the href disagree about their reference point, which causes markdown link-checkers that parse the display text literally to report a false mismatch.

**Suggested fix**
Either remove the `docs/` prefix from the link text (matching the relative href):
```markdown
[`design/01_business_requirements.md`](design/01_business_requirements.md)
```
Or convert to an absolute-path href (for consistency with other docs that use absolute paths):
```markdown
[`docs/design/01_business_requirements.md`](/docs/design/01_business_requirements.md)
```
The relative-href form is shorter and matches the convention used throughout the glossary.

---

### docs-mi-03. `README.md` "Architecture overview" section misleadingly describes the Function App as having four triggers when five exist

**Location** — `README.md:76`

```markdown
| **Function App** | Cron + HTTP backend: daily synthetic-trade generator, weekly BACPAC export, `/api/ping` warmup, `/api/ask` AI assistant | ...
```

The description lists four conceptual roles. The actual Function App has five triggers: `daily_generator`, `warmup` (a separate timer that runs at 06:55 RO to pre-warm the SQL connection before the 07:00 generator), `bacpac_export`, `ping` (HTTP GET for on-demand warm-up and `tcp.sql.resume_ms` telemetry), and `ask` (HTTP POST for the AI assistant). The README conflates the `warmup` timer and the `ping` HTTP endpoint into a single "/api/ping warmup" entry, which misrepresents the architecture. The `warmup` timer is a time-triggered function with no HTTP route; `ping` is a separate HTTP-triggered function.

This matters because the architecture table is the first structural description a reader sees. A reviewer who later reads `function_app/README.md` (which correctly lists all five triggers in a table) will notice the discrepancy.

**Suggested fix**
Update the Function App description in `README.md:76` to:

```markdown
| **Function App** | Cron + HTTP backend: daily synthetic-trade generator (`07:00 RO`), warmup timer (`06:55 RO`), weekly BACPAC export (`08:00 RO Sundays`), `/api/ping` warm-up endpoint, `/api/ask` AI assistant |
```

---

### docs-mi-04. `setup.md §A.3` link text says "ADR-related notes" but the link target is `docs/security/threat_model.md` — which is not an ADR

**Location** — `docs/setup.md:67`

```markdown
See [ADR-related notes](../docs/security/threat_model.md) RR-09 for the full integrity chain.
```

The link text "ADR-related notes" implies the destination is an ADR file. The actual destination is the threat model, which is a security document, not an ADR. The RR-09 item is a residual risk entry in the threat model, not an ADR clause. The correct document for ADR-related notes on RR-09 would be the ADR INDEX or ADR-002 (which references RR-09 indirectly). The threat model is the right document for RR-09 itself, but the link text is misleading.

**Suggested fix**
Replace "ADR-related notes" with descriptive text:

```markdown
See [threat model RR-09](../docs/security/threat_model.md) for the full migration integrity chain.
```

---

### docs-mi-05. `powerbi/README.md` "Deploy Procedure" section attributes authorship to "docs-architect parallel agent" and "deploy-agent parallel agent" — these are internal build-tool references that should not appear in committed documentation

**Location** — `powerbi/README.md:104,108`

```markdown
See `docs/runbooks/powerbi_deploy.md` (authored by the docs-architect parallel agent) for the step-by-step deploy runbook.
...
1. Run `powerbi/deploy.ps1` (authored by the deploy-agent parallel agent) ...
```

The agent attribution is a build-process artefact from the Etapa-7 multi-agent build. Committed documentation should describe what the files do and where they live, not the tool that generated them. A thesis examiner reading "authored by the docs-architect parallel agent" will either not know what that means or be distracted by an implementation detail of the build process.

**Suggested fix**
Remove both parenthetical agent-attribution clauses. The revised lines read:

```markdown
See `docs/runbooks/powerbi_deploy.md` for the step-by-step deploy runbook.
...
1. Run `powerbi/deploy.ps1`, which calls the PowerBI REST API via `az rest`.
```

---

### docs-mi-06. `README.md` "Operations" table describes the troubleshooting guide as "8 common failure scenarios" — the document has 9

**Location** — `README.md:223`

This finding is identical to `tut-mi-04` in the companion tutorial review and included here for completeness because it is an information-architecture consistency issue as well as a tutorial-accuracy issue. The root README is the authoritative entry point; a reader who reads "8 scenarios" there and then opens the troubleshooting doc (which says "9 most common failure modes" in its opening line) will notice the discrepancy immediately.

**Suggested fix**
Update `README.md:223` from "8 common failure scenarios" to "9 common failure scenarios".

---

## 5. Nit items

### docs-n-01. `docs/decisions/INDEX.md` outcome column for ADR-003 describes the mechanism but not the decision

**Location** — `docs/decisions/INDEX.md:9`

```markdown
| 003 | ... | ... | The Function App writes the caller's AAD `oid` into `SESSION_CONTEXT` on every connection check-out; RLS predicates join `dim_UserRoles` on that value; deny-by-default when unset. |
```

The one-line outcome is the review spec's key value-add for the INDEX — it should capture the *decision* ("use SESSION_CONTEXT rather than application-layer filtering"), not just the mechanism. The ADR-002 and ADR-004 entries correctly capture the decision ("Materialise `fact_DailyTraderPnL` via a per-day `MERGE`...", "The Function App `TimerTrigger_BacpacExport` is the single owner..."). ADR-003's entry describes implementation, not choice.

**Suggested fix**
Rephrase to lead with the decision:

```markdown
Use `SESSION_CONTEXT('aad_object_id')` as the RLS identity carrier (rather than application-layer row filtering): the Function App writes the caller's AAD `oid` on every connection check-out; predicates join `dim_UserRoles`; deny-by-default when unset.
```

---

### docs-n-02. Glossary §5 "safe_query" entry says "The third independent gate after Anthropic's own safety + AST re-serialisation" — but the gate ordering in the code places safe_query before the Anthropic call

**Location** — `docs/glossary.md:151`

```markdown
| **safe_query** | The TCP module ... that validates LLM-emitted SQL against an allowlist + deny-list before execution. The third independent gate after Anthropic's own safety + AST re-serialisation. |
```

`safe_query` runs on LLM-emitted SQL. The sequence is: (1) `safe_query` validates the SQL before execution, (2) Anthropic's own model safety filtering happens at the prompt/response level. Describing `safe_query` as "the third gate after Anthropic's safety" is accurate in the sense that Anthropic's model safety runs first (at generation time), then `safe_query` runs (at execution time), but the phrasing implies `safe_query` is a downstream check on Anthropic output, which is correct. The confusion is that `safe_query` is actually the *only* gate at the application layer — calling it "the third" without naming the first two may lead a reader to look for a first and second application-layer gate that do not exist. The first two gates are: (1) Anthropic's model-level safety at generation, (2) `sqlglot` AST re-serialisation (which is part of `safe_query` itself, not a separate gate). The phrase conflates all three.

**Suggested fix**
Simplify to: "The application-layer SQL safety module (`tcp/safe_query.py`): validates LLM-emitted SQL against an allowlist + deny-list and re-serialises via `sqlglot` AST before execution. The last line of defence after Anthropic's model-level safety filtering."

---

### docs-n-03. `README.md` Quickstart step 2 runs `tests/integration/test_telemetry_no_pii.py` as part of the no-live-Azure block — but `setup.md §A.4` correctly categorises this test as living under `tests/integration/` and having no live-env deps

**Location** — `README.md:40-42`

The README Quickstart block correctly notes the PII test has no live-env requirement. This matches `setup.md §A.4`. No correctness issue, but the README does not include the SQL schema tests (`tests/sql/`) in its quickstart even though those are also part of Track A. A reviewer who wants to verify the DB layer at a glance cannot do so from the README Quickstart alone — they must go to `docs/setup.md §A.4` for the full list.

**Suggested fix** (low priority)
Add a comment to the Quickstart noting that SQL schema tests require `sqlcmd` and a running container: `# SQL schema tests: see docs/setup.md §A.4`.

---

### docs-n-04. `docs/observability/README.md` "Open follow-ups" section references "Etapa 9 documentation pass" as a consumer of information — but the section has not been updated now that Etapa 9 is in progress

**Location** — `docs/observability/README.md:162-168`

```markdown
The two that the next stage (Etapa 9 documentation pass) should highlight:
- Multi-window burn-rate: ...
- Custom-metrics migration (RR-06): ...
```

This forward reference from Etapa-8 to Etapa-9 now reads as a self-reference since Etapa 9 is the current stage. The observability README is a component README that gained a "Component scope" header in Etapa 9, but the body text still refers to Etapa 9 in the future tense.

**Suggested fix**
Update the forward reference now that Etapa 9 is active: change "the next stage (Etapa 9 documentation pass) should highlight" to "Etapa 12 polish pass should address", since neither of the two items listed was resolved in Etapa 9 (they are deferred to Etapa-12 per the SLO doc §6).

---

## 6. Strengths

1. **Navigation graph is coherent.** Every component README opens with an identical one-line "Component scope" header that links to the root README and the glossary. A reader who arrives at any component-level document via a search result immediately knows the escape routes to the project entry point and the terminology reference. The consistency across six different READMEs (each written to different levels of depth) is rare and valuable.

2. **Glossary supersession is handled correctly.** The explicit statement "This page supersedes the per-document glossaries" with a bulleted list of the documents that now delegate here (lines 14-19), combined with the `01_business_requirements.md §11` note added in Etapa 9, creates a clean one-way hierarchy. There is no ambiguity about where the authoritative definition lives for any term in scope.

3. **ADR INDEX outcome-column quality.** Four of the five ADR outcome entries (ADR-001, ADR-002, ADR-004, ADR-005) correctly capture the decision rather than the context. ADR-005 in particular is an excellent one-liner: "Look up the caller's scope with a single parameterised SELECT TOP 1 on an admin-bypass connection; close immediately." That is precisely the information a reviewer needs to decide whether they need to read the full ADR.

4. **`docs/setup.md` "What gets deployed" cost table.** The per-resource cost-band table at the end of `setup.md` (lines 272-283) is an effective thesis-defence affordance. It translates the abstract "€0/month" claim into a resource-by-resource breakdown with free-tier justifications. An examiner who questions the cost model can verify each row independently without reading `docs/design/03_architecture.md §10` first.

5. **Troubleshooting template consistency.** All nine scenarios follow the Symptom → Diagnostic → Resolution → Reference structure without exception. The reference block in every scenario links to the exact document section (or ADR subsection) rather than to a top-level document, so readers can navigate directly to the source. The pattern of "if condition A → sub-resolution A; if condition B → sub-resolution B" within the Resolution block (used in scenarios 4, 5, 6, 8) is the correct troubleshooting tree structure for a document that must be self-sufficient without a human support chain.

6. **`swa/README.md` backend-contract documentation.** The JSON envelope schema (lines 112-128) and the `app.js#renderAnswer` dispatch table (lines 130-142) are the clearest API contract documentation in the project. The explicit mapping of each `status` value to its UI rendering path is load-bearing for anyone modifying the frontend or the backend independently, and it is the one place in the project where the contract is stated in full rather than inferred from code.

7. **`db/README.md` migration policy section.** The four-bullet migration policy (never edit a merged script, numbered forward only, idempotency contract, rollback scripts are advisory) is exactly the right level of rule-documentation for a single-developer thesis project. It is precise enough to prevent the most common migration mistakes (editing a merged script, running rollback in production) without over-specifying the toolchain.

---

## 7. Information graph verification

Spot-check of the critical navigation paths a reader hits in the first 30 minutes:

| Reader entry point | Path taken | Gap found? |
|---|---|---|
| Root README → Quickstart → `docs/setup.md` | Direct link; setup.md opens with Track A/B structure | None |
| Root README → Architecture overview → `docs/design/03_architecture.md` | Link exists at line 87 | None |
| Root README → Architecture decisions → ADR INDEX | Link at line 159; INDEX correctly summarises all 5 ADRs | None |
| Root README → Operations → `docs/troubleshooting.md` | Link at line 223; count says "8" (actual: 9) — **docs-mi-06** | Count mismatch |
| Root README → Documentation index → `docs/glossary.md` | Link at line 256 | None |
| `docs/troubleshooting.md` scenario 1 → ADR-001 §6.1 | Dead reference — ADR-001 has no §6.1 — **docs-CR-01** | Dead link |
| `docs/troubleshooting.md` diagnostic commands → shell variables | Variables undefined — **docs-CR-02** | Undefined variables |
| `docs/glossary.md` → term "TMDL" | Term not found — **docs-MA-05** | Missing term |
| `docs/glossary.md` → term "config_*" | Term not found — **docs-MA-04** | Missing entry |
| `powerbi/README.md` → "DAX measures" → root README | 48 vs 67 discrepancy — **docs-CR-03** | Count conflict |
| Component README → root README | One-line scope header present in all 6 components | None |
| Component README → glossary | Link in scope header present in all 6 components | None |
| `docs/setup.md §B.2` → OIDC role assignment | Role-assignment command uses app client ID — **docs-MA-03** | Incorrect CLI form |
| `docs/setup.md` → `docs/dev_setup.md` | Relative link `dev_setup.md` from `docs/setup.md` resolves to `docs/dev_setup.md` — correct | None |
| `docs/glossary.md` → `docs/design/01_business_requirements.md §11` | Supersession note added; §11 has back-reference note | None |

---

## 8. Recommendation

**CHANGES-REQUESTED.**

Block the convergence pass on:
- **docs-CR-01**: dead ADR-001 §6.1 reference (1-line fix in `troubleshooting.md:26`)
- **docs-CR-02**: undefined diagnostic variables (add a preamble block to `troubleshooting.md`)
- **docs-CR-03**: DAX measure count conflict (update `README.md:78` to "48 DAX measures")

The six Majors should land in the same convergence diff:
- **docs-MA-01**: remove `az portal` from diagnostic shortcuts (1 line)
- **docs-MA-02**: update TMDL file count to 20 in `README.md:118` (1 line)
- **docs-MA-03**: fix OIDC role-assignment command in `setup.md §B.2` (3 lines)
- **docs-MA-04**: add `config_*` entry to glossary §2 (2 lines)
- **docs-MA-05**: add TMDL and PBIR to glossary §3 + update Term-finding tip (5 lines)
- **docs-MA-06**: remove dead `scripts/apply_schema.py` reference from `function_app/README.md` (2 lines)

The six Minor items and four Nit items can defer to the Etapa-12 polish pass without blocking a thesis-defence reproduction.

The companion review (`review_etapa9_tutorial.md`) should be read alongside this report. That review covers the Track A/B learner journey (tut-CR-01: acceptance checklist unresolved variables, tut-CR-02: PowerShell dual-track) and several Majors that overlap in scope with the deploy walkthrough correctness findings above. The convergence diff should address all findings from both reviews in a single pass.

---

*End of `review_etapa9_docs.md`. See `review_etapa9_tutorial.md` for the companion tutorial-engineering pass covering the learner journey, PowerShell dual-track, and acceptance checklist usability.*
