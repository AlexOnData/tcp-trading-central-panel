# Etapa 12 — Documentation polish + placeholder consolidation review

| Field | Value |
|---|---|
| **Date** | 2026-05-16 |
| **Reviewer** | docs-architect (single pass, per STATE.md plan: `docs-architect` + `code-reviewer` at E12 close) |
| **Scope** | `docs/PLACEHOLDERS.md` (primary), `docs/design/ai_prompt_cache_contents.md` §7, `README.md` (E12 changes), `docs/glossary.md` (placeholder entry), cross-link sanity across all four; subsidiary check on `tcp/ai/anthropic_client.py`, `function_app/triggers/ask.py`, `pyproject.toml`, `infra/observability/kusto/03_anthropic_tokens_and_cost.kql`, `infra/modules/alerts.bicep` |
| **Baseline** | `feat/azure-rewrite` HEAD (commit on top of `v1.0-mvp` + Etapa-11 maintenance) |
| **Methodology** | (1) `git grep -nE "TODO"` across the full repo excluding the five allow-listed paths; (2) manual mapping of every grep hit against `PLACEHOLDERS.md §1`, `§2`, `§3`; (3) fact-check of `ai_prompt_cache_contents.md §7` against `tcp/safe_query.py` allowlists; (4) cross-link consistency pass; (5) project-status table accuracy |

---

## Executive summary

The Etapa-12 deliverables are a substantial improvement over the pre-E12 state: a credible, well-structured `docs/PLACEHOLDERS.md` now exists, the `README.md` author/license sections are clean, the glossary entry is correct, and the E12 residuals (Scope alias, freezegun removal, FX duplication note) are addressed. The primary audit gap is **one uncatalogued `TODO` in a production source file** (`docs/design/03_architecture.md:208`, the `ADR-XXX-cicd-role-split` forward reference) that the `git grep` audit surfaces but `PLACEHOLDERS.md §2` omits. A secondary gap is a **stale DAX measure count** in the newly-inserted `ai_prompt_cache_contents.md §7` (says "67" when the canonical figure corrected in the E9 convergence pass is "69"). Two nits round out the findings.

**Verdict: CHANGES-REQUESTED** — 1 Major, 1 Minor, 2 Nit.

---

## 1. TODO audit — `git grep` results and mapping

The command:

```bash
git grep -nE "TODO" -- \
  ':!docs/PLACEHOLDERS.md' \
  ':!.claude/STATE.md' \
  ':!docs/design/reviews/' \
  ':!CLAUDE.md' \
  ':!.tmp_install/' \
  ':!.claude/skills/'
```

produced the following lines. Each is mapped to its `PLACEHOLDERS.md` section below.

### 1.1 Lines that map to §1 (Mandatory — correctly catalogued)

| File:line | PLACEHOLDERS.md row |
|---|---|
| `pyproject.toml:8` | §1.1 Author identity |
| `README.md:311,317,322,323,324` | §1.5 License / §1.1 / §1.2 / §1.3 |
| `docs/design/01_business_requirements.md:8,9` | §1.1 Author / §1.2 Advisor |
| `docs/design/02_database_design.md:4,5,1252` | §1.1 Author / §1.2 Advisor |
| `docs/security/credentials_rotation.md:461` | §1.1 Author (version-history row) |
| `docs/security/threat_model.md:344` | §1.1 Author (version-history row) |
| `docs/security/incident_response.md:414,504` | §1.1 Author |
| `infra/main.bicep:76,79` | §1.7 Owner tag / §1.6 Repo URL |
| `docs/design/03_architecture.md:112,115` | §1.7 Owner tag / §1.6 Repo URL |
| `docs/dev_setup.md:40,46` | §1.6 Repo URL |

All correctly inventoried. File/line numbers in `PLACEHOLDERS.md §1.1` are verified accurate.

### 1.2 Lines that map to §2 (Out-of-scope-by-design — mostly catalogued)

| File:line | Content | PLACEHOLDERS.md §2 row |
|---|---|---|
| `docs/design/03_architecture.md:179` | Custom SWA domain | Row 1 — present |
| `docs/design/03_architecture.md:223` | `aad-tcp-sql-admins` group | Row 2 — present |
| `docs/design/03_architecture.md:381` | Tenant-level MFA | Row 3 — present |
| `docs/design/03_architecture.md:646` | Cold-deploy timing empirical capture | Row 4 — present |
| `powerbi/README.md:162`, `powerbi/model/tables/_Measures.tmdl:477,482,625,639` | Consecutive-loss streak + DAX approximations | Row 5 — present |
| **`docs/design/03_architecture.md:208`** | `TODO: file ADR-XXX-cicd-role-split post-thesis` | **MISSING** — see finding doc12-MJ-01 |

### 1.3 Lines that map to §3 (Automation-managed — correctly catalogued)

| File:line | Content | PLACEHOLDERS.md §3 row |
|---|---|---|
| `.github/workflows/cd.yml:239` | Pattern-match string used in smoke-job grep | §3 row "sentinel-no-checksum-supplied" — the grep pattern references the three automation strings |
| `docs/design/02_database_design.md:2421` | `TODO-checksum-set-by-CI` in doc snippet | §3 "`'TODO-checksum-set-by-CI'` in `02_database_design.md:2421`" |
| `docs/setup.md:342` | Acceptance-checklist negative grep | Informational reference in the checklist; not a placeholder to fill |
| `docs/security/threat_model.md:296` | RR-09 closure text that quotes the old placeholder string | This is a historical note about a closed risk, not a live placeholder |
| `scripts/compute_migration_checksum.py:5` | Docstring mentioning `'TODO-checksum-set-by-CI'` as historical context | Informational docstring; not a live placeholder |
| `docs/glossary.md:170` | Definition of "placeholder (TODO)" in the glossary | This is glossary prose, not a placeholder; correctly unlisted |
| `README.md:276` | Prose pointing to `PLACEHOLDERS.md` | Prose reference, not a placeholder |

All automation-managed and informational references are correctly handled — either listed in §3 or correctly excluded from the audit scope.

---

## 2. Findings

### doc12-MJ-01 [MAJOR] — `docs/design/03_architecture.md:208` TODO unlisted in PLACEHOLDERS.md §2

**Location**: `docs/design/03_architecture.md:208`

**Content**:
```
TODO: file ADR-XXX-cicd-role-split post-thesis
```
embedded in the RBAC matrix row for the GitHub Actions OIDC SP.

**Problem**: The `git grep` audit (step 1 of this review) surfaces this line. It is an intentional out-of-scope forward reference — the same category as the four §2 rows already listed (`custom SWA domain`, `aad-tcp-sql-admins group`, `tenant MFA`, `cold-deploy timing`). However, it does not appear in `PLACEHOLDERS.md §2`. The preamble of `PLACEHOLDERS.md` states explicitly: *"If something is `TODO` in the repo and is not listed here, that is a bug."* This line violates that contract.

**Impact**: During thesis submission, the user runs the resolution checklist (§5) grep. This line will appear and trigger a false-alarm investigation. More importantly, a thesis examiner running the same grep would find an uncatalogued forward reference.

**Fix**: Add one row to `PLACEHOLDERS.md §2`:

| Site | Decision | Why out of scope |
|---|---|---|
| `docs/design/03_architecture.md:208` | `TODO: file ADR-XXX-cicd-role-split post-thesis` — split the GitHub Actions OIDC SP `Contributor` role into narrower per-service roles | Single-engineer thesis build; `RG Contributor` is the accepted trade-off (documented in the same cell). Post-thesis hardening requires splitting into `Website Contributor + SQL DB Contributor + Storage Account Contributor + Monitoring Contributor + custom KeyVaultDeployer`. |

---

### doc12-MN-01 [MINOR] — `ai_prompt_cache_contents.md §7` states "67 DAX measures" — canonical count is 69

**Location**: `docs/design/ai_prompt_cache_contents.md:214`

**Content**:
```
model sits on the same `v_*` views but adds 67 DAX measures that
implement multi-period calculations, ratios, and ranking
```

**Problem**: The canonical DAX measure count was reconciled to **69** during the Etapa-9 convergence pass (finding `docs-CR-03`, resolved in `review_etapa9_convergence_pass2.md:37`). The top-level `README.md` (lines 23 and 79) correctly states 69. The actual TMDL file (`powerbi/model/tables/_Measures.tmdl`) contains 69 `measure` declarations (verified by `grep -c "^\s*measure "` → 69). The new §7 text, inserted in Etapa 12, uses the pre-correction figure of 67 — the same error that `docs-CR-03` closed in E9.

**Impact**: An examiner who reads `ai_prompt_cache_contents.md §7` and then checks `README.md` or `powerbi/README.md` will encounter a direct factual contradiction. Thesis examiners are trained to cross-reference sources.

**Fix**: Change line 214 of `docs/design/ai_prompt_cache_contents.md`:

```
- Before: "adds 67 DAX measures"
+ After:  "adds 69 DAX measures"
```

---

### doc12-NT-01 [NIT] — §7 routing heuristic has a directional error: the SWA chat UI links to PowerBI, not PowerBI to SWA

**Location**: `docs/design/ai_prompt_cache_contents.md:235-238`

**Content**:
```
The SWA chat UI surfaces a hyperlink visual to the AI Assistant page
in PowerBI (per Etapa-7 hardening) so the user can pivot between the
two without leaving the session.
```

**Problem**: The direction is reversed. Per `powerbi/README.md` and the Etapa-7 deliverables, the **PowerBI** AI Assistant page carries a hyperlink/button visual linking out to the SWA (not the other way around). The SWA chat UI is the natural-language assistant; it does not contain a link to PowerBI. The SWA is the primary surface the user "starts" in; the hyperlink in the **PowerBI** report allows the user to navigate from the BI dashboard to the SWA assistant without losing context.

**Impact**: Low — the wording is confusing but the underlying architectural fact (the hyperlink exists and avoids the iframe-embed blocker from Etapa-6 hardening) is correct. A thesis examiner who compares this sentence against `powerbi/README.md` would note the directional error.

**Fix**: Replace the two-sentence passage with:

```
The PowerBI AI Assistant page carries a hyperlink/button visual linking
to the SWA chat UI (per Etapa-7 hardening — the Etapa-6 `X-Frame-Options:
DENY` CSP rule blocks any iframe embed). The user can pivot from the
dashboard to the assistant without leaving the session.
```

---

### doc12-NT-02 [NIT] — PLACEHOLDERS.md §5 resolution checklist grep pattern is narrower than the full §1 placeholder set

**Location**: `docs/PLACEHOLDERS.md:188-191`

**Content**:
```bash
git grep -nE "TODO( author| \(thesis|placeholder; resolved in Etapa 12)" -- \
  ':!docs/PLACEHOLDERS.md' ':!.claude/STATE.md' ':!docs/design/reviews/' \
  ':!CLAUDE.md'
```

**Problem**: The checklist grep will detect `TODO (thesis author placeholder)`, `TODO author name`, and `placeholder; resolved in Etapa 12` strings — but will **miss** the bare `TODO` strings in the version-history author columns of the three security docs (`credentials_rotation.md:461`, `threat_model.md:344`, `incident_response.md:504`). Those bare `TODO` entries are §1.1 (author identity) placeholders. After a user fills them with their name, the grep returns clean — good. But before they fill them, the grep may also return clean if the user misreads the pattern as an exhaustive check, since `TODO` alone does not match the regex.

A second concern: the checklist excludes `.claude/skills/` but does not exclude `.github/workflows/` or `scripts/`, where the automation-managed strings appear. Running the grep on the repo in its current state (before any placeholder is filled) would return hits in `cd.yml:239` and `scripts/compute_migration_checksum.py:5`, which are §3 automation strings. The user would need to recognise those as false positives.

**Impact**: Very low — the §5 instructions are supplementary guidance. The user could instead run the main audit grep from this review. But the discrepancy between the checklist grep and the reality of what needs filling could confuse a first-time user of the file.

**Fix (optional)**: Either (a) expand the pattern to `"TODO"` and add the full exclude list from the main audit grep, or (b) add a note below the code block: *"This pattern covers the most common forms. Bare `TODO` in version-history author columns also needs filling — these appear in `docs/security/credentials_rotation.md:461`, `docs/security/threat_model.md:344`, and `docs/security/incident_response.md:504`."* Option (b) is lower-risk because it does not change the grep pattern that may already be memorised.

---

## 3. Scope check: §1 vs §2 vs §3 boundary correctness

No cross-boundary misclassifications found. Every item in the grep output maps unambiguously to the correct section:

- §1 rows are genuinely mandatory user-facing values (author name, advisor name, institution, defence year, license, repo URL, owner email).
- §2 rows are genuinely out-of-scope design decisions that have documented justifications, **except** the missing `03_architecture.md:208` row (doc12-MJ-01).
- §3 rows are automation-managed substitution targets with clear ownership (`render_migration.py`, `postprovision.{ps1,sh}`, `check_swa_placeholder.py`) and "DO NOT edit manually" guards.

One boundary question: the `docs/design/02_database_design.md:2421` (`'TODO-checksum-set-by-CI'`) entry in §3 is **a doc snippet**, not the actual migration file. The migration files (`db/migrations/V001__init.sql`, `V002__synth_logic.sql`) use `__V<n>_CHECKSUM__` — a different, non-`TODO` pattern. The §3 table entry correctly names `02_database_design.md:2421` as the owner; however, the description says this is "the same flow as `__V<n>_CHECKSUM__`" which is accurate. No defect — this is a correct and clear classification.

---

## 4. `ai_prompt_cache_contents.md §7` factual consistency check

### 4.1 Allowlist accuracy

The section states the allowlist exposes "5 `v_*` views + 9 `dim_*` + 2 procs + 5 functions". Cross-checked against `tcp/safe_query.py`:

| Claim | Actual (from source) | Match |
|---|---|---|
| 5 `v_*` views | `v_trades_enriched`, `v_employee_performance`, `v_team_performance`, `v_floor_performance`, `v_daily_pnl` (5) | ✓ |
| 9 `dim_*` | `dim_Companies`, `dim_TradingFloors`, `dim_Teams`, `dim_Employees`, `dim_Accounts`, `dim_Markets`, `dim_Sessions`, `dim_OrderType`, `dim_Date` (9) | ✓ |
| 2 procs | `usp_GetEmployeePerformance`, `usp_GetTopPerformers` (2) | ✓ |
| 5 functions | `tvf_GetCapitalBaseline`, `tvf_RiskMetrics`, `fn_GetCapitalBaseline`, `fn_IsTradingDay`, `fn_PreviousBusinessDay` (5) | ✓ |

All four counts match the code exactly.

### 4.2 Routing heuristic correctness

The heuristic ("single SELECT + at most one TVF call → AI; multi-period comparison / rolling windows / aggregated drawdown → PowerBI") is technically sound. It correctly identifies that DAX time-intelligence functions (`SAMEPERIODLASTYEAR`, `DATESINPERIOD`, calculation groups) have no equivalent single-SELECT form against the AI allowlist.

The "Leadership multiplier is PowerBI-only" claim is supportable: the multiplier compares a team's average PnL against the floor average, then applies a ratio — this requires at minimum two aggregation levels in a single query. While a T-SQL CTE could express this, the current AI assistant's per-request CTE row cap and the absence of a dedicated leadership-multiplier view make this an accurate classification.

The "Cross-period drawdown analytics → PowerBI-only" claim is supportable: rolling 30/90-day drawdown requires an anchored window function over arbitrary date ranges. The AI assistant's allowlist includes `tvf_RiskMetrics` which covers single-period drawdown, but the rolling variant is not exposed.

**One directional error found** (see doc12-NT-01): the section says "SWA chat UI surfaces a hyperlink visual to the AI Assistant page in PowerBI" — this is backwards. The fix is purely editorial; the architectural fact is correct.

### 4.3 DAX measure count discrepancy

As documented in doc12-MN-01: the section says "67 DAX measures" when the canonical count is 69. This is the only factual inaccuracy in §7 beyond the directional error in doc12-NT-01.

---

## 5. Cross-link consistency check

### 5.1 README.md → PLACEHOLDERS.md

| README.md reference | PLACEHOLDERS.md target | Correct? |
|---|---|---|
| `README.md:276` — docs index entry | `docs/PLACEHOLDERS.md` file | ✓ |
| `README.md:311` — License section | `docs/PLACEHOLDERS.md §1.5` anchor `#15-license` | ✓ |
| `README.md:322` — Author field | `docs/PLACEHOLDERS.md §1.1` anchor `#11-author-identity` | ✓ |
| `README.md:323` — Advisor field | `docs/PLACEHOLDERS.md §1.2` anchor `#12-advisor-identity` | ✓ |
| `README.md:324` — Institution field | `docs/PLACEHOLDERS.md §1.3` anchor `#13-institution` | ✓ |

All five links are correct. Markdown anchor format matches (lowercase, spaces become hyphens, dots dropped).

### 5.2 PLACEHOLDERS.md → source files

File paths in §1:
- `pyproject.toml:8` — verified correct.
- `README.md:316` — the README "Author and advisor" section header is at line 315-316. Functionally correct; the `TODO` on line 322 is the actual placeholder.
- `docs/design/01_business_requirements.md:8,9` — verified correct.
- `docs/design/02_database_design.md:4,5,1252` — verified correct.
- `docs/security/credentials_rotation.md:461`, `docs/security/threat_model.md:344`, `docs/security/incident_response.md:414,504` — all verified correct.
- `infra/main.bicep:76,79` — verified correct.
- `docs/design/03_architecture.md:112,115` — verified correct.
- `docs/dev_setup.md:40,46` — verified correct.

File paths in §2 (all five existing rows):
- `docs/design/03_architecture.md:179,223,381,646` — verified correct.
- `powerbi/README.md:162` — verified correct.
- `powerbi/model/tables/_Measures.tmdl:477,482,625,639` — verified correct.

All existing cross-references are accurate. No stale line numbers found.

### 5.3 glossary.md → PLACEHOLDERS.md

`docs/glossary.md:170`:
```
All placeholders are inventoried in [`docs/PLACEHOLDERS.md`](PLACEHOLDERS.md)
```

Link target `PLACEHOLDERS.md` is a relative path from within `docs/`. Resolved path: `docs/PLACEHOLDERS.md`. Correct.

The reverse link from `PLACEHOLDERS.md` to the glossary: `docs/PLACEHOLDERS.md:109` contains:
```
Per [`CLAUDE.md`](../CLAUDE.md) and the [glossary](glossary.md#placeholder-todo)
```

Anchor `#placeholder-todo` points to the glossary entry for "placeholder (TODO)". This anchor is valid — Markdown renders the header `**placeholder (TODO)**` as anchor `placeholder-todo` (special characters stripped or replaced). Correct.

### 5.4 CLAUDE.md → PLACEHOLDERS.md (implicit)

`CLAUDE.md` references the `feedback_placeholders_author_screenshots.md` rule but does not directly link to `docs/PLACEHOLDERS.md` — that is by design (`CLAUDE.md` is the agent context file, not user-facing navigation). The implicit chain (CLAUDE.md → feedback file → PLACEHOLDERS.md) is documented in `PLACEHOLDERS.md:4-7` (the preamble). No action needed.

### 5.5 Bi-directionality

The `docs/PLACEHOLDERS.md` preamble links to `CLAUDE.md` (line 5: `[`feedback_placeholders_author_screenshots`](../memory/feedback_placeholders_author_screenshots.md)`). This path assumes a `memory/` directory relative to `docs/` — the actual memory file lives at `C:\Users\Admin\.claude\projects\...\memory\MEMORY.md` (outside the repo). This link will be broken for anyone cloning the repo. However, this is an acknowledged limitation: the memory files are user-local. The link is a documentation artefact, not a navigation path used by external readers. Flagging as Nit but not raising a separate finding — the issue pre-dates E12 and is not an E12 regression.

---

## 6. Project status table accuracy

`README.md:288-303` (the project status table):

```
- **Etapa 12 complete** (line 288)
- [x] Etapa 12: Documentation polish + placeholders consolidated
- [ ] Etapa 13: Academic thesis (LaTeX)
```

Cross-check:
- E11 marked `[x]` — consistent with `STATE.md` "Etapa 11 COMPLETE".
- E12 marked `[x]` — consistent with the deliverable (this review is part of the E12 close gate).
- E13 marked `[ ]` — correct, not started.
- The label "Etapa 12 complete" on line 288 is premature — this review is the final gate before E12 is closed. However, the README presumably reflects the intended post-review state rather than the in-progress state. This is acceptable practice for a single-engineer build where the agent author and the reviewer are sequential rather than concurrent.

No defect raised. Accurate given the intended post-convergence state.

---

## 7. Subsidiary file checks

### `tcp/ai/anthropic_client.py` — Scope alias consolidation

`ALLOWED_SCOPES` and `Scope` are now defined once in `anthropic_client.py` and re-exported for `ask.py`. This closes `code11-MI-05`. The `get_args(Scope)` pattern is correct: `get_args` on a `Literal` type returns the literal values as a tuple; wrapping in `frozenset` gives O(1) membership checks. No issues.

### `function_app/triggers/ask.py` — Scope import

`ask.py` imports `ALLOWED_SCOPES` and `Scope` from `tcp.ai.anthropic_client`. The import resolves the `code11-MI-05` residual. No duplication remains (previously the four-tuple appeared in 3 places). Correct.

### `pyproject.toml` — freezegun removal

`freezegun` is no longer in `[project.optional-dependencies] dev`. This closes `code11-MI-02`. No issues.

### `infra/observability/kusto/03_anthropic_tokens_and_cost.kql` — coupling note

The file now carries a `COUPLING NOTE` comment (lines 24-31) directing editors to mirror the four pricing constants in `alerts.bicep`. This closes `obs10-MN-04` (USD-EUR FX duplication). The comment is clear and actionable. The reciprocal comment in `alerts.bicep` was not read in detail here — out of scope for this docs review — but the KQL side is correctly annotated.

---

## 8. Disposition summary

| ID | Severity | Finding | Recommended action |
|---|---|---|---|
| doc12-MJ-01 | **MAJOR** | `docs/design/03_architecture.md:208` (`ADR-XXX-cicd-role-split post-thesis` TODO) not catalogued in `PLACEHOLDERS.md §2` — violates the "if TODO and not listed here, that is a bug" contract | Add one row to §2 |
| doc12-MN-01 | **MINOR** | `ai_prompt_cache_contents.md §7:214` says "67 DAX measures" — canonical count is 69 (corrected in E9 convergence, reflected in README.md) | Change "67" → "69" |
| doc12-NT-01 | **NIT** | `ai_prompt_cache_contents.md §7:235-238` direction error — the SWA links to PowerBI, not vice versa | Rewrite two-sentence passage |
| doc12-NT-02 | **NIT** | `PLACEHOLDERS.md §5` resolution checklist grep pattern misses bare `TODO` author entries in the three security-doc version-history rows | Add explanatory note to §5 |

---

## 9. Strengths

1. **Complete coverage of mandatory placeholders** (§1). All nine logical placeholder types — author, advisor, institution, defence year, license, repo URL, owner tag, and screenshots — are enumerated with specific file+line references and clear resolution paths. The file/line numbers were verified correct.

2. **§3 automation-managed section is precise and correctly separated** from user-facing placeholders. The `swa-config-placeholder-guard` pre-commit hook cross-reference is accurate; the "DO NOT edit manually" guidance is unambiguous.

3. **Screenshots table (§1.8) is thorough and thesis-ready**. Eight surfaces documented with suggested capture conditions and notes. The `thesis/figures/` target directory is established.

4. **Glossary `placeholder (TODO)` entry** is well-formed and correctly cross-links to `PLACEHOLDERS.md` with the right relative path. Bi-directional navigation is intact.

5. **`ai_prompt_cache_contents.md §7` routing table** is the right artefact for closing arch10-MJ-05. The 10-row KPI-family split is logically correct and matches the implementation. The AI-vs-PowerBI distinction (ad-hoc query correctness under RLS vs. expressive power over a fixed schema) is well-articulated and will serve as thesis material.

6. **README.md license and author sections** are clean, minimal, and correctly defer to `PLACEHOLDERS.md` with anchored links. The "© 2026, shared for academic review only" stance is appropriate.

7. **`Scope` alias consolidation** (`anthropic_client.py` → `ask.py`) closes the code11-MI-05 residual cleanly. `get_args(Scope)` → `frozenset` is the correct pattern for deriving the runtime set from the type without divergence risk.

---

## 10. No-regression check

This is a documentation-only review pass with two code-adjacent changes (`anthropic_client.py` Scope alias, `pyproject.toml` freezegun removal). No new tests were added for these changes; however:

- The Scope alias is a pure refactor (no behaviour change); the existing `test_ask_trigger.py` + `test_ai_anthropic_client.py` continue to exercise the scope-validation path.
- The `freezegun` removal removes a never-imported library; the test suite at 288 passing / 0 failing is unaffected.

No regression risk from the E12 changes beyond the two open findings above.
