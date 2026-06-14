"""Anthropic Claude SDK wrapper for the TCP AI assistant.

A thin layer over ``anthropic.Anthropic`` that:

- Hides the SDK-specific message and tool envelope from the trigger code.
- Pins the model to ``claude-haiku-4-5`` and the temperature to ``0.0``
  for deterministic SQL emission.
- Attaches ``cache_control: {"type": "ephemeral"}`` to the system block
  so the long schema context resolves from Anthropic's prompt cache on
  every subsequent call inside the TTL window.
- Parses the model's ``tool_use`` reply into a typed :class:`AskAnswer`,
  including the refusal path.
- Surfaces token usage (input / output / cache-read / cache-write) for
  downstream emission as App Insights custom metrics.

The configuration is read from environment variables so the Function App
binds ``ANTHROPIC_API_KEY`` via a Key Vault reference. The SDK client is
re-instantiated per request — the SDK manages its own HTTP connection
pool internally and the Function host keeps the Python worker warm
between invocations.
"""

from __future__ import annotations

import os
from typing import Any, Final, Literal, cast, get_args

import anthropic
from anthropic.types import Message
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from tcp.ai.prompts import SCHEMA_SYSTEM_PROMPT, build_user_message

_ANTHROPIC_DEFAULT_MODEL: Final[str] = "claude-haiku-4-5"
_ANTHROPIC_DEFAULT_BASE_URL: Final[str] = "https://api.anthropic.com"
_ANTHROPIC_TOOL_NAME: Final[str] = "emit_sql"

# Etapa-12 consolidation (closes code11-MI-05): single source of truth for the
# four RLS scopes. ``Scope`` is the Literal type used for static checking;
# ``ALLOWED_SCOPES`` is the runtime frozenset derived from it via
# ``typing.get_args`` so the two cannot drift. Re-exported for ``ask.py`` and
# any future caller that needs to validate a scope string at the boundary.
Scope = Literal["trader", "team_lead", "floor_manager", "admin"]
ALLOWED_SCOPES: Final[frozenset[str]] = frozenset(get_args(Scope))


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class AnthropicClientError(Exception):
    """Base class for any failure in this module that is not a refusal."""


class PromptTooLargeError(AnthropicClientError):
    """The user's question would exceed :attr:`AnthropicConfig.max_input_tokens`."""


class MalformedAnthropicResponseError(AnthropicClientError):
    """The SDK returned a response without the expected ``tool_use`` block."""


# ---------------------------------------------------------------------------
# Public Pydantic models
# ---------------------------------------------------------------------------


class AnthropicConfig(BaseModel):
    """Configuration for the Anthropic SDK call.

    Defaults match ``CLAUDE.md`` (``claude-haiku-4-5``, temperature 0,
    public API endpoint). The ``api_key`` is wrapped in
    :class:`pydantic.SecretStr` so accidental logging redacts the value.

    Attributes:
        api_key: The Anthropic API key, sourced from
            ``ANTHROPIC_API_KEY`` via a Key Vault reference in
            production.
        model: The Anthropic model id; pinned to ``claude-haiku-4-5``.
        base_url: HTTPS endpoint of the Anthropic API. Override only for
            tests and replay harnesses.
        max_input_tokens: Hard cap on the per-request input tokens that
            are *not* served from the prompt cache (i.e., the question
            plus the user-message wrapper). Requests larger than this
            cap raise :class:`PromptTooLargeError`.
        max_output_tokens: Hard cap on the model's reply tokens — used
            both as the ``max_tokens`` parameter and as a guard against
            runaway-cost questions.
        temperature: Sampling temperature. Pinned to ``0.0`` so the same
            question + cached schema yields the same SQL.
        timeout_seconds: Request timeout. The host trigger has its own
            outer timeout (the Function App's HTTP 230-second cap), so
            this is the inner per-call ceiling.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr
    model: str = _ANTHROPIC_DEFAULT_MODEL
    base_url: str = _ANTHROPIC_DEFAULT_BASE_URL
    max_input_tokens: int = Field(default=2000, ge=64, le=10_000)
    max_output_tokens: int = Field(default=600, ge=64, le=4096)
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    timeout_seconds: int = Field(default=30, ge=5, le=120)

    @classmethod
    def from_env(cls) -> AnthropicConfig:
        """Build a config from environment variables.

        Reads:

        - ``ANTHROPIC_API_KEY`` — required.
        - ``ANTHROPIC_MODEL`` — optional, defaults to
          :data:`_ANTHROPIC_DEFAULT_MODEL`.
        - ``ANTHROPIC_BASE_URL`` — optional, defaults to the public
          endpoint.

        Raises:
            AnthropicClientError: When ``ANTHROPIC_API_KEY`` is missing
                or empty.
        """
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            msg = "ANTHROPIC_API_KEY is not configured"
            raise AnthropicClientError(msg)
        return cls(
            api_key=SecretStr(key),
            model=os.environ.get("ANTHROPIC_MODEL", _ANTHROPIC_DEFAULT_MODEL),
            base_url=os.environ.get("ANTHROPIC_BASE_URL", _ANTHROPIC_DEFAULT_BASE_URL),
        )


class AskQuestion(BaseModel):
    """Inbound question payload after header / scope validation.

    Attributes:
        question: The raw natural-language question (Romanian or English),
            truncated to 500 characters by the HTTP trigger before
            reaching this layer.
        scope: The RLS scope resolved from ``dim_UserRoles``. The model
            uses the scope to phrase the answer; the actual data filter
            is enforced by SQL Server.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(..., min_length=1, max_length=500)
    scope: Scope


class AnthropicUsage(BaseModel):
    """Token-usage summary parsed from the SDK response.

    Attached to the trigger's HTTP response body so the SWA frontend can
    surface "cost" in development builds and so App Insights custom
    metrics can be emitted directly from this object.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class AskAnswer(BaseModel):
    """Parsed Anthropic reply.

    Attributes:
        sql: A single SELECT statement (the SQL validator gates this
            downstream).
        answer_template: Natural-language template with ``{value:col}``,
            ``{row_count}``, and ``{rows}`` placeholders.
        citation: Short description of the source view / proc and the
            filter applied (e.g., ``"v_floor_performance, last 7 days"``).
        refused: ``True`` when the model declined to answer.
        refusal_reason: Short explanation populated when ``refused`` is
            ``True``; empty string otherwise.
        usage: Token-usage breakdown for the call.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sql: str = ""
    answer_template: str = ""
    citation: str = ""
    refused: bool = False
    refusal_reason: str = ""
    usage: AnthropicUsage = AnthropicUsage()


# ---------------------------------------------------------------------------
# Tool schema (Anthropic JSON schema for ``emit_sql``)
# ---------------------------------------------------------------------------


_EMIT_SQL_TOOL: Final[dict[str, Any]] = {
    "name": _ANTHROPIC_TOOL_NAME,
    "description": (
        "Return a single read-only T-SQL SELECT statement, an answer "
        "template, and a citation. Set refused=true with a short "
        "refusal_reason for out-of-scope questions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "A single T-SQL SELECT statement against the allowlisted "
                    "views/dimensions/functions. Empty string when refused."
                ),
            },
            "answer_template": {
                "type": "string",
                "description": (
                    "A natural-language template. May contain {row_count}, "
                    "{value:<column>} and {rows} placeholders."
                ),
            },
            "citation": {
                "type": "string",
                "description": (
                    "Short source description: view name + filter "
                    "(e.g. 'v_employee_performance, last 7 days')."
                ),
            },
            "refused": {
                "type": "boolean",
                "description": "True if the model declines to answer.",
            },
            "refusal_reason": {
                "type": "string",
                "description": "Short reason in the user's language when refused.",
            },
        },
        "required": ["sql", "answer_template", "citation", "refused", "refusal_reason"],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ask_claude(
    question: AskQuestion,
    *,
    config: AnthropicConfig | None = None,
    client: anthropic.Anthropic | None = None,
) -> AskAnswer:
    """Issue one Anthropic call and return the parsed :class:`AskAnswer`.

    The system block is sent with ``cache_control: ephemeral`` attached
    so subsequent calls within the TTL window read it from the cache.
    The tool ``emit_sql`` constrains the model to the structured JSON
    envelope the trigger needs; we also set ``tool_choice`` to force the
    tool call rather than free-form text.

    Args:
        question: Validated :class:`AskQuestion` (length + scope checked
            upstream).
        config: Optional override of the per-request configuration.
            Defaults to :meth:`AnthropicConfig.from_env`.
        client: Optional pre-built Anthropic client (used by tests to
            inject a mock). When ``None``, a new client is constructed
            from ``config``.

    Returns:
        A populated :class:`AskAnswer`. When the model refuses, the
        ``sql`` and ``answer_template`` fields are empty and ``refused``
        is ``True``.

    Raises:
        PromptTooLargeError: When the wrapped user message exceeds the
            configured ``max_input_tokens`` cap (rough estimate: 4 chars
            per token).
        MalformedAnthropicResponseError: When the SDK response lacks a
            ``tool_use`` block or the tool input does not parse.
        AnthropicClientError: For any other client-side failure.
    """
    cfg = config or AnthropicConfig.from_env()
    user_message = build_user_message(question.question, question.scope)

    # Rough token estimate (4 chars/token heuristic — see CLAUDE.md). The
    # actual tokeniser is on Anthropic's side, but this guard refuses
    # questions that are clearly too large before paying for a round trip.
    estimated_tokens = max(1, len(user_message) // 4)
    if estimated_tokens > cfg.max_input_tokens:
        msg = (
            f"prompt too large: estimated {estimated_tokens} tokens > cap "
            f"{cfg.max_input_tokens}"
        )
        raise PromptTooLargeError(msg)

    sdk_client = client or anthropic.Anthropic(
        api_key=cfg.api_key.get_secret_value(),
        base_url=cfg.base_url,
        timeout=cfg.timeout_seconds,
    )

    # The system block is a list of typed content blocks so we can attach
    # cache_control on the schema chunk. Anthropic's prompt-cache hashes
    # the block contents; the cache hit on the next request within the
    # TTL is what gives us the 90% schema-token discount.
    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": SCHEMA_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    # The SDK uses fine-grained TypedDicts for the ``messages``,
    # ``system``, ``tools``, and ``tool_choice`` parameters. We keep
    # native dicts at the call site for readability and cast to ``Any``
    # at the boundary — the SDK validates the shape at runtime and any
    # drift surfaces as :class:`anthropic.APIError`.
    create: Any = sdk_client.messages.create
    try:
        response: Message = create(
            model=cfg.model,
            max_tokens=cfg.max_output_tokens,
            temperature=cfg.temperature,
            system=system_blocks,
            tools=[_EMIT_SQL_TOOL],
            tool_choice={"type": "tool", "name": _ANTHROPIC_TOOL_NAME},
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as exc:
        msg = f"Anthropic API error: {exc}"
        raise AnthropicClientError(msg) from exc

    return _parse_tool_use(response)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_tool_use(response: Message) -> AskAnswer:
    """Extract the ``emit_sql`` tool block from ``response`` and validate it.

    The forced ``tool_choice`` parameter guarantees the model called the
    tool, but defensive code still asserts the shape — Anthropic's API
    occasionally returns a ``text`` preamble before the tool block, and
    we want to fail loudly if the schema drifts.
    """
    # The SDK's content union has narrow per-variant fields; tests inject
    # ``MagicMock`` instances whose ``.type`` attribute is a plain string.
    # Treat the block as ``Any`` so both production ToolUseBlock and the
    # test double resolve.
    tool_blocks: list[Any] = [
        block for block in response.content if getattr(block, "type", None) == "tool_use"
    ]
    if not tool_blocks:
        msg = "Anthropic response did not contain a tool_use block"
        raise MalformedAnthropicResponseError(msg)
    first = tool_blocks[0]
    if getattr(first, "name", None) != _ANTHROPIC_TOOL_NAME:
        msg = f"unexpected tool name: {getattr(first, 'name', None)}"
        raise MalformedAnthropicResponseError(msg)

    raw = first.input
    if not isinstance(raw, dict):
        msg = "tool_use input was not a JSON object"
        raise MalformedAnthropicResponseError(msg)
    payload = cast(dict[str, Any], raw)

    usage = _parse_usage(response)
    try:
        return AskAnswer(
            sql=str(payload.get("sql", "")),
            answer_template=str(payload.get("answer_template", "")),
            citation=str(payload.get("citation", "")),
            refused=bool(payload.get("refused", False)),
            refusal_reason=str(payload.get("refusal_reason", "")),
            usage=usage,
        )
    except (TypeError, ValueError) as exc:
        msg = f"tool_use payload did not match AskAnswer schema: {exc}"
        raise MalformedAnthropicResponseError(msg) from exc


def _parse_usage(response: Message) -> AnthropicUsage:
    """Extract input / output / cache token counts from ``response.usage``.

    The ``cache_read_input_tokens`` and ``cache_creation_input_tokens``
    fields are present only when prompt caching is enabled — defensive
    ``getattr`` handles older mock instances in tests.
    """
    usage = response.usage
    return AnthropicUsage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )
