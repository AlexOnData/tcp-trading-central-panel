# Etapa 10 — Cross-cutting code review

| Field | Value |
| --- | --- |
| Reviewer | code-reviewer |
| Scope | Cross-stage code health (Python + SQL contract + config coherence + test suite + CSP/SWA chain) |
| Branch | `feat/azure-rewrite` |
| Baseline commit | `2dc18aa` (Etapa 7 — last on log), pending Etapa-10 triage commit |
| Tree state | 285 tests passing, 0 failures, coverage ≥ 90 % |
| Methodology | Paper review — no Azure subscription, no live SQL, no `azd up` |
| **Verdict** | **CHANGES-REQUESTED** — 1 Critical (BACPAC env-var missing in Bicep) + 1 Major (stored-proc parameter name drift in `safe_query` makes a documented API path unreachable) + 4 Minor + several Strengths. None of the findings blocks the `v1.0-mvp` tag *if* the operator accepts that the BACPAC weekly export will fail on first fire and the `validate_proc_call` structured-intent path remains a dead alternative — both are dormant on the v1.0 critical path (the assistant emits raw SQL, never proc-call envelopes, and the BACPAC trigger does not gate the `/api/ask` smoke). With those caveats acknowledged the tag is shippable; the right disposition is to address the Critical pre-tag because it is a true production-blocker for the Sunday-08:00 RO trigger. |

This review is the holistic cross-cutting pass that no single stage-specific reviewer caught. It deliberately steps over findings that prior convergence reports placed in the ACCEPTED RESIDUAL bucket; the `STORAGE-CONNECTION-STRING` vs MI debate (RR-01) is accepted, but the **env-var name** drift that prevents the trigger from booting at all is a fresh finding.

---

## Critical

### code10-CR-01 — `bacpac_export.py` reads `STORAGE_ACCOUNT_KEY` but Bicep never emits it

**Files**:
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\triggers\bacpac_export.py:52` — `_ENV_BACPAC_STORAGE_KEY: Final[str] = "STORAGE_ACCOUNT_KEY"`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\modules\functions.bicep:129-242` — `appSettings` list (no `STORAGE_ACCOUNT_KEY` row)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\modules\keyvault.bicep:157-167` — only `STORAGE-CONNECTION-STRING` is created
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\unit\test_bacpac_export.py:42` — test fixture pins `STORAGE_ACCOUNT_KEY` so unit tests pass without ever touching the real env

**Summary**: `BacpacConfig.from_env()` enumerates `STORAGE_ACCOUNT_KEY` as a required env var (lines 100-120). Bicep does not emit an app setting by that name. The Function App boots fine — the trigger reads `from_env()` only when it fires on Sunday 08:00 RO — and then `BacpacConfig.from_env()` raises `ValueError("BacpacConfig missing required env vars: STORAGE_ACCOUNT_KEY")` which bubbles up as `tcp.bacpac.failed` in App Insights. This is the same defect the Etapa-4 convergence pass-2 flagged as residual #2 ("`bacpac_export.py` env-var realignment"), explicitly saying "**Etapa 5 cannot wire the real Export call without this alignment**" — but Etapa 5 wired the Python body without resolving the Bicep side, and no later stage closed the loop.

**Why it matters**: the BACPAC export is ADR-004's only documented DR path. With no historical telemetry to verify it ran successfully (the alert at `infra/modules/alerts.bicep` "BACPAC missed" would fire correctly, but only after the first failure), the team cannot trust the published RPO/RTO posture. The tag `v1.0-mvp` advertises a working `0 0 8 * * 0` trigger; today that trigger 100 % fails on the first fire.

**Suggested fix** (smallest credible change, picked deliberately to avoid widening RR-01):
1. Extract the account key from `STORAGE-CONNECTION-STRING` at runtime in `_load_config`. The connection string has shape `DefaultEndpointsProtocol=https;AccountName=...;AccountKey=<key>;EndpointSuffix=...`. A 3-line parser converts it to the key. Then update Bicep to emit one new app setting `STORAGE_ACCOUNT_KEY = @Microsoft.KeyVault(SecretUri=.../secrets/STORAGE-CONNECTION-STRING/)` (the connection-string secret resolves at app-setting-resolution time; the Python code splits on `;` and indexes `AccountKey=…`).
2. Alternative (cleaner but deeper): create a dedicated KV secret `STORAGE-ACCOUNT-KEY` in `keyvault.bicep`, sourced from `storage.bicep`'s `listKeys().keys[0].value`, and bind `STORAGE_ACCOUNT_KEY` directly. This avoids the parse step but means a storage-key rotation must update two secrets in lockstep (the rotation runbook in `docs/security/credentials_rotation.md §2.4` would need a one-line edit).

Either option is ≤ 30 lines of Bicep + Python. Both keep the existing unit tests passing without modification (the tests mock the env directly).

The integration-test side has the same gap — the BACPAC trigger has **no integration test** so the bootstrapping bug is invisible to the local pytest run. Adding a smoke test that asserts `BacpacConfig.from_env()` is constructable from the same env-var set Bicep emits (parsing `azd env get-values` output) would have caught this drift at PR time.

---

## Major

### code10-MJ-01 — `safe_query.validate_proc_call` builds proc invocations whose parameter names do not exist on the SQL side

**Files**:
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tcp\safe_query.py:430-442` — `_PROC_SIGNATURES` uses `from_date` / `to_date`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tcp\safe_query.py:415-420` — assembles `f"@{k} = ?"` literally from the dict keys
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\db\migrations\V001__init.sql:1078-1081, 1115-1119` — both procs declare parameter names `@from` / `@to` (not `@from_date` / `@to_date`)
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\unit\test_safe_query.py:519, 537` — unit tests assert the produced SQL contains `@employee_id = ?` only, never test the `@from_date` / `@to_date` portion against a live SQL Server

**Summary**: `validate_proc_call("usp_GetEmployeePerformance", {"employee_id": 17, "from_date": "...", "to_date": "..."})` returns `sql="EXEC dbo.usp_GetEmployeePerformance @employee_id = ?, @from_date = ?, @to_date = ?"`. The SQL proc accepts `@from`, `@to` — not `@from_date`, `@to_date`. SQL Server would reject this with `Msg 8145: @from_date is not a parameter for procedure usp_GetEmployeePerformance`. The Python tests mock-only the dict shape and the placeholder count; they never exercise an actual `cursor.execute` against the proc.

**Why it matters**: today the bug is dormant because `validate_proc_call` is dead code in production — `function_app/triggers/ask.py` never calls it. The Anthropic SDK schema (`_EMIT_SQL_TOOL`) only exposes the free-form `sql` envelope, not a structured-intent envelope. So the procs `usp_GetEmployeePerformance` / `usp_GetTopPerformers` are technically allowlisted but unreachable. The risk is the opposite of the BACPAC one: if a future stage promotes the structured-intent path (sensible — the Etapa-5 security review specifically recommended it for typed access), the first integration test would fail and the team would spend hours hunting a stage-5-era bug. The placeholder string is also misleading documentation — `ALLOWED_PROCS` and the `tcp/ai/prompts.py` schema body advertise these procs to the model.

**Suggested fix**: rename the keys in `_PROC_SIGNATURES` to `from_` / `to_` to match the SQL (Python identifiers can't be the reserved keyword `from` directly — `from_` with a trailing underscore is the PEP-8 convention and tested by many SDKs). Adjust the assembly at line 415:

```python
# Map Python-side names to SQL parameter names. The trailing-underscore form
# is the PEP-8 convention for the reserved keywords `from` and `to`.
_SQL_PARAM_NAME = {"from_": "from", "to_": "to"}

placeholders = ", ".join(
    f"@{_SQL_PARAM_NAME.get(k, k)} = ?" for k in spec
)
```

…or, more directly, accept `from` / `to` as quoted-string dict keys (Python allows them as dict keys; only attribute access is blocked). The dict shape is `dict[str, type]`, so:

```python
_PROC_SIGNATURES: Final[dict[str, dict[str, type]]] = {
    "usp_GetEmployeePerformance": {
        "employee_id": int,
        "from": str,
        "to": str,
    },
    ...
}
```

Either path is a single-commit fix. The accompanying test update is to assert the produced SQL string contains the literal substring `@from = ?` and `@to = ?` — not the current `@from_date = ?`.

Also: add an `@pytest.mark.integration` test that executes the produced SQL against a real proc and asserts at least one row comes back. This is the gate that catches the next drift.

---

## Minor

### code10-MN-01 — CI's `mypy strict` does not cover `function_app/` or `scripts/`

**Files**:
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\.github\workflows\ci.yml:46-47` — `run: uv run mypy tcp tests`

**Summary**: the CI step types `tcp` and `tests`. The two other Python trees in the repo — `function_app/` (5 triggers, ≈ 800 LOC) and `scripts/` (3 helpers, ≈ 200 LOC) — are skipped. `pyproject.toml` configures `[tool.mypy]` as strict project-wide, so a local `uv run mypy tcp tests scripts function_app` is the only thing that catches a typed regression in the trigger code. Today `ask.py` does include `# type: ignore[arg-type]` and `cast(Any, …)` patterns at the SDK boundary; without strict typing in CI, a future `Any` leak in (say) `_render_answer` would land silently.

**Why it matters**: the strict typing posture is asymmetric and contradicts the "100 % Azure-native + 100 % strict" framing in `CLAUDE.md`. A green CI says "strict mypy" but only on half the surface.

**Suggested fix**: extend ci.yml line 47 to `uv run mypy tcp tests scripts function_app`. If a strict run on `function_app/` surfaces new findings (likely the `Any` boundary at the Anthropic SDK + the `getattr(usage, ...)` shape), suppress them with localised `# type: ignore[attr-defined]` comments rather than relaxing the project-wide config. Worst case the run takes 5 % longer.

### code10-MN-02 — `tcp.synth.runner` and `seed_employees` import the private `_open_raw_connection` directly

**Files**:
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tcp\synth\runner.py:39`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tcp\synth\seed_employees.py:34`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tcp\db.py:181-232` — the public guard `open_connection(bypass_session_context=True)` raises unless the explicit kwarg is passed

**Summary**: the public ADR-003 contract (line 215 of `db.py`) says "all user-driven paths MUST go through `connection_for_user`" and gates `open_connection` behind `bypass_session_context=True`. The synth code legitimately needs the admin path because it sets `SESSION_CONTEXT` itself for the generator MI. But it imports the *private* `_open_raw_connection` symbol, bypassing the public guard entirely. The two callers compensate by calling `set_admin_session_context(conn, generator_oid)` themselves, which is correct, but the convention is fragile: a future contributor reading `tcp/db.py` and tightening the `bypass_session_context` invariant might think they've covered every escape hatch, then `synth/*` slips past unchanged.

**Why it matters**: this is the kind of "this used to work differently in an earlier stage" smell. The original Etapa-2 design only had `open_connection`; Etapa-3 added the private alias to keep the synth code from having to pass `bypass_session_context=True` on every call. A cleaner contract would have a *public* admin-scoped helper (e.g., `open_admin_connection(oid)`) so the audit trail is one symbol, not two.

**Suggested fix**: rename `_open_raw_connection` to `open_raw_connection` (drop the leading underscore — it's already used by two production modules and a test, so it's effectively public) and add a docstring sentence explaining when each of the two functions is correct. Alternative: introduce `open_admin_connection(mi_object_id: UUID) -> Iterator[pyodbc.Connection]` as a `@contextmanager` that wraps the open + `set_admin_session_context` + reset/close lifecycle, then both synth callers consume it. The latter is the right architectural choice; the former is the minimal-diff choice.

### code10-MN-03 — Trigger ↔ FunctionApp circular import has no test, relies on an `E402` waiver

**Files**:
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\function_app.py:31-37` — `from function_app.triggers import (ask, …)  # noqa: E402, F401`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\triggers\ask.py:66` (and 4 sibling triggers) — `from function_app.function_app import app`

**Summary**: the `app = FunctionApp(...)` instance lives in `function_app/function_app.py`. Each trigger module imports `app` to register itself via `@app.route(...)`. The parent module then imports each trigger module *after* its own `app = FunctionApp(...)` line, with E402/F401 waivers. This works because Python evaluates the parent module top-to-bottom: when the trigger import fires, `function_app.function_app` is partially initialised but `app` is already bound. The pattern is documented in the Azure Functions v2 Python programming model; the failure mode is a future change that adds a top-level import in `function_app.py` *between* the `app = ...` line and the `from function_app.triggers import ...` line — that import could transitively import a trigger module and re-enter `function_app.function_app` before `app` is defined.

**Why it matters**: no test exercises the import order. A local `python -c "from function_app.function_app import app"` works; `python -c "from function_app.triggers import ask"` does too. But the negative case (regression) would only surface at Function App cold start in production.

**Suggested fix**: add a one-line import-smoke test under `tests/unit/test_function_app_imports.py` that does `from function_app import function_app` and asserts `function_app.app` is an instance of `func.FunctionApp` with all five trigger names registered. The cheap path is `assert set(fn.name for fn in function_app.app.get_functions()) == {"ask", "ping", "warmup", "daily_generator", "bacpac_export"}` (verify the actual API; if `get_functions()` isn't public, inspect `app.middleware` or use the registered routes). This is the kind of structural test the seed_employees sys.modules workaround taught us we need.

### code10-MN-04 — `docs/design/03_architecture.md` advertises `--cov-fail-under=80` while CI enforces 90

**Files**:
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\docs\design\03_architecture.md:411` — "Unit tests | `pytest --cov=tcp --cov-fail-under=80`"
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\.github\workflows\ci.yml:76` — `--cov-fail-under=90`

**Summary**: pure documentation drift. The architecture doc still quotes the Etapa-2 starting threshold (80 %) that the Etapa-2 convergence pass-2 raised to 90 % (MJ-04 RESOLVED). Two minor consequences: (1) a reader following the doc would set a weaker local gate; (2) the doc-vs-CI mismatch is the kind of thing the Etapa-9 docs review specifically tried to eradicate.

**Suggested fix**: change "80" to "90" in `docs/design/03_architecture.md:411`. One-line edit.

---

## Strengths

The cross-stage code health is genuinely good. The findings above are gaps, not crises. Highlights:

- **`tcp/safe_query.py`** is exemplary. The deny-list runs *before* sqlglot parse so OPENROWSET-like payloads surface a precise reason, the literal-masking + Unicode-NFKC normalisation closes the ai-MA-01 / ai-MA-03 holes documented in Etapa 5, and the CTE row-limit propagation (`_enforce_row_limit` per CTE body) is a defence many production codebases miss. The two-pass function walk (Anonymous nodes first, then typed Func nodes, with `Anonymous` skipped in pass 2) is the right structural fix for the sqlglot class-hierarchy double-walk bug.

- **`tcp/db.py`** honours its ADR-003 contract with the right granularity. The `connection_for_user` context manager nests `try/finally` so a cursor failure between `connect()` and `set_session_context` still closes the connection (CR-04 fix). The `set_admin_session_context` helper and the explicit `bypass_session_context=True` guard on `open_connection` make the public surface a single ledger of "where the RLS escape hatch is used", which matters for the security re-validation in Etapa 10.3.

- **`function_app/triggers/ask.py`** is the most carefully written trigger in the repo. The unified envelope (`_envelope`) means SWA never branches on HTTP code, the 8 distinct error paths (forwarded-secret, principal, JSON, length, scope, rate-limit, validator, execute) all log the right level of detail without echoing PII, and the `_emit_metrics(log, /, *, …)` positional-only refactor is correctly applied at both call sites. The audit event (`tcp.ask.audit` with SHA-256 fingerprint) and refusal-reason hashing close the obs-CR-01 and obs-MI-06 gaps with positive test assertions, not just absence-of-evidence.

- **`tests/integration/test_telemetry_no_pii.py`** is the most rigorous PII test I have seen in a thesis-grade codebase. Eight paths × three capture channels (structlog, stdlib logging, stdout/stderr) × four canaries (question text, dashed OID, hex OID, base64 principal blob) × positive assertions on `oid_suffix` and `tcp.ask.audit` is the right defence-in-depth posture. The "expected at least one telemetry emission" non-empty guard is the kind of meta-assertion that would have caught a silent logger-removal regression. If anything, the suite is over-prescribed for the v1.0 surface, which is the right side to err on.

- **SQL ↔ Python column-name contract** is tight. The view DDLs in V001 §9 match the column dictionaries in `tcp/ai/prompts.py` exactly (right down to the `DECIMAL(18,4)` typing on PnL columns and the `MAX(...)` aggregates in the per-day grain views). The runner's `_SQL_SELECT_ACTIVE_TRADERS` query joins on the same column names that `dim_Employees` / `dim_Accounts` declare. The `_VALID_PROC_STATUSES` set matches the proc's `RAISERROR`/`RETURN`-side status strings (caught the data-engineer CR-02 regression already).

- **Etapa-10 triage of the `test_seed_employees` sys.modules workaround is clever.** The comment at `tests/unit/test_seed_employees.py:10-16` is exactly the right level of detail for the next reader — it explains *why* the regular dotted import fails (because `tcp.synth.__init__` rebinds the submodule name to the function), and it pulls the module out of `sys.modules` to get the canonical handle. This is the right fix and the right docstring; it does not break anything else because every other test imports `seed_module` via the same explicit pattern or via the function name directly.

- **The `_normalise_for_denylist_scan` Cf/Cc-character rejection** (`tcp/safe_query.py:481-486`) is the kind of detail that catches a real attacker — zero-width joiners and bidi-overrides have been used to bypass SQL deny-lists in published CVEs. The fact that this lives in a thesis project is impressive.

- **`scripts/render_migration.py`** + **`compute_migration_checksum.py`** as the *shared* RR-09 path between PowerShell and Bash postprovision (arch-MA-04) is the right architectural choice — one canonicaliser, two consumers, byte-equivalent output. The matching `db/migrations/*.sql text eol=lf` pin in `.gitattributes` closes the cross-OS leg.

- **CSP / SWA / forwarded-secret chain**: `swa/staticwebapp.config.json` is correct as-shipped *if and only if* both placeholders are substituted. The postprovision `Step 2c` in `infra/scripts/postprovision.sh` (and presumably the `.ps1` sibling) substitutes both `<TENANT_ID>` and `<value-set-by-postprovision>` via a small inline Python heredoc that preserves JSON escaping. The CSP itself is hardened (`'self'` only, no `'unsafe-inline'`, no `'unsafe-eval'`, `frame-ancestors 'none'`) — this is what Etapa 6 promised. The order also matters: the Etapa-5 review moved the file from `function_app/` to `swa/` because the SWA upload pipeline never sees `function_app/` content; the file is now in the right spot.

- **Test suite hygiene**: zero `@pytest.mark.skip` decorators anywhere (only `pytest.skip(…)` calls inside conditional env guards, which is the right pattern). No `xfail`s. Markers (`integration`) registered in `pyproject.toml` `[tool.pytest.ini_options]`. The conftest fixtures auto-pin `TCP_SYNTH_SEED_OFFSET=0` so determinism doesn't depend on the runner's env.

---

## Cross-cutting items deliberately NOT flagged

The following items appeared in my read but were already in prior convergence reports' ACCEPTED RESIDUAL buckets — per the task contract I do not re-litigate them:

- `allowSharedKeyAccess: true` on the storage account (RR-01 — Etapa 6 convergence MJ-03).
- USD-EUR FX duplication between `tcp/synth/fx_rates.py` and the SQL `tvf_GetCapitalBaseline` rate references (Etapa 8 / Etapa 9 ACCEPTED RESIDUAL — defer to Etapa 12).
- The trigger ↔ FunctionApp E402/F401 waiver pattern in `function_app.py` (the noqa is documented in the file comment; my code10-MN-03 only adds a *test* to lock the import contract — it does not propose restructuring).
- Documentation forward-references to ADR-008 (Etapa 9 convergence pass-2 residual).
- The 11 PowerBI deferrals tracked in `powerbi/README.md` "Known limitations".

---

## What I would do if I had Azure access (out of scope)

The paper review can verify code paths but not their runtime behaviour. The five things I would `azd up` to confirm:

1. **Provision a fresh subscription** and run the `cd.yml` smoke job. Confirm `schema_history` has the substituted SHA-256 (not `__V001_CHECKSUM__`) for both migrations.
2. **Fire the BACPAC trigger manually** (`az functionapp invoke …` or wait for Sunday) to surface code10-CR-01 with the actual error message.
3. **Drive a 12-request `/api/ask` flood** to verify the `Retry-After: 60` header lands on the 429 response (the unit test asserts it but only via the envelope shape).
4. **Cycle the Function App** (restart) and time the cold-start latency p95 against the `slo.md` SLI-3 ≤ 3 s threshold.
5. **Inspect App Insights** after a known canary `/api/ask` call to confirm the `tcp.ask.audit` event lands once with `customDimensions.question_sha256` populated and never with the raw question text.

None of these block the `v1.0-mvp` tag — they would inform the Etapa-12 polish pass.

---

## Disposition recommendation

If the v1.0-mvp tag is the goal:

- **Fix code10-CR-01 in a single small commit before tagging.** It is a real production bug, the fix is ≤ 30 lines, and shipping a v1.0 whose advertised DR path fails the first time it runs is the wrong tradeoff.
- **Defer code10-MJ-01 to Etapa 12** with an explicit comment in `tcp/safe_query.py` near `_PROC_SIGNATURES` that says "dead code — proc-call envelope not wired through the model tool schema; resolve before promoting structured-intent". The risk of leaving it dormant is small; the risk of fixing it under time pressure and breaking the prompt cache contents (which would invalidate Anthropic's prompt-cache hit rate target) is also small but non-zero.
- **Apply the 4 Minors as a single batch commit** post-tag, before the Etapa-11 cleanup pass. None of them prevents production behaviour today.

Final verdict: **CHANGES-REQUESTED** — fix code10-CR-01, then tag. The remaining items are honest follow-ups, not blockers.
