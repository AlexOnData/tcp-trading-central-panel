# Review: Etapa-13 Bootstrap Deliverables

| Field | Value |
|---|---|
| **Reviewer** | docs-architect (Claude Sonnet 4.6) |
| **Date** | 2026-05-16 |
| **Branch** | `feat/azure-rewrite` |
| **Scope** | ADR-006, LaTeX skeleton, thesis stubs, refs.bib, CI workflow, PLACEHOLDERS.md §4 |
| **Verdict** | CHANGES-REQUESTED |
| **Counts** | 0 Critical · 4 Major · 7 Minor · 5 Nit |

---

## Methodology

The PDF was re-extracted with `pypdf` (installed into the system Python environment)
and compared line-by-line against every quantitative claim in ADR-006 §§1–9.
All LaTeX source files, the BibTeX bibliography, the CI workflow, and the
PLACEHOLDERS.md extension were read in full. Findings below are keyed with
`e13-MJ-NN` (Major), `e13-MI-NN` (Minor), `e13-NT-NN` (Nit).

---

## 1. ADR-006 Parsed-Requirements Accuracy

**Re-extracted PDF text** (Universitatea Transilvania din Brasov, FMI,
`Ghid_licenta_Informatica_.pdf`, 2 pages) confirms all structural claims.
Quantitative findings:

| Claim in ADR-006 | PDF text | Verdict |
|---|---|---|
| Min 50 pages total | "Minim 50 de pagini" | CORRECT |
| Min 40 pages text (no code/figures) | "minim 40 de pagini text fara a lua in calcul secvente de cod si imagini" | CORRECT |
| Font: 12pt UT Sans or Calibri, single spacing | "font 12 UT Sans sau Calibri la un rand" | CORRECT |
| LaTeX or Word | "elaborate in LaTeX sau Word" | CORRECT |
| Four mandatory chapter blocks | Introducere / Theory-technologies / Contributii personale / Concluzii | CORRECT |
| No minimum bibliography count | PDF is silent on a number | CORRECT |
| IAG bilingual: ToC, Intro, Conclusions, Future Work | "Cuprinsul, Introducerea, Concluziile si Perspectivele de dezvoltare" | CORRECT |
| IAG body: German or English | "Lucrarea va fi scrisa in limba germana sau engleza" | CORRECT |
| Turnitin max global 15% | "Procentul maxim de similitudine acceptat pentru lucrari este de 15%" | CORRECT |
| Turnitin max per-source 5% | "respectiv 5% dintr-o sursa" | CORRECT |
| Authority HS/24.04.2024 | "conform HS/24.04.2024" | CORRECT |
| Bonus: max 1 point without exceeding 10 | "maxim 1 punct suplimentar (fara a depasi punctajul maxim total de 10 puncte)" | CORRECT |
| Minimum 2 committee questions | "minim 2 intrebari" | CORRECT |
| 10 evaluation axes on practical application | PDF lists 10 items | CORRECT (count verified) |

**Summary**: Every quantitative claim in ADR-006 §§1–9 is correctly transcribed from the
PDF. No accuracy errors to report.

---

## 2. ADR-006 Decision Sanity

### D-1 (Program assumption — INFORMATICA default)

SOUND. The user has not stated IAG membership; defaulting to INFORMATICA is the
correct conservative choice. The IAG re-pass path is adequately described. The
conditional bilingual fallback is clear: four chapters need `\begin{otherlanguage}`
blocks if the disposition flips. IAG comment stubs are already in
`00_abstract.tex` and `08_conclusions.tex`.

### D-2 (Thesis language — English)

**e13-MJ-01 (Major) — D-2 language justification is weak for INFORMATICA.**

The guide is silent on body language for the INFORMATICA domain; ADR-006 interprets
silence as permission for English. This is defensible but the ADR does not document
the concrete risk: Romanian universities almost universally expect INFORMATICA theses
in Romanian unless there is a written faculty waiver. The ADR lists only one
mitigation ("a chapter-by-chapter translation pass without re-architecting the
skeleton") but does not name the trigger for that pass (advisor approval / written
waiver) or document that such approval was obtained or sought.

**Impact**: if the committee requires Romanian body prose at the defence, the thesis
requires a full translation pass across 55-60 pages — a major late-stage risk. The
abstract is already written in English prose (not a TODO stub), partially
pre-committing this decision.

**Required fix**: Add one sentence to D-2 noting that the user must confirm with their
advisor that an English body is accepted for their INFORMATICA cohort, and document
the confirmation outcome. If confirmation is not yet obtained, add it as a tracked
Etapa-13 prerequisite in STATE.md.

### D-3 (Chapter map)

SOUND. The eight chapters map cleanly onto the four guide-mandated blocks. The
four "Contributii personale" sub-chapters (Ch04 analysis, Ch05 implementation,
Ch06 testing, Ch07 results) correctly reflect the guide's emphasis on clearly and
structurally highlighting contributions. The separation scores better than a
monolithic contributions chapter.

### D-4 (Length target)

SOUND in intent but contains one implementation gap (see e13-MJ-02 below). The
55-60 page text target leaves meaningful buffer above the 40-page text floor.

**e13-MI-01 (Minor) — D-4 claims a text-page derivation script in CI that does not exist.**

ADR-006 D-4 states: "Length is enforced via `pdfinfo` + a `latex-pages-text`
derivation script in CI." The actual `.github/workflows/thesis-build.yml` contains
only a `pdfinfo` total-page check. No `latex-pages-text` derivation script exists.
Additionally, that check emits `::warning` rather than `::error`, meaning the
CI never blocks a merge on page count. This is appropriate for the stub phase but
should become an error before submission.

**Required fix**: Either implement the derivation script or remove the claim from
D-4. Document that the `::warning` flips to `::error` when the chapter prose is
complete (Etapa-13 closing pass).

### D-5 (Bibliography split)

SOUND on target distribution. Actual `refs.bib` delivers exactly the advertised
split: 5 books + 10 articles + 10 `@misc` docs + 5 `@misc` RFCs/standards = 30
entries. However there are three classification errors and five entries flagged as
needing replacement (see e13-MI-02 and e13-MI-03).

### D-6 (Formatting — LuaLaTeX + Calibri)

SOUND. The `\IfFontExistsTF{Calibri}` conditional with `TeX Gyre Heros` fallback
is the correct approach for CI builds where Calibri is not installed. The fallback
is detectably different (sans-serif vs roman proportions), which is the intended
behavior for proof-reading. The `report` class + `12pt` + `a4paper` matches the
guide requirements.

### D-7 (Turnitin strategy — ≤8% global / ≤3% per source)

SOUND. Targeting half the guide threshold (15%/5%) is a sensible academic safety
margin. The `\begin{quotation}` + ≤40-word direct-quote discipline is correctly
documented.

### D-8 (Diagrams — four reused + two thesis-specific)

SOUND. The four existing `docs/diagrams/*.mmd` files cover the load-bearing flows;
the two new thesis-specific diagrams (deployment pipeline, AI vs BI scope) are
scope-appropriate and not yet authored (correctly deferred).

### D-9 (Build pipeline)

PARTIALLY SOUND. The `latexpand` + `latexmk -lualatex` two-stage build is the
correct approach. However the mermaid-cli rendering step has a latent CI failure
mode (see e13-MJ-02), and the `latexpand` output path carries an undocumented
CWD-dependency risk (see e13-MI-04).

### D-10 (Placeholders)

**e13-MI-05 (Minor) — D-10 cites §6 but the thesis placeholders live in §4.**

ADR-006 D-10 states: "PLACEHOLDERS.md is extended with a new §6 enumerating the
thesis-specific placeholder set." The Consequences block repeats the §6 claim twice.
The actual implementation places the thesis placeholders in **§4** (Etapa-13 LaTeX
placeholders). The PLACEHOLDERS.md file has only 5 top-level sections; §6 does not
exist.

The `thesis/README.md` and `preamble.tex` comments correctly cite §4. The ADR
is the sole artefact with the wrong section number. The navigation graph closes
correctly at all other nodes.

**Required fix**: Replace every occurrence of "§6" in ADR-006 D-10 and the
Consequences block with "§4".

---

## 3. Discrepancy: "Babes-Bolyai" vs. Universitatea Transilvania

The ADR disposition is correct: Claude does not fill institution placeholders.
The ADR correctly documents both resolution paths (UNITBV or UBB) and the
consequences of each (same ADR stays valid vs. re-parse required).

**One improvement recommended (Nit)**:

**e13-NT-01 (Nit) — README.md institution placeholder wording is ambiguous.**

`README.md:7` currently reads something like `Faculty of Mathematics and
Informatics, "Babes-Bolyai" University`. PLACEHOLDERS.md §1.3 notes this is a
placeholder. The word "placeholder" appears inline but the line is not prefixed
with `TODO` so the `git grep` in §5 of PLACEHOLDERS.md would not catch it. The
`(placeholder; resolved in Etapa 12)` suffix is the only signal.

**Recommended action** (not blocking): the ADR disposition to defer is correct
per project rules. No change required to the README now. The §1.3 note in
PLACEHOLDERS.md adequately documents it.

---

## 4. PLACEHOLDERS.md §4 Extension

### Completeness gaps

**e13-MI-06 (Minor) — `\thesisCity` and `\thesisFaculty` missing from PLACEHOLDERS.md §4.1 table.**

`thesis/preamble.tex` defines 6 thesis-specific `\newcommand` entries that need
user resolution before submission:

```
\thesisAuthor    \thesisAdvisor    \thesisInstitution
\thesisFaculty   \thesisProgram    \thesisCity
```

`\thesisYear` is pre-filled (`2026`) and `\thesisTitle` is pre-filled with a
working title. The PLACEHOLDERS.md §4.1 table lists 6 rows but is missing
`\thesisCity` (university's host city — visible on the title page bottom line) and
`\thesisFaculty` (faculty name — visible on the title page header).

`thesis/README.md` correctly mentions both, but the canonical PLACEHOLDERS.md
index is the document the user walks at submission time and it omits them.

**Required fix**: Add `\thesisCity` and `\thesisFaculty` rows to the §4.1 table.

### Signature line discrepancy

**e13-NT-02 (Nit) — PLACEHOLDERS.md §4.3 claims the title page renders `\thesisSignatureLine` but it does not.**

§4.3 states: "The cover page renders a signature line via `\thesisSignatureLine{}`."
The macro is defined in `preamble.tex` but `thesis/chapters/00_title_page.tex`
never calls it. The signature line is therefore not rendered on the compiled cover
page in its current form.

**Impact**: Low for the bootstrap phase; the user signs the printed copy regardless.
But §4.3 is factually wrong and may confuse the user at submission time.

**Recommended fix**: Either call `\thesisSignatureLine` in `00_title_page.tex`
(add it to the author block) or correct §4.3 to state that the signature is
applied to the printed copy by hand and no LaTeX change is needed.

### Screenshot mismatch

**e13-NT-03 (Nit) — PLACEHOLDERS.md §4.4 cites `.png` extension; actual figures are `.pdf`.**

§4.4 states the LaTeX source references screenshots as
`\includegraphics{thesis/images/fig_<name>.png}`. The actual `\includegraphics`
calls in the chapter stubs use bare filenames without extension (e.g.,
`{fig_architecture}`), relying on `\graphicspath{{images/}}` to resolve. LuaLaTeX
prefers PDF for vector figures (which is what mermaid-cli produces). The `.png`
claim is only accurate for screenshots the user captures.

**Recommended fix**: Update §4.4 to say "diagrams are `.pdf` (generated by
mermaid-cli); screenshots may be `.png` or `.pdf` at the user's preference;
the `\graphicspath` resolves both automatically."

### Screenshot set mismatch

**e13-NT-04 (Nit) — Ch07 figure blocks (7) do not align with PLACEHOLDERS.md §1.8 screenshot list (8).**

`Ch07_results_demo.tex` has 7 `\includegraphics` figure environments:
`azure_portal`, `workbook`, `pbi_floor`, `pbi_team`, `pbi_trader`,
`swa_success`, `swa_refusal`.

PLACEHOLDERS.md §1.8 lists 8 captures: SWA empty state + SWA Q&A success + SWA
refusal + PowerBI Floor + Team + Trader Detail + AI Assistant link + workbook.

Two items from §1.8 are absent from Ch07: the SWA empty-state screenshot and the
PowerBI AI Assistant hyperlink screenshot. Ch07 has one item not in §1.8: the
Azure Portal resource group screenshot (a natural addition for the deployment
section). This is a minor catalogue drift that could confuse the user during the
capture pass.

**Recommended fix**: Either add `fig_screenshot_swa_empty` to Ch07 or note in
the §7.4 TODO comment that the SWA empty-state is not shown here. Update §1.8 to
include the Azure Portal screenshot, and decide whether the PowerBI AI Assistant
link warrants a separate figure or is described in prose.

---

## 5. LaTeX Skeleton Structure

### Chapter map

The `\input{}` directives in `thesis/main.tex` match ADR-006 D-3 exactly. All
ten chapter files and three appendix files exist. The ordering is correct.

### Front-matter ordering

The sequence:
title page → abstract → ToC → LoF → LoT → LoListings → body → bibliography → appendices

matches standard Romanian academic convention (Universitatea Transilvania and
equivalent). The abstract precedes the ToC, which is the expected UNITBV placement.

### Page-numbering

Roman numerals for front matter, Arabic for body — correct.

### `\lstlistoflistings`

Provided by the `listings` package (present in `preamble.tex`) — correct.

### `\cleardoublepage` in `oneside` mode

In `oneside` mode, `\cleardoublepage` is equivalent to `\newpage`. The five
consecutive `\cleardoublepage` calls between ToC, LoF, LoT, LoListings, and the
body add no blank pages (since there is no verso concept). This is acceptable
but slightly redundant. Nit only.

### `biblatex` + `biber` backend

The `alphabetic` style with `sorting=nyt` (name-year-title) is a sensible choice
for a computer-science thesis. The Romanian academic convention more commonly uses
numeric or author-year; alphabetic (e.g., [VA17]) is international-conference style.
This is a minor stylistic risk at the advisor's discretion.

---

## 6. Chapter Stub Quality

Each stub carries:
- A 1-3 line comment stating which guide block it maps to
- A target page range
- A per-section source-material reference (file + section)
- A per-section content outline

The stubs are informative enough for a writer to begin without re-deriving the
structure. The source-material cross-references are precise (file path + section
number where applicable). Content outlines have the right level of specificity:
not so granular that they prescribe prose, but specific enough to prevent scope
drift.

Two stubs are pre-written beyond TODO comments:

- `00_abstract.tex`: full English prose abstract (~250 words). This is a strength
  for the skeleton — it demonstrates the level of detail the rest of the thesis
  will target and serves as an authorial baseline for style and tone.
- `05_implementation.tex`: one actual `lstlisting` code block (the `derive_seed`
  function). This is a sound example of how inline listings will be used.

The appendix stubs correctly use `\lstinputlisting` against source files, which
prevents drift between thesis code and actual implementation. This is a notable
design strength.

---

## 7. `thesis/refs.bib` Analysis

### Count and type distribution

Actual count: **30 entries** — matches the advertised count exactly.

| Type | Target (D-5) | Actual | Match |
|---|---|---|---|
| `@book` (books / specialist works) | 5 | 5 | YES |
| `@article` (academic) | 10 | 10 | YES |
| `@misc` Microsoft / Anthropic / OWASP docs | 10 | 10 | YES |
| `@misc` RFCs / Standards | 5 | 5 | YES |

The split is correct. However there are entry-type classification errors and a
recency concern.

**e13-MJ-03 (Major) — Three entry-type misclassifications.**

1. `vaswani2017` — filed as `@book`. The "Attention Is All You Need" paper is a
   **conference paper** (NeurIPS 2017 proceedings), not a book. Correct type is
   `@inproceedings`. This affects how `biber` formats the citation.

2. `date2003` — filed as `@article` with `journal = {Pearson}`. C.J. Date's
   "An Introduction to Database Systems" is a **textbook**, correct type is
   `@book`. Using `@article` with a publisher name as the journal produces a
   malformed citation.

3. `rls2018` — filed as `@article` with `journal = {Microsoft Research Technical
   Report}`. This is a technical report, not a peer-reviewed article. Correct type
   is `@techreport`. The `note = {TODO: replace with actual MSR pub or remove}`
   acknowledges the issue, but the wrong BibTeX type means biber will omit the
   institution field and may suppress the year.

**Required fix**: Change `vaswani2017` to `@inproceedings`; `date2003` to `@book`;
`rls2018` to `@techreport` (or replace with a proper citation before submission).

**e13-MI-02 (Minor) — Five entries explicitly marked TODO for replacement; one is
technically unrelated to the thesis topic.**

The five TODO-tagged entries are clearly marked in `refs.bib` notes — good. Two
deserve specific attention beyond the generic "replace" instruction:

- `rfc9298` ("Proxying UDP in HTTP") — cited as a placeholder RFC but is entirely
  unrelated to the thesis. The note says "replace with thesis-relevant RFC (e.g.,
  OIDC Core)". The correct RFC for OIDC Core is RFC 8693 (Token Exchange) or
  the OIDC Core spec (OpenID Connect Core 1.0 — not an IETF RFC but an OpenID
  Foundation specification). This placeholder being a wrong-topic RFC is a
  concrete problem for committee review.

- `rls2018` — the Microsoft Research author attribution is unverifiable; no actual
  MSR report with that title/year exists in public databases. Should be removed or
  replaced before submission.

**e13-NT-05 (Nit) — Recency distribution: 13 of 30 entries predate 2021.**

The guide requires "recent and updated sources." Canonical historical references
(Turing 1936, Codd 1970, Kimball 2013, RFC 6749/7519) are justifiable as
foundational. However `date2003` (a 23-year-old textbook with a wrong entry type)
and `stonebraker2010` + `tigani2020` + `rls2018` (all marked TODO for replacement)
represent entries that are simultaneously stale and placeholder — they should be
replaced with current, relevant sources before the first prose pass.

---

## 8. `thesis/README.md` Adequacy

The README adequately covers:
- Build commands (incremental + archival + length-check + cleanup)
- Diagram rendering (four existing + two deferred)
- CI build reference
- Placeholder discipline with cross-link to `PLACEHOLDERS.md §4`
- Originality posture with the 8%/3% target vs. 15%/5% guide threshold

Cross-links are accurate (§4, not §6). The README mentions `\thesisFaculty` and
`\thesisCity` — even though PLACEHOLDERS.md §4.1 omits them — providing partial
mitigation for e13-MI-06.

One gap: the README does not mention the `biber` backend or the fact that
`latexmk -lualatex` automatically runs `biber` for bibliography passes. A user
unfamiliar with `biblatex`/`biber` may not know this and may debug a "missing
references" issue unnecessarily. Minor only.

---

## 9. `.github/workflows/thesis-build.yml`

### Structural soundness

- Trigger: `push` + `pull_request` on `thesis/**` + `docs/diagrams/**` — correct.
  `workflow_dispatch` is present — useful for manual re-triggers.
- Container: `texlive/texlive:TL2024-historic` — a real, pinned image. The pinning
  comment ("bump quarterly") is appropriate operational hygiene.
- Permissions: `contents: read` — minimal; correct for a build-only job.
- Artefact retention: 90 days — reasonable.

**e13-MJ-02 (Major) — mermaid-cli in container will likely fail silently due to
Puppeteer/Chromium `--no-sandbox` requirement.**

When mermaid-cli's bundled Puppeteer runs Chromium inside a Docker container
(the texlive container) as root, Chromium requires `--no-sandbox`. Without it,
Chromium typically exits with:

```
Running as root without --no-sandbox is not supported.
```

The workflow does not configure `--no-sandbox`. The rendering step uses
`|| echo "::warning::..."` which swallows the failure. This means:

- All four diagram renderings silently fail.
- The `thesis/images/` directory is empty of PDF figures.
- `\includegraphics{}` in the compiled thesis produces blank figure slots with
  "missing image" warnings (not errors by default in LuaLaTeX).
- The final `thesis_final.pdf` is uploaded successfully but with no diagrams.
- The CI reports green.

This is the most likely failure mode on a first CI run.

**Required fix**: Add a `puppeteer-config.json` at the repo root with
`{ "args": ["--no-sandbox", "--disable-setuid-sandbox"] }` and pass it to
`mmdc` via `mmdc --puppeteerConfig ../../puppeteer-config.json -i ... -o ...`.
Additionally, remove the `|| echo` soft-fail or add a check that at least one
diagram was generated (e.g., `ls thesis/images/fig_*.pdf | wc -l` asserting > 0).

### Page-count check severity

The `::warning` (not `::error`) on the page-count check is appropriate for the
stub phase. A comment in the workflow should note that this should be changed to
`exit 1` when the thesis prose is complete. This is currently undocumented.

### Chromium apt package name

`apt-get install -y chromium` is the correct package name on Debian (the base OS
of the texlive image). On Ubuntu 22.04+, this would be `chromium-browser` or the
snap package. Since the container is Debian-based, the package name is correct.

### PUPPETEER_EXECUTABLE_PATH placement

The env var is set at the step level for the install step — correct. It is also set
for the render-diagrams step — correct. The `npm install -g` command runs with the
env var active, which prevents Puppeteer from downloading its own Chromium binary
during install. This is the right pattern.

---

## 10. Cross-link Coherence Graph

| From | To | Status |
|---|---|---|
| `thesis/README.md` | `docs/PLACEHOLDERS.md §4` | CORRECT |
| `thesis/README.md` | `docs/decisions/ADR-006-thesis-structure.md` | CORRECT |
| `docs/PLACEHOLDERS.md §4` | `ADR-006 "Discrepancy flagged"` | CORRECT |
| `docs/PLACEHOLDERS.md §4.1` | Back-reference to §1.1/§1.2/§1.3 | CORRECT |
| `preamble.tex` comments | `docs/PLACEHOLDERS.md §4.1` | CORRECT |
| `00_title_page.tex` comment | `docs/PLACEHOLDERS.md §4.1` | CORRECT |
| **ADR-006 D-10** | **`docs/PLACEHOLDERS.md §6`** | **WRONG — actual is §4** |
| **ADR-006 Consequences** | **`docs/PLACEHOLDERS.md §6` (twice)** | **WRONG — actual is §4** |
| `ADR-006 References` | `docs/PLACEHOLDERS.md` (no section anchor) | ACCEPTABLE |
| `CLAUDE.md` | `Ghid_licenta_Informatica_.pdf` | CORRECT |
| `STATE.md` | Next = Etapa 13 | CORRECT |

The graph has one broken link (ADR-006 → §6) and is otherwise fully navigable.

---

## Complete Finding Register

| ID | Severity | Location | Title |
|---|---|---|---|
| e13-MJ-01 | Major | `ADR-006 D-2` | Thesis language (English) lacks advisor-confirmation trigger for INFORMATICA domain |
| e13-MJ-02 | Major | `.github/workflows/thesis-build.yml` | mermaid-cli Puppeteer --no-sandbox missing; diagram failures swallowed silently |
| e13-MJ-03 | Major | `thesis/refs.bib` | Three entry-type misclassifications (`vaswani2017`, `date2003`, `rls2018`) |
| e13-MI-01 | Minor | `ADR-006 D-4` + `thesis-build.yml` | Text-page derivation script claimed in D-4 but not implemented; page check is non-fatal warning |
| e13-MI-02 | Minor | `thesis/refs.bib` | `rfc9298` is topic-unrelated; `rls2018` has no verifiable source |
| e13-MI-03 | Minor | `thesis/refs.bib` | `biblatex alphabetic` style is non-standard for Romanian academic theses; advisor should confirm |
| e13-MI-04 | Minor | `thesis/appendix/A_code_snippets.tex` + `thesis-build.yml` | `\lstinputlisting{../tcp/...}` paths are CWD-dependent after `latexpand`; undocumented constraint |
| e13-MI-05 | Minor | `ADR-006 D-10` + Consequences | Section reference is §6 but actual PLACEHOLDERS.md section is §4 |
| e13-MI-06 | Minor | `docs/PLACEHOLDERS.md §4.1` | `\thesisCity` and `\thesisFaculty` macros absent from §4.1 table |
| e13-MI-07 | Minor | `thesis/README.md` | `biber` backend not mentioned; no note that `latexmk` runs it automatically |
| e13-NT-01 | Nit | `README.md:7` | Institution placeholder disposition (correct per project rules; no action needed) |
| e13-NT-02 | Nit | `docs/PLACEHOLDERS.md §4.3` | §4.3 says title page renders `\thesisSignatureLine` but it is not called |
| e13-NT-03 | Nit | `docs/PLACEHOLDERS.md §4.4` | §4.4 cites `.png` for figure paths; actual diagrams are `.pdf` |
| e13-NT-04 | Nit | `thesis/chapters/07_results_demo.tex` | Ch07 figure set (7) drifts from PLACEHOLDERS.md §1.8 screenshot list (8) |
| e13-NT-05 | Nit | `thesis/refs.bib` | 13/30 entries predate 2021; the 4 TODO-for-replacement entries are the highest-risk subset |

---

## Recommended Resolution Priority

**Before next prose commit (blocking)**:

1. `e13-MJ-02` — Fix mermaid-cli `--no-sandbox` in CI; the diagram rendering step
   must be verifiably functional before diagram-inclusive chapter prose is written.
2. `e13-MJ-03` — Fix `vaswani2017` → `@inproceedings`; `date2003` → `@book`;
   `rls2018` → `@techreport` or remove.
3. `e13-MI-05` — Correct §6 → §4 in ADR-006 D-10 and Consequences.
4. `e13-MI-06` — Add `\thesisCity` and `\thesisFaculty` rows to PLACEHOLDERS.md §4.1.

**Before first chapter prose draft (important)**:

5. `e13-MJ-01` — Obtain advisor confirmation on English body; record outcome in
   STATE.md.
6. `e13-MI-01` — Remove the text-page derivation script claim from D-4, or implement it.
7. `e13-MI-02` — Replace `rfc9298` with OIDC Core spec citation; remove or replace `rls2018`.

**Before submission (can defer)**:

8. `e13-NT-02` — Decide on signature line rendering.
9. `e13-NT-03` / `e13-NT-04` — Update PLACEHOLDERS.md §4.4; align Ch07 figure set.
10. `e13-NT-05` — Replace stale TODO-tagged bib entries with current sources.
11. `e13-MI-04` — Document CWD dependency in thesis/README.md compilation notes.

---

## Strengths

1. **PDF re-parse fidelity** — every quantitative ADR claim (15%/5% Turnitin, 50-page
   minimum, font specification, IAG bilingual trigger) is accurately transcribed.

2. **Pre-written abstract** — the English abstract with real prose is an unusually
   strong bootstrap deliverable; it establishes authorial voice and demonstrates
   the contribution surface before any body chapter is written.

3. **`\lstinputlisting` anti-drift pattern** — appendix code files are included
   from source via `lstinputlisting`, not copy-pasted. This is the correct
   architectural choice; appendix code will never drift from the implementation.

4. **Conditional Calibri fallback** — `\IfFontExistsTF{Calibri}` with a clearly
   different fallback font is the right design for a team where the CI host may
   not have Calibri installed.

5. **IAG comment stubs** — both `00_abstract.tex` and `08_conclusions.tex` carry
   commented-out IAG bilingual sections. These are zero-cost to maintain in stub
   form and valuable if the program disposition flips.

6. **Per-chapter source-material cross-references** — every section stub names the
   exact `docs/design/`, `docs/decisions/`, or source-code file that the writer
   should draw from. This is the right level of scaffolding for a document that
   will be written across multiple sessions and possibly by multiple agents.
