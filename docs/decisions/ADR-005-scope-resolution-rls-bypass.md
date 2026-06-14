# ADR-005: scope resolution via RLS-bypass connection + single-instance rate limit

- **Status**: Accepted
- **Date**: 2026-05-16
- **Stage**: Etapa 5 (AI assistant body) — surfaced by `review_etapa5_holistic_pass1.md` MA-05 and `review_etapa5_security_pass1.md` MJ-02

## Context

`POST /api/ask` is the only user-facing entry point that combines three
contracts which would otherwise pull in different directions:

1. **RLS via SESSION_CONTEXT** (ADR-003) — every SQL statement that
   reads `fact_Trades` must execute on a connection where
   `sp_set_session_context @key=N'aad_object_id', @value=<oid>` was
   called inside the same session. The user's OID is the only key the
   RLS predicates trust.
2. **Scope resolution before RLS can be honoured** — to phrase the
   answer correctly and to short-circuit unknown principals with a 404,
   the trigger needs the caller's `scope` field from `dim_UserRoles`.
   That row IS the metadata that drives the RLS policy itself; it is
   intentionally excluded from the AI assistant's SQL allowlist.
3. **Best-effort cost control** — the architecture's §8.3 threat model
   acknowledges a DoS-by-cost-overrun risk (Anthropic API spend) and
   committed to a 10 requests/min/user mitigation.

Two consequences:

- A user-driven connection that already has SESSION_CONTEXT set cannot
  see `dim_UserRoles` (the RLS predicate filters everything except the
  caller's own scope row). To resolve the scope, the trigger needs a
  connection without SESSION_CONTEXT — i.e., the `bypass_session_context=True`
  escape hatch documented in ADR-003 §4.
- The Function App's Consumption (Y1) plan auto-scales to multiple
  workers under load. A per-instance rate-limit bucket is the cheapest
  option that requires no extra Azure resources, but it cannot enforce
  a cluster-wide budget — a user who hits N different worker instances
  can issue `10 × N` requests/min.

## Decision

### Scope-resolution RLS bypass

`function_app/triggers/ask.py::_resolve_scope` opens a single
admin-bypass connection (`bypass_session_context=True`), executes one
parameterised SELECT against `dim_UserRoles` keyed on the caller's
`aad_object_id`, and closes the connection within milliseconds.

The bypass is safe because:

- The SQL string is a constant (not concatenated with user input).
- The single bound parameter (`aad_object_id`) is type-bound to a UUID
  before pyodbc sees it — `_parse_principal_header` returns `UUID(...)`
  or `None`, so the parameter cannot carry an injection payload.
- The connection is opened, used for exactly one round-trip, and closed
  before any other user-facing code runs. There is no path that re-uses
  the elevated connection for the LLM-emitted SQL.
- The query returns at most one row, so the elevation cannot be used to
  exfiltrate the rest of the `dim_UserRoles` table.

The bypass connection is closed before the LLM-emitted SQL executes; the
trigger then opens a separate connection via
`tcp.db.connection_for_user(SessionContext(oid=...))` for the user-facing
read, and that connection is the one the RLS policy filters.

### Single-instance rate limit

`function_app/triggers/ask.py::_check_and_record_rate_limit` maintains
an in-process `dict[UUID, deque[float]]` of request timestamps, scoped
to a 60-second sliding window with a 10-request cap per user. Cold
starts clear the bucket; the lock is process-local.

Acceptable for v1.0 because:

- The Y1 plan typically runs one worker for the project's expected
  traffic (≤ 30 users × ≤ 10 requests/h/user during the demo window).
- The Anthropic per-call cost is bounded by `max_output_tokens=600` and
  the prompt cache discount, so even an N-instance worst case is
  bounded by `N × 10 × cost_per_call`.
- The structured log line `tcp.func.ask.rate_limited` lets an operator
  build an App Insights alert that fires on excessive 429s; the alert
  is the cluster-wide signal the in-process counter cannot provide.

### Custom-metrics deferral

The architecture's §3.2.4 monitoring story names
`tcp.ask.latency_ms`, `tcp.ask.input_tokens`, etc. as App Insights
**custom metrics**. The current implementation emits them as structured
log dimensions prefixed `metric_*` (`tcp.ask.metrics` event). Wiring
`azure-monitor-opentelemetry` to publish true `customMetrics` is
deferred to Etapa 8 (production-readiness pass) — KQL queries against
the `traces` table cover the academic-build observability story for
v1.0.

## Alternatives considered

- **Stored proc for scope resolution.** A `usp_GetMyScope` proc executed
  on the user-RLS connection would respect RLS but would not return a
  row for unknown principals — still needs an out-of-band bypass to
  distinguish "not registered" (404) from "registered but admin"
  (admin scope intentionally has zero RLS filtering). Adds a proc to
  maintain without removing the bypass.
- **Azure Table Storage rate-limit ledger.** Durable, cluster-wide,
  free tier. Adds one Azure resource, a Managed Identity grant on the
  table, and a per-request round trip (~10 ms). Acceptable for Etapa 8
  but disproportionate for v1.0 traffic.
- **API Management consumption tier.** Free policy-based rate limit at
  the gateway. Adds an APIM resource (free SKU exists but not zero
  configuration) and a deployment step. Out of scope for v1.0.

## Consequences

- The user-facing `/api/ask` path always opens **two** SQL connections
  (one bypass, one RLS-scoped). The bypass connection has a single
  prepared SELECT; the RLS connection executes the validated LLM SQL.
  Both connections are closed within the trigger's wall-clock budget.
- A worker cold start clears the rate-limit ledger for everyone — a
  user who hits the budget can effectively reset by waiting for the
  worker to scale down (5-minute idle on Y1). Documented; acceptable.
- Future tightening of the bypass to a stored-proc + RLS-aware path
  must update this ADR.

## References

- `docs/decisions/ADR-003-rls-session-context.md` §4 (the bypass
  escape hatch).
- `docs/design/03_architecture.md` §3.2 (user-question path) and §8.3
  (DoS-by-cost-overrun threat row).
- `docs/design/reviews/review_etapa5_holistic_pass1.md` MA-05.
- `docs/design/reviews/review_etapa5_security_pass1.md` MJ-02.
