"""Integration tests for ``HttpTrigger_AskAssistant`` (POST /api/ask).

Marked with ``@pytest.mark.integration`` and skipped unless **both**
``TCP_SQL_SERVER`` and ``ANTHROPIC_API_KEY`` are present in the environment.
The Anthropic call is monkeypatched per-test so we exercise the
end-to-end function logic (header parsing, scope lookup, RLS-scoped
execution, response rendering) without spending API tokens.

The fixture set-up assumes the schema migration ``V001__init.sql`` is
applied and a synthetic ``dim_UserRoles`` row exists for the test OID
with ``scope='trader'`` and ``employee_id`` pointing at a real trader.

To run locally:

```
export TCP_SQL_SERVER=...
export TCP_SQL_DATABASE=tcp_dev
export TCP_SQL_DEV_USER=...
export TCP_SQL_DEV_PASSWORD=...
export ANTHROPIC_API_KEY=sk-ant-...
export SWA_FORWARDED_SECRET=test-secret
pytest tests/integration/test_ask_endpoint.py -m integration
```
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from uuid import UUID

import azure.functions as func
import pytest

pytestmark = pytest.mark.integration

_TEST_OID = UUID("e7e7e7e7-1111-2222-3333-444455556666")
_TEST_SECRET = "test-forwarded-secret"


def _required_env(*names: str) -> bool:
    """Return ``True`` only when every name is present and non-empty."""
    return all(os.environ.get(n) for n in names)


@pytest.fixture(autouse=True)
def _require_live_environment() -> None:
    """Skip the entire module unless live SQL + Anthropic creds are present."""
    if not _required_env("TCP_SQL_SERVER", "ANTHROPIC_API_KEY"):
        pytest.skip("integration tests require TCP_SQL_SERVER and ANTHROPIC_API_KEY")


@pytest.fixture
def forwarded_secret_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin the forwarded-secret env var so the header-validation step passes."""
    monkeypatch.setenv("SWA_FORWARDED_SECRET", _TEST_SECRET)
    return _TEST_SECRET


@pytest.fixture(autouse=True)
def _clear_rate_limit_buckets() -> None:
    """Reset the per-process rate-limit ledger before every test.

    The trigger keeps a module-level dict of timestamps per OID; tests
    that run in the same process would otherwise pollute each other.
    """
    from function_app.triggers import ask as ask_module

    ask_module._RATE_LIMIT_BUCKETS.clear()  # noqa: SLF001


def _build_principal_header(oid: UUID) -> str:
    """Construct a base64 SWA principal blob carrying ``oid`` as the AAD claim."""
    body = {
        "userId": str(oid),
        "userDetails": "integration@tcp-capital.ro",
        "userRoles": ["authenticated"],
        "claims": [
            {
                "typ": "http://schemas.microsoft.com/identity/claims/objectidentifier",
                "val": str(oid),
            }
        ],
    }
    raw = json.dumps(body).encode("utf-8")
    return base64.b64encode(raw).decode("utf-8")


def _build_request(
    *,
    principal_header: str | None,
    forwarded: str | None,
    body: dict[str, Any] | None,
    raw_body: bytes | None = None,
) -> func.HttpRequest:
    """Build a Functions HttpRequest with the given headers and JSON body."""
    headers = {}
    if principal_header is not None:
        headers["x-ms-client-principal"] = principal_header
    if forwarded is not None:
        headers["X-SWA-Forwarded"] = forwarded
    payload = raw_body if raw_body is not None else json.dumps(body or {}).encode("utf-8")
    return func.HttpRequest(
        method="POST",
        url="https://func-tcp-test.local/api/ask",
        body=payload,
        headers=headers,
    )


def _assert_envelope_shape(body: dict[str, Any]) -> None:
    """Pin the unified envelope keys so a future drift fails loudly."""
    for key in (
        "status",
        "answer",
        "rows",
        "row_count",
        "source",
        "latency_ms",
        "anthropic",
        "objects_referenced",
        "error",
    ):
        assert key in body, f"missing envelope key: {key}"
    assert isinstance(body["latency_ms"], int)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_ask_wrong_shared_secret_returns_403_first(forwarded_secret_env: str) -> None:
    """ai MA-05: forwarded-secret check fires BEFORE principal parsing.

    A request with NO principal header and a bad forwarded-secret must
    return 403 (not 401), so the raw Function URL cannot be probed even
    by an attacker who forges a valid principal blob.
    """
    from function_app.triggers.ask import ask

    req = _build_request(
        principal_header=None,
        forwarded="not-the-secret",
        body={"question": "How are we doing?"},
    )
    resp = ask(req)
    assert resp.status_code == 403
    body = json.loads(resp.get_body())
    _assert_envelope_shape(body)
    assert body["status"] == "forbidden"


def test_ask_missing_principal_returns_401(forwarded_secret_env: str) -> None:
    from function_app.triggers.ask import ask

    req = _build_request(
        principal_header=None,
        forwarded=_TEST_SECRET,
        body={"question": "How are we doing?"},
    )
    resp = ask(req)
    assert resp.status_code == 401
    body = json.loads(resp.get_body())
    _assert_envelope_shape(body)
    assert body["status"] == "unauthorized"


def test_ask_wrong_shared_secret_with_good_principal_returns_403(
    forwarded_secret_env: str,
) -> None:
    from function_app.triggers.ask import ask

    req = _build_request(
        principal_header=_build_principal_header(_TEST_OID),
        forwarded="not-the-secret",
        body={"question": "How are we doing?"},
    )
    resp = ask(req)
    assert resp.status_code == 403


def test_ask_unknown_oid_returns_404(
    forwarded_secret_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OID with no active dim_UserRoles row maps to 404."""
    from function_app.triggers import ask as ask_module

    # Force the scope lookup to return None without touching SQL.
    monkeypatch.setattr(ask_module, "_resolve_scope", lambda oid, *, sql_config=None: None)

    req = _build_request(
        principal_header=_build_principal_header(_TEST_OID),
        forwarded=_TEST_SECRET,
        body={"question": "How are we doing?"},
    )
    resp = ask_module.ask(req)
    assert resp.status_code == 404
    body = json.loads(resp.get_body())
    _assert_envelope_shape(body)
    assert body["status"] == "not_found"


@pytest.mark.parametrize(
    ("body", "raw"),
    [
        (None, b"not-json-at-all"),
        ({"not_question": "x"}, None),
        ({"question": "x" * 600}, None),
    ],
)
def test_ask_malformed_payload_returns_400(
    forwarded_secret_env: str,
    body: dict[str, Any] | None,
    raw: bytes | None,
) -> None:
    """ai MA-07: malformed JSON / missing question / oversize question -> 400."""
    from function_app.triggers.ask import ask

    req = _build_request(
        principal_header=_build_principal_header(_TEST_OID),
        forwarded=_TEST_SECRET,
        body=body,
        raw_body=raw,
    )
    resp = ask(req)
    assert resp.status_code == 400
    envelope = json.loads(resp.get_body())
    _assert_envelope_shape(envelope)
    assert envelope["status"] == "bad_request"
    assert envelope["error"]["code"] in {
        "invalid_json",
        "missing_question",
        "question_too_long",
    }


def test_ask_refused_question_returns_422(
    forwarded_secret_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model-refused question maps to 422 with the refusal reason."""
    from function_app.triggers import ask as ask_module
    from tcp.ai.anthropic_client import AnthropicUsage, AskAnswer

    monkeypatch.setattr(
        ask_module,
        "_resolve_scope",
        lambda oid, *, sql_config=None: "trader",
    )
    monkeypatch.setattr(
        ask_module,
        "ask_claude",
        lambda q: AskAnswer(
            refused=True,
            refusal_reason="Out of scope.",
            usage=AnthropicUsage(input_tokens=100, output_tokens=20, cache_read_tokens=3500),
        ),
    )

    req = _build_request(
        principal_header=_build_principal_header(_TEST_OID),
        forwarded=_TEST_SECRET,
        body={"question": "What's the company's IBAN?"},
    )
    resp = ask_module.ask(req)
    assert resp.status_code == 422
    body = json.loads(resp.get_body())
    _assert_envelope_shape(body)
    assert body["status"] == "refused"
    assert body["error"]["code"] == "refused_by_model"
    assert body["error"]["message"] == "Out of scope."


def test_ask_validation_failure_returns_generic_422(
    forwarded_secret_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ai MA-06: 422 validation envelope must NOT echo the validator's reason.

    Pinning a generic ``error.message`` prevents allowlist enumeration.
    """
    from function_app.triggers import ask as ask_module
    from tcp.ai.anthropic_client import AnthropicUsage, AskAnswer

    monkeypatch.setattr(
        ask_module,
        "_resolve_scope",
        lambda oid, *, sql_config=None: "trader",
    )
    monkeypatch.setattr(
        ask_module,
        "ask_claude",
        lambda q: AskAnswer(
            sql="DROP TABLE fact_Trades",
            answer_template="will never render",
            citation="",
            refused=False,
            usage=AnthropicUsage(),
        ),
    )

    req = _build_request(
        principal_header=_build_principal_header(_TEST_OID),
        forwarded=_TEST_SECRET,
        body={"question": "show me data"},
    )
    resp = ask_module.ask(req)
    assert resp.status_code == 422
    body = json.loads(resp.get_body())
    _assert_envelope_shape(body)
    assert body["status"] == "validation_error"
    assert body["error"]["code"] == "sql_validation_failed"
    # The on-wire message must not leak the exception class or the
    # offending table name.
    assert "DROP" not in body["error"]["message"]
    assert "fact_Trades" not in body["error"]["message"]
    assert "DisallowedTokenError" not in body["error"]["message"]


def test_ask_rate_limit_returns_429_on_eleventh_request(
    forwarded_secret_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """security MJ-02: 11th request inside the 60-second window returns 429."""
    from function_app.triggers import ask as ask_module
    from tcp.ai.anthropic_client import AnthropicUsage, AskAnswer

    monkeypatch.setattr(
        ask_module,
        "_resolve_scope",
        lambda oid, *, sql_config=None: "admin",
    )
    monkeypatch.setattr(
        ask_module,
        "ask_claude",
        lambda q: AskAnswer(
            sql="SELECT TOP 1 employee_id FROM v_employee_performance",
            answer_template="ok",
            citation="v_employee_performance",
            refused=False,
            usage=AnthropicUsage(),
        ),
    )

    req = _build_request(
        principal_header=_build_principal_header(_TEST_OID),
        forwarded=_TEST_SECRET,
        body={"question": "ping"},
    )

    # Fire the budget; the 11th request must return 429.
    last_status = None
    for _ in range(ask_module._RATE_LIMIT_MAX_REQUESTS):  # noqa: SLF001
        resp = ask_module.ask(req)
        last_status = resp.status_code
    assert last_status in (200, 500)  # depends on whether SQL responds

    resp = ask_module.ask(req)
    assert resp.status_code == 429
    body = json.loads(resp.get_body())
    _assert_envelope_shape(body)
    assert body["status"] == "rate_limited"
    assert body["error"]["code"] == "rate_limited"


def test_ask_happy_path_mocked_anthropic(
    forwarded_secret_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: real SQL exec under SESSION_CONTEXT, real RLS filter, mocked Anthropic."""
    from function_app.triggers import ask as ask_module
    from tcp.ai.anthropic_client import AnthropicUsage, AskAnswer

    monkeypatch.setattr(
        ask_module,
        "_resolve_scope",
        lambda oid, *, sql_config=None: "admin",
    )
    monkeypatch.setattr(
        ask_module,
        "ask_claude",
        lambda q: AskAnswer(
            sql="SELECT TOP 1 employee_id FROM v_employee_performance",
            answer_template="Found {row_count} row.",
            citation="v_employee_performance, top 1",
            refused=False,
            usage=AnthropicUsage(input_tokens=42, output_tokens=21, cache_read_tokens=3500),
        ),
    )

    req = _build_request(
        principal_header=_build_principal_header(_TEST_OID),
        forwarded=_TEST_SECRET,
        body={"question": "How many rows?"},
    )
    resp = ask_module.ask(req)

    assert resp.status_code == 200
    body = json.loads(resp.get_body())
    _assert_envelope_shape(body)
    assert body["status"] == "ok"
    assert body["row_count"] >= 0
    assert body["source"].startswith("v_employee_performance")
    assert body["anthropic"]["cache_read_tokens"] == 3500
    # objects_referenced is surfaced for the SWA citation footer.
    assert "v_employee_performance" in body["objects_referenced"]


def test_ask_rls_scope_enforced(
    forwarded_secret_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trader-scope cross-tenant SELECT is filtered by RLS to zero rows."""
    from function_app.triggers import ask as ask_module
    from tcp.ai.anthropic_client import AnthropicUsage, AskAnswer

    other_trader_id = 99999  # an id that is unlikely to belong to the test caller

    monkeypatch.setattr(
        ask_module,
        "_resolve_scope",
        lambda oid, *, sql_config=None: "trader",
    )
    monkeypatch.setattr(
        ask_module,
        "ask_claude",
        lambda q: AskAnswer(
            sql=(
                f"SELECT * FROM v_employee_performance "
                f"WHERE employee_id = {other_trader_id}"
            ),
            answer_template="{row_count} rows.",
            citation="v_employee_performance",
            refused=False,
            usage=AnthropicUsage(),
        ),
    )

    req = _build_request(
        principal_header=_build_principal_header(_TEST_OID),
        forwarded=_TEST_SECRET,
        body={"question": "show me other trader's data"},
    )
    resp = ask_module.ask(req)
    assert resp.status_code == 200
    body = json.loads(resp.get_body())
    _assert_envelope_shape(body)
    assert body["status"] == "ok"
    # The RLS predicate should hide rows belonging to another trader; the
    # test OID is registered as ``trader`` scope.
    assert body["row_count"] == 0
