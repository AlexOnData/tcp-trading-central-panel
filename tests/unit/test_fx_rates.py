"""Unit tests for tcp.synth.fx_rates."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tcp.synth.fx_rates import BASE_FX_RATES, compute_wobble, get_fx_rate


def test_eur_returns_exact_one() -> None:
    assert get_fx_rate("EUR", date(2026, 5, 14)) == Decimal("1.0")
    # Case insensitivity.
    assert get_fx_rate("eur", date(2026, 5, 14)) == Decimal("1.0")


def test_get_fx_rate_is_deterministic() -> None:
    assert get_fx_rate("USD", date(2026, 5, 14)) == get_fx_rate("USD", date(2026, 5, 14))


def test_rate_changes_across_dates() -> None:
    a = get_fx_rate("USD", date(2026, 5, 14))
    b = get_fx_rate("USD", date(2026, 5, 15))
    # The wobble may collide for some date pairs; pick a span where
    # collision is astronomically unlikely.
    found_different = False
    for offset in range(1, 30):
        if get_fx_rate("USD", date.fromordinal(date(2026, 5, 14).toordinal() + offset)) != a:
            found_different = True
            break
    assert found_different
    assert a == a  # sanity
    assert b == b


@pytest.mark.parametrize("ccy", sorted(c for c in BASE_FX_RATES if c != "EUR"))
def test_wobble_bounds_per_currency(ccy: str) -> None:
    base = BASE_FX_RATES[ccy]
    lower = (base * (Decimal("1") - Decimal("0.005"))).quantize(Decimal("0.00000001"))
    upper = (base * (Decimal("1") + Decimal("0.005"))).quantize(Decimal("0.00000001"))
    # Sample 200 distinct dates across a year; every result must fall inside
    # the ±0.5 % envelope.
    for offset in range(0, 365, 2):
        d = date.fromordinal(date(2026, 1, 1).toordinal() + offset)
        rate = get_fx_rate(ccy, d)
        assert lower <= rate <= upper, f"{ccy} on {d}: {rate} not in [{lower}, {upper}]"


def test_unknown_currency_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        get_fx_rate("XXX", date(2026, 5, 14))


def test_wobble_within_explicit_bounds() -> None:
    for ccy in ("USD", "JPY", "CHF", "GBP", "RON"):
        for offset in range(0, 60):
            d = date.fromordinal(date(2026, 1, 1).toordinal() + offset)
            wobble = compute_wobble(ccy, d)
            assert Decimal("-0.005") <= wobble <= Decimal("0.005")


def test_rates_are_positive() -> None:
    for ccy in BASE_FX_RATES:
        if ccy == "EUR":
            continue
        rate = get_fx_rate(ccy, date(2026, 5, 14))
        assert rate > Decimal("0")
