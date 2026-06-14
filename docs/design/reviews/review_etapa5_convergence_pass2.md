# Etapa 5 convergence review — pass 2

**Reviewer**: code-reviewer (verification pass)
**Date**: 2026-05-16
**Verdict**: ACCEPT

## Summary

Every pass-1 finding scoped for this convergence pass is verified
RESOLVED with code, tests, or ADR evidence: both Critical envelope-
divergence items, all eight ai-engineer Majors (MA-01 … MA-07), the
three security Majors (MJ-01 … MJ-03), the three python-pro Majors
(MJ-01 … MJ-03), and all six holistic Majors (MA-01 … MA-06) now have
load-bearing implementations backed by regression tests. Etapa 1-4
artefacts (`tcp/db.py`, `tcp/synth/*`, `db/migrations/*`,
`infra/*.bicep`) were not touched by this pass, no real secrets appear
anywhere in the diff, no new `|| true` masks were added in CI, the
SWA config file lives at `swa/staticwebapp.config.json` only (the
stale `function_app/` copy is removed), and both postprovision scripts
substitute the new path. English-only is maintained across all
committed artefacts. The body is demo-ready and clears the gate for
tagging `v1.0-mvp`.

## Pass-1 ID status table

| ID | Source | Severity | Status | Evidence |
|---|---|---|---|---|
| CR-01 | holistic | Critical | RESOLVED | `swa/app.js:374-400` `renderAnswer` builds `buildSourceCitation` (`Sources: <code>v_…</code>, …` footer driven by `objects_referenced`) and emits the Claude badge via `buildClaudeBadge` (`app.js:481-506`); README contract updated (`swa/README.md:104-135`). Token badge gated behind `isDebugMode()` (`app.js:74-82`) so the production UI stays clean while the cache-discount story is one query-string flag away for the thesis demo. |
| CR-02 | holistic | Critical | RESOLVED | All paths in `function_app/triggers/ask.py` route through `_envelope` (`ask.py:167-207`) which carries the unified `{status, answer, rows, row_count, source, latency_ms, anthropic, objects_referenced, error}` shape. SWA `app.js:330-340` branches on `payload.status` (`ok` / `refused` / `validation_error`) rather than HTTP code; `renderModelDecline` (`app.js:407-424`) renders refusal as a first-class bot bubble so the Romanian refusal text persists. Integration tests `_assert_envelope_shape` (`tests/integration/test_ask_endpoint.py:113-127`) pin all nine keys. |
| ai MA-01 | ai | Major | RESOLVED | `_normalise_for_denylist_scan` (`tcp/safe_query.py:458-487`) applies `unicodedata.normalize("NFKC", sql)` and rejects any character in Unicode `Cf`/`Cc` categories (except `\t\n\r`) with `DisallowedTokenError`. Pinned by `test_unicode_format_control_in_comment_is_rejected` and `test_nbsp_separator_does_not_break_comment_detection` (`tests/unit/test_safe_query.py:338-369`). |
| ai MA-02 | ai | Major | RESOLVED | The function-name pass in `_walk_and_validate` (`tcp/safe_query.py:669-687`) explicitly checks `fn_name_lower in _allowed_procs_lower` before the function-allowlist check and raises `DisallowedObjectError("procedure '…' must be invoked via validate_proc_call, not as an inline function")`. Pinned by `test_proc_invoked_as_function_is_rejected` (`test_safe_query.py:393-400`). |
| ai MA-03 | ai | Major | RESOLVED | `_STRING_LITERAL_RE` (`tcp/safe_query.py:445-455`) plus the literal-masking pass inside `_normalise_for_denylist_scan` replace `'…'` / `N'…'` content with same-length filler before the deny-list scan, so substrings like `'INTO'` inside a `WHERE` clause no longer fire the `INTO` token. Pinned by `test_into_inside_string_literal_is_allowed` (`test_safe_query.py:371-383`). |
| ai MA-04 | ai | Major | RESOLVED | `_walk_and_validate` calls `_enforce_row_limit(inner)` on every CTE body (`tcp/safe_query.py:585`). Pinned by `test_cte_inner_unbounded_is_capped` (asserts both `TOP 1000` and `TOP 10` appear in the sanitised SQL) and `test_cte_inner_above_max_raises` (`test_safe_query.py:402-429`). |
| ai MA-05 | ai | Major | RESOLVED | The trigger flow in `function_app/triggers/ask.py:505-540` validates `X-SWA-Forwarded` first (→ 403) and only then parses the principal header (→ 401). Integration test `test_ask_wrong_shared_secret_returns_403_first` (`test_ask_endpoint.py:135-153`) pins that a missing principal + bad secret returns 403, not 401. |
| ai MA-06 | ai | Major | RESOLVED | `ask.py:660-677` returns a generic `"The generated query was rejected by the safety validator."` on the wire; the detailed `error_class` / `error` / `sql_prefix` are logged via `structlog` only (`ask.py:664-669`). Pinned by `test_ask_validation_failure_returns_generic_422` which asserts `"DROP"`, `"fact_Trades"`, and `"DisallowedTokenError"` are absent from the response (`test_ask_endpoint.py:278-322`). |
| ai MA-07 | ai | Major | RESOLVED | `test_ask_malformed_payload_returns_400` parametrises over non-JSON body, missing-question key, and oversize question and asserts envelope status `bad_request` + one of the documented error codes (`test_ask_endpoint.py:207-238`). |
| sec MJ-01 | security | Major | RESOLVED | `swa/staticwebapp.config.json:36` CSP reads `"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"` — `unsafe-inline` is gone from both `script-src` and `style-src`, and `object-src`/`frame-ancestors`/`base-uri` close the suggested ancillary vectors. |
| sec MJ-02 | security | Major | RESOLVED | `_check_and_record_rate_limit` (`ask.py:280-311`) implements a sliding-window 10 req/60 s/OID counter under a `threading.Lock`. The trigger calls it at `ask.py:604` and returns 429 on exhaustion. Pinned by `TestRateLimit` (4 cases in `test_ask_trigger.py:80-115`) and `test_ask_rate_limit_returns_429_on_eleventh_request` (`test_ask_endpoint.py:324-367`). ADR-005 documents the single-instance residual risk. |
| sec MJ-03 | security | Major | RESOLVED | `ProcCallResult` dataclass (`tcp/safe_query.py:94-113`) returns `(sql, params: tuple[Any, ...])` ordered to match the `?` placeholders. Pinned by `TestValidateProcCall.test_param_order_matches_signature_not_input_dict` which passes the params dict in reverse insertion order and asserts the result tuple still matches spec order (`test_safe_query.py:530-538`). |
| py MJ-01 | python | Major | RESOLVED | `function_app/triggers/bacpac_export.py:30` imports `from collections.abc import Callable`; the `_poll_export` sleep parameter is annotated `Callable[[float], None] \| None` (`bacpac_export.py:280`). No `type: ignore[name-defined]` remains on that signature. |
| py MJ-02 | python | Major | RESOLVED | The function-name walk in `_walk_and_validate` is split into two passes (`safe_query.py:657-708`): pass 1 over `exp.Anonymous`, pass 2 over `exp.Func` with an explicit `isinstance(func, exp.Anonymous): continue` guard. The inline comment cites `py MJ-02`. |
| py MJ-03 | python | Major | RESOLVED | `_TcpJsonEncoder` (`ask.py:133-164`) handles `Decimal → float`, `datetime/date → ISO-8601`, `UUID → canonical string`, raising `TypeError` on unknown types. `_envelope` calls `json.dumps(..., cls=_TcpJsonEncoder)` (`ask.py:204`). Pinned by the five-case `TestTcpJsonEncoder` class (`test_ask_trigger.py:44-72`). |
| hol MA-01 | holistic | Major | RESOLVED | `tcp/ai/prompts.py:127-181` declares `gross_pnl_eur`, `commission_eur`, `net_pnl_eur`, `net_pnl_eur_total`, `cumulative_net_pnl_eur`, `gross_pnl_eur_total`, `commission_eur_total` as `DECIMAL(18,4)` — matching `02_database_design.md §6.1`. `test_pnl_columns_are_decimal_18_4` (`tests/unit/test_ai_prompts.py:18-48`) iterates the seven columns and asserts each line carries `DECIMAL(18,4)` and never `DECIMAL(18,2)`. |
| hol MA-02 | holistic | Major | RESOLVED | `ask.py:97` defines `_MAX_FETCH_ROWS: Final[int] = safe_query.MAX_ROW_LIMIT`. Pinned by `test_max_fetch_rows_matches_safe_query_constant` (`test_ask_trigger.py:123-125`) which asserts equality at runtime. |
| hol MA-03 | holistic | Major | RESOLVED | `swa/app.js:556-561` `renderHttpError` reads `payload.error.message` directly with no `data.detail || data.message || data.error` fallback chain. Inline comment cites `holistic MA-03 — no Pydantic-style fallbacks`. |
| hol MA-04 | holistic | Major | RESOLVED | `test_insert_into_marker_is_insert_not_into` (`test_safe_query.py:385-391`) asserts both that `"INSERT"` appears in the error message and that the marker is not the more generic `"INTO"` — so a future reorder of `_DENY_TOKEN_PATTERNS` that put `INTO` ahead of `INSERT` would fail this test. |
| hol MA-05 | holistic | Major | RESOLVED | `docs/decisions/ADR-005-scope-resolution-rls-bypass.md` (130 lines) documents the bypass with safety arguments, alternatives, and consequences. `_resolve_scope` docstring at `ask.py:319-336` references the ADR by name. |
| hol MA-06 | holistic | Major | RESOLVED | `swa/staticwebapp.config.json` is the only copy (the `function_app/` copy is deleted per `git diff HEAD --stat`). Both `infra/scripts/postprovision.ps1:172-187` and `infra/scripts/postprovision.sh:151-177` resolve the path as `$repoRoot/swa/staticwebapp.config.json` and substitute `<TENANT_ID>` + `<value-set-by-postprovision>` before deploy. The `.sh` form uses an inline `python3` block to preserve JSON escaping. |

## Regressions

None detected.

- `tcp/db.py`, `tcp/synth/*`, `db/migrations/*`, `infra/main.bicep`, and `infra/modules/*.bicep` are all untouched by this pass (`git diff --stat HEAD` lists only Etapa-5 files plus the SWA config relocation).
- The `_DENY_TOKEN_PATTERNS` ordering with `INSERT` ahead of `INTO` is preserved and locked by `test_insert_into_marker_is_insert_not_into` so a reorder would fail loudly.
- `_resolve_scope` continues to use the documented `bypass_session_context=True` escape hatch from `tcp/db.py`; the ADR captures the contract and no behaviour drift was introduced.
- `staticwebapp.config.json` content matches Etapa-4 contract except for the CSP tightening (intended) and the path move (intended); both `<TENANT_ID>` and `<value-set-by-postprovision>` placeholders survive in the repo copy and are substituted at provision time exactly as before.
- No new `|| true` masks were added in CI — the single pre-existing one at `.github/workflows/ci.yml:236` is the informational `bicep what-if` job inherited from Etapa 4 and is explicitly justified in a code comment.
- No real secrets surfaced (only test placeholders like `sk-ant-test` and the bash export examples in test runbook prose).

## Remaining gaps

All convergence-scoped items are RESOLVED. The following pass-1 Minors
are deliberately deferred (none of them are blockers for the
`v1.0-mvp` tag):

- **ai MI-04** — short OID suffix already uses `oid.hex[-8:]` (`ask.py:542`); this minor was already addressed before the convergence pass.
- **ai MI-08** — `_parse_principal_header` defensive logging at each return is not added; the four `return None` paths still map to a single 401 envelope. Low-value operational nit; defer to Etapa 8.
- **py MN-01 … MN-10** — small typing / fixture / regex tightening items, none security-critical, all left for the Etapa-12 final review sweep.
- **hol mi-01 … mi-08** — token-estimate heuristic, English vs Romanian suggested-question copy, integration-test naming. None block the demo.
- **sec MN-01, MN-02, MN-03** — MN-02 (the `build_user_message` scope guard) is already addressed via `_VALID_SCOPES` (`prompts.py:32-42` + `prompts.py:315-318`); MN-01 is addressed via the generic 422 message (ai MA-06 fix); MN-03 is addressed via the `_log.debug("…api_error_body", body_snippet=…)` pattern at `bacpac_export.py:253-257` and `:299-303`. These three Minor items are RESOLVED as a side-effect of the Major fixes, not because they were individually called out as in-scope for the convergence pass.

## Recommendation

ACCEPT. The Etapa-5 deliverable is converged: every Critical and every
high-value Major from the four pass-1 reviews has been addressed with
load-bearing code and at least one regression test per fix, the
architecture invariants (RLS contract, validator fail-closed posture,
prompt-cache anchor, secrets discipline) are intact, no Etapa 1-4
artefact was disturbed, and the residual Minor list is purely
hardening polish for the Etapa-12 final pass. Proceed to tag
`v1.0-mvp` and open Etapa 6 (SWA deployment + production smoke).

## Verification stats

- Resolved: **22** (CR-01, CR-02, ai MA-01..07, sec MJ-01..03, py MJ-01..03, hol MA-01..06)
- Partially resolved: **0**
- Not resolved: **0**
- Regressions: **0**
