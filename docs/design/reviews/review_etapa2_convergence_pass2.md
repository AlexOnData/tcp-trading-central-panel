# Etapa 2 convergence review — pass 2

**Reviewer**: code-reviewer (verification pass)
**Date**: 2026-05-15
**Verdict**: ACCEPT_WITH_MINOR_CHANGES

## Summary

All four pass-1 critical items (SQL CR-01, code-review CR-01..CR-04, security CR-01..CR-02 — overlap counted once) are fully resolved with correct, well-commented fixes; the SQL agent's `tvf_RiskMetrics` rewrite uses the prescribed CROSS APPLY + `SELECT DISTINCT` pattern verbatim and the Python `connection_for_user` lifecycle now closes the connection even when `conn.cursor()` raises (CR-04 regression test present at `tests/unit/test_db.py:360`). Twelve of the thirteen verified major items are RESOLVED; the remaining one (security MJ-01, third-party action SHA pin) is PARTIALLY RESOLVED — the workflow has unambiguous `TODO: pin to full SHA` placeholders that the user must fill before tagging `v1.0-db-ready`. One small documentation regression slipped in (`tcp/README.md:101` still references the pre-rename `ConnectionError` symbol — the class is now `TcpConnectionError`).

## Pass-1 ID status table

| ID (pass 1) | Source review | Severity | Status (pass 2) | Notes / evidence |
|---|---|---|---|---|
| CR-01 | review_etapa2_sql_pass1 | critical | RESOLVED | `db/migrations/V001__init.sql:997-1030` — new body uses outer projection from derived table `a` (scalar aggregates) + `CROSS APPLY` derived table `p` for `var_95` via `PERCENTILE_CONT(...) OVER ()` with `SELECT DISTINCT`. Both subqueries hit `dbo.v_employee_performance` filtered by the same `(employee_id, trade_date_ro)` predicate; `WITH SCHEMABINDING` retained on line 1004. Compiles cleanly mentally — no aggregate/window mix in any single SELECT. |
| MJ-01 | review_etapa2_sql_pass1 | major | RESOLVED | `V001__init.sql:1283-1286` — both `ALTER ROLE ... ADD MEMBER tcp_admin` calls are wrapped in `IF IS_ROLEMEMBER(N'<role>', N'tcp_admin') = 0` guards. Re-apply now skips the additions silently instead of raising Msg 15410. |
| MJ-02 | review_etapa2_sql_pass1 | major | RESOLVED | `docs/design/02_database_design.md:1267-1276` — spec §15 now matches the implementation shape `(script_name NVARCHAR(200) PK, applied_at_utc DATETIME2(3), checksum NVARCHAR(128))`; line 1267 carries the explicit alignment note dated 2026-05-15 referencing `review_etapa2_sql_pass1.md` MJ-02. The §15 INSERT (line 2420) and the rollback DELETE (`V001__init.down.sql:155`) reference the same columns. |
| CR-01 | review_etapa2_python_ci_pass1 | critical | RESOLVED | `.github/workflows/ci.yml:105` — `uv run sqlfluff lint db/ tests/sql/ --dialect tsql` runs without `\|\| true` or stderr redirection; non-zero exit propagates. |
| CR-02 | review_etapa2_python_ci_pass1 | critical | RESOLVED | `ci.yml:144` — `uv run bandit -r tcp -lll -iii` (HIGH severity + HIGH confidence threshold) with no trailing `\|\| true`. |
| CR-03 | review_etapa2_python_ci_pass1 | critical | RESOLVED | `ci.yml:147` — `uv run pip-audit --strict` without mask. |
| CR-04 | review_etapa2_python_ci_pass1 | critical | RESOLVED | `tcp/db.py:256-283` — `conn = _open_raw_connection(...)` is followed by an outer `try:` containing `cursor = conn.cursor()`; `conn.close()` runs in the outer `finally:` even if `conn.cursor()` raises. The dedicated regression test at `tests/unit/test_db.py:360 test_connection_for_user_closes_when_cursor_creation_fails` mocks `conn.cursor.side_effect = pyodbc.OperationalError(...)` and asserts the body never runs and `conn.close()` is still called. |
| MJ-01 | review_etapa2_python_ci_pass1 | major | RESOLVED | All four invocations of sqlfluff/bandit/pip-audit in `ci.yml` use `uv run`/`uv sync` (lines 40, 70, 102, 132); no `pip install` anywhere in the workflow. |
| MJ-02 | review_etapa2_python_ci_pass1 | major | RESOLVED | `.sqlfluff:7` — `exclude_rules = LT02, LT05` (3.x codes); `pyproject.toml:29` pins `sqlfluff>=3.0,<4`. Comment on `.sqlfluff` lines 4-6 records the 1.x→3.x mapping for future maintainers. |
| MJ-03 | review_etapa2_python_ci_pass1 | major | RESOLVED | `docs/dev_setup.md` — PowerShell variants present alongside bash for One-Time Setup (sections 1-2, lines 38-77), schema apply (lines 108-111), unit tests (line 130, marked identical), integration tests (lines 148-155), and SQL integration tests (lines 168-173). Each block labelled `# Linux/macOS (bash)` / `# Windows (PowerShell)`. |
| MJ-04 | review_etapa2_python_ci_pass1 | major | RESOLVED | `ci.yml:73` — `--cov-fail-under=90`. |
| MJ-05 | review_etapa2_python_ci_pass1 | major | RESOLVED | `scripts/validate_naming_convention.py:21,86-92` — `FUNCTION_PATTERN = ^(fn\|tvf)_[A-Z][a-zA-Z0-9]*$` is real, applied in the loop, and emits descriptive errors. No dead `pass` body remains. |
| MJ-06 | review_etapa2_python_ci_pass1 | major | RESOLVED | `scripts/validate_naming_convention.py:29` — shared `_SCHEMA_QUALIFIED = (?:\[?dbo\]?\.)?\[?(\w+)\]?` substituted into all four CREATE patterns. Handles `foo`, `dbo.foo`, `[dbo].[foo]`, and `[foo]`. |
| MJ-07 | review_etapa2_python_ci_pass1 | major | RESOLVED | `tests/unit/test_validate_naming_convention.py` exists (318 lines) with positive and negative fixtures across table/view/procedure/function families, bracketed identifiers (`test_bracketed_*`), `IF NOT EXISTS` guards, multi-statement files, the CLI entrypoint (zero-exit / one-exit / missing-dir / no-args), and a read-failure path. |
| CR-01 | review_etapa2_security_pass1 | critical | RESOLVED | `.gitleaks.toml:16-22` — `files` array now lists `docker-compose.dev.yml`, `docs/dev_setup.md`, `db/README.md`, `tcp/README.md`, and `tests/unit/test_db.py`; `regexes` includes both `YourStrong!Passw0rd` and `p@ssw0rd!`. Coverage matches actual placeholder locations (verified via repo-wide grep). |
| CR-02 | review_etapa2_security_pass1 | critical | RESOLVED | Same as code-review CR-01/02/03; verified once at `ci.yml:105,144,147`. |
| MJ-01 | review_etapa2_security_pass1 | major | PARTIALLY RESOLVED | `ci.yml` still references `astral-sh/setup-uv@v3` (lines 35, 65, 97, 127) and `gitleaks/gitleaks-action@v2` (line 139) by mutable tags. Each line carries an unambiguous `# TODO: pin to full SHA` comment and the workflow header documents the policy (lines 30-32, 134-137). This matches the spec's stated allowance — "a clear `TODO: pin SHA` placeholder for the user to resolve" — but the SHAs themselves are not yet pinned, so the supply-chain hardening is documented, not enforced. Must be closed before tagging `v1.0-db-ready`. |
| MJ-02 | review_etapa2_security_pass1 | major | RESOLVED | `tests/sql/test_rls_smoke.sql:158-201` adds `TC-4 read_only_lock`. The test issues `sp_set_session_context @read_only=1`, then issues a second `sp_set_session_context` without the override inside a nested `BEGIN TRY`, captures `ERROR_NUMBER()`, and asserts it equals 15664 (raising severity 16 otherwise). Header comment (lines 8-9) explicitly references ADR-003 §2. The test also includes a bonus TC-5 `block_insert_other_trader` (lines 203-285) confirming error 33504 from the BLOCK PREDICATE — covering security MN-10 as well. |
| MJ-03 | review_etapa2_security_pass1 | major | RESOLVED | `tcp/db.py:176` — `parts.append("Pooling=False")` is appended on every non-`SQL_AUTH_DEV` branch (covers both `AAD_MANAGED_IDENTITY` and `AAD_DEFAULT`). Comment on lines 173-175 cites ADR-003 §4 and the SESSION_CONTEXT-leakage rationale. The dev path keeps pooling enabled (documented on lines 168-169). |
| MJ-04 | review_etapa2_security_pass1 | major | RESOLVED | `tcp/db.py:181` — internal helper `_open_raw_connection` performs the actual `pyodbc.connect`. The public `open_connection` (lines 209-232) refuses to run unless `bypass_session_context=True` is explicitly passed, raising `AuthError` otherwise. Internal caller `connection_for_user:256` invokes the private helper. Integration tests (`tests/integration/test_db_smoke.py:37,91`) pass the flag explicitly with a comment citing ADR-003 §4. Unit test `test_open_connection_requires_explicit_bypass` at `tests/unit/test_db.py:356` asserts the `AuthError` on the default path. |
| MN-05 | review_etapa2_security_pass1 | minor | RESOLVED | `docker-compose.dev.yml:12` binds `"127.0.0.1:1433:1433"` so the well-known SA password is not reachable beyond the loopback interface. Lines 9-10 document the rationale. |
| MN-08 | review_etapa2_security_pass1 | minor | RESOLVED | `ci.yml:85-86` (sql-lint) and `ci.yml:113-114` (secret-scan) both declare `permissions: { contents: read }`. `id-token: write` is correctly absent from these two jobs (they do not exchange OIDC tokens). |

## Regressions

- **R-1 (minor doc drift)** | `tcp/README.md:101` | The "Defensive helpers" section still lists the three concrete `TcpDbError` subclasses as `SessionContextUnsetError`, **`ConnectionError`**, and `AuthError`. The Python module (`tcp/db.py:60`) renamed the class to `TcpConnectionError` as part of MN-01 (and security MN-03). Result: a reader following the README will fail to import the symbol. Spec note for this convergence pass explicitly called this out as a check ("if `ConnectionError` was renamed to `TcpConnectionError`, the README example uses the new name"). The example block (lines 80-88) does not reference the exception, but the prose enumeration on line 101 does. **Fix**: replace `\`ConnectionError\`` with `\`TcpConnectionError\`` in that single line. Five-second edit; will not be required to re-trigger CI gates because the doc is not tested in CI.

No other regressions detected. V001 still applies in a clean dependency order; the rollback still drops every object created by V001 (security policy → predicate fn → fact tables → views → procs → fns → config + dim tables → roles → rls schema → schema_history row); the public surface in `tcp/__init__.py` is empty (no re-exports), so callers consistently import from `tcp.db` and pick up the renamed class.

## Remaining gaps (if any)

1. **MJ-01 security (SHA pin)** — five `TODO: pin to full SHA` placeholders in `ci.yml` (four `astral-sh/setup-uv@v3`, one `gitleaks/gitleaks-action@v2`). The spec accepts these as resolved on the condition that the user fills them before `v1.0-db-ready`; flagged here so the gate is not forgotten at tagging time. Recommend adding a Dependabot config (or a one-line CI check) so SHA pins do not silently drift.
2. **R-1 (README drift)** — already documented above. One-line edit to `tcp/README.md:101`.

No outstanding criticals. Minor/nits from pass 1 (MN-01 through MN-15 in code review, MN-01/03/06/07/09 in security) were not requested as part of this convergence cycle and were not re-verified; the agents addressed the subset explicitly listed in the punch list (MN-04 SQL test tightening, MN-05 docker bind, MN-08 per-job permissions, MN-10 TC-5 block-insert) and left the rest for a follow-up.

## Verification notes (additional checks)

- **No new dependency-order issues in V001**. `tvf_RiskMetrics` continues to reference `dbo.v_employee_performance` (created earlier in §9), so the schemabinding chain holds. The CROSS APPLY pattern does not introduce any new schema dependency.
- **Mermaid / YAML / TOML / Python syntax** is valid in every modified file (manually parsed each one). `.sqlfluff` is a flat INI file with two rule-block sections — syntactically clean. `pyproject.toml` parses (no trailing commas in TOML arrays). `ci.yml` has consistent indentation, valid `permissions:` blocks, and no duplicate keys.
- **English-only policy**: upheld. The only Romanian content remains inside `dim_Date.month_name_ro` and `dim_Date.ro_holiday_name`, plus the deliberate Easter-Monday label and the Pentecost/Children's-Day combined label noted in pass 1 (out-of-scope for this verification pass).
- **New test files exit non-zero on failure**: `test_rls_smoke.sql` raises `RAISERROR(..., 16, 1)` from every assertion failure in TC-4 and TC-5 (verified at lines 185, 187, 199, 270, 272, 283). `test_validate_naming_convention.py` uses pytest `assert` statements and the CLI entrypoint test asserts `main() == 1` on violations.
- **One stylistic note (non-blocking)**: `scripts/validate_naming_convention.py:29` only accepts the `dbo` schema prefix. A future `rls.fn_<name>` declaration outside V001 would be matched by the inner `(\w+)` group and validated against `FUNCTION_PATTERN`, but a violator declared as `rls.fn_lowerName` would still be caught — good. However, a declaration like `CREATE FUNCTION rls.bad_name(...)` is matched (the inner group captures `bad_name`), and the violation message will not mention the `rls` schema (only the bare name). Cosmetic; flag for a follow-up if multi-schema declarations grow.

## Recommendation

**ACCEPT_WITH_MINOR_CHANGES.** Land the one-line `tcp/README.md` rename to `TcpConnectionError` before merging Etapa 2. Resolve the five `TODO: pin to full SHA` placeholders in `ci.yml` before tagging `v1.0-db-ready`; this is the only remaining substantive security gap, and it is a documented, deliberate hand-off to the user rather than an oversight by the SQL or Python agents. Every critical from pass 1 is RESOLVED; twelve of thirteen verified majors are RESOLVED with one (security MJ-01) PARTIALLY RESOLVED behind explicit TODO markers. The SESSION_CONTEXT contract, the RLS policy shape, the CI gate posture (no more `\|\| true` masks), and the connection-leak window are all production-grade. The convergence is essentially complete; pass 3 is not required.

## Files inspected

- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\db\migrations\V001__init.sql`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\db\migrations\rollback\V001__init.down.sql`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\sql\test_naming_convention.sql`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\sql\test_rls_smoke.sql`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\sql\test_fx_rate_completeness.sql`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\db\README.md`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\docs\design\02_database_design.md`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\pyproject.toml`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tcp\__init__.py`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tcp\db.py`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tcp\README.md`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\unit\test_db.py`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\unit\test_validate_naming_convention.py`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\integration\test_db_smoke.py`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\.github\workflows\ci.yml`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\.sqlfluff`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\docker-compose.dev.yml`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\.gitleaks.toml`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\scripts\validate_naming_convention.py`
- `d:\Personal\Proiect Licenta\TCP_TradingCentralPanel\docs\dev_setup.md`
