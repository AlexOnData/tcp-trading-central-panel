"""Unit tests for the ``ask`` trigger module helpers.

The trigger entrypoint itself is exercised by
``tests/integration/test_ask_endpoint.py`` against live SQL + a mocked
Anthropic SDK; this module covers helpers that do not need either —
the JSON encoder, the rate-limit ledger, and the cell formatter.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from function_app.triggers import ask as ask_module
from function_app.triggers.ask import (
    _RATE_LIMIT_BUCKETS,
    _RATE_LIMIT_MAX_REQUESTS,
    _RATE_LIMIT_WINDOW_SECONDS,
    _TcpJsonEncoder,
    _check_and_record_rate_limit,
    _format_cell,
    _render_answer,
)
from tcp import safe_query

_OID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


@pytest.fixture(autouse=True)
def _clear_buckets() -> None:
    """Reset the in-process rate-limit ledger before every test."""
    _RATE_LIMIT_BUCKETS.clear()


# ---------------------------------------------------------------------------
# JSON encoder
# ---------------------------------------------------------------------------


class TestTcpJsonEncoder:
    """Closes py MJ-03 / hol mi-07 — stable typed-value serialisation."""

    def test_decimal_serialises_to_float(self) -> None:
        body = json.dumps({"v": Decimal("1234.5678")}, cls=_TcpJsonEncoder)
        # Float round-trip preserves the value within the EUR display
        # precision required by the SWA (Intl.NumberFormat at 2 frac digits).
        assert json.loads(body) == {"v": 1234.5678}

    def test_datetime_serialises_to_iso_with_offset(self) -> None:
        ts = datetime(2026, 5, 14, 7, 0, tzinfo=timezone.utc)
        body = json.dumps({"v": ts}, cls=_TcpJsonEncoder)
        assert json.loads(body) == {"v": "2026-05-14T07:00:00+00:00"}

    def test_date_serialises_to_iso(self) -> None:
        """SQL DATE columns (``trade_date_ro``) flow through pyodbc as ``date``."""
        body = json.dumps({"v": date(2026, 5, 14)}, cls=_TcpJsonEncoder)
        assert json.loads(body) == {"v": "2026-05-14"}

    def test_uuid_serialises_to_canonical_string(self) -> None:
        body = json.dumps({"v": _OID}, cls=_TcpJsonEncoder)
        assert json.loads(body) == {"v": str(_OID)}

    def test_unknown_type_raises(self) -> None:
        class Unsupported:
            pass

        with pytest.raises(TypeError):
            json.dumps({"v": Unsupported()}, cls=_TcpJsonEncoder)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimit:
    """Closes security MJ-02 — 10 questions / 60 s / oid in-process."""

    def test_first_n_requests_allowed(self) -> None:
        for _ in range(_RATE_LIMIT_MAX_REQUESTS):
            assert _check_and_record_rate_limit(_OID) is True

    def test_eleventh_request_blocked(self) -> None:
        for _ in range(_RATE_LIMIT_MAX_REQUESTS):
            _check_and_record_rate_limit(_OID)
        assert _check_and_record_rate_limit(_OID) is False

    def test_window_slides_so_old_entries_drop_off(self) -> None:
        """Past requests outside the window must not count toward the budget."""
        base = 1000.0
        for offset in range(_RATE_LIMIT_MAX_REQUESTS):
            assert _check_and_record_rate_limit(_OID, now=base + offset)
        # Inside the window: blocked.
        assert (
            _check_and_record_rate_limit(_OID, now=base + _RATE_LIMIT_MAX_REQUESTS - 1)
            is False
        )
        # Outside the window: allowed again.
        assert (
            _check_and_record_rate_limit(
                _OID, now=base + _RATE_LIMIT_WINDOW_SECONDS + 1
            )
            is True
        )

    def test_different_oids_have_independent_budgets(self) -> None:
        other = UUID("11111111-2222-3333-4444-555555555555")
        for _ in range(_RATE_LIMIT_MAX_REQUESTS):
            _check_and_record_rate_limit(_OID)
        # The other user is still untouched.
        assert _check_and_record_rate_limit(other) is True


# ---------------------------------------------------------------------------
# Single source of truth for the row cap (hol MA-02)
# ---------------------------------------------------------------------------


def test_max_fetch_rows_matches_safe_query_constant() -> None:
    """``_MAX_FETCH_ROWS`` must be derived from ``safe_query.MAX_ROW_LIMIT``."""
    assert ask_module._MAX_FETCH_ROWS == safe_query.MAX_ROW_LIMIT  # noqa: SLF001


# ---------------------------------------------------------------------------
# Answer template rendering
# ---------------------------------------------------------------------------


def test_render_answer_substitutes_row_count_and_value() -> None:
    rendered = _render_answer(
        "Found {row_count} row, PnL = {value:net_pnl_eur_total}.",
        [{"net_pnl_eur_total": Decimal("1234.5678")}],
    )
    assert rendered.startswith("Found 1 row")
    # Decimal renders through _format_cell at 2 frac digits for display
    # consistency with the SWA-side EUR formatter.
    assert "1234.57" in rendered


def test_render_answer_handles_empty_rows() -> None:
    rendered = _render_answer(
        "Found {row_count} row(s).",
        [],
    )
    assert rendered == "Found 0 row(s)."


def test_format_cell_decimal_renders_two_decimals() -> None:
    assert _format_cell(Decimal("1234.5678")) == "1234.57"


def test_format_cell_none_renders_na() -> None:
    assert _format_cell(None) == "n/a"


def test_format_cell_bool_renders_lowercase() -> None:
    assert _format_cell(True) == "true"
    assert _format_cell(False) == "false"
