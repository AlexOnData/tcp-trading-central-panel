"""Core deterministic synthetic-trade generator.

Produces ~7-10 trades per active trader per business day. Every random
draw is sourced from a `random.Random` instance seeded via
`tcp.synth._rng.seed_for_date`, so calling
``generate_for_date(date(2026, 5, 14), ...)`` twice on the same inputs
returns byte-for-byte identical `TradeRow` lists and therefore
identical JSON when serialised through `TradeRow.to_json_dict`.

The generator does NOT touch the database; it returns an in-memory
`list[TradeRow]` that the runner (`tcp.synth.runner`) serialises into
the JSON contract consumed by ``dbo.usp_GenerateDailyTrades``.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from random import Random
from typing import Any, Final, Literal, Sequence, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from tcp.synth._rng import seed_for_date
from tcp.synth.commissions import compute_commission
from tcp.synth.fx_rates import get_fx_rate

# Europe/Bucharest IANA name; ZoneInfo handles DST automatically so the
# +02:00 / +03:00 offset switch is correct year-round.
_TZ_BUCHAREST: Final[ZoneInfo] = ZoneInfo("Europe/Bucharest")

# Quantisation grids match the database column precisions.
_QTY_INT: Final[Decimal] = Decimal("1")
_QTY_CRYPTO: Final[Decimal] = Decimal("0.0001")
_PRICE_QUANTISE: Final[Decimal] = Decimal("0.000001")
_PNL_QUANTISE: Final[Decimal] = Decimal("0.0001")

# Poisson lambda and clamp bounds for the per-trader daily trade count.
_POISSON_LAMBDA: Final[float] = 8.0
_MIN_TRADES_PER_TRADER: Final[int] = 3
_MAX_TRADES_PER_TRADER: Final[int] = 15

_MARKET_ORDER_WEIGHT: Final[float] = 0.70
_LIMIT_ORDER_WEIGHT: Final[float] = 0.20
_STOP_ORDER_WEIGHT: Final[float] = 0.10

_OPEN_TRADE_PROBABILITY: Final[float] = 0.05

# Win-bias drifts so closed trades realise ~52 % win-rate per KPI-TR-060.
# μ_side = ±0.0006 ⇒ Φ(0.0006 / 0.012) = Φ(0.05) ≈ 0.520 gross win rate.
# Documented spec target is ≥ 55 %; the generator deliberately undershoots
# by ~3 pp so natural Monte-Carlo variation across 30 days × 30 traders does
# not produce an obviously-overcalibrated dataset (~10× Sharpe inflation
# would otherwise result). See docs/design/reviews/review_etapa3_stats_pass1.md.
_BUY_DRIFT: Final[float] = 0.0006
_SELL_DRIFT: Final[float] = -0.0006
_RETURN_SIGMA: Final[float] = 0.012
_ENTRY_PRICE_SIGMA: Final[float] = 0.02

# Exponential holding-time mean (minutes) and clamp.
_HOLDING_MEAN_MINUTES: Final[float] = 90.0
_HOLDING_MIN_MINUTES: Final[int] = 5
_HOLDING_MAX_MINUTES: Final[int] = 480

# Per-symbol base prices in quote-currency units. Numbers are coarse but
# realistic for the 2026 calendar window: equities ~150 USD, FX majors near
# their 2026 central tendencies, BTC at 95k USD, gold at 2050 USD, etc.
_BASE_PRICES: Final[dict[str, Decimal]] = {
    # US equities (USD)
    "AAPL": Decimal("185.00"),
    "MSFT": Decimal("415.00"),
    "GOOGL": Decimal("170.00"),
    "AMZN": Decimal("185.00"),
    "META": Decimal("510.00"),
    "TSLA": Decimal("240.00"),
    "NVDA": Decimal("950.00"),
    "JPM": Decimal("210.00"),
    "XOM": Decimal("115.00"),
    "SPY": Decimal("520.00"),
    # FX pairs (quote in the second leg)
    "EURUSD": Decimal("1.0850"),
    "GBPUSD": Decimal("1.2800"),
    "USDJPY": Decimal("155.40"),
    "USDCHF": Decimal("0.8500"),
    "AUDUSD": Decimal("0.6600"),
    "EURGBP": Decimal("0.8400"),
    "EURJPY": Decimal("168.00"),
    "USDRON": Decimal("4.6500"),
    # Crypto (USD)
    "BTCUSD": Decimal("95000.00"),
    "ETHUSD": Decimal("3400.00"),
    "SOLUSD": Decimal("145.00"),
    "ADAUSD": Decimal("0.55"),
    "XRPUSD": Decimal("0.62"),
    "DOGEUSD": Decimal("0.16"),
    # Commodities (USD)
    "XAUUSD": Decimal("2050.00"),
    "XAGUSD": Decimal("28.50"),
    "WTI": Decimal("78.00"),
    "BRENT": Decimal("82.00"),
    "NATGAS": Decimal("3.40"),
    "COPPER": Decimal("4.20"),
}
_FALLBACK_BASE_PRICE: Final[Decimal] = Decimal("100.00")


# ---------------------------------------------------------------------------
# Input row models (the runner populates these from dim_* SELECTs)
# ---------------------------------------------------------------------------


class TraderProfile(BaseModel):
    """A trader / team-lead eligible for daily trade generation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trader_id: int = Field(..., gt=0)
    account_id: int = Field(..., gt=0)


class MarketRow(BaseModel):
    """A single row from ``dim_Markets`` relevant to the generator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market_id: int = Field(..., gt=0)
    symbol: str = Field(..., min_length=1, max_length=20)
    asset_class: Literal["equity", "fx", "crypto", "commodity"]
    quote_currency: str = Field(..., min_length=3, max_length=3)
    is_active: bool = True


class SessionRow(BaseModel):
    """A single row from ``dim_Sessions``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: int = Field(..., gt=0)
    session_code: Literal["pre_market", "regular", "after_hours"]
    start_time_local: time
    end_time_local: time


class OrderTypeRow(BaseModel):
    """A single row from ``dim_OrderType``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    order_type_id: int = Field(..., gt=0)
    order_type_code: Literal["market", "limit", "stop", "stop_limit"]


# ---------------------------------------------------------------------------
# Output row model
# ---------------------------------------------------------------------------


class TradeRow(BaseModel):
    """A single synthetic trade ready for ``fact_Trades`` insertion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trade_uid: str
    trader_id: int
    account_id: int
    market_id: int
    session_id: int
    order_type_id: int
    side: Literal["B", "S"]
    quantity: Decimal
    price_entry: Decimal
    price_exit: Decimal | None
    time_entry: datetime
    time_exit: datetime | None
    gross_pnl_eur: Decimal | None
    commission_eur: Decimal
    net_pnl_eur: Decimal | None
    is_open: bool
    fx_rate_to_eur: Decimal | None

    def to_json_dict(self) -> dict[str, Any]:
        """Serialise the trade into the JSON shape consumed by the SQL proc.

        Decimals are emitted as floats so ``json.dumps`` does not need a
        custom encoder; datetimes are emitted as ISO-8601 strings with the
        offset preserved; ``is_open`` is emitted as ``0``/``1`` to match
        the ``BIT`` column on the SQL side.

        Returns:
            A dict whose keys match the OPENJSON schema of
            ``dbo.usp_GenerateDailyTrades`` (see the contract in
            `docs/design/02_database_design.md` §7.1 / the V002 migration).
        """
        return {
            "trade_uid": self.trade_uid,
            "trader_id": self.trader_id,
            "account_id": self.account_id,
            "market_id": self.market_id,
            "session_id": self.session_id,
            "order_type_id": self.order_type_id,
            "side": self.side,
            "quantity": float(self.quantity),
            "price_entry": float(self.price_entry),
            "price_exit": None if self.price_exit is None else float(self.price_exit),
            "time_entry": self.time_entry.isoformat(),
            "time_exit": None if self.time_exit is None else self.time_exit.isoformat(),
            "gross_pnl_eur": None if self.gross_pnl_eur is None else float(self.gross_pnl_eur),
            "commission_eur": float(self.commission_eur),
            "net_pnl_eur": None if self.net_pnl_eur is None else float(self.net_pnl_eur),
            "is_open": 1 if self.is_open else 0,
            "fx_rate_to_eur": (
                None if self.fx_rate_to_eur is None else float(self.fx_rate_to_eur)
            ),
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _poisson_clamped(rng: Random, lam: float, lo: int, hi: int) -> int:
    """Draw a Poisson(λ) variate via Knuth's algorithm, clamped to [lo, hi].

    Implemented inline to keep the dependency surface tight (no NumPy) and
    to stay 100 % deterministic against `random.Random`.
    """
    target = math.exp(-lam)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= target:
            n = k - 1
            break
    return max(lo, min(hi, n))


# Etapa-11 typing fix: generic so the per-call return type matches the
# `choices[*][0]` value type (SessionRow / OrderTypeRow / etc.) rather than
# falling through to `Any`. This was producing `no-any-return` errors at the
# call-sites in `_pick_session` / `_pick_order_type`.
_WeightedT = TypeVar("_WeightedT")


def _weighted_choice(rng: Random, choices: Sequence[tuple[_WeightedT, float]]) -> _WeightedT:
    """Pick an element from ``choices`` using the bundled float weights.

    ``choices`` is a sequence of ``(value, weight)`` tuples; weights must
    be non-negative and sum to a positive number.
    """
    total = sum(w for _, w in choices)
    target = rng.random() * total
    cumulative = 0.0
    for value, weight in choices:
        cumulative += weight
        if target <= cumulative:
            return value
    # Floating-point fallback: return the last element.
    return choices[-1][0]


def _pick_session(rng: Random, asset_class: str, sessions: Sequence[SessionRow]) -> SessionRow:
    """Pick a session weighted by the instrument's asset class.

    Equities are restricted to ``regular``; FX rolls between ``regular``
    and ``after_hours``; crypto rolls across all three. Commodities use
    the equity rule (regular session dominates).
    """
    by_code = {s.session_code: s for s in sessions}
    if asset_class == "equity" or asset_class == "commodity":
        return by_code.get("regular", sessions[0])
    if asset_class == "fx":
        candidates: list[tuple[SessionRow, float]] = []
        if "regular" in by_code:
            candidates.append((by_code["regular"], 0.7))
        if "after_hours" in by_code:
            candidates.append((by_code["after_hours"], 0.3))
        if not candidates:
            return sessions[0]
        return _weighted_choice(rng, candidates)
    # crypto: uniform across whatever sessions exist.
    return rng.choice(list(sessions))


def _pick_order_type(rng: Random, order_types: Sequence[OrderTypeRow]) -> OrderTypeRow:
    """Pick an order type with a 70/20/10 market/limit/stop split."""
    by_code = {ot.order_type_code: ot for ot in order_types}
    candidates: list[tuple[OrderTypeRow, float]] = []
    if "market" in by_code:
        candidates.append((by_code["market"], _MARKET_ORDER_WEIGHT))
    if "limit" in by_code:
        candidates.append((by_code["limit"], _LIMIT_ORDER_WEIGHT))
    if "stop" in by_code:
        candidates.append((by_code["stop"], _STOP_ORDER_WEIGHT))
    if not candidates:
        return order_types[0]
    return _weighted_choice(rng, candidates)


def _draw_quantity(rng: Random, asset_class: str) -> Decimal:
    """Draw a quantity sized to the instrument's asset class."""
    if asset_class == "equity":
        return Decimal(rng.randint(10, 500)).quantize(_QTY_INT)
    if asset_class == "fx":
        return Decimal(rng.randint(1000, 50000)).quantize(_QTY_INT)
    if asset_class == "crypto":
        raw = rng.uniform(0.01, 5.0)
        return Decimal(str(raw)).quantize(_QTY_CRYPTO, rounding=ROUND_HALF_UP)
    if asset_class == "commodity":
        return Decimal(rng.randint(1, 50)).quantize(_QTY_INT)
    msg = f"Unhandled asset_class={asset_class!r}"
    raise ValueError(msg)


def _draw_entry_price(rng: Random, symbol: str) -> Decimal:
    """Return a Gaussian-perturbed entry price quantised to 6 decimals."""
    base = _BASE_PRICES.get(symbol.upper(), _FALLBACK_BASE_PRICE)
    factor = rng.gauss(1.0, _ENTRY_PRICE_SIGMA)
    # Keep prices strictly positive even on heavy-tail draws.
    factor = max(factor, 0.5)
    raw = base * Decimal(str(factor))
    return raw.quantize(_PRICE_QUANTISE, rounding=ROUND_HALF_UP)


def _draw_entry_time(
    rng: Random,
    trade_date: date,
    session: SessionRow,
) -> datetime:
    """Return a tz-aware Europe/Bucharest timestamp inside the session window."""
    start_minutes = session.start_time_local.hour * 60 + session.start_time_local.minute
    end_minutes = session.end_time_local.hour * 60 + session.end_time_local.minute
    # Pad by one minute on either end so the exit (which can extend past
    # the window) still resolves cleanly inside the calendar day.
    minute_offset = rng.randint(start_minutes, max(start_minutes, end_minutes - 1))
    hour, minute = divmod(minute_offset, 60)
    second = rng.randint(0, 59)
    naive = datetime.combine(trade_date, time(hour=hour, minute=minute, second=second))
    return naive.replace(tzinfo=_TZ_BUCHAREST)


def _draw_holding(rng: Random) -> timedelta:
    """Draw an exponential holding time clamped to [5, 480] minutes."""
    raw_minutes = rng.expovariate(1.0 / _HOLDING_MEAN_MINUTES)
    clamped = max(_HOLDING_MIN_MINUTES, min(_HOLDING_MAX_MINUTES, int(round(raw_minutes))))
    return timedelta(minutes=clamped)


def _close_trade(
    rng: Random,
    *,
    side: Literal["B", "S"],
    quantity: Decimal,
    price_entry: Decimal,
    asset_class: str,
    quote_currency: str,
    trade_date: date,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Compute closing fields for a trade marked as closed.

    Returns:
        ``(price_exit, fx_rate_to_eur, gross_pnl_eur, commission_eur,
        net_pnl_eur)``. All Decimals already quantised to their target
        precisions.
    """
    drift = _BUY_DRIFT if side == "B" else _SELL_DRIFT
    epsilon = rng.gauss(drift, _RETURN_SIGMA)
    price_exit_raw = price_entry * (Decimal("1") + Decimal(str(epsilon)))
    price_exit = price_exit_raw.quantize(_PRICE_QUANTISE, rounding=ROUND_HALF_UP)

    direction = Decimal("1") if side == "B" else Decimal("-1")
    gross_pnl_quote = (price_exit - price_entry) * quantity * direction
    fx_rate = get_fx_rate(quote_currency, trade_date)
    gross_pnl_eur = (gross_pnl_quote * fx_rate).quantize(_PNL_QUANTISE, rounding=ROUND_HALF_UP)
    commission_eur = compute_commission(asset_class, quantity, price_entry, fx_rate)
    net_pnl_eur = (gross_pnl_eur - commission_eur).quantize(_PNL_QUANTISE, rounding=ROUND_HALF_UP)
    return price_exit, fx_rate, gross_pnl_eur, commission_eur, net_pnl_eur


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_for_date(
    trade_date: date,
    *,
    traders: Sequence[TraderProfile],
    markets: Sequence[MarketRow],
    sessions: Sequence[SessionRow],
    order_types: Sequence[OrderTypeRow],
) -> list[TradeRow]:
    """Produce a full day's synthetic trades for the given inputs.

    The function is pure: it touches no global state, performs no I/O, and
    is deterministic — the same ``(trade_date, traders, markets, sessions,
    order_types)`` returns an identical list every time.

    Args:
        trade_date: Business date the trades are stamped against (used both
            as the RNG seed material and as the date portion of the
            generated ``time_entry`` timestamps).
        traders: Active trading-eligible employees. Order is preserved and
            determines the iteration order, so callers should sort the
            sequence before calling for reproducibility.
        markets: Catalogue of instruments; only rows with ``is_active=True``
            are eligible for selection.
        sessions: Trading sessions; the asset-class-to-session weighting
            assumes the standard pre_market / regular / after_hours triple.
        order_types: Order types; the 70/20/10 split assumes at least
            ``market``, ``limit``, and ``stop`` codes are present.

    Returns:
        A list of `TradeRow` instances, one row per generated trade. Each
        ``trade_uid`` is unique within the list and follows the
        ``T<YYYYMMDD>-<NNNN>`` format where ``NNNN`` is a 4-digit running
        sequence starting at 0001.
    """
    rng = Random(seed_for_date(trade_date, suffix="trades"))
    active_markets = [m for m in markets if m.is_active]
    if not active_markets:
        return []
    if not sessions or not order_types:
        return []

    rows: list[TradeRow] = []
    seq = 0
    date_token = trade_date.strftime("%Y%m%d")
    for trader in traders:
        n_trades = _poisson_clamped(
            rng, _POISSON_LAMBDA, _MIN_TRADES_PER_TRADER, _MAX_TRADES_PER_TRADER
        )
        for _ in range(n_trades):
            seq += 1
            market = rng.choice(active_markets)
            session = _pick_session(rng, market.asset_class, sessions)
            order_type = _pick_order_type(rng, order_types)
            side: Literal["B", "S"] = "B" if rng.random() < 0.5 else "S"
            quantity = _draw_quantity(rng, market.asset_class)
            price_entry = _draw_entry_price(rng, market.symbol)
            time_entry = _draw_entry_time(rng, trade_date, session)

            is_open = rng.random() < _OPEN_TRADE_PROBABILITY

            if is_open:
                # Commission is still paid at entry. fx_rate_to_eur and
                # gross/net pnl stay NULL until the trade closes.
                fx_rate_for_commission = get_fx_rate(market.quote_currency, trade_date)
                commission_eur = compute_commission(
                    market.asset_class, quantity, price_entry, fx_rate_for_commission
                )
                rows.append(
                    TradeRow(
                        trade_uid=f"T{date_token}-{seq:04d}",
                        trader_id=trader.trader_id,
                        account_id=trader.account_id,
                        market_id=market.market_id,
                        session_id=session.session_id,
                        order_type_id=order_type.order_type_id,
                        side=side,
                        quantity=quantity,
                        price_entry=price_entry,
                        price_exit=None,
                        time_entry=time_entry,
                        time_exit=None,
                        gross_pnl_eur=None,
                        commission_eur=commission_eur,
                        net_pnl_eur=None,
                        is_open=True,
                        fx_rate_to_eur=None,
                    )
                )
                continue

            holding = _draw_holding(rng)
            time_exit = time_entry + holding
            # MA-02 (python-pro review): clamp time_exit to the same calendar
            # day in Europe/Bucharest so the daily-generator semantics are
            # preserved (`time_exit::date == trade_date_ro`). A draw of up to
            # 480 minutes from a 21:59 entry would otherwise spill into the
            # next local day.
            end_of_day = datetime.combine(
                trade_date, time(23, 59, 59), tzinfo=_TZ_BUCHAREST
            )
            if time_exit > end_of_day:
                time_exit = end_of_day
            price_exit, fx_rate, gross_pnl_eur, commission_eur, net_pnl_eur = _close_trade(
                rng,
                side=side,
                quantity=quantity,
                price_entry=price_entry,
                asset_class=market.asset_class,
                quote_currency=market.quote_currency,
                trade_date=trade_date,
            )
            rows.append(
                TradeRow(
                    trade_uid=f"T{date_token}-{seq:04d}",
                    trader_id=trader.trader_id,
                    account_id=trader.account_id,
                    market_id=market.market_id,
                    session_id=session.session_id,
                    order_type_id=order_type.order_type_id,
                    side=side,
                    quantity=quantity,
                    price_entry=price_entry,
                    price_exit=price_exit,
                    time_entry=time_entry,
                    time_exit=time_exit,
                    gross_pnl_eur=gross_pnl_eur,
                    commission_eur=commission_eur,
                    net_pnl_eur=net_pnl_eur,
                    is_open=False,
                    fx_rate_to_eur=fx_rate,
                )
            )
    return rows
