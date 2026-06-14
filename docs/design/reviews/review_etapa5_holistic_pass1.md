# Etapa 5 holistic review — pass 1

**Reviewer**: code-reviewer (cross-component)
**Date**: 2026-05-16
**Verdict**: ACCEPT_WITH_CHANGES

## Summary

End-to-end the Etapa 5 deliverables (SWA frontend, `/api/ask` HTTP trigger,
Anthropic client wrapper, `safe_query` validator, BACPAC export timer, supporting
tests and docs) hang together: the contracts are typed at every layer
(`AskQuestion` → `AskAnswer` → `ValidationResult` → `_execute_validated_sql`),
the RLS contract from ADR-003 is honoured (user oid bound, MI is **not** bound
to user-facing SESSION_CONTEXT), and the prompt-cache anchor (`cache_control:
ephemeral` on the system block) is exercised by a unit test.

The story has three real cross-component gaps the team should fix before
declaring Etapa 5 done:

1. **SWA response-envelope drift (CR-01).** `/api/ask` returns
   `anthropic` and `objects_referenced` keys; the frontend ignores both and the
   `swa/README.md` "Backend contract" section advertises only five fields.
   Either trim the trigger's response or extend the SWA contract + UI.
2. **Refusal envelope is heterogeneous (CR-02).** A 200 response carries
   `answer/rows/row_count/source/latency_ms`; a 422 refusal carries
   `status/detail/anthropic`. The SWA toast handler renders the refusal as a
   transient toast (matching `renderError`) but the user loses the citation
   and any structured detail. The contract should be unified.
3. **`net_pnl_eur` decimal-precision drift (MA-01).** The cached schema in
   `prompts.py` documents `DECIMAL(18,2)`; `02_database_design.md §6.1` and
   §4 store these as `DECIMAL(18,4)`. The cached doc invalidates and re-pays
   the cache-write cost the next time someone notices and fixes it.

Beyond those, the rest of the checklist passes: validator allowlists agree
with `02_DB §6/§7/§8`, `pyproject.toml` and `function_app/requirements.txt`
pin the same `anthropic>=0.40`, `sqlglot>=23.0`, `httpx>=0.27`, `pydantic>=2.7`
floors, ≥ 20 adversarial prompts are covered by `test_safe_query.py`, BACPAC
unit tests mock the management/storage REST plane, and the integration test
exercises the full happy path with the Anthropic SDK mocked.

`staticwebapp.config.json` lives only in `function_app/` (matches the
README claim and the SWA build pipeline merge convention). `azure.yaml`
declares both `api` and `web` services. No real secrets in the diff.
English-only across artefacts.

## Critical

- [ ] **CR-01** | `function_app/triggers/ask.py:486-497`, `swa/app.js:349-390`,
  `swa/README.md:97-107` | The trigger returns
  `{answer, rows, row_count, source, latency_ms, anthropic, objects_referenced}`
  but the SWA contract advertises five fields and the frontend never reads
  `anthropic` or `objects_referenced`. | Token-usage and source-attribution
  telemetry that the trigger pays to compute is silently dropped by the UI;
  in a thesis demo you cannot show the cache-discount story without these
  fields. | Either (a) trim the production response to the five documented
  fields and emit `anthropic` only when `?debug=1` is set, or (b) extend the
  SWA contract + README + `renderAnswer` to surface the cache-read tokens
  (e.g., a small footer in the answer meta strip). Pick one and pin it in
  `swa/README.md`.

- [ ] **CR-02** | `function_app/triggers/ask.py:440-451` and `:456-469`,
  `swa/app.js:436-474` | Refusal (422) and SQL-validation-failed (422) bodies
  are `{status, detail, anthropic}` shaped; 200 bodies are
  `{answer, rows, row_count, source, latency_ms, anthropic, ...}` shaped.
  The SWA's `renderError` shows a generic "Question rejected: <detail>" toast
  and never appends a bot bubble. | The user loses the Romanian refusal
  reason (which the model spent tokens producing) once the toast auto-hides
  after 8 s, breaking UC-05/UC-12 demo flows where the model legitimately
  refuses. | Treat 422 as a first-class assistant turn: either return the
  refusal as a 200 with `answer = refusal_reason` and `row_count = 0`, or
  teach `app.js` to append a bot bubble for 422 specifically. Document the
  resolution in `swa/README.md` and add an integration-test assertion that
  exercises whichever shape wins.

## Major

- [ ] **MA-01** | `tcp/ai/prompts.py:115-119` vs `docs/design/02_database_design.md:406-408`
  (and §6.1) | The cached system prompt declares `gross_pnl_eur`,
  `commission_eur`, `net_pnl_eur` as `DECIMAL(18,2)`; the actual `fact_Trades`
  schema and `v_trades_enriched` use `DECIMAL(18,4)`. | A precision-aware
  LLM emitting `ROUND(net_pnl_eur, 2)` will produce truncated answers; worse,
  fixing this now invalidates the prompt cache for one round trip (5-minute
  TTL). | Bring the prompt body to `DECIMAL(18,4)` for the EUR columns. Land
  the change at a low-traffic window so the cache miss is cheap. Add a unit
  test that diffs the column list against a JSON fixture extracted from
  `02_DB §6.1` to prevent future drift.

- [ ] **MA-02** | `function_app/triggers/ask.py:226-247` | `_execute_validated_sql`
  uses `cursor.fetchmany(_MAX_FETCH_ROWS)` (1000) but the validator already
  clamps with `TOP MAX_ROW_LIMIT = 1000`. The two limits are not derived from
  the same constant. | A future bump of `safe_query.MAX_ROW_LIMIT` to 5000
  without touching the trigger would silently truncate at 1000 rows. | Import
  and reuse `safe_query.MAX_ROW_LIMIT` for the fetch cap (or vice versa),
  and add a unit test that asserts the equality.

- [ ] **MA-03** | `swa/app.js:333` | `safeReadDetail` reads `data.detail ||
  data.message || data.error`. The trigger consistently uses `detail` only;
  the fallbacks are dead code documented as "Pydantic/FastAPI-style" which
  Functions does not use. | Misleading code; future contributors will copy
  the wrong contract. | Drop the `message`/`error` fallbacks or document why
  they exist (link to an actual upstream code path). Lean preference: drop
  them.

- [ ] **MA-04** | `tcp/safe_query.py:243-253` | The `INTO` token is in the
  deny-list, but `INSERT INTO` legitimately needs `INTO` and the test suite
  relies on `INSERT` firing first because both keywords are denied. The
  comment in the code admits this is belt-and-braces. | The error message
  for `INSERT INTO v_foo VALUES (1)` will surface `INSERT` (good) but
  `INSERT(...)` is denied for the wrong reason from a telemetry standpoint
  if someone reorders the deny-list. | Hold the current ordering and add a
  unit test that asserts the marker is `INSERT` not `INTO` for `INSERT INTO`
  payloads, so a reorder triggers a clear failure.

- [ ] **MA-05** | `function_app/triggers/ask.py:193-218` |
  `_resolve_scope` uses `open_connection(bypass_session_context=True)`. The
  ADR-003 §4 escape hatch is gated to "infrastructure tasks". A user-driven
  HTTP path runs an admin-bypass connection on every `/api/ask` call. | The
  scope-lookup query is single-row by primary key and immediately closed,
  so the practical risk is minimal — but it is exactly the pattern
  CLAUDE.md asks us to document via ADR. | Open ADR-005 ("dim_UserRoles
  scope lookup uses bypass connection"), reference it from the docstring,
  and link from `03_architecture.md §3.2`. Already partially called out in
  the open-questions section below — promoting to MA because the docstring
  cites an ADR that does not exist yet.

- [ ] **MA-06** | `function_app/staticwebapp.config.json:8` | The AAD
  registration block hard-codes `<TENANT_ID>` as a placeholder. | Deploy
  pipeline will silently ship the literal string unless a postprovision
  hook rewrites it. | Confirm `infra/scripts/postprovision.{ps1,sh}`
  substitutes the placeholder, and add a unit test that fails if the
  string `<TENANT_ID>` survives into the published artefact. (Outside the
  Etapa 5 diff, but the holistic gap is real.)

## Minor

- [ ] **mi-01** | `tcp/ai/anthropic_client.py:284` | Token estimate uses
  `len(user_message) // 4`. The OpenAI/Anthropic 4-chars-per-token rule of
  thumb breaks on Romanian text with diacritics (è, ș, ț) that often
  tokenise to 2-3 tokens per character. | Estimate is permissive; an
  oversized question may still pass `max_input_tokens=2000` and only fail
  at the SDK boundary. | Document the heuristic in the docstring (already
  cross-referenced from CLAUDE.md). No code change required.

- [ ] **mi-02** | `swa/index.html:51-99` vs `01_BR §6 UC-04..UC-14` | The
  ten suggested questions are English; the demo language is Romanian. UC-05
  ("Top Earner Query") shows up as "Who was the top earner last week...".
  | English-first matches the project's "UI strings English" rule but the
  thesis demo is in Romanian; the demo script may want Romanian copies as a
  separate locale toggle. | Out-of-scope for v1.0. Note as future work.

- [ ] **mi-03** | `tests/integration/test_ask_endpoint.py:222-263` |
  `test_ask_happy_path` mocks `ask_claude` so the title is misleading — it
  is the "happy path with mocked Anthropic", not a true end-to-end call. |
  No correctness issue; only a naming concern. | Rename to
  `test_ask_happy_path_mocked_anthropic` and add a follow-up `test_ask_full_e2e`
  that hits the real API behind a `RUN_REAL_ANTHROPIC=1` env flag.

- [ ] **mi-04** | `tcp/ai/anthropic_client.py:191-234` | The `_EMIT_SQL_TOOL`
  schema has `additionalProperties: false` but Anthropic's tool API tolerates
  extra keys at runtime. | None; pure conservatism. | Keep as-is — the
  rejection at the host catches drift quickly.

- [ ] **mi-05** | `swa/style.css:74-80` (dark theme) | Dark theme tokens
  exist but the `index.html` body never opts into it. | None unless a user
  has `prefers-color-scheme: dark`. | Confirm visual QA on dark mode and
  capture a screenshot for the thesis appendix.

- [ ] **mi-06** | `tcp/safe_query.py:329-352` (test) | The `CHAR()`-encoded
  payload test ("either passes or fails — both are acceptable") is a
  documented limitation. | Already explicit in the test docstring. | No
  change; promote to "Open questions" section below for visibility.

- [ ] **mi-07** | `function_app/triggers/ask.py:107` |
  `json.dumps(payload, default=str)` will emit `Decimal(...)` as
  `'100.0000'` (string), then the SWA `formatCell` heuristic falls back to
  `String(value)` because the value is not a number. | EUR amounts may
  render as plain strings without `12.345,67 €` formatting. | Add a JSON
  encoder hook that coerces `decimal.Decimal` to `float` server-side, then
  the existing `looksLikeEurColumn` path applies.

- [ ] **mi-08** | `docs/design/ai_prompt_cache_contents.md:165-173` | Mentions
  `cache_control: {"type": "persistent"}` as an opt-in upgrade — at the time
  of writing Anthropic exposes 1-hour and ephemeral, not "persistent". |
  Documentation accuracy. | Reword to "longer TTL caches" without naming
  a flag that does not exist in the public SDK.

## Cross-component contract matrix

| Contract | Producer | Consumer | Status |
|---|---|---|---|
| HTTP 200 success envelope | `ask.py:486-497` | `app.js:renderAnswer` | Partial — extra `anthropic` / `objects_referenced` keys dropped silently (CR-01) |
| HTTP 422 refused envelope | `ask.py:440-451` | `app.js:renderError` (toast) | Partial — refusal shown as toast, citation discarded (CR-02) |
| HTTP 422 validation_failed envelope | `ask.py:456-469` | `app.js:renderError` (toast) | OK (`detail` exposed) |
| HTTP 401 / 403 / 404 / 500 envelopes | `ask.py:359-380, 411-422, 472-479` | `app.js:436-474` | OK |
| `AskQuestion → ask_claude → AskAnswer` | `ask.py:427` | `anthropic_client.ask_claude` | OK |
| `AskAnswer.sql → safe_query.validate → ValidationResult` | `ask.py:455` | `safe_query.validate` | OK — `validated.sanitized_sql` is what executes (`ask.py:238`) |
| `ValidationResult.sanitized_sql → cursor.execute` | `ask.py:238` | pyodbc | OK |
| `SessionContext(oid=user_oid) → connection_for_user` | `ask.py:233-235` | `tcp.db.connection_for_user` | OK — user OID, never MI OID |
| Scope lookup via bypass connection | `ask.py:195-213` | `tcp.db.open_connection(bypass=True)` | OK behaviour, but missing ADR (MA-05) |
| Anthropic token usage flow | `anthropic_client.AnthropicUsage` | `ask.py:_emit_metrics` + response | OK on logs, dropped at UI (CR-01) |
| `staticwebapp.config.json` location | `function_app/staticwebapp.config.json` | SWA build merge | OK (matches `swa/README.md:36-41`) |
| Allowlists vs `02_DB §6/§7/§8` | `safe_query.ALLOWED_*` | `prompts.SCHEMA_SYSTEM_PROMPT` | OK — 5 views, 9 dims (excl. dim_UserRoles), 2 procs, 5 functions all present |
| Cache-control anchor | `anthropic_client.py:302-308` | Anthropic prompt cache | OK — `test_system_block_carries_cache_control_ephemeral` |
| `azure.yaml` services | `azure.yaml:13-22` | `azd deploy api` / `azd deploy web` | OK — both `api` and `web` declared |
| `pyproject.toml` vs `requirements.txt` versions | both | both | OK — same floors for anthropic, sqlglot, httpx, pydantic |

## Field-name parity table

| Field | Producer (`ask.py`) | Consumer (`app.js`) | Match? |
|---|---|---|---|
| `answer` | `ask.py:488` | `payload.answer` `app.js:352-355` | OK |
| `rows` | `ask.py:489` | `Array.isArray(payload.rows)` `app.js:357` | OK |
| `row_count` | `ask.py:490` | `payload.row_count` `app.js:358-359` | OK |
| `source` | `ask.py:491` | `payload.source` `app.js:368-372` | OK |
| `latency_ms` | `ask.py:492` | `payload.latency_ms` `app.js:376-381` | OK |
| `anthropic` | `ask.py:493` | (none) | **Dropped by SWA (CR-01)** |
| `objects_referenced` | `ask.py:494` | (none) | **Dropped by SWA (CR-01)** |
| `status` (refusal/error) | `ask.py:446, 463, 477` | `safeReadDetail` checks `detail` only | OK (status is HTTP-coded) |
| `detail` (refusal/error) | `ask.py:447, 465, 478` | `app.js:333` | OK |
| `refusal_reason` (model) | `anthropic_client.AskAnswer.refusal_reason` | Surfaced via `detail` in refusal envelope | OK |
| `sql` (model) | `anthropic_client.AskAnswer.sql` | Validated, never returned to UI | OK |
| `citation` (model) | `anthropic_client.AskAnswer.citation` | Surfaced as `source` | OK |
| `answer_template` (model) | `anthropic_client.AskAnswer.answer_template` | Rendered server-side via `_render_answer` | OK |
| `usage.input_tokens` | `AnthropicUsage` | `anthropic.input_tokens` (dropped at UI) | Producer/consumer mismatch (see CR-01) |
| `usage.cache_read_tokens` | `AnthropicUsage` | `anthropic.cache_read_tokens` (dropped at UI) | Producer/consumer mismatch (see CR-01) |

## Open questions / known limitations

1. **CHAR()-encoded payload bypass** — `_DENY_TOKEN_PATTERNS` matches the
   literal tokens `UNION`, `INSERT`, etc. on the input string. An LLM
   reconstructing them via `CHAR(85)+CHAR(78)+CHAR(73)+CHAR(79)+CHAR(78)`
   inside a string literal survives the token scan. The AST walk then
   either treats it as an inert string predicate (safe) or — if used to
   reach a non-allowlisted table — is rejected by the table allowlist.
   Documented in `test_char_encoded_payload_passes_token_scan` (mi-06).
   No action required; called out for the thesis security section.

2. **`open_connection(bypass_session_context=True)` from a user path** —
   `_resolve_scope` opens an admin-bypass connection on every `/api/ask`.
   The lookup is single-row by primary key and the connection closes within
   milliseconds, but the pattern conflicts with the strict reading of
   ADR-003 §4 ("infrastructure tasks only"). MA-05 above tracks the ADR
   addition.

3. **5-minute prompt-cache TTL** — A 6-minute idle period flushes the
   ephemeral cache and the next request pays the full 3 500-token write.
   Demo sessions ≤ 30 questions over a continuous window stay warm; a
   lecture-room demo with long Q&A pauses will see one full cache write
   per pause. Surfaced in `ai_prompt_cache_contents.md §4`.

4. **`Decimal` JSON serialisation** — `json.dumps(default=str)` emits
   `Decimal` values as strings. The SWA's `formatCell` treats them as
   strings (no `Intl.NumberFormat`), so EUR cells in the result table will
   render as `100.0000` not `100,00 €`. Tracked as mi-07.

5. **Refusal envelope vs. assistant turn** — Open product question: is a
   model refusal a "failed request" (toast) or "a turn in the conversation"
   (bot bubble)? CR-02 above forces this decision.

6. **English vs Romanian UI** — Suggested-question copies are English by
   project rule; demo audience and `refusal_reason` are Romanian. The
   asymmetry is intentional but jarring; mi-02 above flags it for future
   work.

7. **No e2e Anthropic test** — Every integration test mocks `ask_claude`.
   The "real" path is exercised in development only. mi-03 tracks the
   naming fix and a follow-up `RUN_REAL_ANTHROPIC=1` test.

## Recommendation

ACCEPT_WITH_CHANGES. The architectural skeleton, contracts, and security
posture are sound. Fix CR-01 and CR-02 before the Etapa 5 close-out commit
(both are within an hour of work each). Land MA-01..MA-06 before the demo
rehearsal. Defer Minor items to Etapa 12 (final code-review pass) unless
the team has bandwidth before the convergence pass.

Suggested gate for Etapa 5 sign-off:

1. CR-01 resolved (decide debug flag vs UI surface; document the choice).
2. CR-02 resolved (decide refusal-as-turn vs refusal-as-toast; integration
   test asserts the chosen shape).
3. MA-01 resolved (prompt-cache content matches `02_DB §6.1`).
4. MA-02 resolved (`MAX_FETCH_ROWS` derived from `safe_query.MAX_ROW_LIMIT`).
5. MA-05 resolved (ADR-005 lands and is referenced from the scope-lookup
   docstring).
6. ADR-005 (`function_app/staticwebapp.config.json` `<TENANT_ID>`
   substitution mechanism) — MA-06 — verified in the post-provision script
   and asserted by a CI gate.

After these, a Pass-2 convergence review can confirm the holistic story is
demo-ready.
