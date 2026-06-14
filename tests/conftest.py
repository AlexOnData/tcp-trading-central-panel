"""Shared pytest fixtures for the TCP test suite.

Provides:
- ``synthetic_dims``: session-scoped, in-memory dim-row models covering all
  asset classes, all standard sessions, all order types, and 30 traders.
  Used by both unit and integration tests so the dimension set is consistent.
- ``tmp_seed_offset``: autouse session fixture that pins ``TCP_SYNTH_SEED_OFFSET``
  to "0" so every test run is byte-for-byte deterministic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from typing import Final

import pytest

# ---------------------------------------------------------------------------
# Constants that mirror the TCP org structure.
# ---------------------------------------------------------------------------

_TRADER_COUNT: Final[int] = 30  # 24 traders + 6 team leads
_TRADING_FLOOR_NAMES: Final[list[str]] = ["București", "Cluj-Napoca"]
_TEAM_NAMES: Final[list[str]] = [
    "Alpha",
    "Beta",
    "Gamma",
    "Delta",
    "Epsilon",
    "Zeta",
]


# ---------------------------------------------------------------------------
# Lightweight in-memory row models.
# The parallel python-pro agent produces Pydantic models; we mirror the same
# field names so tests can be swapped to the real models without editing
# assertions.  We use dataclasses here so conftest has zero runtime dependency
# on tcp.synth (which may not exist yet during early test runs).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TraderProfile:
    """Minimal trader profile used by ``generate_for_date``.

    Field names mirror the production ``tcp.synth.trades.TraderProfile``
    Pydantic model so the generator can iterate the fixture rows directly.
    """

    trader_id: int
    account_id: int
    full_name: str = ""
    team_id: int = 1
    floor_id: int = 1
    capital_baseline_eur: Decimal = Decimal("80000.00")
    is_active: bool = True


@dataclass(frozen=True)
class _MarketRow:
    """Minimal market dimension row used by ``generate_for_date``.

    Field names mirror the production ``tcp.synth.trades.MarketRow``.
    """

    market_id: int
    symbol: str
    asset_class: str  # 'equity' | 'fx' | 'crypto' | 'commodity'
    quote_currency: str
    typical_spread: Decimal
    lot_size: Decimal
    price_min: Decimal
    price_max: Decimal
    is_active: bool = True


@dataclass(frozen=True)
class _SessionRow:
    """Minimal trading session dimension row.

    Field names mirror the production ``tcp.synth.trades.SessionRow``.
    """

    session_id: int
    session_code: str  # 'pre_market' | 'regular' | 'after_hours'
    start_time_local: time
    end_time_local: time
    session_name: str = ""


@dataclass(frozen=True)
class _OrderTypeRow:
    """Minimal order-type dimension row.

    Field names mirror the production ``tcp.synth.trades.OrderTypeRow``.
    """

    order_type_id: int
    order_type_code: str  # 'market' | 'limit' | 'stop' | 'stop_limit'
    order_type_name: str = ""


@dataclass(frozen=True)
class SyntheticDims:
    """Aggregated in-memory dimension fixtures for generator tests."""

    traders: list[_TraderProfile]
    markets: list[_MarketRow]
    sessions: list[_SessionRow]
    order_types: list[_OrderTypeRow]


# ---------------------------------------------------------------------------
# Factory helpers — not exposed as fixtures directly.
# ---------------------------------------------------------------------------


def _build_traders() -> list[_TraderProfile]:
    """Build 30 trader profiles (24 traders + 6 team leads) spread across 6 teams."""
    traders: list[_TraderProfile] = []
    employee_id = 1
    for team_idx in range(6):
        team_id = team_idx + 1
        floor_id = 1 if team_idx < 3 else 2
        # 1 team lead + 4 traders per team = 5 members, 6 teams = 30 total.
        for member_idx in range(5):
            traders.append(
                _TraderProfile(
                    trader_id=employee_id,
                    account_id=employee_id,
                    full_name=f"Trader_{employee_id:02d}",
                    team_id=team_id,
                    floor_id=floor_id,
                )
            )
            employee_id += 1
    return traders


def _build_markets() -> list[_MarketRow]:
    """Build one representative market per asset class and quote currency."""
    return [
        # Equities (USD-quoted, per-share commission)
        _MarketRow(
            market_id=1,
            symbol="AAPL",
            asset_class="equity",
            quote_currency="USD",
            typical_spread=Decimal("0.01"),
            lot_size=Decimal("1"),
            price_min=Decimal("100.00"),
            price_max=Decimal("300.00"),
        ),
        _MarketRow(
            market_id=2,
            symbol="MSFT",
            asset_class="equity",
            quote_currency="USD",
            typical_spread=Decimal("0.01"),
            lot_size=Decimal("1"),
            price_min=Decimal("200.00"),
            price_max=Decimal("500.00"),
        ),
        # FX (USD-quoted)
        _MarketRow(
            market_id=3,
            symbol="EURUSD",
            asset_class="fx",
            quote_currency="USD",
            typical_spread=Decimal("0.00010"),
            lot_size=Decimal("100000"),
            price_min=Decimal("1.0500"),
            price_max=Decimal("1.1500"),
        ),
        # FX (EUR-quoted)
        _MarketRow(
            market_id=4,
            symbol="EURGBP",
            asset_class="fx",
            quote_currency="GBP",
            typical_spread=Decimal("0.00010"),
            lot_size=Decimal("100000"),
            price_min=Decimal("0.8400"),
            price_max=Decimal("0.9000"),
        ),
        # Crypto (USD-quoted)
        _MarketRow(
            market_id=5,
            symbol="BTCUSD",
            asset_class="crypto",
            quote_currency="USD",
            typical_spread=Decimal("50.00"),
            lot_size=Decimal("0.01"),
            price_min=Decimal("20000.00"),
            price_max=Decimal("80000.00"),
        ),
        # Commodities (USD-quoted, per-contract commission)
        _MarketRow(
            market_id=6,
            symbol="XAUUSD",
            asset_class="commodity",
            quote_currency="USD",
            typical_spread=Decimal("0.30"),
            lot_size=Decimal("1"),
            price_min=Decimal("1800.00"),
            price_max=Decimal("2500.00"),
        ),
        # EUR-quoted equity for PnL sanity tests
        _MarketRow(
            market_id=7,
            symbol="EURSTK",
            asset_class="equity",
            quote_currency="EUR",
            typical_spread=Decimal("0.01"),
            lot_size=Decimal("1"),
            price_min=Decimal("10.00"),
            price_max=Decimal("200.00"),
        ),
    ]


def _build_sessions() -> list[_SessionRow]:
    """Build the three standard TCP trading sessions.

    Field names mirror production: ``session_code`` /
    ``start_time_local`` / ``end_time_local``.
    """
    return [
        _SessionRow(
            session_id=1,
            session_code="pre_market",
            session_name="European Morning",
            start_time_local=time(8, 0),
            end_time_local=time(12, 0),
        ),
        _SessionRow(
            session_id=2,
            session_code="regular",
            session_name="European Afternoon",
            start_time_local=time(9, 30),
            end_time_local=time(17, 30),
        ),
        _SessionRow(
            session_id=3,
            session_code="after_hours",
            session_name="US Session",
            start_time_local=time(15, 30),
            end_time_local=time(22, 0),
        ),
    ]


def _build_order_types() -> list[_OrderTypeRow]:
    """Build the standard order-type dimension rows.

    Field names mirror production: ``order_type_code``.
    """
    return [
        _OrderTypeRow(order_type_id=1, order_type_code="market", order_type_name="Market"),
        _OrderTypeRow(order_type_id=2, order_type_code="limit", order_type_name="Limit"),
        _OrderTypeRow(order_type_id=3, order_type_code="stop", order_type_name="Stop"),
        _OrderTypeRow(
            order_type_id=4,
            order_type_code="stop_limit",
            order_type_name="Stop-Limit",
        ),
    ]


# ---------------------------------------------------------------------------
# Public fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def synthetic_dims() -> SyntheticDims:
    """Session-scoped in-memory dimensions covering all asset classes and 30 traders.

    Both unit (test_synth_distributions.py) and integration
    (test_generator_idempotency.py) tests consume this fixture so the
    dimension set is identical across the full suite.
    """
    return SyntheticDims(
        traders=_build_traders(),
        markets=_build_markets(),
        sessions=_build_sessions(),
        order_types=_build_order_types(),
    )


@pytest.fixture(scope="session", autouse=True)
def tmp_seed_offset() -> None:
    """Pin TCP_SYNTH_SEED_OFFSET=0 for the entire test session.

    Ensures that generate_for_date is byte-for-byte deterministic regardless
    of the environment the tests run in. The fixture is autouse at session
    scope so it takes effect before the first test collects.
    """
    os.environ["TCP_SYNTH_SEED_OFFSET"] = "0"
