# Etapa 3 statistical review — pass 1

**Reviewer**: general-purpose (acting as statistical-analyst)
**Date**: 2026-05-15
**Verdict**: ACCEPT_WITH_CHANGES

## Summary

The win-rate calibration is mathematically inconsistent with the documented KPI targets:
with `_BUY_DRIFT = ±0.003` and `_RETURN_SIGMA = 0.012` the gross win rate is
`Φ(0.003 / 0.012) = Φ(0.25) ≈ 0.5987`, not the ~0.52 the source comment claims.
Worse, the spec (`01_business_requirements.md` §4 KPI-TR-060) actually requires
**win rate ≥ 55 %** and **profit factor ≥ 1.5**, so 0.52 was never the right
contract target. The generator's *current* 0.60 win rate happens to satisfy
KPI-TR-060 (≥ 0.55) and KPI-TR-036 (≥ 1.5), but combined with the per-trade
volatility it implies an unrealistic annualised Sharpe ≈ 10, and the assertion
`0.48 ≤ win_rate ≤ 0.56` in `test_win_rate_close_to_52_percent` will fail at the
expected ~0.60 value. The math, the comment, the spec, and the test
collectively contradict each other and must be reconciled in one coherent
calibration before merge.

## Critical (statistical bugs producing non-thesis-defensible data)

- [ ] **CR-01** | `tcp/synth/trades.py:51-54` and `tests/unit/test_synth_distributions.py:91-105` |
  **Win-rate calibration vs. test tolerance contradict each other.**
  Source comment says "~52 % win-rate per KPI-TR-031" (wrong KPI number — actually KPI-TR-060)
  and uses `_BUY_DRIFT = 0.003`, `_RETURN_SIGMA = 0.012`. Probability a buy wins =
  `P(ε > 0 | ε ~ N(+0.003, 0.012)) = Φ(0.003 / 0.012) = Φ(0.25) ≈ 0.5987`. By symmetry the
  sell side gives the same 0.5987, so the *gross* win rate ≈ **0.599**.
  After commission drag (negligible for equities/commodities, ~0.2 % for FX, ~0.8 % for crypto),
  the *net* win rate stays near 0.59. The test asserts `0.48 ≤ win_rate ≤ 0.56`, which is
  ~3σ away from 0.599 (σ ≈ √(0.6·0.4/6800) ≈ 0.0059 over 30 d × 30 traders × ~7.6 closed trades).
  **Statistical impact**: at p = 0.599 the binomial bound on the 30-day sample places the
  observed win rate at roughly **z ≈ (0.599 − 0.56)/0.0059 ≈ 6.6**; the test must fail.
  If the test does pass in CI it can only be because the actual sample size is much smaller
  than expected (n_closed × 30d is being short-circuited by a fixture issue — see CR-03)
  or the assertion was loosened after the fact. **Fix**: pick one coherent calibration —
  e.g. set `_BUY_DRIFT = +0.0006`, `_SELL_DRIFT = −0.0006` so `P(win) = Φ(0.05) ≈ 0.520`,
  then change the test bound to `[0.49, 0.55]` and update the comment to reference KPI-TR-060
  honestly (or document why 0.52 deliberately undershoots the 0.55 spec target).
  Alternatively, accept the 0.60 win rate and rewrite the test as `[0.57, 0.63]` — but then
  the Sharpe blow-up in CR-02 must be addressed.

- [ ] **CR-02** | `tcp/synth/trades.py:54, 292-304` |
  **Annualised Sharpe is ~10×, an order of magnitude above the spec target of ≥ 1.0.**
  Per-trade gross relative return is `N(±0.003, 0.012)` on a ~25 000 EUR FX notional
  (`rng.randint(1000, 50000)` × 1.0850 ≈ 27 600 EUR mid). Per-trade gross PnL
  μ ≈ 0.003 × 27 600 ≈ **82.8 EUR**, σ ≈ 0.012 × 27 600 ≈ **331 EUR**.
  At ~7.6 closed trades/trader/day, daily PnL μ ≈ 7.6 × 82.8 ≈ **629 EUR**, σ ≈ √7.6 × 331 ≈ **913 EUR**.
  Daily Sharpe ≈ 629 / 913 ≈ 0.69 → annualised = 0.69 × √252 ≈ **10.9**.
  The spec (KPI-TR-033 / KPI-TM-031 / KPI-FL-031) targets ≥ 1.0; 2.0 is "excellent".
  A synthetic dataset producing a Sharpe of 10 will look obviously fake in PowerBI
  and will undercut thesis credibility. **Statistical impact**: every Sharpe-driven
  comparison KPI (trader/team/floor) is saturated; cross-trader/team variance is
  pure noise on top of a 10× baseline; max-drawdown will be artificially small.
  **Fix**: drop the drift to ±0.0006 *and* drop σ to ~0.006-0.008 so daily Sharpe lands
  at 0.06-0.12 → annualised 1.0-2.0. The relationship is:
  `Sharpe_daily = (μ_side / σ) × √n_trades` (for symmetric μ across B/S), so
  `Sharpe_annual = (μ_side / σ) × √(n_trades × 252)`.
  For Sharpe_annual = 1.5 with n_trades = 7: `μ_side / σ = 1.5 / √1764 ≈ 0.0357`.
  Pick `σ = 0.008`, `μ_side = 0.000285` → Sharpe ≈ 1.5, win-rate ≈ Φ(0.0357) ≈ 0.514.
  This jointly satisfies the realism constraints but leaves win-rate *below* the
  0.55 spec target — see CR-03 for the resolution.

- [ ] **CR-03** | `docs/design/01_business_requirements.md:248` vs. `tcp/synth/trades.py:51` |
  **KPI targets and generator math are jointly infeasible without skewed loss distribution.**
  For a *symmetric* Gaussian shift (which is what the generator uses), win rate and
  profit factor are tied: `PF = p_w / (1 − p_w)`. At `p_w = 0.55` → PF = 1.222
  (spec says ≥ 1.5 — fails). At `p_w = 0.60` → PF = 1.5 (passes both). So the only
  way to satisfy both KPI-TR-036 (PF ≥ 1.5) and KPI-TR-060 (WR ≥ 0.55) with a
  symmetric model is to push win rate to ≥ 0.60 — exactly what the code does today,
  but then Sharpe explodes (CR-02). **Statistical impact**: the spec is internally
  over-constrained for any symmetric return distribution; it implicitly assumes
  win size > loss size. **Fix**: introduce asymmetric magnitudes, e.g. wins drawn
  from `|N(0, σ_w)|` with σ_w = 0.010 and losses from `|N(0, σ_l)|` with σ_l = 0.008,
  then independently set the win probability via a Bernoulli flip. This decouples
  win-rate, profit-factor and Sharpe and lets the calibration hit all three KPIs
  realistically (p_w = 0.55, PF = (0.55 × 0.010) / (0.45 × 0.008) ≈ 1.53,
  daily Sharpe ≈ (0.55 × 0.010 − 0.45 × 0.008) × N / σ_daily → tuneable to ~1-2 annual).

- [ ] **CR-04** | `tests/unit/test_synth_distributions.py:143, 220, 307` |
  **Tests reference attributes that do not exist on `TradeRow`.**
  - `r.holding_minutes` (line 143) — `TradeRow` has `time_entry` and `time_exit`,
    no derived `holding_minutes` field. Test raises `AttributeError`.
  - `r.quote_currency` (line 220) — `TradeRow` has `market_id`, not `quote_currency`;
    the quote currency lives on `MarketRow`. Test cannot run as written.
  - `r.employee_id` (line 307) — `TradeRow` exposes `trader_id`. Test fails on
    attribute access for every iteration.
  **Statistical impact**: three of the most important distribution tests
  (holding-time exponential check, EUR-only PnL algebra check, per-trader Poisson
  clamp check) cannot execute. The remaining tests do not exercise these contracts.
  **Fix**: either add `@computed_field` properties on `TradeRow` for
  `holding_minutes`, `quote_currency`, `employee_id`, or rewrite the tests against
  a join with the `MarketRow`/`TraderProfile` fixtures and the timestamp pair.

## Major

- [ ] **MA-01** | `tcp/synth/trades.py:51` |
  **Misleading comment + wrong KPI reference.** Source says "~52 % win-rate per KPI-TR-031".
  KPI-TR-031 is *Max Drawdown %*. The right reference is **KPI-TR-060** (Win Rate ≥ 55 %).
  Also the 0.52 target contradicts both the actual code and the spec. Either fix the
  numbers (CR-01) or fix the comment to truthfully describe the 0.60 outcome and cite
  KPI-TR-060.

- [ ] **MA-02** | `tcp/synth/trades.py:55` |
  **Entry-price σ = 0.02 (2 %) is too wide; `factor = max(factor, 0.5)` truncates the
  left tail and biases the mean upward.** `Normal(1.0, 0.02)` puts only ~5e-7 mass
  below 0.5, so the clip almost never fires — but a 2 % daily σ on the entry price
  means SPY at 520 USD draws prices in [490, 550] with ~68 % mass, which is roughly
  a 2-day realised vol on a low-vol index; for BTC at 95 000 the same 2 % gives a band
  of [93 100, 96 900] — implausibly tight relative to crypto's actual ~3-4 % daily vol.
  Consider a per-asset-class `σ_entry`: equity 0.005, FX 0.003, crypto 0.030, commodity 0.015.
  **Impact**: PowerBI charts will show identical-magnitude noise across asset classes,
  which is a credibility hit in the thesis demo.

- [ ] **MA-03** | `tcp/synth/trades.py:430` |
  **Uniform `market = rng.choice(active_markets)` ignores asset-class realism.**
  With 30 symbols (10 eq, 8 fx, 6 cr, 6 cm), the expected mix is 33/27/20/20 %.
  Real boutique trading firms heavily favour equities + FX. The thesis would be
  more defensible with weights ~ {equity: 0.50, fx: 0.30, crypto: 0.10, commodity: 0.10}.
  Implement via `_weighted_choice` with weights set on `MarketRow`. **Impact**: the FX
  weight materially affects commission drag (FX is the only `notional × rate` line
  with a non-trivial commission share); current weight overstates that drag.

- [ ] **MA-04** | `tcp/synth/trades.py:435` |
  **Open-trade probability check happens *after* the RNG has already drawn quantity
  and entry-price for the closing branch.** That's fine for determinism but means a
  +1 line change to add a "carry-over close on next business day" feature would silently
  desynchronise the seed chain. Add an ADR note that the RNG-call order is part of the
  contract.

- [ ] **MA-05** | `tcp/synth/trades.py:341-369` |
  **Sign convention is correct but undocumented.** Verified:
  - Buy at 100, exit 102, qty 50 → `gross_quote = (102 − 100) × 50 × (+1) = +100`. OK.
  - Sell at 100, exit 98, qty 50 → `gross_quote = (98 − 100) × 50 × (−1) = +100`. OK.
  - Buy at 100, exit 98, qty 50 → `gross_quote = (98 − 100) × 50 × (+1) = −100`. OK.
  - Sell at 100, exit 102, qty 50 → `gross_quote = (102 − 100) × 50 × (−1) = −100`. OK.
  But the win-side bias is **side-symmetric** (`_BUY_DRIFT = +0.003`, `_SELL_DRIFT = −0.003`),
  so P(win | B) = P(ε > 0 | N(+0.003, 0.012)) = Φ(0.25) = 0.5987 and
  P(win | S) = P(ε < 0 | N(−0.003, 0.012)) = Φ(0.25) = 0.5987. Confirmed.
  Add a one-line docstring to `_close_trade` stating the win-bias mechanics so future
  contributors don't re-derive it.

- [ ] **MA-06** | `tcp/synth/trades.py:336-338` |
  **Truncated-exponential bias.** Drawing `Exp(λ = 1/90)` then clamping to [5, 480]
  shifts the mean from 90 to roughly:
  `E[X | 5 ≤ X ≤ 480] = (∫₅⁴⁸⁰ x · (1/90) e^(−x/90) dx) / (e^(−5/90) − e^(−480/90))`
  ≈ (90 − 5 · e^(−5/90) − 480 · e^(−480/90)) / (e^(−5/90) − e^(−480/90))
  ≈ (90 − 4.73 − 2.30) / (0.9460 − 0.00479)
  ≈ 82.97 / 0.9412 ≈ **88.2 min**.
  Plus the upper clamp (`min(..., 480)`) re-stacks mass at 480 with probability
  `e^(−480/90) ≈ 0.48 %`. Net effect: empirical mean ≈ 88 min, slight right-spike at 480.
  Acceptable. **Test contract**: the median assertion `[40, 140]` is wide enough
  (exponential median = 90 · ln 2 ≈ 62.4 min, clamp shifts negligibly).

## Minor / nits

- [ ] **MN-01** | `tcp/synth/trades.py:312` |
  `factor = max(factor, 0.5)` is dead-ish code (Φ((0.5 − 1)/0.02) = Φ(−25) ≈ 0).
  Keep as a defensive guard but add a comment that it only matters if `_ENTRY_PRICE_SIGMA`
  is raised dramatically.

- [ ] **MN-02** | `tcp/synth/fx_rates.py:46-67` |
  Wobble is deterministic per (currency, date), good. ±0.5 % bound is narrow but
  defensible for daily fixings. Note: because wobble is *quote-currency* keyed,
  cross-pair arbitrage is impossible (USDJPY and EURJPY both share JPY's wobble),
  which is a desirable property — call it out in the docstring.

- [ ] **MN-03** | `tests/unit/test_synth_distributions.py:82-88` |
  Total-count tolerance ±10 % is wide. Expected mean 7200, σ for a Poisson sum
  with clamps ≈ √7200 ≈ 85 trades. The clamp [3, 15] tightens this further. A ±5 %
  band (6840, 7560) is still > 4σ wide — tighten to make the test more meaningful.

- [ ] **MN-04** | `tests/unit/test_synth_distributions.py:294-317` |
  The clamp test is a hard `[3, 15]` check — strong contract, but combined with
  λ = 8 the truncation probability `P(X < 3 ∨ X > 15) ≈ 0.040`, so ~4 % of draws
  are silently re-shaped. Acceptable, document in the generator docstring.

- [ ] **MN-05** | `tcp/synth/trades.py:430` |
  Side flip is exact 0.5/0.5; no test asserts side-balance after restricting to *closed*
  trades. Optional: add a test that conditional-on-closed buy proportion is in [0.46, 0.54].

## Calibration table

| KPI | Spec target | Generator expected (math) | Verdict |
|---|---|---|---|
| Win rate (KPI-TR-060) | ≥ 0.55 | **0.599** = Φ(0.25) for both sides | OK numerically but **contradicts comment + test** |
| Profit factor (KPI-TR-036) | ≥ 1.5 | **1.49** ≈ 0.599 / 0.401 (gross, before commissions) | OK at 0.599 win-rate; **FAILS at 0.52** |
| Annualised Sharpe (KPI-TR-033) | ≥ 1.0 (1.5 good, 2.0 excellent) | **~10.9** | **UNREALISTIC** — order of magnitude too high |
| Max drawdown % (KPI-TR-031) | ≤ 8 % of 80 000 EUR baseline | **~15 %** for 1 750 trades at 25 k notional and σ = 0.012 (≈ 12 000 EUR / 80 000 EUR) | **OFF** at current calibration; OK if quantities scaled down ~2× |
| Profitable-day rate (KPI-TR-061) | ≥ 0.60 | At daily Sharpe ≈ 0.69 → P(daily PnL > 0) = Φ(0.69) ≈ **0.755** | OK in absolute terms but mechanically tied to the inflated Sharpe |
| Open-trade rate | ~0.05 | 0.05 (Bernoulli, exact in expectation) | OK |
| Holding median | [40, 140] min | 62 min (clamped expo, median = 90 · ln 2) | OK |
| Per-trader count | [3, 15]/day, λ = 8 | mean ≈ 7.98 after clamp | OK |
| Side balance | [0.46, 0.54] | 0.50 exact | OK |

## Recommendation

Adopt the following joint calibration to make the synthetic dataset thesis-defensible
in a *single* coherent change-set:

1. **Decouple win-rate from win/loss magnitude** (per CR-03). Replace the single
   `Normal(±μ_side, σ)` shift with: (a) a Bernoulli for win/loss, p_w = 0.55;
   (b) win magnitude `|N(0, σ_w)|` with σ_w ≈ 0.008; (c) loss magnitude
   `|N(0, σ_l)|` with σ_l ≈ 0.006. This yields:
   - WR = 0.55 (passes KPI-TR-060).
   - PF ≈ (0.55 × σ_w) / (0.45 × σ_l) = (0.55 × 0.008) / (0.45 × 0.006) ≈ **1.63**
     (passes KPI-TR-036 ≥ 1.5).
   - Per-trade μ = 0.55 × 0.008 − 0.45 × 0.006 = 0.0044 − 0.0027 = **0.0017**;
     per-trade σ ≈ √(p_w · σ_w² + (1 − p_w) · σ_l² − μ²) ≈ √(3.52e-5 + 1.62e-5 − 2.9e-6) ≈ **0.0070**.
   - Daily Sharpe ≈ (0.0017 / 0.0070) × √7 ≈ 0.643 → annualised ≈ **10.2**. Still too high.

2. **Reduce position sizing** (per CR-02 + max-DD). Scale FX quantity range
   `(1000, 50000)` → `(500, 15000)` (3.3× reduction). The mean FX notional drops from
   ~28 k EUR to ~8.4 k EUR. Per-trade σ on PnL drops linearly. Daily Sharpe drops by
   the same factor → annualised Sharpe ≈ 10.2 / 3.3 ≈ **3.1**. Still high; recommend
   *also* shrinking the σ_w/σ_l by ~50 % to land at annualised ≈ 1.5-2.0.
   Final suggested values: `σ_w = 0.005`, `σ_l = 0.004`, FX qty `(500, 15000)`,
   equity qty `(5, 200)`, crypto qty `(0.005, 2.0)`, commodity qty `(1, 25)`.

3. **Fix the test assertions** to match the new contract (per CR-01):
   - `test_win_rate_close_to_52_percent` → rename to `test_win_rate_close_to_55_percent`,
     bound `[0.52, 0.58]` (3σ over 6800 trades ≈ ±0.018).
   - `test_total_trade_count_within_target` → tighten to ±5 % (per MN-03).
   - Add new `test_profit_factor_at_least_1_5`.
   - Add new `test_daily_sharpe_in_band` with bound [0.05, 0.20] (annualised 0.8-3.2).

4. **Fix `TradeRow` attribute coverage** (per CR-04). Either add `@computed_field`
   properties on `TradeRow` for `holding_minutes`, `quote_currency`, `employee_id`,
   or rewrite the three broken tests to join against the dim fixtures. CI must
   exercise these tests under the new calibration before merging.

5. **Source comments and KPI references**: replace the misleading
   "~52 % win-rate per KPI-TR-031" with the truthful "~55 % win-rate per KPI-TR-060;
   profit factor ≈ 1.6 per KPI-TR-036; daily Sharpe ~0.10 (annualised ≈ 1.6) per KPI-TR-033."

6. **Asset-class market weighting** (per MA-03): apply `{equity: 0.50, fx: 0.30,
   crypto: 0.10, commodity: 0.10}` weights when selecting `market` to reflect a
   realistic boutique-firm mix.

7. **Per-asset entry-price σ** (per MA-02): split `_ENTRY_PRICE_SIGMA` into
   `{equity: 0.005, fx: 0.003, crypto: 0.030, commodity: 0.015}`.

The single highest-leverage fix is CR-03 (decouple win-rate from magnitude via
Bernoulli + half-normal), because it unblocks all three of WR, PF, and Sharpe
simultaneously and is the only model change that lets the spec KPIs be satisfied
*jointly* without contradiction.
