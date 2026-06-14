"""PII redaction sanity test for the App Insights telemetry surface (Etapa 8).

This test drives a synthetic ``/api/ask`` request through the full
``function_app.triggers.ask`` handler with the Anthropic call and the SQL
execution monkeypatched out, then captures every structlog + stdlib-logging +
stdout/stderr emission during the request and asserts that **none** of them
contain:

1. The full AAD object id (a 36-char UUID with dashes, or its 32-char hex form).
   Only the last 8 hex characters (``oid_suffix``) are allowed in telemetry,
   per the threat-model decision in `docs/security/threat_model.md` §S05.
2. The user's question text. The system never persists raw prompts; the audit
   hook in Kusto query 07 reduces questions to a SHA-256 hash.
3. The base64-encoded principal header (decodes to UPN + role list — a
   user-identifiable blob that must stay out of telemetry).

The test does NOT require live SQL or Anthropic — it stays self-contained so
it can run under `pytest tests/unit` (CI default) without env-gating.

If this test fails, do NOT silence it. The fix is in the production code:
re-bind the leaking field (e.g. swap ``oid=str(oid)`` for
``oid_suffix=oid.hex[-8:]``), or remove the ``question_text`` from whatever
log event surfaced it.

The Etapa-8 convergence pass extended the original 5-path suite (code-MA-04,
obs-MI-05, obs-MI-06):

* All five original paths now co-capture stdlib `logging` + stdout/stderr to
  block a future refactor that swaps structlog for stdlib logging from
  slipping past the gate.
* Three additional early-exit paths cover bad-JSON, question-too-long, and
  forwarded-secret-mismatch — the previously uncovered short-circuits where a
  future regression that adds ``question_text=…`` to a warning line would not
  trip the original suite.
* The refusal path now exercises an Anthropic reply whose `refusal_reason`
  contains the canary verbatim, mirroring real-world model behaviour that
  quotes the question back at the user.
* The success path also positively asserts the `tcp.ask.audit` event is
  emitted with the canary's SHA-256, converting the audit hook from a
  documentation aspiration into an enforced contract (obs-CR-01).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import logging
from typing import Any, Iterator
from uuid import UUID

import azure.functions as func
import pytest
import structlog

# Test fixtures: a unique OID and a unique question string; if either appears
# verbatim in any captured event, the test fails. Both are picked so the
# first 8 hex chars are *different* from the last 8 hex chars — ensures the
# permitted ``oid_suffix`` (last 8) does not accidentally match the disallowed
# full prefix.
_TEST_OID = UUID("a1a1a1a1-bbbb-cccc-dddd-e0e0e0e0e0e0")
_TEST_OID_FULL_STR = str(_TEST_OID)                # 36 chars with dashes
_TEST_OID_HEX_NO_DASHES = _TEST_OID.hex             # 32 chars, lowercase hex
_TEST_OID_SUFFIX = _TEST_OID.hex[-8:]               # 8 chars — the allowed form
_TEST_QUESTION = "tcp-pii-canary-string-7c0a4f-do-not-leak"
_TEST_QUESTION_SHA256 = hashlib.sha256(_TEST_QUESTION.encode("utf-8")).hexdigest()

_TEST_SECRET = "test-forwarded-secret-pii"


def _build_principal_header(oid: UUID) -> str:
    """Construct a base64 SWA principal blob carrying ``oid`` as the AAD claim."""
    body = {
        "userId": str(oid),
        "userDetails": "pii-canary@tcp-capital.ro",
        "userRoles": ["authenticated"],
        "claims": [
            {
                "typ": "http://schemas.microsoft.com/identity/claims/objectidentifier",
                "val": str(oid),
            }
        ],
    }
    return base64.b64encode(json.dumps(body).encode("utf-8")).decode("utf-8")


_VALID_PRINCIPAL_HEADER = _build_principal_header(_TEST_OID)


def _build_request(
    *,
    body: dict[str, Any] | None = None,
    raw_body: bytes | None = None,
    principal_header: str | None = _VALID_PRINCIPAL_HEADER,
    forwarded: str | None = _TEST_SECRET,
) -> func.HttpRequest:
    """Build a Functions HttpRequest with optional header / body overrides."""
    if raw_body is None:
        raw_body = json.dumps(body or {"question": _TEST_QUESTION}).encode("utf-8")
    headers: dict[str, str] = {"content-type": "application/json"}
    if principal_header is not None:
        headers["x-ms-client-principal"] = principal_header
    if forwarded is not None:
        headers["X-SWA-Forwarded"] = forwarded
    return func.HttpRequest(
        method="POST",
        url="https://example.invalid/api/ask",
        headers=headers,
        params={},
        body=raw_body,
        route_params={},
    )


@contextlib.contextmanager
def _capture_all_telemetry(caplog: pytest.LogCaptureFixture) -> Iterator[dict[str, Any]]:
    """Capture structlog events + stdlib log records + stdout/stderr in one bag.

    A future refactor that swaps structlog for stdlib ``logging`` would slip
    past a single-channel capture; a regression that uses ``print(question)``
    would slip past both. Co-capturing all three closes those gaps.
    """
    # Force caplog to capture across the entire hierarchy at INFO level.
    caplog.set_level(logging.DEBUG)
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    structlog_events: list[dict[str, Any]] = []
    bag: dict[str, Any] = {
        "structlog": structlog_events,
        "stdlib_records": caplog.records,
        "stdout": stdout_buf,
        "stderr": stderr_buf,
    }
    with structlog.testing.capture_logs() as captured:
        # Mutate the same list reference so the caller sees the captured events
        # after the context exits.
        structlog_events_ref = captured
        bag["structlog"] = structlog_events_ref
        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            yield bag


def _flatten_event(event: dict[str, Any]) -> str:
    """Render a single captured structlog event as a single string for substring search."""
    return json.dumps(event, default=str, ensure_ascii=False)


def _flatten_all(bag: dict[str, Any]) -> str:
    """Combine every capture channel into a single searchable text blob."""
    parts: list[str] = []
    for event in bag["structlog"]:
        parts.append(_flatten_event(event))
    for record in bag["stdlib_records"]:
        parts.append(record.getMessage())
        # Include the structured args — a regression that does
        # `logger.warning("…", extra={"question": q})` would only surface here.
        parts.append(json.dumps(record.__dict__, default=str, ensure_ascii=False))
    parts.append(bag["stdout"].getvalue())
    parts.append(bag["stderr"].getvalue())
    return "\n".join(parts)


def _assert_no_pii(bag: dict[str, Any]) -> str:
    """Assert no captured channel contains any PII canary. Return the flattened blob."""
    flattened = _flatten_all(bag)
    # Co-capture must have produced *something*; an empty capture would let a
    # silently-stripped logger pass every assertion (code-MA-04).
    assert (
        bag["structlog"]
        or bag["stdlib_records"]
        or bag["stdout"].getvalue()
        or bag["stderr"].getvalue()
    ), "expected at least one telemetry emission; did the logger configuration regress?"
    assert _TEST_QUESTION not in flattened, (
        "PII LEAK: the user's question text appeared in a telemetry channel.\n"
        f"Offending payload (truncated): {flattened[:1500]}"
    )
    assert _TEST_OID_FULL_STR not in flattened, (
        "PII LEAK: the full AAD oid (dashed form) appeared in telemetry. "
        "Only `oid_suffix` (last 8 hex) is permitted.\n"
        f"Offending payload (truncated): {flattened[:1500]}"
    )
    assert _TEST_OID_HEX_NO_DASHES not in flattened, (
        "PII LEAK: the full AAD oid (32-char hex form) appeared in telemetry. "
        "Only `oid_suffix` (last 8 hex) is permitted.\n"
        f"Offending payload (truncated): {flattened[:1500]}"
    )
    assert _VALID_PRINCIPAL_HEADER not in flattened, (
        "PII LEAK: the base64 SWA principal header appeared verbatim in "
        "telemetry. It decodes to UPN + role list and must stay out of logs.\n"
        f"Offending payload (truncated): {flattened[:1500]}"
    )
    return flattened


@pytest.fixture(autouse=True)
def _pin_forwarded_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin SWA_FORWARDED_SECRET so the trigger's header check passes."""
    monkeypatch.setenv("SWA_FORWARDED_SECRET", _TEST_SECRET)


@pytest.fixture(autouse=True)
def _clear_rate_limit_buckets() -> None:
    """Reset the per-process rate-limit ledger so previous tests don't bleed in."""
    from function_app.triggers import ask as ask_module

    ask_module._RATE_LIMIT_BUCKETS.clear()  # noqa: SLF001


def _patch_handler_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    scope: str | None,
    answer: Any | None,
    rows: list[dict[str, Any]] | None = None,
    raise_on_execute: BaseException | None = None,
) -> None:
    """Replace the handler's external dependencies with deterministic stubs."""
    from function_app.triggers import ask as ask_module

    monkeypatch.setattr(
        ask_module,
        "_resolve_scope",
        lambda oid, *, sql_config=None: scope,
    )
    if answer is not None:
        monkeypatch.setattr(ask_module, "ask_claude", lambda q: answer)
    if raise_on_execute is not None:
        def _raise(*args: Any, **kwargs: Any) -> None:
            raise raise_on_execute
        monkeypatch.setattr(ask_module, "_execute_validated_sql", _raise)
    elif rows is not None:
        monkeypatch.setattr(
            ask_module,
            "_execute_validated_sql",
            lambda oid, validated, *, sql_config=None: rows,
        )


# ---------------------------------------------------------------------------
# Success / refusal / validation / unknown-principal / execute-failure paths
# ---------------------------------------------------------------------------


def test_telemetry_redacts_pii_on_success_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Happy-path /api/ask never leaks PII AND emits the obs-CR-01 audit event."""
    from function_app.triggers import ask as ask_module
    from tcp.ai.anthropic_client import AnthropicUsage, AskAnswer

    answer = AskAnswer(
        sql="SELECT TOP 1 employee_id FROM v_employee_performance",
        answer_template="row_count={row_count}",
        citation="v_employee_performance",
        refused=False,
        usage=AnthropicUsage(input_tokens=100, output_tokens=20, cache_read_tokens=3500),
    )
    _patch_handler_dependencies(
        monkeypatch,
        scope="trader",
        answer=answer,
        rows=[{"employee_id": 42}],
    )

    with _capture_all_telemetry(caplog) as bag:
        resp = ask_module.ask(_build_request())

    assert resp.status_code == 200, resp.get_body()
    flattened = _assert_no_pii(bag)

    # Positive assertion 1: oid_suffix MUST be bound on telemetry so an
    # operator can correlate rate-limit hits to a specific user fingerprint.
    assert _TEST_OID_SUFFIX in flattened, (
        "expected oid_suffix to be bound on at least one telemetry event; "
        "did the trigger's `_log.bind(oid_suffix=...)` regress?"
    )

    # Positive assertion 2 (obs-CR-01): the `tcp.ask.audit` event MUST be
    # emitted with the SHA-256 of the canary question. Converts the audit
    # hook from a documentation aspiration into an enforced contract.
    audit_events = [
        e for e in bag["structlog"] if e.get("event") == "tcp.ask.audit"
    ]
    assert len(audit_events) == 1, (
        f"expected exactly one tcp.ask.audit event, got {len(audit_events)}"
    )
    assert audit_events[0].get("question_sha256") == _TEST_QUESTION_SHA256


def test_telemetry_redacts_pii_on_refusal_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """obs-MI-06: model-echoed canary in refusal_reason must not leak to telemetry."""
    from function_app.triggers import ask as ask_module
    from tcp.ai.anthropic_client import AnthropicUsage, AskAnswer

    # Anthropic real-world refusals often quote the user's question back —
    # simulate that explicitly with the canary embedded.
    refusal_text = (
        f"I cannot help with the request '{_TEST_QUESTION}' because "
        "it appears to be testing my refusal handling."
    )
    refusal = AskAnswer(
        refused=True,
        refusal_reason=refusal_text,
        usage=AnthropicUsage(input_tokens=50, output_tokens=10, cache_read_tokens=3500),
    )
    _patch_handler_dependencies(monkeypatch, scope="trader", answer=refusal)

    with _capture_all_telemetry(caplog) as bag:
        resp = ask_module.ask(_build_request())

    assert resp.status_code == 422
    _assert_no_pii(bag)


def test_telemetry_redacts_pii_on_validation_failure_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A safe_query rejection must NOT log the user's question text."""
    from function_app.triggers import ask as ask_module
    from tcp.ai.anthropic_client import AnthropicUsage, AskAnswer

    bad_answer = AskAnswer(
        sql="DROP TABLE fact_Trades",
        answer_template="never rendered",
        citation="",
        refused=False,
        usage=AnthropicUsage(input_tokens=80, output_tokens=15, cache_read_tokens=3500),
    )
    _patch_handler_dependencies(monkeypatch, scope="trader", answer=bad_answer)

    with _capture_all_telemetry(caplog) as bag:
        resp = ask_module.ask(_build_request())

    assert resp.status_code == 422
    _assert_no_pii(bag)


def test_telemetry_redacts_pii_on_unknown_principal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unknown OID logs `tcp.func.ask.unknown_principal` — must redact."""
    from function_app.triggers import ask as ask_module

    _patch_handler_dependencies(monkeypatch, scope=None, answer=None)

    with _capture_all_telemetry(caplog) as bag:
        resp = ask_module.ask(_build_request())

    assert resp.status_code == 404
    _assert_no_pii(bag)


def test_telemetry_redacts_pii_on_execute_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A SQL-execution exception is logged with error class — never the prompt."""
    from function_app.triggers import ask as ask_module
    from tcp.ai.anthropic_client import AnthropicUsage, AskAnswer
    from tcp.db import TcpDbError

    answer = AskAnswer(
        sql="SELECT TOP 1 employee_id FROM v_employee_performance",
        answer_template="row_count={row_count}",
        citation="v_employee_performance",
        refused=False,
        usage=AnthropicUsage(input_tokens=100, output_tokens=20, cache_read_tokens=3500),
    )
    _patch_handler_dependencies(
        monkeypatch,
        scope="trader",
        answer=answer,
        raise_on_execute=TcpDbError("simulated failure for the PII canary"),
    )

    with _capture_all_telemetry(caplog) as bag:
        resp = ask_module.ask(_build_request())

    assert resp.status_code == 500
    _assert_no_pii(bag)


# ---------------------------------------------------------------------------
# obs-MI-05: early-exit paths (bad JSON, too-long question, forwarded-secret)
# ---------------------------------------------------------------------------


def test_telemetry_redacts_pii_on_bad_json_body(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Malformed JSON body returns 400 without leaking principal_header or canary."""
    from function_app.triggers import ask as ask_module

    # No need to patch ask_claude — the trigger returns 400 before reaching it.
    # Patch scope just so we make it past step 3 if json parse somehow succeeded.
    _patch_handler_dependencies(monkeypatch, scope="trader", answer=None)

    # Body contains a canary-shaped raw string (not valid JSON) — verify the
    # handler does not echo it into the warning log line.
    raw_payload = f"not-json-{_TEST_QUESTION}".encode("utf-8")

    with _capture_all_telemetry(caplog) as bag:
        resp = ask_module.ask(_build_request(raw_body=raw_payload))

    assert resp.status_code == 400
    _assert_no_pii(bag)


def test_telemetry_redacts_pii_on_question_too_long(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An over-length question returns 400 without echoing the canary into logs."""
    from function_app.triggers import ask as ask_module

    _patch_handler_dependencies(monkeypatch, scope="trader", answer=None)

    long_question = _TEST_QUESTION + ("x" * 1000)
    with _capture_all_telemetry(caplog) as bag:
        resp = ask_module.ask(_build_request(body={"question": long_question}))

    assert resp.status_code == 400
    _assert_no_pii(bag)


def test_telemetry_redacts_pii_on_forwarded_secret_mismatch(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Mismatched forwarded secret returns 403 without leaking the principal header."""
    from function_app.triggers import ask as ask_module

    with _capture_all_telemetry(caplog) as bag:
        resp = ask_module.ask(
            _build_request(
                forwarded="wrong-secret-value",
                body={"question": _TEST_QUESTION},
            )
        )

    assert resp.status_code == 403
    _assert_no_pii(bag)
