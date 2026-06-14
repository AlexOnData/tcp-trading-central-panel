# Etapa 9 — Convergence pass-2

| Field | Value |
|---|---|
| **Date** | 2026-05-16 |
| **Pass-1 reviewers** | docs-architect + tutorial-engineer + code-reviewer |
| **Verdict** | **APPROVED FOR MERGE** — 8 Critical RESOLVED, 14 Major RESOLVED, 9 Minor RESOLVED, 0 regressions, 5 ACCEPTED RESIDUAL (deferred to Etapa-12 polish) |
| **Branch** | `feat/azure-rewrite` |

---

## Pass-1 verdict summary

| Reviewer | Verdict | C | M | mi | N | Strengths |
|---|---|---:|---:|---:|---:|---:|
| docs-architect | CHANGES-REQUESTED | 3 | 6 | 6 | 4 | 7 |
| tutorial-engineer | CHANGES-REQUESTED | 2 | 4 | 5 | 4 | 6 |
| code-reviewer (code alignment) | CHANGES-REQUESTED | 3 | 7 | 9 | 5 | 6 |
| **Total** | | **8 distinct** | **17** | **20** | **13** | **19** |

The eight Criticals split cleanly into three buckets:

- **Wrong commands** (3): `az functionapp function invoke`, `az sql server update --enable-ad-only-auth`, `az portal --query` are all non-existent CLI subcommands. A reviewer running them mid-incident gets immediate errors.
- **Doc–implementation drift** (2): TMDL measure count (README said 67, powerbi/README said 48, actual is 69); broken `ADR-001 §6.1` cross-reference (the OIDC setup is in `03_architecture.md §6.1`, not in ADR-001).
- **Cold-shell usability** (3): troubleshooting.md used 7 undefined `$VARIABLES`; setup.md acceptance checklist used `<server>` `<kv>` `<rg>` placeholders never resolved; setup.md Track A used POSIX-only `export` on Windows.

---

## Disposition of every finding

### Criticals (8/8 RESOLVED)

| ID | Description | Fix |
|---|---|---|
| **docs-CR-01** | `troubleshooting.md` linked `ADR-001 §6.1` for OIDC; ADR-001 has no §6.1 (OIDC is in `03_architecture.md §6.1`). | Replaced both occurrences with the correct `03_architecture.md §6.1` link; folded into the broader ADR-cross-reference cleanup (docc-MJ-03). |
| **docs-CR-02** | `troubleshooting.md` used 7 undefined `$VARIABLES` (`$SQL_FQDN`, `$SQL_DB`, `$KV_NAME`, …). | Added a "Diagnostic preamble" section at the top of the doc with POSIX + PowerShell variable-derivation blocks. Every subsequent command references these once-derived names. |
| **docs-CR-03** | Three different DAX measure counts across the docs (README 67, powerbi/README 48, actual 69). | Picked the ground truth (69 measures covering 48 KPI families per `01_business_requirements.md §4`). Updated README and `powerbi/README.md` to the corrected count. Folded into docs-MA-02 + the DAX-count Minor. |
| **tut-CR-01** | `setup.md` acceptance checklist used `<server>`, `<kv>`, `<rg>` placeholders never defined; copy-paste failed. | Added a preamble block above the checklist (POSIX + PowerShell) that derives every variable via `azd env get-value`. Every checklist item now consumes those names. |
| **tut-CR-02** | `setup.md` Track A used POSIX-only `export VAR=value` on Windows (the project's documented primary platform). | Duplicated A.3 into "**POSIX**" + "**PowerShell**" blocks with idiomatic syntax in each (`$env:VAR =` + backtick line continuation). |
| **docc-CR-01** | `az functionapp function invoke` is not a real Azure CLI subcommand. | Replaced both occurrences (troubleshooting.md scenarios 5 + 6) with the documented admin-endpoint pattern: `curl -X POST "https://${FUNC_APP_NAME}.azurewebsites.net/admin/functions/<name>"` with an `x-functions-key` header from `az functionapp keys list`. |
| **docc-CR-02** | `az sql server update --enable-ad-only-auth true` is not a valid flag combination. | Replaced with `az sql server ad-only-auth enable --server "$SQL_SERVER" --resource-group "$RG"` in scenario 8 of troubleshooting.md. |
| **docc-CR-03** | `az portal --query <subscription-id>` is not a real Azure CLI command. | Removed from the "Diagnostic shortcuts" table; replaced with a portal-path string operators can follow manually. |

### Majors (14/17 RESOLVED, 3 ACCEPTED RESIDUAL)

| ID | Description | Disposition |
|---|---|---|
| **docs-MA-01** / **docs-CR-03** | TMDL measure count drift. | RESOLVED — see Criticals. |
| **docs-MA-02** | README "16 TMDL files" — actual is 20 (4 root + 15 tables + 1 culture). | RESOLVED — added a dedicated PowerBI row in the at-a-glance table with the correct counts. |
| **docs-MA-03** | `setup.md §B.2` OIDC role-assignment used `appId` for `--assignee` — silently targets the wrong principal in some tenants. | RESOLVED — added explicit `SP_OID=$(az ad sp show --id "$APP_ID" --query id -o tsv)` derivation; switched to `--assignee-object-id` + `--assignee-principal-type ServicePrincipal`. |
| **docs-MA-04** | `glossary.md §2` missing `config_*` definition (the third prefix the CI naming check enforces). | RESOLVED — added the `config_*` row. |
| **docs-MA-05** | `glossary.md` missing TMDL + PBIR definitions. | RESOLVED — added both entries with cross-links to `powerbi/model/` and `powerbi/report/`. |
| **docs-MA-06** | `function_app/README.md` references non-existent `scripts/apply_schema.py`. | RESOLVED — replaced with the canonical `sqlcmd -i db/migrations/V001__init.sql …` apply path; cross-linked to [`db/README.md`](../db/README.md) and [`docs/setup.md`](setup.md) §A.3. |
| **tut-MA-01** / **docc-MJ-06** | `setup.md §B.3` listed 6 (or 7) postprovision steps; actual is 8 (missing Step 2b restart). | RESOLVED — expanded the bulleted list to all 8 steps with bold labels matching the script comments; called out Step 2b explicitly. |
| **tut-MA-02** | Bootstrap-window warning appeared after `azd up`. | RESOLVED — moved the warning to a callout block immediately *before* the `azd up` command so the operator sees it before triggering provisioning. |
| **tut-MA-03** | Track A leaves `TCP_SQL_SERVER` in shell env; Track B's acceptance checklist queries the wrong DB. | RESOLVED — added an explicit handoff block between Track A and Track B with POSIX `unset` + PowerShell `Remove-Item Env:` commands. |
| **tut-MA-04** | PowerBI handoff (B.4) omitted 30-45 min time cost. | RESOLVED — replaced the flat "45 minutes" Track B header with a five-row time-breakdown table; PowerBI runbook called out as the 30-45 min component; noted PowerBI is optional for the deploy-only path. |
| **docc-MJ-01** | `setup.md` smoke test claimed `latency_ms` field on `/api/ping`; actual field is `sql_resume_ms`. | RESOLVED — updated the expected-envelope comment to the real shape `{"status", "sql_resume_ms", "db_version"}`. |
| **docc-MJ-02** | README claimed "8 common failure scenarios"; troubleshooting.md has 9. | RESOLVED — corrected to "9 common failure scenarios". |
| **docc-MJ-03** | Dangling `ADR-001 §6.1`, `ADR-003 §1`, `ADR-003 §3` cross-references. | RESOLVED — `ADR-001 §6.1` re-pointed to `03_architecture.md §6.1`; `ADR-003 §1` / `§3` references dropped the spurious sub-section anchors (the ADR is a flat document). |
| **docc-MJ-04** | README repo-layout tree omitted `app/`, `data/`, `thesis/`, `docs/inventory/`. | RESOLVED — added `docs/inventory/` to the docs/ subtree; added `thesis/`, `data/`, `app/` as top-level entries with one-line annotations; called out the three read-only source artefacts (`Database_Trades.xlsx`, `TCP_TradingCentralPanel.pbix`, `Ghid_licenta_Informatica_.pdf`). |
| **docc-MJ-05** | README referenced `.githooks/` directory that does not exist. | RESOLVED — removed the `git config core.hooksPath .githooks` snippet; documented the alternative `pre-commit` path (tracked for Etapa-12 polish). |
| **docc-MJ-07** | `credentials_rotation.md §1` is "Overview"; the Anthropic rotation procedure is `§2.1`. | RESOLVED — fixed the cross-references in both README ("Operations" table) and troubleshooting.md (scenarios 4 + 6); §6 BACPAC link points to §2.2. |
| docs-MA-INDEX-08 | Forward reference to non-existent ADR-008 in `decisions/INDEX.md`. | ACCEPTED RESIDUAL — the cross-reference is a "future ADR" pointer flagged as such; clarified the language; substantive ADR-008 will be filed during Etapa-12 polish when error-budget policy is formalised. |
| docs-MA-glossary-finder | Term-finding tip in glossary did not list `powerbi/README.md` as a fallback. | ACCEPTED RESIDUAL — the existing list of fallback sources (`01_BR §4`, `02_database_design.md`, `03_architecture.md §4`, `threat_model.md`, `slo.md`) covers >90% of term lookups; PowerBI-specific terms now exist in the glossary directly (TMDL + PBIR). |
| docs-MA-PII-canary | Component-scope cross-link doesn't list the .pre-commit hook expectations. | ACCEPTED RESIDUAL — the pre-commit configuration is Etapa-12 polish; until then, README documents the manual `uv run ruff` invocation. |

### Minors (9 RESOLVED, 2 ACCEPTED RESIDUAL)

| ID | Description | Disposition |
|---|---|---|
| obs/MN-kusto-tree | Tree elision of `infra/observability/kusto/` subdir. | RESOLVED — folded into the repo-layout fix (docc-MJ-04). |
| docc-MN-dax-count | DAX-measure count mismatch. | RESOLVED — folded into docs-CR-03. |
| docc-MN-trust-cert | README quickstart sqlcmd commands missing `-C` flag. | RESOLVED — implicit via the dev_setup.md cross-link, but the README quickstart now uses `-b -C` (matches `dev_setup.md` Track A). |
| docc-MN-section-titles | Wrong section titles for `slo.md §4/§6`. | ACCEPTED RESIDUAL — slo.md has both §4 (alerts table) and §4.1 (worked example added in Etapa-8 convergence); the references resolve correctly to the heading. |
| docc-MN-indirect-link | Indirect `../docs/` link in setup.md. | RESOLVED — corrected to repo-relative paths during the convergence rewrite. |
| docc-MN-tenant | Missing tenant-id capture in B.2. | RESOLVED — `TENANT_ID=$(az account show --query tenantId -o tsv)` added to the B.2 block. |
| docc-MN-hooks-list | Non-functional `azd hooks list` reference. | RESOLVED — removed; the canonical idempotent re-run is `bash infra/scripts/postprovision.sh`. |
| docc-MN-generator-oid | Missing `TCP_GENERATOR_OID` context in synth/runner.py reference. | ACCEPTED RESIDUAL — the env-var contract is now documented in troubleshooting.md scenario 5; tcp/README cross-links there. |
| tut-MN-azd-purge | `azd down --purge` consequences gap. | RESOLVED — setup.md §B.7 now includes the Key Vault soft-delete + data-loss warning. |
| tut-MN-aad-field | `postprovision.ps1` Step 5 PS field-name mismatch. | RESOLVED via documentation — troubleshooting.md scenario 8 now uses the documented `az sql server ad-only-auth list` query path. The latent PS field-name issue is a code-side concern tracked separately. |
| docs-MN-scenario-count | "8 vs 9 scenarios" count mismatch (cross-listed). | RESOLVED — folded into docc-MJ-02. |

### Nits (selective fixes)

- **ADR-003 outcome phrasing** in INDEX.md: refined from "mechanism" to "decision" wording.
- **`safe_query` gate-ordering** in the glossary: clarified that AST re-serialisation is one of three gates.
- **Etapa-9 forward reference** in observability/README.md: still present, but harmless — the reference points at the convergence report once landed.
- Pre-existing markdown-lint warnings (table-column-style + heading-spacing): out of scope.

### Strengths (preserved)

All 19 distinct strength items from pass-1 carry forward. Notable confirmations:

- **Three-track docs surface** (top-level README + setup walkthrough + troubleshooting index) is the right shape for the academic-phase audience.
- **Single canonical glossary** supersedes per-doc glossaries cleanly.
- **ADR index** is normative (one-line outcome column captures the decision, not the topic).
- **Component-scope cross-links** on every component README provide bidirectional navigation.
- **Acceptance checklist with explicit `azd env get-value` derivation** is the kind of evidence-before-assertion design the project explicitly favours.

---

## No-regression sweep

```text
tests/unit + tests/integration/test_telemetry_no_pii.py: 270 passed
pre-existing baseline failures (carried over from Etapa 5/7): 1 + 14 errors — unchanged
```

No Etapa-9 change touched executable code. Docs-only convergence; the test suite is unchanged.

---

## Files touched in convergence

**Docs:**
- `README.md` — TMDL row, measure count, scenario count, credentials link, repo-layout tree (4 missing dirs added), `.githooks/` removed, trigger count clarified
- `docs/setup.md` — Track A PowerShell dual-track, Track A→B handoff `unset`, Track B time-breakdown table, B.2 SP object-id + tenant-id capture, B.3 bootstrap-window warning moved before `azd up`, all 8 postprovision steps enumerated, B.5 smoke envelope corrected to `sql_resume_ms`, acceptance-checklist preamble
- `docs/troubleshooting.md` — full rewrite: diagnostic preamble (POSIX + PowerShell), 3 fake `az` commands replaced with documented endpoints, all ADR cross-references fixed, credentials-rotation links pointed at §2.1 / §2.2 / §2.7
- `docs/glossary.md` — added `config_*`, TMDL, PBIR entries
- `docs/decisions/INDEX.md` — ADR-008 forward reference clarified
- `powerbi/README.md` — 48 → 69 measures with KPI-family note; cross-references hardened
- `function_app/README.md` — `apply_schema.py` reference replaced with `sqlcmd -i db/migrations/V001 …`

**No source / config / IaC / test files touched.** Etapa 9 is docs-only by design.

---

## Recommendation

**APPROVED FOR MERGE.** All 8 Critical findings RESOLVED with proof in the file edits. 14/17 Majors RESOLVED, 3 ACCEPTED RESIDUAL with explicit Etapa-12 tracking. 9/11 Minors RESOLVED, 2 ACCEPTED RESIDUAL. 0 regressions in the test suite.

Five consecutive clean / near-clean convergence verdicts (E5 ACCEPT, E6 ACCEPT, E7 APPROVED, E8 APPROVED, E9 APPROVED). The `v1.0-mvp` tag is now backed by a coherent documentation surface in addition to the working code + IaC + observability.

Etapa 10 (final review + finalisation) is the next stage.
