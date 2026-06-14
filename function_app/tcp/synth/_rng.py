"""Deterministic seed derivation for the synthetic-data generator.

All RNG-consuming code paths in `tcp.synth` derive their seeds through
`seed_for_date` so that running `generate_for_date(d)` twice on the same
date yields byte-for-byte identical output. The seed is computed from a
SHA-256 of an explicit byte representation of the trade date plus an
optional suffix (used to derive independent streams for distinct concerns
inside a single date).
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Final

# 8 bytes = 64-bit seed space, which fits comfortably in Python's `int` and
# in `random.Random(seed)` / `numpy.random.default_rng(seed)` initialisers.
_SEED_BYTES: Final[int] = 8


def seed_for_date(trade_date: date, *, suffix: str = "") -> int:
    """Return a stable, non-negative 64-bit seed derived from a date and suffix.

    The seed is computed as the leading 8 bytes of
    ``sha256(f"tcp.synth|{trade_date.isoformat()}|{suffix}")`` interpreted as
    an unsigned big-endian integer. The result is fully deterministic across
    Python versions and operating systems — it depends only on the SHA-256
    implementation, which is part of the CPython standard library.

    Args:
        trade_date: The business date this seed is keyed against.
        suffix: An optional discriminator that lets callers derive multiple
            independent random streams from the same date (e.g.
            ``suffix="fx"`` vs ``suffix="trades"``).

    Returns:
        A non-negative integer suitable for ``random.Random(seed)`` and
        ``numpy.random.default_rng(seed)``.
    """
    material = f"tcp.synth|{trade_date.isoformat()}|{suffix}".encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return int.from_bytes(digest[:_SEED_BYTES], "big", signed=False)
