# ADR-002: Daily PnL materialisation strategy

- **Status**: Accepted
- **Date**: 2026-05-15
- **Stage**: Etapa 1 (design), informs Etapa 2 (schema apply) and Etapa 8 (observability)

## Context

Risk KPIs in `01_business_requirements.md` §4.4 (Sharpe Ratio, Sortino Ratio, max drawdown, VaR-95, daily volatility) all depend on a per-(employee_id, trade_date_ro) **daily PnL series**. The Etapa-1 database design (`02_database_design.md` §6) exposes this series through the view `v_daily_pnl`, which is defined as:

```text
v_daily_pnl
  ← v_employee_performance     (per-day aggregation of net PnL)
       ← v_trades_enriched     (8-table join over fact_Trades + dims)
            ← fact_Trades + dim_Employees + dim_Teams + dim_TradingFloors +
              dim_Markets + dim_Sessions + dim_OrderType + dim_Date
```

Every query against `v_daily_pnl` therefore re-runs the full 8-table join + a layered aggregation. The pass-1 database review (`review_db_pass1.md` MJ-03) flagged that:

- A single "Trader Detail" PowerBI page exercising five risk visuals re-runs the full stack five times.
- Indexed views are **not available** because the underlying views use `AT TIME ZONE 'E. Europe Standard Time'`, which is non-deterministic — disqualifying for `WITH SCHEMABINDING + INDEX`.
- The §13 performance budget targets ≤ 200 ms warm for the floor leaderboard but does not budget for `v_daily_pnl`-backed Sharpe / Sortino calculations.

We must decide whether to keep the view stack as-is, refactor to a flatter view definition, or introduce a materialised summary table.

## Options considered

### Option A — Keep the layered view stack

Pros: zero schema change; pure read-time computation; freshness is automatic.

Cons: every Sharpe/Sortino/drawdown query repeats the full 8-table scan; PowerBI Import refresh at 07:30 RO will still incur the cost daily; the AI assistant `/api/ask` worst-case p95 budget (≤ 1.5 s warm) becomes tight.

### Option B — Flatten `v_daily_pnl` to source directly from `fact_Trades`

Define `v_daily_pnl` to read directly from `fact_Trades + dim_Employees` (skipping the `v_employee_performance` → `v_trades_enriched` layers). Pros: removes two layers of overhead while keeping the freshness story. Cons: duplicates the daily aggregation logic between `v_employee_performance` and `v_daily_pnl` (two places to maintain the `is_open = 0` filter, the `net_pnl_eur` sum, etc.); the saving is modest because the cost driver is the per-row joins, not the aggregation depth.

### Option C — Introduce a materialised `fact_DailyTraderPnL`

Add a thin fact table `fact_DailyTraderPnL` keyed on `(employee_id, trade_date_ro)` with columns `trade_count, gross_pnl_eur_total, net_pnl_eur_total, win_count, loss_count, max_drawdown_window_eur` (the last computed in the closure step). Populate inside `usp_GenerateDailyTrades` in the same transaction as the daily insert into `fact_Trades`, so freshness is guaranteed without a separate refresh.

Pros: risk-KPI queries become point lookups on a 60-row-per-day table; the daily Sharpe / Sortino / drawdown calculations run in single-digit milliseconds; PowerBI relationships to `dim_Date` and `dim_Employees` remain trivial.

Cons: introduces a true OLAP-style summary table that must be kept in sync (handled by the proc); adds ~5 columns × 24 traders × 250 days × 6 years ≈ 36 000 rows total — negligible storage; minor migration ceremony in V001.

## Decision

**Adopt Option C — materialised `fact_DailyTraderPnL`** populated inside `usp_GenerateDailyTrades` in the same transaction as the `fact_Trades` insert. `v_daily_pnl` is then redefined as a thin presentation view over `fact_DailyTraderPnL` + `dim_Employees` + `dim_Date`, exposing cumulative PnL via `SUM(...) OVER (PARTITION BY employee_id ORDER BY trade_date_ro)`.

The materialisation is **not** an indexed view — it is a real append-update target. Idempotency is handled by `MERGE INTO fact_DailyTraderPnL USING (per-trader daily aggregate) ON (employee_id, trade_date_ro) WHEN MATCHED THEN UPDATE WHEN NOT MATCHED THEN INSERT;` inside the proc, so re-runs of the daily generator for the same date produce the same end state.

### Schema sketch

```sql
CREATE TABLE dbo.fact_DailyTraderPnL (
    daily_pnl_id          INT IDENTITY(1,1) NOT NULL,
    employee_id           INT NOT NULL,
    trade_date_ro         DATE NOT NULL,
    trade_count           INT NOT NULL,
    gross_pnl_eur_total   DECIMAL(18,4) NOT NULL,
    net_pnl_eur_total     DECIMAL(18,4) NOT NULL,
    commission_eur_total  DECIMAL(18,4) NOT NULL,
    win_count             INT NOT NULL,
    loss_count            INT NOT NULL,
    avg_holding_minutes   DECIMAL(18,4) NULL,
    created_at            DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_fact_DailyTraderPnL_created_at DEFAULT (SYSDATETIMEOFFSET()),
    updated_at            DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_fact_DailyTraderPnL_updated_at DEFAULT (SYSDATETIMEOFFSET()),
    CONSTRAINT PK_fact_DailyTraderPnL PRIMARY KEY (employee_id, trade_date_ro),
    CONSTRAINT FK_fact_DailyTraderPnL_dim_Employees FOREIGN KEY (employee_id) REFERENCES dbo.dim_Employees(employee_id),
    CONSTRAINT FK_fact_DailyTraderPnL_dim_Date      FOREIGN KEY (trade_date_ro) REFERENCES dbo.dim_Date(calendar_date)
);
CREATE NONCLUSTERED INDEX IX_fact_DailyTraderPnL_trade_date_ro ON dbo.fact_DailyTraderPnL(trade_date_ro) INCLUDE (net_pnl_eur_total);
```

`tcp_generator` gets `INSERT, UPDATE` on this table; `tcp_ai_assistant`, `tcp_bi_reader` get `SELECT`. `fact_DailyTraderPnL` is **subject to the same RLS policy** as `fact_Trades` so the row-filtering story stays uniform; the predicate is identical (`employee_id` matches the principal's scope).

## Consequences

- Adds one new fact-style table to V001 (Etapa 2) — naming-convention compliant (`fact_PascalCase`).
- `usp_GenerateDailyTrades` (Etapa 5) is responsible for the synchronous `MERGE` — failure to update the summary fails the entire daily transaction (consistent with the "all-or-nothing" idempotency contract).
- The performance budget for Sharpe/Sortino/VaR queries moves from "best-effort" to ≤ 50 ms warm.
- PowerBI dataset gains a true star-schema spoke from `dim_Date` → `fact_DailyTraderPnL` (Import mode).
- The RLS predicate function does not need to change — it is parameterised on `@trader_id_in_row`, which is `employee_id` here as it is in `fact_Trades`.

## Alternatives rejected

- **Option A (status quo)** — too risky for the §13 budget once five risk visuals stack on the same page.
- **Option B (flat view)** — saves one layer but not the join cost driver; not worth the duplicated aggregation logic.

## References

- `docs/design/02_database_design.md` §6 (views) and §13 (performance budget).
- `docs/design/reviews/review_db_pass1.md` MJ-03 (the originating finding).
- `docs/design/01_business_requirements.md` §4.4 (Risk KPI family).
