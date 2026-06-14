"""Unit tests for tcp.synth._rng.seed_for_date."""

from __future__ import annotations

from datetime import date

from tcp.synth._rng import seed_for_date


def test_seed_for_date_is_deterministic() -> None:
    assert seed_for_date(date(2026, 5, 14)) == seed_for_date(date(2026, 5, 14))


def test_seed_for_date_changes_with_date() -> None:
    assert seed_for_date(date(2026, 5, 14)) != seed_for_date(date(2026, 5, 15))


def test_seed_for_date_changes_with_suffix() -> None:
    base = seed_for_date(date(2026, 5, 14))
    assert seed_for_date(date(2026, 5, 14), suffix="trades") != base
    assert seed_for_date(date(2026, 5, 14), suffix="fx") != base
    assert (
        seed_for_date(date(2026, 5, 14), suffix="trades")
        != seed_for_date(date(2026, 5, 14), suffix="fx")
    )


def test_seed_for_date_is_non_negative_and_64_bit() -> None:
    seed = seed_for_date(date(2026, 5, 14))
    assert seed >= 0
    assert seed < 2**64


def test_seed_for_date_stable_value_across_python_versions() -> None:
    # Pinning a known seed so a future ruff/cpython upgrade can't silently
    # change the output. Computed on Python 3.12 against the canonical input.
    expected = int.from_bytes(
        __import__("hashlib").sha256(b"tcp.synth|2026-05-14|").digest()[:8],
        "big",
        signed=False,
    )
    assert seed_for_date(date(2026, 5, 14)) == expected
