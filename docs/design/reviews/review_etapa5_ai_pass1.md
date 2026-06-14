# Etapa 5 AI assistant review — pass 1

**Reviewer**: ai-engineer
**Date**: 2026-05-16
**Verdict**: ACCEPT_WITH_CHANGES

## Summary

The Etapa-5 AI body is solid against the production contract: the Anthropic
client pins `claude-haiku-4-5` + `temperature=0.0`, attaches
`cache_control: {"type": "ephemeral"}` on the schema block, forces
`tool_choice` to `emit_sql`, and parses the typed envelope (refusal path
included). `safe_query.py` is appropriately fail-closed with a broad token
deny-list that fires before sqlglot parsing, an AST-walk allowlist for
views/dims/funcs/procs, multi-statement rejection, comment denial, `TOP`/
`FETCH NEXT` clamping at 1000, and a documented PII firewall on the
prompt. The 22-row adversarial fixture exceeds the §6.4 ≥ 20 requirement.
The `ask.py` trigger wires headers → scope → Anthropic → validate → RLS
execute → render correctly with non-leaky error envelopes. Issues found
are mostly Major (parser-edge bypasses in the deny-list and a few
integration-test gaps) and Minor (telemetry naming + docs). No Critical
finding blocks merge, but the Major items should land before tagging
`v1.0-mvp`.

## Critical

(none)

## Major

- [ ] **MA-01** | `tcp/safe_query.py:202` | The `--` comment regex is unanchored — it also fires on bare `-` arithmetic / negative-literal expressions that happen to be adjacent, but more importantly the **regex is bypass-able with whitespace**: a payload like `SELECT 1 - - DROP ...` (NBSP between hyphens) or any Unicode minus-like char will sneak past the literal `--` match while T-SQL still treats `--` as a line comment after sqlglot reserialisation. | Defense in depth on comments is the highest-value smuggling vector per the module docstring; a bypass here re-opens multi-statement smuggling. | Run the deny-list pass against the **sqlglot-reserialised** string in addition to the raw input; or add a Unicode-normalisation step (`unicodedata.normalize("NFKC", sql)`) before the regex scan, and assert no characters in `Cf`/`Cc` categories remain.
- [ ] **MA-02** | `tcp/safe_query.py:571-598` | `_walk_and_validate` iterates `exp.Anonymous, exp.Func` but does **not** validate stored-procedure call expressions emitted as `exp.Command` or `exp.Anonymous` with name like `dbo.usp_*`. The earlier `find_all(exp.Command)` skip + the deny-listed `EXEC` keyword catches the simple shape, but a Common Table Expression that wraps `SELECT * FROM dbo.usp_GetEmployeePerformance(...)` (T-SQL TVF-style call) bypasses both because: (a) `EXEC` is absent, (b) `dbo.usp_GetEmployeePerformance` is in `ALLOWED_PROCS` but is **not** a TVF and would fail with a real error — but the validator currently returns `ValidationResult` because it treats `usp_GetEmployeePerformance` as an allowed function via the `ALLOWED_FUNCTIONS` fallback path. | The procs are not allowlisted as inline functions and the validator should refuse, not let it through to the SQL engine. | Tighten the function-name pass: if the anonymous function name matches `ALLOWED_PROCS` (case-insensitive), raise `DisallowedObjectError("procs must be invoked via validate_proc_call")`.
- [ ] **MA-03** | `tcp/safe_query.py:240-245` | The `INTO` deny token rejects the legitimate `SELECT ... GROUP BY ... HAVING SUM(x) INTO @var` form, **but** also blocks any future `STRING_AGG(x, ',') WITHIN GROUP (ORDER BY y)` style of safe SELECT that happens to mention `INTO` in a quoted literal — the regex is word-boundary-anchored but `\bINTO\b` still fires inside an N-string content (`N'Categoria INTO ...'`). | Risk of false refusals on legitimate Romanian-language string literals (e.g., a `WHERE comment LIKE '%intoarcere%'` clause containing `INTO` inside the substring would be cut by word-boundary, but `'INTO câștig'` literal is rejected). | Tokenise string literals out before the regex scan (`sqlglot.tokenize`) or move `INTO` detection to the AST-walk pass that already inspects `select.args.get("into")` — the AST check is precise and the token version is redundant once the AST one is in place. Minimum: add a unit test pinning the false-positive surface and a code comment.
- [ ] **MA-04** | `tcp/safe_query.py:602-640` | `_enforce_row_limit` injects `TOP MAX_ROW_LIMIT` for queries that have **no** limit, but for queries with `ORDER BY ... OFFSET 0 ROWS FETCH NEXT 50 ROWS ONLY`, the resulting sanitised SQL still has the small `FETCH NEXT` — yet a subsequent `WITH cte AS (SELECT ...)` outer SELECT that lacks its own `TOP` would receive `TOP 1000` only at the **outer** level. Tests confirm only the outer-level injection. Inner CTE bodies that the LLM might write as `WITH cte AS (SELECT * FROM v_trades_enriched) SELECT TOP 10 ...` will materialise the full unbounded CTE in tempdb before the outer `TOP 10` applies. | On Azure SQL Free (1 vCore), an unbounded inner CTE on `v_trades_enriched` (60-90k rows projected at year-end) is a soft DoS risk. | Either inject `TOP MAX_ROW_LIMIT` on every CTE inner SELECT as well, or document the trade-off explicitly in §6.4 and add a `pyodbc` query-timeout (15 s) on the user-driven cursor. The cursor timeout fix is the easier of the two.
- [ ] **MA-05** | `function_app/triggers/ask.py:386-406` | The 400 `bad_request` path is not in the documented status list (§3.2 lists 401/403/404/422/500). Three branches return 400: invalid JSON, missing/non-string question, and over-500-char question. The `_validate_forwarded_secret` check returns 403 *before* the principal is parsed — but the principal-header presence check (line 359) returns 401 first, which means a request with no headers gets 401 even when the secret is also missing. Order matters for §8.2 bullet 4 ("the function rejects any request lacking the matching value with HTTP 403, regardless of whether the `x-ms-client-principal` header is well-formed"). | The architecture spec explicitly says the 403 path applies regardless of principal-header well-formedness. Current ordering returns 401 first if both are missing. | Swap the order: `_validate_forwarded_secret` first → 403 if missing/mismatched; then `_parse_principal_header` → 401 if absent/malformed. Update the spec or the code, but they must agree. Recommend updating the code to match §8.2.
- [ ] **MA-06** | `function_app/triggers/ask.py:465-468` | The 422 validation-failed body leaks the raw `SafeQueryError` message via `f"{type(exc).__name__}: {exc}"`. For an LLM that emitted `SELECT * FROM dim_UserRoles`, the response body becomes `"DisallowedObjectError: table or view 'dim_UserRoles' is not in the allowlist"` — which **discloses the existence of the RLS-metadata table to an unauthenticated-since-403-bypassed attacker**. The body should mention the failure class without echoing the LLM payload back. | Information disclosure of internal table names is a low-severity leak but a real one — the architecture's §8.3 threat row "Determined attacker bypassing SWA + forging principal header" cites the shared-secret as the only mitigation, and a chatty 422 body amplifies the attack surface if the attacker ever crosses the 403 boundary. | Return `{"status": "validation_failed", "detail": "SQL validation rejected the model output", "reason_class": type(exc).__name__}` and log the full message + offending SQL via `structlog` only.
- [ ] **MA-07** | `tests/integration/test_ask_endpoint.py` | The checklist requires a "malformed payload" integration case. The current file has the 6 documented cases (missing principal 401, wrong secret 403, unknown OID 404, refused 422, validation-failed 422, happy-path 200, RLS-scope 200) but **no test for `body=None` or `body={"question": ""}` returning 400**. The 400 branch in `ask.py:386-406` is untested in integration. | The audit checklist explicitly lists "malformed payload" as one of the 7 required integration cases. | Add `test_ask_malformed_payload_returns_400` covering: (a) non-JSON body, (b) JSON with no `question` key, (c) `question` ≥ 501 chars. Three parametrised cases satisfy the requirement.
- [ ] **MA-08** | `function_app/triggers/ask.py:317-326` | Telemetry is emitted as a single `_log.info("tcp.ask.metrics", ...)` structured-log record. App Insights' Python SDK parses structured logs but **does not auto-convert** `kwargs` into `customMetrics`; those land in the `traces` table as `customDimensions`. The spec language in §3.2.4 reads "emitted as `tcp.anthropic.input_tokens` ... custom metrics", which on App Insights means `track_metric()` calls via `opencensus`/`azure-monitor-opentelemetry`. As coded, you will not be able to chart `tcp.ask.latency_ms` in the App Insights Metrics blade — only query it from Logs. | The architecture explicitly calls these out as metrics, not log dimensions; PowerBI / Azure Workbooks consume them differently. | Either (a) accept the trade-off and amend §3.2.4 to say "as custom log dimensions" + provide the KQL queries, or (b) wire `azure-monitor-opentelemetry` and call `meter.create_counter(...).add(value, attrs)` for each metric. The KQL route is cheaper for the academic build.

## Minor

- [ ] **MI-01** | `tcp/ai/anthropic_client.py:301-308` | The `system_blocks` literal-dict approach is correct, but the SDK's preferred type is `anthropic.types.TextBlockParam` (`{"type": "text", "text": ..., "cache_control": {...}}`). The `Any` cast at line 315 masks the drift. Suggest a `# type: ignore[arg-type]` comment in case the SDK tightens the typed-dict, plus a comment pointing at the cache-control docs URL. | Future SDK upgrade may break silently. | Pin `anthropic>=0.34` in `pyproject.toml` and add a `# noqa: type-arg` annotation explaining the deliberate cast.
- [ ] **MI-02** | `tcp/ai/prompts.py:32` | The system prompt mixes prose with code-fenced DDL. A future addition of any per-request data here (e.g., "current date is ...") would silently invalidate the cache for every request. Add a module-level constant `_FORBIDDEN_DYNAMIC_TOKENS = ["{", "}"]` and a unit test asserting the prompt body contains none of them; the only braces should be in the few-shot JSON literals. | Prevents accidental cache invalidation. | Add `test_prompt_has_no_format_strings` to `test_ai_anthropic_client.py`.
- [ ] **MI-03** | `tcp/ai/prompts.py:278` | The triple-quoted string is followed by a stray `"""Long-form schema context for prompt caching. See module docstring."""` (line 279) which is dead code — it's a string expression with no effect. | Cosmetic. | Delete line 279.
- [ ] **MI-04** | `function_app/triggers/ask.py:382` | `oid_suffix=str(oid)[-4:]` is correct per ADR-003 §3, but `UUID` formats with hyphens — the last 4 chars are often a digit cluster (`5666`) which is more guessable than the recommended `hex[-8:]` form. | Minor privacy nit. | Use `oid.hex[-8:]` for a higher-entropy short id while still avoiding full-OID disclosure.
- [ ] **MI-05** | `tcp/safe_query.py:540-545` | The schema check rejects non-`dbo` schemas but allows the catalog list `{"", "tcp", "tcp_dev"}`. In production the database is named `sqldb-tcp-prod-weu` (per `03_architecture.md §4.1`), not `tcp` or `tcp_dev`. | Cosmetic — three-part references like `[sqldb-tcp-prod-weu].dbo.v_trades_enriched` would be rejected unnecessarily. | Either remove the catalog allowlist entirely (rely on the `dbo` schema check and the table allowlist) or update the set to include `sqldb-tcp-prod-weu`.
- [ ] **MI-06** | `tests/unit/test_ai_anthropic_client.py:147-165` | The `test_system_block_carries_cache_control_ephemeral` test pins the entire `SCHEMA_SYSTEM_PROMPT` string equality. Any prompt edit (which is expected per the maintenance note in `ai_prompt_cache_contents.md §7`) breaks this test. | Test fragility. | Replace `assert system_blocks[0]["text"] == SCHEMA_SYSTEM_PROMPT` with `assert system_blocks[0]["text"].startswith("You are the TCP analytics assistant")` and a length-range assertion (`12_000 < len(system_blocks[0]["text"]) < 20_000`).
- [ ] **MI-07** | `docs/design/ai_prompt_cache_contents.md:170-173` | The doc mentions `cache_control: {"type": "persistent"}` — this is not a documented Anthropic public option (the public API has `ephemeral` only at the time of writing). | Factually inaccurate. | Replace the persistent-cache aside with: "Anthropic may offer extended TTLs (1h / 24h) on a contractual basis — confirm with the API team before relying on it."
- [ ] **MI-08** | `function_app/triggers/ask.py:117-161` | `_parse_principal_header` returns `None` for any malformed input and maps to 401 with a generic detail. A defense-in-depth log of which branch fired (b64 decode failure / JSON parse failure / missing oid / bad UUID) would help debugging without leaking detail to the client. | Operational visibility. | Add `log.debug("tcp.func.ask.principal_parse_step", step=...)` at each early return.
- [ ] **MI-09** | `tcp/safe_query.py:266-315` | The docstring lists nine numbered steps in the contract but the implementation order is slightly different (token deny-list runs before whitespace strip in narrative, but the code strips first). Pedantic. | Doc drift. | Renumber to match implementation order, or rephrase as "ordered set of checks" without numbering.

## Spec conformance matrix

| Spec item | File | Verdict |
|---|---|---|
| Model = `claude-haiku-4-5` | `tcp/ai/anthropic_client.py:34` | PASS |
| temperature = 0.0 | `tcp/ai/anthropic_client.py:96`, test_ai L180 | PASS |
| `cache_control: ephemeral` on system block | `tcp/ai/anthropic_client.py:306`, test_ai L165 | PASS |
| `emit_sql` tool with documented schema | `tcp/ai/anthropic_client.py:191-234` | PASS |
| `tool_choice` forces `emit_sql` | `tcp/ai/anthropic_client.py:323`, test_ai L195 | PASS |
| Response parsed from `tool_use` (not text) | `tcp/ai/anthropic_client.py:338-365` | PASS |
| Refusal path detected | `tcp/ai/anthropic_client.py:373` + `ask.py:440` | PASS |
| 5 view DDLs in prompt | `tcp/ai/prompts.py` §View definitions | PASS |
| Proc + function signatures in prompt | `tcp/ai/prompts.py:75-89` | PASS |
| RLS contract documented to model | `tcp/ai/prompts.py:171-186` | PASS |
| Romanian locale rules in prompt | `tcp/ai/prompts.py:199-205` | PASS |
| ≥ 3 few-shot examples | `tcp/ai/prompts.py:244-278` | PASS |
| No row data / PII in prompt | `tcp/ai/prompts.py` (whole file) | PASS |
| Token estimate ~3 000-5 000 | `ai_prompt_cache_contents.md §3` | PASS (~3500) |
| SELECT-only (incl. CTEs) | `tcp/safe_query.py:457-474` | PASS |
| Deny tokens: UNION/INTO/WAITFOR/xp_*/sp_set_session_context | `tcp/safe_query.py:197-254` | PASS |
| TOP / OFFSET ≤ 1000 enforced | `tcp/safe_query.py:602-640`, tests L262/L272 | PASS |
| Tables/views/funcs allowlist matches spec | `tcp/safe_query.py:98-150` | PASS |
| Comment forms denied (`--`, `/* */`) | `tcp/safe_query.py:202-204` | PARTIAL — see MA-01 |
| Multi-statement input denied | `tcp/safe_query.py:428-454`, test L310 | PASS |
| ≥ 20 adversarial prompts | `tests/unit/test_safe_query.py:155-220` | PASS (22) |
| DROP/INSERT/UPDATE/DELETE/MERGE rejected | tests L84-107 | PASS |
| UNION rejected | tests L161 | PASS |
| xp_cmdshell / sp_executesql rejected | tests L174-175, L110-118 | PASS |
| WAITFOR DELAY rejected | tests L167 | PASS |
| OPENROWSET / OPENDATASOURCE / OPENQUERY | tests L173, L206-208 | PASS |
| Comment-bypass attempts caught | tests L176-185 | PASS (but see MA-01) |
| Multi-statement caught | tests L158, L310 | PASS |
| Row-limit overflow caught | tests L259-272 | PASS |
| `x-ms-client-principal` → 401 | `ask.py:358-364`, test L107 | PASS |
| `X-SWA-Forwarded` → 403 (hmac compare) | `ask.py:164-176`, test L119 | PASS (but see MA-05 ordering) |
| Body length ≤ 500 chars | `ask.py:399-406` | PASS |
| Scope via `dim_UserRoles` → 404 | `ask.py:183-218`, test L131 | PASS |
| Refusal → 422 | `ask.py:440-451`, test L150 | PASS |
| Validation fail → 422 | `ask.py:454-469`, test L185 | PASS (but see MA-06) |
| RLS-scoped exec | `ask.py:226-247`, test L266 | PASS |
| ≤ 1000 rows fetched | `ask.py:240` | PASS |
| Response JSON shape | `ask.py:486-497` | PASS |
| 500 catch-all without stack traces | `ask.py:411-416`, 474-479 | PASS |
| App Insights custom metrics emitted | `ask.py:304-326` | PARTIAL — see MA-08 |
| structlog last-4 OID | `ask.py:382` | PASS (see MI-04) |
| Parameterised scope lookup SQL | `ask.py:199-203` | PASS |
| `bypass_session_context=True` escape hatch documented | `tcp/db.py:209-232` | PASS |
| LLM SQL injection mitigated | `tcp/safe_query.py` whole module | PASS |
| Unit tests mock Anthropic SDK | `test_ai_anthropic_client.py` | PASS |
| Integration gated on env vars | `test_ask_endpoint.py:50-54` | PASS |
| 7 documented integration cases | `test_ask_endpoint.py` | PARTIAL — missing malformed-payload, see MA-07 |
| Every status code has a path | `ask.py` | PASS (401/403/404/413/422/500; 400 undocumented) |
| Prompt cache contents doc | `ai_prompt_cache_contents.md` | PASS |
| Cross-references to spec accurate | `ai_prompt_cache_contents.md §6` | PASS |

## Recommendation

ACCEPT_WITH_CHANGES. The implementation is production-quality for the
academic-build target: the AI contract is correctly pinned, prompt
caching is wired with the right block, the SQL allowlist is fail-closed
with a strong deny-list, and the trigger pipeline implements every
documented error path. The eight Major findings cluster around two
themes — (a) defence-in-depth gaps in the deny-list (MA-01, MA-02, MA-03)
that adversarial fuzzing could surface, and (b) operational polish on
the trigger envelope (MA-05, MA-06, MA-07, MA-08) plus one query-timeout
suggestion (MA-04). None of them invalidate the design; all are
actionable in a single follow-up commit. Recommend converging in a
pass-2 review after the Major items land, then tagging `v1.0-mvp` and
moving to Etapa 6 (SWA frontend).
