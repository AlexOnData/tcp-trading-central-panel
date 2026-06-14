"""Per-asset-class commission model for synthetic trades.

Constants and formulas track the OQ-02 contract from
`docs/design/01_business_requirements.md` §12:

- ``equities``:    ``0.005 EUR/unit``  (per-share commission, USD->EUR neutral).
- ``fx``:          ``0.00002 * notional_eur``.
- ``crypto``:      ``0.001  * notional_eur``.
- ``commodities``: ``0.01 EUR/unit``   (per-contract commission).

Notional is computed in EUR (``quantity * price_entry * fx_rate_to_eur``)
because the rate-card constants are expressed as EUR fractions of the
EUR-denominated notional. Equity and commodity commissions are
unit-priced and EUR-denominated by convention, so the FX rate does not
enter their formulas.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Final

# Persisted PnL columns are `DECIMAL(18,4)`; we quantise to 4 dp throughout
# the generator so what the proc inserts matches what Python computed.
_COMMISSION_QUANTISE: Final[Decimal] = Decimal("0.0001")

_EQUITY_PER_UNIT_EUR: Final[Decimal] = Decimal("0.005")
_FX_NOTIONAL_RATE: Final[Decimal] = Decimal("0.00002")
_CRYPTO_NOTIONAL_RATE: Final[Decimal] = Decimal("0.001")
_COMMODITY_PER_UNIT_EUR: Final[Decimal] = Decimal("0.01")


def compute_commission(
    asset_class: str,
    quantity: Decimal,
    price_entry: Decimal,
    fx_rate_to_eur: Decimal,
) -> Decimal:
    """Return the EUR commission for a single trade.

    The commission depends on the instrument's asset class. Equity and
    commodity classes use a per-unit rate (EUR/unit) and ignore the FX
    argument; FX and crypto use a fraction of the EUR-denominated notional.

    Args:
        asset_class: One of ``'equity'``, ``'fx'``, ``'crypto'``,
            ``'commodity'``. Case-sensitive — the values match
            ``dim_Markets.asset_class``.
        quantity: Trade quantity (units / lots / contracts), positive.
        price_entry: Entry price in the symbol's quote currency, positive.
        fx_rate_to_eur: Rate converting one unit of the quote currency to
            EUR; ``Decimal('1.0')`` for EUR-denominated instruments. Only
            used for the ``fx`` and ``crypto`` formulas.

    Returns:
        Commission in EUR, quantised to 4 decimal places.

    Raises:
        ValueError: If ``asset_class`` is not a recognised value.
    """
    match asset_class:
        case "equity":
            commission = _EQUITY_PER_UNIT_EUR * quantity
        case "fx":
            commission = _FX_NOTIONAL_RATE * (quantity * price_entry * fx_rate_to_eur)
        case "crypto":
            commission = _CRYPTO_NOTIONAL_RATE * (quantity * price_entry * fx_rate_to_eur)
        case "commodity":
            commission = _COMMODITY_PER_UNIT_EUR * quantity
        case _:
            msg = (
                f"Unknown asset_class={asset_class!r}; expected one of "
                "'equity', 'fx', 'crypto', 'commodity'."
            )
            raise ValueError(msg)
    return commission.quantize(_COMMISSION_QUANTISE, rounding=ROUND_HALF_UP)
