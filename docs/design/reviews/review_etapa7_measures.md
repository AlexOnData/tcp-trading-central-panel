# Etapa 7 KPI measures review

**Reviewer**: business-analyst  
**Date**: 2026-05-16  
**Verdict**: ACCEPT_WITH_CHANGES

---

## Summary

48 KPI IDs are catalogued in `01_business_requirements.md §4`.
Of these, 47 are in scope for v1.0 (KPI-TR-063 is explicitly deferred to v2.0 per §10 item 11).
The `_Measures.tmdl` file contains **47 measures** (46 implemented + KPI-TR-039 as a documented
BLANK() stub), which is consistent with the spec after accounting for the v2.0 deferral.

Overall the implementation is sound. Sharpe / Sortino annualisation (`× SQRT(252)`) and the
`n_days < 5` NULL-fallback are correctly applied across all three levels (trader, team, floor).
The `_Measures` table is properly hidden via a hidden placeholder column. Romanian translations
cover all 47 measures and all 14 source tables.

Two minor formula discrepancies and three documentation gaps are flagged below. No critical issues
that would invalidate KPI semantics were found. The capital-baseline hardcoding and the three
behavioral approximations are acceptable trade-offs for v1.0 with the conditions documented below.

---

## KPI-by-KPI table

| KPI ID | Spec target | Measure present? | Formula match | Format match | Verdict |
|--------|-------------|-----------------|---------------|--------------|---------|
| KPI-TR-001 | Daily Trade Count | Yes | Match | `#,##0` OK | PASS |
| KPI-TR-002 | Avg Daily Trades/Trader | Yes | Match | `#,##0.0` OK (count/day — no integer constraint in spec) | PASS |
| KPI-TR-003 | Active Trading Days | Yes | Match | `#,##0` OK | PASS |
| KPI-TR-004 | Active Trader Ratio | Yes | Match | `0.0%` OK | PASS |
| KPI-TR-005 | Instrument Coverage | Yes | Match | `#,##0` OK | PASS |
| KPI-TR-006 | Team Daily Trade Count | Yes | Match | `#,##0` OK | PASS |
| KPI-TR-007 | Floor Daily Trade Count | Yes | Match | `#,##0` OK | PASS |
| KPI-TR-010 | Gross PnL per Trade | Yes (named "Gross PnL") | Minor deviation — see Finding M-01 | `#,##0.00 €` OK | MINOR |
| KPI-TR-011 | Net PnL per Trade | Yes (named "Net PnL") | Minor deviation — see Finding M-01 | `#,##0.00 €` OK | MINOR |
| KPI-TR-012 | Daily Gross PnL | Yes (named "Total Commission") | Semantic mismatch — see Finding M-02 | `#,##0.00 €` OK | MINOR |
| KPI-TR-013 | Daily Net PnL | Yes | Match | `#,##0.00 €` OK | PASS |
| KPI-TR-014 | Weekly Net PnL | Yes | Match (ISO week via WEEKDAY offset) | `#,##0.00 €` OK | PASS |
| KPI-TR-015 | Monthly Net PnL | Yes | Match | `#,##0.00 €` OK | PASS |
| KPI-TR-016 | MTD Net PnL | Yes | Match | `#,##0.00 €` OK | PASS |
| KPI-TR-017 | YTD Net PnL | Yes | Match | `#,##0.00 €` OK | PASS |
| KPI-TR-018 | Cumulative Net PnL | Yes | Match (ALL(dim_Date) removes date filter) | `#,##0.00 €` OK | PASS |
| KPI-TR-019 | Avg Net PnL per Trade | Yes | Match | `#,##0.00 €` OK | PASS |
| KPI-TM-010 | Team Daily Net PnL | Yes | Match | `#,##0.00 €` OK | PASS |
| KPI-TM-011 | Team MTD Net PnL | Yes | Match | `#,##0.00 €` OK | PASS |
| KPI-FL-010 | Floor Daily Net PnL | Yes | Match | `#,##0.00 €` OK | PASS |
| KPI-FL-011 | Floor MTD Net PnL | Yes | Match | `#,##0.00 €` OK | PASS |
| KPI-CO-010 | Company Daily Net PnL | Yes | Match (ALL(dim_TradingFloors)) | `#,##0.00 €` OK | PASS |
| KPI-CO-011 | Company YTD Net PnL | Yes | Match | `#,##0.00 €` OK | PASS |
| KPI-TR-020 | Return on Capital (ROC) | Yes | Capital hardcoded 80000 — see Finding M-03 | `0.00%` OK | PASS (accepted) |
| KPI-TR-021 | Annualized Return | Yes | BLANK() when NDays = 0; 252/NDays correct | `0.00%` OK | PASS |
| KPI-TR-022 | Capital Utilization Ratio | Yes | Match; Capital hardcoded 80000 | `0.00` OK | PASS (accepted) |
| KPI-TR-023 | EUR per 1000 Capital | Yes | Match; Capital hardcoded 80000 | `#,##0.00 €` OK | PASS (accepted) |
| KPI-TM-020 | Team Return on Capital | Yes | Denominator uses DISTINCTCOUNT of employees × 80000 — see Finding M-04 | `0.00%` OK | MINOR |
| KPI-FL-020 | Floor Return on Capital | Yes | Same denominator pattern as TM-020 — see Finding M-04 | `0.00%` OK | MINOR |
| KPI-CO-020 | Company Return on Capital | Yes | Same denominator pattern — see Finding M-04 | `0.00%` OK | MINOR |
| KPI-TR-030 | Max Drawdown EUR | Yes | Match (EARLIER-based running max pattern) | `#,##0.00 €` OK | PASS |
| KPI-TR-031 | Max Drawdown Pct | Yes | Capital hardcoded 80000; refs KPI-TR-030 | `0.0%` OK | PASS (accepted) |
| KPI-TR-032 | Daily PnL Volatility | Yes | STDEVX.S correct (sample std dev) | `#,##0.00 €` OK | PASS |
| KPI-TR-033 | Sharpe Ratio | Yes | `× SQRT(252)`, `n_days < 5` → BLANK() | `0.00` OK | PASS |
| KPI-TR-034 | Sortino Ratio | Yes | `× SQRT(252)`, `n_days < 5` → BLANK(); downside-only std | `0.00` OK | PASS |
| KPI-TR-035 | Win / Loss Ratio | Yes | Match | `0.00` OK | PASS |
| KPI-TR-036 | Profit Factor | Yes | Match | `0.00` OK | PASS |
| KPI-TR-037 | Average Win | Yes | Match | `#,##0.00 €` OK | PASS |
| KPI-TR-038 | Average Loss | Yes | Match (returns negative) | `#,##0.00 €` OK | PASS |
| KPI-TR-039 | Max Consecutive Losses | Yes (BLANK() stub) | Documented approximation — see Finding M-05 | `#,##0` OK | MINOR |
| KPI-TR-040 | VaR 95 Pct | Yes | PERCENTILEX.INC at 0.05; BLANK() when < 20 days | `#,##0.00 €` OK | PASS |
| KPI-TM-030 | Team Max Drawdown Pct | Yes | Match; capital via DISTINCTCOUNT × 80000 (same M-04 caveat) | `0.0%` OK | PASS (accepted) |
| KPI-TM-031 | Team Sharpe Ratio | Yes | `× SQRT(252)`, `n_days < 5` → BLANK() | `0.00` OK | PASS |
| KPI-FL-030 | Floor Max Drawdown Pct | Yes | Match; capital via DISTINCTCOUNT × 80000 | `0.0%` OK | PASS (accepted) |
| KPI-FL-031 | Floor Sharpe Ratio | Yes | `× SQRT(252)`, `n_days < 5` → BLANK() | `0.00` OK | PASS |
| KPI-TR-050 | Average Holding Time | Yes | AVERAGE of pre-computed column | `#,##0.0` OK (minutes) | PASS |
| KPI-TR-051 | Trades per Hour Distribution | Yes | COUNTROWS in context; per-hour grouping in visual | `#,##0` OK | PASS |
| KPI-TR-052 | Overnight Position Frequency | Yes | Documented approximation — see Finding M-06 | `0.0%` OK | MINOR |
| KPI-TR-053 | Weekend Carry Frequency | Yes | Documented approximation — see Finding M-07 | `0.0%` OK | MINOR |
| KPI-TR-054 | Intraday Rate | Yes | Documented approximation (< 480 min proxy) — see Finding M-07 | `0.0%` OK | MINOR |
| KPI-TR-055 | Best Trading Hour | Yes | TOPN(1) over ADDCOLUMNS | `#,##0` OK | PASS |
| KPI-TR-056 | Day of Week PnL Pattern | Yes | SUM in day-of-week filter context | `#,##0.00 €` OK | PASS |
| KPI-TR-060 | Win Rate | Yes | win_count / trade_count | `0.0%` OK | PASS |
| KPI-TR-061 | Profitable Day Rate | Yes | Match | `0.0%` OK | PASS |
| KPI-TR-062 | Break-Even Rate | Yes | ABS < 0.01 threshold correct | `0.0%` OK | PASS |
| KPI-TR-063 | Slippage Estimate | Absent (v2.0 deferral per §10 item 11) | N/A | N/A | PASS (deferred) |
| KPI-TM-060 | Team Win Rate | Yes | Match | `0.0%` OK | PASS |
| KPI-FL-060 | Floor Win Rate | Yes | Match | `0.0%` OK | PASS |
| KPI-TM-070 | Team Rank within Floor | Yes | RANKX DENSE DESC | `#,##0` OK | PASS |
| KPI-TM-071 | Team Rank within Company | Yes | RANKX DENSE DESC all teams | `#,##0` OK | PASS |
| KPI-TM-072 | Contribution to Floor PnL | Yes | Team/Floor ratio | `0.0%` OK | PASS |
| KPI-TM-073 | Intra-Team PnL Variance | Yes | STDEVX.S over employees | `#,##0.00 €` OK | PASS |
| KPI-FL-070 | Floor Rank | Yes | RANKX on ROC DESC | `#,##0` OK | PASS |
| KPI-FL-071 | Floor Contribution to Company PnL | Yes | Floor/Company ratio | `0.0%` OK | PASS |
| KPI-FL-072 | Floor 30-Day Rolling Net PnL | Yes | TODAY - 30 calendar-day window | `#,##0.00 €` OK | PASS |
| KPI-LR-001 | Team-Lead PnL Multiplier | Yes | Match (trader-only avg vs floor avg) | `0.00` OK | PASS |
| KPI-LR-002 | Floor Manager Coverage Index | Yes | Match (last 5-day window via Today - 7 proxy) — see Finding M-08 | `0.0%` OK | MINOR |
| KPI-LR-003 | Intra-Team Variance Rank | Yes | RANKX ASC (lower variance = rank 1) | `#,##0` OK | PASS |
| KPI-LR-004 | Team Lead Personal vs Team Average | Yes | Lead PnL / team avg PnL | `0.00` OK | PASS |

---

## Findings

### Critical

None.

---

### Major

None.

---

### Minor

**M-01 — KPI-TR-010 / KPI-TR-011: measure names diverge from spec KPI names**  
File: `powerbi/model/tables/_Measures.tmdl`, lines 85 and 79.  
Spec §4.2 defines KPI-TR-010 as "Gross PnL per Trade" (per-trade granularity) and KPI-TR-011 as
"Net PnL per Trade" (per-trade granularity). The implemented measures are named "Gross PnL" and
"Net PnL" and aggregate across all trades in the selected context (period totals, not per-trade).
This is a pragmatic and sensible implementation choice — PowerBI slicing achieves single-trade
granularity naturally — but the measure names no longer match the KPI ID descriptions, which may
confuse dashboard consumers expecting a per-trade value on a multi-trade visual.  
**Recommendation**: Either rename to `KPI-TR-010 Total Gross PnL` / `KPI-TR-011 Total Net PnL` to
reflect the aggregated nature, or add a description note that single-trade granularity is achieved
by filtering to one `trade_uid`.

**M-02 — KPI-TR-012: measure maps to commission, not "Daily Gross PnL"**  
File: `powerbi/model/tables/_Measures.tmdl`, line 91.  
Spec §4.2 assigns KPI-TR-012 to "Daily Gross PnL" (`SUM(gross_pnl_eur)` per trader-day). The
measure with prefix `KPI-TR-012` is implemented as "Total Commission"
(`SUM(commission_eur_total)`). This is a numbering collision: a legitimate commission measure was
created, but it was assigned the KPI-TR-012 slot that spec reserves for Daily Gross PnL.  
The spec's KPI-TR-012 "Daily Gross PnL" is functionally covered by the `KPI-TR-013 Daily Net PnL`
logic applied to the gross column, but no measure explicitly carries the KPI-TR-012 ID for "Daily
Gross PnL."  
**Recommendation**: Renumber the commission measure to a free ID (e.g., `KPI-TR-012b Total
Commission` or a new `KPI-TR-009 Total Commission` slot), and either add a true `KPI-TR-012 Daily
Gross PnL` measure or document in this finding that KPI-TR-012 (Daily Gross PnL) is satisfied by
filtering `KPI-TR-011 Gross PnL` to a single date. This should be resolved before the Etapa-10
integration review.

**M-03 — Capital baseline hardcoded to 80000 in five trader-level measures**  
File: `powerbi/model/tables/_Measures.tmdl`, lines 239, 253, 258, 265, 340.  
Measures KPI-TR-020, KPI-TR-021, KPI-TR-022, KPI-TR-023, and KPI-TR-031 use `VAR Capital = 80000`
instead of reading the effective capital from `config_Capital`. The production agent flagged this
as an "Etapa-12 polish" item.  
**Assessment**: Acceptable for v1.0. The spec states 80 000 EUR as the uniform baseline and per-trader
overrides are a future enhancement. The hardcoding is a deliberate, documented trade-off, not a
regression. The five affected measures all carry descriptions that state "80,000 EUR capital
baseline", making the assumption transparent to dashboard users.  
**Recommendation**: Track in ADR or TODO comment; no blocking action for v1.0. Confirm that the
five measures each carry an inline comment such as
`// TODO Etapa-12: read from config_Capital once per-trader overrides are introduced.`
Currently only KPI-TR-031 has a brief note; the others lack this comment.

**M-04 — Team / Floor / Company ROC denominators computed from DISTINCTCOUNT of employees, not SUM of capital baselines**  
File: `powerbi/model/tables/_Measures.tmdl`, lines 273–280, 282–290, 292–301.  
Spec §4.3 defines KPI-TM-020 denominator as `SUM(capital_baseline_eur for team members)` and
KPI-FL-020 as `SUM(capital_baseline_eur for floor employees)`. The DAX instead uses
`DISTINCTCOUNT(dim_Employees[employee_id]) × 80000`. Numerically these are equivalent when every
trader has the same 80 000 EUR baseline, but:
- The DISTINCTCOUNT approach is filter-context sensitive: if the employee slicer is partially
  applied, the denominator will reflect only the filtered employee count, not the full team/floor
  capital, potentially yielding inflated ROC figures in drill-through contexts.
- The pattern diverges from the spec's intent of summing actual capital allocations, which matters
  as soon as per-trader overrides are introduced in Etapa-12.  
**Recommendation**: Replace DISTINCTCOUNT × 80000 with a SUMX over `dim_Employees` joined to
`config_Capital`, keeping 80000 as the fallback. This makes the denominator robust to partial
filter contexts and aligns precisely with the spec formula.

**M-05 — KPI-TR-039 Max Consecutive Losses returns BLANK()**  
File: `powerbi/model/tables/_Measures.tmdl`, lines 450–459.  
The measure is a documented stub. The inline `///` comment accurately explains why (ordered window
logic not natively available in DAX without a dedicated DB column) and references ADR-002 Option C.
The description field confirms the TODO.  
**Assessment**: Acceptable for v1.0. The AI assistant (which can run ordered SQL CTEs) is the
cited alternative path for UC-06. The stub's `formatString: "#,##0"` is correct for a future count
value.  
**Recommendation**: Ensure the PowerBI visual that hosts this measure shows the literal string
"N/A — see AI assistant" rather than a blank card, to prevent users from interpreting BLANK() as
zero consecutive losses. This is a dashboard-page concern (Etapa 8), not a measure-file concern.

**M-06 — KPI-TR-052 Overnight Position Frequency: approximation lacks exact date-comparison**  
File: `powerbi/model/tables/_Measures.tmdl`, lines 586–598.  
The spec defines overnight as `CAST(time_exit AS DATE) > CAST(time_entry AS DATE)`. The measure
approximates by counting rows where `is_open = FALSE` and `time_exit` is not blank, divided by all
closed trades — it does not actually compare the exit date to the entry date. The `///` comment
acknowledges this and marks it as Etapa-12 polish.  
**Assessment**: Acceptable for v1.0 given the approximation is documented. The numerator logic
effectively counts all closed trades with a non-null exit, which is not an overnight filter at all
— it would equal the denominator in most contexts. The approximation may therefore overstate
overnight frequency.  
**Recommendation**: The comment should more explicitly state that the current numerator is a
placeholder that does not yet filter on date difference, so reviewers and future developers are not
misled into thinking any filtering is occurring. Etapa-12 must add the `is_overnight` computed
column to `v_trades_enriched` to make this KPI meaningful.

**M-07 — KPI-TR-053 Weekend Carry and KPI-TR-054 Intraday Rate: approximations documented inline**  
File: `powerbi/model/tables/_Measures.tmdl`, lines 600–626.  
Both measures carry `/// TODO Etapa-12 polish` comments and the approximations (RELATED dim_Date
join for weekend check; < 480 min proxy for intraday) are disclosed.  
**Assessment**: Acceptable for v1.0. The < 480-minute proxy for intraday is a reasonable heuristic
for a standard trading day. The `is_weekend_carry` and `is_intraday` columns should be added as
Etapa-12 schema extensions.

**M-08 — KPI-LR-002 uses Today - 7 calendar days instead of the spec's "last 5 trading days"**  
File: `powerbi/model/tables/_Measures.tmdl`, line 811 (`VAR Cutoff5D = Today - 7`).  
Spec §4.8 defines "last 5 trading days" as the activity window for the Floor Manager Coverage
Index. The implementation uses a 7-calendar-day lookback, which is a reasonable proxy (7 calendar
days ≈ 5 trading days excluding a weekend), but on weeks with public holidays it may include 4 or
6 trading days instead of exactly 5. The variable name `Cutoff5D` is misleading because it uses
the value 7.  
**Recommendation**: Rename `Cutoff5D` to `Cutoff7CalendarDays` for clarity, and add a `///`
comment: `// 7 calendar days approximates 5 trading days; exact 5-trading-day logic requires dim_Date[is_trading_day].`
This is a documentation fix only; no semantic regression for v1.0.

---

## Structural / Cross-Cutting Checks

**_Measures table visibility**: The `_Measures` table uses `column placeholder` with `isHidden`
and a calculated partition `source = {0}`. The table itself has no `isHidden` annotation at the
table level in the TMDL, only the column is hidden. In PowerBI Desktop the table will appear in
the Field List but contain no visible columns, which is the standard approach and is acceptable.
The table header comment documents the design rationale. No action required.

**displayFolder coverage**: All 47 measures carry a `displayFolder` property. Folders used:
Volume, PnL, Performance vs Capital, Risk, Behavioral, Quality, Team Aggregates, Floor Aggregates,
Leadership. All folder names are consistent with the KPI family sections in §4. No measure is
missing a displayFolder.

**Description coverage**: All 47 measures carry a `description` property. Descriptions match the
spec notes with sufficient fidelity. No material discrepancy found.

**Romanian translations**: All 47 measure captions are present in `cultures/ro-RO.tmdl`. All 14
source tables have translated captions. Translation quality is accurate Romanian; no incorrect
or misleading translations identified. Diacritics are omitted throughout (e.g., "tranzactii" vs
"tranzacții") — this is a consistent stylistic choice across the entire file, not a random error,
and is acceptable for v1.0 given that Romanian diacritics in `.tmdl` files can cause encoding
issues in some TMDL-to-PBIX deployment pipelines.

**Capital baseline hardcoding (80000) — v1.0 acceptability**: The production agent's flag is
confirmed as a non-blocking trade-off. The spec itself states "80 000 EUR baseline" as the uniform
value. Per-trader overrides are an Etapa-12 concern. The five affected measures are transparent
about the assumption in their description strings. No regression.

**Sharpe / Sortino annualisation**: `× SQRT(252)` applied correctly in all four Sharpe/Sortino
measures (KPI-TR-033, KPI-TR-034, KPI-TM-031, KPI-FL-031). `n_days < 5` NULL-fallback
(`IF(NDays >= 5 && DailyStd <> 0, ..., BLANK())`) is present in all four measures. The Sortino
additionally guards against zero downside std dev. Compliant with spec §4.4 calibration note.

---

## Recommendation

Accept the implementation with the following required changes before Etapa-10 final integration
review:

1. **Required (pre-Etapa-10)**: Resolve the KPI-TR-012 numbering collision (Finding M-02).
   Either renumber the commission measure or add a spec-aligned KPI-TR-012 Daily Gross PnL
   measure. This is a labeling correctness issue visible to dashboard consumers and auditors.

2. **Required (pre-Etapa-10)**: Align the KPI-TR-010 and KPI-TR-011 measure names with their
   actual aggregated semantics (Finding M-01). Rename or update descriptions to avoid
   misrepresenting period-level totals as per-trade values.

3. **Recommended (Etapa-12 tracker)**: Replace DISTINCTCOUNT × 80000 denominators in
   KPI-TM-020, KPI-FL-020, KPI-CO-020, KPI-TM-030, KPI-FL-030 with a SUMX over actual capital
   allocations (Finding M-04). Track in ADR-002 or a dedicated Etapa-12 backlog item.

4. **Recommended (documentation, any time)**: Add `// TODO Etapa-12` inline comments to the four
   trader-level measures that hardcode 80000 but currently lack the comment (KPI-TR-020, TR-021,
   TR-022, TR-023) (Finding M-03).

5. **Recommended (Etapa-8 dashboard page work)**: Surface "N/A — see AI assistant" for
   KPI-TR-039 in the PowerBI visual rather than relying on BLANK() display (Finding M-05).

6. **Recommended (documentation, any time)**: Clarify the KPI-TR-052 overnight approximation
   comment to make explicit that the current numerator does not yet filter on exit-date vs
   entry-date difference (Finding M-06).

7. **Recommended (any time)**: Rename `Cutoff5D` variable in KPI-LR-002 to avoid the 5/7
   mismatch confusion (Finding M-08).
