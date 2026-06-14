"""Unit tests for ``tcp.ai.anthropic_client`` — fully mocked, no network.

Verifies the four properties that pin the production contract:

1. The system block carries ``cache_control: {"type": "ephemeral"}`` so
   the prompt cache hits on subsequent calls.
2. The model id is ``claude-haiku-4-5`` (per ``CLAUDE.md``).
3. The sampling temperature is ``0.0`` (deterministic SQL emission).
4. The ``tool_use`` reply is parsed into a typed :class:`AskAnswer`,
   including the refusal path and the token-usage extraction.

A fifth test guards the ``max_input_tokens`` cap: oversized questions
are rejected client-side before paying for a round trip.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr, ValidationError

from tcp.ai.anthropic_client import (
    AnthropicClientError,
    AnthropicConfig,
    AnthropicUsage,
    AskAnswer,
    AskQuestion,
    MalformedAnthropicResponseError,
    PromptTooLargeError,
    ask_claude,
)
from tcp.ai.prompts import SCHEMA_SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_mock_response(
    *,
    payload: dict[str, Any] | None = None,
    include_tool_block: bool = True,
    tool_name: str = "emit_sql",
    input_tokens: int = 50,
    output_tokens: int = 80,
    cache_read: int = 3500,
    cache_write: int = 0,
) -> MagicMock:
    """Build a MagicMock that mimics ``anthropic.types.Message``.

    Mirrors only the attributes ``_parse_tool_use`` and ``_parse_usage``
    actually read — the rest of the SDK envelope is irrelevant for unit
    tests.
    """
    payload = payload if payload is not None else {
        "sql": "SELECT TOP 7 * FROM v_employee_performance",
        "answer_template": "Iată ultimele {row_count} zile.",
        "citation": "v_employee_performance, last 7 days",
        "refused": False,
        "refusal_reason": "",
    }
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = tool_name
    tool_block.input = payload

    response = MagicMock()
    response.content = [tool_block] if include_tool_block else []
    response.usage = MagicMock(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )
    return response


def _build_mock_client(response: MagicMock) -> MagicMock:
    """Build a mock ``anthropic.Anthropic`` instance whose .messages.create returns ``response``."""
    client = MagicMock()
    client.messages.create.return_value = response
    return client


# ---------------------------------------------------------------------------
# AnthropicConfig
# ---------------------------------------------------------------------------


class TestAnthropicConfig:
    def test_defaults_pin_haiku_and_temperature(self) -> None:
        cfg = AnthropicConfig(api_key=SecretStr("k-test"))
        assert cfg.model == "claude-haiku-4-5"
        assert cfg.temperature == 0.0
        assert cfg.max_input_tokens == 2000
        assert cfg.max_output_tokens == 600

    def test_from_env_requires_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(AnthropicClientError):
            AnthropicConfig.from_env()

    def test_from_env_reads_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.com")

        cfg = AnthropicConfig.from_env()

        assert cfg.api_key.get_secret_value() == "sk-ant-test"
        assert cfg.model == "claude-haiku-4-5"
        assert cfg.base_url == "https://example.com"

    def test_temperature_clamped_to_unit_range(self) -> None:
        with pytest.raises(ValidationError):
            AnthropicConfig(api_key=SecretStr("k"), temperature=1.5)


# ---------------------------------------------------------------------------
# ask_claude — happy path
# ---------------------------------------------------------------------------


class TestAskClaudeHappyPath:
    def test_returns_parsed_answer(self) -> None:
        response = _build_mock_response()
        client = _build_mock_client(response)
        cfg = AnthropicConfig(api_key=SecretStr("k-test"))

        answer = ask_claude(
            AskQuestion(question="Cum am performat săptămâna asta?", scope="trader"),
            config=cfg,
            client=client,
        )

        assert isinstance(answer, AskAnswer)
        assert answer.refused is False
        assert "SELECT" in answer.sql.upper()
        assert answer.citation
        assert isinstance(answer.usage, AnthropicUsage)
        assert answer.usage.input_tokens == 50
        assert answer.usage.output_tokens == 80
        assert answer.usage.cache_read_tokens == 3500

    def test_system_block_carries_cache_control_ephemeral(self) -> None:
        """The schema chunk is the cache anchor — drift here loses the discount."""
        response = _build_mock_response()
        client = _build_mock_client(response)
        cfg = AnthropicConfig(api_key=SecretStr("k-test"))

        ask_claude(
            AskQuestion(question="hello", scope="admin"),
            config=cfg,
            client=client,
        )

        call_kwargs = client.messages.create.call_args.kwargs
        system_blocks = call_kwargs["system"]
        assert isinstance(system_blocks, list)
        assert len(system_blocks) == 1
        assert system_blocks[0]["type"] == "text"
        assert system_blocks[0]["text"] == SCHEMA_SYSTEM_PROMPT
        assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}

    def test_model_is_haiku_and_temperature_is_zero(self) -> None:
        response = _build_mock_response()
        client = _build_mock_client(response)
        cfg = AnthropicConfig(api_key=SecretStr("k-test"))

        ask_claude(
            AskQuestion(question="hello", scope="admin"),
            config=cfg,
            client=client,
        )

        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-haiku-4-5"
        assert kwargs["temperature"] == 0.0
        assert kwargs["max_tokens"] == cfg.max_output_tokens

    def test_tool_choice_forces_emit_sql(self) -> None:
        response = _build_mock_response()
        client = _build_mock_client(response)
        cfg = AnthropicConfig(api_key=SecretStr("k-test"))

        ask_claude(
            AskQuestion(question="hello", scope="admin"),
            config=cfg,
            client=client,
        )

        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["tool_choice"] == {"type": "tool", "name": "emit_sql"}
        assert len(kwargs["tools"]) == 1
        assert kwargs["tools"][0]["name"] == "emit_sql"

    def test_user_message_includes_scope(self) -> None:
        """The scope is appended so the model phrases the answer correctly."""
        response = _build_mock_response()
        client = _build_mock_client(response)
        cfg = AnthropicConfig(api_key=SecretStr("k-test"))

        ask_claude(
            AskQuestion(question="show top 5", scope="team_lead"),
            config=cfg,
            client=client,
        )

        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["messages"][0]["role"] == "user"
        assert "team_lead" in kwargs["messages"][0]["content"]
        assert "show top 5" in kwargs["messages"][0]["content"]


# ---------------------------------------------------------------------------
# Refusal path
# ---------------------------------------------------------------------------


class TestAskClaudeRefusal:
    def test_refused_response_propagates(self) -> None:
        payload = {
            "sql": "",
            "answer_template": "",
            "citation": "",
            "refused": True,
            "refusal_reason": "Solicitarea iese din scopul asistentului.",
        }
        response = _build_mock_response(payload=payload)
        client = _build_mock_client(response)
        cfg = AnthropicConfig(api_key=SecretStr("k-test"))

        answer = ask_claude(
            AskQuestion(question="What's the IBAN?", scope="trader"),
            config=cfg,
            client=client,
        )

        assert answer.refused is True
        assert answer.sql == ""
        assert "scopul" in answer.refusal_reason


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestAskClaudeErrorPaths:
    def test_prompt_too_large_is_rejected(self) -> None:
        """The cap rejects an oversized wrapped user-message before the SDK call.

        The token estimate is ``len(message) // 4``. With ``max_input_tokens``
        set to ``64``, a 400-character question (~100 tokens after the
        scope wrapper is appended) exceeds the cap and raises.
        """
        small_cfg = AnthropicConfig(api_key=SecretStr("k-test"), max_input_tokens=64)
        client = _build_mock_client(_build_mock_response())

        with pytest.raises(PromptTooLargeError):
            ask_claude(
                AskQuestion(question="x" * 400, scope="admin"),
                config=small_cfg,
                client=client,
            )

        # Sanity: a short question fits well under the same small cap.
        answer = ask_claude(
            AskQuestion(question="hi", scope="admin"),
            config=small_cfg,
            client=client,
        )
        # Pin a non-trivial invariant on the result so a regression that
        # silently turned this call into a refusal would trip the test
        # (py MN-04).
        assert answer.sql.upper().startswith("SELECT")

    def test_missing_tool_block_raises(self) -> None:
        response = _build_mock_response(include_tool_block=False)
        client = _build_mock_client(response)
        cfg = AnthropicConfig(api_key=SecretStr("k-test"))

        with pytest.raises(MalformedAnthropicResponseError):
            ask_claude(
                AskQuestion(question="hello", scope="admin"),
                config=cfg,
                client=client,
            )

    def test_unexpected_tool_name_raises(self) -> None:
        response = _build_mock_response(tool_name="emit_something_else")
        client = _build_mock_client(response)
        cfg = AnthropicConfig(api_key=SecretStr("k-test"))

        with pytest.raises(MalformedAnthropicResponseError):
            ask_claude(
                AskQuestion(question="hello", scope="admin"),
                config=cfg,
                client=client,
            )
