"""Statistical distribution tests for the tcp.synth in-memory generator.

All tests run against the output of ``generate_for_date`` with no live SQL
Server.  Seeds are controlled via the session-scoped ``tmp_seed_offset``
autouse fixture in conftest.py and by the deterministic ``seed_for_date``
implementation in ``tcp.synth._rng``.

Statistical tolerances are derived from the spec targets in
``docs/design/01_business_requirements.md`` §4.6 (win rate ≥ 55 %,
profitable-day rate ≥ 60 %) and from the generator design contract
(Poisson λ=8 trades/trader/day, exponential holding-time mean=90 min,
per-trader clamp [3, 15]).  All tolerances include a deliberate margin
wide enough to survive normal Monte-Carlo variation across 30 days × 30
traders without becoming flaky.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Final

import pytest

from tcp.synth import TradeRow, generate_for_date
from tests.conftest import SyntheticDims

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRADE_DATE_BASE: Final[date] = date(2026, 5, 14)  # A Thursday.
_SAMPLE_DAYS: Final[int] = 30
_TRADER_COUNT: Final[int] = 30
_LAMBDA: Final[int] = 8  # Target trades/trader/day.


def _sample_dates(n: int = _SAMPLE_DAYS) -> list[date]:
    """Return n consecutive business dates starting from _TRADE_DATE_BASE."""
    result: list[date] = []
    d = _TRADE_DATE_BASE
    while len(result) < n:
        if d.weekday() < 5:  # Mon-Fri only.
            result.append(d)
        d += timedelta(days=1)
    return result


def _generate_sample(
    dims: SyntheticDims,
    dates: list[date] | None = None,
) -> list[TradeRow]:
    """Generate trade rows for all sample dates and return the combined list."""
    all_rows: list[TradeRow] = []
    for trade_date in (dates or _sample_dates()):
        rows = generate_for_date(
            trade_date,
            traders=dims.traders,
            markets=dims.markets,
            sessions=dims.sessions,
            order_types=dims.order_types,
        )
        all_rows.extend(rows)
    return all_rows


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_total_trade_count_within_target(synthetic_dims: SyntheticDims) -> None:
    """Total trade volume over 30 days fits the Poisson(λ=8) per-trader target.

    Expected mean: 30 days × 30 traders × 8 trades = 7 200.
    Tolerance band: ±10 % = [6 480, 7 920].
    """
    rows = _generate_sample(synthetic_dims)
    expected_mean = _SAMPLE_DAYS * _TRADER_COUNT * _LAMBDA
    low = int(expected_mean * 0.90)
    high = int(expected_mean * 1.10)
    total = len(rows)
    assert low <= total <= high, (
        f"Total trade count {total} outside [{low}, {high}] "
        f"(30d × 30 traders × λ={_LAMBDA} ± 10 %)"
    )


def test_win_rate_close_to_52_percent(synthetic_dims: SyntheticDims) -> None:
    """Win rate (net_pnl_eur > 0 among closed trades) is in [0.49, 0.55].

    Calibration: ``_BUY_DRIFT = ±0.0006`` with ``_RETURN_SIGMA = 0.012`` gives
    ``Φ(0.05) ≈ 0.520`` gross win rate (statistical CR-01). KPI-TR-060 spec
    is ≥ 55 %; the generator deliberately undershoots so cross-trader/team
    variance is not saturated. At n ≈ 6 800 closed samples, 3σ around p=0.52
    is roughly [0.503, 0.537]; the [0.49, 0.55] band is ~3.5σ on each side.
    """
    rows = _generate_sample(synthetic_dims)
    closed = [r for r in rows if not r.is_open]
    assert len(closed) > 0, "No closed trades found in 30-day sample"
    wins = sum(1 for r in closed if r.net_pnl_eur is not None and r.net_pnl_eur > Decimal("0"))
    win_rate = wins / len(closed)
    assert 0.49 <= win_rate <= 0.55, (
        f"Win rate {win_rate:.4f} outside [0.49, 0.55]; spec target ~0.52"
    )


def test_open_rate_close_to_5_percent(synthetic_dims: SyntheticDims) -> None:
    """Proportion of open (unrealised) positions is in [0.03, 0.07].

    Generator design targets ~5 % of trades remaining open at end-of-day.
    """
    rows = _generate_sample(synthetic_dims)
    open_rate = sum(1 for r in rows if r.is_open) / len(rows)
    assert 0.03 <= open_rate <= 0.07, (
        f"Open-trade rate {open_rate:.4f} outside [0.03, 0.07]"
    )


def test_side_balance(synthetic_dims: SyntheticDims) -> None:
    """Buy/Sell split is roughly equal — each side in [0.46, 0.54]."""
    rows = _generate_sample(synthetic_dims)
    buys = sum(1 for r in rows if r.side == "B")
    buy_rate = buys / len(rows)
    assert 0.46 <= buy_rate <= 0.54, (
        f"Buy proportion {buy_rate:.4f} outside [0.46, 0.54]"
    )


def test_holding_time_distribution(synthetic_dims: SyntheticDims) -> None:
    """Holding times (minutes) satisfy the exponential-mean-90 contract.

    Checks:
    - Median in [40, 140] minutes.
    - No negative holding time exists.
    - Maximum holding time is ≤ 480 minutes (8 h intraday cap).

    Only closed trades have meaningful holding times; open trades have no
    ``time_exit`` and are excluded. Holding time is derived from
    ``(time_exit - time_entry)`` since ``TradeRow`` exposes the raw
    timestamps (statistical CR-02 review).
    """
    rows = _generate_sample(synthetic_dims)
    closed = [r for r in rows if not r.is_open and r.time_exit is not None]
    holding_times = [
        (r.time_exit - r.time_entry).total_seconds() / 60.0  # type: ignore[operator]
        for r in closed
    ]
    assert holding_times, "No holding-time values found for closed trades"

    # No negative durations.
    negs = [h for h in holding_times if h < 0]
    assert not negs, f"Found {len(negs)} negative holding-time(s): {negs[:5]}"

    # Maximum cap. MA-02 clamps time_exit to end-of-day in Europe/Bucharest,
    # so an entry close to midnight may produce a holding < 480 min — the
    # upper bound stays 480 min by construction.
    max_ht = max(holding_times)
    assert max_ht <= 480.5, f"Max holding time {max_ht:.1f} min exceeds 480 min cap"

    # Median check.
    sorted_ht = sorted(holding_times)
    n = len(sorted_ht)
    median = (sorted_ht[n // 2] + sorted_ht[(n - 1) // 2]) / 2
    assert 40 <= median <= 140, (
        f"Median holding time {median:.1f} min outside [40, 140] "
        f"(exponential mean=90 ± skew)"
    )


def test_no_negative_quantities(synthetic_dims: SyntheticDims) -> None:
    """Every trade has a strictly positive quantity."""
    rows = _generate_sample(synthetic_dims)
    bad = [r for r in rows if r.quantity <= Decimal("0")]
    assert not bad, (
        f"Found {len(bad)} trade(s) with non-positive quantity; "
        f"first: quantity={bad[0].quantity}"
    )


def test_trade_uid_format(synthetic_dims: SyntheticDims) -> None:
    """Every trade_uid matches T<YYYYMMDD>-<NNNN> as required by CLAUDE.md."""
    pattern = re.compile(r"^T\d{8}-\d{4}$")
    rows = _generate_sample(synthetic_dims)
    bad = [r.trade_uid for r in rows if not pattern.match(r.trade_uid)]
    assert not bad, (
        f"Found {len(bad)} malformed trade_uid(s); first: {bad[0]!r}"
    )


def test_trade_uid_uniqueness_within_day(synthetic_dims: SyntheticDims) -> None:
    """No duplicate trade_uid values within a single trading date's output."""
    for trade_date in _sample_dates():
        rows = generate_for_date(
            trade_date,
            traders=synthetic_dims.traders,
            markets=synthetic_dims.markets,
            sessions=synthetic_dims.sessions,
            order_types=synthetic_dims.order_types,
        )
        uids = [r.trade_uid for r in rows]
        unique_uids = set(uids)
        assert len(uids) == len(unique_uids), (
            f"Duplicate trade_uid on {trade_date}: "
            f"{len(uids) - len(unique_uids)} collision(s)"
        )


def test_pnl_matches_quantity_price_fx(synthetic_dims: SyntheticDims) -> None:
    """Sanity: gross_pnl_eur matches (exit-entry)*qty*direction for EUR markets.

    For EUR-quoted instruments ``fx_rate_to_eur`` is exactly 1.0 so no
    conversion ambiguity exists. ``TradeRow`` does not expose
    ``quote_currency`` directly — we derive it by joining ``r.market_id``
    against the dim_Markets fixture (statistical CR-02 review).
    We allow ±0.01 EUR rounding tolerance for DECIMAL(18,4) quantisation.
    """
    rows = generate_for_date(
        _TRADE_DATE_BASE,
        traders=synthetic_dims.traders,
        markets=synthetic_dims.markets,
        sessions=synthetic_dims.sessions,
        order_types=synthetic_dims.order_types,
    )
    eur_market_ids = {
        m.market_id for m in synthetic_dims.markets if m.quote_currency == "EUR"
    }
    eur_closed = [
        r for r in rows
        if not r.is_open
        and r.market_id in eur_market_ids
        and r.price_exit is not None
        and r.gross_pnl_eur is not None
    ]
    assert eur_closed, (
        "No closed EUR-quoted trades found on the test date; "
        "add an EUR market to the synthetic_dims fixture if needed"
    )
    tolerance = Decimal("0.01")
    mismatches: list[str] = []
    for r in eur_closed:
        direction = Decimal("1") if r.side == "B" else Decimal("-1")
        assert r.price_exit is not None  # narrow for mypy
        assert r.gross_pnl_eur is not None
        expected_gross = (
            (r.price_exit - r.price_entry) * r.quantity * direction
        ).quantize(Decimal("0.0001"))
        diff = abs(r.gross_pnl_eur - expected_gross)
        if diff > tolerance:
            mismatches.append(
                f"trade_uid={r.trade_uid} expected={expected_gross} "
                f"got={r.gross_pnl_eur} diff={diff}"
            )
    assert not mismatches, (
        f"PnL formula mismatch for {len(mismatches)} EUR trade(s):\n"
        + "\n".join(mismatches[:5])
    )


def test_commission_positive(synthetic_dims: SyntheticDims) -> None:
    """Every trade row carries a strictly positive commission_eur."""
    rows = _generate_sample(synthetic_dims)
    bad = [r for r in rows if r.commission_eur <= Decimal("0")]
    assert not bad, (
        f"Found {len(bad)} trade(s) with non-positive commission; "
        f"first: trade_uid={bad[0].trade_uid} commission={bad[0].commission_eur}"
    )


def test_determinism_byte_equal(synthetic_dims: SyntheticDims) -> None:
    """Two identical calls to generate_for_date produce byte-equal output.

    The TCP_SYNTH_SEED_OFFSET=0 fixture (conftest.py) pins the RNG chain;
    this test guards against any accidental use of time.time() or os.urandom
    inside the generator.
    """
    first = generate_for_date(
        _TRADE_DATE_BASE,
        traders=synthetic_dims.traders,
        markets=synthetic_dims.markets,
        sessions=synthetic_dims.sessions,
        order_types=synthetic_dims.order_types,
    )
    second = generate_for_date(
        _TRADE_DATE_BASE,
        traders=synthetic_dims.traders,
        markets=synthetic_dims.markets,
        sessions=synthetic_dims.sessions,
        order_types=synthetic_dims.order_types,
    )

    def _row_as_dict(r: TradeRow) -> dict[str, object]:
        return r.model_dump(mode="json")

    first_json = json.dumps([_row_as_dict(r) for r in first], sort_keys=True)
    second_json = json.dumps([_row_as_dict(r) for r in second], sort_keys=True)
    assert first_json == second_json, (
        "generate_for_date is not deterministic: output differed between two "
        "identical calls with the same date and dimension fixtures"
    )


def test_per_trader_distribution_clamped(synthetic_dims: SyntheticDims) -> None:
    """Per-trader trade count is in [3, 15] for every (date, trader) pair.

    KPI-TR-001 requires ≥ 3 trades/day per active trader; the generator
    hard-clamps the Poisson draw to the interval [3, 15].
    """
    low, high = 3, 15
    violations: list[str] = []
    for trade_date in _sample_dates():
        rows = generate_for_date(
            trade_date,
            traders=synthetic_dims.traders,
            markets=synthetic_dims.markets,
            sessions=synthetic_dims.sessions,
            order_types=synthetic_dims.order_types,
        )
        count_by_trader: dict[int, int] = {}
        for r in rows:
            # TradeRow exposes ``trader_id`` (not ``employee_id``) per the
            # production JSON contract; statistical CR-02 review.
            count_by_trader[r.trader_id] = count_by_trader.get(r.trader_id, 0) + 1
        for trader_id, count in count_by_trader.items():
            if not (low <= count <= high):
                violations.append(
                    f"date={trade_date} trader_id={trader_id} count={count}"
                )
    assert not violations, (
        f"Per-trader count outside [{low}, {high}] on "
        f"{len(violations)} (date, trader) pair(s):\n"
        + "\n".join(violations[:10])
    )
