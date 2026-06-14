# Review: Etapa 13 — Reproducibility + Reader Cold-Start Experience

| Field | Value |
|---|---|
| **Reviewer role** | Tutorial engineering specialist — reproducibility + reader experience |
| **Companion review** | Docs-architect (structural correctness) — separate pass, not duplicated here |
| **Branch** | `feat/azure-rewrite` |
| **Date** | 2026-05-16 |
| **Artefacts read** | `thesis/README.md`, `thesis/preamble.tex`, `thesis/main.tex`, all 10 chapter files, all 3 appendix files, `thesis/refs.bib`, `.github/workflows/thesis-build.yml`, `docs/decisions/ADR-006-thesis-structure.md`, `docs/PLACEHOLDERS.md §4`, `.gitignore` |

---

## Severity scale

- **Critical** — build breaks on a clean clone or the examiner cannot open the PDF.
- **Major** — the PDF is produced but contains material errors visible to an examiner; or a contributor loses > 30 min debugging a non-obvious environment issue.
- **Minor** — visible gap in documentation or process; fixable in < 30 min; does not break the build.
- **Nit** — cosmetic; correctness not affected.

---

## Finding R-01 — `thesis/build/` directory not created before `latexpand` on local builds

**Severity: Critical**

**Location**: `thesis/README.md`, "Build commands" section, line 65.

**Observation**: The README "single-file archival build" command runs:

```bash
latexpand main.tex > build/thesis_final.tex
```

from inside the `thesis/` working directory. The shell redirect `>` will fail immediately on a clean clone because `thesis/build/` does not exist — it is gitignored (`thesis/build/` in `.gitignore` line 77) and no `.gitkeep` is placed inside it. The CI workflow correctly issues `mkdir -p build` before the `latexpand` call (workflow line 72), but the README does not include this step. A contributor following the README verbatim will see:

```
bash: build/thesis_final.tex: No such file or directory
```

with no output explaining what went wrong. The quick-build (`latexmk -lualatex … main.tex`, README line 62) does not need the directory and would succeed, masking the issue until the contributor tries the archival build path.

**Impact**: Every first-time contributor who follows the README exactly hits a silent shell error on the archival build path. Because `latexpand` exits 0 even when the redirect fails (it writes to stdout, the shell manages the file), the error message is from the shell, not the tool, and its connection to the missing directory is non-obvious.

**Fix**: Add `mkdir -p build` as the first line of the "Single-file archival build" code block in `thesis/README.md`.

---

## Finding R-02 — Page-count gate is `::warning` but the guide imposes a hard minimum

**Severity: Major**

**Location**: `.github/workflows/thesis-build.yml`, lines 82–84.

**Observation**: The workflow emits a GitHub Actions `::warning` when the page count is below 50, and then exits 0:

```yaml
echo "::warning::Page count $pages below the 50-page minimum (Ghid §I)"
```

`ADR-006 §1` ("Parsed requirements") records the guide requirement as: **minimum 50 pages total, of which minimum 40 pages of text**. A warning that does not fail the workflow means a pull request with a 35-page PDF passes CI and can be merged. The examiner will reject the submission; the contributor finds out at or after submission, not at push time.

The workflow comment itself (line 9) says "assert ≥ 50", which implies an error/failure, contradicting the implementation.

**Impact**: CI provides a false green signal on an under-length submission. The only protection is the contributor reading the warning annotation in the Actions UI — easy to miss in a passing check.

**Fix**: Change the `::warning` to `exit 1` (or equivalent) so the workflow fails the build when `$pages -lt 50`. A note comment can acknowledge that during the stub/WIP phase the threshold should be overrideable via a `$ALLOW_SHORT_PDF` environment variable or a `workflow_dispatch` input, so contributors can iterate on incomplete chapters without blocking CI.

---

## Finding R-03 — `PUPPETEER_EXECUTABLE_PATH` set on the `install` step but not read by `npm install -g`

**Severity: Major**

**Location**: `.github/workflows/thesis-build.yml`, lines 48–55.

**Observation**: The "install mermaid-cli + chromium" step sets `PUPPETEER_EXECUTABLE_PATH: /usr/bin/chromium` as an environment variable on the step. However, `npm install -g @mermaid-js/mermaid-cli` triggers a Puppeteer post-install script that downloads its own bundled Chromium unless it finds `PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true` set. The `PUPPETEER_EXECUTABLE_PATH` variable does not suppress the download — it only tells an already-installed Puppeteer where to find the browser at runtime.

The combined effect on a `texlive/texlive:TL2024-historic` container (a Debian-based image without a configured npm/node environment) is:

1. `npm install -g @mermaid-js/mermaid-cli` attempts to download Chromium via Puppeteer's post-install hook.
2. The download either succeeds (wasted bandwidth + time, downloading a second Chromium next to the apt-installed one) or fails if the image's network sandbox blocks it.
3. Even if it succeeds, the `mmdc` CLI will launch the apt-installed Chromium at `/usr/bin/chromium` at runtime (because `PUPPETEER_EXECUTABLE_PATH` is set on the "render diagrams" step) — but the download step itself was unnecessary.

The correct pattern requires `PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true` set **before** the `npm install` call, so Puppeteer knows to skip the bundled Chromium download and rely on the system binary.

**Impact**: On a clean runner the install step may take 3–5 extra minutes downloading a redundant Chromium binary (or fail in a sandboxed environment). If Puppeteer's download fails mid-install, the entire `npm install -g` fails, and the render step is skipped (the `|| echo "::warning"` only covers `mmdc` execution, not the install step).

**Fix**:

```yaml
- name: install mermaid-cli + chromium
  env:
    PUPPETEER_SKIP_CHROMIUM_DOWNLOAD: "true"
    PUPPETEER_EXECUTABLE_PATH: /usr/bin/chromium
  run: |
    apt-get update
    apt-get install -y --no-install-recommends nodejs npm chromium
    npm install -g @mermaid-js/mermaid-cli
```

Setting both variables on the install step ensures the skip is honoured during the post-install hook.

---

## Finding R-04 — Two ADR-006 thesis-specific diagrams are referenced in the README but absent from both the CI render loop and any `\includegraphics` call

**Severity: Major**

**Location**: `thesis/README.md` lines 89–93; `docs/decisions/ADR-006-thesis-structure.md` §D-8; `.github/workflows/thesis-build.yml` lines 62–65.

**Observation**: ADR-006 §D-8 commits to two new thesis-specific diagrams:

- `fig_deployment_pipeline.pdf` (GitHub Actions OIDC + `azd up` + postprovision)
- `fig_ai_vs_bi_scope.pdf` (AI-vs-BI scope routing)

The README echoes this commitment at lines 89–93 ("Two new thesis-specific diagrams … will be added later under `thesis/images/`"). However:

1. Neither diagram's `.mmd` source file exists in `docs/diagrams/` — the glob `docs/diagrams/*.mmd` finds only the four pre-existing files.
2. The CI render loop (`for mmd in docs/diagrams/*.mmd`) will not generate these files because the source does not exist.
3. No chapter file contains `\includegraphics{fig_deployment_pipeline}` or `\includegraphics{fig_ai_vs_bi_scope}`, so there is no compile-time `!` (missing image) error either.

The result is a silent gap: the ADR promises artefacts that the build does not produce and the chapters do not reference. A future contributor writing Chapter 4 or 7 who follows the ADR will try to reference `fig_deployment_pipeline` and get a "file not found" compile warning with no obvious resolution path.

**Impact**: Missing source `.mmd` files means the ADR promise cannot be fulfilled without additional work. The README creates a false expectation that these will "be added later" without tracking where the source will live.

**Fix**: Either (a) create stub `docs/diagrams/deployment_pipeline.mmd` and `docs/diagrams/ai_vs_bi_scope.mmd` files now so the CI loop picks them up, or (b) track the gap explicitly in `docs/PLACEHOLDERS.md §4` with a clear note that these diagrams are pending source creation, so the next writing session does not re-derive this from scratch.

---

## Finding R-05 — Calibri fallback to TeX Gyre Heros proceeds silently in CI; contributor cannot distinguish font from layout failures

**Severity: Minor**

**Location**: `thesis/preamble.tex` lines 32–37; `.github/workflows/thesis-build.yml` (no font install step).

**Observation**: `preamble.tex` uses `\IfFontExistsTF{Calibri}` and emits `\PackageWarning` when the fallback activates. The `texlive/texlive:TL2024-historic` container does not include Calibri (a Microsoft proprietary font) and there is no step in the workflow to install a metric-compatible substitute such as `fonts-carlito` (the free Calibri metric-compatible font available in Debian repos).

The README correctly documents this ("Calibri installed at the OS level or the build falls back to TeX Gyre Heros — visibly different; intentional so proof-readers notice the fallback"). The intentionality is clear. However:

1. The CI workflow comment on line 37 says the image "carries TeX Live 2024 + latexmk + biber + latexpand + Calibri-equivalent free fonts" — but this claim cannot be verified without checking the actual `texlive/texlive:TL2024-historic` image manifest, and the workflow does not install any Calibri-equivalent.
2. `fonts-carlito` is available via `apt-get` in one line. Installing it would make the CI build font-accurate (matching the guide's Calibri requirement) at negligible cost.
3. A contributor on Windows (where Calibri is pre-installed) will get a different visual output than the CI-generated PDF, making visual proofing against the CI artefact unreliable.

**Impact**: Minor for reproducibility (the build succeeds), but confusing for a first-time contributor who downloads the CI artefact and sees TeX Gyre Heros instead of Calibri and does not read the README carefully.

**Fix**: Add `apt-get install -y fonts-carlito` to the install step and rename the font selection in `preamble.tex` to try `Carlito` as a secondary fallback before `TeX Gyre Heros`, so the CI build matches the expected visual output. Alternatively, update the CI comment to remove the "Calibri-equivalent free fonts" claim until the package is actually installed.

---

## Finding R-06 — `thesis/images/.gitkeep` does not enumerate expected figure names; a clean-clone reader cannot know which figures are missing

**Severity: Minor**

**Location**: `thesis/images/.gitkeep` (entire file); `docs/PLACEHOLDERS.md §4.4`.

**Observation**: The `.gitkeep` comment reads: "PDF figures are produced at build time (Mermaid diagrams) or captured by the user at submission time (screenshots)." It does not list the expected filenames. A contributor on a clean clone who runs the build without first rendering the diagrams will see 12 separate LuaLaTeX `! LaTeX Error: File 'fig_architecture.pdf' not found.` warnings — one per `\includegraphics{}` call — but has no single inventory to check progress against.

Cross-referencing all `\includegraphics` calls across the thesis reveals 12 expected image files:

**Mermaid-generated (CI produces these):**
- `fig_architecture.pdf`
- `fig_erd.pdf` (used twice: `04_analysis_design.tex` + `B_database_schema.tex`)
- `fig_ai_sequence.pdf`
- `fig_cron_flow.pdf`

**Screenshots (user captures these):**
- `fig_screenshot_azure_portal.pdf`
- `fig_screenshot_workbook.pdf`
- `fig_screenshot_pbi_floor.pdf`
- `fig_screenshot_pbi_team.pdf`
- `fig_screenshot_pbi_trader.pdf`
- `fig_screenshot_swa_success.pdf`
- `fig_screenshot_swa_refusal.pdf`

**ADR-006 §D-8 pending (no source yet):**
- `fig_deployment_pipeline.pdf`
- `fig_ai_vs_bi_scope.pdf`

`docs/PLACEHOLDERS.md §4.4` references the screenshot set from §1.8 but does not list the Mermaid-generated figures or the two pending ADR-006 figures. A reader consulting only PLACEHOLDERS.md gets an incomplete inventory.

**Impact**: A contributor who has rendered the four Mermaid PDFs but not captured screenshots cannot quickly confirm "all generated figures are present; I only need to add screenshots." They must grep `\includegraphics` themselves.

**Fix**: Expand the `.gitkeep` comment to a structured list of all expected files with their source category. Alternatively, expand `docs/PLACEHOLDERS.md §4.4` to include the Mermaid-generated files and the two pending ADR-006 figures.

---

## Finding R-07 — Diagram name generated by the CI loop matches `\includegraphics` references correctly — no mismatch

**Severity: Nit (positive confirmation)**

**Location**: CI workflow lines 62–65 vs. `\includegraphics` calls in `04_analysis_design.tex`.

**Observation**: The render loop:

```bash
for mmd in docs/diagrams/*.mmd; do
  name=$(basename "$mmd" .mmd)
  mmdc -i "$mmd" -o "thesis/images/fig_${name}.pdf" ...
done
```

with the four files `architecture.mmd`, `erd.mmd`, `ai_sequence.mmd`, `cron_flow.mmd` produces:
- `fig_architecture.pdf` → matches `\includegraphics{fig_architecture}` ✓
- `fig_erd.pdf` → matches `\includegraphics{fig_erd}` ✓
- `fig_ai_sequence.pdf` → matches `\includegraphics{fig_ai_sequence}` ✓
- `fig_cron_flow.pdf` → matches `\includegraphics{fig_cron_flow}` ✓

The `\graphicspath{{images/}}` in `preamble.tex` is correct; `graphicx` will search the `images/` directory and find all four files. No name mismatch exists for the currently committed diagrams.

**Note**: This finding confirms correctness rather than raising a concern. The name-matching logic is sound for the four existing files. The gap identified in R-04 (two ADR-006 pending diagrams) would break this clean pattern if added to chapters without first creating their `.mmd` sources.

---

## Finding R-08 — `\includegraphics` calls use no file extension; `graphicx` search order may produce surprises

**Severity: Nit**

**Location**: All `\includegraphics{}` calls in `04_analysis_design.tex`, `07_results_demo.tex`, `B_database_schema.tex`.

**Observation**: All calls omit the extension (e.g., `\includegraphics{fig_architecture}`). Under LuaLaTeX + `graphicx`, the search order for extensionless names is: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.eps`. This is correct and intentional (CI produces `.pdf` outputs; screenshots will likely be `.png` or `.jpg`). However, a contributor who captures a screenshot as `fig_screenshot_azure_portal.png` and places it in `thesis/images/` will have it correctly picked up — but a contributor who accidentally provides both a `.png` and a `.pdf` of the same name will silently get the `.pdf` version (the first hit in the search order).

The `.gitignore` excludes `.pdf`, `.png`, `.jpg`, `.jpeg`, `.svg` from `thesis/images/`, so this scenario can only arise from uncommitted local files, which is acceptable. No action required; noting for completeness.

---

## Finding R-09 — `thesis/build/` is gitignored but the CI `latexmk` output-directory flag points into it — `latexmk` may fail to find `thesis_final.tex` relative to the working directory

**Severity: Major**

**Location**: `.github/workflows/thesis-build.yml`, lines 73–76.

**Observation**: The compile step runs with `working-directory: thesis` and issues:

```bash
latexpand main.tex > build/thesis_final.tex
latexmk -lualatex -interaction=nonstopmode -halt-on-error \
  -output-directory=build build/thesis_final.tex
```

`latexmk` is invoked with both `-output-directory=build` and the input file `build/thesis_final.tex`. The input path `build/thesis_final.tex` is relative to the current working directory (`thesis/`), which is correct. However, `latexmk` internally uses the directory of the source file to resolve `\input{}` and `\includegraphics{}` paths. After `latexpand`, `build/thesis_final.tex` is a flat file with all `\input{}` directives inlined, so relative path resolution inside the file is no longer `thesis/` but effectively root-relative from wherever the file sits.

The critical issue: `preamble.tex` sets `\graphicspath{{images/}}`. After `latexpand`, this path is baked into `thesis_final.tex`. When `latexmk` compiles `build/thesis_final.tex`, its working assumption for relative paths is `thesis/build/` (the directory of the source file being compiled), not `thesis/`. So it will look for images at `thesis/build/images/` — a directory that does not exist — rather than `thesis/images/`.

The README's quick-build path (`latexmk -lualatex main.tex` from inside `thesis/`) avoids this problem because the working directory and source file directory coincide. The archival path breaks image resolution.

**Impact**: The CI-compiled PDF will be missing all figures. `latexmk` (and LuaLaTeX under `-interaction=nonstopmode`) will not abort on missing image files by default; it will produce a PDF with blank figure placeholders and emit warnings. The `if-no-files-found: error` on the upload step will not catch this because the PDF file is still produced — it is just missing all 12 images.

**Fix option A** (minimal): Change the compile step to avoid the `build/` subdirectory for the source file while still writing outputs there:

```bash
latexpand main.tex > build/thesis_final.tex
# Compile from the thesis/ root so relative paths resolve correctly
latexmk -lualatex -interaction=nonstopmode -halt-on-error \
  -output-directory=build main.tex
```

This compiles `main.tex` directly (not the latexpand output), using `build/` only for output files. The archival `thesis_final.tex` is still produced as a submission artefact; the PDF is produced from the canonical `main.tex`.

**Fix option B** (more robust): Use `-cd` flag with the source file so `latexmk` changes to the source file's directory before resolving relative paths, and set `TEXINPUTS` and `graphicspath` explicitly:

```bash
latexmk -lualatex -interaction=nonstopmode -halt-on-error \
  -output-directory=../build -cd build/thesis_final.tex
```

But fix option A is simpler and removes the ambiguity entirely.

---

## Finding R-10 — `\lstinputlisting` paths in appendices are relative to the compiled source file, not the repo root

**Severity: Major**

**Location**: `appendix/A_code_snippets.tex` lines 19–68; `appendix/B_database_schema.tex` lines 18–31.

**Observation**: Both appendix files use paths like:

```latex
\lstinputlisting{../tcp/safe_query.py}
\lstinputlisting{../db/migrations/V001__init.sql}
```

These paths resolve correctly when the source file being compiled is `thesis/main.tex` (working directory `thesis/`) because `../` goes up one level to the repo root. However, after `latexpand` produces `thesis/build/thesis_final.tex`, the same relative paths `../tcp/safe_query.py` now resolve from `thesis/build/`, not `thesis/`, landing one directory too deep: `thesis/tcp/safe_query.py` (which does not exist).

Unlike `\input{}` directives (which `latexpand` inlines before compilation), `\lstinputlisting{}` is evaluated at compile time — `latexpand` does not expand it. So the flat `thesis_final.tex` retains the `../` relative paths verbatim, and `latexmk` resolves them relative to the source file's location (`thesis/build/`).

**Impact**: All six `\lstinputlisting` calls in Appendix A and both calls in Appendix B will fail to find their source files when compiling from `thesis/build/thesis_final.tex`. The appendices will be blank. The `latexmk` log will contain `File not found: ../tcp/safe_query.py` but the PDF is still produced (LuaLaTeX does not abort on a missing `\lstinputlisting` file by default in nonstopmode). The CI will upload a PDF with empty appendices and no error.

This is actually a second independent manifestation of the same root cause as R-09 (compiling the latexpand output from a subdirectory). Fix option A from R-09 (compile `main.tex` directly, use `build/` only for output) resolves both R-09 and R-10 simultaneously.

**Fix**: Same as R-09 fix option A. If `thesis_final.tex` must be compiled directly (for archival fidelity), use `TEXINPUTS` to tell TeX where to look: set `TEXINPUTS=..//:` before the `latexmk` call in the workflow so TeX searches up from the build directory.

---

## Finding R-11 — `thesis/build/` must exist before `latexmk` writes to `-output-directory=build`; CI creates it, README does not

**Severity: Minor**

**Location**: `.github/workflows/thesis-build.yml` line 71; `thesis/README.md` lines 66–68.

**Observation**: The CI workflow correctly runs `mkdir -p build` (line 71) before the `latexmk` call. The README archival build commands omit this step (same as R-01 — the `latexpand` redirect and the `latexmk -output-directory` both require the directory to exist). This is a corollary of R-01 but affects the `latexmk` step in addition to the `latexpand` step.

Already covered by R-01's fix; raising separately to indicate the README needs both commands corrected.

---

## Finding R-12 — Placeholder bibliography entries are not visually distinct from final entries in `refs.bib`

**Severity: Minor**

**Location**: `thesis/refs.bib` lines 69, 86–87, 94, 139–140, 261.

**Observation**: Five entries carry `note = {TODO: replace …}` comments:

- `stonebraker2010` — "TODO: replace with thesis-relevant article on OLAP/OLTP separation."
- `rls2018` — "TODO: replace with actual MSR pub or remove if no peer-reviewed source."
- `tigani2020` — "TODO: replace with thesis-relevant BI article."
- `willison2023` — "TODO: replace with peer-reviewed alternative or move to web-resources section."
- `rfc9298` — "TODO: replace with thesis-relevant RFC (e.g., OIDC Core)."

A first-time reader of the `.bib` source cannot quickly distinguish these five "placeholder citations that must be replaced" from the 25 "final picks that are ready." The only signal is a `note` field containing `TODO:`, which requires reading each entry.

Furthermore, `rls2018` has `author = {Microsoft Research}` and `journal = {Microsoft Research Technical Report}` but no real URL, DOI, or volume — it is a fabricated entry with no real bibliographic coordinates. If cited in the thesis, biber will compile it without error but the generated bibliography entry will be missing key fields that an examiner would use to verify the source.

**Impact**: A contributor who adds `\cite{stonebraker2010}` while writing Chapter 2 produces a citation to an entry flagged for replacement. The build succeeds; the bad citation makes it to the examiner. The `rls2018` entry is effectively a ghost citation.

**Fix**: Prefix the keys of placeholder entries with `PLACEHOLDER_` (e.g., `PLACEHOLDER_stonebraker2010`) so they cannot be `\cite{}`d without triggering a biber "undefined citation" error. Document in the `refs.bib` header comment that any key starting with `PLACEHOLDER_` must be replaced before it is cited.

---

## Finding R-13 — Chapter stubs provide strong scaffolding for a returning writer; Chapter 5 (Implementation) is the most complete

**Severity: Nit (positive observation)**

**Location**: All `thesis/chapters/*.tex` stubs.

**Observation**: This is an assessment of writing usability (review brief item 7). Evaluating Chapter 5 (`05_implementation.tex`) from the perspective of a Romanian undergraduate with no prior LaTeX experience returning after three weeks:

**What works well:**
- Every section has a `% Target: X pages` comment giving a concrete length goal.
- Every section's `% Source material:` comment gives the exact file path(s) to read, down to the section number (e.g., `docs/design/03_architecture.md §3.2`).
- The `% Cover:` bullet lists in each section are detailed enough to be converted directly to LaTeX subsections or paragraphs without re-reading the source design docs.
- The pre-written `\lstlisting` block for `derive_seed()` in §5.3 is a worked example that shows exactly how inline code should look, reducing the "how do I start" anxiety.
- Section labels are pre-defined (`\label{sec:safe-query}`) so cross-references from other chapters can be added without editing this file.

**Gaps:**
- None of the stubs include a `\subsection{}` skeleton. A returning writer knows the topics but may not know how to divide 2.5 pages on the AI pipeline (§5.6) into logical subsections. A commented-out subsection scaffold (`% \subsection{Stage 1: header validation}` etc.) would lower the activation energy further.
- Chapter 5 has a pre-written `\lstlisting` block but chapters 2, 3, and 4 have no worked examples of tables, figures with captions, or quotation blocks — tools that chapter authors will need frequently.

Overall assessment: the stubs are usable for an experienced developer coming back to write. They are above average for academic thesis scaffolding. A non-developer Romanian undergraduate would still struggle without LaTeX knowledge; the README's "Prerequisites" section does not point to any LaTeX introductory resource.

---

## Finding R-14 — Navigation from `README.md` → `thesis/README.md` → chapter files → `docs/` source is functional but has one broken link

**Severity: Minor**

**Location**: `docs/PLACEHOLDERS.md §4.4`, line referencing `\includegraphics{thesis/images/fig_<name>.png}`.

**Observation**: Cross-link discoverability audit (review brief item 8):

- Top-level `README.md` → `thesis/README.md`: present as a relative link in the layout table (assumption; PLACEHOLDERS.md references it correctly).
- `thesis/README.md` → `thesis/main.tex`: implied by the component description but no explicit hyperlink. A reader wanting to see the chapter orchestration must know to open `main.tex`.
- `thesis/README.md` → individual chapter files: not linked. The layout table describes each file but does not hyperlink to it.
- `thesis/README.md` → `docs/design/` source material: present via the `thesis/README.md` → `ADR-006` link; `ADR-006` itself links to most design docs.

**Broken link found**: `docs/PLACEHOLDERS.md §4.4` (line 204) contains:

```
\includegraphics{thesis/images/fig_<name>.png}
```

This path is wrong in two ways: (1) the actual `\includegraphics` calls use bare names (`fig_architecture`, not `thesis/images/fig_architecture`) because `\graphicspath{{images/}}` handles the prefix; (2) the extension is `.png` but the Mermaid CI step generates `.pdf` files. A first-time reader consulting PLACEHOLDERS.md to understand what screenshots to capture will receive incorrect path guidance.

**Fix**: Correct the `\includegraphics` path example in `docs/PLACEHOLDERS.md §4.4` to reflect the actual call pattern (`\includegraphics{fig_screenshot_azure_portal}`) and note that extensions are omitted (LuaLaTeX resolves `.pdf` before `.png`).

---

## Finding R-15 — Title-page TODO-rendered-as-text is acceptable as draft signal; resolution path is discoverable

**Severity: Nit (positive observation)**

**Location**: `thesis/chapters/00_title_page.tex`; `thesis/preamble.tex` lines 10–16; `thesis/README.md` lines 109–113.

**Observation**: This addresses review brief item 9. The title page renders `TODO TODO TODO` for institution, faculty, and program when compiled in stub form. The README's "Placeholders" section (lines 109–113) explains the macro pattern and directs the user to `preamble.tex` as the single-edit point. `docs/PLACEHOLDERS.md §4.1` gives a table with the exact macro name per field.

Assessment: the resolution path is discoverable if the reader consults `thesis/README.md`. However, the rendered PDF itself gives no hint — a reader who opens the CI-generated PDF artefact directly (without reading the README) sees only `TODO TODO TODO` on the cover with no explanation of how to fix it. Adding a `%TODO:` rendered string such as `(replace \thesisAuthor in preamble.tex)` in the PDF would be non-standard.

The more practical issue: the CI artefact name is `thesis_final-<sha>` — if the user shares this PDF with their advisor for early feedback, the advisor sees `TODO` on the cover and may not know this is intentional. A `\textit{[DRAFT — author field pending]}` rendering instead of bare `TODO` would be more professional while preserving the draftness signal.

This is a nit — the current approach is consistent with the project's placeholder discipline and the README makes the resolution clear.

---

## Finding R-16 — `refs.bib` type misuse: `vaswani2017` is tagged `@book` but is a conference paper

**Severity: Nit**

**Location**: `thesis/refs.bib` lines 36–42.

**Observation**: The Vaswani *et al.* "Attention Is All You Need" entry uses `@book` but the entry body has a `booktitle` field (NeurIPS proceedings) — this is the structure of `@inproceedings`, not `@book`. Biber will compile it without error, but the generated bibliography entry will be formatted as a book (publisher, etc.) rather than a conference proceedings entry. The `isbn` field is absent (correct for a proceedings paper), but `publisher` is also absent. The rendered entry will look unusual.

**Fix**: Change `@book{vaswani2017}` to `@inproceedings{vaswani2017}` and add `year = {2017}` in the correct field position.

---

## Finding R-17 — `paths:` trigger in CI includes `docs/diagrams/**` but not `thesis/images/` or `docs/PLACEHOLDERS.md`

**Severity: Nit**

**Location**: `.github/workflows/thesis-build.yml` lines 19–27.

**Observation**: The `paths:` filter for push + pull_request triggers is:

```yaml
paths:
  - "thesis/**"
  - ".github/workflows/thesis-build.yml"
  - "docs/diagrams/**"
```

This is correct and complete for the diagram-render → compile pipeline. A push to `docs/diagrams/architecture.mmd` alone will trigger a rebuild, as expected. A push that adds a new `docs/diagrams/deployment_pipeline.mmd` (the pending ADR-006 diagram from R-04) will also trigger correctly once the file exists.

The omission of `docs/PLACEHOLDERS.md` is acceptable — placeholder metadata changes do not affect the compiled output. The omission of `thesis/images/` is also acceptable — those files are gitignored anyway.

No action required; this is a nit confirming the trigger scope is appropriate.

---

## Summary table

| ID | Severity | Area | Title |
|---|---|---|---|
| R-01 | Critical | Clean-clone build | `thesis/build/` not created before `latexpand` in README |
| R-02 | Major | CI build | Page-count gate is `::warning` instead of failing the build |
| R-03 | Major | CI build | Puppeteer Chromium download not suppressed during `npm install` |
| R-04 | Major | Diagram rendering | Two ADR-006 pending diagrams have no source `.mmd` and no CI path |
| R-05 | Minor | Calibri fallback | CI comment claims "Calibri-equivalent" fonts but none are installed |
| R-06 | Minor | Reader experience | `.gitkeep` and PLACEHOLDERS.md do not enumerate all expected figure names |
| R-07 | Nit | Diagram rendering | Diagram name loop → `\includegraphics` mapping is correct (confirmed) |
| R-08 | Nit | Diagram rendering | Extensionless `\includegraphics` is fine; documents the resolution order |
| R-09 | Major | CI build | `latexmk` compiling from `build/` breaks `\graphicspath` resolution |
| R-10 | Major | CI build | `\lstinputlisting` relative paths break when compiled from `thesis/build/` |
| R-11 | Minor | Clean-clone build | README missing `mkdir -p build` before `latexmk` call (corollary of R-01) |
| R-12 | Minor | Bibliography | Placeholder `.bib` entries not visually/structurally distinct from final ones |
| R-13 | Nit | Reader experience | Chapter stubs are usable; subsection scaffolding would lower activation energy |
| R-14 | Minor | Reader experience | `docs/PLACEHOLDERS.md §4.4` has wrong `\includegraphics` path example |
| R-15 | Nit | Reader experience | TODO cover page is acceptable draft signal; resolution path is discoverable |
| R-16 | Nit | Bibliography | `vaswani2017` uses `@book` type instead of `@inproceedings` |
| R-17 | Nit | CI build | `paths:` trigger scope is correct and complete |

---

## Severity counts

| Severity | Count |
|---|---|
| Critical | 1 |
| Major | 5 |
| Minor | 5 |
| Nit | 6 |
| **Total** | **17** |

---

## VERDICT: CHANGES-REQUESTED

The bootstrap is well-structured and the chapter scaffolding demonstrates genuine pedagogical care. However, findings R-09 and R-10 together mean the CI-compiled PDF has blank figures and empty appendices — both invisible failures because LuaLaTeX in nonstopmode continues and produces a PDF file without aborting. The CI upload step will not catch this. An examiner downloading the workflow artefact receives a structurally incomplete PDF. R-01 ensures even the README-following local build fails before producing any output. These three Critical/Major findings must be resolved before the workflow can be trusted.

**Top 3 findings:**

1. **R-09 (Major) + R-10 (Major)** — Compiling `latexpand`-generated `thesis_final.tex` from `thesis/build/` silently drops all figures (broken `\graphicspath` resolution) and all appendix code listings (broken `\lstinputlisting` relative paths). The CI produces a PDF that passes the upload gate but has blank images and empty appendices. Fix: compile `main.tex` directly and use `-output-directory=build` for output placement only.

2. **R-01 (Critical)** — `thesis/build/` directory is gitignored and not created by the README commands, causing the archival build path to fail with a shell error on every clean clone. Fix: add `mkdir -p build` to the README's archival build section.

3. **R-02 (Major)** — The page-count gate emits `::warning` instead of failing the workflow when the thesis is below the 50-page guide minimum. The workflow comment says "assert ≥ 50" but the implementation does not assert — it advises. Fix: replace the warning with `exit 1`, optionally gated by a `$ALLOW_SHORT_PDF` environment variable for WIP iterations.
