"""Unit tests for tcp.synth.commissions."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tcp.synth.commissions import compute_commission


def test_equity_per_unit_formula() -> None:
    # 200 shares at 150 USD with fx 0.91 -> commission ignores fx for equities.
    result = compute_commission(
        "equity", Decimal("200"), Decimal("150.00"), Decimal("0.91")
    )
    assert result == Decimal("1.0000")


def test_commodity_per_unit_formula() -> None:
    result = compute_commission(
        "commodity", Decimal("50"), Decimal("2050.00"), Decimal("0.91")
    )
    assert result == Decimal("0.5000")


def test_fx_notional_formula() -> None:
    # 1000 * 1.08 * 0.91 = 982.8 notional EUR; * 0.00002 = 0.019656.
    result = compute_commission(
        "fx", Decimal("1000"), Decimal("1.08"), Decimal("0.91")
    )
    assert result == Decimal("0.0197")  # rounded to 4 dp HALF_UP


def test_crypto_notional_formula() -> None:
    # 1 BTC at 95000 USD, fx 0.91 -> notional EUR = 86450; * 0.001 = 86.45.
    result = compute_commission(
        "crypto", Decimal("1"), Decimal("95000"), Decimal("0.91")
    )
    assert result == Decimal("86.4500")


def test_unknown_asset_class_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown asset_class"):
        compute_commission("bonds", Decimal("100"), Decimal("100"), Decimal("1"))


@pytest.mark.parametrize("asset_class", ["equity", "fx", "crypto", "commodity"])
def test_commission_is_quantised_to_4_decimals(asset_class: str) -> None:
    result = compute_commission(
        asset_class, Decimal("7"), Decimal("1.234567"), Decimal("0.910001")
    )
    # The quantum is 0.0001; the exponent of the resulting Decimal must be -4.
    # `Decimal.as_tuple().exponent` is typed as `Literal['n','N','F'] | int`
    # to cover NaN / sNaN / Infinity; `compute_commission` always returns a
    # finite Decimal, so the int cast is sound and silences a stdlib-typing
    # `operator` error (Etapa-11 code11-MJ-02 fallout).
    exponent = result.as_tuple().exponent
    assert isinstance(exponent, int), f"unexpected non-finite Decimal: {result}"
    assert -exponent == 4


def test_commission_non_negative_for_all_classes() -> None:
    for asset_class in ("equity", "fx", "crypto", "commodity"):
        result = compute_commission(
            asset_class, Decimal("10"), Decimal("100"), Decimal("0.91")
        )
        assert result >= Decimal("0")
