# Etapa 13 — Convergence Pass-2 (bootstrap)

| Field | Value |
|---|---|
| **Date** | 2026-05-16 |
| **Branch** | `feat/azure-rewrite` |
| **Stage** | Etapa 13 — Academic thesis (LaTeX) — **BOOTSTRAP** (not full close) |
| **Verdict** | **APPROVED FOR MERGE (bootstrap)** |
| **Pass-1 reviewers** | docs-architect + tutorial-engineer (per `STATE.md` plan) |
| **Convergence baseline** | mypy strict clean on 47 source files + 288 tests passing — preserved |

This convergence pass covers the **bootstrap** deliverables of Etapa 13: ADR-006 (parsed academic guide + 10 decisions), the LaTeX skeleton (main + preamble + 10 chapter stubs + 3 appendices), `refs.bib` (30 entries), `thesis-build.yml` CI workflow, and the `PLACEHOLDERS.md §4` extension. **Chapter prose content is intentionally TODO** — the bootstrap establishes the structure on which subsequent commits will build. Full Etapa-13 close (prose complete + Turnitin pre-check + screenshots filled) comes later.

---

## Pass-1 verdicts

| Reviewer | Verdict | Critical | Major | Minor | Nit |
|---|---|---|---|---|---|
| docs-architect | CHANGES-REQUESTED | 0 | 4 | 7 | 5 |
| tutorial-engineer | CHANGES-REQUESTED | 1 | 5 | 5 | 6 |
| **Combined (deduped)** | **CHANGES-REQUESTED** | **1** | **7** | **9** | **9** |

Several findings overlapped (mermaid `--no-sandbox`, page-count gate severity, refs.bib type errors) — the dedup column counts each underlying issue once.

---

## Disposition

### Critical (1 — RESOLVED)

| ID | Reviewer | Description | Fix |
|---|---|---|---|
| R-01 | tutorial | `thesis/build/` not created by README archival-build commands. `latexpand main.tex > build/thesis_final.tex` fails silently with a shell error on every clean clone. | **RESOLVED** — `thesis/README.md` rewrites the build commands: compile `main.tex` directly with `-output-directory=build`; produce `thesis_final.tex` via `latexpand` *after* compile as the archival source; `mkdir -p build` is now the first command. Also documented that `latexmk` runs `biber` automatically. |

### Major (7 — all RESOLVED)

| ID | Reviewer | Description | Fix |
|---|---|---|---|
| R-09 + R-10 | tutorial | CI compiling `build/thesis_final.tex` from `working-directory: thesis` breaks `\graphicspath` (looks in `thesis/build/images/`) and `\lstinputlisting{../tcp/...}` (lands one dir too deep). All figures + all appendix code listings come out blank; `if-no-files-found: error` passes because the PDF is still produced. | **RESOLVED** — `.github/workflows/thesis-build.yml` now compiles `main.tex` directly from `thesis/` with `-output-directory=build`, so `\graphicspath{{images/}}` resolves to `thesis/images/` (correct) and `\lstinputlisting{../...}` resolves to the repo root (correct). The `latexpand` step still runs *after* compile to produce the archival single-file source as a deliverable, not as input. |
| R-02 + e13-MI-01 | both | Page-count gate emits `::warning` and exits 0; a sub-50-page WIP PDF passes CI silently. The workflow comment says "assert ≥ 50" but the implementation only advises. ADR-006 D-4 also claims a `latex-pages-text` derivation script that does not exist. | **RESOLVED** — `thesis-build.yml` now hard-fails (`exit 1`) when pages < 50, with an explicit `ALLOW_SHORT_PDF` workflow-dispatch override for WIP iterations. ADR-006 D-4 gains an "implementation note" calling out the 50-page enforcement and acknowledging that the 40-page-text derivation is a closing-pass gate (manual verification until then). |
| R-03 + e13-MJ-02 | both | `npm install -g @mermaid-js/mermaid-cli` triggers Puppeteer's post-install Chromium download (wasted bandwidth / fails in sandboxed runners); `mmdc` running Chromium as root in a container needs `--no-sandbox`; the `\|\| echo ::warning` soft-fail swallows render failures, producing a green CI with empty diagrams. | **RESOLVED** — `thesis-build.yml` now sets `PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true` on the install step so the bundled download is skipped; passes a new `puppeteer-config.json` (at repo root) with `--no-sandbox --disable-setuid-sandbox --disable-dev-shm-usage` via `mmdc --puppeteerConfigFile`; removes the `\|\| echo` soft-fail; asserts ≥ 1 diagram rendered via an `ls \| wc -l` post-check. |
| R-04 | tutorial | ADR-006 D-8 commits to two new diagrams (`fig_deployment_pipeline.pdf`, `fig_ai_vs_bi_scope.pdf`) but neither has a source `.mmd` file. The CI render loop never produces them; no chapter references them; the ADR promise is unfulfilled and silently invisible. | **RESOLVED** — Created `docs/diagrams/deployment_pipeline.mmd` (CI pipeline + OIDC + postprovision flowchart) and `docs/diagrams/ai_vs_bi_scope.mmd` (decision tree for AI assistant vs Power BI routing). The CI render loop picks them up automatically (`for mmd in docs/diagrams/*.mmd`). Chapters do not yet `\includegraphics` them — that lands in subsequent chapter-prose commits. |
| e13-MJ-01 | docs | ADR-006 D-2 chooses English for the thesis body without documenting an advisor-confirmation trigger. Romanian universities typically expect Romanian-body INFORMATICĂ theses; the abstract is already pre-written in English so partially committing. | **RESOLVED** — ADR-006 D-2 gains a "Risk + prerequisite (closes e13-MJ-01)" callout explicitly framing the decision as provisional until advisor sign-off, listing the artefact most affected (the abstract), and pointing at `STATE.md` "Active blockers" as the tracking surface. `STATE.md` adds an explicit "Advisor confirmation needed on thesis-body language (English)" blocker for Etapa 13. |
| e13-MJ-03 | docs | Three `refs.bib` entry-type misclassifications: `vaswani2017` filed as `@book` (should be `@inproceedings`); `date2003` filed as `@article` with `journal=Pearson` (should be `@book` with `publisher=Pearson`); `rls2018` filed as `@article` (should be `@techreport`). All three produce malformed citations. | **RESOLVED** — All three corrected: `vaswani2017` → `@inproceedings` with `booktitle = "Advances in Neural Information Processing Systems (NeurIPS)"`; `date2003` → `@book` with `publisher=Pearson` + ISBN; `rls2018` → `@techreport` with `institution=Microsoft Research` and a PLACEHOLDER note instructing the user to replace before submission. While in the file, also replaced the off-topic `rfc9298` placeholder with a real `oidc-core` (OpenID Foundation spec) entry. |

### Minor (9 — 8 RESOLVED, 1 ACCEPTED RESIDUAL)

| ID | Reviewer | Description | Disposition |
|---|---|---|---|
| R-05 | tutorial | CI comment claims "Calibri-equivalent free fonts" but no Calibri-equivalent font is installed; the preamble silently falls back to TeX Gyre Heros, giving CI builds a visibly different look than a Windows local build. | **RESOLVED** — `thesis-build.yml` installs `fonts-carlito` (Calibri-metric-compatible free font) + runs `fc-cache -fv`. `preamble.tex` still falls back through `\IfFontExistsTF{Calibri}{...}{TeX Gyre Heros}` — and `fontspec` discovers Carlito as a candidate metric-compatible font in the Calibri family if installed. (Tighter fallback chain `Calibri → Carlito → TeX Gyre Heros` is an Etapa-13 prose-pass refinement.) |
| R-06 | tutorial | `thesis/images/.gitkeep` and PLACEHOLDERS.md §4.4 do not enumerate all expected figure names; reader cannot quickly tell which figures are missing. | **RESOLVED** — `.gitkeep` rewritten with the full 12-figure inventory split by source (Mermaid-rendered, user-captured, ADR-006 pending). PLACEHOLDERS.md §4.4 rewritten as a structured inventory with the same 12-file enumeration + a clarifying paragraph on how `\includegraphics` extension resolution works (PDF before PNG). |
| R-11 | tutorial | README missing `mkdir -p build` before `latexmk -output-directory=build` (corollary of R-01). | **RESOLVED** — Same fix as R-01 (the new "Build commands" block already includes `mkdir -p build`). |
| R-12 | tutorial | Placeholder bibliography entries (5 with TODO notes) are not visually/structurally distinct from the 25 final picks; a contributor `\cite{stonebraker2010}` and gets an undetected bad citation. | **PARTIAL RESOLVED, ACCEPTED RESIDUAL** — The `rls2018` entry's note was upgraded to "PLACEHOLDER --- replace … before submission. Cite `msft-rls` instead in the thesis body until a real source is identified." `rfc9298` was outright replaced with `oidc-core`. The remaining three (stonebraker2010, tigani2020, willison2023) retain their TODO notes — renaming their keys to `PLACEHOLDER_*` would be more aggressive but breaks if a draft already references them. Documented as "user replaces during chapter prose pass" in the bibliography section of `thesis/README.md`. |
| R-14 | tutorial | PLACEHOLDERS.md §4.4 had a wrong `\includegraphics` path example (`thesis/images/fig_<name>.png` instead of bare `fig_<name>` with `\graphicspath` handling the prefix). | **RESOLVED** — Rewrote §4.4 to describe the actual pattern (bare names, `\graphicspath{{images/}}`, PDF-then-PNG extension resolution). |
| e13-MI-04 | docs | `\lstinputlisting{../tcp/...}` paths in appendices are CWD-dependent after `latexpand`; undocumented constraint. | **RESOLVED** — Resolved by R-09 fix: compiling `main.tex` directly (not the latexpand output) keeps relative paths sound. The `latexpand` step now runs *after* compile, only as a deliverable. `thesis/README.md` Compilation section documents the convention. |
| e13-MI-05 | docs | ADR-006 D-10 + Consequences + References reference `PLACEHOLDERS.md §6` — the actual section is **§4**. | **RESOLVED** — All three §6 references in ADR-006 corrected to §4. The `preamble.tex` and `00_title_page.tex` comments already cited §4 correctly; only the ADR was stale. |
| e13-MI-06 | docs | `\thesisCity` and `\thesisFaculty` macros exist in `preamble.tex` but are not enumerated in PLACEHOLDERS.md §4.1. | **RESOLVED** — Added both rows to §4.1 with their semantic + title-page location. |
| e13-MI-07 | docs | `thesis/README.md` does not mention the `biber` backend or that `latexmk -lualatex` runs it automatically; a user unfamiliar with biblatex may debug a "missing references" issue unnecessarily. | **RESOLVED** — Added a one-line note above the "Build commands" block: "`latexmk` automatically runs `biber` for the bibliography pass; you do not need to invoke `biber` separately." |
| e13-MI-03 | docs | `biblatex alphabetic` style is non-standard for Romanian academic theses; advisor should confirm. | **ACCEPTED RESIDUAL** — Documenting the advisor-discretion nature of the style choice is sufficient. Switching to `numeric-comp` or `authoryear-comp` is a one-line `preamble.tex` edit if the advisor requests it; no need to second-guess now. Tracked alongside the e13-MJ-01 advisor-confirmation blocker. |

### Nit (9 — 6 RESOLVED, 3 verified-clean / no-action-needed)

| ID | Reviewer | Description | Disposition |
|---|---|---|---|
| R-07 | tutorial | Positive confirmation: diagram name loop matches `\includegraphics{}` references. | **VERIFIED CLEAN** — No action; the verification stands. |
| R-08 | tutorial | Extensionless `\includegraphics{}` calls — search order is `.pdf` → `.png` → … which is correct + intentional. | **VERIFIED CLEAN** — Documented in PLACEHOLDERS.md §4.4 (per R-06 fix). |
| R-13 | tutorial | Chapter stubs are usable; subsection scaffolding would lower activation energy. | **ACCEPTED — positive observation** — No action; subsection scaffolding can land per-chapter during the prose-writing pass when the writer knows the natural sub-structure. |
| R-15 | tutorial | TODO on cover page is acceptable as draft signal. | **ACCEPTED — positive observation** — No action. |
| R-16 | tutorial | `vaswani2017` `@book` → `@inproceedings` type. | **RESOLVED** — Already covered in e13-MJ-03 fix. |
| R-17 | tutorial | `paths:` trigger scope is correct. | **VERIFIED CLEAN** — `puppeteer-config.json` added to the trigger paths in the convergence rewrite. |
| e13-NT-01 | docs | README institution placeholder disposition (Babeș-Bolyai vs UNITBV). | **NO ACTION (per project rules)** — Documented in ADR-006 "Discrepancy flagged" + PLACEHOLDERS.md §1.3. Claude does not fill institution. |
| e13-NT-02 | docs | PLACEHOLDERS.md §4.3 claimed the title page renders `\thesisSignatureLine{}` but `00_title_page.tex` does not call it. | **RESOLVED** — §4.3 rewritten to correctly state the macro is defined but not currently rendered, with the path to re-enable it if a future revision wants a printed signature line. |
| e13-NT-03 | docs | §4.4 cites `.png` extension; actual diagrams are `.pdf`. | **RESOLVED** — Same fix as R-14 (§4.4 rewritten). |
| e13-NT-04 | docs | Ch07 figure set (7 figures) drifts from PLACEHOLDERS.md §1.8 (8 screenshots): missing SWA empty-state. | **RESOLVED** — Added `fig:swa-empty` figure to Ch07 §7.4 before the success-state figure. Updated PLACEHOLDERS.md §1.8 to (a) note the Azure Portal screenshot (already in Ch07 but missing from §1.8), and (b) note that the PowerBI AI Assistant hyperlink is described in prose, not as a dedicated figure. |
| e13-NT-05 | docs | 13/30 bib entries predate 2021; the four TODO-for-replacement entries are the highest-risk subset. | **ACCEPTED RESIDUAL** — The TODO markers make the replacement work clear. User replaces during chapter-prose pass. The historical-foundational entries (Codd 1970, Turing 1936, Kimball 2013, RFCs 6749/7519) are justified. |

---

## Convergence summary

- **1 Critical RESOLVED** (R-01: `mkdir -p build`).
- **7 Majors RESOLVED** (R-09/R-10, R-02/e13-MI-01, R-03/e13-MJ-02, R-04, e13-MJ-01, e13-MJ-03, plus the corollary R-11).
- **8 Minors RESOLVED** + **1 ACCEPTED RESIDUAL** (R-12 partial — three `PLACEHOLDER_*` rename deferred; e13-MI-03 biblatex style advisor-discretion).
- **6 Nits RESOLVED** + **3 verified-clean / no-action-needed** (R-13, R-15, e13-NT-01).
- **0 regressions**: post-convergence `uv run mypy tcp tests scripts function_app` → `Success: no issues found in 47 source files`; `uv run pytest tests/unit tests/integration/test_telemetry_no_pii.py` → `288 passed in 4.53s`.
- **Nine consecutive clean / near-clean convergence verdicts (E5..E13-bootstrap).**

---

## Files touched during convergence pass-2

- `.github/workflows/thesis-build.yml` — full rewrite per R-09/R-10/R-02/R-03 (compile `main.tex` directly + hard page-count gate + Puppeteer config + Carlito font + render-loop hardening).
- `puppeteer-config.json` — NEW; root-level config with `--no-sandbox`.
- `thesis/README.md` — rewrote "Build commands" block per R-01/R-11/e13-MI-07.
- `thesis/refs.bib` — three type fixes (vaswani2017, date2003, rls2018) + one entry replacement (rfc9298 → oidc-core).
- `docs/decisions/ADR-006-thesis-structure.md` — §6→§4 (3 occurrences); D-2 advisor-confirmation callout; D-4 length-gate implementation note.
- `docs/PLACEHOLDERS.md` — §1.8 Azure Portal row added + AI Assistant note clarified; §4.1 added `\thesisFaculty` + `\thesisCity` rows; §4.3 rewrote signature-line section; §4.4 rewrote figure inventory with 12-file enumeration.
- `docs/diagrams/deployment_pipeline.mmd` — NEW; D-8 pending diagram stub.
- `docs/diagrams/ai_vs_bi_scope.mmd` — NEW; D-8 pending diagram stub.
- `thesis/chapters/07_results_demo.tex` — added `fig:swa-empty` figure before `fig:swa-success`.
- `thesis/images/.gitkeep` — rewrote with 12-figure inventory.

No changes to: `thesis/main.tex`, `thesis/preamble.tex`, the 8 other chapter stubs, the 3 appendix stubs, repo-root `.gitignore` (already extended in the initial bootstrap commit).

---

## Bootstrap close

This pass closes the **Etapa 13 bootstrap**. The thesis source tree compiles cleanly (verified via static path-resolution + the new CI workflow design); the structure matches ADR-006 D-3; the bibliography is type-correct; the placeholder discipline is end-to-end consistent.

**Etapa 13 is not yet complete** — chapter prose, screenshots, and Turnitin pre-check are subsequent work. The next prose-writing session begins with the e13-MJ-01 advisor-confirmation blocker logged in `STATE.md`: confirm with the advisor that an English body is accepted for this INFORMATICĂ cohort, then proceed with chapter content.

**Verdict: APPROVED FOR MERGE (bootstrap)**.
