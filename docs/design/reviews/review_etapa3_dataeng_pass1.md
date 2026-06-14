# Etapa 3 data-pipeline review — pass 1

**Reviewer**: data-engineer
**Date**: 2026-05-15
**Verdict**: REJECT

## Summary

The core SQL contract in `V002__synth_logic.sql` is well-engineered: the
OPENJSON staging, the idempotency probe, the cross-row Europe/Bucharest
invariant, and the MERGE into `fact_DailyTraderPnL` are all correctly
shaped and atomic. The Python generator (`tcp/synth/trades.py`) is
deterministic, the JSON keys align with the SQL OPENJSON `WITH` clause,
and `to_json_dict()` correctly emits `is_open` as `0/1` for BIT and
ISO-8601 strings with offsets for `DATETIMEOFFSET(3)`. However, the
pipeline as wired today has four blocking defects: (1) the runner's
`_open_raw_connection` path never sets `SESSION_CONTEXT('aad_object_id')`,
so the V001 BLOCK PREDICATE rejects every INSERT from the production
generator MI (the dim_UserRoles row exists, but the predicate cannot find
it); (2) the runner collapses `'skipped_non_trading_day'` to `'ok'`,
making the contract documented in `run_daily`'s docstring impossible to
return; (3) the active-traders SELECT requires every team-lead to have a
live EUR account, but `seed_employees` only opens accounts for the 24
traders — the integration test's "30 PnL rows" assertion is therefore
unreachable; (4) `test_run_daily_cross_date_rejected` builds the payload
with key `employee_id` instead of `trader_id`, so the proc raises a
NOT NULL violation on `trader_id` before the cross-date check runs and
the substring assertion fails.

## Critical (blocks merging Etapa 3)

- [ ] **CR-01** | `tcp/synth/runner.py:227` (and `tcp/db.py:181-206`) |
  The runner opens its production connection via `_open_raw_connection()`,
  which by design does **not** set `SESSION_CONTEXT('aad_object_id')`. The
  V001 RLS predicate `rls.fn_TradesPredicate` (V001:1177-1206) filters
  `dim_UserRoles` on `aad_object_id = CAST(SESSION_CONTEXT(N'aad_object_id') AS UNIQUEIDENTIFIER)`,
  so an unset context returns zero rows → no scope is granted → the
  BLOCK PREDICATE on `fact_Trades AFTER INSERT` (V001:1219) returns 0
  → the daily INSERT is rejected with error 33504 even though the
  generator MI is registered with `scope='admin'`. The "admin bypasses
  the FILTER" half of ADR-003 §5 only works once the predicate has
  resolved the principal; without SESSION_CONTEXT the predicate cannot
  resolve anything. **Why**: the entire daily generation pipeline fails
  with no rows inserted in production; only the integration test's
  rolled-back transaction happens to mask it because the test fixture
  uses the same SESSION_CONTEXT-less mode and the test would also fail
  if it ran end-to-end against fact_Trades, except that the rolled-back
  fixture never commits and the dim_UserRoles row never participates.
  **Fix**: in `tcp/synth/runner.py`, immediately after acquiring the
  connection (owned or borrowed), execute
  `EXEC sys.sp_set_session_context @key=N'aad_object_id', @value=?, @read_only=1`
  with the generator MI's `aad_object_id` (sourced from a Key Vault
  secret or App Setting `TCP_GENERATOR_AAD_OBJECT_ID`); clear it on the
  way out for the non-owned-conn path. Alternatively, introduce a thin
  `tcp.db.connection_for_generator()` wrapper that hides this from the
  runner.

- [ ] **CR-02** | `tcp/synth/runner.py:284-292` |
  The status normalisation `status = "already_generated" if text == "already_generated" else "ok"`
  silently collapses the SQL proc's third documented status,
  `'skipped_non_trading_day'`, into `'ok'`. The proc returns exactly that
  string at V002:74-77 when `fn_IsTradingDay(@trade_date) = 0`, and the
  runner's own docstring at lines 217-219 promises that
  `'skipped_holiday'` (also one of three statuses) is the response. The
  net effect: a caller never sees the difference between "ran fine, but
  the date came from `dim_Date` so by construction it was a business
  day" and "the proc was actually called with a non-trading day". This
  also breaks `test_run_daily_skips_holiday`
  (`tests/integration/test_generator_idempotency.py:198-203`) which
  asserts `status == "skipped_non_trading_day"` — under the current
  runner, on `today=2026-01-02 (Fri)` the previous-business-day query
  resolves to `2025-12-31 (Wed, non-holiday)` and the proc runs normally,
  so the assertion sees `'ok'` not `'skipped_non_trading_day'`. **Why**:
  the holiday-skip contract is unreachable through the runner — App
  Insights will never receive a `tcp.synth.skipped_non_trading_day`
  event and the integration test fails. **Fix**: change the mapping to
  ```python
  allowed = {"ok", "already_generated", "skipped_non_trading_day"}
  status = text if text in allowed else "ok"
  ```
  and propagate the `"skipped_non_trading_day"` literal through both
  the structlog event name and the return dict. Reconcile with the
  docstring's `'skipped_holiday'` vocabulary (pick one name and use it
  consistently — recommended: `'skipped_non_trading_day'` to match the
  SQL side).

- [ ] **CR-03** | `tcp/synth/runner.py:43-51` vs
  `tcp/synth/seed_employees.py:354-365` |
  `_SQL_SELECT_ACTIVE_TRADERS` returns every employee with
  `employee_role IN ('trader', 'team_lead')` joined to an active row
  in `dim_Accounts`. `seed_employees()` only opens one EUR account per
  employee whose role is `'trader'` (line 266:
  `traders = [e for e in employees if e.role == "trader"]`). Team leads
  (6 rows) therefore have no account_id and are silently dropped from
  the INNER JOIN, so the runner generates trades for at most 24
  employees. The integration test
  `test_seed_employees_then_run_daily_twice` asserts
  `pnl_row_count == 30` (24 traders + 6 team leads) at line 177 —
  unreachable. **Why**: either the test expectation is wrong, or
  `seed_employees` is incomplete, or the SQL filter should drop team
  leads. Whichever way the team decides, the three artefacts must
  agree. **Fix (recommended)**: open a paper or live EUR account for
  team leads too inside `seed_employees` (extend the
  `traders = [...]` projection to include team leads, or maintain a
  separate `trading_eligible = traders + team_leads`); keep the SELECT
  as-is; keep the assertion as `== 30`. The alternative — restricting
  the SELECT to `employee_role = 'trader'` — would silently shrink the
  per-day fact-table footprint and contradict §1 of the project's
  "24 traders + 6 team leads" trade-eligible roster.

- [ ] **CR-04** | `tests/integration/test_generator_idempotency.py:345-365` |
  The hand-crafted JSON payload uses the key `"employee_id"` instead of
  the contract key `"trader_id"`. The V002 `OPENJSON ... WITH` block at
  lines 152-169 binds `$.trader_id` only — `$.employee_id` is ignored,
  so the staging table-variable column `trader_id` becomes NULL and
  the INSERT into `@parsed` violates the inline `NOT NULL` declaration
  on `trader_id` (V002:101). The proc therefore raises a NOT NULL
  insertion error wrapped as 50199, and the test's assertion
  `"time_entry whose europe/bucharest date" in error_message` never
  finds the substring because the cross-date check at V002:179-184
  is never reached. **Why**: the test claims to validate the cross-date
  invariant but actually validates the wrong failure path; the real
  cross-date branch has no integration coverage. **Fix**: rename the
  payload field to `"trader_id"` to match the contract. Re-run the
  test and confirm the error message contains the cross-date substring.

## Major

- [ ] **MJ-01** | `tcp/synth/trades.py:197-211` |
  `to_json_dict()` serialises every `Decimal` field via `float(...)`.
  For `fx_rate_to_eur` declared as `DECIMAL(18,8)` on both sides, this
  is a silent precision-loss vector: `float(Decimal("0.91234567"))`
  binary-rounds to ~`0.9123456700000001`, and on the SQL side OPENJSON
  parses it back as a JSON number then `TRY_CAST` to `DECIMAL(18,8)`
  which rounds again — the round-trip is correct for fx rates with
  ≤ 7 significant digits but not for the full 8 declared. The same
  concern applies to `quantity DECIMAL(18,4)` for crypto sizes
  (`Decimal("0.0001")` is fine, but heavier-tailed crypto sizes near
  `0.99995` round). **Why**: the round-trip is good *today* because
  the generator quantises to known-safe grids (`_PRICE_QUANTISE = 1e-6`,
  `_PNL_QUANTISE = 1e-4`), but the contract is one quantum change
  away from data-quality drift. **Fix**: emit decimals as JSON strings
  (`str(self.fx_rate_to_eur)`); the OPENJSON `WITH` clause already
  declares the target precision and SQL Server happily parses
  `"0.91234567"` into `DECIMAL(18,8)` losslessly. Alternative: keep
  floats but add a `_check_round_trip` unit test that re-parses every
  field via `Decimal.from_float` and asserts identity at the declared
  precision.

- [ ] **MJ-02** | `tcp/synth/runner.py:185-192` |
  `_resolve_target_date` returns `None` when the `dim_Date` lookup
  yields no row, and the caller maps that to
  `status="skipped_holiday"`. But the only condition that produces no
  row is "`today` is before the dim_Date population window starts"
  (i.e. before 2024-01-01) — Romanian public holidays are skipped
  *over* by the SQL query, not turned into `NULL`. The same status
  string is therefore used for two semantically distinct events
  (out-of-range today vs. legitimate skip), and out-of-range will
  be logged as if it were a holiday. **Why**: the
  `tcp.synth.skipped_holiday` App Insights event becomes ambiguous;
  a misconfigured date range can hide for weeks. **Fix**: distinguish
  the two: raise a hard `RuntimeError` when `_resolve_target_date`
  returns `None` (it means `dim_Date` was not provisioned for the
  current year). Holiday-skip should be reserved for the SQL-side
  `skipped_non_trading_day` path (which is currently unreachable per
  CR-02 — fix CR-02 first).

- [ ] **MJ-03** | `tcp/synth/runner.py:89-113` |
  `previous_business_day` is exposed as a public helper but is
  **not** used by `run_daily` — the runner unconditionally calls
  `_resolve_target_date` which goes to `dim_Date`. The Python
  implementation also lacks the RO public-holiday awareness
  (acknowledged in the docstring at line 98). **Why**: maintaining two
  business-day functions invites drift. The mid-stage "1.0-db-ready"
  tagging contract intends `dim_Date` to be the source of truth;
  the Python helper looks like a fallback that nobody uses. **Fix**:
  either drop the unused helper (preferred — `dim_Date` is the single
  source of truth) or document it as test-only and move it under
  `tests/_helpers.py`.

- [ ] **MJ-04** | `tcp/synth/runner.py:271-282` |
  When `conn` is borrowed (the test fixture path), an exception
  inside `cursor.execute(_SQL_EXEC_PROC, ...)` is re-raised but the
  borrowed connection is **not** rolled back here — the fixture's
  outer `BEGIN TRANSACTION` is left in whatever state the proc's
  CATCH put it (the proc's TRY/CATCH already rolled back the inner
  tran on failure, so `@@TRANCOUNT` returns to the outer fixture's
  count of 1). However, the runner has no way to know whether the
  proc raised before `BEGIN TRANSACTION` or after, so the contract is
  fragile: a future refactor that adds a pre-transaction THROW (e.g.
  the existing ISJSON/ISJSON checks at V002:91-95) raises
  *before* the proc opens its own tran and the fixture's tran is
  still healthy. Today's behaviour happens to work because SQL
  Server's XACT_ABORT ON inside the proc forces the inner tran to
  roll back before THROW propagates. **Why**: the test fixture
  pattern from `tests/conftest.py` is documented as "non-destructive
  rolled-back transaction"; relying on XACT_ABORT semantics in the
  proc to keep the fixture happy is implicit and not asserted
  anywhere. **Fix**: add a one-line note to the proc's header
  ("the proc preserves the caller's @@TRANCOUNT delta — XACT_ABORT
  ON guarantees the inner BEGIN/COMMIT/ROLLBACK pairs are balanced
  even when the proc raises after CATCH"), and add a regression test
  that calls the proc inside an outer BEGIN, asserts the outer tran
  is still healthy after a forced THROW, then ROLLBACKs cleanly.

- [ ] **MJ-05** | `V002__synth_logic.sql:280` |
  The schema_history row is inserted with
  `checksum = N'TODO-checksum-set-by-CI'`. This is acceptable as a
  build-time placeholder *if* the CI replaces it on commit — but the
  current `.github/workflows/` (per the gitStatus snapshot) does not
  appear to have a step that rewrites this. **Why**: shipping a real
  string `'TODO-checksum-set-by-CI'` to production breaks the
  drift-detection workflow defined in V001's schema_history contract
  (`docs/design/02_database_design.md` §5.4). **Fix**: either compute
  the checksum inline via `HASHBYTES('SHA2_256', <bound migration text>)`
  at migration apply time (preferred — self-contained), or add a CI
  job that rewrites the placeholder via `sed` *before* the migration
  is applied in CI/prod. Track this in `docs/decisions/ADR-004` or
  similar so the gap is explicit.

- [ ] **MJ-06** | `V002__synth_logic.sql:82-89` (idempotency probe) |
  The probe runs `IF EXISTS (SELECT 1 FROM dbo.fact_Trades WHERE trade_date_ro = @trade_date)`
  then re-queries `COUNT(*)`. Under READ COMMITTED isolation, between
  the EXISTS and the COUNT another writer could insert/remove rows
  (single-writer pattern by design, but worth being defensive).
  **Why**: cosmetic — `rows_inserted` reflects the count *after* the
  EXISTS evaluation, so a concurrent inserter could make the
  reported count drift from the true insertion. Single-writer is the
  documented contract, so practical risk is zero. **Fix**: replace
  the two probes with one `SELECT @rows_inserted = COUNT(*) ...`,
  then `IF @rows_inserted > 0 ...`; one round-trip, one snapshot.

- [ ] **MJ-07** | `V002__synth_logic.sql:217-218` (MERGE win/loss) |
  `SUM(CASE WHEN p.net_pnl_eur > 0 THEN 1 ELSE 0 END)` correctly
  counts open trades (where `net_pnl_eur IS NULL`) as zero because
  `NULL > 0` evaluates to UNKNOWN → falls into `ELSE 0`. Good. But
  the same `SUM(CASE WHEN ... = 0 THEN ...)` pattern is used for
  zero PnL — a closed trade with exactly `net_pnl_eur = 0` (rare but
  legal — `CK_fact_Trades_open_closed` requires `NOT NULL`, not
  non-zero) is counted as neither a win nor a loss, so
  `win_count + loss_count != trade_count - open_count`. ADR-002 is
  silent on this edge case. **Why**: KPIs that compute
  `win_rate = win_count / (win_count + loss_count)` are well-defined,
  but `win_rate = win_count / trade_count` (a common alternative) is
  understated. **Fix**: document the convention in ADR-002 §"Schema
  sketch" footnote: "closed trades with net_pnl_eur = 0 are treated
  as neither wins nor losses". No code change needed if the
  convention is `win_count / (win_count + loss_count)`.

- [ ] **MJ-08** | `tcp/synth/trades.py:430` (50/50 side bias) |
  `side = "B" if rng.random() < 0.5 else "S"` is exactly 50% in
  expectation. The review checklist asks for "50/50 ±2 pp" which is
  inside acceptable noise for one day's draws. **Why**: not a
  problem per se, but there is no unit test that verifies the
  empirical ratio over a multi-day window stays inside [48 %, 52 %].
  **Fix**: add a unit test `test_side_distribution` that generates
  60 days of trades against the same dims and asserts
  `0.48 <= P(side=B) <= 0.52`.

## Minor / nits

- [ ] **MN-01** | `V002__synth_logic.sql:184` |
  The error message reads "Europe/Bucharest date does not equal
  @trade_date" — humane, but it does not include the offending
  `trade_uid` or the actual offending date, so debugging a
  production rejection requires running an ad-hoc SELECT against
  the rejected payload (which is gone after the ROLLBACK). **Fix**:
  capture an example row in a TOP 1 SELECT and stitch it into the
  THROW message (`'... offending: trade_uid=' + @example_uid +
  ', time_entry_ro_date=' + CONVERT(NVARCHAR(10), @example_date)`).

- [ ] **MN-02** | `tcp/synth/runner.py:140-152` (`_coerce_time`) |
  The fallback string-parse branch assumes well-formed `HH:MM[:SS[.fff]]`
  with no AM/PM or trailing offset. SQL Server's `TIME` columns
  always come back as `datetime.time` or `pyodbc.Time` so the
  fallback is dead code in practice. **Fix**: drop the string-parse
  branch and replace it with `raise TypeError(f"Unhandled time value: {value!r}")`
  so unexpected types fail loudly instead of silently mis-parsing.

- [ ] **MN-03** | `tcp/synth/trades.py:419-420` |
  `seq` is incremented per trader-per-trade, not per day; the
  resulting `T<YYYYMMDD>-<NNNN>` is unique within the list and
  scopes to ≤ `_MAX_TRADES_PER_TRADER * len(traders) = 15 * 24 = 360`
  per day — comfortably inside the 4-digit `NNNN` ceiling. **Fix**:
  add an assertion `assert seq <= 9999` after the inner loop, so a
  future expansion of the team (e.g. 666 traders) fails loud rather
  than silently overflows the format. **Note**: also document the
  ceiling in the docstring at line 408 — currently the format string
  is described but the ceiling is implicit.

- [ ] **MN-04** | `tcp/synth/trades.py:327` (`_draw_entry_time`) |
  `minute_offset = rng.randint(start_minutes, max(start_minutes, end_minutes - 1))`
  — for the "US Session" (15:30-22:00 in the test fixture), this
  resolves to `[930, 1319]` and `divmod(1319, 60) = (21, 59)`. Plus
  up to 480 minutes holding = exit at 05:59 the **next day**.
  Combined with the cross-row invariant at V002:179-184 (every
  `time_entry`'s Europe/Bucharest date must equal `@trade_date`),
  the *entry* is on @trade_date but the *exit* can spill over.
  Today the constraint is `time_entry` only, so no problem. **Note**:
  worth adding a future invariant on `time_exit` if/when overnight
  carry becomes a business concern.

- [ ] **MN-05** | `V002__synth_logic.sql:266-272` (CATCH wrapping) |
  The CATCH re-raises every error as `50199` with the original
  ERROR_NUMBER embedded in the message. Caller-facing telemetry
  loses the ability to switch on the original error number without
  string-parsing the wrapped message. **Fix**: re-raise the original
  number via `RAISERROR(@wrapped, 16, 1) WITH NOWAIT;` if you want
  to keep the `50199` envelope, or `THROW @err_num, @wrapped, 1;`
  to preserve the original number. (`THROW` requires user-defined
  error 50000+, so this is only safe when `@err_num >= 50000`; check
  before re-raising.)

- [ ] **MN-06** | `tcp/synth/runner.py:284-287` |
  `if result_row is not None: rows_inserted = int(result_row[0]) if result_row[0] is not None else 0`
  — defensive, but the proc's contract at V002:22-24 guarantees a
  single non-NULL row, so the `is not None` guard masks a real
  pipeline failure if the result set ever comes back empty. **Fix**:
  raise `RuntimeError("usp_GenerateDailyTrades returned no result set")`
  when `result_row is None`.

- [ ] **MN-07** | `tests/integration/test_generator_idempotency.py:92-108` |
  The `seeded_employees` fixture inserts a synthetic admin row into
  `dim_UserRoles` with a hard-coded UUID
  `'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'` but never invokes
  `sp_set_session_context @key=N'aad_object_id', @value=...`. As
  noted in CR-01, without the SESSION_CONTEXT set, the RLS
  predicate cannot match this row. Once CR-01 is fixed (runner sets
  the context), this fixture must also set the same UUID via
  `sp_set_session_context` on `db_conn` after the INSERT, or the
  test will start blocking the runner's INSERT.

- [ ] **MN-08** | `V002__synth_logic.sql:201` |
  `SET @rows_inserted = @@ROWCOUNT;` immediately after the INSERT
  is correct. Worth a one-line comment noting that subsequent
  statements (MERGE) overwrite `@@ROWCOUNT` so the order matters.

- [ ] **MN-09** | `tcp/synth/runner.py:254` |
  `payload_json = json.dumps([r.to_json_dict() for r in rows], default=str)` —
  the `default=str` fallback never fires because `to_json_dict()`
  already coerces every field to a JSON-native type. **Fix**: drop
  `default=str` so a future field-type regression (e.g. forgetting
  to convert a new `Decimal` column) raises `TypeError` instead of
  silently emitting `"Decimal('0.91')"` as a string.

- [ ] **MN-10** | `tcp/synth/seed_employees.py:262` |
  Docstring claims the function returns counts including
  `'accounts'`. The Etapa-3 contract expects 24 accounts for
  24 traders — which is what the code does — but the test asserts
  `accounts == 24` (line 135) while CR-03 calls for opening accounts
  for team leads too (raising to 30). Reconcile once CR-03 is
  resolved.

- [ ] **MN-11** | `V002__synth_logic.sql:97-117` (staging table
  variable) — the `@parsed` table-variable lacks statistics; for
  ≤ 360 rows the optimiser will pick a nested-loop join into
  `fact_Trades` and a hash aggregate for the GROUP BY, both of which
  are correct at this scale. No fix needed; noted for future scale.

## End-to-end flow trace

### Successful day (today = 2026-05-15 Friday, target trade_date = 2026-05-14 Thursday)

1. **Timer Trigger fires at 07:00 RO** via Function App `WEBSITE_TIME_ZONE='E. Europe Standard Time'`. NCRONTAB `0 0 7 * * 1-5`. (Out of scope for this review.)
2. **`run_daily(today=None)`** resolves `today = datetime.now(Europe/Bucharest).date() = 2026-05-15`.
3. **`_open_raw_connection()`** opens an MI-authenticated pyodbc connection with `autocommit=False`. **CR-01**: SESSION_CONTEXT is never set, so the RLS predicate will reject the INSERT.
4. **`_resolve_target_date(cursor, date(2026,5,15))`** queries
   `SELECT TOP 1 calendar_date FROM dim_Date WHERE calendar_date < '2026-05-15' AND is_weekday=1 AND is_ro_holiday=0 ORDER BY calendar_date DESC`
   → returns `2026-05-14`.
5. **Dim fetches**: `_fetch_traders` (≤ 30 rows after CR-03 fix; 24 today), `_fetch_markets` (≤ 24 rows from V001 seed), `_fetch_sessions` (3 rows), `_fetch_order_types` (4 rows).
6. **`generate_for_date(date(2026,5,14), traders, markets, sessions, order_types)`** returns ~24 × Poisson(8) ≈ 192 `TradeRow` instances. Deterministic via `seed_for_date(date(2026,5,14), suffix='trades')`.
7. **`json.dumps([r.to_json_dict() for r in rows], default=str)`** — produces a ~70 KB JSON array. Every key matches the V002 OPENJSON `WITH` clause one-to-one.
8. **`cursor.execute("EXEC dbo.usp_GenerateDailyTrades @trade_date=?, @trades=?", target, payload_json)`** ships the payload.
9. **Inside the proc**: `fn_IsTradingDay(2026-05-14)` returns 1 → proceed. Idempotency probe `IF EXISTS (SELECT 1 FROM fact_Trades WHERE trade_date_ro='2026-05-14')` returns 0 → proceed. `ISJSON(@trades)` returns 1. `OPENJSON ... WITH (...)` populates `@parsed` (~192 rows). Cross-row invariant check passes (every `time_entry`'s Europe/Bucharest date is 2026-05-14 by construction).
10. **`BEGIN TRANSACTION`**. INSERT `@parsed → fact_Trades`. **CR-01**: BLOCK PREDICATE rejects every row. Without the fix, the proc lands in CATCH, ROLLBACKs, and re-raises as 50199.
11. **(With CR-01 fixed)**: MERGE per-trader aggregate into `fact_DailyTraderPnL`. WHEN NOT MATCHED → 24 INSERTs. `COMMIT TRANSACTION`. Return `(192, 'ok')`.
12. **Runner**: `cursor.fetchone() = (192, 'ok')`; `conn.commit()` (owned conn). Returns `{'trade_date': '2026-05-14', 'rows_inserted': 192, 'duration_ms': ~600, 'status': 'ok'}`. Emits `tcp.synth.complete` structlog event.

### Failed day (malformed payload)

1. Steps 1-7 as above.
2. **`cursor.execute(...)`** ships a payload where the runner accidentally serialised one row with `time_entry = "not-a-timestamp"`.
3. **Inside the proc**: `OPENJSON ... WITH (time_entry NVARCHAR(40))` accepts the string. `TRY_CAST(j.time_entry AS DATETIMEOFFSET(3))` returns NULL. The `INSERT INTO @parsed` fails because `@parsed.time_entry` is `NOT NULL`. CATCH fires, rolls back (no tran was open yet, so `@@TRANCOUNT = 0` and the ROLLBACK is a no-op), re-raises as 50199 with the original message embedded.
4. **Runner**: `cursor.execute` raises `pyodbc.DatabaseError`. The `except Exception` block calls `conn.rollback()` (owned conn). The outer `except Exception as exc` block re-raises after emitting `tcp.synth.failed` with the error message.
5. **Function App**: surfaces the exception, App Insights captures it, retry policy decides whether to re-invoke (typically no retry — the same payload will fail the same way; an operator must inspect the structlog event).

## JSON contract conformance matrix

| Field            | Python type / serialised form                 | SQL type        | Notes |
|------------------|-----------------------------------------------|-----------------|-------|
| `trade_uid`      | `str` → JSON string                           | `VARCHAR(14)`   | Format `T<YYYYMMDD>-<NNNN>`; matches V001 regex (V001:392-393). |
| `trader_id`      | `int` → JSON integer                          | `INT NOT NULL`  | NOT NULL on @parsed; CR-04 test sends wrong key. |
| `account_id`     | `int` → JSON integer                          | `INT NOT NULL`  | OK. |
| `market_id`      | `int` → JSON integer                          | `INT NOT NULL`  | OK. |
| `session_id`     | `int` → JSON integer                          | `INT NOT NULL`  | OK. |
| `order_type_id`  | `int` → JSON integer                          | `INT NOT NULL`  | OK. |
| `side`           | `'B'` or `'S'` → JSON string                  | `CHAR(1) NOT NULL` | OK. |
| `quantity`       | `float(Decimal)` → JSON number                | `DECIMAL(18,4) NOT NULL` | MJ-01: precision risk on full-decimal-range values; safe for current quantisation grids. |
| `price_entry`    | `float(Decimal)` → JSON number                | `DECIMAL(18,6) NOT NULL` | Same caveat as above. |
| `price_exit`     | `float(Decimal) \| None` → JSON number / null | `DECIMAL(18,6) NULL` | OK for closed trades; NULL for opens (CK_fact_Trades_open_closed). |
| `time_entry`     | `datetime.isoformat()` → JSON string `"2026-05-14T10:23:45.000+03:00"` (DST-aware via `ZoneInfo("Europe/Bucharest")`) | `DATETIMEOFFSET(3) NOT NULL` (staged as `NVARCHAR(40)` then `TRY_CAST`) | OK; the staging detour at V002:140-144 is correct because OPENJSON `WITH` does not natively bind DATETIMEOFFSET. |
| `time_exit`      | `datetime.isoformat() \| None` → JSON string / null | `DATETIMEOFFSET(3) NULL` | OK. |
| `gross_pnl_eur`  | `float(Decimal) \| None` → JSON number / null | `DECIMAL(18,4) NULL` | OK; ISNULL'd to 0 inside the MERGE SUM. |
| `commission_eur` | `float(Decimal)` → JSON number                | `DECIMAL(18,4) NOT NULL` | OK; charged at entry for opens. |
| `net_pnl_eur`    | `float(Decimal) \| None` → JSON number / null | `DECIMAL(18,4) NULL` | OK; ISNULL'd in MERGE SUM; correctly excluded from win/loss CASE. |
| `is_open`        | `0` / `1` (int) → JSON integer                | `BIT NOT NULL`  | OPENJSON accepts 0/1; OK. |
| `fx_rate_to_eur` | `float(Decimal) \| None` → JSON number / null | `DECIMAL(18,8) NULL` | MJ-01: highest precision risk (8 fractional digits); recommend emitting as JSON string. |

**Field count**: 17 Python keys, 17 OPENJSON bindings, 17 `@parsed` columns, 17 `fact_Trades` columns — symmetric.

## Idempotency analysis

### Second invocation, same `@trade_date`

1. `run_daily(today=2026-05-15, conn=db_conn)` — same arguments.
2. `_resolve_target_date` returns `2026-05-14` (deterministic; `dim_Date` is read-only).
3. Dim fetches return the same rows (caching not enabled, but the deterministic seed and frozen dim contents make the JSON payload byte-identical to the first run).
4. `generate_for_date(date(2026,5,14), ...)` — deterministic by construction (`seed_for_date`); returns the same `TradeRow` list.
5. `cursor.execute(_SQL_EXEC_PROC, target, payload_json)` — same `@trade_date`, payload is regenerated but never reaches the parsing branch.
6. **Inside the proc**: `fn_IsTradingDay(2026-05-14) = 1`. Idempotency probe `IF EXISTS (SELECT 1 FROM fact_Trades WHERE trade_date_ro = '2026-05-14')` returns **1**. Short-circuit: `SELECT COUNT(*) ... AS rows_inserted, N'already_generated' AS [status]; RETURN 0;`. Crucially, **the MERGE into fact_DailyTraderPnL is NOT re-run** — the early return is *before* the BEGIN TRANSACTION block. Good.
7. **Runner**: maps `'already_generated'` → status `'already_generated'`. Returns the same dict shape with `status='already_generated'` and `rows_inserted` = the existing row count (192 in the example).
8. **Side effects**: zero. Neither `fact_Trades` nor `fact_DailyTraderPnL` is touched. No `updated_at` column rolls. No new transaction log records. The retry path is genuinely cheap.

### Idempotency invariants verified

- ✅ The pre-flight `IF EXISTS (... fact_Trades WHERE trade_date_ro = @trade_date)` correctly fires before the INSERT (V002:82-89).
- ✅ The MERGE on `fact_DailyTraderPnL` is gated by the same early-return (V002:207-247 is unreachable on the second invocation).
- ✅ The Python runner is also idempotent on retry: the second `run_daily` for the same date is a no-op on the database side.
- ⚠️ **Partial: open trades on day N stay `is_open = 1` forever** — there is no "carry-forward" mechanism that closes them on day N+1 or recomputes their PnL. This is fine for the synthesis exercise (per-day grain is the contract) but worth noting as a known limitation in `02_database_design.md`.
- ⚠️ **Watch-out**: if a contributor deletes `fact_DailyTraderPnL` rows manually (e.g. to recompute aggregates), re-running the proc for the same `@trade_date` will return `'already_generated'` and **will NOT** re-MERGE. Recovery requires also deleting the matching `fact_Trades` rows first. Document this in the operational runbook.

## Recommendation

**REJECT** in current form. The Etapa-3 deliverables are nearly there
and the design is sound — the SQL contract, the determinism guarantees,
and the MERGE-based materialisation are all correct. But **CR-01**
(missing SESSION_CONTEXT in the runner) is a production-stopper: the
daily generator simply cannot write to `fact_Trades` under the V001 RLS
policy as wired today. **CR-02** silently hides one of three documented
statuses. **CR-03** is a roster-size mismatch between three artefacts
(`seed_employees`, the runner's SELECT, and the integration test) and
must be reconciled. **CR-04** is a one-character typo in the integration
test that defeats the cross-date coverage. None of the four require
architectural change; all four are mechanical fixes well under one
engineer-day of work.

**Recommended sequence**:

1. Fix **CR-01** first (set SESSION_CONTEXT to the generator MI's
   `aad_object_id` in the runner; add the matching fixture-side
   `sp_set_session_context` per **MN-07**). Verify the full integration
   test passes against a live SQL Free DB.
2. Fix **CR-02** (status normalisation) and re-run
   `test_run_daily_skips_holiday` against a date whose previous
   business day is genuinely a RO holiday inside `dim_Date`. If no such
   date exists in the 2024-2030 window, parameterise the proc call with
   an explicit `@trade_date` that lands on a known holiday for the
   test.
3. Fix **CR-03** (extend `seed_employees` to open accounts for team
   leads too, so the 30-employee roster is fully trade-eligible).
4. Fix **CR-04** (rename `employee_id` → `trader_id` in the test
   payload).
5. Address **MJ-01** (string-encode decimals in `to_json_dict`),
   **MJ-02** (distinguish "no business day in dim_Date" from
   "skipped_holiday"), **MJ-05** (resolve the schema_history checksum
   placeholder) before stage tagging.
6. Address the remaining majors and minors at convenience or in
   pass 2.

After CR-01..CR-04 are fixed and CI is green, the verdict converts to
**ACCEPT_WITH_CHANGES** (with majors tracked in
`docs/decisions/ADR-004` or follow-up issues). After majors are
addressed, **ACCEPT**.
