# Etapa 6 backend security sweep

**Reviewer**: backend-security-coder
**Date**: 2026-05-16
**Verdict**: ACCEPT_WITH_CHANGES

---

## Summary

The codebase demonstrates a mature, layered security posture for a thesis-grade project. The SQL injection surface is well-closed: every `cursor.execute` call uses parameterised bindings or static literals, and the `safe_query.py` AST + deny-list pipeline erects a robust second gate in front of LLM-emitted SQL. Two findings require changes before production traffic: (1) the 429 response on `/api/ask` omits the mandatory `Retry-After` header, which browsers and SWA caching layers cannot honour; and (2) the `globalHeaders` block in `staticwebapp.config.json` is missing `Strict-Transport-Security` and `X-Frame-Options`, leaving two standard header expectations unfulfilled. All other findings are minor or informational.

---

## Critical

None.

---

## Major

### MJ-01 | `function_app/triggers/ask.py:609-615` | 429 response omits `Retry-After` header | Threat: Client-side retry storms, SWA caching confusion | Fix: add `headers={"Retry-After": str(int(_RATE_LIMIT_WINDOW_SECONDS))}` to the `func.HttpResponse(...)` call inside `_envelope` when `http_status == 429`, or pass it as an extra kwarg at the specific return site.

RFC 6585 §4 requires `Retry-After` on 429. Without it, naive client retry loops can hammer the endpoint as fast as the network allows and a misbehaving SWA edge node may not respect the rate-limit intent. The in-process ledger already correctly records the timestamp and returns `False`; the only gap is the missing header on the wire response. The current `_envelope` helper has no `headers` parameter, so the fix requires either adding one or constructing the `func.HttpResponse` directly at the 429 return site.

### MJ-02 | `swa/staticwebapp.config.json:35-39` | Missing `Strict-Transport-Security` and `X-Frame-Options` in `globalHeaders` | Threat: Downgrade attacks, clickjacking from non-SWA origins | Fix: add `"Strict-Transport-Security": "max-age=63072000; includeSubDomains"` and `"X-Frame-Options": "DENY"` to the `globalHeaders` block.

The CSP already includes `frame-ancestors 'none'`, which is the modern equivalent of `X-Frame-Options: DENY` and takes precedence in modern browsers. However, Internet Explorer 11 and some legacy corporate proxies parse only `X-Frame-Options`. Adding it costs nothing. `Strict-Transport-Security` is absent: Azure Static Web Apps serves exclusively over HTTPS, so HSTS should be emitted to prevent the initial HTTP request that a downgrade attacker could intercept. The `max-age=63072000` (two years) value matches HSTS preload requirements.

---

## Minor / nits

### MN-01 | `function_app/triggers/ping.py:54-56` | `SELECT @@VERSION` leaks SQL Server build string to anonymous callers | Threat: Reconnaissance (version-specific exploit targeting) | Fix: replace with `SELECT 1`; log `@@VERSION` server-side only. The warmup trigger correctly uses `SELECT 1`; ping should match.

### MN-02 | `function_app/triggers/ask.py:542` | OID logged as `oid.hex[-8:]` (8 hex chars = 4 bytes) vs. documented "last 4 chars" | Threat: Marginal privacy overexposure in logs | Fix: align with the ADR-003 §3 stated policy. `hex[-8:]` emits 4 bytes (8 hexadecimal digits). The docstring in `db.py:252` says "last 4 chars of the OID". The discrepancy is cosmetic but creates a documentation/implementation drift that could confuse future maintainers. The current amount is not a material privacy risk given the OID's non-sensitive nature, but consistency is important.

### MN-03 | `tcp/synth/runner.py:331` | `cursor.execute(_SQL_EXEC_PROC, target, payload_json)` passes `payload_json` (up to thousands of chars) as a single NVARCHAR parameter | Threat: SQL Server NVARCHAR(MAX) length limit compliance | Fix: no code change required, but document that `usp_GenerateDailyTrades` must accept `@trades NVARCHAR(MAX)` (not a sized type). Verify in the V001 migration; if a sized column is used, truncation silently corrupts rows.

### MN-04 | `tcp/ai/anthropic_client.py:284` | Token-count estimate uses 4-chars/token heuristic without adjusting for multi-byte Romanian characters | Threat: `PromptTooLargeError` may not fire for questions at the boundary that contain dense Unicode | Fix: document the known inaccuracy; consider capping at 400 chars/token-estimate or using `anthropic.count_tokens()` in a follow-up. This is a best-effort guard, not a hard safety control.

### MN-05 | `swa/staticwebapp.config.json:5` | `rolesSource: "/api/auth/roles"` references an HTTP trigger that does not exist in the current codebase | Threat: SWA falls back to anonymous role assignment if the endpoint 404s; the `authenticated` gate on `/api/ask` could silently degrade | Fix: either implement the roles endpoint or remove the `rolesSource` key so SWA uses its built-in role assignment. Verify in deployment that the current route table still enforces `allowedRoles: ["authenticated"]` on `/api/ask` even when `rolesSource` is misconfigured.

### MN-06 | `tcp/synth/seed_employees.py:333` | `assert conn is not None` in production code path | Threat: `AssertionError` is not a `RuntimeError`; would surface as an opaque exception in the generator trigger | Fix: replace with an explicit `if conn is None: raise RuntimeError(...)` guard. Python strips `assert` when run with `-O` (optimised bytecode), making this a correctness risk in any deployment that enables Python optimisation.

### MN-07 | `tcp/db.py:196-199` | `_open_raw_connection` logs the full (redacted) connection string at INFO level on every connection open | Threat: Log verbosity — if `_redact` has an unforeseen miss (e.g., a non-standard ODBC key), partial credentials appear in App Insights | Fix: demote to DEBUG level, or emit only `server`, `database`, `auth_mode` (already separate fields) and drop `conn_str` from the log call entirely.

### MN-08 | `swa/staticwebapp.config.json` | Missing `Permissions-Policy` header | Threat: Browser features (camera, mic, geolocation) available by default | Fix: add `"Permissions-Policy": "camera=(), microphone=(), geolocation=()"` to `globalHeaders`. Low risk given the application type, but completing the standard header set is best practice.

---

## Public-surface contract matrix

| Surface | Input validation | AuthN | AuthZ | Rate limit | Output encoding | Verdict |
|---|---|---|---|---|---|---|
| `POST /api/ask` | Body: JSON parse guarded, type-checked, 500-char cap enforced. Headers: forwarded-secret timing-safe, principal base64+JSON parsed, UUID type-bound. | `x-ms-client-principal` (AAD OID, UUID-typed); forwarded-secret first. | `dim_UserRoles` scope lookup, parameterised, allowlist-checked (`_ALLOWED_SCOPES`). | In-process sliding window 10 req/60 s per OID; thread-safe under `_RATE_LIMIT_LOCK`. **Missing `Retry-After` header (MJ-01).** | Typed `_TcpJsonEncoder`; static error strings; no user input echoed. | ACCEPT_WITH_CHANGES (MJ-01) |
| `GET /api/ping` | No body; no query params consumed. `req` is explicitly deleted at line 47. | Anonymous (intentional). | None (anonymous route). | None — documented gap, delegated to SWA platform. **DoS surface via `SELECT @@VERSION` (MN-01).** | Static JSON (`status`, `sql_resume_ms`, `db_version`); no user content. | MINOR (MN-01) |
| `TimerTrigger_DailyGenerator` | No external input; reads `TCP_GENERATOR_OID` env (UUID-validated). | MI-based connection; `set_admin_session_context` per ADR-003. | Admin scope only; RLS BLOCK predicate enforced at DB level. | Timer platform; single fire per weekday 07:00 RO. | Structured logs only; no external output. | PASS |
| `TimerTrigger_WarmupTrigger` | No external input; `bypass_session_context=True` explicit. | MI-based connection. | Admin scope bypass, documented in ADR-005. | Timer platform. | Structured logs only. | PASS |
| `TimerTrigger_BacpacExport` | No external input; config validated via Pydantic `BacpacConfig`. `SecretStr` on both secrets. | `DefaultAzureCredential` (MI in prod). | `SQL DB Contributor` + `Storage Blob Data Contributor` RBAC; minimal scope. | Timer platform; idempotency via blob HEAD. | Structured logs; secrets redacted before emission. | PASS |

---

## SQL injection deep-walk

Every `cursor.execute` call in production code is catalogued below. No call uses string concatenation or f-string interpolation of external input.

| Location | SQL | Binding method | Security disposition |
|---|---|---|---|
| `tcp/db.py:264` | `EXEC sp_set_session_context @key=N'aad_object_id', @value=?, @read_only=1` | `oid_str` (str coercion of UUID) | SAFE — parameterised; UUID type-bound upstream |
| `tcp/db.py:270` | `EXEC sp_set_session_context @key=N'aad_object_id', @value=NULL, @read_only=0` | Static literal | SAFE — no user input |
| `tcp/db.py:310` | Same SET_CONTEXT template | `str(mi_object_id)` (UUID) | SAFE — parameterised; UUID origin |
| `tcp/db.py:329` | `SELECT CAST(SESSION_CONTEXT(...) AS UNIQUEIDENTIFIER)` | Static literal | SAFE — no user input |
| `triggers/ping.py:54` | `SELECT @@VERSION` | Static literal | SAFE — no parameters. See MN-01 for information-disclosure concern |
| `triggers/warmup.py:51` | `SELECT 1` | Static literal | SAFE |
| `triggers/ask.py:341-345` | `SELECT TOP 1 scope FROM dbo.dim_UserRoles WHERE aad_object_id = ? AND is_active = 1` | `str(oid)` (UUID) | SAFE — parameterised; single column, TOP 1, no user-controlled string |
| `triggers/ask.py:380` | `validated.sanitized_sql` (re-serialised by sqlglot) | No parameters (LLM SQL has no `?` binding; all literal values are part of the AST-validated+re-serialised string) | SAFE — the `validate()` pipeline ensures: (a) single SELECT only; (b) allowlisted objects; (c) deny-list pre-scan; (d) AST re-serialisation from sqlglot's own IR, not from the raw LLM string. See SQL validation deep-dive below. |
| `tcp/synth/runner.py:195` | `SELECT TOP 1 calendar_date FROM dbo.dim_Date WHERE calendar_date < ? ...` | `today` (Python `date` object) | SAFE — parameterised; date type |
| `tcp/synth/runner.py:331` | `EXEC dbo.usp_GenerateDailyTrades @trade_date=?, @trades=?` | `target` (date), `payload_json` (str from `json.dumps`) | SAFE — parameterised. Payload is JSON-serialised from Python objects (not user input). The SP must accept `NVARCHAR(MAX)` — see MN-03. |
| `tcp/synth/runner.py:130,139,169,183` | Static SELECT queries against `dim_Employees`, `dim_Markets`, `dim_Sessions`, `dim_OrderType` | No parameters (no external input) | SAFE |
| `tcp/synth/seed_employees.py:339,344,349` | Static COUNT queries | `_COMPANY_ID` (int constant) or no params | SAFE |
| `tcp/synth/seed_employees.py:357-367` | `MERGE dbo.dim_Employees ... USING (SELECT ? AS company_id, ...)` | 8 positional `?` placeholders, all from `_EmployeeRow` (Faker-generated, never from external HTTP input) | SAFE |
| `tcp/synth/seed_employees.py:372` | `SELECT employee_id FROM dbo.dim_Employees WHERE email = ?` | `emp.email` (Faker-generated) | SAFE |
| `tcp/synth/seed_employees.py:402` | `UPDATE dbo.dim_Employees SET manager_employee_id = ? WHERE employee_id = ?` | `manager_id`, `email_to_id[emp.email]` (ints from DB lookups) | SAFE |
| `tcp/synth/seed_employees.py:409-416` | `MERGE dbo.dim_Accounts ...` | `account_code`, `employee_id`, `"live"`, `"EUR"`, `hire_date` (all from Faker/constants) | SAFE |

### SQL validator deep-walk (`tcp/safe_query.py`)

The `validate()` pipeline runs in this order:

1. **Strip + empty check** — trivial.
2. **`_normalise_for_denylist_scan`** — NFKC unicode normalisation (closes look-alike bypass, ai MA-01) + string-literal masking (closes false-positive on `WHERE name LIKE '%INTO%'`, ai MA-03) + explicit control-character rejection.
3. **Deny-list token scan** — 34 regex patterns covering comments (`--`, `/*`), all DML/DDL keywords, system procs (`sp_set_session_context`, `sp_executesql`, `xp_*`), and external data sources (`OPENROWSET`, `OPENDATASOURCE`). **Potential gap**: the deny-list uses `re.IGNORECASE` and word-boundary anchors, which is correct for ASCII. NFKC normalisation upstream handles Unicode confusables. The masking of string literals before the scan prevents `WHERE type = 'DELETE'` from firing the `DELETE` pattern — correct.
4. **`sqlglot.parse` single-statement check** — multi-statement payloads (`;`-separated) are rejected.
5. **`_unwrap_select`** — non-SELECT top-level statements are rejected, including `Union`, `Except`, `Intersect`.
6. **`_walk_and_validate`** — CTE recursion with per-CTE row-limit injection (ai MA-04), UNION/EXCEPT/INTERSECT AST node rejection, nested DML/DDL node rejection, `SELECT INTO` arg check, table allowlist (schema-qualified to `dbo` only, cross-database references rejected), function two-pass (anonymous UDFs vs built-in scalars). Proc names inside SELECT expressions are explicitly rejected.
7. **`_enforce_row_limit`** — `TOP n` injected or clamped; `OFFSET ... FETCH NEXT n` validated.
8. **`parsed.sql(dialect="tsql")`** — re-serialises from sqlglot's own IR, so the executed string is never the raw LLM string.

**One residual concern**: the `catalog` allowlist at `safe_query.py:628` permits catalog names `""`, `"tcp"`, and `"tcp_dev"`. If the Azure SQL server is configured with linked servers or if a future database is named `tcp`, a cross-database reference like `tcp.dbo.dim_Employees` would pass the catalog check and proceed to the table allowlist. The table name check would then catch it only if `dim_Employees` is in `ALLOWED_DIMS` — which it is, so the query would execute successfully. This is not a vulnerability (the table allowlist still gates), but it documents that the catalog check does not provide additional restriction beyond what the table allowlist already provides. Tightening the catalog to `{""}` (unqualified only) would be a belt-and-suspenders improvement.

---

## Authentication / authorisation deep-dive

### `x-ms-client-principal` parsing (`ask.py:215-258`)

- **Malformed base64**: caught by `binascii.Error`.
- **Invalid JSON**: caught by `json.JSONDecodeError`.
- **Missing `claims` key**: `body.get("claims") or []` returns `[]`; the loop produces no match; falls through to `userId` fallback.
- **Empty claims array**: same path — falls through to `userId` fallback.
- **Non-list `claims`**: explicit `isinstance(claims, list)` check; returns `None` → 401.
- **Claim with empty `val`**: `UUID("")` raises `ValueError` → returns `None` → 401. Correct.
- **Empty `oid`**: same as above — UUID constructor rejects the empty string.
- **Malformed `identityProvider`**: not parsed; only `claims` and `userId` are consumed. No risk.
- **`userId` fallback**: accepts a UUID-parseable string when no OID claim is present. This is intentional for the local SWA emulator and is safe because the OID is validated against `dim_UserRoles` in the next step regardless of which field it was sourced from.

### `X-SWA-Forwarded` shared secret (`ask.py:261-272`)

- `hmac.compare_digest` is timing-safe: correct.
- Empty `expected` (env var not set): `not expected` branch returns `False` with a logged error — fail-closed. Correct.
- No early-return on length mismatch: `hmac.compare_digest` in CPython performs a constant-time comparison independent of string length when both operands are `str`. Correct.
- The check runs **before** any other processing, including the principal parse: correct sequencing per ai MA-05.

### Scope resolution (`ask.py:319-360`)

- Parameterised SQL (`WHERE aad_object_id = ? AND is_active = 1`): correct.
- `TOP 1` prevents multi-row result surprises. However, `cursor.fetchone()` would also naturally take only the first row even without `TOP 1`; the `TOP 1` is belt-and-suspenders — good.
- Post-query allowlist check (`scope in _ALLOWED_SCOPES`): prevents an unexpected scope string from reaching the Anthropic prompt even if `dim_UserRoles` were corrupted with an out-of-range value.
- `bypass_session_context=True` usage: the connection closes within the `try/finally` block immediately after the single-row SELECT. The elevated connection lifetime is ~milliseconds and it is never passed to user-driven code paths. The escape hatch is narrow and correctly documented in ADR-005.
- **Future contributor risk**: `open_connection(bypass_session_context=True)` requires an explicit kwarg, which is a good guard. The function raises `AuthError` if called without the flag. No discovered code path accidentally reuses this connection for subsequent user queries.

### Rate-limit implementation (`ask.py:280-311`)

- The ledger is `dict[UUID, deque[float]]` mutated under `threading.Lock`: thread-safe within a single process.
- Sliding window: `bucket.popleft()` until `bucket[0] >= cutoff` ensures old entries are evicted on every check, keeping memory bounded.
- The timestamp is recorded (appended) only when the request is allowed: correct — a rejected request does not consume a slot.
- The cursor is **not** held open during the rate check: the `open_connection` for scope resolution is closed before `_check_and_record_rate_limit` is called. Correct.
- **Missing `Retry-After` header**: see MJ-01.

---

## Logging / telemetry verification

- `_redact()` covers `PWD=` and `Password=` with `re.IGNORECASE`: correct.
- OID in `connection_for_user`: `oid_str[-4:]` — 4 chars. OID in `ask.py:542`: `oid.hex[-8:]` — 8 hex chars (4 bytes). Documented discrepancy in MN-02.
- `SecretStr` fields: `AnthropicConfig.api_key`, `BacpacConfig.sql_admin_password`, `BacpacConfig.storage_account_key`. All `repr`/`str` calls on these produce `**********`. No `get_secret_value()` call outside of the actual API call site or the BACPAC payload construction. Correct.
- Stack traces: the `except Exception` handler in `ask()` logs via `_log.error(..., error=str(exc)[:200])` (truncated) and returns a static error envelope with no trace content. Correct.
- Anthropic error logging: `str(exc)[:200]` truncation applied. Correct.
- `tcp.bacpac.start_api_error_body`: Azure response body logged at DEBUG with a 256-char snippet. Acceptable — DEBUG is typically not forwarded to App Insights in production sampling.

---

## Frontend XSS / CSRF verification

- `renderAnswer`, `renderModelDecline`, `buildAnswerMeta`, `buildSourceCitation`, `buildClaudeBadge`, `buildResultTable`: all use `el()` which calls `node.textContent = textContent`. No `innerHTML` with user-controlled strings anywhere in the file. Confirmed by grep (zero matches).
- `el()` sets attributes via `node.setAttribute(key, attrs[key])`: attribute values from `payload` fields (e.g., `payload.source`, `objects[i]`) are set as text content, not as HTML. Correct.
- No `eval()`, no `Function('...')`, no `document.write` anywhere in `app.js` or `index.html`. Confirmed by grep.
- CSRF: `/api/ask` is authenticated via `x-ms-client-principal` (server-injected by SWA; the browser cannot forge it) plus the forwarded-secret (injected by `forwardingGateway`). The browser never sends these headers manually. SameSite cookie semantics apply at the SWA layer. No additional CSRF token is needed given the architecture.
- CORS: all requests go to the same SWA origin; the backend is a "linked backend" proxy. No `Access-Control-Allow-Origin: *` is set.
- CSP: `default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'`. No `unsafe-inline`. No `unsafe-eval`. Correct.

---

## Recommendation

1. **Fix MJ-01** (missing `Retry-After` on 429) before production traffic. Add `headers={"Retry-After": "60"}` to the 429 `func.HttpResponse` — a one-line change.
2. **Fix MJ-02** (missing HSTS + X-Frame-Options) in `staticwebapp.config.json` before deployment. Two header entries.
3. **Fix MN-06** (`assert conn is not None` in `seed_employees.py`) — replace with `RuntimeError` to survive Python `-O` deployments.
4. **Fix MN-01** (`SELECT @@VERSION` in ping) — swap to `SELECT 1` and log the version server-side if needed. This reduces the information-disclosure surface of the only anonymous endpoint.
5. **Defer MN-02** (OID suffix length discrepancy) — document the chosen policy in ADR-003 and align the two call sites. Low urgency.
6. **Defer MN-05** (missing `rolesSource` endpoint) — verify in the Azure SWA deployment that the missing endpoint does not silently degrade the `authenticated` role gate, then either implement or remove the `rolesSource` key.

The SQL validation pipeline in `safe_query.py` is the strongest technical element in the codebase: the deny-list + AST + re-serialisation tri-layer defence substantially exceeds what most production AI-assistant backends implement. No injection vector was found through this pipeline.
