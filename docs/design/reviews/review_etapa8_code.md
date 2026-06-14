# Etapa 8 — Code Review (Python / SQL / scripts)

| Field | Value |
|---|---|
| **Reviewer role** | code-reviewer (Etapa 8 first pass, Python + SQL + shell) |
| **Date** | 2026-05-16 |
| **Branch** | `feat/azure-rewrite` |
| **Scope** | `scripts/compute_migration_checksum.py`, `tests/unit/test_compute_migration_checksum.py`, `tests/integration/test_telemetry_no_pii.py`, `function_app/triggers/ask.py` (`_emit_metrics` refactor), `db/migrations/V001__init.sql` / `V002__synth_logic.sql` (Section 13 / Section 3 placeholder + re-apply branch), `infra/scripts/postprovision.{ps1,sh}` Step 0, `docs/security/threat_model.md` RR-09 line. |
| **Working tree** | clean — last commit `2dc18aa feat(powerbi): Etapa 7 — TMDL semantic model + PBIR report + deploy automation`; Etapa 8 changes are uncommitted on top. |
| **Verdict** | **CHANGES-REQUESTED** — no Critical findings, but 4 Major items worth resolving before convergence: the missing-file path is uncaught, the SHA-256 helper has a CRLF round-trip blind spot, the SQL re-apply `UPDATE` is unsafe under concurrent applies, and the PII test's structlog-only capture would silently miss a regression to stdlib logging. The strengths column is strong — canonicalisation is deterministic, the test bench is well isolated, and the `_emit_metrics` change closes a real correlation gap. |

| Severity | Count |
|---|---:|
| Critical | 0 |
| Major | 4 |
| Minor | 7 |
| Nit | 5 |
| Strengths | 6 |

---

## 1. Summary

Etapa 8 adds three orthogonal pieces of work that together close residual risk RR-09 ("`schema_history.checksum` left as `TODO-checksum-set-by-CI`"), surface a real Application Insights correlation gap for the `tcp.ask.metrics` event (now fixed via the `_emit_metrics(log, ...)` refactor), and prove out the no-PII contract for the entire `/api/ask` handler via five new structlog assertions.

The work is small in line-count but load-bearing: every consumer of the checksum (CI gate, post-deploy smoke, the SQL `INSERT/UPDATE` itself) must apply byte-identical normalisation, and the PII contract is the privacy backbone of the assistant — a regression here cannot wait for monthly threat-model reviews to surface.

The code is structured cleanly, English-only, docstring-complete, and follows the project conventions (Python 3.12, `from __future__ import annotations`, frozen Pydantic models, `structlog.get_logger`). The four Major findings below are correctness-or-safety items the convergence pass should resolve; the Minors are cosmetic + mypy strictness items; the Nits are taste. No regressions detected against earlier stages — Etapa-5 ai MA-06 (server-side-only SQL-prefix log) is still honoured, the unified envelope helper is still the single response path, and RLS bypass usage is unchanged.

---

## 2. Major findings (must fix before convergence)

### code-MA-01. `compute_migration_checksum.py --paths <missing>` raises an uncaught `FileNotFoundError` — operator-hostile

**Files**
- `scripts/compute_migration_checksum.py:138` (the `compute_checksum(p) for p in paths` comprehension)
- `tests/unit/test_compute_migration_checksum.py:123-126` (the test *codifies* this behaviour: `pytest.raises(FileNotFoundError)`)
- `infra/scripts/postprovision.ps1:90-93` (the loop validates path existence before invoking sqlcmd, but the python helper has already been called by then)
- `infra/scripts/postprovision.sh:88-91` (mirror of the PS1)

**Problem**

The unit test under `test_main_fails_loudly_on_missing_file` *requires* that `main(["--paths", str(nonexistent)])` raises `FileNotFoundError` — i.e., a raw uncaught Python exception with a stack trace becomes part of the contract.

Two consequences:

1. CLI consumers (operators running `python scripts/compute_migration_checksum.py --paths foo.sql` manually) see a stack trace instead of a non-zero exit + clean `ERROR:` line on stderr — inconsistent with the empty-default-dir path which prints `ERROR: no migrations discovered.` and returns `1` (`compute_migration_checksum.py:131-136`).
2. The script's `--ci` mode promises "deterministic output", but a stack trace is not deterministic across Python versions (the wording of `FileNotFoundError` formatting differs slightly between 3.11 and 3.12 message templates).

Why-it-matters: in postprovision, the script is invoked under PowerShell `$ErrorActionPreference = 'Stop'` and bash `set -euo pipefail`. Both `Stop`/`-e` already terminate on a non-zero exit, so a stack trace adds nothing; conversely, a clean `[ERROR] Missing migration file: foo.sql` line + exit 1 is what an operator scanning the postprovision log expects to see. The current behaviour is *correct in outcome* but *incoherent in surface*.

**Suggested fix**

Wrap `compute_checksum(p)` in a try/except `OSError` block inside `main` and convert to:

```python
try:
    checksums = {p.name: compute_checksum(p) for p in paths}
except FileNotFoundError as exc:
    print(f"ERROR: {exc.filename}: file not found.", file=sys.stderr)
    return 1
except PermissionError as exc:
    print(f"ERROR: {exc.filename}: permission denied.", file=sys.stderr)
    return 2
```

Update `test_main_fails_loudly_on_missing_file` to assert `rc == 1` and that the stderr contains the path. The "fail loudly" intent is preserved — the script *still* returns a non-zero exit code — but the surface now matches the empty-dir path. Documenting the exit codes (1 = discovery/IO, 2 = invalid hex) in the module docstring would close the loop.

---

### code-MA-02. Canonicalisation drops lone `\r` characters — silently equates two non-equivalent SQL files

**Files**
- `scripts/compute_migration_checksum.py:55-58` (`text.replace("\r\n", "\n").split("\n")`)
- `tests/unit/test_compute_migration_checksum.py:33-37` (only `\r\n` ↔ `\n` is tested; bare `\r` is not)

**Problem**

The canonical-form rule, as implemented, is:

1. `text.replace("\r\n", "\n")` — replaces CRLF with LF only.
2. `.split("\n")` then `rstrip()` each line, re-join with `\n`.
3. `rstrip("\n")` of the whole string.

Two corner cases bypass step (1):

- A bare `\r` (old-classic-Mac line ending). `text.replace("\r\n", "\n")` leaves it intact, then `.split("\n")` produces a single line whose body contains the `\r`. `line.rstrip()` *does* strip trailing `\r` (Python's `str.rstrip()` defaults strip the whole ASCII whitespace set, including `\r`). So a file containing `SELECT 1;\r\rSELECT 2;` (two `\r` between statements) canonicalises to `SELECT 1;` — completely dropping `SELECT 2;` because the rstrip eats the whole line content as whitespace.
- An interior `\r` that is *not* immediately followed by `\n`: `line.rstrip()` strips the `\r` only when it's a trailing character. An interior `\r` is preserved; but if a developer's editor produces mixed `\r\n` + `\r` line endings (Notepad-on-Windows-saving-from-clipboard is the canonical case), the hash now depends on whether the embedded `\r` is at line end or not.

Concretely, the two files

```
SELECT 1;\r\n
SELECT 2;\n
```

and

```
SELECT 1;\n
SELECT 2;\n
```

hash identically (correct), but

```
SELECT 1;\r
SELECT 2;\n
```

hashes to a *different* value than the LF-only version — even though every editor on Earth would render them the same.

Why-it-matters: the docstring promise is that "a developer checking out the file on Windows with `core.autocrlf=true` produces the same hash as a Linux CI runner." That promise holds for CRLF↔LF round-trips, but it does not hold for the (admittedly rare) bare-CR case, and there is no test asserting that the bare-CR case is *not* a regression vector.

**Suggested fix**

Replace step 1 with a one-pass normalisation that handles all three line-ending conventions:

```python
text = raw.decode("utf-8")
# Normalise line endings: handle CR-LF, lone CR, and lone LF uniformly.
text = text.replace("\r\n", "\n").replace("\r", "\n")
lines = [line.rstrip() for line in text.split("\n")]
canonical = "\n".join(lines).rstrip("\n")
```

Add a test:

```python
def test_canonicalise_strips_lone_cr(helper) -> None:
    """Lone CR characters normalise to LF (old-Mac convention)."""
    cr = b"SELECT 1;\rSELECT 2;\r"
    lf = b"SELECT 1;\nSELECT 2;\n"
    assert helper.canonicalise(cr) == helper.canonicalise(lf)
```

For the truly belt-and-braces version, use `bytes.splitlines()` directly — it handles `\r\n`, `\r`, `\n`, `\v`, `\f`, `\x1c`, `\x1d`, `\x1e`, `\x85`, ` `, ` ` per the Python data model. The bytes-level loop avoids the encoding round-trip:

```python
def canonicalise(raw: bytes) -> bytes:
    lines = [line.rstrip() for line in raw.splitlines()]
    return b"\n".join(lines)  # splitlines() drops the trailing separator already
```

This also collapses the "strip one trailing newline" step into the splitlines semantics — fewer corners, fewer bugs.

---

### code-MA-03. `IF NOT EXISTS / ELSE UPDATE` is racy when two postprovision runs hit the same DB simultaneously

**Files**
- `db/migrations/V001__init.sql:1301-1308`
- `db/migrations/V002__synth_logic.sql:281-288`

**Problem**

The migration tail reads (V001 version; V002 is identical):

```sql
IF NOT EXISTS (SELECT 1 FROM dbo.schema_history WHERE script_name = 'V001__init.sql')
    INSERT INTO dbo.schema_history (script_name, applied_at_utc, checksum)
    VALUES ('V001__init.sql', SYSUTCDATETIME(), '__V001_CHECKSUM__');
ELSE
    UPDATE dbo.schema_history
       SET checksum = '__V001_CHECKSUM__'
     WHERE script_name = 'V001__init.sql'
       AND checksum <> '__V001_CHECKSUM__';
```

The `IF NOT EXISTS / INSERT` pair is the classic "upsert race": two concurrent sessions both observe `NOT EXISTS`, both try to `INSERT`, and the second one violates `PK_schema_history` (a NVARCHAR(200) primary key, V001:30). The migration's outermost `SET XACT_ABORT ON` (V001:22) will then abort the transaction — and `azd` will report the second concurrent postprovision as failed.

This is a real risk during CI: the same workflow can be retried while the previous attempt is still completing Step 0; the federated-credential `azd provision` command does not lock the SQL endpoint. There is no `BEGIN TRANSACTION ... WITH (HOLDLOCK, UPDLOCK)` around the upsert, nor a `MERGE` with the same hints.

Additionally, the `UPDATE ... WHERE checksum <> '__V001_CHECKSUM__'` *almost* preserves `applied_at_utc` correctly — it doesn't touch the column — but the *comment* claims "the FIRST-applied timestamp is what operators care about." That assertion is only true *because* the `UPDATE` does not set `applied_at_utc`, not because the code defends against accidental overwrite. A future cosmetic change ("let's also bump `applied_at_utc` so we know the most recent re-apply") would silently lose the first-applied stamp. There is no `last_reapplied_at_utc` column to receive the new timestamp.

NVARCHAR / VARCHAR comparison: the `checksum` column is `NVARCHAR(128)` (V001:32). The placeholder `__V001_CHECKSUM__` is a varchar literal in the SQL (unprefixed with `N'...'` in V001:1303,1306; V002 *does* prefix with `N'...'` in V002:283,286,288). SQL Server implicitly converts varchar → nvarchar for the comparison; the lower-collation precedence rule (Database default collation is `SQL_Latin1_General_CP1_CI_AS`) is preserved. For a 64-char hex string the comparison is safe — but the V001/V002 inconsistency (one uses `N'...'`, the other doesn't) is a sign of a contract that's evolving without a style guide. Pick one and lint it.

Why-it-matters: the schema ledger is the load-bearing artefact for RR-09. A spurious failure during CD makes operators distrust the ledger; a silent-overwrite race could mark a file as applied when it wasn't.

**Suggested fix**

Replace the if/else pair with a `MERGE` carrying the right lock hints, or a `BEGIN TRAN ... SERIALIZABLE` wrapper:

```sql
MERGE dbo.schema_history WITH (HOLDLOCK) AS target
USING (SELECT N'V001__init.sql' AS script_name) AS source
ON target.script_name = source.script_name
WHEN MATCHED AND target.checksum <> N'__V001_CHECKSUM__'
    THEN UPDATE SET checksum = N'__V001_CHECKSUM__'
WHEN NOT MATCHED BY TARGET
    THEN INSERT (script_name, applied_at_utc, checksum)
         VALUES (N'V001__init.sql', SYSUTCDATETIME(), N'__V001_CHECKSUM__');
```

Same pattern for V002, with `script_name = N'V002__synth_logic.sql'`. Standardise on `N'...'` literals across both migrations.

Optional but recommended: add a `last_reapplied_at_utc DATETIME2(3) NULL` column in a follow-up V003, so re-applies are observable without overwriting the original timestamp. The ledger then carries both "when was this first applied" and "when did we last reconcile the checksum" — which is the operator-friendly shape RR-09 actually wants.

---

### code-MA-04. PII test relies exclusively on `structlog.testing.capture_logs()` — a refactor to stdlib `logging` slips past it silently

**Files**
- `tests/integration/test_telemetry_no_pii.py:189, 217, 243, 256, 283` (every `with structlog.testing.capture_logs()` block)
- `function_app/triggers/ask.py:88` (`_log = structlog.get_logger(__name__)`)

**Problem**

`structlog.testing.capture_logs()` intercepts events emitted via structlog's processor chain. If a future maintainer makes any of the following changes, the test silently passes while telemetry leaks:

1. Adds a stdlib `logging.warning("missing principal oid=%s", oid)` line directly (e.g. as a "while we're here" diagnostic during debugging) — stdlib events do not flow through structlog's capture.
2. Adds a `print(...)` statement for debugging (e.g. while iterating on the rate-limit code) — `print()` is invisible to both structlog *and* stdlib capture.
3. Refactors `_log` to use `logging.getLogger(__name__)` directly (the project documents `BoundLogger` everywhere but does not actually call `structlog.configure()` — `tcp/db.py:45` uses a type-hint-only `BoundLogger` annotation, see Minor code-mi-06 below). At that point structlog's capture sees nothing, the test asserts on the empty event list, and `_assert_no_pii_in_events([])` returns trivially True (the for-loop body never runs).

Why-it-matters: the test's positive assertion at line 199 (`assert _TEST_OID_SUFFIX in flattened`) defends against the third case for the *success path only* — if no event ever bound `oid_suffix`, that one assertion fails. But the refusal-path / unknown-principal / execute-failure paths have no equivalent positive assertion. A handler that emits zero structlog events on those paths (because someone moved them to stdlib) would still pass.

Additionally, the `captured` variable from `structlog.testing.capture_logs()` is checked for truthiness only on the success path (`assert captured` at line 193). The other four paths skip that guard, so an empty capture is silently accepted as PII-safe.

The fix is twofold:

**Suggested fix**

(a) Add a `caplog` co-capture on every path so stdlib `logging.*` events are also scanned:

```python
def test_telemetry_redacts_pii_on_refusal_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="function_app")  # whole-tree
    # ... existing setup ...
    with structlog.testing.capture_logs() as captured:
        resp = ask_module.ask(_build_request(body={"question": _TEST_QUESTION}))
    assert resp.status_code == 422
    _assert_no_pii_in_events(captured)
    _assert_no_pii_in_stdlib(caplog.records)  # NEW
```

`_assert_no_pii_in_stdlib` flattens `record.getMessage()` + `record.args` and runs the same three substring checks.

(b) Add a minimum-event-count guard on every path:

```python
assert captured, f"no structlog events captured — telemetry may have been refactored away"
```

This converts the "silently empty" failure mode into a loud "the handler didn't log anything, fix the test".

(c) Optionally, monkeypatch `print` and `sys.stderr.write` to assert the handler never writes to stdout/stderr — that defends against the debug-`print` regression.

**Bonus rigour observation**: the canary OID `a1a1a1a1-bbbb-cccc-dddd-e0e0e0e0e0e0` is *well-chosen* — first 8 hex (`a1a1a1a1`) differ from last 8 hex (`e0e0e0e0`), so a leak of either half can be distinguished cleanly. The canary question text `tcp-pii-canary-string-7c0a4f-do-not-leak` is similarly unmistakable in any text body. These design choices are excellent (see strengths code-S-04). The structural blind spot is purely on the *transport*, not on the canary itself.

---

## 3. Minor findings

### code-mi-01. `python` vs `python3` invocation asymmetry between `postprovision.ps1` and `postprovision.sh`

**Files**
- `infra/scripts/postprovision.ps1:76, 103-fragment` (uses `python` and `Get-Content` for substitution)
- `infra/scripts/postprovision.sh:82, 105` (uses `python3` and a python heredoc for substitution)

**Problem**

PS1 calls `python "$repoRoot\scripts\compute_migration_checksum.py"`. On a vanilla Windows runner without the `py` launcher, this resolves to whatever `python.exe` is first on PATH — which could be 2.7 on some CI images. Bash uses `python3` which is unambiguous on every modern distro. The CI matrix should standardise either on `py -3` (Windows convention) or on `python3` (Linux convention) but not split.

**Suggested fix**

Use `py -3` on Windows and `python3` on Linux, or add a `Where-Object` guard in PS1 that errors on Python < 3.10. Documenting the minimum interpreter version in the script header would also close the loop.

---

### code-mi-02. PS1 placeholder substitution via `Get-Content -Raw` ≠ Linux read

**Files**
- `infra/scripts/postprovision.ps1:103`
- `infra/scripts/postprovision.sh:105-111`

**Problem**

`Get-Content -Raw` on Windows reads the file with the *system* encoding by default (UTF-8 with BOM detection in PS 7), and pipes through `.Replace($placeholder, $checksum)`. Linux side reads with `open(path, encoding="utf-8")`. If the migration file ever gains a UTF-8 BOM (e.g., an SSMS save), PS reads it as a leading character it then includes in `$rendered`; Linux Python's `open()` would also pass it through (it does not strip the BOM unless `encoding="utf-8-sig"`). On a CRLF-checked-out clone, `Get-Content -Raw` returns CRLF-terminated lines, which sqlcmd handles fine — but the *checksum* computed by `compute_migration_checksum.py` canonicalises CRLF → LF (per code-MA-02), so the substituted placeholder string `__V001_CHECKSUM__` is replaced with a value that has *no embedded line endings* (just 64 hex chars), which is BOM/CRLF-safe regardless. Net effect: probably fine in practice, but the asymmetry between the two scripts is a maintenance hazard.

**Suggested fix**

Use the same python helper on both sides for the substitution step. PS1 can call:

```powershell
$rendered = python "$repoRoot\scripts\substitute_placeholder.py" $migration $placeholder $checksum
```

This eliminates the encoding asymmetry and shrinks the maintenance surface from two scripts to one. The existing helper script already has a similar pattern (used in `postprovision.sh:105-111` for the SWA config substitution); promote it into `scripts/render_placeholder.py` and reuse.

---

### code-mi-03. `_emit_metrics(log, *, ...)` signature: the bound-logger param is positional-only and easy to misuse

**Files**
- `function_app/triggers/ask.py:458-489`
- `function_app/triggers/ask.py:662, 715` (both call sites)

**Problem**

The new signature is `def _emit_metrics(log, *, latency_ms, answer, row_count)`. The `*` correctly makes `latency_ms`, `answer`, `row_count` keyword-only. But the `log` parameter is *positional-or-keyword* — a future caller writing `_emit_metrics(latency_ms, ...)` (forgetting the logger) gets a confusing `int has no .info` failure deep inside the function body rather than a clean signature error at the call site.

The review-request asks: "Could we mark the param `*`-only (positional-only)?" Yes — Python 3.8+ syntax:

```python
def _emit_metrics(
    log: structlog.stdlib.BoundLogger,
    /,
    *,
    latency_ms: int,
    answer: AskAnswer,
    row_count: int,
) -> None:
```

The `/` makes `log` positional-only — it cannot be accidentally passed by keyword either (`_emit_metrics(log=mylog, latency_ms=...)` becomes a TypeError, so the call site stays uniform). Both existing callers already use positional form, so no migration needed.

**Suggested fix**

Add the `/` separator after `log`. Document in the docstring that the bound logger is the *first* arg by contract so the App-Insights correlation fields (`oid_suffix`, `scope`) are inherited.

---

### code-mi-04. `_emit_metrics` second call site emits `refused=False` on success even though the field is meaningful only on the refusal path

**Files**
- `function_app/triggers/ask.py:479-489` (the `refused=answer.refused` dimension)
- `function_app/triggers/ask.py:715` (success-path call, where `answer.refused` is False by construction)

**Problem**

The `refused` dimension is always emitted, but on the success path it's always `False` (the handler short-circuits at line 659-670 when `answer.refused` is True). The Kusto query that powers the rate-limit-spike alert filters on `metric_*` dimensions; a constant-False column adds noise without signal. Splitting into `tcp.ask.refusals` and `tcp.ask.success` events would be cleaner. The cost is one more event-name string to maintain.

Mild — not worth blocking on. Worth a comment in the docstring that `refused` is included for the refusal path's `_emit_metrics` call and is constant `False` on the success path.

---

### code-mi-05. Test `test_main_returns_error_on_empty_default_dir` mutates the module attribute, not a CLI flag

**Files**
- `tests/unit/test_compute_migration_checksum.py:129-133`

**Problem**

The test pins `DEFAULT_MIGRATIONS_DIR` to an empty tmp_path. That works because the module reads the constant lazily inside `main()`. But the test does not exercise the equivalent *real* failure mode: someone running the script from a fresh checkout where `db/migrations/` was never created (rare, but possible during early-stage refactors). The `discover_migrations` glob returns an empty list, and the test correctly asserts rc == 1.

Two improvements:

1. Assert that the error message reaches stderr (`capsys.readouterr().err` contains `"no migrations discovered"`).
2. Add a sibling test that passes `--paths` with an empty list — the argparse `nargs="+"` rejects empty lists at parse time, but documenting that in a test is useful.

**Suggested fix**

```python
def test_main_returns_error_on_empty_default_dir(
    helper, tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(helper, "DEFAULT_MIGRATIONS_DIR", tmp_path)
    rc = helper.main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "no migrations discovered" in err
```

---

### code-mi-06. `_emit_metrics` parameter type `structlog.stdlib.BoundLogger` is aspirational — no `structlog.configure()` in the codebase

**Files**
- `function_app/triggers/ask.py:459` (type hint)
- `tcp/db.py:24-25, 45` (the only file that imports `BoundLogger` and annotates)
- (codebase-wide grep: no `structlog.configure(...)` call exists)

**Problem**

Without a `structlog.configure()` call, `structlog.get_logger()` returns a `BoundLoggerLazyProxy`, not a `BoundLogger`. The type hint is *technically* incorrect — `mypy --strict` would flag this if it traced the assignment chain rigorously, but it doesn't because the assignment happens implicitly at the call site (`log = _log.bind(oid_suffix=...)` returns a `FilteringBoundLogger | BoundLoggerBase | BoundLoggerLazyProxy` depending on whether configure ran).

For a thesis-grade codebase, the right answer is to add a one-time `structlog.configure(...)` call at function-app cold-start, which:

1. Pins the wrapper class to `structlog.stdlib.BoundLogger`, making the type hint accurate.
2. Threads the App Insights JSON renderer (`structlog.processors.JSONRenderer()`) so the captured events are App-Insights-correlatable in production, not just under `structlog.testing.capture_logs()` in tests.

Without this, the structured logs *might* still surface to App Insights (Functions auto-bridges stdlib logging records), but the dimensions (`oid_suffix`, `scope`, `metric_*`) only land if structlog converts them to KV-formatted strings — which the default `KeyValueRenderer` does. Acceptable for thesis posture; documented as Etapa-12 work elsewhere (`docs/security/threat_model.md` RR-06).

**Suggested fix**

Either (a) loosen the type hint to `structlog.types.FilteringBoundLogger` (the actually-most-general type) and add a `# mypy --strict happy` comment, or (b) add a `_configure_structlog()` call in `function_app/function_app.py` that runs once at module import. Option (b) is cheap and aligns the runtime with the type hints.

---

### code-mi-07. The bash heredoc inside `postprovision.sh` is `<<'PYEOF'` (single-quoted) — but uses `sys.argv` which is *fine* — worth a comment

**Files**
- `infra/scripts/postprovision.sh:105-111`

**Problem**

The heredoc `<<'PYEOF'` correctly prevents shell expansion of the python code body. The substituted values (`$migration`, `$placeholder`, `$checksum`) are passed as `sys.argv` rather than interpolated — which is the right call (defends against `$placeholder` containing a `$` character or backtick). No bug here; the *style* is right but undocumented. A one-line comment "single-quoted heredoc + sys.argv = injection-safe" inside the script would close the implicit contract for future maintainers.

The `set -euo pipefail` propagation works through the python heredoc because the exit code of `python3 - ...` is the exit code of the command substitution; the rendered text is captured into `$rendered`, and a python crash would propagate via `set -e` … *except* `$()` masks command exit codes when used inside an assignment under bash's POSIX-mode quirks. To be safe under `set -euo pipefail`, decompose:

```bash
rendered=$(python3 ... ) || error "placeholder substitution failed for $base"
```

Currently the script does not check the return code of the `python3 -` invocation that produces `$rendered`. If the python helper raises, `$rendered` is empty and the subsequent `echo "$rendered" | sqlcmd ...` silently runs an empty script against the database — which `sqlcmd -b` would treat as a no-op success.

**Suggested fix**

Add explicit `|| error` after the `$()` capture, or use `pipefail` with an intermediate file.

---

## 4. Nits (style + cosmetics)

### code-n-01. `compute_migration_checksum.py` module docstring uses `\\n` (escaped LF) when describing the canonicalisation

**File**: `scripts/compute_migration_checksum.py:25, 27`

The double-backslash works because the docstring is a regular string, but it renders as `\n` literal in `help()` output instead of an actual newline. Use a raw string `r"""..."""` or just spell `LF` / `newline` in prose. Cosmetic only — `mypy --strict` is silent on this.

### code-n-02. `discover_migrations` lexicographic order is documented but not enforced — a `V010__foo.sql` would sort *before* `V002__bar.sql`

**File**: `scripts/compute_migration_checksum.py:74`

`sorted(directory.glob("V*.sql"))` produces `["V001__init.sql", "V010__foo.sql", "V002__bar.sql"]` for ASCII sort because `'0' < '1'`. The current state has only V001 / V002 so it doesn't trip; once V010+ exists, the ordering becomes a real bug. Either zero-pad to 4 digits (`V0001__init.sql`) and document the contract, or use a `natsort`-style key. The same lexicographic-ordering assumption appears in `postprovision.{ps1,sh}` Step 0 hardcoded migration list — at least there the list is explicit.

### code-n-03. The PII canary string contains the literal `7c0a4f` which is *itself* a 6-hex-char sequence

**File**: `tests/integration/test_telemetry_no_pii.py:49`

`_TEST_QUESTION = "tcp-pii-canary-string-7c0a4f-do-not-leak"`. The `7c0a4f` substring is 6 hex chars; if a future log event prints a colour code or another hex (e.g. `#7c0a4f`), the substring search in `_assert_no_pii_in_events` would *correctly* flag it as a leak. Probably fine in practice (the canary token includes `-` boundaries) but worth pointing out that the canary intentionally avoids ambiguity by including the `tcp-pii-canary-string-` prefix.

### code-n-04. `_RATE_LIMIT_BUCKETS.clear()` in the autouse fixture is not symmetric with `_pin_forwarded_secret`

**File**: `tests/integration/test_telemetry_no_pii.py:124-128`

`_pin_forwarded_secret` uses `monkeypatch.setenv` which auto-reverts; `_clear_rate_limit_buckets` mutates global state and does not yield/restore. Net effect on the suite is fine (each test re-clears at setup), but the asymmetry suggests one of the two patterns is the project standard — pick one for consistency. `tests/unit/test_ask_trigger.py:34-36` uses the same clear-on-setup pattern, so that's the established convention. Leave as-is; documenting it would help future readers.

### code-n-05. SQL `IF NOT EXISTS ... ELSE UPDATE ... AND checksum <> ...` produces a 0-rowcount `UPDATE` when the checksum already matches — harmless but worth noting

**Files**: `db/migrations/V001__init.sql:1305-1308`, `V002__synth_logic.sql:285-288`

The `AND checksum <> '__V001_CHECKSUM__'` predicate guards against rewriting the same value (good — preserves the row's `last_modified` clock if SQL ever adds one). When the predicate matches, the `UPDATE` is a no-op but still produces an `INSERTED` notification under change-data-capture if CDC is ever enabled on `schema_history`. Harmless on the Free tier (CDC is paid).

---

## 5. Strengths

### code-S-01. Canonicalisation rule is documented at the function level AND at the module level, with the same four-step recipe

Both `scripts/compute_migration_checksum.py:20-32` (module docstring) and `scripts/compute_migration_checksum.py:49-54` (`canonicalise` docstring) spell out the same four-step rule with cross-references. Future maintainers reading either entrypoint get the full contract.

### code-S-02. `_sqlcmd_var_name` truncation to leading `V<digits>` makes the placeholder shape rename-proof

`V001__init.sql` and `V001__init_v2.sql` both produce `V001_CHECKSUM`. A future maintainer who renames a migration's descriptive suffix does not break the placeholder substitution. The trade-off (two files with the same leading V-digit collide silently) is acceptable because V-numbering is unique-by-convention.

### code-S-03. `_emit_metrics(log, ...)` refactor is the right architectural call

The original `_emit_metrics()` used the module-level `_log` and so metric events did not carry the request-scoped `oid_suffix` + `scope` bindings. App Insights' KQL queries grouping by `customDimensions.oid_suffix` would have produced an empty result for the metric trace events — a real, silent observability gap. The new signature inherits the bindings, both callers pass `log` (post-bind), and the docstring explains the why. Good catch from the PII test, surfaced and fixed cleanly.

### code-S-04. Canary values in `test_telemetry_no_pii.py` are designed against false negatives

- The OID `a1a1a1a1-bbbb-cccc-dddd-e0e0e0e0e0e0` is constructed so the permitted suffix (`e0e0e0e0`) is *not* a prefix of the disallowed full hex — a leak of either form is unambiguously detectable.
- The question text `tcp-pii-canary-string-7c0a4f-do-not-leak` is long enough (43 chars), uniquely shaped, and self-documenting in any captured log payload.
- `_flatten_event(event)` uses `json.dumps(..., default=str, ensure_ascii=False)` so nested UUID/datetime/Decimal values are coerced into searchable text — no field type can hide the canary by being un-JSON-serialisable.

### code-S-05. Tests respect the established style (`tests/unit/test_ask_trigger.py`)

Both new files (`test_compute_migration_checksum.py` and `test_telemetry_no_pii.py`) follow the project conventions: `from __future__ import annotations`, `@pytest.fixture(autouse=True)` for isolation, no shared global state, `monkeypatch` for env vars, deliberate docstring on every test method (1-3 lines). No drift from `tests/unit/test_ask_trigger.py`.

### code-S-06. `--ci` flag adds a meaningful guard beyond the `--json` / kv default

The `--ci` branch re-asserts that every checksum is 64 chars of `[0-9a-f]` (`compute_migration_checksum.py:147-156`). This catches the edge case where `canonicalise()` silently returns malformed bytes (e.g., a future refactor that re-encodes through `latin-1` and produces non-hex output) — a defence the `--json` path does not have. Distinct exit code (`2` vs `1`) lets CI grep on it.

---

## 6. Walk-through: canonicalisation correctness against the prompt's worked example

The review prompt asks: "Is the rule 'normalize CRLF → LF, strip trailing whitespace per line, strip one trailing newline' both deterministic AND sensitive enough to catch a meaningful change? Walk through an example."

**Deterministic across platforms**: yes, for CRLF↔LF round-trips (proven by `test_canonicalise_strips_crlf`). **Not deterministic for lone CR** (see code-MA-02).

**Sensitivity to meaningful changes**: yes — any character difference in the body content flips the hash. Example:

- Original (LF): `IF NOT EXISTS (SELECT 1 FROM dbo.schema_history WHERE script_name = 'V001__init.sql')\n`
- Edited (added comma): `IF NOT EXISTS (SELECT 1, 2 FROM dbo.schema_history WHERE script_name = 'V001__init.sql')\n`

After canonicalisation both produce a bytes value differing in the body, so `sha256(...)` produces two distinct 64-hex values. The `<>` comparison in the SQL `UPDATE` catches the second case and refreshes the row.

**Insensitive to non-meaningful changes** (the property the canonicalisation tries to preserve):

- CRLF on Windows checkout → matches LF on Linux runner. **Confirmed**.
- Trailing space at end of a comment line → stripped by `line.rstrip()`. **Confirmed**.
- Optional trailing newline (POSIX nl at EOF) → stripped by `rstrip("\n")`. **Confirmed**.
- BOM at file start → **not stripped**. A new BOM addition flips the hash. Probably desirable (BOM means encoding drift, which should re-trigger the gate). Document it.
- Tab → space substitution inside a line → flips the hash. **Correct**: cosmetic-but-meaningful change deserves a re-record.

Net: the rule is *right*, modulo the lone-CR corner. The fix in code-MA-02 closes that gap without expanding the contract beyond "ASCII whitespace at line end is irrelevant, line endings normalise to LF, optional final newline is irrelevant".

---

## 7. mypy strict-mode cleanliness check

I did not run `mypy --strict tcp tests scripts function_app` from this review session, but a manual walk through the new files:

- `scripts/compute_migration_checksum.py` — clean. All functions have full annotations. `argparse.Namespace.paths` is typed `list[Path] | None` correctly. The `dict[str, str]` comprehension on line 138 is concrete.
- `tests/unit/test_compute_migration_checksum.py` — the `helper` fixture is untyped (`def helper():` with no return type). `mypy --strict` complains about missing return types on every test function (`-> None` is required). Test methods *do* have `-> None`; fixtures do not. Conventional in pytest, but the project's strict mode does flag `tests/` per `pyproject.toml` — verify against the existing CI.
- `tests/integration/test_telemetry_no_pii.py` — same pattern; the helper functions have `-> str` / `-> None` annotations. The dict-of-Any return type in `_flatten_event(event: dict[str, Any]) -> str` is correct.
- `function_app/triggers/ask.py:459` — `log: structlog.stdlib.BoundLogger` is aspirationally correct but at runtime is a `BoundLoggerLazyProxy` (see code-mi-06). `mypy --strict` does not catch this because structlog's stubs declare `get_logger()` returns `BoundLoggerLazyProxy`, which the trigger then `.bind(...)`s — the result type is `Any` in practice.

**Suggested verification**: run `uv run mypy --strict scripts tests function_app/triggers/ask.py` and check for new errors. The likely-new errors are:

- `test_compute_migration_checksum.py:18` — Function is missing a return type annotation (`def helper() -> ModuleType`).
- `test_compute_migration_checksum.py:18` — `Returning Any from function declared to return ...` (the `spec.loader.exec_module` chain returns Any).

Both are easy to fix.

---

## 8. Test coverage observation

The CI gate is `--cov-fail-under=90`. With the new files:

- `compute_migration_checksum.py:138` (the `compute_checksum(p) for p in paths` line) is covered indirectly by `test_main_kv_output_matches_compute` and `test_main_json_output_is_parseable`.
- `compute_migration_checksum.py:130-136` (the empty-discovery branch) is covered by `test_main_returns_error_on_empty_default_dir`.
- `compute_migration_checksum.py:147-156` (the `--ci` hex-validation branch) is NOT directly covered with a malformed-hash scenario. The current `test_main_ci_mode_returns_zero_for_real_repo` only exercises the positive case. A test that monkeypatches `compute_checksum` to return `"not-hex"` and asserts `rc == 2` would add ~10 lines of coverage and is worth doing.

- `function_app/triggers/ask.py:458-489` (`_emit_metrics`) — directly covered by the success/refusal/execute-failure paths in the PII test. Coverage should improve, not degrade.

Net: coverage stays above 90 % with no special attention; one additional negative test on `--ci` mode would round out the coverage of the helper.

---

## 9. RR-09 closure verification

`docs/security/threat_model.md:296` reads:

> ~~`schema_history.checksum` left as `'TODO-checksum-set-by-CI'`~~ — **CLOSED in Etapa 8.** Migrations now carry `__V<n>_CHECKSUM__` placeholders that `infra/scripts/postprovision.{ps1,sh}` Step 0 substitutes with the canonicalised SHA-256 (computed by `scripts/compute_migration_checksum.py`) before piping the file to sqlcmd. The CI gate (`ci.yml › sql-lint`) and the CD smoke job (`cd.yml › smoke`) both fail when an unsubstituted placeholder appears in `dbo.schema_history`.

**Verdict on the closure claim**: ACCEPTED *with one caveat*. The CI gate (`ci.yml › sql-lint`) and CD smoke job (`cd.yml › smoke`) are referenced but I did not load those files in this review — the convergence pass should verify their *actual* presence (a grep for `schema_history` + `TODO-checksum-set-by-CI` against the workflow YAML). If those gates exist as claimed, RR-09 is correctly closed; if either is missing, the closure is documentation-only.

The line 313 reference in the same threat model (`A08 Software and Data Integrity` row) still mentions `schema_history.checksum TODO is residual RR-09` — that's the *summary* row in the OWASP mapping table and should also be updated to reflect the closure. Otherwise the doc reads inconsistently (closure in §7, residual in §8).

---

## 10. Convergence checklist (suggested for the fix agent)

| ID | Action | Files |
|---|---|---|
| code-MA-01 | Catch `OSError` in `main()`, return exit 1 with stderr message; update test to assert `rc == 1` + stderr text | `scripts/compute_migration_checksum.py`, `tests/unit/test_compute_migration_checksum.py` |
| code-MA-02 | Replace step 1 with `text.replace("\r\n", "\n").replace("\r", "\n")` OR use `splitlines()` bytes-level; add `test_canonicalise_strips_lone_cr` | `scripts/compute_migration_checksum.py`, `tests/unit/test_compute_migration_checksum.py` |
| code-MA-03 | Replace `IF NOT EXISTS / ELSE UPDATE` with `MERGE ... WITH (HOLDLOCK)`; standardise on `N'...'` literals across V001 and V002 | `db/migrations/V001__init.sql`, `db/migrations/V002__synth_logic.sql` |
| code-MA-04 | Add `caplog` co-capture on every PII path; assert `captured` is non-empty on every path; add a stdlib-no-PII helper | `tests/integration/test_telemetry_no_pii.py` |
| code-mi-01 | Standardise on `py -3` / `python3` per platform; document in script header | `infra/scripts/postprovision.ps1` |
| code-mi-02 | Promote placeholder substitution into a shared `scripts/render_placeholder.py` used by both PS1 and bash | `infra/scripts/postprovision.{ps1,sh}`, `scripts/render_placeholder.py` (new) |
| code-mi-03 | Add `/` after `log` in `_emit_metrics` to make it positional-only | `function_app/triggers/ask.py` |
| code-mi-07 | `rendered=$(...) || error "..."` to defend against silent helper failures under `set -euo pipefail` | `infra/scripts/postprovision.sh` |
| code-n-02 | Zero-pad V-numbers to 4 digits (`V0001`) OR document the V001-V099 ceiling | `db/migrations/`, project docs |
| §9 caveat | Verify `ci.yml › sql-lint` and `cd.yml › smoke` *actually* fail on an unsubstituted `__V<n>_CHECKSUM__`; update §8 OWASP A08 row of threat model to reflect closure | `.github/workflows/ci.yml`, `.github/workflows/cd.yml`, `docs/security/threat_model.md` |

Items code-mi-04, code-mi-05, code-mi-06, and all code-n-* nits are taste / cosmetics and can be deferred to Etapa-12.

---

## 11. Files reviewed (absolute paths)

1. `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\scripts\compute_migration_checksum.py`
2. `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\unit\test_compute_migration_checksum.py`
3. `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\integration\test_telemetry_no_pii.py`
4. `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\triggers\ask.py`
5. `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\db\migrations\V001__init.sql` (sections 0 and 13)
6. `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\db\migrations\V002__synth_logic.sql` (section 3)
7. `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\scripts\postprovision.ps1` (Step 0 block)
8. `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\scripts\postprovision.sh` (Step 0 block)
9. `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\docs\security\threat_model.md` (RR-09 line)

Context-only (for cross-reference, not modified in Etapa 8): `tcp/ai/anthropic_client.py`, `tests/unit/test_ask_trigger.py`, `tests/conftest.py`, `pyproject.toml`, `docs/design/reviews/review_etapa6_security_sweep.md` (RR-09 origin), `docs/design/reviews/review_etapa7_holistic.md` (style template).
