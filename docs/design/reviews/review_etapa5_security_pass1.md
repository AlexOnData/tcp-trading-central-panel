# Etapa 5 security review — pass 1

**Reviewer**: backend-security-coder
**Date**: 2026-05-16
**Verdict**: ACCEPT_WITH_CHANGES

---

## Summary

The Etapa 5 codebase introduces a high-quality, defence-in-depth security posture. The critical
path — AAD header validation → scope resolution → Anthropic call → `safe_query` allowlist gate →
RLS-scoped SQL execution — is implemented correctly and the ADR-003 SESSION_CONTEXT contract is
honoured in `tcp/db.py`. All secrets use `SecretStr`; no secrets appear in source.

Three findings require a code change before the next stage merge. Two are security-relevant
(MJ-01: `unsafe-inline` in CSP; MJ-02: missing rate limiting per §8.3 threat model), one is a
data-integrity risk (MJ-03: `validate_proc_call` parameter ordering not enforced). Three minor
findings are hardening-in-depth items. All Critical and Major findings are tractable with small
changes.

**Finding counts**: Critical: 0 | Major: 3 | Minor: 3

---

## Critical

No critical findings.

---

## Major

### MJ-01 | `swa/staticwebapp.config.json:36` | `unsafe-inline` in Content-Security-Policy nullifies XSS protection

**Issue**: The `globalHeaders` CSP is:
```
default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'
```
`'unsafe-inline'` in `script-src` permits inline `<script>` tags and event-handler attributes
(`onclick`, `onload`, etc.), defeating the primary goal of CSP as an XSS second-line defence. An
attacker who finds a stored-XSS or reflected injection vector (for example through a maliciously
crafted `refusal_reason` string if a future path ever sets `innerHTML`) can execute arbitrary JS
in the user's authenticated session.

**Threat**: XSS leading to session credential exfiltration (steals `x-ms-client-principal` cookie
/ bearer token and can issue arbitrary `/api/ask` requests as the victim — OWASP A03 / A05).

**Fix**: `app.js` already uses `textContent` and `createElement` throughout; no inline scripts
exist in `index.html`. Remove `'unsafe-inline'` from both `script-src` and `style-src`. Because
`style.css` is a separate file (`<link rel="stylesheet">`), `'unsafe-inline'` in `style-src` is
also unnecessary.

Recommended replacement:
```json
"Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
```
Add `frame-ancestors 'none'` to replace the missing `X-Frame-Options` header (clickjacking).
Add `object-src 'none'` and `base-uri 'self'` to close Flash/plugin and `<base>` injection
vectors.

---

### MJ-02 | `function_app/triggers/ask.py` | No rate limiting on `/api/ask`

**Issue**: `ask.py` performs an Anthropic API call (latency ~1–5 s, non-trivial cost) on every
request that passes header validation. There is no per-user or per-IP request budget. The
architecture doc `03_architecture.md §8.3` explicitly lists "DoS via cost overrun (Anthropic)" as
an **in-scope threat** with the stated mitigation "10 questions/min/user; circuit-breaker at 1000
tokens/question." Neither control is implemented in the reviewed code.

**Threat**: A single authenticated (but malicious or compromised) user can send hundreds of
requests per minute, burning the Anthropic API quota or inflating the Functions execution budget.
The `max_input_tokens=2000` / `max_output_tokens=600` Pydantic caps reduce per-call cost but do
not bound call frequency (OWASP A05 / Denial-of-Service).

**Fix**: Implement one of the following before the Etapa-6 merge:

1. **Durable per-user counter in Azure Table Storage** (zero extra cost on the free tier): key on
   `oid`, bucket by minute, reject with HTTP 429 when `count > 10`.
2. **Azure API Management** (has a free tier consumption SKU, though it adds a resource):
   rate-limit policy on the `/api/ask` route.
3. **Lightweight in-memory LRU with a TTL** acceptable for single-instance Consumption plan
   (simple, no persistence, resets on cold start — acceptable for thesis scope given the bounded
   user count).

If the fix is deferred, document it explicitly in ADR-004 or a new ADR-005 and add an alert in
Application Insights that fires when `tcp.ask.latency_ms` event rate from a single `oid_suffix`
exceeds 10/min.

---

### MJ-03 | `tcp/safe_query.py:389` | `validate_proc_call` parameter order not enforced — callers must pass correct positional sequence to `cursor.execute`

**Issue**: `validate_proc_call` returns a T-SQL `EXEC` string with `@param = ?` placeholders in
the iteration order of `_PROC_SIGNATURES[proc_name]` (a plain `dict`, which is insertion-ordered
in Python 3.7+ but is not a contract). The function's docstring states: "the caller is responsible
for passing the matching parameter sequence to `cursor.execute`." If any caller passes the `params`
dict values in a different order than `spec` iteration order, the positional `?` binding silently
assigns wrong values to wrong parameters (e.g., `@from_date = to_date_value`).

**Threat**: A future caller implementing `usp_GetEmployeePerformance` or `usp_GetTopPerformers`
could pass data-access parameters out of order, resulting in a date-range reversal or incorrect
`top_n` value. At minimum this is a correctness bug; at worst (if `scope` is confused with
`trader_id`) it widens the visible data slice (OWASP A01 / Broken Access Control).

**Fix**: `validate_proc_call` should return both the parameterised SQL string **and** an ordered
tuple of the bound values, so the caller never needs to reconstruct the binding order:

```python
@dataclass(frozen=True)
class ProcCallResult:
    sql: str          # "EXEC dbo.usp_GetEmployeePerformance @employee_id = ?, ..."
    params: tuple     # (employee_id_val, from_date_val, to_date_val) in spec order

def validate_proc_call(proc_name: str, params: dict[str, Any]) -> ProcCallResult:
    ...
    ordered_values = tuple(params[k] for k in spec)
    placeholders = ", ".join(f"@{k} = ?" for k in spec)
    return ProcCallResult(
        sql=f"EXEC dbo.{proc_name} {placeholders}",
        params=ordered_values,
    )
```

---

## Minor

### MN-01 | `function_app/triggers/ask.py:468` | `SafeQueryError` detail included in HTTP 422 response body

**Issue**: The 422 response body contains
`"detail": f"{type(exc).__name__}: {exc}"`, which surfaces the full exception message from
`safe_query.py`. Example: `"DisallowedObjectError: table or view 'sys.objects' is not in the
allowlist"`. This is an information disclosure of the exact allowlist logic and rejection token to
the caller.

**Threat**: An attacker enumerating the allowlist boundary via crafted questions gains oracle
feedback on exactly which object names/tokens are blocked, reducing the effort needed to craft
bypass payloads (OWASP A09). Severity is low because `safe_query.py` is fail-closed and the
allowlist itself is not a secret, but reducing oracle feedback is a hardening best practice.

**Fix**: Return a generic `"SQL validation failed"` message to the client. Log the full
`exc` detail via `_log.warning` (already done — line 457–462) but strip it from the response:

```python
return _json_response(
    {
        "status": "validation_failed",
        "detail": "SQL validation failed",
        "anthropic": answer.usage.model_dump(),
    },
    422,
)
```

---

### MN-02 | `tcp/ai/prompts.py:282` | `build_user_message` injects `scope` without sanitisation

**Issue**: `build_user_message` interpolates the `scope` string directly into the user-role
context prefix: `f"User scope: {scope}. Question: {question}"`. The `scope` value is resolved from
`dim_UserRoles` (a parameterised database lookup) and is validated against `_ALLOWED_SCOPES` in
`ask.py:218`, so in production this is not exploitable. However, the validation happens in the
trigger layer, not in `build_user_message` itself. If `build_user_message` were called from a new
code path that skipped the scope allowlist check, the raw scope string would be injected into the
Anthropic prompt unescaped.

**Threat**: Prompt injection via a maliciously crafted `dim_UserRoles.scope` database value
(requires prior SQL write access — chained risk). Severity is very low in the current architecture
but a defence-in-depth guard is cheap.

**Fix**: Add a runtime assertion inside `build_user_message`:
```python
_VALID_SCOPES: Final[frozenset[str]] = frozenset(
    {"trader", "team_lead", "floor_manager", "admin"}
)

def build_user_message(question: str, scope: str) -> str:
    if scope not in _VALID_SCOPES:
        raise ValueError(f"build_user_message: invalid scope {scope!r}")
    return f"User scope: {scope}. Question: {question}"
```

---

### MN-03 | `function_app/triggers/bacpac_export.py:253` | `response.text[:512]` in error messages may log sensitive Azure error details

**Issue**: In `_start_export` (line 253) and `_poll_export` (line 295), Azure REST error bodies
are sliced and included in `RuntimeError` messages. These error bodies, when logged via
`log.exception("tcp.bacpac.failed", ...)` at line 450, are forwarded verbatim to App Insights.
Azure SQL/Storage error responses can include internal resource IDs, partial SAS tokens, or SQL
error messages that aid privilege escalation enumeration.

**Threat**: Sensitive Azure resource metadata or partial credentials in application logs (OWASP A09).

**Fix**: Log only the HTTP status code and a fixed error category string in the `RuntimeError`
message. Log the truncated body separately at `debug` level (not `exception`), which can be
suppressed in production telemetry sampling:
```python
_log.debug("tcp.bacpac.api_error_body", body_snippet=response.text[:256])
raise RuntimeError(f"Export API rejected the request: HTTP {response.status_code}")
```

---

## OWASP Top 10 mapping

| Item | Status | Evidence |
|------|--------|----------|
| A01 Broken Access Control | PASS with note | RLS via SESSION_CONTEXT enforced per ADR-003; `dim_UserRoles` scope resolution parameterised; `dim_UserRoles` excluded from LLM allowlist. Note: MJ-03 is a latent access-control risk for future proc callers. |
| A02 Cryptographic Failures | PASS | No secrets in source; `SecretStr` on all sensitive Pydantic fields; TLS enforced on SQL (`Encrypt=yes`, `TrustServerCertificate=no`); storage and management plane calls use HTTPS; Key Vault references for all secrets. |
| A03 Injection | PASS | `safe_query.py` is the single gate; deny-list runs before sqlglot parse; allowlist + AST walk; re-serialised SQL executed, not original input; parameterised `dim_UserRoles` lookup; `validate_proc_call` uses `?` placeholders. |
| A04 Insecure Design | PASS | Fail-closed design in `safe_query`; deny-by-default RLS when SESSION_CONTEXT unset; shared-secret header (`X-SWA-Forwarded`) closes raw Function URL forgery path. |
| A05 Security Misconfiguration | FAIL (MJ-01, MJ-02) | CSP `unsafe-inline` undermines XSS second line defence (MJ-01); missing rate limit is an acknowledged in-scope gap (MJ-02). `staticwebapp.config.json` `forwardingGateway.requiredHeaders` present and correct. |
| A06 Vulnerable Components | NOTE | `anthropic>=0.40` is a floating lower bound. Pin to an exact version (e.g., `anthropic==0.49.0`) in `function_app/requirements.txt` and in CI `pip-audit` to prevent silent upgrades that break the tool-call API shape. |
| A07 Identification & Authentication Failures | PASS | `x-ms-client-principal` validated + `hmac.compare_digest` timing-safe secret check; principal parsed to `UUID` (rejects non-UUID OIDs); `dim_UserRoles is_active=1` check; `_ALLOWED_SCOPES` allowlist on resolved scope; 401/403/404 paths correctly separated. |
| A08 Software & Data Integrity | PASS | No `eval()`; no `innerHTML`; SWA `staticwebapp.config.json` route protection on `/api/ask`; OIDC-only CI deploy (no static secrets). |
| A09 Logging & Monitoring | PASS with note | Structured `structlog` events; OID last-4-chars only in logs; secrets redacted (`_redact`, `SecretStr`; `storageKey: "***"` in BACPAC log). Note: MN-01 and MN-03 reduce verbosity of potentially sensitive detail in logs. |
| A10 SSRF | PASS | No user-supplied URLs are fetched. BACPAC export target URL is composed from validated env-var config; Anthropic `base_url` from env but defaults to the hardcoded constant and is not user-controllable. |

---

## Threat surface delta vs Etapa 4

| New surface | Risk introduced | Mitigated by |
|-------------|-----------------|--------------|
| `POST /api/ask` HTTP trigger | Unauthenticated use of raw Function URL | `x-ms-client-principal` validation + `X-SWA-Forwarded` shared-secret |
| LLM-generated SQL execution | SQL injection via model output | `safe_query.py` deny-list + allowlist + row-limit injection |
| Anthropic API key in use | Key exfiltration, cost amplification | KV reference only; `SecretStr`; 30-second timeout; `max_output_tokens=600` |
| BACPAC export trigger | Storage key leakage, runaway poll loop | `SecretStr` on `storage_account_key`; 30-min / 180-attempt poll ceiling; idempotency blob check |
| SWA `app.js` rendering of DB rows | XSS via data row content | `textContent` / `createElement` DOM API used throughout — no `innerHTML` on user data |
| `refusal_reason` from Anthropic surfaced to browser | Prompt-injection-echo XSS | `textContent` in `showToast()` — safe; but `detail` field in 422 body should be generic (MN-01) |

Compared to Etapa 4 (IaC + CI only), Etapa 5 adds all runtime data-path surfaces. The most
significant net-new risk is the LLM-generated SQL path; `safe_query.py` addresses it with a
multi-layer defence that exceeds the §6.4 spec. The rate-limiting gap (MJ-02) is the only
documented-but-unimplemented mitigation.

---

## Recommendation

**Resolve MJ-01 (CSP `unsafe-inline`)** and **MJ-03 (`validate_proc_call` ordering)** before the
next stage begins — both are small one-file changes. **MJ-02 (rate limiting)** may be deferred
to Etapa 6 if an ADR or TODO entry is created that explicitly documents the residual risk and
commits to the implementation milestone. The three minor findings (MN-01–MN-03) are hardening
items; address them in the same commit as MJ-01/MJ-03 if convenient, or track in backlog.

The overall architecture is sound, the SESSION_CONTEXT lifecycle is correctly implemented, and
`safe_query.py` is a well-designed, fail-closed validation layer. After the major findings are
resolved this codebase should clear a pass-2 review without further architectural changes.
