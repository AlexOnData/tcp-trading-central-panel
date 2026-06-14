# Etapa 12 — Convergence Pass-2

| Field | Value |
|---|---|
| **Date** | 2026-05-16 |
| **Branch** | `feat/azure-rewrite` |
| **Stage** | Etapa 12 — Documentation polish + placeholders consolidated |
| **Verdict** | **APPROVED FOR MERGE** |
| **Pass-1 reviewers** | docs-architect + code-reviewer (light-touch, 2 reviewers — matches the STATE.md plan for the small E12 surface) |
| **Convergence baseline** | mypy strict clean on 47 source files + 288 tests passing — preserved |

---

## Pass-1 verdicts

| Reviewer | Verdict | Critical | Major | Minor | Nit |
|---|---|---|---|---|---|
| docs-architect | CHANGES-REQUESTED | 0 | 1 | 1 | 2 |
| code-reviewer | APPROVED FOR MERGE | 0 | 0 | 4 | 3 |
| **Combined** | **CHANGES-REQUESTED** | **0** | **1** | **5** | **5** |

The two reviewers found **0 Critical** + **1 Major** + **5 Minor** + **5 Nit** (one Nit per-reviewer was a borderline cross-reference that resolved on inspection — see `code12-NIT-03` disposition below).

---

## Disposition

### Major (1 — all RESOLVED)

| ID | Reviewer | Description | Disposition | Fix |
|---|---|---|---|---|
| doc12-MJ-01 | docs-architect | `docs/design/03_architecture.md:208` `TODO: file ADR-XXX-cicd-role-split post-thesis` not catalogued in `PLACEHOLDERS.md §2`. The PLACEHOLDERS preamble guarantees *"If something is `TODO` in the repo and is not listed here, that is a bug."* | **RESOLVED** | Added one row to `PLACEHOLDERS.md §2` describing the post-thesis OIDC SP role-split as out-of-scope work justified by the single-engineer build profile. |

### Minor (5 — all RESOLVED)

| ID | Reviewer | Description | Disposition | Fix |
|---|---|---|---|---|
| doc12-MN-01 | docs-architect | `ai_prompt_cache_contents.md §7:214` says "67 DAX measures" — canonical figure (corrected in E9 convergence, reflected in `README.md:23,79`) is 69. | **RESOLVED** | Changed `67` → `69` on the one line; verified against `powerbi/model/tables/_Measures.tmdl` (69 `measure` declarations). |
| code12-MI-01 | code-reviewer | `docs/PLACEHOLDERS.md §1.1–§1.5` cite stale `README.md` line numbers (off by +6 for §1.1–§1.4 author/advisor/institution/defence; off by +1 for §1.5 license). Caused by the same-commit README author-block rewrite outpacing the index. | **RESOLVED** | Switched the README cross-references in §1.1–§1.5 from line-numbered to **section-anchor** links (`README.md#author-and-advisor`, `README.md#license`). Anchors are reflow-stable; the README sections themselves are named for the exact placeholder semantic, so the navigation intent is preserved while the brittleness is removed. |
| code12-MI-02 | code-reviewer | `infra/main.bicep:72` `@description` carries `TODO` substrings (`'... is a TODO placeholder ...'`, `'... is a TODO until ...'`) that PLACEHOLDERS.md does not catalogue. Reviewer's option (b) — rewrite the @description to drop the TODO words and reference PLACEHOLDERS.md instead. | **RESOLVED** | Rewrote the @description: `Resource tags applied to every resource. The 'owner' and 'repo' default values are intentional placeholders enumerated in docs/PLACEHOLDERS.md §1.6 (repo URL) and §1.7 (owner tag); the user resolves both at thesis submission and re-runs azd provision to propagate the new tags.` The `owner: 'TODO'` + `repo: 'TODO'` values (the actual placeholders, on lines 76 and 79) remain — those are still catalogued in §1.6 / §1.7 of PLACEHOLDERS.md. Only the *description* now reads cleanly. |
| code12-MI-03 | code-reviewer | `_ALLOWED_SCOPES: Final[frozenset[str]] = ALLOWED_SCOPES` in `ask.py:117` undercuts the "single source of truth" claim — the *value* is canonical but the *name* duplicates `ALLOWED_SCOPES` (line 69) and `_ALLOWED_SCOPES` (line 117). Reviewer's recommended fix: drop the alias entirely; rename the single call site at line 372. | **RESOLVED** | Dropped the alias. The single call site at line 372 now reads `return scope if scope in ALLOWED_SCOPES else None` (one-character diff). The trailing inline comment near line 660 was updated to match. The 5-line "Etapa-12 consolidation" comment block at lines 113–117 was rewritten to state the new single-source-of-truth contract clearly. |
| code12-MI-04 | code-reviewer | FX/cost-constant coupling notes in `alerts.bicep` ⇄ `kusto/03_*.kql` are reciprocal and well-written but lack a CI guard; the "MUST be mirrored in same commit" handshake is convention-only. Reviewer's suggested fix: add ~10 lines of shell to `ci.yml` that greps both files and asserts the four constants share identical literals. | **ACCEPTED RESIDUAL — Etapa 13 polish** | The reviewer themselves classified this as Minor (not Major) on the basis that Etapa 13 is documentation-only and the workbook tile reads the .kql side so drift surfaces within hours of any deploy. The structural justification for the duplication (ARM `scheduledQueryRules` rejects multi-line query literals → `loadTextContent` unusable) is sound; the coupling notes are reciprocal and dated. A CI guard is the natural Etapa-13 follow-up. **Not implementing here** to keep the E12 surface small and avoid pulling CI workflow edits into a polish stage. |
| doc12-NT-02 | docs-architect | `PLACEHOLDERS.md §5` resolution-checklist grep pattern misses bare `TODO` entries in the three security-doc version-history *Author* columns (`credentials_rotation.md:461`, `threat_model.md:344`, `incident_response.md:504`). | **RESOLVED** | Added a blockquote-formatted note below the grep block enumerating the three bare-`TODO` lines that the regex does not match, with the broader `git grep -nE "\bTODO\b"` fallback pattern and instructions to cross-reference each hit against the §1/§2/§3 disposition. Kept the original narrower regex (no behaviour change) so any operator who already memorised it is not surprised. |

### Nit (5 — 4 RESOLVED, 1 verified-clean)

| ID | Reviewer | Description | Disposition | Fix |
|---|---|---|---|---|
| doc12-NT-01 | docs-architect | `ai_prompt_cache_contents.md §7:235-238` directional error — text says "the SWA chat UI surfaces a hyperlink visual to the AI Assistant page in PowerBI", but the actual direction (per `powerbi/README.md` + Etapa-7 deliverables) is reversed: the **PowerBI** AI Assistant page carries the hyperlink to the SWA. | **RESOLVED** | Rewrote the two-sentence passage to state the correct direction explicitly, and called out the architectural rationale (the Etapa-6 `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'` blocks any iframe embed, so the hyperlink is the only viable in-PowerBI-report pivot to the SWA assistant). |
| code12-NIT-01 | code-reviewer | The new Etapa-12 `freezegun` removal comment block in `pyproject.toml:46-50` sits flush against the `sqlfluff` pin discussion on the next line; a future reader might mistake the freezegun comment for a comment *on* the sqlfluff dep. | **RESOLVED** | Added a single blank comment line (`#`) separator between the two comment blocks. The freezegun comment now visually terminates at line 51; the sqlfluff comment starts at line 52. |
| code12-NIT-02 | code-reviewer | `ai_prompt_cache_contents.md §7` table uses unicode `✓` (U+2713) and `✗` (U+2717) — harmless in markdown but a LaTeX-render risk for the Etapa-13 thesis chapter where font-fallback may produce tofu glyphs. | **RESOLVED** | Replaced all unicode marks with `Yes` / `No` / `Partial`. Same semantics, ASCII-only, LaTeX-safe across any document class. |
| code12-NIT-03 | code-reviewer | `docs/PLACEHOLDERS.md:162` (§3 row) references `.github/workflows/cd.yml:239` — reviewer flagged as worth spot-checking given the README line-number drift pattern. | **VERIFIED — NO FIX NEEDED** | Re-read `.github/workflows/cd.yml:235-244` directly: line 239 is exactly the `grep -Eq "__V[0-9]+_CHECKSUM__\|TODO-checksum-set-by-CI\|sentinel-no-checksum-supplied"` invocation. Reference is current; nothing to fix. |
| doc12-NT-02-supplement | docs-architect | Same as `doc12-NT-02` above; the docs-architect filed this once as a Nit and the code-reviewer did not double-count. Listed here for tracking completeness. | (rolled up into doc12-NT-02 above) | — |

---

## Convergence summary

- **1 Major RESOLVED** (doc12-MJ-01).
- **4 Minors RESOLVED** (doc12-MN-01, code12-MI-01, code12-MI-02, code12-MI-03) + 1 Minor ACCEPTED RESIDUAL (code12-MI-04 — FX CI guard, deferred to Etapa 13).
- **4 Nits RESOLVED** (doc12-NT-01, doc12-NT-02, code12-NIT-01, code12-NIT-02) + 1 Nit verified-clean (code12-NIT-03 — line ref already correct).
- **0 regressions**: post-convergence `uv run mypy tcp tests scripts function_app` → `Success: no issues found in 47 source files`; `uv run pytest tests/unit tests/integration/test_telemetry_no_pii.py` → `288 passed in 5.01s`.

**Eight consecutive clean / near-clean convergence verdicts (E5..E12).**

---

## Files touched during convergence pass-2

- `docs/PLACEHOLDERS.md` — added §2 row for `03_architecture.md:208`; switched §1.1–§1.5 README cross-refs from line-numbered to section-anchor links; added §5 grep-pattern note.
- `docs/design/ai_prompt_cache_contents.md` — `67 DAX measures` → `69 DAX measures`; rewrote SWA↔PowerBI direction passage; replaced unicode `✓`/`✗` with `Yes`/`No`/`Partial`.
- `infra/main.bicep` — rewrote the `tags` `@description` to reference `PLACEHOLDERS.md §1.6 / §1.7` instead of embedding bare `TODO` words.
- `function_app/triggers/ask.py` — dropped the `_ALLOWED_SCOPES: Final[frozenset[str]] = ALLOWED_SCOPES` alias; renamed the call site at line 372 to use `ALLOWED_SCOPES` directly; updated the matching comment near line 660.
- `pyproject.toml` — added a single-line comment separator between the Etapa-12 freezegun removal block and the sqlfluff pin discussion.

No changes to: `tcp/ai/anthropic_client.py` (the `Scope` + `ALLOWED_SCOPES` exports were already canonical), `infra/observability/kusto/03_anthropic_tokens_and_cost.kql` (coupling note already correct), `infra/modules/alerts.bicep` (coupling note already correct), `README.md` (the line-anchored back-references already use section anchors), `docs/glossary.md` (link already current).

---

## Recommendation

**APPROVED FOR MERGE.** All non-deferred findings resolved with no regressions. The single accepted residual (code12-MI-04 — FX coupling CI guard) is a documented Etapa-13 follow-up and does not block the Etapa-12 close.

The `v1.0-mvp` tag at the end of Etapa 10 remains valid; Etapa 12 lands on top as a documentation-polish head with no re-tag needed. Etapa 13 (LaTeX thesis) can now begin against this baseline.
