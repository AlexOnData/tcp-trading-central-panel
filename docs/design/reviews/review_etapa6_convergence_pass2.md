# Etapa 6 convergence review — pass 2

**Reviewer**: security-auditor (verification pass)
**Date**: 2026-05-16
**Verdict**: ACCEPT

---

## Summary

All four pass-1 majors are addressed: `Retry-After` is now emitted on the 429 path via a generic `extra_headers` mechanism in `_envelope`; `staticwebapp.config.json` `globalHeaders` carries the full HSTS + X-Frame-Options + Permissions-Policy set plus a tightened CSP with `form-action 'self'`; `Storage allowSharedKeyAccess: true` is retained with an in-source rationale comment and is captured as accepted residual RR-01 in the new threat-model document; the `schema_history.checksum` TODO is acknowledged as RR-09 with a documented CI follow-up trigger. The four new security documents (`threat_model.md`, `credentials_rotation.md`, `incident_response.md`, `bootstrap_window.md`) total 1331 lines, contain no real secrets, resolve the previously-dangling cross-references, and provide the operational depth that was absent in pass 1. No regressions introduced: the `extra_headers` plumbing is additive (Content-Type is set first, then merged-over only by the caller-supplied keys; the 429 path supplies only `Retry-After`), CSP `form-action 'self'` does not interfere with the AAD redirect-based login flow (which is HTTP 302-driven, not form-submission-driven), and the Permissions-Policy denial set covers only features the chat UI does not use. The single residual is the pre-existing informational `|| true` on the `bicep what-if` PR job inherited from Etapa 4 — explicitly justified in a code comment and not a regression.

---

## Pass-1 ID status table

| ID | Source | Severity | Status | Evidence |
|---|---|---|---|---|
| MJ-01 | security-auditor | Major | **RESOLVED** | `function_app/triggers/ask.py:179` — `_envelope` accepts `extra_headers: dict[str, str] \| None = None`. `ask.py:622` — 429 path passes `extra_headers={"Retry-After": str(int(_RATE_LIMIT_WINDOW_SECONDS))}`. Header merge order at `ask.py:206-208` sets `Content-Type` first then updates with caller-supplied headers — safe because `Retry-After` and `Content-Type` are disjoint keys. |
| MJ-02 | security-auditor | Major | **RESOLVED** | `swa/staticwebapp.config.json:35-42` — `globalHeaders` now carries `Strict-Transport-Security: max-age=63072000; includeSubDomains`, `X-Frame-Options: DENY`, and `Permissions-Policy: accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()`. CSP also extended with `form-action 'self'`. |
| MJ-03 | security-auditor | Major | **ACCEPTED RESIDUAL** | `infra/modules/storage.bicep:48-56` — `allowSharedKeyAccess: true` retained with an inline 8-line rationale citing `AzureWebJobsStorage` boot-path dependency and pointing to `docs/security/credentials_rotation.md` for the migration path. Captured as RR-01 in `threat_model.md` §7 and §3 (asset table). Acceptable for thesis posture. |
| MJ-04 | security-auditor | Major | **ACCEPTED RESIDUAL** | Documented as RR-09 in `threat_model.md` §7 with follow-up trigger "Implement CI-computed SHA-256 checksums (MJ-04 procedure) before any multi-developer or production deployment." Acceptable. |
| MJ-01 | backend-security-coder | Major | **RESOLVED** | Same fix as auditor MJ-01. |
| MJ-02 | backend-security-coder | Major | **RESOLVED** | Same fix as auditor MJ-02 (HSTS + X-Frame-Options + Permissions-Policy all landed in one commit). |
| MN-01 | both | Minor | NOT RESOLVED (informational) | `ping.py:54` still has `SELECT @@VERSION`. Not addressed in this pass; out of scope for the Etapa-6 security hardening commit. Tracking remains in the pass-1 reports. |
| MN-02 | both | Minor | NOT RESOLVED (informational) | OID-suffix length discrepancy (`oid.hex[-8:]` vs `oid_str[-4:]`) not aligned. Cosmetic drift; ADR-003 wording unchanged. |
| MN-03 (auditor) | security-auditor | Minor | NOT RESOLVED (informational) | `usp_DeleteEmployeeData` not added; synthetic-data scope documented in `threat_model.md` §9 GDPR row. Acceptable for thesis posture. |
| MN-04 (auditor) | security-auditor | Minor | NOT RESOLVED (informational) | `_open_raw_connection` still logs redacted conn-string at INFO. Cosmetic. |
| MN-05 (auditor) | security-auditor | Minor | NOT RESOLVED (informational) | `rolesSource: "/api/auth/roles"` still references a non-existent endpoint. Unchanged. |
| MN-06 (auditor) | security-auditor | Minor | NOT RESOLVED (informational) | `assert conn is not None` unchanged in `seed_employees.py`. Cosmetic; Functions Linux runtime does not run `-O`. |
| MN-07 (auditor) | security-auditor | Minor | **RESOLVED** | `docs/security/bootstrap_window.md` now exists (101 lines); cross-doc forward reference from `threat_model.md` §6 and `incident_response.md` Scenario D resolves correctly. |
| MN-08 (auditor) | security-auditor | Minor | NOT RESOLVED (informational) | `safe_query.py` catalog allowlist still accepts `""`, `"tcp"`, `"tcp_dev"`. Defense-in-depth point; the table allowlist remains the load-bearing gate. |
| MN-09 (auditor) | security-auditor | Minor | NOT RESOLVED (informational) | `<a href="/.auth/logout">` missing `rel="noopener noreferrer"`. Marginal. |
| MN-10 (auditor) | security-auditor | Minor | NOT RESOLVED (informational) | No `responseOverrides` section. Marginal. |
| MN-03 (backend) | backend-security-coder | Minor | NOT RESOLVED (informational) | `usp_GenerateDailyTrades` `@trades` parameter NVARCHAR(MAX) compliance not re-verified in this pass; carried forward as documentation TODO. |
| MN-04 (backend) | backend-security-coder | Minor | NOT RESOLVED (informational) | Token-count heuristic unchanged. Acceptable. |
| MN-05 (backend) | backend-security-coder | Minor | NOT RESOLVED (informational) | Same as MN-05 (auditor). |
| MN-06 (backend) | backend-security-coder | Minor | NOT RESOLVED (informational) | Same as MN-06 (auditor). |
| MN-07 (backend) | backend-security-coder | Minor | NOT RESOLVED (informational) | Same as MN-04 (auditor). |
| MN-08 (backend) | backend-security-coder | Minor | **RESOLVED** | `Permissions-Policy` added in `globalHeaders`. |

---

## New docs sanity check

| Document | Lines | Completeness | Verdict |
|---|---|---|---|
| `docs/security/threat_model.md` | 344 | 11 STRIDE surface matrices (POST /api/ask, GET /api/ping, BACPAC export, daily generator, warmup, raw Function URL, SQL public endpoint, KV public endpoint, Storage public endpoint, GitHub Actions runners, developer workstation); 7 trust boundaries (TB-1 through TB-7); 4 in-scope adversaries (A1–A4) plus 2 out-of-scope (A5–A6); 9 residual risks (RR-01 through RR-09); OWASP Top 10 mapping with verdicts; GDPR + data-residency + audit-trail compliance section; change history. | **COMPLETE** |
| `docs/security/credentials_rotation.md` | 382 | 6 secrets inventoried (Anthropic key, SQL-admin-export password, SWA forwarded secret, storage connection string, OIDC federated credentials, bootstrap SQL admin password); each with KV name + Function App setting + cadence + impact-of-exposure + impact-of-rotation-downtime + verification + step-by-step Azure CLI procedure; Year-1 rotation schedule (Q1–Q4 2027); lost-secret 6-step playbook with post-mortem skeleton; change history. | **COMPLETE** |
| `docs/security/incident_response.md` | 504 | 4-severity matrix (P0–P3); 6 detection sources; 6 incident scenarios (cross-tenant RLS leak P0, Anthropic key compromise P1, OIDC SP compromise P0, SQL admin password leak P1, Function MI compromise P0, Anthropic spend spike P2) each with KQL + Azure CLI containment steps and post-mortem trigger; communication template; recovery validation checklist across 6 layers; change history. | **COMPLETE** |
| `docs/security/bootstrap_window.md` | 101 | Defines the window, explains why it cannot be zero (Free Offer constraint), provides expected duration breakdown (3–8 min), enumerates 6 mitigations, gives operator before/during/after behaviour checklist, documents when the window re-opens, captures RR-08 residual acceptance, cross-references `threat_model.md` §6 / `credentials_rotation.md` / `incident_response.md` Scenario D / ADR-004; change history. | **COMPLETE** |

Cross-reference integrity verified: `threat_model.md` §6 → `bootstrap_window.md` (matches); `threat_model.md` §7 RR-08 → `bootstrap_window.md` (matches); `incident_response.md` Scenario D → `credentials_rotation.md` §2.2 (matches); `credentials_rotation.md` §2.4 → RR-01 in `threat_model.md` (matches); `bootstrap_window.md` → `incident_response.md` Scenario D / `credentials_rotation.md` (matches); `storage.bicep:53` → `docs/security/credentials_rotation.md` (matches). No dangling references.

Secrets sanity: no `sk-ant-...` literal, no `AccountKey=…` base64 blob, no `<TENANT_ID>` placeholders left un-bracketed in production-context examples (the `<TENANT_ID>` substring in `staticwebapp.config.json:8` is the documented postprovision placeholder, not a leaked value). `<offending-aad-object-id>`, `<new-key-value>`, `<APP_ID>` etc. are explicit human placeholders.

English-only verified: every line in all four new documents is English; no Romanian artifacts.

---

## Regressions

### 1. `extra_headers` plumbing in `_envelope`

The mechanism (`ask.py:179-214`) declares `extra_headers: dict[str, str] | None = None`, initialises `headers = {"Content-Type": "application/json"}` first, then `headers.update(extra_headers)` if non-None. This means a caller could theoretically override `Content-Type` (e.g., by passing `extra_headers={"Content-Type": "text/html"}`), but no current caller does — only the 429 path uses it, and it passes `{"Retry-After": "60"}`. The risk is theoretical and well-bounded (single internal helper, two call sites). **NOT A REGRESSION.** A defensive `headers.pop("Content-Type", None)` from extra_headers would be belt-and-suspenders, but the current shape is acceptable.

The `func.HttpResponse(...)` constructor receives both `headers=headers` and `mimetype="application/json"`. Azure Functions Python worker behaviour: `mimetype` and `headers["Content-Type"]` coexist with `headers["Content-Type"]` winning when both are present. No duplicate `Content-Type` header is emitted on the wire (Azure Functions deduplicates). **NOT A REGRESSION.**

### 2. `form-action 'self'` in CSP

The `form-action` CSP directive controls the `action=` URL of any `<form>` submission. The AAD login flow uses HTTP 302 redirects to `https://login.microsoftonline.com/<tenant>/oauth2/v2.0/authorize?...` initiated by the SWA platform's `/.auth/login/aad` redirect, **not** by a `<form action="...">` element. The chat UI's only `<form>` is the question-submission form in `swa/index.html`, which submits via JavaScript `fetch('/api/ask', ...)` (same-origin POST) — `fetch` is not affected by `form-action` (that directive scopes only to `<form>` `action=` attributes used by classic form submission). **NOT A REGRESSION.**

### 3. Permissions-Policy denial set

`accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()` — every directive denied across all origins. The chat UI in `swa/index.html` + `swa/app.js` does not invoke any of these APIs (no `navigator.mediaDevices`, no `navigator.geolocation`, no `PaymentRequest`, no `navigator.usb`, no `DeviceMotionEvent`). The denial set is correctly scoped and does not break any functionality. **NOT A REGRESSION.**

### 4. HSTS `max-age=63072000; includeSubDomains`

Two-year max-age with subdomain inclusion. The SWA-managed certificate covers the apex domain; subdomain inclusion is sound because SWA does not provision additional non-HTTPS subdomains. The `preload` directive is intentionally omitted (would require submission to the Chromium HSTS preload list and is irrevocable for ~6 months). **NOT A REGRESSION.**

### 5. `X-Frame-Options: DENY` + CSP `frame-ancestors 'none'`

Both headers emitted simultaneously. Modern browsers prefer `frame-ancestors`; legacy IE11 / corporate proxies parse only `X-Frame-Options`. The two headers do not conflict — they say the same thing. **NOT A REGRESSION.**

### 6. `|| true` survey

Grep of the entire repository for `|| true` shows the single inherited Etapa-4 informational `bicep what-if` mask at `.github/workflows/ci.yml:236` (explicitly justified by the inline comment at `ci.yml:224`), plus shell-helper scripts in `.claude/skills/` (out of project scope) and `.claude/STATE.md` documentation references. **NO NEW MASKS ADDED.**

---

## Remaining gaps

- **MJ-03 (Storage `allowSharedKeyAccess`)**: accepted residual RR-01. Migration to identity-based `AzureWebJobsStorage` deferred to a follow-up pass. Acceptable per documented thesis posture.
- **MJ-04 (`schema_history.checksum` TODO)**: accepted residual RR-09. CI-computed SHA-256 deferred to Etapa 8. Acceptable.
- **MN-01 (`SELECT @@VERSION` in ping)**: not addressed; recommend swapping to `SELECT 1` in a small follow-up commit. Information-disclosure surface only.
- **MN-02 (OID suffix length drift)**: not addressed; cosmetic. ADR-003 §3 wording could be aligned in the same follow-up commit.
- **MN-04 (conn-string log at INFO)**: not addressed; demote to DEBUG when convenient.
- **MN-05 (missing `rolesSource` endpoint)**: not addressed; deploy-time verification recommended. The `authenticated` gate on `/api/ask` remains effective via SWA's built-in role assignment even if `rolesSource` 404s — verified by reading `staticwebapp.config.json` `routes` block.
- **MN-06 (`assert` in production)**: not addressed; cosmetic but worth a one-line fix when next touching that file.
- **MN-08 (catalog allowlist `{"tcp", "tcp_dev"}`)**: not addressed; defence-in-depth only. The table allowlist remains the load-bearing gate.

None of these gaps block the Etapa-6 v1.0 tag. All are documented in the pass-1 reports as minor / informational.

---

## Recommendation

**ACCEPT.** All four pass-1 majors are addressed: two via direct code fixes (MJ-01 `Retry-After`, MJ-02 HSTS / X-Frame-Options / Permissions-Policy), two via documented residuals with follow-up triggers (MJ-03 storage shared-key access in RR-01, MJ-04 schema-history checksum in RR-09). The four new security documents (1331 lines total) provide the operational depth (threat model, credentials rotation, incident response, bootstrap window) that pass 1 flagged as missing for the production-readiness claim; for the thesis posture they substantially exceed the academic minimum. No regressions introduced by the `extra_headers` plumbing or by the extended `globalHeaders` block: the header-merge order is safe, CSP `form-action 'self'` does not interfere with the AAD redirect-based login flow, and the Permissions-Policy denial set covers only features the chat UI does not use. Etapa 6 is converged; proceed to the next stage.
