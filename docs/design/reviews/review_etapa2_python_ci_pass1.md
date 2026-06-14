# Etapa 2 Python + CI review — pass 1

**Reviewer**: code-reviewer
**Date**: 2026-05-15
**Verdict**: ACCEPT_WITH_CHANGES

## Summary

The Python connection layer (`tcp/db.py`) faithfully implements the ADR-003
SESSION_CONTEXT contract: parameterised `sp_set_session_context` with
`@read_only=1`, guaranteed RESET in a nested `try/finally`, last-four-char OID
logging, three-mode auth resolution, frozen pydantic models, and a clean
exception hierarchy. The unit test suite is thorough (12 tests covering happy
path, exception path, exit-after-exception path, redaction, env resolution,
and the type guards). The blocking issues are concentrated in **CI gating**:
three security/lint jobs are wired with `|| true`, which silently turns them
into "warn-only" — the opposite of the audit requirement. A second set of
issues clusters around dev-environment ergonomics (no PowerShell variants in
`dev_setup.md` despite a Windows-only user), SQLFluff rule-code drift between
1.x and 3.x, and a small but real resource-leak window in `connection_for_user`
if `conn.cursor()` itself raises.

## Critical (blocks merging Etapa 2)

- [ ] **CR-01** | `.github/workflows/ci.yml:92` | sqlfluff job ends in `2>&1 || true` |
  the lint step can never fail the build, so a malformed migration ships
  unnoticed; identical effect to deleting the gate |
  remove `|| true` and `2>&1`; convert to a hard gate: `uv run sqlfluff lint db/ tests/sql/ --dialect tsql`.

- [ ] **CR-02** | `.github/workflows/ci.yml:111` | `bandit -r tcp -ll || true` |
  trailing `|| true` masks every bandit finding; combined with `-ll`
  (medium-and-above only) the job becomes near-noise. Audit checklist
  explicitly requires fail-on-findings |
  drop `|| true`; use `bandit -r tcp -lll -iii` or set a `bandit.yaml` and
  fail on HIGH; promote to an explicit step that exits non-zero.

- [ ] **CR-03** | `.github/workflows/ci.yml:114` | `pip-audit --strict || true` |
  same pattern — `--strict` is rendered cosmetic by `|| true` |
  remove `|| true`; let a CVE in `pyodbc` / `azure-identity` / `pydantic` fail
  the build. Add an allowlist file if a transient false positive needs muting.

- [ ] **CR-04** | `tcp/db.py:212-214` | `conn = open_connection(...)` and
  `cursor = conn.cursor()` execute **outside** the outer `try:` block |
  if `conn.cursor()` raises (driver-side OOM, transport reset between
  `connect` and first cursor), the just-opened `conn` is never closed and
  leaks until GC. This is the exact pooled-connection edge case ADR-003
  warns about |
  move cursor creation inside the outer `try:` so the `finally` block runs:

  ```python
  conn = open_connection(config=config, auth_mode=auth_mode)
  try:
      cursor = conn.cursor()
      cursor.execute(_SQL_SET_CONTEXT, oid_str)
      ...
  finally:
      ...
      conn.close()
  ```

## Major

- [ ] **MJ-01** | `.github/workflows/ci.yml:89` | `pip install sqlfluff` (and
  same for bandit, pip-audit) instead of `uv tool install` |
  CLAUDE.md forbids `pip + requirements.txt` and the rest of the workflow uses
  `uv`. Mixing installers risks a different Python interpreter than the one
  resolving the lockfile, and undermines reproducibility |
  switch to `uv tool install sqlfluff bandit pip-audit` (or add them as `dev`
  extras and run via `uv run`).

- [ ] **MJ-02** | `.sqlfluff:5` | `exclude_rules = L016, L036` |
  these are SQLFluff **1.x** rule codes; SQLFluff 3.x renamed them to
  `layout.long_lines` and `layout.indent`. CI installs an unpinned sqlfluff
  (`pip install sqlfluff`), so when 3.x is resolved the excludes silently no-op |
  pin sqlfluff in `pyproject.toml` (`sqlfluff>=3.0,<4`) and rewrite the
  excludes as `LT05, LT02` (3.x aliases) or the modern `layout.*` names.

- [ ] **MJ-03** | `docs/dev_setup.md` (entire file) | bash-only commands
  (`export VAR=…`, `apt install`, `curl … | sh`) on a project where the
  documented developer OS is Windows 11 |
  the user runs PowerShell; `export TCP_SQL_DEV_PASSWORD='…'` is a syntax
  error there and the credential never reaches `docker compose` |
  add PowerShell variants alongside the bash ones for at least the SQL/test
  invocations: `$env:TCP_SQL_DEV_PASSWORD='YourStrong!Passw0rd'`,
  `docker compose -f docker-compose.dev.yml up -d`, etc. Mark each block as
  `# Linux/macOS (bash)` / `# Windows (PowerShell)`.

- [ ] **MJ-04** | `.github/workflows/ci.yml:68` | `--cov-fail-under=85` |
  audit specifies the coverage floor for `tcp/db.py` is **90 %** and the
  current test set is already above that. 85 % leaves ~50 lines of regression
  headroom that ADR-003 cannot afford |
  raise to `--cov-fail-under=90`; the existing tests still pass.

- [ ] **MJ-05** | `scripts/validate_naming_convention.py:72-77` | dead
  `CREATE_FUNCTION_PATTERN` loop body is `pass` with a TODO comment |
  the regex is compiled and the loop walks every match but does nothing —
  silent acceptance of any function name, including violations of the spec.
  Either implement validation or remove the regex and the loop |
  decide on a `udf_*` PascalCase pattern (mirrors `usp_*`), implement, and
  back it with a unit test; or remove the dead code with an `ADR-XXX` link
  explaining the deferral.

- [ ] **MJ-06** | `scripts/validate_naming_convention.py:19-34` | regex only
  matches `dbo.<name>` not `[dbo].[<name>]` or quoted identifiers |
  T-SQL migrations frequently bracket-quote schema-qualified names; a
  violator named `[dim_lowerSnake]` slips past. Audit checklist explicitly
  lists "schema-qualified names" |
  extend the schema-prefix group: `(?:\[?dbo\]?\.)?\[?(\w+)\]?` (and add a
  test fixture).

- [ ] **MJ-07** | `scripts/validate_naming_convention.py` (whole file) | no
  unit tests despite hand-crafted regexes that gate every migration |
  the script is the only enforcement layer for the naming contract used
  throughout the rest of the project |
  add `tests/unit/test_validate_naming_convention.py` with positive and
  negative fixtures for `fact_*`, `dim_*`, `config_*`, `v_*`, `usp_*`,
  bracketed identifiers, `IF NOT EXISTS`, and multi-statement files.

## Minor / nits

- **MN-01** | `tcp/db.py:58` | `class ConnectionError(TcpDbError)` shadows
  the builtin `ConnectionError`. The `noqa: A001` makes ruff quiet but
  callers that do `from tcp.db import ConnectionError` and elsewhere `from
  builtins import ConnectionError` get whichever import was last. Rename to
  `TcpConnectionError` (or keep but ensure the package re-export is the only
  public entry point).

- **MN-02** | `tcp/db.py:238-252` | `assert_session_context_set` creates an
  unmanaged cursor (`conn.cursor().execute(...)`) that is never closed |
  small leak per invocation; pyodbc tolerates it but `try: cursor = conn.cursor(); ... finally: cursor.close()` is cleaner and matches the rest of the module.

- **MN-03** | `tcp/db.py:153-159` | `password = os.environ.get("TCP_SQL_DEV_PASSWORD")`
  goes into a string concatenation and survives in `parts` / the returned
  connection string in memory for the lifetime of the calling frame |
  for a thesis project this is acceptable, but consider using `getpass`-style
  scrubbing or wrapping the secret in a Pydantic `SecretStr` field for
  Etapa 5 production hardening.

- **MN-04** | `pyproject.toml:67` | `[[tool.mypy.overrides]] module = ["pyodbc.*", "polars.*"]`
  with `ignore_missing_imports = true`, but `types-pyodbc>=5.0` is in the
  dev extra and provides stubs |
  the override shadows the stubs (`mypy` will not look up the package); drop
  `pyodbc.*` from the overrides and rely on `types-pyodbc` for full types.

- **MN-05** | `pyproject.toml:63` | `mypy_path = "tcp"` is unusual for a
  package built by hatchling — it points mypy at the inside of the package |
  remove the line; hatchling + `packages = ["tcp"]` is enough for mypy to
  resolve imports from the repo root.

- **MN-06** | `tests/unit/test_db.py:58, 257` | `with pytest.raises(Exception)`
  is `noqa: B017` but loses precision |
  use `pytest.raises(pydantic.ValidationError)` to make the intent (and the
  guard against future contract changes) explicit.

- **MN-07** | `docker-compose.dev.yml:9` | `MSSQL_TCP_PORT: "1433"` is
  ignored by the official `mssql/server:2022-latest` image (the listener is
  hardcoded to 1433) |
  remove the line to avoid implying a configurable port.

- **MN-08** | `docker-compose.dev.yml:3` | `mcr.microsoft.com/mssql/server:2022-latest`
  is mutable — `:2022-latest` resolves to a different digest over time |
  acceptable for dev (documented trade-off), but consider an immutable digest
  pin (e.g., `:2022-CU14-ubuntu-22.04`) or document the trade-off near the
  service definition.

- **MN-09** | `docs/dev_setup.md:121` | references `tests/sql/test_naming_convention.sql`,
  `test_rls_smoke.sql`, `test_fx_rate_completeness.sql` that are not part of
  this Etapa-2 deliverable |
  if these are Etapa-3 artifacts, note "(added in Etapa 3)" so a reader from
  Etapa 2 does not chase missing files.

- **MN-10** | `docs/dev_setup.md:227` | `tcp/README.md (if present; otherwise see tcp/__init__.py)` |
  `tcp/README.md` is present and authoritative — drop the conditional.

- **MN-11** | `tcp/db.py:30` | `_PWD_REDACTION_PATTERN` only masks `PWD=` |
  also consider `Password=` (the long form is accepted by some ODBC builds);
  add `(?:PWD|Password)=` to be defensive.

- **MN-12** | `.github/workflows/ci.yml:21,49,79,99` | five jobs all pin
  `ubuntu-22.04` |
  GitHub Actions deprecated this image in late 2025; bump to `ubuntu-24.04`
  (or `ubuntu-latest` with a tracking ADR).

- **MN-13** | `tests/integration/test_db_smoke.py:60-64` | the test
  `INSERT`s into `dim_UserRoles` then rolls back — but `pyodbc` opens an
  implicit transaction on first DML; the explicit `BEGIN TRANSACTION` nests,
  and the `ROLLBACK` only unwinds the outer if `XACT_ABORT` is ON |
  prefer `conn.autocommit = False` (already set in `open_connection`) plus
  `conn.rollback()` on the connection rather than a `ROLLBACK TRANSACTION`
  emitted through the cursor.

- **MN-14** | `tcp/db.py:120-128` | `match auth_mode:` with three exhaustive
  arms but no `case _:` |
  `StrEnum` plus the future-proof `case _: raise AuthError(...)` would prevent
  silent drop-through if a new mode is added without updating this builder.

- **MN-15** | `.gitleaks.toml:6-14` | allowlist scopes the password literal
  to two files but `regexes` is global; works as intended but the README
  example in `tcp/README.md` and the test file at `tests/unit/test_db.py:128`
  contain `p@ssw0rd!` which gitleaks will pick up as a low-confidence hit |
  add `p@ssw0rd!` to the regexes list to avoid noise on PR scans.

## Coverage map (`tcp/db.py`)

| Function | Happy path | Exception path | Edge cases | Verdict |
| -------- | ---------- | -------------- | ---------- | ------- |
| `SqlConfig.from_env` | yes | n/a | defaults + overrides + frozen | OK |
| `AuthMode.from_env` | yes | n/a | three branches (dev/MI/default) | OK |
| `_build_aad_kwargs` | indirect (via build) | n/a | missing `case _:` guard | nit MN-14 |
| `_redact` | yes | n/a | case-insensitive + no-op | OK |
| `build_connection_string` | AAD default, AAD MI, SQL dev | AuthError on missing creds | no test for `Password=` long form | OK |
| `open_connection` | env-driven happy path | pyodbc.Error -> ConnectionError | no test for cursor() failure | partial |
| `connection_for_user` | yes (set+reset+close) | body raises -> still resets | SET failure closes conn | **gap on `conn.cursor()` failure (CR-04)** |
| `assert_session_context_set` | UUID return, string parse | NULL row, no row | unmanaged cursor leak | nit MN-02 |

Effective coverage estimate: ~92 % of `tcp/db.py` once CR-04 is patched and
the new test for `conn.cursor()` failure is added.

## CI gate map

| Job | Tool | Failure threshold | Verdict |
| --- | ---- | ----------------- | ------- |
| static-analysis | ruff check | non-zero on any violation | OK |
| static-analysis | ruff format --check | non-zero on diff | OK |
| static-analysis | mypy --strict | non-zero on any error | OK |
| python-unit | pytest + coverage | `--cov-fail-under=85` (should be 90, MJ-04) | partial |
| sql-lint | sqlfluff | **`|| true` — never fails (CR-01)** | **BROKEN** |
| sql-lint | validate_naming_convention.py | exit code propagates | OK (regex gaps in MJ-05/MJ-06) |
| secret-scan | gitleaks-action@v2 | non-zero on findings | OK |
| secret-scan | bandit | **`-ll || true` — near-noise (CR-02)** | **BROKEN** |
| secret-scan | pip-audit | **`--strict || true` — masked (CR-03)** | **BROKEN** |

Three of nine CI gates are effectively disabled. This is the dominant
finding for Etapa 2 and is the main reason the verdict is
ACCEPT_WITH_CHANGES rather than ACCEPT.

## Recommendation

Resolve **CR-01 through CR-04** before merging Etapa 2 — the three CI
masking issues are one-line fixes and the connection-leak window is a
five-line refactor. The Major items can land in a follow-up commit but
should be closed before tagging `v1.0-db-ready`. Minor items can be
batched into a `chore: code-review pass 1 polish` commit at the next safe
stop point.

The cryptographic posture, the SESSION_CONTEXT contract implementation,
and the unit test design are all production-grade. The remaining gaps are
**process** (CI gates that warn instead of fail) and **portability**
(Windows-only developer running a bash-only setup guide), not correctness
of the connection layer itself.
