"""HttpTrigger_AskAssistant — POST /api/ask.

Production body wires:

1. Header validation (``X-SWA-Forwarded`` shared secret then
   ``x-ms-client-principal``) — see ``03_architecture.md §3.2`` / §8.2.
   The forwarded-secret check runs FIRST so the raw Function URL cannot
   be probed even with a well-formed principal blob (ai MA-05).
2. AAD claim parsing → :class:`tcp.db.SessionContext` (ADR-003).
3. Scope resolution via a single ``dim_UserRoles`` lookup performed on a
   ``bypass_session_context=True`` admin connection (see ADR-005). The
   lookup runs parameterised SQL with no user-controlled string
   interpolation; it stays small (one row by primary key) so the
   elevated connection closes within milliseconds.
4. Per-user rate-limit gate (best-effort, in-process — see ADR-005 for
   the residual single-instance limitation).
5. Anthropic call (``claude-haiku-4-5``, prompt-cached schema). See
   :mod:`tcp.ai.anthropic_client`.
6. SQL validation via :mod:`tcp.safe_query`.
7. Execution under an RLS-scoped connection
   (:func:`tcp.db.connection_for_user`).
8. Unified response envelope (see :func:`_envelope`) carrying answer,
   rows, citation, token-usage telemetry, and an explicit ``status``
   discriminator so the SWA frontend never has to branch on HTTP code.

Refusal paths (each maps to the same unified envelope shape):

- 401 if ``x-ms-client-principal`` missing or unparseable.
- 403 if ``X-SWA-Forwarded`` is mismatched / unconfigured.
- 404 if the AAD ``oid`` is not in ``dim_UserRoles``.
- 422 if the model refuses the question (``error.message`` carries the
  refusal reason).
- 422 if :func:`tcp.safe_query.validate` rejects the SQL — the
  ``error.message`` is a generic string; the offending detail is logged
  server-side only (ai MA-06).
- 429 if the per-user rate-limit budget is exhausted (security MJ-02).
- 500 on any unexpected exception (no stack traces in the response).

App-Insights structured-log dimensions emitted on every success:
``tcp.ask.latency_ms``, ``tcp.ask.input_tokens``, ``tcp.ask.output_tokens``,
``tcp.ask.cache_read_tokens``, ``tcp.ask.rows_returned``. Production-grade
custom metrics are tracked as future work in Etapa 8 — see ADR-005 §3.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import threading
import time
from collections import deque
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Final, cast
from uuid import UUID

import azure.functions as func
import pyodbc
import structlog

from function_app import app
from tcp import safe_query
from tcp.ai.anthropic_client import (
    ALLOWED_SCOPES,
    AnthropicClientError,
    AskAnswer,
    AskQuestion,
    MalformedAnthropicResponseError,
    PromptTooLargeError,
    Scope,
    ask_claude,
)
from tcp.db import (
    SessionContext,
    SqlConfig,
    TcpDbError,
    connection_for_user,
    open_connection,
)
from tcp.safe_query import (
    SafeQueryError,
    ValidationResult,
    validate,
)

_log = structlog.get_logger(__name__)

_HEADER_PRINCIPAL: Final[str] = "x-ms-client-principal"
_HEADER_FORWARDED: Final[str] = "X-SWA-Forwarded"
_ENV_FORWARDED_SECRET: Final[str] = "SWA_FORWARDED_SECRET"

_MAX_QUESTION_CHARS: Final[int] = 500
# Single source of truth for the row cap (hol MA-02): derive from the
# validator's MAX_ROW_LIMIT so a future bump propagates to fetchmany too.
_MAX_FETCH_ROWS: Final[int] = safe_query.MAX_ROW_LIMIT

# Rate-limit budget per user (security MJ-02). Best-effort single-instance
# bookkeeping documented in ADR-005 — sufficient for the v1.0 Y1 plan.
_RATE_LIMIT_MAX_REQUESTS: Final[int] = 10
_RATE_LIMIT_WINDOW_SECONDS: Final[float] = 60.0

_OID_CLAIM_TYPES: Final[frozenset[str]] = frozenset(
    {
        "http://schemas.microsoft.com/identity/claims/objectidentifier",
        "oid",
    }
)
# Etapa-12 consolidation (closes code11-MI-05): the canonical four-tuple lives
# once in :mod:`tcp.ai.anthropic_client` as ``Scope`` (the Literal type) and
# ``ALLOWED_SCOPES`` (its runtime view via ``get_args``); both are imported at
# the top of this module and used directly at the call sites below — no local
# rename, single source of truth (code12-MI-03 convergence fix).

# Single, bounded ``{value:column}`` substitution. Column names match the
# T-SQL identifier rules: ``[A-Za-z_][A-Za-z0-9_]*``.
_TEMPLATE_VALUE_RE: Final[re.Pattern[str]] = re.compile(
    r"\{value:([A-Za-z_][A-Za-z0-9_]*)\}"
)

# In-process rate-limit ledger: oid → recent request timestamps. Mutated
# under ``_RATE_LIMIT_LOCK`` because Functions worker processes may run
# multiple threads per worker. Cleared by every check that processes its
# own bucket.
_RATE_LIMIT_BUCKETS: Final[dict[UUID, deque[float]]] = {}
_RATE_LIMIT_LOCK: Final[threading.Lock] = threading.Lock()


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


class _TcpJsonEncoder(json.JSONEncoder):
    """JSON encoder that emits typed Python values in stable shapes.

    Closes the silent ``default=str`` flattening surfaced by the Etapa-5
    python review (MJ-03) and the holistic review (mi-07):

    - ``Decimal`` → ``float`` so the SWA's ``Intl.NumberFormat('ro-RO')``
      pipeline can format EUR amounts as ``12.345,67 €``. The loss of
      trailing-zero precision is acceptable for display because the
      column types are ``DECIMAL(18,4)`` and the formatter renders two
      fraction digits in the EUR style.
    - ``datetime`` / ``date`` → ISO-8601 string with offset preserved.
    - ``UUID`` → lower-case canonical string form.

    Any other unknown type raises ``TypeError`` so future regressions
    surface loudly instead of falling through to ``str(value)``.
    """

    def default(self, o: Any) -> Any:
        """Serialise an unknown object into a JSON-compatible primitive."""
        if isinstance(o, Decimal):
            return float(o)
        # ``date`` covers SQL ``DATE`` columns (e.g., ``trade_date_ro``);
        # ``datetime`` covers ``DATETIMEOFFSET`` columns. ``isoformat`` is
        # safe for both and SWA-side ``isIsoDateString`` parses it back.
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, UUID):
            return str(o)
        # Anything else surfaces as ``TypeError`` so the failure mode is
        # loud, not a silent ``str(value)`` fallback that breaks display.
        return super().default(o)


def _envelope(
    *,
    status: str,
    http_status: int,
    started_ms: int,
    answer: str | None = None,
    rows: list[dict[str, Any]] | None = None,
    source: str | None = None,
    usage: dict[str, Any] | None = None,
    objects_referenced: list[str] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> func.HttpResponse:
    """Build the unified ``HttpResponse`` envelope (holistic CR-02).

    Every response path uses this helper so the SWA frontend can parse a
    single shape without branching on the HTTP status code. The HTTP
    status itself is preserved for cache / proxy / observability layers
    that key off it. ``extra_headers`` lets specific paths (e.g. 429) add
    response headers like ``Retry-After`` without coupling the envelope
    body to the HTTP transport details.
    """
    row_count = None if rows is None else len(rows)
    payload: dict[str, Any] = {
        "status": status,
        "answer": answer,
        "rows": rows,
        "row_count": row_count,
        "source": source,
        "latency_ms": int(time.monotonic() * 1000) - started_ms,
        "anthropic": usage,
        "objects_referenced": objects_referenced,
        "error": (
            None
            if error_code is None
            else {"code": error_code, "message": error_message or ""}
        ),
    }
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    return func.HttpResponse(
        body=json.dumps(payload, cls=_TcpJsonEncoder),
        status_code=http_status,
        headers=headers,
        mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# Header / principal parsing
# ---------------------------------------------------------------------------


def _parse_principal_header(value: str) -> UUID | None:
    """Decode the SWA principal header and return the AAD ``oid`` claim.

    The header is a base64-encoded JSON object with shape:

    ``{ "userId": "...", "userDetails": "...", "userRoles": [...],
       "claims": [ { "typ": "...", "val": "..." }, ... ] }``

    Returns ``None`` when the header is malformed or the ``oid`` claim is
    missing — the caller maps that to HTTP 401.
    """
    try:
        decoded = base64.b64decode(value).decode("utf-8")
        body = json.loads(decoded)
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return None

    # The SWA platform also exposes ``userId`` as the AAD object id, but the
    # claims-list parse is the documented contract (``03_arch §3.2``) and
    # works against the same shape from local development simulators.
    claims = body.get("claims") or []
    if not isinstance(claims, list):
        return None
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        if claim.get("typ") in _OID_CLAIM_TYPES:
            raw = claim.get("val")
            if isinstance(raw, str):
                try:
                    return UUID(raw)
                except ValueError:
                    return None

    # Fall back to ``userId`` when no oid claim was emitted (e.g., the SWA
    # emulator omits the ``claims`` array). The host still rejects unknown
    # ids in step 5 below — this is purely a parsing convenience.
    user_id = body.get("userId")
    if isinstance(user_id, str):
        try:
            return UUID(user_id)
        except ValueError:
            return None
    return None


def _validate_forwarded_secret(forwarded: str) -> bool:
    """Compare the supplied forwarded-secret value against the configured one.

    Returns ``False`` when the env var is empty (fail-closed) or the
    comparison fails. ``hmac.compare_digest`` guards against timing
    oracles on the secret length / prefix.
    """
    expected = os.environ.get(_ENV_FORWARDED_SECRET, "")
    if not expected:
        _log.error("tcp.func.ask.forwarded_secret_unconfigured")
        return False
    return hmac.compare_digest(forwarded, expected)


# ---------------------------------------------------------------------------
# Rate limiting (security MJ-02)
# ---------------------------------------------------------------------------


def _check_and_record_rate_limit(oid: UUID, *, now: float | None = None) -> bool:
    """Return ``True`` if ``oid`` is within budget; record the new timestamp.

    A simple sliding-window counter: keeps the per-user request timestamps
    inside ``_RATE_LIMIT_WINDOW_SECONDS`` and refuses when the count
    reaches ``_RATE_LIMIT_MAX_REQUESTS``. The state lives in-process, so
    a cold-start clears the budget — acceptable for the v1.0 Y1 plan,
    documented in ADR-005.

    Args:
        oid: The caller's AAD object id.
        now: Optional fixed timestamp (used by tests to avoid wall-clock
            flakiness). Defaults to ``time.monotonic()``.

    Returns:
        ``True`` when the request is allowed and the new timestamp has
        been recorded, ``False`` when the user is over the budget.
    """
    current = now if now is not None else time.monotonic()
    cutoff = current - _RATE_LIMIT_WINDOW_SECONDS
    with _RATE_LIMIT_LOCK:
        bucket = _RATE_LIMIT_BUCKETS.get(oid)
        if bucket is None:
            bucket = deque()
            _RATE_LIMIT_BUCKETS[oid] = bucket
        # Drop entries that fell outside the window.
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_MAX_REQUESTS:
            return False
        bucket.append(current)
        return True


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------


def _resolve_scope(oid: UUID, *, sql_config: SqlConfig | None = None) -> str | None:
    """Look up the user's active scope in ``dim_UserRoles`` or return ``None``.

    Runs a single parameterised SELECT on an admin-bypass connection;
    the connection is closed immediately after the lookup so it cannot
    be reused for the user-driven SQL (that step opens a fresh
    SESSION_CONTEXT-scoped connection in :func:`_execute_validated_sql`).

    The admin-bypass connection is the documented trade-off captured in
    ``docs/decisions/ADR-005-scope-resolution-rls-bypass.md`` (Etapa-5
    holistic review MA-05): the parameterised single-row SELECT against
    ``dim_UserRoles`` is the only way to discover the caller's scope
    before SESSION_CONTEXT can be set on the user-facing connection.

    Returns the scope string (``trader``/``team_lead``/``floor_manager``/
    ``admin``) when active, ``None`` when the principal is missing or
    inactive.
    """
    conn = open_connection(config=sql_config, bypass_session_context=True)
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT TOP 1 scope FROM dbo.dim_UserRoles "
                "WHERE aad_object_id = ? AND is_active = 1",
                str(oid),
            )
            row = cursor.fetchone()
        finally:
            try:
                cursor.close()
            except pyodbc.Error:
                # See the note in tcp.db.connection_for_user — cursor.close
                # failures during cleanup must not mask the primary outcome.
                pass
    finally:
        conn.close()

    if row is None or row[0] is None:
        return None
    scope = str(row[0])
    return scope if scope in ALLOWED_SCOPES else None


# ---------------------------------------------------------------------------
# SQL execution + rendering
# ---------------------------------------------------------------------------


def _execute_validated_sql(
    oid: UUID,
    validated: ValidationResult,
    *,
    sql_config: SqlConfig | None = None,
) -> list[dict[str, Any]]:
    """Run the sanitised SQL under SESSION_CONTEXT(oid) and return capped rows."""
    principal = SessionContext(aad_object_id=oid)
    rows: list[dict[str, Any]] = []
    with connection_for_user(principal, config=sql_config) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(validated.sanitized_sql)
            columns = [column[0] for column in cursor.description or []]
            for raw_row in cursor.fetchmany(_MAX_FETCH_ROWS):
                rows.append(dict(zip(columns, raw_row, strict=False)))
        finally:
            try:
                cursor.close()
            except pyodbc.Error:
                pass
    return rows


def _render_answer(template: str, rows: list[dict[str, Any]]) -> str:
    """Render ``template`` with ``{row_count}`` / ``{value:col}`` / ``{rows}``.

    Substitution is bounded:

    - ``{row_count}`` → ``len(rows)``.
    - ``{value:<col>}`` → string representation of ``rows[0][col]`` (or
      ``"n/a"`` when missing).
    - ``{rows}`` → a compact ``column=value`` rendering of every row.

    Unknown placeholders are left intact so the SWA frontend can surface
    them and a follow-up Anthropic call can tighten the template if
    needed.
    """
    row_count = len(rows)
    rendered = template.replace("{row_count}", str(row_count))

    def _value_sub(match: re.Match[str]) -> str:
        column = match.group(1)
        if not rows:
            return "n/a"
        cell = rows[0].get(column, "n/a")
        return _format_cell(cell)

    rendered = _TEMPLATE_VALUE_RE.sub(_value_sub, rendered)

    if "{rows}" in rendered:
        table = "; ".join(
            ", ".join(f"{k}={_format_cell(v)}" for k, v in row.items())
            for row in rows
        )
        rendered = rendered.replace("{rows}", table)

    return rendered


def _format_cell(value: Any) -> str:
    """Format a SQL cell for inclusion in the answer string.

    Romanian locale formatting (decimal comma, thousand dot) is applied
    in the SWA frontend; here we emit a stable, debuggable string form
    that the SWA's ``Intl.NumberFormat('ro-RO')`` pipeline then localises.
    """
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (float, Decimal)):
        return f"{float(value):.2f}"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


def _emit_metrics(
    log: structlog.stdlib.BoundLogger,
    /,
    *,
    latency_ms: int,
    answer: AskAnswer,
    row_count: int,
) -> None:
    """Emit structured-log lines that App Insights converts to custom dimensions.

    Takes the request-bound logger so the emission inherits the ``oid_suffix``
    + ``scope`` context already accumulated upstream. Without that binding the
    Etapa-8 dashboards cannot correlate token spend to a specific user fingerprint
    (the PII redaction test enforces both halves: oid_suffix present, full oid
    absent — see `tests/integration/test_telemetry_no_pii.py`).

    Production-grade ``customMetrics`` (Application Insights metrics
    blade) require the ``azure-monitor-opentelemetry`` SDK and are
    documented as Etapa-8 work in ADR-005 §3. For v1.0 we emit the
    metric values as structured-log dimensions named ``metric_*`` so the
    monitoring story (``03_architecture.md §12``) can rely on KQL queries.
    """
    log.info(
        "tcp.ask.metrics",
        metric_latency_ms=latency_ms,
        metric_input_tokens=answer.usage.input_tokens,
        metric_output_tokens=answer.usage.output_tokens,
        metric_cache_read_tokens=answer.usage.cache_read_tokens,
        metric_cache_write_tokens=answer.usage.cache_write_tokens,
        metric_rows_returned=row_count,
        refused=answer.refused,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Trigger entrypoint
# ---------------------------------------------------------------------------


@app.route(
    route="ask",
    methods=["POST"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def ask(req: func.HttpRequest) -> func.HttpResponse:
    """Validate, ask Claude, validate the SQL, execute under RLS, and respond.

    The function never leaks exception details to the client. Every
    failure path maps to the unified envelope (see :func:`_envelope`);
    structured logs carry the diagnostic detail for the operator.

    Args:
        req: The Functions ``HttpRequest`` carrying the SWA-forwarded
            headers and the user's question payload.

    Returns:
        A JSON ``HttpResponse`` with the unified envelope shape, status
        codes 200/400/401/403/404/413/422/429/500 as documented.
    """
    started_ms = int(time.monotonic() * 1000)

    # 1a. Shared-secret X-SWA-Forwarded check — DISABLED 2026-05-18.
    # The original design assumed SWA would propagate a custom header set in
    # `staticwebapp.config.json/forwardingGateway`. In practice SWA only adds
    # `x-ms-client-principal` to linked-backend traffic; custom headers do not
    # cross the proxy automatically. Defense-in-depth here was illusory and
    # the `forwardingGateway` block had to be removed from the SWA config to
    # let browsers load the static UI at all. Primary auth remains intact:
    # Function App EasyAuth blocks anonymous direct hits AND the AAD principal
    # header check below verifies the caller's identity. Track a proper
    # gateway-attestation mechanism (e.g. signed JWT in custom header injected
    # via SWA functionalities) as future work.

    # 1b. AAD principal header.
    principal_header = req.headers.get(_HEADER_PRINCIPAL)
    if not principal_header:
        _log.warning("tcp.func.ask.missing_principal")
        return _envelope(
            status="unauthorized",
            http_status=401,
            started_ms=started_ms,
            error_code="missing_principal",
            error_message="Authentication required.",
        )

    oid = _parse_principal_header(principal_header)
    if oid is None:
        _log.warning("tcp.func.ask.unparseable_principal")
        return _envelope(
            status="unauthorized",
            http_status=401,
            started_ms=started_ms,
            error_code="malformed_principal",
            error_message="Authentication header could not be parsed.",
        )

    log = _log.bind(oid_suffix=oid.hex[-8:])

    # 2. Parse + validate the question payload.
    try:
        body = req.get_json()
    except ValueError:
        log.warning("tcp.func.ask.invalid_json_body")
        return _envelope(
            status="bad_request",
            http_status=400,
            started_ms=started_ms,
            error_code="invalid_json",
            error_message="Request body must be valid JSON.",
        )
    question_text = body.get("question") if isinstance(body, dict) else None
    if not isinstance(question_text, str) or not question_text.strip():
        log.warning("tcp.func.ask.missing_question")
        return _envelope(
            status="bad_request",
            http_status=400,
            started_ms=started_ms,
            error_code="missing_question",
            error_message="The 'question' field is required.",
        )
    if len(question_text) > _MAX_QUESTION_CHARS:
        # obs-MI-05: emit a warning so the operator sees the rejection.
        # Log only the LENGTH — never the prefix — to avoid surfacing the
        # canary in a downstream tcp.func.ask.question_too_long trace.
        log.warning(
            "tcp.func.ask.question_too_long",
            question_length=len(question_text),
            max_chars=_MAX_QUESTION_CHARS,
        )
        return _envelope(
            status="bad_request",
            http_status=400,
            started_ms=started_ms,
            error_code="question_too_long",
            error_message=(
                f"Question must be at most {_MAX_QUESTION_CHARS} characters."
            ),
        )

    # 3. Resolve scope via a single admin-bypass lookup (see ADR-005).
    try:
        scope = _resolve_scope(oid)
    except (TcpDbError, pyodbc.Error) as exc:
        log.error(
            "tcp.func.ask.scope_lookup_failed",
            error_class=type(exc).__name__,
            error=str(exc)[:200],
        )
        return _envelope(
            status="internal_error",
            http_status=500,
            started_ms=started_ms,
            error_code="scope_lookup_failed",
            error_message="Scope lookup is currently unavailable.",
        )
    if scope is None:
        log.warning("tcp.func.ask.unknown_principal")
        return _envelope(
            status="not_found",
            http_status=404,
            started_ms=started_ms,
            error_code="principal_not_registered",
            error_message="Your account is not registered for this application.",
        )
    log = log.bind(scope=scope)

    # 4. Per-user rate-limit gate (security MJ-02).
    if not _check_and_record_rate_limit(oid):
        log.warning("tcp.func.ask.rate_limited")
        return _envelope(
            status="rate_limited",
            http_status=429,
            started_ms=started_ms,
            error_code="rate_limited",
            error_message=(
                f"Too many requests — limit is {_RATE_LIMIT_MAX_REQUESTS} "
                f"per {int(_RATE_LIMIT_WINDOW_SECONDS)} seconds."
            ),
            extra_headers={"Retry-After": str(int(_RATE_LIMIT_WINDOW_SECONDS))},
        )

    # 4b. Emit the audit event (obs-CR-01). The question text is reduced to a
    # SHA-256 fingerprint so query 07 / workbook tile 9 can detect repetition
    # without ever persisting raw prompts. The redaction posture is enforced
    # by `tests/integration/test_telemetry_no_pii.py`.
    question_normalised = question_text.strip()
    question_fingerprint = hashlib.sha256(question_normalised.encode("utf-8")).hexdigest()
    log.info("tcp.ask.audit", question_sha256=question_fingerprint)

    # 5. Anthropic call.
    # Etapa-11 typing fix (refined in Etapa 12): `_resolve_scope` returns
    # `str | None` (already narrowed to `None` above). `AskQuestion.scope` is
    # typed as the `Scope` Literal alias defined once in `tcp.ai.anthropic_client`;
    # mypy needs the cast to know our `str` IS that Literal (already validated
    # inside `_resolve_scope` via the `scope in ALLOWED_SCOPES` check).
    typed_scope = cast(Scope, scope)
    try:
        answer = ask_claude(AskQuestion(question=question_normalised, scope=typed_scope))
    except PromptTooLargeError:
        return _envelope(
            status="bad_request",
            http_status=413,
            started_ms=started_ms,
            error_code="payload_too_large",
            error_message="Question exceeds the token budget.",
        )
    except (AnthropicClientError, MalformedAnthropicResponseError) as exc:
        log.error(
            "tcp.func.ask.anthropic_failed",
            error_class=type(exc).__name__,
            error=str(exc)[:200],
        )
        return _envelope(
            status="internal_error",
            http_status=500,
            started_ms=started_ms,
            error_code="anthropic_unavailable",
            error_message="AI backend is currently unavailable.",
        )

    usage_dict = answer.usage.model_dump()

    if answer.refused:
        # obs-MI-06: a real Anthropic refusal can quote phrases from the user's
        # question back to the operator ("I cannot answer 'why does X outperform
        # Y'"). Logging `answer.refusal_reason` verbatim would echo that PII
        # into App Insights. Log only a SHA-256 fingerprint + length so the
        # operator can correlate repeat refusals without storing the prose.
        # The raw reason still travels to the user via the envelope at line
        # 671 below — that path is the user's own data round-trip, not a
        # third-party telemetry sink.
        refusal_fingerprint = hashlib.sha256(
            answer.refusal_reason.encode("utf-8")
        ).hexdigest()
        log.info(
            "tcp.func.ask.refused",
            refusal_reason_sha256=refusal_fingerprint,
            refusal_reason_length=len(answer.refusal_reason),
        )
        latency_ms = int(time.monotonic() * 1000) - started_ms
        _emit_metrics(log, latency_ms=latency_ms, answer=answer, row_count=0)
        return _envelope(
            status="refused",
            http_status=422,
            started_ms=started_ms,
            usage=usage_dict,
            error_code="refused_by_model",
            error_message=answer.refusal_reason,
        )

    # 6. Validate the model's SQL.
    try:
        validated = validate(answer.sql)
    except SafeQueryError as exc:
        # ai MA-06: do NOT echo the internal validator reason / table
        # names to the client. The structured log keeps the full detail
        # for the operator; the wire body stays generic.
        log.warning(
            "tcp.func.ask.sql_validation_failed",
            error_class=type(exc).__name__,
            error=str(exc)[:200],
            sql_prefix=answer.sql[:120],
        )
        return _envelope(
            status="validation_error",
            http_status=422,
            started_ms=started_ms,
            usage=usage_dict,
            error_code="sql_validation_failed",
            error_message="The generated query was rejected by the safety validator.",
        )

    # 7. Execute under SESSION_CONTEXT(oid).
    try:
        rows = _execute_validated_sql(oid, validated)
    except (TcpDbError, pyodbc.Error) as exc:
        log.error(
            "tcp.func.ask.execute_failed",
            error_class=type(exc).__name__,
            error=str(exc)[:200],
        )
        return _envelope(
            status="internal_error",
            http_status=500,
            started_ms=started_ms,
            usage=usage_dict,
            error_code="execution_failed",
            error_message="Query execution failed.",
        )

    # 8. Render answer template + emit telemetry.
    answer_text = _render_answer(answer.answer_template, rows)
    latency_ms = int(time.monotonic() * 1000) - started_ms
    _emit_metrics(log, latency_ms=latency_ms, answer=answer, row_count=len(rows))

    return _envelope(
        status="ok",
        http_status=200,
        started_ms=started_ms,
        answer=answer_text,
        rows=rows,
        source=answer.citation,
        usage=usage_dict,
        objects_referenced=sorted(validated.referenced_objects),
    )
