# Etapa 3 Python review — pass 1

**Reviewer**: python-pro
**Date**: 2026-05-15
**Verdict**: ACCEPT_WITH_CHANGES

## Summary

The Etapa-3 synthetic-data package is well structured, deterministic, English-only, and conforms to the V002 OPENJSON contract on all 17 fields. Pydantic models are frozen and strictly typed; Decimal discipline is correctly applied with quantisation grids matching every persisted column. The runner and `seed_employees` both support an injected `conn` for tests and manage their own transactions when they own the connection. The remaining defects are bounded: one status-mapping bug in the runner that swallows `skipped_non_trading_day`, one same-day-exit drift that can push `time_exit` past midnight, and a handful of mypy/PEP-585 and docstring polish items. None of the major findings block correctness of the OPENJSON contract, so they are upgrade work rather than rewrites.

## Critical (blocks merging Etapa 3)

None. All critical-path invariants (JSON shape, Decimal precision, idempotency, single-transaction commit/rollback, parameterised SQL, deterministic seeds) hold.

## Major

- [ ] **MA-01** | `tcp/synth/runner.py:286-291` | `status` mapping is binary (`already_generated` vs `ok`); the SQL proc can also return `skipped_non_trading_day` (V002 line 76), and that value is silently rewritten to `ok` because the `else` arm defaults to `'ok'`. Why: this masks a legitimate skip signal — if `dim_Date` falsely classifies a date as a business day but `fn_IsTradingDay` rejects it (e.g., a holiday added to one table but not the other), the runner reports a successful 0-row insert. Fix: replace the binary check with an explicit allow-list mapping: `text in {'ok', 'already_generated', 'skipped_non_trading_day'}`; treat unknown strings as `'ok'` only if `rows_inserted > 0`, else surface the raw value or raise.
- [ ] **MA-02** | `tcp/synth/trades.py:334-338, 467-468` | `_draw_holding` clamps to `[5, 480]` minutes (8 h); an entry at 21:59 with a 480-minute draw produces `time_exit` at 05:59 the **next** calendar day. Why: the V002 invariant only constrains `time_entry`'s Bucharest date, so the SQL side accepts this. However, the audit checklist (item §7 "`time_exit` is strictly after `time_entry` and on the same date for the daily generator") and the implicit semantics of "daily" generation are violated. Also: any future report or view that joins `time_exit::date = trade_date_ro` will lose ~0.05 % of closed trades. Fix: either clamp `time_exit` to `time_entry`'s session end (or 23:59:00 local) **or** clamp `_draw_holding` to `min(_HOLDING_MAX_MINUTES, max(_HOLDING_MIN_MINUTES, end_of_day - time_entry))`. Recommend the latter — adjust per-trade in `generate_for_date` after `_draw_entry_time` is known.
- [ ] **MA-03** | `tcp/synth/runner.py:284-291` | When the proc returns `status='already_generated'`, the runner copies the proc's `rows_inserted` (which equals the *existing* row count from V002 lines 84-87) into the response dict. Why: the field name `rows_inserted` is misleading in that case — callers expect "rows inserted in this call". A Function-App caller that uses this to drive Application Insights metrics will double-count on a replay. Fix: when `status == 'already_generated'`, set the response `rows_inserted = 0` and add a separate `existing_rows` key (or drop the count entirely); document the semantics in the function docstring.
- [ ] **MA-04** | `tests/unit/test_fx_rates.py:10`, `tests/unit/test_seed_employees.py:10` | Tests import the private symbols `_compute_wobble`, `_ascii_slug`, `_build_org`. Why: the checklist explicitly says "tests don't reach into private modules", and a future refactor of those helpers (e.g., renaming `_ascii_slug` to a public utility) will silently break unrelated tests. Fix: either (a) promote the helpers to public names (`compute_wobble`, `ascii_slug`, `build_org`) if they are part of the module's contract, or (b) move the unit-tests of those helpers next to their definitions as doctests, or (c) leave the private import but mark with a `# noqa` and document the deliberate coupling.

## Minor / nits

- [ ] **MI-01** | `tcp/synth/trades.py:21`, `tcp/synth/runner.py:26` | `from typing import ... Sequence` is the deprecated PEP 484 alias since Python 3.9; PEP 585 prefers `from collections.abc import Sequence`. Why: `ruff` rule `UP035` will flag this and `mypy --strict` is not affected, but the project's stated convention is "Python 3.12, modern". Fix: import `Sequence` from `collections.abc`.
- [ ] **MI-02** | `tcp/synth/trades.py:178` | `to_json_dict` returns `dict[str, Any]`. Why: the JSON schema is fixed and contract-bearing; an inline `TypedDict` named `TradeJsonRow` would make the contract checkable by mypy and self-documenting. Fix: declare a `TypedDict` and use it as the return annotation; this also documents the side's `Literal["B","S"]` and `is_open: Literal[0,1]` to readers without consulting V002.
- [ ] **MI-03** | `tcp/synth/trades.py:237-251` | `_weighted_choice` returns `Any`. Why: a generic `TypeVar("T")` would give callers proper inference (e.g., `_pick_order_type` would infer `OrderTypeRow` instead of `Any`). Fix: `def _weighted_choice(rng: Random, choices: Sequence[tuple[T, float]]) -> T:` with `T = TypeVar("T")`.
- [ ] **MI-04** | `tcp/synth/fx_rates.py:64-67` | Comment block says "the centre point (49.5) corresponds to 0 % wobble" but the actual mapping `-_WOBBLE_BOUND + step * 49` with `step = 0.01/99 ≈ 0.000101` lands at `-5.05e-5`, not zero. Why: comment vs implementation drift. Fix: either correct the comment ("bucket 49 is closest to 0 %, ~-5e-5") or use a centred mapping `(_WOBBLE_BOUND * (2*bucket - (_WOBBLE_STEPS-1))) / Decimal(_WOBBLE_STEPS-1)`.
- [ ] **MI-05** | `tcp/synth/runner.py:44-51` | `MIN(a.account_id)` picks an arbitrary account when a trader has more than one. The current Etapa-3 spec only creates one live-EUR account per trader so this is benign, but the comment is missing. Why: a future Etapa that creates a `demo` account alongside `live` would silently change which `account_id` lands in `fact_Trades`. Fix: add a comment, or filter `WHERE a.account_type = 'live' AND a.currency = 'EUR'` and assert exactly one row per trader.
- [ ] **MI-06** | `tcp/synth/seed_employees.py:283-284` | `conn = conn if conn is not None else _open_raw_connection()` re-binds the parameter; mypy in strict mode is fine with this, but the local re-bind makes the `owned_conn` flag's role less obvious. Why: readability. Fix: name the local `effective_conn` so the parameter remains the documented input. (Same pattern in `runner.py:227`.)
- [ ] **MI-07** | `tcp/synth/trades.py:330-331` | `naive.replace(tzinfo=_TZ_BUCHAREST)` is correct for non-DST boundaries but silently produces an ambiguous wall-clock time on the autumn fall-back (`02:00-02:59 EET twice`). Why: the synth uses 07:00-22:00 session windows so this is unreachable today, but a future after-hours session that crossed the boundary would break. Fix: add a one-line assertion `assert hour >= 5` near the call or document the assumption in the docstring.
- [ ] **MI-08** | `tcp/synth/trades.py:199-209` | Decimals are emitted as `float`. The 6-dp `price_entry` round-trips through float losslessly for prices < 2^24/1e6 ≈ 16.7M (true for every symbol), and 4-dp PnL through float losslessly to ~2^48/1e4 ≈ 28 billion EUR. Why: this is fine for the current rate cards but worth documenting in the docstring — a reader cannot tell from the code alone that `float()` is precision-safe here. Fix: add one line to the `to_json_dict` docstring stating the precision-safety argument and the source columns' max magnitudes.
- [ ] **MI-09** | `tcp/synth/trades.py:219-234` | `_poisson_clamped` uses Knuth's algorithm which is O(λ) per call; for λ=8 this is ~9 RNG draws per trader per day, ~270 draws/day total. Fine. Why: not a problem, but the docstring should note that the choice was deliberate (NumPy dependency avoidance). Fix: append "Knuth chosen over `np.random.poisson` to keep the dependency surface tight; λ=8 makes O(λ) cost negligible." to the docstring.
- [ ] **MI-10** | `tests/unit/test_synth_trades.py:102-119` | `test_open_rate_within_tolerance_over_large_sample` asserts `0.03 ≤ rate ≤ 0.07` over 60 dates. With ~12 800 trades, the binomial 95 % CI around 5 % is roughly ±0.4 %, so the ±2 % tolerance is loose enough to never flake — good. Why: just confirming intent. Fix: none required; consider tightening to ±1 % to actually catch a future regression.
- [ ] **MI-11** | `tests/unit/test_synth_runner.py:262-271` | `test_invalid_dialect_does_not_break_cursor_close` — the test name "invalid_dialect" is misleading; the test exercises a cursor-close failure path, not anything dialect-related. Why: future contributor confusion. Fix: rename to `test_cursor_close_failure_does_not_leak_connection`.
- [ ] **MI-12** | `tcp/synth/seed_employees.py:97-102` | `_spread_hire_date` uses `int(round(...))` which can produce two adjacent indices mapping to the same date — i.e. the spread is not strictly monotonic. Why: harmless for `dim_Employees.hire_date` (no uniqueness constraint), but the docstring promises a "deterministic spread" without noting that adjacent collisions occur. Fix: trivial docstring tweak.
- [ ] **MI-13** | `tcp/synth/runner.py:289-291` | `if status_raw is not None: ... else: status = "ok"` — the implicit `else` is achieved by initialising `status = "ok"` before the block. Combined with **MA-01**, this is the root of the swallowed-status bug. Fix: covered by MA-01.
- [ ] **MI-14** | `tcp/synth/seed_employees.py:42-45` | `_TEAMS_BY_FLOOR` hard-codes team IDs `(1,2,3)` and `(4,5,6)`. Why: V001 must seed teams in exactly that order or `dim_Employees.team_id` will reference the wrong floor's team. The current V001 does so, but the dependency is silent. Fix: replace the hard-coded constants with a `SELECT team_id, floor_id FROM dim_Teams ORDER BY team_id` and bind dynamically, or assert at runtime that each `team_id` belongs to the expected `floor_id` via a `SELECT 1 FROM dim_Teams WHERE team_id = ? AND floor_id = ?` pre-check.
- [ ] **MI-15** | `tcp/synth/runner.py:140-152` | `_coerce_time` parses strings with a hand-rolled split; the stdlib `time.fromisoformat` does this correctly. Why: dependency on string format. Fix: try `time.fromisoformat(text)` first, fall back to the manual split as a last resort.
- [ ] **MI-16** | `tcp/synth/trades.py:415-416` | `if not sessions or not order_types: return []` — an empty `traders` list also returns `[]` but only because the `for trader in traders` loop body never runs; this is correct but not obviously so. Why: documentation. Fix: add `if not traders: return []` for symmetry and clarity.
- [ ] **MI-17** | `tcp/synth/seed_employees.py:264` | `Faker.seed(_FAKER_SEED)` is a classmethod that sets a global seed; calling `seed_employees` twice in the same process is deterministic but parallel pytest-xdist runs across modules can interfere. Why: pytest-xdist isn't currently used. Fix: none required; note in the module docstring that Faker's seeding is process-global.
- [ ] **MI-18** | `tcp/synth/__init__.py:1-7` | `__init__.py` does not re-export `MarketRow` from `tcp.synth.trades` as part of the public API but the module-level docstring claims it's the package interface. Why: it does — line 14 imports `MarketRow`. Verified. Fix: none required; reading note for the reviewer.

## Type / contract conformance matrix

| Spec item | File:section | Verdict | Notes |
|---|---|---|---|
| `trade_uid str(14)` `T<YYYYMMDD>-<NNNN>` | `trades.py:446,480` | PASS | `f"T{date_token}-{seq:04d}"` = 14 chars exactly |
| `trader_id int` | `trades.py:114,447,481` | PASS | Pydantic `Field(gt=0)` |
| `account_id int` | `trades.py:115,448,482` | PASS | |
| `market_id int` | `trades.py:123` | PASS | |
| `session_id int` | `trades.py:135` | PASS | |
| `order_type_id int` | `trades.py:146` | PASS | |
| `side Literal["B","S"]` | `trades.py:166` | PASS | uniform 50/50 selection, not biased |
| `quantity float (4dp)` | `trades.py:167,199`, `commissions._COMMISSION_QUANTISE` | PASS | Quantised to `Decimal("1")` for equity/fx/commodity, `Decimal("0.0001")` for crypto. Note: DB column is `DECIMAL(18,4)`; integer quantities are losslessly representable |
| `price_entry float (6dp)` | `trades.py:168,200`, `_PRICE_QUANTISE` | PASS | `Decimal("0.000001")` quantum |
| `price_exit float\|None (6dp)` | `trades.py:169,201,361` | PASS | NULL for open trades |
| `time_entry ISO 8601 with offset` | `trades.py:170,202,322-331` | PASS | tz-aware Europe/Bucharest, `isoformat()` |
| `time_exit ISO\|None` | `trades.py:171,203` | PASS — with MA-02 caveat | Same-day not enforced |
| `gross_pnl_eur float\|None (4dp)` | `trades.py:172,204,366` | PASS | `_PNL_QUANTISE = Decimal("0.0001")` |
| `commission_eur float (4dp)` | `trades.py:173,205,367,441`, `commissions.py:25,76` | PASS | Always non-NULL, even for open trades |
| `net_pnl_eur float\|None (4dp)` | `trades.py:174,206,368` | PASS | |
| `is_open int (0/1)` | `trades.py:175,207` | PASS | `1 if self.is_open else 0` — BIT accepts both ints and bools; explicit int is the safer choice |
| `fx_rate_to_eur float\|None (8dp)` | `trades.py:176,208-210`, `fx_rates._QUANTISE` | PASS | `Decimal("0.00000001")` quantum; NULL for open trades, set for closed |
| Win-rate +0.3% B / -0.3% S via price-exit shift | `trades.py:358-359` | PASS | Drift applied in `_close_trade` only; side selection is 50/50 in line 430 |
| Open-trade probability 5 % | `trades.py:49,435` | PASS | `_OPEN_TRADE_PROBABILITY = 0.05` |
| Commission for open trades | `trades.py:437-443` | PASS | Entry commission paid; `fx_rate_to_eur` field left NULL |
| `trade_uid` resets per day, starts at `0001` | `trades.py:418-420` | PASS | `seq = 0; seq += 1` before use; first UID is `-0001` |
| Per-trader count `Poisson(8)` clamped `[3,15]` | `trades.py:41-43, 219-234, 422-424` | PASS | Knuth's algo; no stdlib built-in for Poisson |
| Time-of-day inside session windows | `trades.py:317-331` | PASS | `randint(start_minutes, end_minutes-1)` |
| `time_exit > time_entry` | `trades.py:467-468` | PASS — with MA-02 caveat | Holding is always ≥5 minutes |
| Inactive markets filtered | `trades.py:412-414` | PASS | List comprehension before iteration |
| 32 employees: 24+6+2 | `seed_employees.py:117-173` | PASS | 2 floor mgrs + 6 leads + 6×4=24 traders = 32 |
| Diacritic-stripped emails, diacritics in names | `seed_employees.py:69-78,82-94` | PASS | NFKD + ASCII ignore for slug; raw Faker output for `first_name`/`last_name` |
| Manager hierarchy | `seed_employees.py:329-352` | PASS | floor_manager → NULL (skipped); team_lead → floor_manager; trader → team_lead |
| Idempotent via MERGE | `seed_employees.py:205-215, 225-234` | PASS | `MERGE ... NOT MATCHED THEN INSERT` only |
| V001 hierarchy pre-check | `seed_employees.py:288-303` | PASS | Verifies dim_Companies, dim_TradingFloors, dim_Teams; raises clear errors |
| Injected `conn` for tests | `seed_employees.py:240,283-284`, `runner.py:199,226-227` | PASS | `owned_conn` flag governs commit/close |
| Single transaction; commit/rollback | `seed_employees.py:367-375`, `runner.py:271-282` | PASS | Autocommit OFF (set by `_open_raw_connection`) |
| Returns canonical dict shape | `runner.py:235-240,264-269,301-306` | PASS | `{trade_date, rows_inserted, duration_ms, status}` |
| Parameterised SQL (no string interp) | All `cursor.execute` calls | PASS | Every call uses `?` placeholders |
| No `eval`/`exec`/`pickle` | Whole package | PASS | grep-clean |
| No secrets in source | Whole package | PASS | Only env-var reads in `tcp.db` |
| English-only artifacts | Whole package | PASS | Only Faker `ro_RO` runtime data is Romanian |

## Coverage estimate

| Module | Coverage estimate | Gaps |
|---|---|---|
| `tcp/synth/_rng.py` | ~100 % | None — every line and every branch is exercised. |
| `tcp/synth/fx_rates.py` | ~95 % | `get_fx_rate` for every supported ccy, EUR short-circuit, KeyError branch, wobble bounds all covered. Missing: case-mixed inputs other than `'eur'`/`'EUR'` (e.g. `'Usd'`) — trivial. |
| `tcp/synth/commissions.py` | ~95 % | All four asset classes + the ValueError branch + quantisation parametrise across classes. Missing: zero-quantity edge case (returns 0; not asserted) and negative-quantity defence (the function does not guard against it — see also no explicit precondition assertion). |
| `tcp/synth/trades.py` | ~88 % | `_draw_quantity` ValueError branch (line 303-304) not tested; `_pick_session`'s fallback `sessions[0]` when no candidates exist (line 271) not tested; `_weighted_choice`'s floating-point fallback (line 251) not tested; `_FALLBACK_BASE_PRICE` (line 101) only reachable via an unknown symbol — not exercised. |
| `tcp/synth/seed_employees.py` | ~90 % | Happy path, missing-V001 rollback, manager wiring, account creation, idempotency, dry-run, and ASCII normalisation all covered. Gaps: `_spread_hire_date` boundary (`total == 1`) not tested; `pyodbc.Error` raised inside `cursor.close` (suppressed branch in `finally`) not tested. |
| `tcp/synth/runner.py` | ~92 % | Happy path, dry-run, skipped_holiday, rollback on proc failure, already-generated, cursor-close-failure all covered. Gaps: `_coerce_time` with `datetime` input (line 144-145) not exercised; `_coerce_time` with bare-string input (line 148-152) not exercised; **MA-01** — `skipped_non_trading_day` from the proc is silently rewritten to `'ok'` so no test catches it (which itself is evidence of the bug). |

Overall package coverage estimate: **~92 %**. Above the 90 % bar.

## Recommendation

ACCEPT_WITH_CHANGES. Address **MA-01** (status mapping) and **MA-02** (same-day `time_exit` clamp) before tagging Etapa-3. **MA-03** and **MA-04** are correctness-adjacent and should be fixed in the same convergence pass to avoid follow-up review noise. The minor items can be batched into a "Etapa-3 polish" commit or rolled forward into Etapa-4 work. Re-run `mypy --strict`, `ruff check`, and the full unit-test suite after the major fixes land; expect the existing tests to remain green except where they exercise the changed `time_exit` semantics — add a regression test that an entry near session close has its `time_exit` clamped to the same calendar day in Europe/Bucharest.
