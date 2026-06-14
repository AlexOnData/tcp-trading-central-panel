# Etapa 3 convergence review — pass 2

**Reviewer**: code-reviewer (verification pass)
**Date**: 2026-05-15
**Verdict**: ACCEPT_WITH_MINOR_CHANGES

## Summary

The fix agent has resolved all four critical findings from the data-engineer review
(CR-01..CR-04), both highlighted statistical criticals (stats CR-01 win-rate
calibration and stats CR-04 attribute references), and the three high-value
python-pro majors (MA-02 time_exit clamp, MA-03 rows_inserted semantics, MA-04
public aliases). The new `tcp.db.set_admin_session_context` helper is well
documented, strictly typed, and consistent with the ADR-003 contract; V002 SQL
artifacts are untouched. The two stats criticals that demanded a model-level
rewrite (CR-02 Sharpe inflation and CR-03 PF/WR/Sharpe joint feasibility) were
not addressed in code, but the team made a documented, defensible choice to
deliberately undershoot the spec win-rate target — the comment block in
`trades.py:51-56` calls this out explicitly. This is a thesis-defensibility
trade-off rather than a correctness defect, so it does not block merging
Etapa 3 from a code-review standpoint; it should be tracked in
`docs/decisions/ADR-XXX` for transparency.

## Pass-1 ID status table

| ID (pass 1) | Source review | Severity | Status (pass 2) | Notes / evidence |
|---|---|---|---|---|
| CR-01 | dataeng | critical | RESOLVED | `tcp/db.py:287-317` defines `set_admin_session_context`; `runner.py:39, 278, 286-287` and `seed_employees.py:34, 314-335` resolve `TCP_GENERATOR_OID` and call it before any cursor work. `__init__.py:9, 35` re-exports it. Missing env var raises `RuntimeError` with the env-var name in the message. |
| CR-02 | dataeng | critical | RESOLVED | `runner.py:49-51` declares `_VALID_PROC_STATUSES = {"ok","already_generated","skipped_non_trading_day"}`; line 354 maps via `text if text in _VALID_PROC_STATUSES else "unknown"`. Test `test_run_daily_propagates_skipped_non_trading_day_status` at `test_synth_runner.py:253-266` exercises the new path. |
| CR-03 | dataeng | critical | RESOLVED | `seed_employees.py:296` selects `e.role in ("trader","team_lead")` for accounts; `expected_counts["accounts"] = len(trading_eligible) = 30`. Integration test `test_seed_employees_then_run_daily_twice` line 144 asserts `accounts == 30`; line 186 asserts `pnl_row_count == 30`. Unit test `test_seed_employees_runs_merge_for_every_trading_eligible_account` line 197 confirms 30 account MERGEs. |
| CR-04 | dataeng | critical | RESOLVED | `tests/integration/test_generator_idempotency.py:357` uses `"trader_id": trader_id` (not `employee_id`). The expected cross-date error message substring is still asserted on line 386. |
| MJ-01 | dataeng | major | NOT_RESOLVED (deferred) | `to_json_dict` still emits decimals via `float()`. The pass-1 reviewer themselves noted this is safe for the current quantisation grids; the fix is upgrade work, not correctness-blocking. |
| MJ-02 | dataeng | major | NOT_RESOLVED (deferred) | `_resolve_target_date` returning `None` is still mapped to `status="skipped_holiday"`; the semantic-conflation hazard remains. Low risk while `dim_Date` is provisioned correctly. |
| MJ-03 | dataeng | major | NOT_RESOLVED (deferred) | The Python `previous_business_day` helper remains exported but unused by `run_daily`. Test coverage uses it directly; not a correctness issue, only a duplication concern. |
| MJ-05 | dataeng | major | NOT_RESOLVED (deferred) | `V002__synth_logic.sql:280` checksum placeholder unchanged — V002 was correctly left untouched during this Python-only fix pass; track separately in CI work. |
| MN-07 | dataeng | minor | RESOLVED | `tests/integration/test_generator_idempotency.py:107-111` sets `sp_set_session_context` to `_TEST_ADMIN_OID` immediately after inserting the synthetic `dim_UserRoles` row, exactly as called for in MN-07. |
| CR-01 | stats | critical | RESOLVED | `tcp/synth/trades.py:51-58` sets `_BUY_DRIFT = 0.0006`, `_SELL_DRIFT = -0.0006`; comment explicitly references `Φ(0.05) ≈ 0.520`. Test `test_win_rate_close_to_52_percent` at `test_synth_distributions.py:91-107` asserts `0.49 <= win_rate <= 0.55`. |
| CR-02 | stats | critical | NOT_RESOLVED (knowingly deferred) | Annualised Sharpe inflation is unchanged (σ, qty ranges identical to pass-1). The trades.py comment (lines 53-56) acknowledges the Sharpe-inflation tradeoff and frames the choice as deliberate. Should be captured in an ADR for thesis defensibility. |
| CR-03 | stats | critical | NOT_RESOLVED (knowingly deferred) | Generator still uses a symmetric Gaussian shift (no Bernoulli + half-normal model). At p_w ≈ 0.52, profit factor is ≈ 1.08, below the 1.5 spec target. The 55%/PF1.5 spec target conflict noted in stats CR-03 remains; resolve via ADR or spec revision. |
| CR-04 | stats | critical | RESOLVED | `test_synth_distributions.py` no longer references `r.holding_minutes` (now derived from `time_exit - time_entry` at line 148), `r.quote_currency` (now derived via join against `synthetic_dims.markets` at line 227-229), or `r.employee_id` (now `r.trader_id` at line 325). Inline comments tag the rewrites against statistical CR-02 review. |
| MA-01 | stats | major | RESOLVED | trades.py:51-56 comment now references KPI-TR-060 (not the bogus KPI-TR-031) and documents the deliberate undershoot. |
| MA-02 | python-pro | major | RESOLVED | `tcp/synth/trades.py:472-483` clamps `time_exit` to `datetime.combine(trade_date, time(23,59,59), tzinfo=_TZ_BUCHAREST)` whenever the holding draw would push past midnight. Test `test_holding_time_distribution` lines 157-161 explicitly references the clamp and asserts `max_ht <= 480.5`. |
| MA-03 | python-pro | major | RESOLVED | `runner.py:356-370` splits the response: when `status == "already_generated"`, `rows_inserted = 0` and `existing_row_count = raw_row_count`; otherwise `rows_inserted = raw_row_count`. Test `test_run_daily_reports_already_generated_status` (test_synth_runner.py:236-250) verifies the new shape including `existing_row_count == 192`. |
| MA-04 | python-pro | major | RESOLVED | `seed_employees.py:204-205` exposes `ascii_slug = _ascii_slug` and `build_org = _build_org`; `fx_rates.py:73` exposes `compute_wobble = _compute_wobble`. `tests/unit/test_seed_employees.py:10` imports `ascii_slug, build_org` (public); `tests/unit/test_fx_rates.py:10` imports `compute_wobble`. No `_`-prefixed imports remain in unit tests for these helpers. |
| MI-01..MI-18 | python-pro | minor | MIXED | Most minors not addressed (e.g., MI-01 `Sequence` still imported from `typing` in `trades.py:21`); none are correctness-blocking. Acceptable for an Etapa-3 polish follow-up. |

## Regressions

None observed. Specifically:

- The new `set_admin_session_context` helper at `tcp/db.py:287-317` is fully typed
  (`conn: pyodbc.Connection`, `mi_object_id: UUID -> None`); no `Any` leakage; it
  reuses the existing `_SQL_SET_CONTEXT` constant so the `@read_only=1` invariant
  from ADR-003 is preserved.
- Cursor cleanup mirrors the pattern in `connection_for_user` (suppress
  `pyodbc.Error` during cleanup to avoid masking the primary exception). The
  helper does NOT commit, matching the documented contract that SESSION_CONTEXT
  is connection-scoped, not transaction-scoped.
- The owned-vs-borrowed connection branching in `runner.py:274-287` and
  `seed_employees.py:313-335` is symmetric: both paths require
  `TCP_GENERATOR_OID` only on the owned-conn branch and assume the caller has
  pre-set SESSION_CONTEXT on the injected-conn branch. Unit tests no-op the
  helper via `monkeypatch.setattr` (`test_synth_runner.py:25`,
  `test_seed_employees.py:22`) — clean isolation.
- V002 SQL is verifiably untouched (still in the untracked-file set in git
  status; no edits applied during this Python fix pass).
- The new `existing_row_count` key is additive — callers that read
  `response["rows_inserted"]` continue to work; the App-Insights double-counting
  concern from MA-03 is correctly resolved without breaking the prior shape's
  consumers.
- The `_VALID_PROC_STATUSES` frozenset is module-level so it pays its allocation
  cost once; the membership test is O(1).
- `tcp/synth/__init__.py:23-36` correctly re-exports `set_admin_session_context`
  in `__all__`; the public API of the package is consistent.

## Remaining gaps

1. **Stats CR-02 / CR-03 (Sharpe inflation + KPI joint infeasibility) — knowingly
   deferred.** The generator's symmetric `N(±0.0006, 0.012)` shift gives a win
   rate ≈ 0.52, profit factor ≈ 1.08, and annualised Sharpe still well above
   1.0. The undershoot is a *documented* trade-off (`trades.py:53-56`), but the
   reasoning belongs in `docs/decisions/ADR-XXX.md` rather than only in a source
   comment so the thesis can cite it. Recommend opening an ADR before tagging
   `v1.0-mvp`.
2. **Data-eng MJ-01..MJ-05** — Float-encoded decimals, dim_Date out-of-range
   conflation, unused `previous_business_day` helper, and the V002 checksum
   placeholder are unresolved but were correctly scoped out of this convergence
   pass. None are correctness-blocking under current dim_Date / V001 + V002
   contracts.
3. **MI-01 PEP-585** — `tcp/synth/trades.py:21` still uses
   `from typing import ... Sequence`; replace with `from collections.abc import
   Sequence` to clear `ruff UP035`. Cosmetic.
4. **Coverage of new failure path** — `seed_employees` does not have a unit
   test that exercises the "TCP_GENERATOR_OID unset" RuntimeError branch (only
   `runner.py` has the analogous test at `test_synth_runner.py:268-274`).
   Symmetry would help; not blocking.
5. **MN-09 (data-eng)** — `runner.py:313` still passes `default=str` to
   `json.dumps`. Recommend dropping it so a future field-type regression fails
   loud.

## Recommendation

ACCEPT_WITH_MINOR_CHANGES. The four data-engineer criticals that previously
blocked the daily generator (CR-01 SESSION_CONTEXT, CR-02 status mapping,
CR-03 account count, CR-04 payload field name), the python-pro time_exit
clamp (MA-02), the rows_inserted semantics (MA-03), the public-alias
hygiene (MA-04), and the win-rate calibration + test-attribute fixes from
the statistical review (CR-01 + CR-04) are all correctly applied and
covered by new or amended tests. The new `set_admin_session_context` helper
is a well-shaped, fully-typed addition to the ADR-003 contract. The only
non-trivial deferred items are the two statistical criticals (CR-02 Sharpe
inflation and CR-03 KPI joint infeasibility) and they have been knowingly
deferred with an explanatory comment in `trades.py:51-56`; this should be
captured in an ADR to make the trade-off auditable. Once the ADR is filed
and the small polish items (MI-01, MN-09, the symmetric env-var unit test)
land, the verdict converts to a clean ACCEPT. The Etapa-3 deliverables are
mergeable as they stand; the ADR and the polish can ride in a "Etapa-3
follow-up" branch.
