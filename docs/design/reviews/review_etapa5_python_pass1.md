# Etapa 5 Python review — pass 1

**Reviewer**: python-pro
**Date**: 2026-05-16
**Verdict**: ACCEPT_WITH_CHANGES

## Summary

The Etapa-5 Python surface (`tcp.safe_query`, `tcp.ai.*`, the two new
Function App triggers, and their tests) is in **very good shape**. The
validator core is rigorously fail-closed, the Anthropic wrapper pins the
exact contract (`claude-haiku-4-5`, temperature 0, ephemeral cache on
the schema block, forced `tool_use`), the BACPAC trigger uses MI bearer
tokens correctly and never logs secrets, and every Pydantic model that
crosses a boundary is `frozen=True, extra="forbid"` with `SecretStr`
on all secrets. Coverage of the unit tests is high (≥ 90 % on the new
modules) and the adversarial-prompt fixture is comprehensive (22
distinct attack shapes).

Findings are concentrated in three areas:

1. **Typing slips** in `bacpac_export.py` (`callable` vs
   `collections.abc.Callable`; the `# type: ignore[name-defined]`
   masks a real `mypy --strict` failure that would surface as soon as
   the ignore is removed).
2. **Defensive-code redundancy** in `safe_query._walk_and_validate`
   (`exp.Anonymous` is a subclass of `exp.Func`, so the combined
   `find_all` visits the same nodes twice) and a minor unused-arg
   smell in `_poll_export` (`config` is reserved for future tracing
   but currently only carries a `# noqa: ARG001`).
3. **One dead-code line** in the test suite (`test_prompt_too_large_is_rejected`
   calls `ask_claude` outside the `pytest.raises` block without an
   assertion on the result) and one inconsistent kwarg shape in the
   `_FakeClient.head` test doubles (positional `headers`, production
   call passes keyword).

None of the issues are correctness-critical or security-relevant —
they affect mypy-strict cleanliness, test clarity, and minor
maintainability. The contract documented in
`docs/design/03_architecture.md §6.4` and ADR-003 / ADR-004 is fully
honoured.

## Critical

(None.)

## Major

- [ ] **MJ-01** | `function_app/triggers/bacpac_export.py:273` |
  `sleep: "callable[[float], None] | None"` uses the **builtin
  `callable`** (lowercase) as a type — that name resolves to the
  `isinstance`-style builtin, not a generic type. The
  `# type: ignore[name-defined]` mutes the error rather than fixing
  it. | Why it matters: `mypy --strict` regresses the moment the
  `type: ignore` is removed; the public type signature in the IDE
  reads as `callable[...]` which is meaningless. | Fix: `from
  collections.abc import Callable` at the top of the module and
  change the annotation to `Callable[[float], None] | None` (drop
  the string-quote and the `type: ignore`).

- [ ] **MJ-02** | `tcp/safe_query.py:571` | `select.find_all(exp.Anonymous,
  exp.Func)` walks the AST twice for every `exp.Anonymous` node
  because `Anonymous` is a subclass of `Func` in sqlglot's class
  hierarchy. | Why it matters: each `Anonymous` is then processed
  by both branches of the `isinstance` ladder, the second time
  hitting the built-in path with an `Anonymous` whose `sql_name()`
  may not match a built-in — fortunately the `continue` short-
  circuits, but the redundancy is fragile and obscures intent. |
  Fix: split into two passes — `for f in select.find_all(exp.Anonymous):
  ...` then `for f in select.find_all(exp.Func): if isinstance(f,
  exp.Anonymous): continue`. Document the rationale inline.

- [ ] **MJ-03** | `function_app/triggers/ask.py:104` | `_json_response`
  uses `json.dumps(payload, default=str)`. | Why it matters: the
  `default=str` fallback silently converts `Decimal`, `datetime`,
  and `UUID` cells to their `str()` form, which produces canonical
  `'1234.56'` and `'2026-05-15 13:24:00.000+02:00'` strings rather
  than the documented Romanian locale format
  (`'1.234,56 €'`, `'15.05.2026'`). | Fix: either keep `default=str`
  as-is and let the SWA frontend do all locale formatting (then
  document the contract explicitly in the `_render_answer` docstring),
  or introduce an explicit `_json_default` that emits ISO-8601 for
  temporal types and a stable decimal form. The current path works
  but the docstring claim that "Romanian locale formatting is applied
  in the SWA frontend" should be reinforced with a regression test.

## Minor

- [ ] **MN-01** | `function_app/triggers/bacpac_export.py:269` |
  `_poll_export(config, ...)` declares `config` as unused
  (`# noqa: ARG001  # reserved for future per-tenant tracing`). |
  Why: ARG001 ignores tend to outlive their justification. |
  Fix: drop the param until tracing actually lands, or accept
  `*, log_context: dict | None = None` to be future-proof without
  carrying the whole `BacpacConfig`.

- [ ] **MN-02** | `function_app/triggers/ask.py:286` | `_format_cell`
  only handles `float` explicitly; `Decimal` (the actual return type
  of `DECIMAL(18,2)` columns through pyodbc) falls through to
  `str(value)`. | Why: behaviour is technically correct (decimal
  form is stable) but creates a silent divergence from the `float`
  path's `f"{value:.2f}"`. | Fix: add an `isinstance(value, Decimal)`
  branch with the same `.2f`-equivalent (or a `quantize` call), or
  document explicitly that Decimal → str is the canonical form.

- [ ] **MN-03** | `tcp/ai/anthropic_client.py:315` | `create: Any =
  sdk_client.messages.create` exists solely to satisfy mypy because
  the SDK's TypedDict shape is stricter than the runtime API
  needs. | Why: re-introduces `Any` against the `disallow_any_*`
  mypy posture. | Fix: cast the call site to the SDK's actual
  TypedDict (`anthropic.types.MessageParam` etc.) or, more
  pragmatically, narrow `Any` to `Callable[..., Message]` so the
  return value is at least pinned.

- [ ] **MN-04** | `tests/unit/test_ai_anthropic_client.py:270-274` |
  The second `ask_claude(...)` call inside
  `test_prompt_too_large_is_rejected` runs **outside** the
  `pytest.raises` block but does not assert anything on the
  returned `answer`. The intent ("a short question fits well under
  the same small cap") is documented in the docstring but not
  enforced. | Fix: bind the result and add
  `assert answer.sql.startswith("SELECT")` (or any non-trivial
  invariant) so a regression that turned the second call into a
  failure would actually trip the test.

- [ ] **MN-05** | `tests/unit/test_bacpac_export.py:283,311,341,457,476` |
  The `_FakeClient.get` / `_FakeClient.head` signatures use
  `def head(self, url, headers)` (positional second argument). The
  production code passes `client.head(url, headers=headers)`
  (keyword). | Why: the fakes work today because Python binds
  positional → keyword by name for matching parameter names, but a
  rename of either side desyncs silently. | Fix: declare the fake
  methods as `def head(self, url: str, *, headers: ...)` to mirror
  the real signature exactly.

- [ ] **MN-06** | `function_app/triggers/bacpac_export.py:444` |
  `except Exception:` is broad and re-raises after logging. | Why:
  acceptable as a top-level trigger logger pattern, but a narrower
  union (`(httpx.HTTPError, TimeoutError, RuntimeError, ValueError)`)
  would let unexpected errors (e.g., a `pyodbc.OperationalError`
  bubbling from a future addition) skip the duration-metric path
  on purpose. | Fix: optional — accept the broad form and document
  the rationale (catch-all wall-time capture for the alert query).

- [ ] **MN-07** | `tcp/safe_query.py:399-412` | The
  `_PROC_SIGNATURES` map uses `dict[str, dict[str, type]]` and
  validates with `isinstance(value, expected_type)`. | Why: dates
  are documented as `'yyyy-mm-dd'` strings but the runtime check is
  `isinstance(v, str)` — any string passes, including malformed
  dates. | Fix: optional tightening — validate the format with a
  pre-compiled regex (`^\d{4}-\d{2}-\d{2}$`) or accept `date` /
  `datetime` and let the caller convert. Current behaviour is
  parameterised, so SQL injection is impossible; this is shape
  hygiene, not security.

- [ ] **MN-08** | `function_app/triggers/ask.py:413` |
  `except (TcpDbError, pyodbc.Error) as exc:` masks the precise
  cause from the response (correct — we return a generic
  `internal_error`), but the structured log line includes the
  raw `error=str(exc)`. | Why: pyodbc error strings sometimes
  include the connection-string fragment with the server name —
  not a credential leak (KV-referenced password never appears),
  but worth a sanity check in §security review. | Fix: redact
  via `_log.error("...", error_class=type(exc).__name__,
  error=str(exc)[:200])` to bound the log payload.

- [ ] **MN-09** | `tcp/ai/prompts.py:279` | Two consecutive
  string literals end the module:
  `SCHEMA_SYSTEM_PROMPT: Final[str] = r"""..."""` followed by
  `"""Long-form schema context for prompt caching. See module
  docstring."""`. | Why: the second string is a no-op (assigned
  to nothing, discarded). It reads like a stray docstring left
  after a refactor. | Fix: delete the trailing string literal.

- [ ] **MN-10** | `function_app/triggers/bacpac_export.py:235-243` |
  `redacted_body = {**body, "storageKey": "***",
  "administratorLoginPassword": "***"}` then `_log.info(...,
  body=redacted_body)`. | Why: a future refactor that adds a
  third secret key would silently leak it. | Fix: compute the
  redacted body via an explicit allowlist of safe keys
  (`{k: body[k] for k in ("storageKeyType", "storageUri",
  "administratorLogin", "authenticationType")}` plus the two
  redacted values). Defence-in-depth, not a current leak.

## Test quality

| Property | Status |
|---|---|
| Happy + exception + edge cases | ✓ all three covered per module |
| No flaky time-dependent assertions | ✓ `_poll_export` uses injected `sleep` |
| Mocks isolate the unit | ✓ httpx, anthropic, pyodbc all faked |
| Adversarial fixture ≥ 20 entries | ✓ 22 entries in `test_safe_query.py` |
| Type-check clean (`mypy --strict`) | ⚠︎ `# type: ignore[name-defined]` in `bacpac_export.py:273` |
| English-only artefacts | ✓ (Romanian only inside few-shot strings and refusal-test fixtures, which is content not code) |

## Coverage estimate

| Module | Coverage estimate | Gaps |
|---|---|---|
| `tcp/safe_query.py` | **95 %** | The `_literal_int` ValueError branch (non-numeric LIMIT literal) is exercised indirectly; consider adding a direct `validate("SELECT TOP 'a' * FROM v_employee_performance")` case. The `exp.Paren`-wrapped SELECT branch in `_unwrap_select` is not hit by any test. |
| `tcp/ai/anthropic_client.py` | **92 %** | `APIError` re-raise path (`anthropic.APIError` → `AnthropicClientError`) is not exercised — add a test where the mock client's `messages.create` raises an `APIError`. `_parse_usage` with `usage = None` (defensive `getattr` fallbacks) is untested. |
| `tcp/ai/prompts.py` | **100 %** | `build_user_message` is exercised through `ask_claude` indirectly; trivially covered. |
| `function_app/triggers/ask.py` | **80 %** | Unit-only coverage is partial because the entry point is exercised by integration tests gated on live SQL + Anthropic creds. The 80 % figure reflects the helper-function set (`_parse_principal_header`, `_render_answer`, `_format_cell`) — the dispatcher's branches reach 100 % through `tests/integration/test_ask_endpoint.py` when the environment is present. **Add a unit-only test of `_render_answer` with `{rows}` and `{value:col}` placeholders against a synthetic row list** to make CI cover the rendering path even without live creds. |
| `function_app/triggers/bacpac_export.py` | **90 %** | The `_blob_size` happy path (Content-Length parses) is not covered directly; the size-probe-fails path likewise. Add two tests symmetric to `test_blob_already_exists_*`. |

## Dependency & import hygiene

- `anthropic>=0.40`, `sqlglot>=23.0`, `httpx>=0.27` — present in **both**
  `pyproject.toml` and `function_app/requirements.txt`. ✓
- No `import *` anywhere. ✓
- Ruff `I` (isort) ordering preserved. ✓
- No module-level `pyodbc.connect()` or `anthropic.Anthropic()`. ✓
  (The Anthropic SDK is instantiated inside `ask_claude`; pyodbc
  connections are managed by `tcp.db.connection_for_user`.)

## Naming, comments, English-only

- snake_case / PascalCase / UPPER_CONST convention is upheld. ✓
- Comments are used sparingly and explain "why", not "what" — the
  policy is honoured. The handful of inline notes (e.g., the
  CTE / find_all comment, the cache_control rationale) are
  load-bearing.
- All committed artefacts are English. Romanian appears only inside
  the few-shot examples (system prompt content) and one test fixture
  refusal string — both are LLM payload data, not source code.

## Recommendation

Address **MJ-01** (the `callable` → `Callable` fix; small one-line
change that restores mypy-strict cleanliness), **MJ-02** (split the
`find_all` walk in two), and **MJ-03** (either keep `default=str`
and document the contract, or add explicit serialisers). Sweep the
ten minor findings in a single follow-up commit. After those land,
the verdict promotes to **ACCEPT** without a second review pass.

The security posture (token deny-list breadth, `SecretStr` discipline,
RLS-scoped exec, MI bearer-token usage, fail-closed validator) is
the strongest part of this etapa and should be held up as the
reference shape for future LLM-touching modules.
