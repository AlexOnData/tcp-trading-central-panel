"""Deterministic per-(quote_currency, trade_date) FX-rate table.

The synthetic generator must convert each non-EUR trade's gross PnL into
EUR so `fact_Trades.gross_pnl_eur` and `net_pnl_eur` carry the firm's
reporting currency. ADR per `docs/design/01_business_requirements.md`
§10 item 4 (MA-05 follow-up) calls for a deterministic table — not a
runtime feed — that yields the same rate when the same `(symbol,
trade_date)` is replayed.

This module implements that table as a static set of base rates (as of
2026-01-01) plus a small, deterministic per-date wobble derived from a
SHA-256 hash of the `(quote_currency, trade_date)` pair. The wobble lies
in [-0.5 %, +0.5 %] of the base rate, which is realistic for
short-horizon currency variation without introducing chart-breaking
jumps.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import Final

# Per OQ-02 / business-requirements §10, the supported quote currencies are
# USD (US equities, most FX, all crypto, most commodities), JPY (USDJPY,
# EURJPY), CHF (USDCHF), GBP (GBPUSD, EURGBP), RON (USDRON), and EUR (no
# conversion). Anything outside this set is a configuration error.
BASE_FX_RATES: Final[dict[str, Decimal]] = {
    "EUR": Decimal("1.0000000"),
    "USD": Decimal("0.9100000"),
    "JPY": Decimal("0.0060000"),
    "CHF": Decimal("1.0500000"),
    "GBP": Decimal("1.1700000"),
    "RON": Decimal("0.2010000"),
}

# Wobble bounds: ±0.5 % expressed as Decimal. The hash byte (0..99) maps
# linearly onto [-_WOBBLE_BOUND, +_WOBBLE_BOUND]; the centre point (49.5)
# corresponds to 0 % wobble.
_WOBBLE_BOUND: Final[Decimal] = Decimal("0.005")
_WOBBLE_STEPS: Final[int] = 100
_QUANTISE: Final[Decimal] = Decimal("0.00000001")  # 8 decimal places.


def _compute_wobble(quote_currency: str, trade_date: date) -> Decimal:
    """Return the deterministic ±0.5 % wobble factor for the given pair.

    The factor is a multiplicative perturbation: the final rate equals
    ``base_rate * (Decimal('1') + wobble)``. A zero wobble (i.e. exactly the
    base rate) is possible but rare; the value lies in the closed interval
    ``[-Decimal('0.005'), +Decimal('0.005')]`` regardless of inputs.

    Args:
        quote_currency: ISO-4217 alpha-3 code; case-insensitive.
        trade_date: The business date the wobble is keyed against.

    Returns:
        A `Decimal` in ``[-0.005, +0.005]``.
    """
    material = f"fx|{quote_currency.upper()}|{trade_date.isoformat()}".encode("utf-8")
    digest = hashlib.sha256(material).digest()
    bucket = int.from_bytes(digest[:4], "big", signed=False) % _WOBBLE_STEPS
    # Map [0, 99] -> [-_WOBBLE_BOUND, +_WOBBLE_BOUND].
    # Step size = (2 * bound) / (steps - 1) so the endpoints are reached.
    step = (_WOBBLE_BOUND * Decimal(2)) / Decimal(_WOBBLE_STEPS - 1)
    return (-_WOBBLE_BOUND + step * Decimal(bucket)).quantize(_QUANTISE)


# Public alias for the wobble helper. Exposed for unit-testability of the
# bounds contract per python-pro review MA-04; the leading-underscore symbol
# is retained for back-compat callers but tests should prefer this name.
compute_wobble = _compute_wobble


def get_fx_rate(quote_currency: str, trade_date: date) -> Decimal:
    """Return the EUR conversion rate for one unit of ``quote_currency``.

    For ``quote_currency == 'EUR'`` the function returns ``Decimal('1.0')``
    unconditionally — no wobble is applied to the reporting currency itself.
    For all other supported currencies the result is
    ``BASE_FX_RATES[ccy] * (Decimal('1') + wobble)`` quantised to 8 decimal
    places, matching the precision of ``fact_Trades.fx_rate_to_eur``.

    Args:
        quote_currency: ISO-4217 alpha-3 code (case-insensitive).
        trade_date: The business date the rate is keyed against.

    Returns:
        A positive `Decimal` rate such that ``amount_in_quote * rate`` is
        the EUR-equivalent amount on ``trade_date``.

    Raises:
        KeyError: If ``quote_currency`` is not in ``BASE_FX_RATES``.
    """
    code = quote_currency.upper()
    if code == "EUR":
        return Decimal("1.0")
    base = BASE_FX_RATES[code]
    wobble = _compute_wobble(code, trade_date)
    rate = base * (Decimal("1") + wobble)
    return rate.quantize(_QUANTISE)
