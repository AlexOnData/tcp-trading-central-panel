"""Unit tests for tcp.synth.trades.generate_for_date."""

from __future__ import annotations

import json
import re
from datetime import date, time

import pytest

from tcp.synth.trades import (
    MarketRow,
    OrderTypeRow,
    SessionRow,
    TraderProfile,
    generate_for_date,
)

_TRADE_UID_RE = re.compile(r"^T\d{8}-\d{4}$")


def _markets() -> list[MarketRow]:
    return [
        MarketRow(market_id=1, symbol="AAPL", asset_class="equity", quote_currency="USD"),
        MarketRow(market_id=2, symbol="EURUSD", asset_class="fx", quote_currency="USD"),
        MarketRow(market_id=3, symbol="USDJPY", asset_class="fx", quote_currency="JPY"),
        MarketRow(market_id=4, symbol="BTCUSD", asset_class="crypto", quote_currency="USD"),
        MarketRow(market_id=5, symbol="XAUUSD", asset_class="commodity", quote_currency="USD"),
        MarketRow(
            market_id=6, symbol="DEAD", asset_class="equity", quote_currency="USD", is_active=False
        ),
    ]


def _sessions() -> list[SessionRow]:
    return [
        SessionRow(
            session_id=1,
            session_code="pre_market",
            start_time_local=time(7, 0),
            end_time_local=time(9, 30),
        ),
        SessionRow(
            session_id=2,
            session_code="regular",
            start_time_local=time(9, 30),
            end_time_local=time(17, 30),
        ),
        SessionRow(
            session_id=3,
            session_code="after_hours",
            start_time_local=time(17, 30),
            end_time_local=time(22, 0),
        ),
    ]


def _order_types() -> list[OrderTypeRow]:
    return [
        OrderTypeRow(order_type_id=1, order_type_code="market"),
        OrderTypeRow(order_type_id=2, order_type_code="limit"),
        OrderTypeRow(order_type_id=3, order_type_code="stop"),
        OrderTypeRow(order_type_id=4, order_type_code="stop_limit"),
    ]


def _traders(n: int = 30) -> list[TraderProfile]:
    # Match the production org: 24 traders + 6 team leads = 30 trading-eligible.
    return [TraderProfile(trader_id=i, account_id=i) for i in range(1, n + 1)]


_TRADE_DATE = date(2026, 5, 14)


def test_generates_within_per_trader_bounds() -> None:
    rows = generate_for_date(
        _TRADE_DATE,
        traders=_traders(),
        markets=_markets(),
        sessions=_sessions(),
        order_types=_order_types(),
    )
    n_traders = 30
    # Per-trader bound is [3, 15]; total must lie in [n*3, n*15].
    assert n_traders * 3 <= len(rows) <= n_traders * 15


def test_generation_is_deterministic_byte_for_byte() -> None:
    kwargs = dict(
        traders=_traders(),
        markets=_markets(),
        sessions=_sessions(),
        order_types=_order_types(),
    )
    a = generate_for_date(_TRADE_DATE, **kwargs)  # type: ignore[arg-type]
    b = generate_for_date(_TRADE_DATE, **kwargs)  # type: ignore[arg-type]
    a_json = json.dumps([r.to_json_dict() for r in a], default=str, sort_keys=True)
    b_json = json.dumps([r.to_json_dict() for r in b], default=str, sort_keys=True)
    assert a_json == b_json


def test_open_rate_within_tolerance_over_large_sample() -> None:
    # Run across many dates to accumulate a large sample, then assert the
    # observed open-rate is within ±2 % of the 5 % target.
    total = 0
    opened = 0
    for offset in range(0, 60):
        d = date.fromordinal(_TRADE_DATE.toordinal() + offset)
        rows = generate_for_date(
            d,
            traders=_traders(),
            markets=_markets(),
            sessions=_sessions(),
            order_types=_order_types(),
        )
        total += len(rows)
        opened += sum(1 for r in rows if r.is_open)
    rate = opened / total if total else 0
    assert 0.03 <= rate <= 0.07, f"observed open-rate={rate:.3f}"


def test_trade_uid_format_and_uniqueness() -> None:
    rows = generate_for_date(
        _TRADE_DATE,
        traders=_traders(),
        markets=_markets(),
        sessions=_sessions(),
        order_types=_order_types(),
    )
    seen: set[str] = set()
    for r in rows:
        assert _TRADE_UID_RE.match(r.trade_uid), r.trade_uid
        assert r.trade_uid.startswith("T20260514-")
        assert r.trade_uid not in seen
        seen.add(r.trade_uid)


def test_time_exit_never_before_time_entry() -> None:
    rows = generate_for_date(
        _TRADE_DATE,
        traders=_traders(),
        markets=_markets(),
        sessions=_sessions(),
        order_types=_order_types(),
    )
    for r in rows:
        if r.is_open:
            assert r.time_exit is None
            assert r.price_exit is None
            assert r.gross_pnl_eur is None
            assert r.net_pnl_eur is None
            assert r.fx_rate_to_eur is None
        else:
            assert r.time_exit is not None
            assert r.time_exit >= r.time_entry


def test_closed_trades_have_net_equal_to_gross_minus_commission() -> None:
    rows = generate_for_date(
        _TRADE_DATE,
        traders=_traders(),
        markets=_markets(),
        sessions=_sessions(),
        order_types=_order_types(),
    )
    for r in rows:
        if r.is_open:
            continue
        assert r.gross_pnl_eur is not None
        assert r.net_pnl_eur is not None
        # Quantised difference must match exactly.
        diff = (r.gross_pnl_eur - r.commission_eur).quantize(r.net_pnl_eur)
        assert diff == r.net_pnl_eur


def test_inactive_markets_never_selected() -> None:
    rows = generate_for_date(
        _TRADE_DATE,
        traders=_traders(),
        markets=_markets(),
        sessions=_sessions(),
        order_types=_order_types(),
    )
    dead_id = 6
    assert all(r.market_id != dead_id for r in rows)


def test_empty_inputs_return_empty_list() -> None:
    assert (
        generate_for_date(
            _TRADE_DATE,
            traders=_traders(),
            markets=[],
            sessions=_sessions(),
            order_types=_order_types(),
        )
        == []
    )
    assert (
        generate_for_date(
            _TRADE_DATE,
            traders=_traders(),
            markets=_markets(),
            sessions=[],
            order_types=_order_types(),
        )
        == []
    )


def test_to_json_dict_payload_shape() -> None:
    rows = generate_for_date(
        _TRADE_DATE,
        traders=_traders(n=2),
        markets=_markets(),
        sessions=_sessions(),
        order_types=_order_types(),
    )
    sample = rows[0].to_json_dict()
    required_keys = {
        "trade_uid",
        "trader_id",
        "account_id",
        "market_id",
        "session_id",
        "order_type_id",
        "side",
        "quantity",
        "price_entry",
        "price_exit",
        "time_entry",
        "time_exit",
        "gross_pnl_eur",
        "commission_eur",
        "net_pnl_eur",
        "is_open",
        "fx_rate_to_eur",
    }
    assert set(sample.keys()) == required_keys
    assert sample["side"] in ("B", "S")
    assert sample["is_open"] in (0, 1)
    # time_entry must be a tz-aware ISO-8601 string.
    assert "+" in sample["time_entry"] or "-" in sample["time_entry"][10:]


@pytest.mark.parametrize("offset", [0, 1, 2, 3])
def test_two_different_dates_produce_different_outputs(offset: int) -> None:
    base = generate_for_date(
        _TRADE_DATE,
        traders=_traders(n=4),
        markets=_markets(),
        sessions=_sessions(),
        order_types=_order_types(),
    )
    other_date = date.fromordinal(_TRADE_DATE.toordinal() + offset + 1)
    other = generate_for_date(
        other_date,
        traders=_traders(n=4),
        markets=_markets(),
        sessions=_sessions(),
        order_types=_order_types(),
    )
    base_json = json.dumps([r.to_json_dict() for r in base], default=str)
    other_json = json.dumps([r.to_json_dict() for r in other], default=str)
    assert base_json != other_json
