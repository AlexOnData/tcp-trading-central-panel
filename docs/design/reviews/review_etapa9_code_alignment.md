# Etapa 9 — Documentation ↔ code alignment review

| Field | Value |
|---|---|
| **Reviewer** | code-reviewer (Etapa 9 first pass, documentation-vs-implementation alignment) |
| **Date** | 2026-05-16 |
| **Branch** | `feat/azure-rewrite` |
| **Scope** | `README.md`, `docs/setup.md`, `docs/troubleshooting.md`, `docs/decisions/INDEX.md`, `docs/glossary.md`, six per-component READMEs (`tcp/`, `db/`, `function_app/`, `swa/`, `powerbi/`, `docs/observability/`), `docs/design/01_business_requirements.md` §11 superseded note. Cross-referenced against `infra/scripts/postprovision.{ps1,sh}`, `scripts/compute_migration_checksum.py`, `scripts/render_migration.py`, `db/migrations/V001__init.sql`, `function_app/triggers/{ask,ping,daily_generator,bacpac_export}.py`, `.github/workflows/{ci,cd}.yml`, `pyproject.toml`, `docker-compose.dev.yml`, `infra/main.bicep`, `infra/modules/alerts.bicep`, `infra/observability/`, `azure.yaml`, `powerbi/`. |
| **Working tree** | clean — last commit `2dc18aa feat(powerbi): Etapa 7 — TMDL semantic model + PBIR report + deploy automation`; Etapa 9 documentation is on top. |
| **Verdict** | **CHANGES-REQUESTED** — 3 Critical command-accuracy bugs (invalid Azure CLI subcommands that will fail at the terminal), 7 Major drift items (wrong field names, miscounts, missing/dangling cross-references), 9 Minor doc-quality nits. The narrative is otherwise accurate and the per-component component-scope banners are consistent. |

| Severity | Count |
|---|---:|
| Critical | 3 |
| Major | 7 |
| Minor | 9 |
| Nits | 5 |
| Strengths | 6 |

---

## 1. Summary

Etapa 9 is a documentation-only stage: no executable code changes, no schema changes, no infra changes. The deliverables are a top-level `README.md`, the two-track `docs/setup.md`, a 9-scenario `docs/troubleshooting.md`, the ADR index at `docs/decisions/INDEX.md`, the consolidated `docs/glossary.md`, six per-component README banners, and a "superseded" note on the Etapa-1 glossary. The job of this review is to verify that the docs say what the code actually does.

The high-order narrative is faithful: stack list, hierarchy counts (1/2/6/32), table counts (10 dim + 2 fact + 1 config), 9 KQL files, 8 alert rules, 9 workbook tiles, postprovision step ordering, the AAD-only flip semantics, the BACPAC schedule, the safe_query / RLS chain, and the file paths in the linked deep-dives are all accurate. The component-scope banners on the six READMEs are present and uniform, with working back-links to `../README.md` and `../docs/glossary.md`.

The defects fall into three buckets:

1. **Three Azure CLI invocations are not real commands.** `az functionapp function invoke` does not exist (two occurrences in troubleshooting.md). `az sql server update --enable-ad-only-auth true` is not a valid flag (the actual subcommand is `az sql server ad-only-auth enable`). `az portal --query <subscription-id>` is not a CLI command at all. An operator following the runbook hits a non-zero exit immediately.
2. **Field-name and count drift.** The `/api/ping` response field is `sql_resume_ms`, not `latency_ms` (setup.md says the wrong thing). The troubleshooting guide has 9 scenarios but the README and the troubleshooting preamble both say "8". Two cross-references to ADR sub-sections (`ADR-001 §6.1`, `ADR-003 §1` / `§3`) point at sections that do not exist. The repo-layout tree omits four real directories (`app/`, `data/`, `thesis/`, `docs/inventory/`).
3. **Path / naming clarifications worth tightening.** `.githooks/` is referenced as a real opt-in path but does not exist. The README's `infra/observability/` tree line elides the `kusto/` subdirectory. The `credentials_rotation.md §1` reference for "rotate the Anthropic key" is one section off (the procedure is §2.1; §1 is the Overview).

None of the defects is load-bearing for the build itself — the *code* is correct (Etapa 8 closed with 37 passing tests and a green smoke). The defects are doc bugs that the convergence pass should resolve before this artefact lands in the thesis chapter.

---

## 2. Critical findings (the command will not run)

### docc-CR-01 — `az functionapp function invoke` is not a real Azure CLI subcommand

**Files**

- `docs/troubleshooting.md:172` — "Re-run the trigger manually: `az functionapp function invoke --name "$FUNC_APP_NAME" -g "$RG" --function-name bacpac_export`."
- `docs/troubleshooting.md:277` — diagnostic-shortcuts table row: "Manually invoke a timer trigger | `az functionapp function invoke -n <func> -g <rg> --function-name daily_generator`".

**Problem**

The `az functionapp function` command group exposes exactly four subcommands: `delete`, `keys`, `list`, `show`. There is no `invoke`. An operator running either of the two snippets above gets `az functionapp function: 'invoke' is not in the 'az functionapp function' command group.` and a non-zero exit immediately — the very situation the troubleshooting doc was supposed to escape.

This is the highest-severity finding because the surrounding text frames both commands as the *resolution* path: scenario 6 ("BACPAC export missed Sunday") tells the reader to use it to force a re-run, and the diagnostic-shortcuts table presents it as the canonical "run this timer trigger now" recipe.

**Why-it-matters**

The BACPAC export and the daily generator are the two recurring runtime contracts that the doc most urgently needs a manual re-run path for (the alert that fires on missed BACPAC is the only severity-2 alert the operator will see all year). Sending the operator to a non-existent CLI in that exact moment defeats the purpose of the runbook.

**Suggested fix**

The supported "force a timer trigger now" recipe on Functions v2 / Y1 is a POST against the function's admin endpoint:

```bash
MASTER_KEY=$(az functionapp keys list -g "$RG" -n "$FUNC_APP_NAME" --query masterKey -o tsv)
curl -s -X POST "https://${FUNC_APP_NAME}.azurewebsites.net/admin/functions/bacpac_export" \
  -H "x-functions-key: ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"input":""}'
```

Both call sites should be replaced with this idiom (or with `func azure functionapp invoke`, which is the `func` CLI form — note `func`, not `az`).

---

### docc-CR-02 — `az sql server update --enable-ad-only-auth true` is not a valid flag combination

**Files**

- `docs/troubleshooting.md:237` — bottom of scenario 8 ("The bootstrap window slipped"): `az sql server update --name "$SQL_SERVER" -g "$RG" --enable-ad-only-auth true`.

**Problem**

`az sql server update` accepts neither `--enable-ad-only-auth` nor `--name`-on-its-own-with-an-AAD-only-flag. AAD-only authentication is managed by the dedicated subcommand group `az sql server ad-only-auth`, with verbs `enable`, `disable`, `get`, `list`. The postprovision script itself uses `az sql server ad-only-auth enable --resource-group "$RG" --server-name "$SQL_SERVER"` (`postprovision.sh:222`); the troubleshooting doc should mirror that exact invocation.

The defect is silent in the sense that the previous lines of scenario 8 already advised the operator to "Re-run the postprovision idempotently", which works. But the *fallback* step at line 237 — the one the doc presents as the manual rescue when even AAD-admin registration is intact — is the one that crashes.

**Why-it-matters**

Scenario 8 is the *only* runbook entry for an incomplete AAD-only flip, which is the single longest-tenure residual risk in the threat model (RR-08, bootstrap window). The fallback command being wrong negates the value of the entry.

**Suggested fix**

Replace the line with:

```bash
az sql server ad-only-auth enable -g "$RG" --server-name "$SQL_SERVER"
```

Or, more robustly, point the reader to re-run `infra/scripts/postprovision.{ps1,sh}` — the script is idempotent and is the only path that also handles the SWA-config substitution and the bootstrap-secret deletion in sequence (the manual line on its own leaves both undone).

---

### docc-CR-03 — `az portal --query <subscription-id>` is not a real Azure CLI command

**Files**

- `docs/troubleshooting.md:278` — diagnostic-shortcuts table row: "Open the workbook quickly | `az portal --query <subscription-id>` then navigate to *Monitor → Workbooks → TCP — Operations dashboard*".

**Problem**

There is no `az portal` command in Azure CLI 2.x. The intended UX — "open the Azure portal at this subscription" — is achieved with a deep-link URL, not a CLI command. The line will error out with `az: 'portal' is not in the 'az' command group`.

**Why-it-matters**

The diagnostic-shortcuts table is the page an operator will flip to first when something is broken — every command in it must be a real one-liner. A non-existent command at the very moment the operator is stressed and needs the shortcut undermines confidence in every other row.

**Suggested fix**

Replace the row with a portal-deep-link recipe:

```bash
SUB_ID=$(az account show --query id -o tsv)
echo "https://portal.azure.com/#@/blade/HubsExtension/BrowseResource/resourceType/Microsoft.Insights%2FworkBooks"
# Or, for a direct workbook deep-link once the resource id is known:
WB_ID=$(azd env get-value AZURE_WORKBOOK_ID 2>/dev/null || echo "")
[ -n "$WB_ID" ] && echo "https://portal.azure.com/#@/resource${WB_ID}/workbook"
```

`AZURE_WORKBOOK_ID` is not currently an output of `azd env get-values`; that part would require a Bicep-output addition. The first echo (the workbook *list* deep-link) works today.

---

## 3. Major findings (will mislead an operator but won't crash)

### docc-MJ-01 — `setup.md` reports the wrong field name in the `/api/ping` envelope

**Files**

- `docs/setup.md:216` — `# Expected: {"status": "warm"} or {"status": "resumed"}, latency_ms field present`.
- Ground truth: `function_app/triggers/ping.py:70-74` emits `{"status": status, "sql_resume_ms": duration_ms, "db_version": db_version}`.

**Problem**

The field is `sql_resume_ms`, not `latency_ms`. An operator scripting against the smoke output (e.g. `jq '.latency_ms'` from a CI assertion) gets `null` and assumes the endpoint is malformed when it is in fact returning correct data under a different key.

The doc-team-style log lines in the ping handler (`sql_resume_ms=duration_ms`) are also the same name in App Insights `traces.customDimensions`, so the field is consistent across response body + telemetry — the doc is the only outlier.

**Why-it-matters**

`setup.md §B.5` is the canonical smoke recipe for the Etapa-4-acceptance checklist. The smoke is the load-bearing artefact the user will demonstrate at the thesis defence; a copy-pasted snippet that asserts on the wrong field will fail the demo silently.

**Suggested fix**

Change line 216 to:

```bash
# Expected: {"status": "warm" | "resumed", "sql_resume_ms": <int>, "db_version": "Microsoft SQL Azure …"}
```

Optionally add a one-liner assertion: `curl -fsS "https://${FUNC_HOST}/api/ping" | jq -e '.status == "warm" or .status == "resumed"'`.

---

### docc-MJ-02 — `README.md` says "8 common failure scenarios" but `troubleshooting.md` has 9

**Files**

- `README.md:223` — "[`docs/troubleshooting.md`](docs/troubleshooting.md) — 8 common failure scenarios with diagnostic commands"
- `docs/troubleshooting.md:3` — "The 9 most common failure modes a TCP operator hits…"
- `docs/troubleshooting.md:282` — "The 9 scenarios above are the ones a single operator hits across the academic-build lifecycle."

**Problem**

The README under-counts by one. The troubleshooting doc self-references "9" twice and has numbered headings `## 1` through `## 9`. A reader cross-referencing the README's count against the actual file will doubt the README's numbers everywhere else (which are otherwise correct).

**Why-it-matters**

The README is the document examiners read first; a count mismatch in the operations table is a footnote, but it is a footnote in a table whose other rows (workbook tiles, SLI count) the reader is also relying on without verification. Tighter counts also help future maintainers stay disciplined as scenarios are added.

**Suggested fix**

Change `README.md:223` from "8 common failure scenarios" to "9 common failure scenarios", and add scenario 9 (PowerBI refresh) to the operations table if there is a natural slot. If the count is intended to drift as scenarios are added, soften to "Common failure scenarios …" and let the doc itself be authoritative.

---

### docc-MJ-03 — Cross-references to non-existent ADR sub-sections (`ADR-001 §6.1`, `ADR-003 §1`, `ADR-003 §3`)

**Files**

- `docs/troubleshooting.md:26` — "[ADR-001 §6.1](decisions/ADR-001-powerbi-deployment.md) for the OIDC setup pattern"
- `docs/troubleshooting.md:57` — "[ADR-003 §1](decisions/ADR-003-rls-session-context.md)"
- `docs/troubleshooting.md:146` — "[ADR-003 §3](decisions/ADR-003-rls-session-context.md)"

**Problem**

Two distinct defects:

1. **ADR-001 is about the PowerBI deployment strategy**, not OIDC; scenario 1's link to it for "OIDC setup pattern" is at the wrong document entirely. The OIDC setup pattern lives in `docs/design/03_architecture.md §6.1` (setup.md §B.1 correctly links there).
2. **ADR-003 and ADR-001 have no numbered sub-section headings** (`ADR-003` has the four standard ADR sections "Context / Decision / Consequences / Alternatives rejected" — none numbered). `§1` and `§3` do not exist.

**Why-it-matters**

A reader clicking through to track down "the admin-scope path used by `daily_generator`" lands at the top of ADR-003 with no breadcrumb to the specific paragraph. For the academic thesis chapter that quotes the troubleshooting doc, the dangling `§N` references suggest reviewer-fixable errata that look unprofessional.

**Suggested fix**

- Replace `ADR-001 §6.1` with `docs/design/03_architecture.md §6.1` (the actual home of the OIDC setup pattern).
- Drop the `§N` suffixes from the ADR-003 links (or replace with the section name, e.g. "ADR-003 § Decision"). Same for `ADR-005` if it picks up sub-sections later.

---

### docc-MJ-04 — The README repo-layout tree omits four directories that exist in the repo

**Files**

- `README.md:93-151` — the directory-tree fenced block.
- Real directories not listed: `app/`, `data/`, `thesis/`, `docs/inventory/` (each present at `D:/.../TCP_TradingCentralPanel/`).

**Problem**

The README presents the layout as a canonical map of the repo, but four real directories are absent:

1. **`app/`** — top-level directory (cause of presence TBD; possibly a leftover from a prior scaffolding step).
2. **`data/`** — top-level directory; likely the home of the `Database_Trades.xlsx` historical fixture.
3. **`thesis/`** — top-level directory with `main.aux`, `main.bbl`, `main.bcf`, `main.tex` and friends — the LaTeX academic-thesis scaffold (Etapa 13). The `pyproject.toml` `[tool.ruff].extend-exclude` and `[tool.mypy].exclude` lists already exclude `thesis`, so the directory is known to the build but unmentioned in the README map.
4. **`docs/inventory/source_artifacts.md`** — a real index of read-only source artefacts (the `.xlsx`, `.pbix`, `.pdf`); useful enough to deserve a line in the tree.

**Why-it-matters**

For a thesis examiner doing a code-walk, an undisclosed directory at the repo root looks like cruft or unfinished work — neither impression is desirable, especially `thesis/` which is the production target of the next stage. For a new contributor, "where do I put the LaTeX edit?" has no answer from the README.

**Suggested fix**

Add four entries to the tree:

```
├── app/                              — (TODO: clarify purpose or delete in Etapa 11 cleanup)
├── data/                             — Read-only source fixtures (Database_Trades.xlsx)
├── thesis/                           — LaTeX academic thesis sources (Etapa 13)
└── docs/
   ├── inventory/                     — Source-artefact index (xlsx / pbix / pdf provenance)
```

If `app/` is dead code, raise it as an Etapa-11 cleanup line item.

---

### docc-MJ-05 — `README.md` references `.githooks/` as an opt-in hooks path but the directory does not exist

**Files**

- `README.md:204-206` — "to enable it locally: `git config core.hooksPath .githooks`".

**Problem**

There is no `.githooks/` directory in the repo (verified by direct listing). Running the suggested command silently sets the hooks path to a non-existent directory, which is a no-op for git (git falls back to the empty hooks set, *not* to the default `.git/hooks/`). The reader thinks they have enabled the ruff hook locally; nothing happens.

The actual ruff auto-format hook is installed via `.claude/settings.json` `PostToolUse` for the Claude Code harness — i.e., it runs *only when Claude edits a file*, not on git operations. So the doc's claim ("the hook is opt-in for humans") is also misleading: there is no human-facing git hook at all today.

**Why-it-matters**

Contributors expecting `git commit` to run ruff get a silent miss; the first lint failure surfaces in CI (the `ci.yml` ruff job), which is exactly the friction the suggested setup was supposed to remove.

**Suggested fix**

Either:

- **Ship a real `.githooks/pre-commit`** (a small wrapper that runs `uv run ruff format --check` + `uv run ruff check`) and document the opt-in line. This is the path that actually delivers what the README promises.
- **Or remove the section entirely**, replacing it with a note that ruff runs in CI and that contributors are encouraged to run `uv run ruff format` before pushing.

---

### docc-MJ-06 — `setup.md` postprovision enumeration omits "Step 2b: restart Function App"

**Files**

- `docs/setup.md:185-192` — the "postprovision hooks … the eight-step idempotent bootstrap" enumeration.

**Problem**

The doc claims "eight-step idempotent bootstrap" and lists: Step 0, Step 1, Step 2, Step 2c, Step 3, Step 4, Step 5. That is **seven** bullets — the actual script (`postprovision.sh:178` / `postprovision.ps1:188`) has an explicit `Step 2b: Restart Function App` between Step 2 and Step 2c. Counting it would make the enumeration eight, matching the prose count.

The restart matters because it is what causes the new `TCP_GENERATOR_OID` app setting (set in Step 2) to be picked up by the running container *before* the AAD-only flip in Step 3. An operator stepping through the script manually who skips it (because the doc says it isn't there) sees the daily generator fail at the next 07:00 RO trigger.

**Why-it-matters**

The eight-step contract is the load-bearing guarantee of the bootstrap window's safety (Step 1 is RLS-disabled, Step 4 deletes the bootstrap secret, Step 5 verifies — the rest must run in order). A reader who maps Step 2 directly to Step 2c by following the doc misses the restart and may also misattribute a subsequent failure to a code defect rather than a sequencing issue.

**Suggested fix**

Insert the missing bullet between Step 2 and Step 2c:

```
   - Step 2b: Restart the Function App so the new TCP_GENERATOR_OID app
     setting is picked up by the running runtime before Step 3 flips
     SQL to AAD-only.
```

---

### docc-MJ-07 — `credentials_rotation.md §1` cross-reference is one section off

**Files**

- `README.md:221` — "How do I rotate the Anthropic key? | [`docs/security/credentials_rotation.md`](docs/security/credentials_rotation.md) §1"
- `docs/troubleshooting.md:110` — "follow [`docs/security/credentials_rotation.md`](security/credentials_rotation.md) §1"
- Ground truth: `credentials_rotation.md` §1 is "Overview"; the Anthropic procedure is `§2.1 ANTHROPIC_API_KEY` (verified at `credentials_rotation.md:22`).

**Problem**

Both cross-references point at the Overview section instead of the actual rotation procedure. A reader following the link lands on the architectural framing instead of the four-step rotation recipe.

**Why-it-matters**

Cred-rotation is one of the load-bearing operational drills the doc must support cleanly. Mis-targeting at the section level wastes the reader's first few seconds of an already-stressful operation and gives the impression that the rotation doc is incomplete.

**Suggested fix**

Change both references from `§1` to `§2.1` (the actual ANTHROPIC_API_KEY procedure). While in there, the `§6 BACPAC` and `§2 SQL admin export password` link in scenarios 6 and 9 should also be checked — they appear to be in the right section, but the prose says "§2" for "SQL admin export password rotation", which is actually the *section group* 2, not a specific procedure. Tighten to `§2.6` (the export-password procedure, if that's its anchor).

---

## 4. Minor findings (correct in spirit, slightly wrong in detail)

### docc-MN-01 — README `infra/observability/` tree elides the `kusto/` subdirectory

`README.md:127` reads `│   ├── observability/                   — workbook.json + 9 .kql files`. The `.kql` files actually live under `infra/observability/kusto/`, not directly in `infra/observability/`. The narrative in `docs/observability/README.md` and `docs/glossary.md` consistently uses `infra/observability/kusto/*.kql`. Tighten the README line to `observability/ (workbook.json + kusto/*.kql, 9 files)`.

### docc-MN-02 — README "DAX measure" count differs from `powerbi/README.md`

`README.md:24` claims `8 alert rules` (correct) and the architecture-overview table mentions `67 DAX measures`. `powerbi/README.md:9` says `48 DAX measures`. The TMDL inventory (`powerbi/model/tables/_Measures.tmdl`, 69 `measure` blocks) supports neither claim cleanly — the truth lies somewhere between, with some measures duplicated in the `ro-RO.tmdl` culture for localisation strings. Reconcile the two READMEs to the same canonical number and add a footnote that the culture file duplicates measure metadata for localisation (it is not "more measures").

### docc-MN-03 — README quickstart sqlcmd commands lack `-C` (TrustServerCertificate)

`README.md:44-48` runs `sqlcmd -S localhost,1433 -U sa -P '…' -Q "CREATE DATABASE tcp_dev;"` etc. without `-C`. sqlcmd 18+ enables strict certificate validation by default; against the self-signed cert in the dev container the connection is rejected with `SSL Provider: certificate chain was issued by an authority that is not trusted`. The fuller `setup.md` recipe correctly adds `-C` (`docs/setup.md:62-64, 87-92`). Either add `-C` to the README quickstart or wrap the four sqlcmd lines in a `sqlcmd 18+ users add -C` note.

### docc-MN-04 — `README.md` Quickstart references `uv.lock` indirectly but `uv.lock` is absent

`setup.md:42` says "The lockfile is `uv.lock`" but `uv.lock` is not in the repo root (verified). `uv sync` will create the lockfile on first run, so the doc is *forward-correct*; but a reader stepping through expecting to inspect a checked-in lock will not find it. Either commit the lockfile (recommended for reproducibility — uv lock files are designed to be committed) or change the line to "The lockfile is `uv.lock`, generated by `uv sync` on first run."

### docc-MN-05 — Wrong `§` numbers for the `slo.md` references in troubleshooting.md

`docs/troubleshooting.md:115` references `docs/observability/slo.md §4 — SLI-1 availability burn` but `slo.md` §4 is titled "Burn-rate alerts" (no SLI-1 label in the heading; SLI-1 is defined at §2). Similar drift at `troubleshooting.md:285` which references `[SLO doc](observability/slo.md) §6 known issues` — `slo.md` §6 is titled "Open questions and future work", not "known issues". Both are close enough to be defensible but the section title should match the actual heading.

### docc-MN-06 — `docs/setup.md` `[ADR-related notes]` link is structurally valid but unnecessarily indirect

`docs/setup.md:67` links to `../docs/security/threat_model.md`. Since `setup.md` is itself in `docs/`, the canonical relative path is `security/threat_model.md`; the `../docs/` form resolves *up* to the repo root and then *into* `docs/security/`, which works but invites a future relocation of `setup.md` to break silently. The same file uses the clean form at line 119 (`design/03_architecture.md`). Normalise.

### docc-MN-07 — `setup.md §B.2` "Owner at subscription scope" recipe lacks tenant-id capture

`docs/setup.md:153` says "Store `APP_ID`, `SUB_ID`, and the tenant id in the GitHub repository's Actions variables…" but the recipe above only captures `APP_ID` and `SUB_ID`. Add `TENANT_ID=$(az account show --query tenantId -o tsv)` for completeness. A reader who paste-runs the block ends up with an empty `AZURE_TENANT_ID` variable in GH Actions and the next step (azd auth) fails non-obviously.

### docc-MN-08 — `troubleshooting.md` scenario 8 `grep -A3 "Step 3" <(azd hooks list)` is non-functional

`docs/troubleshooting.md:212` says "Was Step 3 of postprovision reached? `grep -A3 "Step 3" <(azd hooks list)`". `azd hooks list` does not exist as an azd subcommand — `azd hooks` is a `run`-only command group in the current azd CLI. The intended check is to inspect the postprovision script's output from the last `azd up` run, which is in `~/.azd/<env>/logs/` or simply re-displayed by stdout during the run. The line is misleading.

Suggested replacement:

```bash
# Tail the last postprovision run (azd writes hook output verbatim to stdout)
azd env get-values | grep "AZURE_FUNCTION_APP_PRINCIPAL_ID"
# If empty, postprovision never set the value → Step 3 likely never reached.
```

### docc-MN-09 — `tcp/synth/runner.py` reference in troubleshooting is unguarded

`docs/troubleshooting.md:147` references `[tcp/synth/runner.py](../tcp/synth/runner.py)` as "the runner that the timer calls". True (verified `run_daily` at `tcp/synth/runner.py:229`). But the doc doesn't surface that the env var feeding the admin-scope handshake is `TCP_GENERATOR_OID` (set by postprovision Step 2). Add a short parenthetical: "(the timer reads `TCP_GENERATOR_OID` to set the ADR-003 admin session)".

---

## 5. Nits (style / consistency)

### docc-N-01 — `troubleshooting.md` mixes `az functionapp config appsettings list` and `az webapp config appsettings list`

`troubleshooting.md:101` uses `az webapp …` while `:136` uses `az functionapp …` for the same operation. Both work today, but `az functionapp` is the preferred / future-stable form for Functions resources. Normalise on `az functionapp config appsettings list`.

### docc-N-02 — `INDEX.md` row for ADR-004 spells out the NCRONTAB expression in-line; the others summarise

The ADR-004 outcome cell contains "NCRONTAB `0 0 8 * * 0` (Sunday 08:00 Europe/Bucharest)" — a useful detail but a tonal break from the other rows, which summarise the *rule* (verbs first, no schedule literals). Move the cron literal into the ADR body and leave the outcome cell at "the Function App is the single owner of the weekly BACPAC export on Sunday 08:00 RO".

### docc-N-03 — `glossary.md` defines `Capital Utilisation Ratio` once and once as `Capital Utilization Ratio` (US vs UK spelling)

`docs/glossary.md:28` uses `Capital Utilisation Ratio` (UK), `docs/design/01_business_requirements.md:616` uses `Capital Utilization Ratio` (US). The glossary is the source-of-truth post-Etapa-9; either roll the design doc forward to UK spelling or accept the variance with a `(US spelling: utilization)` note in the glossary entry.

### docc-N-04 — `glossary.md` group numbering is 1..6 in headings but the TOC at the top labels group 6 as "Project conventions" with no link to its own anchor

`docs/glossary.md:10` reads "6. [Project conventions](#6-project-conventions)" — the anchor is `#6-project-conventions`, but the heading at `:158` is `## 6. Project conventions` (anchor would be `#6-project-conventions` — actually the same). Verified. Nit because the rendering may vary by markdown engine; the link works on GitHub but not on every static-site generator. If the doc is ever migrated to a generator that uses different anchor mangling (mkdocs strips the `.` etc.), it will need explicit `id=…` anchors.

### docc-N-05 — Per-component README banners use slightly different phrasing for the back-link

All six READMEs open with a `> **Component scope.** This README documents the X directory only. For project-wide context …`, but the second sentence varies: `tcp/README.md` and `function_app/README.md` say "For terminology, see the [glossary]"; `powerbi/README.md` says "For terminology, see the [glossary]" then adds the powerbi runbook back-link. The variance is minor but a uniform two-sentence banner (link to top-level README + link to glossary) would scan as more polished.

---

## 6. Strengths (worth preserving)

### docc-ST-01 — Track A / Track B split in `setup.md` is the right primary axis

`setup.md` is correctly partitioned into a no-Azure local-development happy path and a full Azure-deploy path. The split matches how reviewers actually work (most reviewers will reproduce Track A; a small subset will run Track B). The fact that the doc explicitly says "Stop here if you only need a local reproduction" (`:107`) is exactly the right kind of off-ramp.

### docc-ST-02 — Acceptance checklist is testable

The §"Acceptance checklist" (`setup.md:251`) lists nine acceptance items, each with a one-line `az`-or-`sqlcmd` check. Once docc-CR-02 is fixed, the entire checklist is runnable as a smoke script — that is a strong contract for the Etapa-4 acceptance gate.

### docc-ST-03 — ADR index correctly states the actual *rule*, not the *topic*

Every row of `decisions/INDEX.md` describes the decision in normative language ("Use the PowerBI REST API via `az rest` …", "Materialise `fact_DailyTraderPnL` …", "The Function App writes the caller's AAD `oid` into `SESSION_CONTEXT` …"). No row reads "ADR-N discusses X" or "ADR-N decides on Y". This is the strongest pattern in the doc set and should be the model for future ADR indexes.

### docc-ST-04 — Glossary group structure is genuinely useful

The five-group split (Trading & KPI / Database / Azure / Observability / Security + Project conventions) lets a reader skip to the section their question lives in. The closing "Term-finding tip" (`:174`) with five fallback locations is a thoughtful affordance for terms not yet promoted to the glossary.

### docc-ST-05 — Per-component banners are present and consistent

All six READMEs (`tcp/`, `db/`, `function_app/`, `swa/`, `powerbi/`, `docs/observability/`) carry the same `> **Component scope.**` banner with working back-links to the top-level README and the glossary. The phrasing is uniform enough to read as a deliberate pattern.

### docc-ST-06 — File-existence claims are accurate in the load-bearing places

Every file-system path I spot-checked in the new docs (the kql files in `infra/observability/kusto/`, the Bicep modules in `infra/modules/`, `infra/scripts/postprovision.{ps1,sh}`, `scripts/compute_migration_checksum.py`, `scripts/render_migration.py`, `function_app/triggers/{ask,ping,daily_generator,bacpac_export}.py`, `tcp/safe_query.py`, `tcp/ai/anthropic_client.py`, `tcp/synth/runner.py`, `tcp/db.py`, `docs/runbooks/powerbi_deploy.md`, `docs/security/{bootstrap_window,credentials_rotation,incident_response,threat_model}.md`, `docs/observability/{README,slo}.md`, `docs/design/{01,02,03,ai_prompt_cache_contents}.md`, `tests/integration/test_telemetry_no_pii.py`) exists and matches the doc's framing. The drift is concentrated in *what the file does at a sub-section level* (docc-MJ-03, docc-MJ-07, docc-MN-05), not in *whether the file exists* — the latter is the harder of the two contracts to keep right, and Etapa 9 nailed it.

---

## 7. Convergence-pass shopping list (suggested ordering)

For the convergence pass, the cheapest path to a green doc set is:

1. Fix the three Critical command bugs (docc-CR-01, docc-CR-02, docc-CR-03) — one-line edits each.
2. Fix the ping field name (docc-MJ-01) — one-line edit.
3. Reconcile the 8-vs-9 scenario count (docc-MJ-02) — one-line edit + verify the operations table.
4. Re-target the §-numbered ADR / credentials_rotation cross-references (docc-MJ-03, docc-MJ-07) — three line edits.
5. Add the four missing directories to the repo-layout tree (docc-MJ-04) — three line additions + a `(TODO clarify)` if `app/` is genuinely cruft.
6. Decide whether `.githooks/` is real (ship the hook) or remove the line (docc-MJ-05).
7. Insert the missing Step 2b enumeration (docc-MJ-06) — one line.
8. Sweep the Minors in one pass; the Nits can land in an Etapa-12 polish commit.

No tests need to change — Etapa 9 is documentation-only and the live smoke suite already validates the behaviours the docs describe. The convergence pass can verify each fix by re-reading the affected section against the ground-truth file cited in the finding.

---

## 8. Out-of-scope confirmations

The following items were checked and are *not* defects, despite looking suspicious at first read:

- `azd env new tcp-prod --location westeurope --no-prompt` (setup.md:161): valid — matches `.github/workflows/cd.yml:50-52` exactly.
- `azd env get-value <KEY>` (singular): valid as of azd 1.6+; the CD pipeline also uses it (`cd.yml:56`).
- `azd env set ANTHROPIC_API_KEY` / `azd env set NOTIFICATION_EMAILS '…'`: valid — azd maps SCREAMING_SNAKE env vars to camelCase Bicep params automatically; both `anthropicApiKey` and `notificationEmails` are real Bicep params (verified at `infra/main.bicep:59` and `:83`).
- `infra/scripts/postprovision.{ps1,sh}` "eight-step idempotent bootstrap" count: correct (Steps 0, 1, 2, 2b, 2c, 3, 4, 5 — eight total stops), even though setup.md's enumeration is missing Step 2b (docc-MJ-06).
- Schema-history table shape (`script_name`, `applied_at_utc`, `checksum`): correctly stated in setup.md §B.5 and the acceptance checklist.
- The `principal_not_registered`, `anthropic_unavailable`, `execution_failed` error codes referenced in `troubleshooting.md`: all emitted verbatim by `function_app/triggers/ask.py`.
- The `AllowAllAzureServices` firewall rule referenced in scenario 9 of troubleshooting.md: real, defined at `infra/modules/sql.bicep:88-95`.
- The 10 dim + 2 fact + 1 config counts in the README: matches the `CREATE TABLE dbo.(dim|fact|config)_*` headings in `V001__init.sql`.
- The 9 KQL files / 8 alert rules / 9 workbook tiles counts: all match the actual files / resources.

These are the load-bearing claims an examiner is most likely to spot-check; getting them right is the headline accomplishment of Etapa 9 and the reason the verdict is **CHANGES-REQUESTED**, not **REJECTED**.
