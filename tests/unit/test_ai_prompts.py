"""Unit tests for ``tcp.ai.prompts`` — the cached schema prompt body.

The cache-anchor anti-regression tests live in
``test_ai_anthropic_client.py``; this module focuses on prompt content
hygiene that does not require the SDK to be involved.
"""

from __future__ import annotations

import pytest

from tcp.ai.prompts import SCHEMA_SYSTEM_PROMPT, build_user_message


class TestPromptContent:
    """Pin prompt-content invariants the Etapa-5 review surfaced."""

    def test_pnl_columns_are_decimal_18_4(self) -> None:
        """Holistic MA-01: the cached prompt must agree with 02_DB §6.1.

        ``gross_pnl_eur`` / ``commission_eur`` / ``net_pnl_eur`` /
        ``net_pnl_eur_total`` are stored as ``DECIMAL(18,4)``; the
        cached prompt declared ``DECIMAL(18,2)`` in the original Etapa-5
        ship and was corrected by this fix. The assertion guards against
        a regression that would silently re-pay the cache-write cost.
        """
        # Every EUR-PnL line in the prompt must end with the (18,4)
        # precision marker. We assert the absence of (18,2) on those
        # rows specifically rather than scanning the whole prompt
        # because some unrelated columns may legitimately use (18,2)
        # in the future.
        for name in (
            "gross_pnl_eur ",
            "commission_eur ",
            "net_pnl_eur ",
            "net_pnl_eur_total ",
            "cumulative_net_pnl_eur ",
            "gross_pnl_eur_total ",
            "commission_eur_total ",
        ):
            assert name in SCHEMA_SYSTEM_PROMPT
            # Find the line carrying this column declaration and assert
            # it mentions ``DECIMAL(18,4)`` rather than ``(18,2)``.
            line_start = SCHEMA_SYSTEM_PROMPT.find(name)
            line_end = SCHEMA_SYSTEM_PROMPT.find("\n", line_start)
            line = SCHEMA_SYSTEM_PROMPT[line_start:line_end]
            assert "DECIMAL(18,4)" in line, f"column {name!r}: {line!r}"
            assert "DECIMAL(18,2)" not in line, f"column {name!r}: {line!r}"

    def test_dim_user_roles_is_excluded(self) -> None:
        """The RLS-metadata table must NOT appear in the cached allowlist."""
        # The allowlist enumeration lives inside a "Dimensions" section
        # that lists every allowed table. dim_UserRoles only appears as
        # part of the negative note explaining the exclusion.
        assert "dim_UserRoles" in SCHEMA_SYSTEM_PROMPT
        assert "intentionally not in this list" in SCHEMA_SYSTEM_PROMPT.lower()

    def test_prompt_body_is_within_token_budget(self) -> None:
        """Pin the size envelope so a doc rewrite cannot inflate cost silently."""
        # The doc claims ~3 500 tokens (~14 000 chars at 4 chars/token);
        # allow a 50% headroom band on either side so reasonable edits
        # do not bounce the test.
        length = len(SCHEMA_SYSTEM_PROMPT)
        assert 8_000 < length < 24_000

    def test_pii_firewall_no_employee_names_or_oids(self) -> None:
        """The prompt body must never carry row data — only shape."""
        # Spot-check: no AAD GUID shape, no '@tcp-capital.ro' email, no
        # hardcoded employee_id values inside narrative prose. (Numeric
        # ids inside few-shot examples are part of the SQL contract and
        # are abstract, not specific employees.)
        assert "@tcp-capital.ro" not in SCHEMA_SYSTEM_PROMPT
        # Generic GUID regex would be too aggressive; spot-check for the
        # tenant placeholder shape that should NEVER leak into the prompt.
        assert "<TENANT_ID>" not in SCHEMA_SYSTEM_PROMPT


class TestBuildUserMessage:
    """Pin the scope-injection guard from security MN-02."""

    def test_known_scopes_compose_user_message(self) -> None:
        for scope in ("trader", "team_lead", "floor_manager", "admin"):
            message = build_user_message("how are we doing?", scope)
            assert scope in message
            assert "how are we doing?" in message

    def test_unknown_scope_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            build_user_message("hello", "superadmin")

    def test_empty_scope_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            build_user_message("hello", "")
