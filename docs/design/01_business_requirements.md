# Business Requirements & KPI Catalog
## TCP — Trading Central Panel
### TCP Capital Management SRL

**Document version**: 1.0  
**Stage**: Etapa 1 — Business Requirements  
**Status**: Draft — pending stakeholder review  
**Author**: TODO (thesis author placeholder)  
**Advisor**: TODO (thesis advisor placeholder)  
**Date**: 15.05.2026  

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Stakeholders & Personas](#2-stakeholders--personas)
3. [Business Goals](#3-business-goals)
4. [KPI Catalog](#4-kpi-catalog)
   - 4.1 [Volume & Activity KPIs](#41-volume--activity-kpis)
   - 4.2 [PnL KPIs](#42-pnl-kpis)
   - 4.3 [Performance vs Capital KPIs](#43-performance-vs-capital-kpis)
   - 4.4 [Risk KPIs](#44-risk-kpis)
   - 4.5 [Behavioral KPIs](#45-behavioral-kpis)
   - 4.6 [Quality KPIs](#46-quality-kpis)
   - 4.7 [Team & Floor Aggregate KPIs](#47-team--floor-aggregate-kpis)
   - 4.8 [Leadership & Coverage KPIs](#48-leadership--coverage-kpis)
5. [Dimensions & Slicers](#5-dimensions--slicers)
6. [Use Cases](#6-use-cases)
7. [AI-Assistant Scope & Guardrails](#7-ai-assistant-scope--guardrails)
8. [Acceptance Criteria](#8-acceptance-criteria)
9. [Non-Functional Requirements](#9-non-functional-requirements)
10. [Out of Scope for v1.0](#10-out-of-scope-for-v10)
11. [Glossary](#11-glossary)
12. [Open Questions](#12-open-questions)

---

## 1. Executive Summary

TCP — Trading Central Panel is an employee performance analytics platform built for TCP Capital Management SRL, a fictional Romanian boutique trading firm operating two trading floors (București as primary HQ and Cluj-Napoca as secondary). The platform ingests synthetic daily trading activity generated each weekday at 07:00 Europe/Bucharest time, stores it in an Azure SQL Database following a strict star-schema naming convention, and surfaces insights through two complementary consumer channels: PowerBI dashboards refreshed at 07:30 every trading day, and an Anthropic Claude–powered AI assistant that answers natural-language queries via a secured HTTP endpoint. The system models a four-level organizational hierarchy — Company, Trading Floor, Team, and individual Trader — across 32 employees, enabling drill-down performance analysis from executive level down to per-trade detail. The primary purpose is to give floor managers, team leads, and traders a single authoritative source of truth for trading performance metrics, risk-adjusted returns, behavioral patterns, and capital utilization, while operating entirely on Azure free-tier services at zero recurring monetary cost.

---

## 2. Stakeholders & Personas

### 2.1 Floor Manager

| Attribute | Details |
|-----------|---------|
| **Count** | 2 (1 per trading floor: București and Cluj-Napoca) |
| **Role** | Responsible for the overall performance, risk posture, and headcount of one trading floor. Reports to company leadership. Approves capital allocation changes. |
| **Decisions they make** | Which team receives additional capital allocation. Whether a team lead should be replaced. How the floor compares against the other floor. Whether aggregate risk thresholds are being breached. Monthly performance reviews. |
| **Frequency of access** | Daily — checks floor-level dashboard at approximately 08:00 after overnight data refresh. Deep review sessions weekly and monthly. |
| **Primary KPIs** | Floor PnL (daily, MTD, YTD), floor return on capital, max drawdown per floor, floor Sharpe ratio, floor vs. floor rank, team contribution to floor PnL, active-trader ratio per floor, intra-floor PnL variance. |
| **PowerBI pages** | "Company Overview", "Floor Performance" |
| **AI-assistant usage** | Ad hoc queries: "Which trader on the București floor had the highest max consecutive losses this month?", "How does Cluj compare to București in terms of profit factor YTD?" |

### 2.2 Team Lead

| Attribute | Details |
|-----------|---------|
| **Count** | 6 (1 per team, 3 per floor) |
| **Role** | Manages a team of typically 4 traders. Responsible for team-level performance, mentoring, and daily oversight. Acts as a trader themselves in addition to a supervisory role. |
| **Decisions they make** | Which trader needs coaching. Whether a trader's strategy is excessively risky. How to reallocate intra-team effort. Week-over-week improvement tracking. |
| **Frequency of access** | Daily — first thing in the morning after data refresh. Detailed drill-down sessions mid-week for performance reviews. |
| **Primary KPIs** | Team PnL, team win rate, team Sharpe ratio, per-trader PnL within the team, intra-team variance, team rank within floor, team-lead PnL multiplier, individual trader drawdown alerts. |
| **PowerBI pages** | "Team Performance", "Trader Detail" |
| **AI-assistant usage** | Diagnostic queries: "Which of my traders had the worst average holding time last week?", "Who on my team is dragging down our win rate this month?" |

### 2.3 Trader

| Attribute | Details |
|-----------|---------|
| **Count** | 24 (plus 6 team leads who also trade, for 30 total trading individuals) |
| **Role** | Executes trades against their allocated capital. Responsible for their own P&L, risk management, and adherence to firm guidelines. |
| **Decisions they make** | Strategy adjustment based on recent performance. Whether current drawdown requires reducing position sizes. Which sessions or market hours have historically been most profitable. |
| **Frequency of access** | Daily — personal dashboard checked at start and end of trading session. Weekly self-review. |
| **Primary KPIs** | Personal daily PnL, cumulative PnL, ROC, Sharpe ratio (MTD), win rate, max drawdown, average holding time, profit factor. |
| **PowerBI pages** | "Trader Detail" (filtered to own employee ID via AAD row-level security) |
| **AI-assistant usage** | Self-service queries: "What is my Sharpe ratio for this month?", "On which days of the week do I lose the most?" |

### 2.4 AI-Assistant User

| Attribute | Details |
|-----------|---------|
| **Count** | All authenticated users of the platform (all 32 employees) |
| **Role** | Any authenticated user accessing the `POST /api/ask` endpoint through the Static Web App interface. The AI assistant surfaces answers to natural-language questions grounded in the trading dataset. |
| **Decisions they make** | Depends on the user's organizational role. The assistant augments, not replaces, the PowerBI dashboards. |
| **Frequency of access** | On demand, any time, any device with a browser and network access. |
| **Primary KPIs** | Any KPI in this catalog, expressed in natural language. The assistant cites the underlying view and metric name in every response. |
| **AI-assistant usage** | Cross-cutting or ad hoc queries not pre-built into dashboards. Trend narration, comparison queries, ranking queries. |

---

## 3. Business Goals

The following goals define the measurable outcomes that TCP — Trading Central Panel is designed to support. Goals are listed in descending priority.

**BG-01 — Early identification of underperformance**  
Identify individual traders whose rolling 5-trading-day PnL, win rate, or drawdown breach predefined thresholds, so that team leads can intervene within one week of a performance deterioration starting.

**BG-02 — Transparent team and floor benchmarking**  
Produce daily, weekly, and monthly rank tables comparing all 6 teams and both floors on risk-adjusted return, enabling floor managers to make objective capital-reallocation decisions within one business day of the reference period closing.

**BG-03 — Capital utilization monitoring**  
Track each trader's utilization of their 80 000 EUR capital baseline and flag under-utilization (< 30 % average position size relative to baseline) or over-leverage, to ensure firm-wide capital efficiency.

**BG-04 — Risk posture transparency**  
Surface drawdown, volatility, Sharpe and Sortino ratios at every organizational level (trader → team → floor → company) on a daily cadence, so that no risk breach goes unnoticed for more than one trading day.

**BG-05 — Behavioral pattern awareness**  
Give traders and team leads visibility into behavioral metrics (holding time, session heatmap, overnight position frequency) so that systematic behavioral inefficiencies can be diagnosed and corrected within a monthly coaching cycle.

**BG-06 — AI-assisted insight discovery**  
Enable any authenticated user to ask natural-language questions against the full trading dataset and receive accurate, cited answers within 4 seconds (p95), reducing ad hoc data-request load on any analytics team.

**BG-07 — Zero recurring cost operation**  
Maintain all Azure infrastructure on free-tier offerings so that the total recurring monetary cost of operating TCP — Trading Central Panel remains at EUR 0/month throughout the entire lifecycle of this thesis project.

**BG-08 — Academic-grade reproducibility**  
All data generation, schema creation, KPI computation, and dashboard deployment are fully automated and version-controlled, so that the complete platform can be torn down and redeployed from scratch in a single `azd up` command for thesis demonstration purposes.

---

## 4. KPI Catalog

### Notation conventions

Throughout this catalog the following field-name aliases are used. Column names match the actual schema in `02_database_design.md`.

| Alias | Meaning |
|-------|---------|
| `gross_pnl_eur` | Realized profit or loss of a single trade in EUR before commissions (positive = profit, negative = loss). Persisted in `fact_Trades`; see `02_database_design.md` §4.1 for storage rationale. |
| `commission_eur` | Commission cost charged per trade in EUR |
| `net_pnl_eur` | `gross_pnl_eur - commission_eur`. Also persisted in `fact_Trades`; see `02_database_design.md` §4.1. |
| `quantity` | Number of units / lots traded |
| `price_entry` | Entry price per unit |
| `price_exit` | Exit price per unit |
| `time_entry` | `DATETIMEOFFSET(3)` timestamp of trade entry (Europe/Bucharest) |
| `time_exit` | `DATETIMEOFFSET(3)` timestamp of trade exit (Europe/Bucharest) |
| `capital_baseline_eur` | Effective capital allocated to a trader from `config_Capital` |
| `trade_date` | Canonical date slicer (`CAST(time_entry AT TIME ZONE 'E. Europe Standard Time' AS DATE)`) |
| `employee_id` | Surrogate key for a trader or team lead in `dim_Employees` |
| `team_id` | Surrogate key for a team in `dim_Teams` |
| `floor_id` | Surrogate key for a trading floor in `dim_TradingFloors` |
| `is_win` | Boolean: `net_pnl_eur > 0` |
| `holding_minutes` | `DATEDIFF(minute, time_entry, time_exit)` |
| `N` | Count of trades in the aggregation window |
| `D` | Count of trading days in the aggregation window |
| `RF` | Risk-free rate — assumed **0** for all ratio calculations (documented per KPI) |

---

### 4.1 Volume & Activity KPIs

| KPI ID | Name | Description | Formula | Granularity | Unit | Refresh | Target / Threshold | Consumer | Notes |
|--------|------|-------------|---------|-------------|------|---------|-------------------|----------|-------|
| KPI-TR-001 | Daily Trade Count | Total number of trades executed by a trader on a given trading day | `COUNT(trade_uid) WHERE trade_date = D AND employee_id = E` | Per trader-day | count | Daily | ≥ 3 trades/day (normal activity floor) | Both | Excludes trades flagged as cancelled or test records |
| KPI-TR-002 | Average Daily Trades per Trader | Mean number of trades per active trading day over a window | `SUM(trade_count) / COUNT(DISTINCT trade_date)` over window | Per trader / rolling window | count/day | Daily | ≥ 3, ≤ 30 (sanity bounds) | Both | Computed over the selected time window in the slicer |
| KPI-TR-003 | Active Trading Days | Number of distinct calendar dates on which a trader executed at least one trade | `COUNT(DISTINCT trade_date)` | Per trader / period | count | Daily | — | PowerBI | Used to normalize period KPIs; excludes weekends and public holidays |
| KPI-TR-004 | Active Trader Ratio | Proportion of the firm's trading individuals (24 traders + 6 team leads) who traded on a given day | `COUNT(DISTINCT employee_id WITH trades) / COUNT(DISTINCT employee_id WHERE is_active=1 AND role IN ('trader','team_lead'))` | Per company-day | % | Daily | ≥ 85 % on any trading day | PowerBI | Denominator is dynamic to handle attrition/onboarding; currently resolves to 30 for the baseline org |
| KPI-TR-005 | Instrument Coverage | Number of distinct financial instruments traded by a trader in a period | `COUNT(DISTINCT market_id)` | Per trader / period | count | Daily | — | Both | Tracks diversification of instrument universe; references `dim_Markets` |
| KPI-TR-006 | Team Daily Trade Count | Total trades across all members of a team on a given day | `SUM(KPI-TR-001) GROUP BY team_id, trade_date` | Per team-day | count | Daily | — | PowerBI | Rolled up from trader level |
| KPI-TR-007 | Floor Daily Trade Count | Total trades across all members of a trading floor on a given day | `SUM(KPI-TR-001) GROUP BY floor_id, trade_date` | Per floor-day | count | Daily | — | PowerBI | Rolled up from team level |

---

### 4.2 PnL KPIs

| KPI ID | Name | Description | Formula | Granularity | Unit | Refresh | Target / Threshold | Consumer | Notes |
|--------|------|-------------|---------|-------------|------|---------|-------------------|----------|-------|
| KPI-TR-010 | Gross PnL per Trade | Realized profit or loss on a single trade before commissions | `price_exit * quantity - price_entry * quantity` | Per trade | EUR | Daily | > 0 ideally | Both | Source field `gross_pnl_eur` in `fact_Trades`; stored pre-computed |
| KPI-TR-011 | Net PnL per Trade | Realized profit or loss after deducting trade commission | `gross_pnl_eur - commission_eur` | Per trade | EUR | Daily | > 0 ideally | Both | `net_pnl_eur` stored; commission model defined in `tcp/synth/commissions.py` |
| KPI-TR-012 | Daily Gross PnL | Sum of gross PnL for all trades by a trader on one trading day | `SUM(gross_pnl_eur) WHERE trade_date = D AND employee_id = E` | Per trader-day | EUR | Daily | > 0 | Both | Displayed as `12.345,67 €` in PowerBI with `ro-RO` locale |
| KPI-TR-013 | Daily Net PnL | Sum of net PnL for all trades by a trader on one trading day | `SUM(net_pnl_eur) WHERE trade_date = D AND employee_id = E` | Per trader-day | EUR | Daily | > 0 | Both | Preferred metric for performance evaluation |
| KPI-TR-014 | Weekly Net PnL | Sum of daily net PnL over a calendar week (Mon–Fri) | `SUM(net_pnl_eur) WHERE ISO_WEEK(trade_date) = W AND employee_id = E` | Per trader-week | EUR | Daily | > 0 | Both | ISO week numbering; partial weeks included at period boundaries |
| KPI-TR-015 | Monthly Net PnL | Sum of daily net PnL for a calendar month | `SUM(net_pnl_eur) WHERE YEAR(trade_date) = Y AND MONTH(trade_date) = M AND employee_id = E` | Per trader-month | EUR | Daily | > 0 | Both | Used for month-over-month comparison charts |
| KPI-TR-016 | MTD Net PnL | Month-to-date cumulative net PnL from the first trading day of the current month | `SUM(net_pnl_eur) WHERE trade_date >= FIRST_DAY_OF_MONTH AND trade_date <= TODAY AND employee_id = E` | Per trader / MTD | EUR | Daily | > 0 | Both | Resets on the first trading day of each month |
| KPI-TR-017 | YTD Net PnL | Year-to-date cumulative net PnL from the first trading day of the current calendar year | `SUM(net_pnl_eur) WHERE YEAR(trade_date) = CURRENT_YEAR AND employee_id = E` | Per trader / YTD | EUR | Daily | > 0 | Both | Resets each January; useful for annual performance reviews |
| KPI-TR-018 | Cumulative Net PnL | All-time cumulative net PnL since the earliest trade on record for a trader | `SUM(net_pnl_eur) WHERE employee_id = E` (all history) | Per trader / all-time | EUR | Daily | > 0 | PowerBI | Useful for visualizing equity curves in line charts |
| KPI-TR-019 | Average Net PnL per Trade | Mean net profit or loss per individual trade over a period | `SUM(net_pnl_eur) / COUNT(trade_uid)` | Per trader / period | EUR | Daily | > 0 | Both | Key denominator in profit factor and expectancy |
| KPI-TM-010 | Team Daily Net PnL | Sum of net PnL for all team members on a given trading day | `SUM(net_pnl_eur) GROUP BY team_id, trade_date` | Per team-day | EUR | Daily | > 0 | Both | Rolled up from KPI-TR-013 |
| KPI-TM-011 | Team MTD Net PnL | Month-to-date cumulative net PnL for a team | `SUM(net_pnl_eur) WHERE team_id = T AND trade_date IN MTD` | Per team / MTD | EUR | Daily | > 0 | Both | — |
| KPI-FL-010 | Floor Daily Net PnL | Sum of net PnL for all members of a trading floor on a given trading day | `SUM(net_pnl_eur) GROUP BY floor_id, trade_date` | Per floor-day | EUR | Daily | > 0 | Both | Rolled up from team level |
| KPI-FL-011 | Floor MTD Net PnL | Month-to-date cumulative net PnL for a trading floor | `SUM(net_pnl_eur) WHERE floor_id = F AND trade_date IN MTD` | Per floor / MTD | EUR | Daily | > 0 | Both | — |
| KPI-CO-010 | Company Daily Net PnL | Sum of net PnL across all 32 employees on a given trading day | `SUM(net_pnl_eur) WHERE trade_date = D` | Per company-day | EUR | Daily | > 0 | PowerBI | The highest-level PnL aggregate |
| KPI-CO-011 | Company YTD Net PnL | Year-to-date net PnL for the entire firm | `SUM(net_pnl_eur) WHERE YEAR(trade_date) = CURRENT_YEAR` | Per company / YTD | EUR | Daily | > 0 | PowerBI | Headline metric for executive summary card |

---

### 4.3 Performance vs Capital KPIs

| KPI ID | Name | Description | Formula | Granularity | Unit | Refresh | Target / Threshold | Consumer | Notes |
|--------|------|-------------|---------|-------------|------|---------|-------------------|----------|-------|
| KPI-TR-020 | Return on Capital (ROC) | Net PnL as a percentage of the trader's allocated capital baseline | `SUM(net_pnl_eur) / capital_baseline_eur * 100` over period | Per trader / period | % | Daily | ≥ 1 % per month (12 % annualized) | Both | Uses effective `capital_baseline_eur` from `config_Capital` at start of period |
| KPI-TR-021 | Annualized Return | ROC extrapolated to an annual basis using trading days in the year (252 assumed) | `ROC_period * (252 / D) * 100` | Per trader / period | % | Daily | ≥ 12 % annualized | Both | D = count of trading days in the period; assumes 252 trading days/year |
| KPI-TR-022 | Capital Utilization Ratio | Average ratio of notional position value to capital baseline, measuring how much of the allocated capital a trader actively deploys | `AVG(price_entry * quantity) / capital_baseline_eur` per day | Per trader-day | ratio | Daily | 0.30 ≤ ratio ≤ 2.00 | PowerBI | Ratio > 1.0 indicates leverage; ratio < 0.30 flagged as under-utilization |
| KPI-TR-023 | EUR per 1000 Capital | Net PnL earned per every 1 000 EUR of allocated capital in the period | `SUM(net_pnl_eur) / (capital_baseline_eur / 1000)` | Per trader / period | EUR | Daily | ≥ 10 EUR / 1 000 EUR / month | Both | Normalized comparator useful when traders have different capital baselines |
| KPI-TM-020 | Team Return on Capital | Team net PnL as a percentage of the sum of all team members' capital baselines | `SUM(net_pnl_eur for team) / SUM(capital_baseline_eur for team members) * 100` | Per team / period | % | Daily | ≥ 1 % per month | Both | SUM of capital baselines denominates across the team |
| KPI-FL-020 | Floor Return on Capital | Floor net PnL as a percentage of the floor's total capital allocation | `SUM(net_pnl_eur for floor) / SUM(capital_baseline_eur for floor employees) * 100` | Per floor / period | % | Daily | ≥ 1 % per month | Both | Used in floor vs. floor comparisons by the floor manager |
| KPI-CO-020 | Company Return on Capital | Firm-wide net PnL as a percentage of total capital deployed across all 30 trading employees | `SUM(net_pnl_eur) / SUM(capital_baseline_eur for all traders) * 100` | Per company / period | % | Monthly | ≥ 10 % annualized | PowerBI | Headline metric reported on the Company Overview page |

---

### 4.4 Risk KPIs

> **Risk-free rate assumption**: All Sharpe and Sortino calculations in this system use RF = 0. This simplification is explicitly documented here and in the PowerBI tooltip for each ratio. The justification is (a) the synthetic EUR-denominated dataset does not model a specific interest rate environment and (b) this is consistent with standard practice for intraday and short-term trading performance evaluation.

| KPI ID | Name | Description | Formula | Granularity | Unit | Refresh | Target / Threshold | Consumer | Notes |
|--------|------|-------------|---------|-------------|------|---------|-------------------|----------|-------|
| KPI-TR-030 | Max Drawdown | The largest peak-to-trough decline in cumulative net PnL over the period, expressed as an absolute EUR amount | `MIN(cumulative_net_pnl) - MAX(cumulative_net_pnl WHERE trade_date ≤ date_of_min)` over the period's equity curve | Per trader / period | EUR | Daily | ≤ 8 % of capital_baseline_eur | Both | Computed over the daily equity curve (one point per trading day) |
| KPI-TR-031 | Max Drawdown % | Max drawdown expressed as a percentage of capital baseline | `max_drawdown_eur / capital_baseline_eur * 100` | Per trader / period | % | Daily | ≤ 8 % | Both | Threshold trigger: if breached, highlight red in PowerBI |
| KPI-TR-032 | Daily PnL Volatility | Standard deviation of daily net PnL over the period — measures consistency of returns | `STDEV(daily_net_pnl) over D trading days` | Per trader / period | EUR | Daily | — | Both | Higher volatility is penalized in Sharpe / Sortino |
| KPI-TR-033 | Sharpe Ratio | Annualised risk-adjusted return: mean daily net PnL divided by the standard deviation of daily net PnL, scaled to an annual basis (RF = 0) | `(MEAN(daily_net_pnl) / STDEV(daily_net_pnl)) × SQRT(252)` | Per trader / period | ratio | Daily | ≥ 1.0 (good), ≥ 1.5 (excellent) | Both | RF = 0; annualised to facilitate cross-period comparison (assumes 252 trading days/year); requires at least 5 trading days — when `n_days < 5` the value is NULL; dashboards must show `'pending — need ≥ 5 trading days'` and the AI assistant must surface the same explanation rather than returning empty |
| KPI-TR-034 | Sortino Ratio | Like Sharpe but penalizes only downside volatility; uses standard deviation of negative daily PnL only, annualised (RF = 0) | `(MEAN(daily_net_pnl) / STDEV(daily_net_pnl WHERE daily_net_pnl < 0)) × SQRT(252)` | Per trader / period | ratio | Daily | ≥ 1.5 | Both | RF = 0; annualised (`× SQRT(252)`); NULL when no losing days exist in the period or when `n_days < 5` |
| KPI-TR-035 | Win / Loss Ratio | Ratio of average winning trade net PnL to absolute value of average losing trade net PnL | `AVG(net_pnl_eur WHERE net_pnl_eur > 0) / ABS(AVG(net_pnl_eur WHERE net_pnl_eur < 0))` | Per trader / period | ratio | Daily | ≥ 1.5 | Both | NULL when no winning or no losing trades exist |
| KPI-TR-036 | Profit Factor | Ratio of total gross profit (sum of winning trades) to total gross loss (absolute sum of losing trades) | `SUM(net_pnl_eur WHERE net_pnl_eur > 0) / ABS(SUM(net_pnl_eur WHERE net_pnl_eur < 0))` | Per trader / period | ratio | Daily | ≥ 1.5 (good), ≥ 2.0 (excellent) | Both | Profit factor > 1.0 means the strategy is net profitable |
| KPI-TR-037 | Average Win | Mean net PnL of winning trades in the period | `AVG(net_pnl_eur WHERE net_pnl_eur > 0)` | Per trader / period | EUR | Daily | > 0 | Both | — |
| KPI-TR-038 | Average Loss | Mean net PnL of losing trades in the period (negative value) | `AVG(net_pnl_eur WHERE net_pnl_eur < 0)` | Per trader / period | EUR | Daily | — | Both | Displayed as a negative number; absolute value used in win/loss ratio |
| KPI-TR-039 | Max Consecutive Losses | The longest unbroken sequence of trades with `net_pnl_eur < 0`, ordered by `time_entry` | Computed via window function: `MAX(consecutive_loss_streak) WHERE employee_id = E AND trade_date IN period` | Per trader / period | count | Daily | ≤ 5 consecutive losses | Both | Requires ordered scan; computed in a SQL CTE or Python aggregation |
| KPI-TR-040 | Value at Risk (95 %) | Historical VaR at 95 % confidence: the 5th percentile of daily net PnL distribution over the period | `PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY daily_net_pnl ASC)` over D days | Per trader / period | EUR | Daily | VaR ≤ 5 % of capital_baseline_eur | Both | Historical simulation method; at least 20 trading days required for validity |
| KPI-TM-030 | Team Max Drawdown % | Max drawdown of the team's cumulative net PnL as a % of total team capital | Same formula as KPI-TR-031 applied to team-level daily equity curve | Per team / period | % | Daily | ≤ 8 % | Both | Team equity curve = SUM of member daily PnL by date |
| KPI-TM-031 | Team Sharpe Ratio | Annualised Sharpe ratio on the team's aggregate daily net PnL series (RF = 0) | Same formula as KPI-TR-033 — `(MEAN(daily_net_pnl) / STDEV(daily_net_pnl)) × SQRT(252)` — applied to `SUM(daily_net_pnl) GROUP BY team_id, trade_date` | Per team / period | ratio | Daily | ≥ 1.0 | Both | Assumes RF = 0; annualised (`× SQRT(252)`); NULL when `n_days < 5` |
| KPI-FL-030 | Floor Max Drawdown % | Max drawdown of the floor's cumulative net PnL as a % of total floor capital | Same formula applied to floor-level daily equity curve | Per floor / period | % | Daily | ≤ 8 % | PowerBI | — |
| KPI-FL-031 | Floor Sharpe Ratio | Annualised Sharpe ratio on the floor's aggregate daily net PnL series (RF = 0) | Same formula as KPI-TR-033 — `(MEAN(daily_net_pnl) / STDEV(daily_net_pnl)) × SQRT(252)` — applied to floor-level daily PnL aggregation | Per floor / period | ratio | Daily | ≥ 1.0 | PowerBI | Assumes RF = 0; annualised (`× SQRT(252)`); NULL when `n_days < 5` |

---

### 4.5 Behavioral KPIs

| KPI ID | Name | Description | Formula | Granularity | Unit | Refresh | Target / Threshold | Consumer | Notes |
|--------|------|-------------|---------|-------------|------|---------|-------------------|----------|-------|
| KPI-TR-050 | Average Holding Time | Mean duration between `time_entry` and `time_exit` across all trades in the period | `AVG(DATEDIFF(minute, time_entry, time_exit))` | Per trader / period | minutes | Daily | — | Both | Trades without a recorded `time_exit` are excluded; long holding times may indicate forgotten open positions |
| KPI-TR-051 | Trades-per-Hour Distribution | Count of trades grouped by the hour-of-day of `time_entry`, forming a 24-bucket histogram | `COUNT(trade_uid) GROUP BY DATEPART(hour, time_entry AT TIME ZONE 'E. Europe Standard Time')` | Per trader / period | count per hour bucket | Daily | — | PowerBI | Used as a heatmap visual on the "Trader Detail" page; hours 0–5 and 21–23 may indicate after-hours activity |
| KPI-TR-052 | Overnight Position Frequency | Proportion of trades where `time_exit` falls on a different calendar date than `time_entry` (i.e., the position was held overnight) | `COUNT(trade_uid WHERE CAST(time_exit AS DATE) > CAST(time_entry AS DATE)) / COUNT(trade_uid) * 100` | Per trader / period | % | Daily | ≤ 10 % (firm guideline for intraday-focused desks) | Both | Overnight positions carry gap risk; threshold is configurable |
| KPI-TR-053 | Weekend-Carry Frequency | Proportion of trades where the position is open over a Saturday or Sunday | `COUNT(trade_uid WHERE DATEPART(weekday, time_entry) IN (6,7) OR DATEPART(weekday, time_exit) IN (6,7)) / COUNT(trade_uid) * 100` | Per trader / period | % | Daily | ≤ 2 % | Both | Nearly always unintentional in this synthetic model; high values indicate a data anomaly |
| KPI-TR-054 | Intraday vs Swing Split | Proportion of trades classified as intraday (closed same day) versus swing (held overnight or longer) | `Intraday%: COUNT(same-day closures) / N * 100; Swing%: 100 - Intraday%` | Per trader / period | % (two values) | Daily | — | PowerBI | Classification boundary: trade_date(time_entry) = trade_date(time_exit) → intraday |
| KPI-TR-055 | Best Trading Hour | The single hour-of-day bucket with the highest total net PnL for the trader over the period | `SELECT TOP 1 DATEPART(hour, time_entry) ORDER BY SUM(net_pnl_eur) DESC` | Per trader / period | hour (0–23) | Daily | — | AI assistant | Narrative output in the AI assistant; displayed as a badge in PowerBI |
| KPI-TR-056 | Day-of-Week PnL Pattern | Net PnL broken down by day of week (Monday through Friday), showing which weekday is systematically most or least profitable | `SUM(net_pnl_eur) GROUP BY DATEPART(weekday, trade_date)` | Per trader / period | EUR per weekday | Daily | — | Both | Useful for diagnosing systematic behavioral biases |

---

### 4.6 Quality KPIs

| KPI ID | Name | Description | Formula | Granularity | Unit | Refresh | Target / Threshold | Consumer | Notes |
|--------|------|-------------|---------|-------------|------|---------|-------------------|----------|-------|
| KPI-TR-060 | Win Rate | Proportion of trades that closed with a positive net PnL | `COUNT(trade_uid WHERE net_pnl_eur > 0) / COUNT(trade_uid) * 100` | Per trader / period | % | Daily | ≥ 55 % | Both | Trades with `net_pnl_eur = 0` are counted as break-even, not wins |
| KPI-TR-061 | Profitable Day Rate | Proportion of trading days where the trader's daily net PnL was positive | `COUNT(DISTINCT trade_date WHERE daily_net_pnl > 0) / COUNT(DISTINCT trade_date) * 100` | Per trader / period | % | Daily | ≥ 60 % | Both | A trader can have a positive day rate but negative overall PnL if loss days are large |
| KPI-TR-062 | Break-Even Rate | Proportion of trades that closed with exactly zero net PnL (within a rounding tolerance of ±0.01 EUR) | `COUNT(trade_uid WHERE ABS(net_pnl_eur) < 0.01) / COUNT(trade_uid) * 100` | Per trader / period | % | Daily | ≤ 5 % | PowerBI | Very high break-even rate may indicate premature exits at cost |
| KPI-TR-063 | Slippage Estimate *(deferred — v2.0)* | Proxy for execution quality: difference between expected trade value at mid-price and actual realized `gross_pnl_eur` for directional trades; modeled as `(price_exit - expected_exit_price) * quantity` when an expected exit price is available in the synthetic data | `AVG((actual_net_pnl - modeled_net_pnl) / quantity)` | Per trader / period | EUR / unit | Daily | — | AI assistant | **Deferred to v2.0 per §10 item 11.** Requires that `fact_Trades` stores a `modeled_pnl_eur` column; this column is not allocated in the v1.0 schema. See OQ-04. |
| KPI-TM-060 | Team Win Rate | Proportion of winning trades across all team members in the period | `COUNT(trade_uid WHERE net_pnl_eur > 0 AND team_id = T) / COUNT(trade_uid WHERE team_id = T) * 100` | Per team / period | % | Daily | ≥ 55 % | Both | — |
| KPI-FL-060 | Floor Win Rate | Proportion of winning trades across all members of a floor in the period | Same formula scoped to `floor_id = F` | Per floor / period | % | Daily | ≥ 55 % | Both | — |

---

### 4.7 Team & Floor Aggregate KPIs

| KPI ID | Name | Description | Formula | Granularity | Unit | Refresh | Target / Threshold | Consumer | Notes |
|--------|------|-------------|---------|-------------|------|---------|-------------------|----------|-------|
| KPI-TM-070 | Team Rank within Floor | Ordinal rank of a team relative to all teams on the same floor, based on MTD net PnL | `RANK() OVER (PARTITION BY floor_id ORDER BY team_mtd_net_pnl DESC)` | Per team / MTD | ordinal (1–3) | Daily | — | Both | Ties broken by team Sharpe ratio |
| KPI-TM-071 | Team Rank within Company | Ordinal rank of a team relative to all 6 teams in the company, based on MTD net PnL | `RANK() OVER (ORDER BY team_mtd_net_pnl DESC)` (all 6 teams) | Per team / MTD | ordinal (1–6) | Daily | — | PowerBI | — |
| KPI-TM-072 | Contribution to Floor PnL | Team's net PnL as a proportion of the floor's total net PnL in the period | `team_net_pnl / floor_net_pnl * 100` | Per team / period | % | Daily | — | PowerBI | Can be negative if the team lost money while the floor was profitable overall |
| KPI-TM-073 | Intra-Team PnL Variance | Standard deviation of individual traders' period net PnL within a team — measures consistency across team members | `STDEV(trader_net_pnl_for_period) GROUP BY team_id` | Per team / period | EUR | Daily | — | Both | High variance indicates one or two traders dominating outcomes; flag when STDEV > 30 % of team average |
| KPI-FL-070 | Floor Rank | Ordinal rank of each floor relative to the other, based on MTD return on capital | `RANK() OVER (ORDER BY floor_roc_mtd DESC)` | Per floor / MTD | ordinal (1–2) | Daily | — | Both | Only two floors exist; rank is 1 or 2 |
| KPI-FL-071 | Floor Contribution to Company PnL | Floor net PnL as a percentage of company net PnL in the period | `floor_net_pnl / company_net_pnl * 100` | Per floor / period | % | Daily | — | PowerBI | Sum of both floors equals 100 % when both are profitable |
| KPI-FL-072 | Floor 30-Day Rolling Net PnL | Cumulative net PnL for a floor over the trailing 30 calendar days | `SUM(net_pnl_eur WHERE floor_id = F AND trade_date BETWEEN TODAY-30 AND TODAY)` | Per floor / rolling 30 days | EUR | Daily | — | Both | Used in the floor vs. floor comparison requested in UC-01 |

---

### 4.8 Leadership & Coverage KPIs

| KPI ID | Name | Description | Formula | Granularity | Unit | Refresh | Target / Threshold | Consumer | Notes |
|--------|------|-------------|---------|-------------|------|---------|-------------------|----------|-------|
| KPI-LR-001 | Team-Lead PnL Multiplier | Ratio of the team's average individual trader net PnL (excluding the team lead) to the floor's average individual trader net PnL; values > 1.0 indicate the team lead is running an above-floor-average team | `AVG(trader_net_pnl WHERE team_id = T AND role = 'Trader') / AVG(trader_net_pnl WHERE floor_id = F AND role = 'Trader')` | Per team lead / period | ratio | Monthly | ≥ 1.0 | Both | Excludes the team lead's own trades from the numerator to measure team coaching effectiveness separately from personal trading |
| KPI-LR-002 | Floor Manager Coverage Index | Proportion of the floor's teams that have a non-null team lead actively trading (i.e., team lead has ≥ 1 trade in the last 5 trading days) | `COUNT(teams WHERE team_lead_active = TRUE) / COUNT(teams WHERE floor_id = F) * 100` | Per floor manager / rolling 5 days | % | Daily | 100 % (all teams covered) | PowerBI | Detects periods where a team lead position is vacant or inactive |
| KPI-LR-003 | Intra-Team Variance Rank | Rank of each team by intra-team PnL variance (KPI-TM-073) within the floor; lower variance = more consistent team = higher rank | `RANK() OVER (PARTITION BY floor_id ORDER BY team_pnl_stdev ASC)` | Per team / period | ordinal (1–3) | Monthly | — | PowerBI | Lower variance ranked first; high-variance teams flagged for team-lead coaching review |
| KPI-LR-004 | Team Lead Personal vs Team Average | Team lead's own net PnL in the period relative to their team's average trader net PnL — shows whether the lead is outperforming their own team | `team_lead_net_pnl / AVG(trader_net_pnl for all team members including lead)` | Per team lead / period | ratio | Monthly | ≥ 1.0 (lead should be above team average) | Both | Ratio < 0.8 may indicate the team lead role is reducing individual trading performance |

---

## 5. Dimensions & Slicers

Every PowerBI page and every AI-assistant query must support filtering and grouping by the dimensions listed below. This section defines the expected cardinality, hierarchy, and default slicer selection for each dimension.

### 5.1 Time Dimension

| Attribute | Details |
|-----------|---------|
| **Source** | Derived from `trade_date` (canonical date column in all views) |
| **Hierarchy** | Year → Quarter → Month → ISO Week → Day |
| **Cardinality** | ~250 trading days per year; expected dataset spans at least 1 year (approx. 250 distinct dates) |
| **Default selection** | Current month (MTD) for all summary cards; last 30 trading days for trending charts |
| **Special slicers** | MTD (Month-to-Date), YTD (Year-to-Date), Rolling 5D, Rolling 30D, Rolling 90D |
| **Format** | `dd.MM.yyyy` (ro-RO locale) on axis labels; `MM.yyyy` for monthly aggregations |
| **Notes** | All date arithmetic uses `trade_date_ro` (the Europe/Bucharest local date). No UTC-local confusion in the PowerBI model — the view always exposes local dates. |

### 5.2 Employee Dimension

| Attribute | Details |
|-----------|---------|
| **Source** | `dim_Employees` |
| **Hierarchy** | Company → Floor → Team → Employee |
| **Cardinality** | 32 rows (fixed organizational size) |
| **Attributes exposed** | `employee_id`, `first_name`, `last_name` (use `trader_full_name` derived in `v_trades_enriched` for display purposes), `email`, `role` (Trader / Team Lead / Floor Manager), `hire_date`, `team_id`, `floor_id` |
| **Default selection** | "All employees" for managers; row-level security (RLS) restricts traders to their own `employee_id` |
| **Notes** | Faker `ro_RO` locale generates Romanian names and `@tcp-capital.ro` email addresses. Names are PII — no real names, purely synthetic. |

### 5.3 Team Dimension

| Attribute | Details |
|-----------|---------|
| **Source** | `dim_Teams` |
| **Hierarchy** | Floor → Team |
| **Cardinality** | 6 rows |
| **Attributes exposed** | `team_id`, `team_name`, `floor_id` |
| **Default selection** | All teams; filtered by floor for floor-manager views |
| **Notes** | 3 teams per floor. Team names are generated and consistent throughout the dataset lifetime. The team lead is resolved via `dim_Employees WHERE team_id = X AND role = 'team_lead'`; there is no `team_lead_employee_id` column on `dim_Teams`. |

### 5.4 Trading Floor Dimension

| Attribute | Details |
|-----------|---------|
| **Source** | `dim_TradingFloors` |
| **Cardinality** | 2 rows: București (primary), Cluj-Napoca (secondary) |
| **Attributes exposed** | `floor_id`, `city`, `floor_code`, `is_primary_hq` |
| **Default selection** | Both floors for company-level views; filtered to own floor for floor managers |
| **Notes** | `city` is the human-readable floor label (e.g., "București", "Cluj-Napoca"). `is_primary_hq` (BIT) identifies the primary HQ floor; only one row has `is_primary_hq = 1`. |

### 5.5 Market / Instrument Dimension

| Attribute | Details |
|-----------|---------|
| **Source** | `dim_Markets` |
| **Hierarchy** | Asset class → Instrument group → Instrument |
| **Cardinality** | Estimated 20–50 synthetic instruments |
| **Attributes exposed** | `market_id`, `symbol`, `display_name`, `asset_class`, `quote_currency` |
| **Default selection** | All instruments |
| **Notes** | Instrument quote currencies vary across the seed (USD/JPY/CHF/GBP/EUR). The v1.0 dashboards expose EUR-only KPIs (`gross_pnl_eur`, `net_pnl_eur`). Non-EUR instruments are traded in their native quote currency; the synthetic generator converts realised PnL to EUR using a deterministic per-date FX-rate table (`tcp/synth/fx_rates.py`) and persists `gross_pnl_eur` / `net_pnl_eur`. Multi-currency drill-downs and exposure dashboards are out of scope for v1.0 (see §10 item 4). All synthetic trades use `lot size = 1`; FX 100 000-unit standard-lot scaling is deferred to v2.0 (see §10 item 12). |

### 5.6 Order Type Dimension

| Attribute | Details |
|-----------|---------|
| **Source** | `dim_OrderType` |
| **Cardinality** | 4 types (Market, Limit, Stop, Stop-Limit) |
| **Attributes exposed** | `order_type_id`, `order_type_code`, `display_name`, `is_directional` |
| **Default selection** | All order types |
| **Notes** | `display_name` is the human-readable label (e.g., "Market", "Stop-limit"). `is_directional BIT NOT NULL DEFAULT 1` — all four v1.0 order types are directional (take a buy or sell side); reserved for future non-directional instruments such as straddles. Used for behavioral analysis; order type will be relevant to slippage estimation if KPI-TR-063 is promoted to v2.0. |

### 5.7 Session / Shift Dimension

| Attribute | Details |
|-----------|---------|
| **Source** | `dim_Sessions` |
| **Cardinality** | 3 sessions: Pre-Market, Regular, After-Hours |
| **Attributes exposed** | `session_id`, `session_code`, `display_name`, `start_time_local`, `end_time_local` |
| **Default selection** | All sessions |
| **Notes** | `display_name` is the human-readable label. `start_time_local` and `end_time_local` are expressed in Europe/Bucharest local time. Sessions are derived from the hour of `time_entry` in Europe/Bucharest local time. Used in the trades-per-hour heatmap (KPI-TR-051). |

### 5.8 Role Dimension (Slicer)

| Attribute | Details |
|-----------|---------|
| **Source** | `role` column in `dim_Employees` |
| **Cardinality** | 3 values: Trader, Team Lead, Floor Manager |
| **Default selection** | All roles (except where RLS narrows automatically) |
| **Notes** | Used to separate team-lead personal trading performance from their supervisory role. Relevant for KPI-LR-001 and KPI-LR-004. |

---

## 6. Use Cases

The following user stories define the primary interaction scenarios with TCP — Trading Central Panel. Each story covers either the PowerBI dashboard experience or the AI-assistant interface (or both).

**UC-01 — Floor-Level PnL Comparison (PowerBI)**  
As a **floor manager**, I want to see my floor's net PnL ranked against the other floor for the last 30 trading days, displayed as a side-by-side comparison card with a sparkline trend, so that I can justify or challenge capital-reallocation decisions in monthly leadership meetings.

**UC-02 — Team Drill-Down (PowerBI)**  
As a **floor manager**, I want to click on a team's aggregate PnL bar and drill through to see each team member's individual contribution to that period's result, so that I can identify which specific trader is pulling the team's rank down and engage the team lead directly.

**UC-03 — Individual Trader Drill-Down from Team View (PowerBI)**  
As a **team lead**, I want to start from my team's aggregate dashboard, identify the trader with the lowest win rate this month, click through to their individual "Trader Detail" page, and see their full KPI card including drawdown, Sharpe ratio, and day-of-week PnL pattern, so that I can prepare a coaching session with specific data points.

**UC-04 — Personal MTD Sharpe Ratio (PowerBI + AI Assistant)**  
As a **trader**, I want to see my Sharpe ratio for the current month-to-date period displayed prominently on my personal dashboard, and also be able to ask the AI assistant "What is my Sharpe ratio for this month?" and receive the same value cited to the underlying `v_employee_performance` view, so that I can self-assess my risk-adjusted performance without requiring a manager.

**UC-05 — Top Earner Query (AI Assistant)**  
As **any authenticated user**, I want to ask "Who was the top earner last week on the Cluj-Napoca floor?" and receive a response that names the trader, states their net PnL in EUR formatted as `12.345,67 €`, specifies the ISO week, and cites the `v_floor_performance` or `v_employee_performance` view it used, so that I can get an instant answer without waiting for a scheduled report.

**UC-06 — Underperformance Alert Diagnostic (AI Assistant)**  
As a **team lead**, I want to ask "Which of my traders had more than 5 consecutive losses in the last 30 days?" and receive a ranked list with the loss streak count and the date range of each streak, so that I can prioritize one-on-one coaching sessions this week.

**UC-07 — Capital Utilization Check (PowerBI)**  
As a **floor manager**, I want to view a heatmap of capital utilization ratios (KPI-TR-022) for all traders on my floor, with red highlighting for traders below 30 % or above 200 % utilization, so that I can quickly identify who is either sitting on idle capital or over-leveraged.

**UC-08 — Day-of-Week Behavioral Insight (AI Assistant)**  
As a **trader**, I want to ask "On which day of the week do I lose the most money on average?" and receive an answer broken down by weekday with EUR figures formatted in Romanian locale, so that I can adjust my trading schedule to reduce systematic day-of-week losses.

**UC-09 — Monthly Team Rank History (PowerBI)**  
As a **team lead**, I want to see a bar chart showing my team's rank within the floor for each of the past 6 calendar months, so that I can demonstrate a trend of improvement to the floor manager in quarterly reviews.

**UC-10 — Intra-Team Consistency Review (PowerBI + AI Assistant)**  
As a **team lead**, I want to see my team's intra-team PnL variance (KPI-TM-073) compared against both other teams on my floor, and also ask the AI assistant "Is my team's variance higher or lower than the floor average?" to confirm the figure narratively, so that I can determine whether my team's results are being driven by one star trader or by consistent collective performance.

**UC-11 — Annual YTD Company Summary (PowerBI)**  
As a **floor manager** acting as a company-level viewer, I want to see the Company Overview page showing company YTD net PnL, company ROC, company Sharpe ratio, and the floor-vs-floor contribution split in a single executive summary view, so that I can present a one-page performance snapshot to company leadership at any time.

**UC-12 — Overnight Position Frequency Alert (AI Assistant)**  
As a **team lead**, I want to ask "Which traders on my team held positions overnight more than 10 % of their trades last month?" and receive a list with names and overnight frequency percentages, so that I can enforce the firm's intraday-trading guideline.

**UC-13 — Personal Equity Curve (PowerBI)**  
As a **trader**, I want to see a line chart of my cumulative net PnL (KPI-TR-018) from the first trading day of the current year to today, with a reference line showing a flat 1 % monthly ROC target, so that I can visualize whether my actual equity curve is tracking, exceeding, or lagging the target growth trajectory.

**UC-14 — Worst Performing Instrument Query (AI Assistant)**  
As a **trader**, I want to ask "Which instrument lost me the most money this quarter?" and receive the instrument name, total net PnL loss in EUR, and count of trades on that instrument, so that I can reconsider my approach to that specific market.

**UC-15 — Floor Manager Morning Briefing (PowerBI)**  
As a **floor manager**, I want to open the "Floor Performance" PowerBI page at 08:00 and see, already refreshed from the 07:30 scheduled refresh, the previous day's floor PnL, each team's daily contribution, and any traders who breached drawdown or consecutive-loss thresholds, so that I can start the trading day with a complete situational picture in under 3 minutes.

---

## 7. AI-Assistant Scope & Guardrails

### 7.1 In-Scope Questions

The AI assistant powered by Anthropic Claude (`claude-haiku-4-5` with prompt caching on schema context) is designed exclusively for **descriptive analytics over the TCP trading dataset**. The assistant translates natural-language questions into read-only parameterized SQL queries against the Azure SQL Database views and returns factual answers grounded in the data.

**In-scope categories**:

| Category | Example Questions |
|----------|------------------|
| KPI retrieval | "What is my win rate for May 2026?", "Show me the top 3 traders by Sharpe ratio this quarter" |
| Ranking and comparison | "Who was the top earner last week on the Cluj-Napoca floor?", "Which team has the best profit factor YTD?" |
| Aggregation | "What is the total net PnL for the București floor this month?", "How many trades did Team Alpha execute last week?" |
| Behavioral patterns | "On which day of the week does Trader X lose the most?", "Which hour of day has the highest PnL across the firm?" |
| Threshold checks | "Which traders breached 8 % max drawdown this month?", "Who had more than 5 consecutive losses this week?" |
| Trend narration | "Has the company's Sharpe ratio improved over the last 3 months?", "Is Team Beta's win rate trending up or down?" |

**Example in-scope Q&A**:

> **User**: "Who was the top earner last week on the Cluj-Napoca floor?"  
> **Assistant**: "Last week (ISO week 20, 12.05.2026 – 16.05.2026), the top earner on the Cluj-Napoca floor was [Trader Name] with a net PnL of 3.240,15 € across 27 trades. Source: `v_employee_performance`, filtered by `floor_id = 2` and `trade_date` between 12.05.2026 and 16.05.2026."

### 7.2 Out-of-Scope Questions

The assistant must refuse or redirect the following categories:

| Category | Reason for Exclusion | Redirect |
|----------|---------------------|---------|
| Trade recommendations | Regulatory risk; not a licensed trading advisor; system contains only synthetic data | "I can describe past performance but cannot recommend trading actions." |
| Price predictions | No market data feeds; would require real-time data and ML model serving not part of this architecture | "Predictive modeling is outside the scope of this system." |
| External market data | System contains only internally generated synthetic trades; no real broker or market data feeds are connected | "I can only answer questions about trades recorded in the TCP dataset." |
| Personal data outside the schema | GDPR-adjacent principle; the schema holds only work-related trading data, not personal financial data | "I only have access to work-related trading activity data." |
| Schema modification or DML | The assistant is read-only; no INSERT, UPDATE, DELETE, or DDL is ever generated | "I cannot modify data. Please contact the system administrator." |
| Identity or authentication queries | The assistant does not have access to AAD user records beyond the authenticated session user's `employee_id` | "Authentication and identity management is handled by Azure Active Directory." |

**Example out-of-scope Q&A**:

> **User**: "Should I buy EUR/USD tomorrow based on my recent performance?"  
> **Assistant**: "Trade recommendations are outside the scope of this system. I can only describe your historical trading performance as recorded in the TCP dataset. For example, I can tell you your average PnL on EUR/USD trades or your win rate on that instrument."

### 7.3 Safety Rails

| Guardrail | Implementation |
|-----------|---------------|
| **Read-only SQL** | The Azure Functions runtime uses a single Managed Identity that holds two narrowly scoped database roles. The AI-assistant code path connects under the `tcp_ai_assistant` role, which has SELECT-only grants on `v_*` views and supporting dimension tables — no direct `fact_Trades` access. The daily-generator code path connects under the `tcp_generator` role (INSERT/UPDATE on `fact_Trades` only). `safe_query.py` enforces that user-driven queries never invoke generator privileges. |
| **Parameterized queries** | All SQL generated by the assistant uses parameterized placeholders. No string concatenation of user input into SQL text. Prevents SQL injection. |
| **Row-level filtering by role** | The HTTP trigger receives the authenticated user's `employee_id` and `role` from the AAD token claim. Traders can only receive answers scoped to their own `employee_id`. Team leads receive their team. Floor managers receive their floor. |
| **Schema-only prompt-cached context** | The prompt cache holds only the schema description (view definitions, column names, data types, and sample value ranges). No actual trade data is embedded in the prompt. This limits the attack surface and keeps prompt tokens minimal. |
| **No PII in prompt context** | Employee names and emails are stored in the database but are returned only in query results, never embedded in the cached prompt context. |
| **Response citation** | Every numerical answer must cite the source view and the filter conditions applied, enabling the user to verify the answer independently in PowerBI. |

---

## 8. Acceptance Criteria

The following criteria are testable and will be verified during the Etapa-10 final integration review. Each criterion references the specific KPIs, pages, or endpoints it validates.

### 8.1 PowerBI Dashboard Acceptance Criteria

| AC ID | Criterion | KPIs Validated |
|-------|-----------|---------------|
| AC-PBI-01 | The "Company Overview" PowerBI page renders KPI-CO-010, KPI-CO-011, KPI-CO-020, KPI-FL-070, and KPI-FL-071 as summary cards with correct `ro-RO` EUR formatting (`12.345,67 €`) and date formatting (`dd.MM.yyyy`) | KPI-CO-010, KPI-CO-011, KPI-CO-020, KPI-FL-070, KPI-FL-071 |
| AC-PBI-02 | The "Floor Performance" PowerBI page renders floor-level KPIs with a last-30-trading-day comparison slicer defaulting to the rolling 30-day window | KPI-FL-010 through KPI-FL-072 |
| AC-PBI-03 | The "Team Performance" PowerBI page renders team-level KPIs (KPI-TM-010 through KPI-TM-073) with a floor slicer and a team rank visual showing ordinal positions 1–3 per floor | KPI-TM-010 through KPI-TM-073 |
| AC-PBI-04 | The "Trader Detail" PowerBI page renders trader-level KPIs (KPI-TR-010 through KPI-TR-063) and enforces row-level security so that a trader logging in with their own AAD account sees only their own data | KPI-TR-010 through KPI-TR-063 |
| AC-PBI-05 | The PowerBI scheduled refresh at 07:30 Europe/Bucharest completes successfully on at least 90 % of weekdays (measured over a 20-day test period), with page load time ≤ 3 seconds on a standard browser over a 10 Mbps connection | Non-functional (NFR-PERF-01) |
| AC-PBI-06 | Drill-through from "Team Performance" to "Trader Detail" works correctly for all 6 teams, filtering the trader detail page to only the selected team's members | UC-03 |
| AC-PBI-07 | The trades-per-hour heatmap (KPI-TR-051) renders with 24 hour-buckets and correct Europe/Bucharest local-time labels on the "Trader Detail" page | KPI-TR-051 |
| AC-PBI-08 | All PowerBI visuals pass WCAG AA color contrast ratio (≥ 4.5:1) for text and ≥ 3:1 for non-text visual indicators, validated with an automated contrast checker | NFR-ACC-01 |

### 8.2 AI-Assistant Acceptance Criteria

| AC ID | Criterion | Test Reference |
|-------|-----------|---------------|
| AC-AI-01 | The `POST /api/ask` endpoint returns a valid JSON response within 4 seconds (p95) for all 10 canonical test questions under load testing | NFR-PERF-02 |
| AC-AI-02 | The AI assistant correctly answers 8 out of 10 canonical test questions in the integration test suite, with answers matching the PowerBI-displayed values within a 0.01 EUR tolerance | Canonical test suite (Etapa 10) |
| AC-AI-03 | The assistant correctly refuses to answer out-of-scope questions (trade recommendations, price predictions, schema modification requests) and provides an appropriate redirect message | Section 7.2 |
| AC-AI-04 | All assistant responses cite the source view name and the filter conditions applied (e.g., "Source: `v_employee_performance`, `floor_id = 2`, `trade_date` between …") | Section 7.3 |
| AC-AI-05 | The assistant does not return data for a trader when queried by a different trader using their own AAD identity; row-level filtering is enforced at the HTTP trigger level | Section 7.3 |
| AC-AI-06 | EUR amounts in assistant responses are formatted as `12.345,67 €` (ro-RO locale) in all user-facing narrative output | NFR-LOC-01 |
| AC-AI-07 | Prompt cache hit rate for the schema-context portion of the prompt is ≥ 80 % after the first 10 requests in a session, as measured in Application Insights logs | NFR-PERF-03 |
| AC-AI-08 | For a query against an MTD Sharpe ratio when `n_days < 5`, the assistant returns the literal phrase `'pending — need ≥ 5 trading days'` rather than returning empty or NULL | KPI-TR-033, KPI-TR-034 |

### 8.3 Data Pipeline Acceptance Criteria

| AC ID | Criterion |
|-------|-----------|
| AC-DATA-01 | The daily generator Azure Function (Timer Trigger at 07:00 RO weekdays) successfully inserts synthetic trades for all 30 trading employees on each trigger, with Trade UIDs in the correct format `T<YYYYMMDD>-<NNNN>` |
| AC-DATA-02 | The Monday trigger correctly generates trades with `trade_date` = previous Friday (not the current Monday), verified for at least 4 consecutive weeks |
| AC-DATA-03 | All `time_entry` and `time_exit` values in `fact_Trades` are stored as `DATETIMEOFFSET(3)` with the correct Europe/Bucharest offset (+02:00 or +03:00 depending on DST) |
| AC-DATA-04 | All database table names conform to the `^(fact|dim|config)_[A-Z][a-zA-Z0-9]*$` naming convention, enforced by the CI gate against `INFORMATION_SCHEMA.TABLES` |
| AC-DATA-05 | `config_Capital` holds at least the 80 000 EUR global baseline row with a valid `effective_from` date and `trader_id IS NULL` |

---

## 9. Non-Functional Requirements

### 9.1 Performance

| NFR ID | Requirement | Measurement Method |
|--------|-------------|-------------------|
| NFR-PERF-01 | PowerBI dashboard pages load in ≤ 3 seconds from a cold start (post-refresh) on a standard consumer browser over a ≥ 10 Mbps connection | Manual timing test during Etapa 10 review; PowerBI Performance Analyzer |
| NFR-PERF-02 | AI-assistant `POST /api/ask` endpoint response time has two service tiers. **Warm-path (SQL active)**: p95 ≤ 1.5 s end-to-end (Function App cold-start ≤ 0.5 s + query ≤ 200 ms + Anthropic ≤ 1 s warm). **Cold-path (SQL paused, no warmup)**: p95 ≤ 35 s; mitigated to a warm path 99 % of the time by the `WarmupTrigger` at 06:55 RO and the SWA "Wake up" button calling `/api/ping`. See `03_architecture.md` §14 for the full latency decomposition. | Azure Application Insights — response time percentile charts |
| NFR-PERF-03 | Anthropic Claude prompt cache hit rate ≥ 80 % for the schema-context cache block after warm-up (first 10 requests) | Application Insights custom metric logged by the HTTP trigger |
| NFR-PERF-04 | The PowerBI dataset uses **Import mode** with Scheduled Refresh at 07:30 Europe/Bucharest. DirectQuery is explicitly rejected to preserve the Azure SQL Free Offer's vCore-second budget (DirectQuery would issue per-visual SQL queries against the auto-paused database on every report render, risking exhaustion of the 100 000 vCore-second monthly allowance). Import queries during the scheduled refresh must complete in ≤ 2 seconds for any date range ≤ 1 year. Trading scope: 30 trading individuals (24 traders + 6 team leads who also trade) × approximately 7–8 trades per active day × 250 trading days ≈ 52 500–60 000 rows in `fact_Trades` per year; the synthetic generator calibrates the per-day distribution accordingly. | Query execution plan review; Azure SQL Query Performance Insight; PowerBI Performance Analyzer |

### 9.2 Availability

| NFR ID | Requirement | Honest Limitation |
|--------|-------------|------------------|
| NFR-AVAIL-01 | Azure SQL Database Serverless auto-pauses after 60 minutes of inactivity. Cold start (resume from pause) adds approximately 20–60 seconds of latency on the first connection after the pause period. This is accepted behavior on the free tier and is documented in the platform's operational notes. | Azure SQL Serverless free tier limitation; no SLA commitment for zero-cost deployment |
| NFR-AVAIL-02 | Azure Functions Consumption plan may experience cold start latency of 1–5 seconds after periods of inactivity. The HTTP trigger implementing `POST /api/ask` is expected to warm up within 2 invocations. | Azure Functions Consumption plan limitation; acceptable for this use case |
| NFR-AVAIL-03 | Azure Static Web Apps Free plan provides best-effort availability with no published SLA (unlike the Standard plan). The expected uptime for thesis demonstration purposes is ≥ 99 % during active demonstration periods. | Free plan limitation; acceptable for academic use case |
| NFR-AVAIL-04 | PowerBI scheduled refresh runs at 07:30 Europe/Bucharest. If the refresh fails (e.g., due to SQL Database cold start), the previous day's data remains visible. A failed refresh is logged and visible in PowerBI Service refresh history. | PowerBI Service dependency; refresh failure does not cause data loss |

### 9.3 Localization

| NFR ID | Requirement |
|--------|-------------|
| NFR-LOC-01 | All EUR amounts displayed in PowerBI visuals and AI-assistant responses use `ro-RO` locale formatting: decimal separator `,`, thousands separator `.`, currency symbol `€` appended (e.g., `12.345,67 €`) |
| NFR-LOC-02 | All dates displayed in PowerBI visuals use `dd.MM.yyyy` format (e.g., `15.05.2026`) |
| NFR-LOC-03 | PowerBI dataset locale is set to `ro-RO` at the model level, applying consistent number and date formatting automatically across all visuals |
| NFR-LOC-04 | All timestamps in the database are stored with the Europe/Bucharest offset (`+02:00` in EET, `+03:00` in EEST); all view-level date columns use `AT TIME ZONE 'E. Europe Standard Time'` conversion |
| NFR-LOC-05 | Day-of-week labels in behavioral charts (KPI-TR-056, KPI-TR-051) are rendered in English (Monday through Friday) for consistency with the English-language PowerBI report |

### 9.4 Accessibility

| NFR ID | Requirement |
|--------|-------------|
| NFR-ACC-01 | All PowerBI visuals use color palettes that meet WCAG 2.1 AA minimum contrast ratios: ≥ 4.5:1 for normal text, ≥ 3:1 for large text and non-text visual elements |
| NFR-ACC-02 | Conditional formatting colors used for threshold alerts (e.g., red for drawdown breach, green for above-target win rate) are accompanied by text labels or icon indicators, not color alone, to support color-blind users |
| NFR-ACC-03 | All PowerBI reports include alt-text descriptions on all chart visuals using the PowerBI Accessibility settings panel |

### 9.5 Security

| NFR ID | Requirement |
|--------|-------------|
| NFR-SEC-01 | All user authentication is handled by Azure Active Directory (AAD/Entra ID) via the Azure Static Web Apps built-in authentication provider. No custom authentication code. |
| NFR-SEC-02 | Role-based access control (RBAC) enforces three permission tiers: Trader (own data only), Team Lead (own team), Floor Manager (own floor). RBAC is implemented in the Azure Functions HTTP trigger by reading AAD token claims. |
| NFR-SEC-03 | All secrets (Anthropic API key, Azure SQL connection string, Application Insights connection string) are stored in Azure Key Vault. The Function App accesses Key Vault via Managed Identity (no static credentials in application settings). |
| NFR-SEC-04 | No secrets are stored in GitHub repository, Azure DevOps pipelines, or any committed file. CI runs `gitleaks` on every push to detect accidental secret commits. |
| NFR-SEC-05 | The Azure SQL Database principal used by the Function App Managed Identity operates under two narrowly scoped database roles. The AI-assistant code path uses the `tcp_ai_assistant` role (SELECT-only on `v_*` views and supporting dimension tables; no direct `fact_Trades` access). The daily-generator code path uses the `tcp_generator` role (INSERT/UPDATE on `fact_Trades` only). Both roles are held by the same Managed Identity; the application selects which to invoke per code path via connection scoping. `safe_query.py` enforces that user-driven queries never invoke generator privileges. |
| NFR-SEC-06 | All HTTPS connections are TLS 1.2 minimum. Azure Static Web Apps and Azure Functions enforce HTTPS by default. |
| NFR-SEC-07 | A credentials rotation plan is documented in `docs/security/credentials_rotation.md` and executed at the conclusion of the thesis build phase. |

### 9.6 Cost

| NFR ID | Requirement |
|--------|-------------|
| NFR-COST-01 | Total recurring monthly Azure cost: **EUR 0**. All Azure services operate on permanently free tier allocations (Azure SQL Database Free Offer, Functions Consumption Y1, Static Web Apps Free, Key Vault Standard with free secret operations up to 10 000/month). |
| NFR-COST-02 | Anthropic API cost is minimized through prompt caching on the schema-context cache block. Expected monthly API cost is within a minimal budget acceptable for academic use. |
| NFR-COST-03 | Log Analytics Workspace ingestion remains within the 5 GB/month free tier. Application Insights sampling is enabled at the Function App level if ingestion approaches the limit. |

---

## 10. Out of Scope for v1.0

The following features and capabilities are explicitly **not** part of the v1.0 release of TCP — Trading Central Panel. They are documented here to prevent scope creep and to provide a clear reference for future enhancement stages.

| # | Feature | Rationale for Exclusion |
|---|---------|------------------------|
| 1 | **Real broker connectivity** | This is a synthetic data platform for academic purposes. Connecting to a live broker API (Interactive Brokers, Alpaca, etc.) would introduce regulatory, legal, and technical complexity far beyond the thesis scope. |
| 2 | **Real-time tick data ingestion** | Tick-by-tick market data requires streaming infrastructure (Event Hubs, Stream Analytics) with non-trivial cost. The daily batch model is sufficient for the analytical goals. |
| 3 | **Options and derivatives analytics (Greeks)** | Delta, gamma, vega, theta calculations require options pricing models (Black-Scholes, etc.) and real-time market data. The synthetic trade model covers equities and FX only. |
| 4 | **Multi-currency P&L beyond EUR-denominated aggregates** | Dashboards expose EUR-only KPIs in v1.0. Non-EUR instruments are traded in their native quote currency (USD/JPY/CHF/GBP); the synthetic generator converts realised PnL to EUR using a deterministic per-date FX-rate table (`tcp/synth/fx_rates.py`). Multi-currency drill-downs and exposure dashboards are out of scope for v1.0. |
| 5 | **Mobile application** | The Static Web App provides a responsive web interface accessible on mobile browsers. A dedicated iOS/Android native application is not planned. |
| 6 | **Real-time dashboard streaming** | PowerBI real-time streaming datasets require push API integration and increase complexity. The 07:30 daily refresh cadence is sufficient for the daily trading analytics use case. |
| 7 | **Predictive / forward-looking analytics** | Machine learning models for churn prediction, price forecasting, or strategy recommendation are excluded. The AI assistant is limited to descriptive analytics over historical data. |
| 8 | **External market benchmark comparison** | Comparing trader PnL against market indices (e.g., BET, S&P 500) requires external data feeds. The system is self-contained with internal benchmarks only. |
| 9 | **Audit trail / compliance reporting** | Regulatory compliance reporting (MiFID II transaction reporting, ESMA requirements) is out of scope. The system is not a compliance tool. |
| 10 | **Collaboration features** | Chat, comments, annotations, or shared dashboards between users are not implemented. Users access their own views in isolation. |
| 11 | **Slippage estimation via `modeled_pnl_eur`** | KPI-TR-063 (Slippage Estimate) is deferred to v2.0. The v1.0 schema does not allocate a `modeled_pnl_eur` column in `fact_Trades`. See OQ-04 (resolved). |
| 12 | **Standard-lot FX semantics** | All synthetic trades use `lot size = 1`. FX 100 000-unit standard-lot scaling is deferred to v2.0. A `contract_size` column is not defined in the v1.0 `dim_Markets` schema. |

---

## 11. Glossary

> **Superseded by the canonical glossary in [`docs/glossary.md`](../glossary.md)** (Etapa 9). The table below is retained as the historical Etapa-1 design artefact; the consolidated glossary covers the same KPI terms plus the security, observability, and infrastructure vocabulary that emerged in later stages. New definitions land in `docs/glossary.md`, not here.

| Term | Definition |
|------|-----------|
| **PnL (Profit and Loss)** | The net financial result of a trading activity. Gross PnL is the raw difference between entry and exit trade value. Net PnL deducts commissions and fees from gross PnL. |
| **Drawdown** | The peak-to-trough decline in a cumulative PnL equity curve. Max drawdown measures the largest such decline over a given period. Expressed as an absolute EUR amount or as a percentage of capital baseline. |
| **Equity Curve** | A time-series line chart showing the cumulative net PnL of a trader, team, or floor over time. A rising equity curve indicates overall profitability. |
| **Sharpe Ratio** | A risk-adjusted performance metric: the ratio of mean return to the standard deviation of returns. In this system, RF = 0, so it is the mean daily net PnL divided by the standard deviation of daily net PnL. Higher is better; ≥ 1.0 is considered acceptable; ≥ 2.0 is excellent. |
| **Sortino Ratio** | A variant of the Sharpe ratio that penalizes only downside volatility (the standard deviation of negative daily returns only). Preferred when evaluating strategies that accept occasional large wins alongside controlled losses. RF = 0 in this system. |
| **Profit Factor** | The ratio of total gross profit (sum of all winning trades) to total gross loss (absolute sum of all losing trades). A profit factor > 1.0 means the strategy is net profitable; > 1.5 is good; > 2.0 is excellent. |
| **Win Rate** | The proportion of trades that close with a positive net PnL. A win rate of 55 % means 55 out of 100 trades are profitable. Win rate alone does not indicate profitability — it must be considered alongside average win vs. average loss size. |
| **Profit Day Rate** | The proportion of trading days on which a trader's daily net PnL is positive. |
| **Max Consecutive Losses** | The longest unbroken sequence of trades (ordered by entry time) where every trade has a negative net PnL. A key indicator of strategy robustness and psychological risk. |
| **Value at Risk (VaR)** | A statistical measure of the potential loss in value of a portfolio over a defined period at a given confidence level. In this system, historical VaR at 95 % confidence is the 5th percentile of daily net PnL over the period. |
| **Return on Capital (ROC)** | Net PnL divided by allocated capital baseline, expressed as a percentage. Normalizes PnL across traders with different capital allocations. |
| **Capital Utilization Ratio** | The ratio of the average notional value of open positions to the trader's capital baseline. A ratio of 1.0 means the trader is fully utilizing their capital; > 1.0 indicates leverage. |
| **Holding Time** | The duration between a trade's entry and exit, typically measured in minutes. Short holding times indicate intraday scalping; long holding times indicate swing or position trading. |
| **Slippage** | The difference between the expected execution price of a trade and the actual execution price. In this synthetic system, slippage is estimated by comparing actual PnL against a modeled PnL based on theoretical mid-price execution. |
| **Overnight Position** | A trade where the exit occurs on a different calendar date than the entry. Overnight positions carry additional gap risk (the risk that the price opens significantly differently the next day). |
| **Weekend Carry** | A trade that remains open over a Saturday or Sunday. Carries higher gap risk due to multi-day market closure. |
| **Intraday Trade** | A trade that is opened and closed within the same calendar trading day. |
| **Swing Trade** | A trade that is held open for more than one trading day (i.e., an overnight position). |
| **Intra-Team Variance** | The statistical dispersion (standard deviation) of individual trader PnL results within a team. Low variance indicates a consistent team; high variance suggests one trader is dominating outcomes. |
| **Team-Lead PnL Multiplier** | A ratio comparing the average net PnL of a team's traders (excluding the team lead) to the average net PnL of all traders on the same floor. Values > 1.0 indicate the team lead's team outperforms the floor average. |
| **MTD (Month-to-Date)** | The period from the first calendar day of the current month through today. |
| **YTD (Year-to-Date)** | The period from the first calendar day of the current year through today. |
| **ISO Week** | The week numbering standard defined by ISO 8601, where weeks start on Monday and the first week of the year is the week containing the first Thursday of January. |
| **DATETIMEOFFSET** | A SQL Server data type that stores a date and time with timezone offset information, enabling timezone-aware calculations. Used for all timestamps in `fact_Trades`. |
| **Trade UID** | A unique identifier for a trade in the format `T<YYYYMMDD>-<NNNN>` (e.g., `T20260514-0001`). The date component uses the Europe/Bucharest local date of the trade. |
| **Prompt Caching** | A feature of the Anthropic Claude API that caches a designated portion of the prompt (the schema context) across API calls, reducing token processing cost and latency on repeated queries with the same context. |
| **RLS (Row-Level Security)** | A data access control mechanism that restricts which rows of data a user can see, based on their identity or role. Implemented in the Azure Functions HTTP trigger for the AI assistant, and via PowerBI RLS for dashboard users. |
| **Star Schema** | A database modeling pattern where a central fact table (containing events and numeric measures) is surrounded by dimension tables (containing descriptive attributes). Used throughout the TCP database design (`fact_Trades` as the central fact table). |
| **TMDL (Tabular Model Definition Language)** | A human-readable, Git-friendly format for defining Power BI / Analysis Services tabular models. Used to deploy the PowerBI dataset programmatically via the XMLA endpoint. Dashboard deployment uses TMDL — see `docs/decisions/ADR-001-powerbi-deployment.md`. |
| **azd (Azure Developer CLI)** | An open-source command-line tool from Microsoft that provides developer-friendly commands for building, deploying, and managing Azure applications following the AZD convention. Used for IaC deployment in this project. |
| **Bicep** | An Azure domain-specific language (DSL) for declarative infrastructure provisioning. Used in this project to define all Azure resources as code in the `infra/` directory. |
| **NCRONTAB** | The cron expression format used by Azure Functions Timer Triggers. The expression `0 0 7 * * 1-5` means "at 07:00:00 on Monday through Friday". |
| **cron** | A time-based job scheduling mechanism. In this project, "cron" refers to the Azure Functions Timer Trigger that runs the daily synthetic data generator at 07:00 Europe/Bucharest on weekdays. |
| **DACPAC** | Data-Tier Application Package — a self-contained SQL Server deployment artifact that captures the schema of a database. Used for offline schema distribution and comparison. |
| **OIDC** | OpenID Connect — an identity layer built on top of OAuth 2.0. Used in this project for GitHub Actions to authenticate to Azure without storing static secrets (federated credential flow). |
| **PITR** | Point-In-Time Restore — Azure SQL Database's automated backup capability that allows restoring the database to any point within the retention window. Relevant to the DR strategy in this project. |
| **RPO** | Recovery Point Objective — the maximum acceptable amount of data loss measured in time. Defines how recent the backup must be when restoring after a failure. |
| **RTO** | Recovery Time Objective — the maximum acceptable time to restore service after a failure. |
| **KQL** | Kusto Query Language — the query language used in Azure Monitor / Log Analytics and Application Insights to query telemetry and log data. |
| **SCD1** | Slowly Changing Dimension Type 1 — a data warehouse strategy where a dimension row is overwritten in place when an attribute changes. No history is preserved. Used for most dimensions in this project. |
| **SCD2** | Slowly Changing Dimension Type 2 — a data warehouse strategy where attribute changes are tracked by inserting a new row with version or effective-date columns, preserving full history. Not used in v1.0. |
| **RBAC** | Role-Based Access Control — a security model that grants permissions to users or principals based on their assigned roles rather than individual identity. Used for Azure resource permissions and Azure SQL database roles in this project. |
| **MI (Managed Identity)** | A type of Azure Active Directory identity automatically managed by Azure for Azure services. Eliminates the need for developers to manage credentials. The Function App uses a system-assigned Managed Identity to access Azure SQL, Key Vault, and other services. |
| **SP (Service Principal)** | An Azure Active Directory application identity used for automated tooling (e.g., GitHub Actions CI/CD). Distinct from a Managed Identity — a Service Principal requires explicit secret or certificate management. |
| **TVP (Table-Valued Parameter)** | A SQL Server feature that allows passing a table-valued dataset as a parameter to stored procedures or functions. Used in `V002__synth_tvp.sql` for bulk trade insertion by the synthetic generator. |
| **DTU** | Database Transaction Unit — a blended measure of CPU, memory, and I/O resources in Azure SQL Database's DTU-based service tiers. This project uses the vCore-based Serverless model instead, not DTUs. |
| **vCore-second** | The unit of compute consumption in the Azure SQL Database Serverless tier. The Free Offer includes 100 000 vCore-seconds per month. One vCore running for one second consumes one vCore-second. |

---

## 12. Open Questions

The following questions are deferred to later stages. Each is tagged with the stage expected to resolve it.

| OQ ID | Question | Deferred To | Notes |
|-------|----------|------------|-------|
| OQ-01 | Should the per-trader Sortino ratio (KPI-TR-034) be exposed as a dedicated visual on the PowerBI "Trader Detail" page, or only available via the AI assistant? | Etapa 6 — PowerBI Design | Concern: Sortino requires sufficient negative-day history; showing NULL for traders with no losing days may confuse users |
| OQ-02 | What is the exact commission model for synthetic trades? | **Resolved** | Commission is set per-trade by the synthetic generator using asset-class policies defined in `tcp/synth/commissions.py`. The schema stores `commission_eur DEFAULT 0` and the generator populates it at trade creation. |
| OQ-03 | Should the intra-team variance KPI (KPI-TM-073) trigger an automated alert (e.g., an email or a flag in the PowerBI dashboard) when it exceeds a threshold? If so, what is the threshold and notification channel? | Etapa 7 — Alerting | Azure Functions could implement the alert; email via SendGrid or similar |
| OQ-04 | Does `fact_Trades` store a `modeled_pnl_eur` column alongside `gross_pnl_eur` for slippage estimation (KPI-TR-063)? | **Resolved** | Deferred to v2.0 per §10 item 11. The v1.0 schema does not allocate `modeled_pnl_eur`. KPI-TR-063 is marked as deferred in the §4.6 catalogue. |
| OQ-05 | What are the exact team names for the 6 teams? The synthetic generator creates them with Faker, but they should be deterministic and documented for consistency across data refreshes. | Etapa 2 — Database Design | Names must be seeded deterministically so that reports reference the same team names across sessions |
| OQ-06 | Should weekend-carry frequency (KPI-TR-053) be implemented as a zero-tolerance alert (since the synthetic model should never produce weekend trades) or as a data quality signal? | Etapa 3 — Data Generator | If the generator never produces weekend trades, this KPI is useful only as a data quality check; value as a business KPI is low |
| OQ-07 | What session/shift boundaries should `dim_Sessions` define? Proposed: Pre-Market (07:00–09:00 RO), Morning (09:00–12:00 RO), Afternoon (12:00–17:00 RO), After-Hours (17:00–21:00 RO). Are these appropriate for the synthetic trading context? | Etapa 2 — Database Design | Boundaries affect KPI-TR-051 and KPI-TR-055; should align with major European market session times |
| OQ-08 | The AI assistant's row-level filtering reads the authenticated user's `employee_id` from the AAD token claim. Which specific AAD token claim maps to `employee_id` in the TCP identity model? A custom claim or the `objectId`? | Etapa 5 — Authentication Integration | May require AAD app registration with custom claims mapping; consult Azure AAD documentation |
| OQ-09 | Should the AI assistant support multi-turn conversation (maintaining context across multiple messages in a session) or only single-turn Q&A? Multi-turn requires session state management in the Azure Function. | Etapa 5 — AI Assistant Design | Multi-turn increases system complexity; v1.0 target is single-turn only |
| OQ-10 | The capital baseline of 80 000 EUR is a global default. Should the synthetic data generator always use the global baseline for all traders (simplest case), or should it generate per-trader overrides in `config_Capital` to demonstrate the override mechanism in action? | Etapa 3 — Data Generator | Per-trader overrides would make the capital utilization and ROC KPIs more interesting to demo; recommend generating 5–10 % of traders with overrides |

---

*End of document — TCP Business Requirements & KPI Catalog v1.0*
