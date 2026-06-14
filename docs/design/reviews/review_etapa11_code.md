# Etapa 11 — Code Review (light-touch maintenance pass)

**Verdict:** APPROVED FOR MERGE (with two Minor follow-ups noted for Etapa 12).

The six Etapa-11 changes are correct, narrow, and consistent with the maintenance posture they advertise. The mypy-strict expansion to `function_app/` is sound, the Bicep `bypass` conditional is behaviourally equivalent to the previous hard-coded value (and now expresses intent), and the new circular-import smoke test plausibly catches a real regression. Two concerns are worth fixing before Etapa 12 finalisation: the `tests.*` mypy disable list is broader than necessary (notably `attr-defined` and `operator`), and the local `swa-config-placeholder-guard` reads the working tree rather than the staged blob (a small evasion path).

---

## Critical

None.

---

## Major

### code11-MJ-01 — `.pre-commit-config.yaml:86` guard reads working tree, not staged blob

**File:** `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\.pre-commit-config.yaml:86`

**Summary.** The inline `swa-config-placeholder-guard` script opens `swa/staticwebapp.config.json` via `open(path, encoding='utf-8').read()`. That reads whatever is on disk in the working tree at hook-execution time, not the staged blob that will actually be committed.

**Why it matters.** The classic stage-versus-working-tree evasion: `git add swa/staticwebapp.config.json` (with substituted secret) → `git restore swa/staticwebapp.config.json` (restores working tree to placeholder) → `git commit`. The hook reads the placeholder from disk, returns 0, and the staged blob (still carrying the resolved SWA forwarded secret) lands in git. This is exactly the footgun arch10-MJ-03 wanted closed; the implementation misses the staged-vs-working-tree axis.

Compounding effect: the hook also sets `pass_filenames: false`, so even if pre-commit passes a list of staged filenames, the script doesn't honour them — making the staged-blob path conceptually trickier to bolt on.

**Suggested fix.** Read the staged blob directly via `git show :swa/staticwebapp.config.json` (the colon prefix targets the index), e.g.:

```yaml
- id: swa-config-placeholder-guard
  entry: python -c "import subprocess, sys; r = subprocess.run(['git','show',':swa/staticwebapp.config.json'], capture_output=True, text=True); sys.exit(0) if r.returncode != 0 or '<value-set-by-postprovision>' in r.stdout else (print('ERROR: staged swa/staticwebapp.config.json has postprovision substitution applied. Run `git restore --staged --worktree swa/staticwebapp.config.json` before committing.', file=sys.stderr) or sys.exit(1))"
  language: system
  files: ^swa/staticwebapp\.config\.json$
  pass_filenames: false
```

The `r.returncode != 0` clause keeps the no-file branch passing (matches the current `content == ''` short-circuit). gitleaks also runs in the same hook config, but its detection rule for SWA forwarded secrets would need a custom signature; the placeholder guard is the right *operational* fix — it just needs to inspect the right bytes.

### code11-MJ-02 — `tests.*` `disable_error_code` list disables real bug-catching codes

**File:** `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\pyproject.toml:146-153`

**Summary.** The Etapa-11 override disables five error codes for `tests.*`: `no-untyped-def`, `unused-ignore`, `attr-defined`, `operator`, `no-any-return`. Three of those (`attr-defined`, `operator`, `unused-ignore`) hide *real* bug classes, not just monkey-patch friction. Two examples drawn from the live tree:

- `attr-defined` catches typos in monkey-patches themselves, e.g. `monkeypatch.setattr(module, 'connection_for_user', fake)` where `connection_for_user` was renamed last sprint — the typo is silent if you also disable `attr-defined`, and the test passes for the wrong reason (the original function still runs, the patch is a no-op). This is the bug that wastes the most time during debugging because the test is green.
- `operator` catches `None - datetime` and similar inadvertent `None`-arithmetic at type-check time. The lone existing suppression at `tests/unit/test_synth_distributions.py:148` exists precisely because the comprehension's filter on `r.time_exit is not None` doesn't narrow inside the generator expression — a *targeted* `type: ignore[operator]` was the correct fix, and disabling the code module-wide loses signal for every other test.
- `unused-ignore` is the one that becomes self-suppressing: with the code disabled module-wide, every existing `# type: ignore[...]` comment is by definition unused — but mypy can't report that because `unused-ignore` itself is in the disable list. Net effect: dead-ignore comments accumulate, and the next developer can't tell which suppressions are load-bearing.

**Why it matters.** "Tests monkey-patch heavily" is a real concern but it's narrowly scoped to ~10-20 sites in the suite, not the whole module space. Disabling at the module level pays a steep price (lost signal across hundreds of tests) for a narrow benefit (skipping `# type: ignore` at the patch sites). The CLAUDE.md quality bar argues for the opposite trade-off.

**Suggested fix.** Drop `attr-defined`, `operator`, and `unused-ignore` from the disable list. Keep `no-untyped-def` (genuinely useful for fixture readability) and `no-any-return` (intentional Any in mock returns). Apply targeted `# type: ignore[attr-defined,operator]` at the actual monkey-patch sites, e.g.:

```python
monkeypatch.setattr(mod, "connection_for_user", fake)  # type: ignore[attr-defined]
```

The grep at `tests/` shows only 2 existing `# type: ignore[...]` comments in the suite (one `operator`, one `attr-defined`), so the audit cost is minimal. Re-enabling these surfaces precisely the bug class the project cares about most (silent monkey-patch typos, None-arithmetic).

---

## Minor

### code11-MN-01 — `.pre-commit-config.yaml:31` ruff pin lags ~18 months

**File:** `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\.pre-commit-config.yaml:31`

**Summary.** `ruff-pre-commit` is pinned to `v0.6.9` (Oct 2024). At the project's current date (May 2026), ruff has shipped multiple minor lines; the local hook applies an older rule-set than `pyproject.toml [tool.ruff]` resolves against in CI (`ruff>=0.5` upper-unbounded). A new lint rule that lands in CI (post-`uv sync` resolution) would not fire locally during `pre-commit run`.

**Why it matters.** The whole point of the pre-commit baseline (per the file header) is "developer workstation fails fast instead of after pushing." A stale pin reverses that: developers see green locally, then red in CI.

**Suggested fix.** Bump to the latest stable on the same line — likely `v0.9.x` or `v0.11.x` depending on what's current at merge time. Optionally add a Dependabot rule for `astral-sh/ruff-pre-commit` so the pin auto-PRs quarterly (same pattern as the SHA-pinned GitHub Actions in `ci.yml`). Same for `sqlfluff` `3.4.2` (pinned to the same major as the dev extra, so probably OK, but worth a quick check against the 3.x latest).

### code11-MN-02 — `pyproject.toml:46` `freezegun` is in `dev` extras but never imported

**File:** `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\pyproject.toml:46`

**Summary.** `grep -r freezegun` returns only the `pyproject.toml` declaration. No test or runtime module imports it. The Etapa-11 commit message claims a "dependency audit" — removing the never-used `types-pyodbc` is exactly the kind of cleanup that should also drop `freezegun` if it's genuinely unused.

**Why it matters.** Dev-extra cruft inflates install time, broadens the attack surface for `pip-audit --strict` (which runs on dev extras), and erodes the audit signal — next time the question is "what's the smallest viable dev install?" the answer is fuzzier than it needs to be.

**Suggested fix.** Remove the line. If a future test wants timezone freezing, the project's preferred idiom (per the existing `tests/conftest.py:296` `autouse` autoload of `TCP_SYNTH_SEED_OFFSET`) is environment-variable seeding plus passing explicit `date` overrides to `run_daily(today=...)`, not `freezegun`.

### code11-MN-03 — `tests/unit/test_function_app_imports.py:48` re-runs `structlog.configure` as a side-effect

**File:** `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\unit\test_function_app_imports.py:48`

**Summary.** The `fresh_function_app` fixture wipes `sys.modules` entries for `function_app.*` and re-imports `function_app.function_app`. Module-import side effects then re-run, including the `structlog.configure(...)` call at `function_app/function_app.py:43`. Because `structlog.configure` mutates *global* state (the structlog default-logger config), every test that runs after `test_function_app_imports.py` sees the post-fixture config, not its previous one.

**Why it matters.** In practice it's a no-op (the second `configure` call uses the same processor chain as the first), so 288 tests pass today. But the contract is fragile — the day someone adds a structlog test that itself calls `structlog.configure(...)` with different processors, that test now leaks into siblings depending on pytest ordering. This is the same class of "passes today, breaks next month under a benign change" bug the smoke test is supposed to *prevent*.

**Suggested fix.** Either (a) snapshot `structlog._config._CONFIG` at fixture setup and restore it at teardown, or (b) split `function_app.function_app` so `structlog.configure(...)` lives in a separate `_configure_logging()` function called only inside the Functions worker entry point (not at module import). Option (b) is cleaner long-term and removes the import-time global mutation.

### code11-MN-04 — `function_app/triggers/ask.py:657` `from typing import cast, Literal` inside a function body

**File:** `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\triggers\ask.py:657`

**Summary.** The Etapa-11 cast fix imports `cast` and `Literal` inside the function body:

```python
from typing import cast, Literal as _Literal
typed_scope = cast(_Literal["trader", "team_lead", "floor_manager", "admin"], scope)
```

This pattern is fine for runtime correctness (the imports are stdlib, no side effects) but offends `ruff PLC0415` (in-function imports) — and the per-file ignore for `function_app/triggers/ask.py` doesn't list `PLC0415`. The reason this passes today is that `PLC0415` is in `select = ["...", "PL", ...]` but the per-file ignores for `ask.py` are `["PLR0911", "PLR0912", "PLR0915", "ANN401", "S105"]` — the `PL` prefix is selected, but only PLR0911-15 are suppressed; `PLC0415` would still fire.

**Why it matters.** Probably it already fires and the suite reports it. If not, the next ruff bump (see MN-01) may surface it. Either way, the cast belongs at module scope where the other `Literal` import lives (already in `tcp.ai.anthropic_client:141` via `from typing import Literal` — could re-export).

**Suggested fix.** Move the imports to the top of `function_app/triggers/ask.py` (`from typing import Literal as _AskScopeLiteral, cast` next to the existing imports) and reference `_AskScopeLiteral` in the function body. Single Literal definition for the four-tuple, easier to keep in sync with `tcp/ai/anthropic_client.py:141`.

### code11-MN-05 — duplicated `Literal` four-tuple in `ask.py:658` and `anthropic_client.py:141`

**File:** `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\triggers\ask.py:658` and `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tcp\ai\anthropic_client.py:141`

**Summary.** The literal `["trader", "team_lead", "floor_manager", "admin"]` is now repeated in three places: `_ALLOWED_SCOPES` (line 111, as `frozenset[str]`), the `AskQuestion.scope` field (`tcp/ai/anthropic_client.py:141`, as `Literal[...]`), and the new `cast(_Literal[...], scope)` at `ask.py:658`. If a fifth role is added, two of the three sites need to update in lockstep, and mypy will not catch a missed one (the `cast` is a runtime no-op, and the `_ALLOWED_SCOPES` runtime check is what gates membership).

**Why it matters.** The Etapa-11 fix solved the symptom (the typing error) without addressing the underlying duplication. Three sources-of-truth for "the set of valid scopes" is a maintenance risk; one source plus two derivations is healthier.

**Suggested fix.** Define `Scope = Literal["trader", "team_lead", "floor_manager", "admin"]` once in `tcp/ai/anthropic_client.py` (or a shared `tcp/ai/types.py`), then derive `_ALLOWED_SCOPES = frozenset(get_args(Scope))` from it. The cast at `ask.py:658` becomes `cast(Scope, scope)`. Single source of truth, mypy-checkable, runtime-checkable.

### code11-MN-06 — `infra/main.bicep` does not thread `kvDefaultAction`/`storageDefaultAction`

**File:** `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\modules\keyvault.bicep:86`, `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\modules\storage.bicep:38`

**Summary.** Both new params have sensible defaults (`'Allow'`), so omitting them at the call site in `main.bicep` produces today's behaviour. However, the Etapa-11 commit message says "the future Deny-flip is a one-parameter change" — that's only true if the param is exposed at the top of `main.bicep` (one location to edit). Currently the only edit is at the module's default, which is reasonable for v1 but doesn't actually deliver the advertised single-knob flip.

**Why it matters.** The Bicep change is fine *behaviourally* (the `bypass` conditional is correct, ARM output is unchanged for `defaultAction='Allow'`, so PSRule + checkov will see the same template they passed in Etapa 4). But the "one-parameter change" promise from the commit message is not really delivered until `main.bicep` exposes the toggle.

**Suggested fix.** Either accept the trade-off as-is and tweak the comment in `keyvault.bicep:81` to say "module-level default" (more honest), or thread the param through `main.bicep` so the Deny-flip is literally one keystroke in `main.parameters.prod.json`. Either resolution closes arch10-MJ-04 cleanly.

---

## Strengths

1. **`tcp/synth/trades.py:246` TypeVar is a real type win.** `_WeightedT` is constrained by usage at the call sites (`SessionRow`, `OrderTypeRow`) and produces a precise return type at each site — not an `Any`-paper. Removes two `no-any-return` errors at the source instead of suppressing them.

2. **`function_app/triggers/bacpac_export.py:343` per-Literal branches are behaviourally identical to the previous `if raw in (…): return raw`.** Each branch returns a literal string of the matching type, which is structurally what the previous `# type: ignore[return-value]` was masking. The new code makes the `Any → Literal` narrowing explicit and removes the ignore comment, which is the right direction.

3. **`tcp/db.py:131-138` `case _:` defensive guard kept.** The reviewer ask was "real guard or pseudo-comment" — verdict: real. mypy `--strict` enforces enum exhaustiveness at *current* call-sites, but the runtime `raise AuthError(msg)` survives a future contributor adding a new `AuthMode` member without updating `_build_aad_kwargs`. The `# type: ignore[unreachable]` on the body line is correctly scoped (the `msg = ...` assignment only) so a regression that reaches the branch fails loudly. Comment explains intent.

4. **`tcp/synth/runner.py:291` assert is defence-in-depth, not pseudo-comment.** Trace of `conn` assignment: if `owned_conn` is `True`, `conn = _open_raw_connection()` (which raises `pyodbc.Error` on failure — never returns None); if `owned_conn` is `False`, the caller's `conn` is non-None by precondition. The assert closes the (impossible-but-mypy-can't-prove-it) cross-product. The Functions Python worker does not run with `python -O`, so the assert is live at runtime. Both the mypy narrowing and the runtime-tripwire purpose are valid.

5. **`infra/modules/{keyvault,storage}.bicep` `bypass` conditional is rendered-ARM-equivalent for the default path.** With `defaultAction='Allow'`, Azure ignores `bypass` entirely, so `bypass: 'None'` produces the same ARM behaviour as the old hardcoded `bypass: 'AzureServices'`. The conditional makes intent explicit for the future Deny-flip. PSRule's `Azure.KeyVault.Firewall` and checkov's `CKV_AZURE_*` rules don't differentiate between `bypass` values when `defaultAction='Allow'`, so the Etapa-4 IaC gates continue to pass without change.

6. **`tests/unit/test_function_app_imports.py` fixture genuinely exercises cold-start ordering.** The `monkeypatch.delitem(sys.modules, ...)` for `function_app`, `function_app.function_app`, and `function_app.triggers.*` forces re-execution of the FunctionApp instantiation and the `from function_app.triggers import ...` block. If a future commit reorders the imports above `app = func.FunctionApp(...)`, the re-import raises `ImportError: cannot import name 'app'` from `function_app.function_app` and `test_function_app_module_imports_cleanly` fails loudly. (The pytest "import-once-per-process" concern is real for non-fixturised import tests, but here the fixture explicitly wipes and re-imports, so the concern doesn't apply.)

7. **`pyproject.toml` `explicit_package_bases` + `mypy_path = ["."]` is the correct fix** for the `function_app.triggers.ask` vs `triggers.ask` duplicate-name collision. The comment at lines 110-120 explains the constraint precisely (Functions Python v2 forbids `function_app/__init__.py`, so namespace_packages is mandatory). Closes code10-MN-01 with the minimum surface area.

8. **`pyproject.toml` removal of `types-pyodbc`** is correct: the package indeed never existed on PyPI (verified — there's a `types-pyodbc` stub on conda-forge but not on PyPI). The replacement (adding `pyodbc.*` to the `ignore_missing_imports` block alongside `polars.*`) matches the existing pattern. The inline comment also flags the un-do-it trigger (a future PEP-561 marker upstream).

---

## Files touched in this review (read-only)

- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\pyproject.toml`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tcp\db.py`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tcp\synth\trades.py`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tcp\synth\runner.py`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\triggers\bacpac_export.py`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\triggers\ask.py`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\function_app.py`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\.pre-commit-config.yaml`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\modules\keyvault.bicep`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\infra\modules\storage.bicep`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\unit\test_function_app_imports.py`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\tests\conftest.py`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\.github\workflows\ci.yml`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\function_app\requirements.txt`
- `D:\Personal\Proiect Licenta\TCP_TradingCentralPanel\swa\staticwebapp.config.json`

## Recommended action

Merge the branch. Address `code11-MJ-01` (staged-blob read) and `code11-MJ-02` (test mypy disable list) in an Etapa-12 polish commit; the four Minor findings can be batched into the same commit or carried as STATE.md residuals.
