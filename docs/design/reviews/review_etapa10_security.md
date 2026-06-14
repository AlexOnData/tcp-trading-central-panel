# Review — Etapa 10: Cross-cutting Security Re-Validation

| Field | Value |
|---|---|
| **Date** | 2026-05-16 |
| **Reviewer role** | security-auditor (final cross-cutting pass before `v1.0-mvp` tag) |
| **Scope** | Re-validation of E1..E9 against the canonical threat model; no live runtime auditing (no Azure subscription) |
| **Verdict** | **APPROVED FOR `v1.0-mvp` TAG** with two Minor follow-ups recommended pre-tag |

---

## Verdict summary

The post-E9 surface holds the security posture established in Etapa 6 and tightened in Etapa 8. The five threats most worth re-checking on this pass — PowerBI SP credential lifecycle, alert action-group fail-open, audit hashing strength, doc-visibility of the bootstrap window, and CI/CD supply-chain additions in `cd.yml` smoke — all check out at a thesis-grade security bar, with two minor doc-hygiene observations called out below. RR-09 closure is real (substitution + CI gate + CD gate all wired through three artefacts on disk, not a doc edit). The PII redaction enforcement test (`tests/integration/test_telemetry_no_pii.py`) is the strongest test in the suite and converts what would otherwise be a "review the code carefully" commitment into a CI-enforced contract across all eight paths.

Zero Critical findings. Zero Major findings. Two Minor findings (both doc-hygiene, neither blocks the tag). Five Strengths called out for the thesis defence narrative.

The recommended path is to **cut the `v1.0-mvp` tag now** and address `sec10-MN-01` and `sec10-MN-02` in the Etapa-12 polish pass.

---

## Critical (0)

None.

---

## Major (0)

None.

---

## Minor (2)

### sec10-MN-01 — `credentials_rotation.md` Year-1 schedule does not reference the `POWERBI-SP-CLIENT-SECRET` quarterly drill explicitly

**Location**: `docs/security/credentials_rotation.md:308-318` (§3 Year-1 Rotation Schedule).

**Summary**: §2.7 documents the PowerBI SP rotation procedure (federated + client-secret fallback paths) and the Q3 cell of the Year-1 schedule says "Re-issue the PowerBI SP federated credential (§2.7) or rotate `POWERBI-SP-CLIENT-SECRET` if the fallback path is in use." That is correct as written, but a casual reader scanning the table for "PowerBI" sees the entry only at Q3 and may miss that the fallback client secret has an explicit annual rotation cadence in §2.7 ("Annual, or immediately on incident"). The Q4 row currently rotates only `SQL-ADMIN-PASSWORD-EXPORT`, leaving the PowerBI fallback's "annual" cadence implicit.

**Why it matters**: PowerBI SP `tcp-powerbi-sp` carries `Dataset.ReadWrite.All`, `Report.ReadWrite.All`, `Workspace.ReadWrite.All` and is registered in `dim_UserRoles` with `scope='admin'`. A stale, undocumented "did we rotate it?" loop at the end of Year-1 is exactly the gap that produces a forgotten credential in Year-2. The threat model's "Medium" sensitivity rating on the PowerBI SP credential (implicit in §2.7's Impact row) deserves the same Q3 + Q4 visibility the other Medium-rated secrets get.

**Suggested fix**: append a row to the Q3 cell or add a Q4 line item: "Verify the PowerBI SP rotation completed (federated trust still valid, or client secret rotated under §2.7)." One sentence in the schedule table; no code change.

---

### sec10-MN-02 — `bootstrap_window.md` is linked from `setup.md` AND `troubleshooting.md` AND `glossary.md`; the cumulative discoverability slightly increases adversary timing knowledge

**Location**: `docs/setup.md:217-218`, `docs/troubleshooting.md:261-304`, `docs/glossary.md` (entry referencing RR-08), `docs/security/bootstrap_window.md` itself.

**Summary**: Etapa 9 made the bootstrap window prominent in the operator-facing surface — `setup.md` line 217 carries a "READ BEFORE RUNNING `azd up`" banner, `troubleshooting.md` §8 is a dedicated diagnostic flow, and `glossary.md` lists RR-08. The window's existence, expected duration (3–8 minutes), and the precise post-provision step that closes it (Step 3, `Set-AzSqlServerActiveDirectoryOnlyAuthentication -Enable $true`) are now public-repo knowledge. The threat model's RR-08 acceptance assumed the FQDN was "not publicly announced"; the FQDN literal `sql-tcp-prod-weu.database.windows.net` appears 67 times across 13 doc files. An attacker reading the repo before/during a known deploy event has the timing window, the deploy step that closes it, and the FQDN format all in one place.

**Why it matters**: this does NOT change RR-08's residual-risk classification — the attack still requires the adversary to (a) discover the deploy moment and (b) brute-force a 120-bit-entropy GUID password in 3–8 minutes (computationally infeasible). The threat model's analysis still holds. The marginal increase in discoverability is from "Azure URL is enumerable across the entire azure.com namespace" (the baseline) to "Azure URL + deploy moment + window duration is enumerable in a public repo". The marginal delta is small but non-zero.

**Suggested fix**: two low-cost improvements.

1. Edit `docs/security/bootstrap_window.md` §"Recommended operator behaviour" to recommend deploying outside of low-cardinality timezones — e.g., "Avoid running `azd provision` during a publicly announced deploy window (e.g., a thesis-defence demo where the audience knows the schedule). Schedule the production deploy at a non-obvious time." A two-line addition.
2. Verify that the thesis itself (Etapa 13 forthcoming) does NOT publish the exact `sql-tcp-prod-weu.database.windows.net` FQDN in any defence slide or written body. The placeholder convention used for author/advisor names in the thesis should be extended to "production resource names" — anyone reading the thesis post-defence should not get a free targeting list.

Neither change is required for the `v1.0-mvp` tag; both are recommended for Etapa 12 hygiene. RR-08's risk acceptance still stands.

---

## Strengths (5)

### sec10-ST-01 — RR-09 closure is genuine, not a doc edit

Verified end-to-end across all three legs:

1. **CI gate** — `.github/workflows/ci.yml:114-119` runs `python scripts/compute_migration_checksum.py --ci`, which discovers `db/migrations/V*.sql`, canonicalises each file (NFKC + UTF-8-sig + CRLF→LF + lone-`\r`→`\n` + trailing-whitespace strip + final-newline strip), and asserts the SHA-256 is a 64-char lowercase hex (`scripts/compute_migration_checksum.py:166-176`). The script returns exit 2 on any non-hex value, which fails the CI job.
2. **Postprovision substitution** — both `infra/scripts/postprovision.ps1:74-115` and `infra/scripts/postprovision.sh:75-117` invoke the shared `scripts/render_migration.py` helper. The helper applies the *same* canonicaliser as the CI checksum compute (verified in `scripts/render_migration.py:30-36`), so the bytes piped to `sqlcmd` are byte-identical on Windows and Linux. This closes arch-MA-04 from the Etapa-8 cloud-architect review.
3. **CD smoke** — `.github/workflows/cd.yml:194-209` queries `dbo.schema_history` after deploy and asserts (a) at least one row exists (closes arch-CR-03 from Etapa 8 — empty result trivially passed the placeholder grep), and (b) no row contains `__V[0-9]+_CHECKSUM__`, `TODO-checksum-set-by-CI`, or `sentinel-no-checksum-supplied`. The grep is `-Eq` (ERE) so the alternation actually fires.

The original sentinel `'TODO-checksum-set-by-CI'` is now only present as a *deny-pattern* in `cd.yml`, not as a placeholder in any migration. The MERGE statement at `V001__init.sql:1303` uses `N'__V001_CHECKSUM__'`, which the substitution path replaces and the smoke detects. RR-09 is closed.

### sec10-ST-02 — PII redaction enforcement is exhaustive and CI-gated

`tests/integration/test_telemetry_no_pii.py` enforces the redaction contract on **eight** request paths, all running in the default `python-unit` CI job (`ci.yml:71-83`):

- Happy path: 200, with positive assertion that `tcp.ask.audit` is emitted exactly once and carries the canary's SHA-256 (`test_telemetry_redacts_pii_on_success_path`, lines 247-290).
- Refusal: 422, with the canary embedded in the model's `refusal_reason` to simulate Anthropic real-world echo behaviour — confirms the `obs-MI-06` fix at `function_app/triggers/ask.py:687-694` hashes the refusal reason before logging.
- SQL validation failure: 422 — confirms `safe_query` rejection logs only `error_class` + `sql_prefix[:120]`, never the user's question.
- Unknown principal: 404.
- SQL execution failure: 500 — confirms `pyodbc.Error` logs `error_class` only.
- Bad JSON body: 400.
- Question too long: 400 — confirms `log.warning("tcp.func.ask.question_too_long", question_length=len(question_text))` logs only the length, never the prefix.
- Forwarded-secret mismatch: 403 — confirms even the principal blob is not echoed when the gate slams shut first.

The capture mechanism is triple-channel (`structlog.testing.capture_logs` + stdlib `caplog` + `redirect_stdout/stderr`), and the test asserts at least one channel emitted to catch a silently-stripped-logger regression (`_assert_no_pii` lines 165-195). This converts "no PII in telemetry" from a documentation aspiration into an enforced contract that blocks merge on regression.

### sec10-ST-03 — `safe_query.py` three independent gates are intact and complete

Walked through the validator end-to-end against the production E9 surface:

1. **Token deny-list** (`tcp/safe_query.py:220-277`) — 27 patterns including SQL comments (`--`, `/*`, `*/`), session-context overwrite (`sp_set_session_context`, `SESSION_CONTEXT`), shell escapes (`xp_cmdshell`, `xp_*` prefix, `sp_OACreate`, `sp_OAMethod`), external-data sources (`OPENROWSET`, `OPENDATASOURCE`, `OPENQUERY`, `OPENXML`), DDL/DML (`INSERT`, `UPDATE`, `DELETE`, `MERGE`, `DROP`, `CREATE`, `ALTER`, `TRUNCATE`, `GRANT`, `REVOKE`, `DENY`), server-level ops (`BACKUP`, `RESTORE`, `BULK`, `WAITFOR`, `SHUTDOWN`, `KILL`, `DBCC`), set ops (`UNION`), table-copy (`INTO`), and generic execute (`EXEC`, `EXECUTE`). Each pattern uses `\b` word-boundary anchors and case-insensitive matching. The deny-list scan runs on a NFKC-normalised, literal-masked copy (`_normalise_for_denylist_scan`, lines 458-487), which closes the Unicode-confusable comment-smuggling bypass (ai MA-01) and the `'…INTO…'` literal false-positive (ai MA-03). Format-control characters (Cf/Cc) are rejected outright at line 481-486, preventing zero-width-joiner-anchor-desync attacks.
2. **AST parse + walk** (`tcp/safe_query.py:509-710`) — `sqlglot.parse(sql, dialect="tsql")` returns a list; the multiple-statement check at line 529 rejects `SELECT 1; SELECT 2`. The walker rejects every non-SELECT top-level statement (`Insert`, `Update`, `Delete`, `Merge`, `Create`, `Drop`, `Alter`, `Command`), every nested DML/DDL inside the SELECT, every `Union`/`Except`/`Intersect`, and any `SELECT ... INTO`. Table allowlist intersection (lines 620-655) rejects `dim_UserRoles` because it is intentionally absent from `ALLOWED_DIMS` (confirmed at lines 132-150 and tested at `test_rls_metadata_table_is_not_allowlisted` and `test_allowed_dims_excludes_user_roles`). Cross-database (`master..sys.objects`) and non-`dbo` schema references are rejected at lines 628-633. Function allowlist is split into two passes (`Anonymous` first, then typed `Func`) to close py MJ-02 from Etapa 5; the `Anonymous` pass also rejects proc names invoked inline (ai MA-02) at lines 674-679.
3. **Re-serialisation** (`tcp/safe_query.py:350`) — the sanitized SQL returned to the caller is `parsed.sql(dialect="tsql")`, i.e., the *re-serialised AST*, not the input string. An attacker cannot smuggle a payload past the AST walk by relying on a byte that the walker ignored — the executed SQL is the walker's re-emission, not the input.

The row-cap enforcement (`_enforce_row_limit`, lines 713-751) injects `TOP MAX_ROW_LIMIT` (1000) when absent and rejects any input requesting more. CTE bodies are recursed into (lines 571-585) so a `WITH cte AS (SELECT … no limit) SELECT TOP 10 *` cannot materialise an unbounded inner result into tempdb (ai MA-04).

I found no relaxation, no allowlist drift, no deny-list typo introduced in E5..E9. The validator is at the same strength documented in `docs/design/03_architecture.md` §6.4.

### sec10-ST-04 — Alert action-group fail-open is intentional and correctly implemented

`infra/modules/alerts.bicep:72-87` provisions the action group with `if (!empty(notificationEmails))`. When `notificationEmails` is empty (the default for the academic-phase posture), the action group resource is not created, and every alert rule omits the `actions` property entirely via `union(baseProps, empty ? {} : {actions: …})` at lines 135, 171, 207, 249, 285, 322, 361, 400. This was the fix for arch-CR-01 + arch-CR-02 in Etapa 8 (ARM rejects an empty `actions: []` array).

The DoS angle in the review brief ("alerts fire silently forever") was considered: when `notificationEmails` is empty, alerts still evaluate and are visible in the Azure portal at `Monitor → Alerts`. They do not send notifications. The behaviour is **documented in three places**: `infra/main.bicep:82`, `infra/modules/alerts.bicep:49`, and `docs/observability/slo.md:141-145`. The operator opens the dashboard manually as the canonical academic-phase posture. The threat model classifies this as ACCEPTED RESIDUAL behaviour for a single-operator system; a future production deployment with a real on-call rotation would set `NOTIFICATION_EMAILS` via `azd env set` and re-deploy.

This is not a new attack vector. The 8 alert rules are an *improvement* over the E6 surface — previously, no rules existed. The "silent in the portal" mode is preferable to "no rules at all". A genuine DoS would require an adversary to know the operator does not check the portal and to time their attack between portal checks; this is a marginal concern relative to the much-larger structural protections (rate limit, AAD-only, RLS).

### sec10-ST-05 — `tcp.ask.audit` SHA-256 hashing is the right cryptographic choice for the audit-trail use case

The review brief asked whether the SHA-256 question fingerprint at `function_app/triggers/ask.py:647-649` gives attackers "a stable rainbow-table-able identifier". I considered this carefully:

The fingerprint is computed as `sha256(question_text.strip())`. There is no salt. SHA-256 is preimage-resistant, but rainbow tables against common questions ("How many traders are active today?") would trivially recover the plaintext.

**Why this is the correct design choice for this system**, despite the absence of a salt:

1. The threat model the audit hook serves is **operator-side correlation** (Kusto query 07, workbook tile 9), not **adversary-side anonymity**. The operator is the only party with read access to `traces` in App Insights — the workspace is RBAC-scoped via the `Log Analytics Reader` role, granted only to the developer and (post-thesis) the advisor.
2. The audit query (`infra/observability/kusto/07_ask_question_audit.kql`) intentionally excludes `oid_suffix` from the same row as `q_hash` (commented at lines 11-15 of the file) so that even an operator with full workspace read access cannot correlate a question hash to a specific user. The 32-employee population is small enough that a join of `q_hash` × `oid_suffix` would shrink the anonymity set to single users.
3. Adding a salt would defeat the **only** purpose of the hash: detecting question *repetition*. A per-request salt would give every question a unique fingerprint, breaking the `summarize occurrences = count() by q_hash` pattern at line 25 of the .kql.
4. A keyed HMAC with a per-deployment key would preserve repetition detection while frustrating offline rainbow attacks, but the threat model classifies the workspace as a trusted side (the operator owns it). An adversary capable of reading App Insights `traces` already has the operator's identity; rainbow-table recovery of the question texts adds nothing beyond what the operator could observe via the SWA chat UI directly.

The choice is correct. The risk surface is the workspace's RBAC, not the hash's cryptographic strength. Tracked as a Strength rather than a Finding because the design is defensible against the actual adversary model in the threat model.

---

## Re-check of the 11 STRIDE surfaces

For each surface in `threat_model.md` §3, I re-ran the cells against the post-E9 surface and noted whether E7/E8/E9 introduced new attack vectors.

| Surface | E7/E8/E9 delta | Posture change |
|---|---|---|
| 1. `POST /api/ask` | E8 added `tcp.ask.audit` event; PII redaction test extended to 8 paths | **Improved.** The audit hook is now both emitted and CI-tested; the redaction posture is enforced, not aspirational. |
| 2. `GET /api/ping` | None | Unchanged. MN-01 from threat model (return `SELECT 1` instead of `@@VERSION`) is still residual. |
| 3. `TimerTrigger_BacpacExport` | E8 added `tcp-alert-bacpac-missed` (8-day look-back) | **Improved.** A missed weekly export now alerts within 24h instead of relying on the operator to notice. |
| 4. `TimerTrigger_DailyGenerator` | E8 added `tcp-alert-daily-generator-failed` (1-hour evaluation) | **Improved.** A generator failure now alerts at the next evaluation cycle. |
| 5. `WarmupTrigger` | None | Unchanged. |
| 6. Raw Function App URL | None | Unchanged. The shared-secret check is still at line 1 of the handler. |
| 7. SQL Server public endpoint | E7 added `tcp-powerbi-sp` to `dim_UserRoles` with `scope='admin'`; E8 added `tcp-alert-sql-cpu-high` + `tcp-alert-sql-quota-burn` | **Improved.** A new admin identity exists but is restricted to PowerBI tenant + `tcp_bi_reader` role (SELECT-only on `v_*` views); rotation procedure in `credentials_rotation.md` §2.7 is complete. CPU + quota alerts catch sustained abuse. |
| 8. Key Vault public endpoint | E7 added optional `POWERBI-SP-CLIENT-SECRET` secret if the fallback path is used | **Neutral.** New secret is correctly placed in KV with the same MI-RBAC model; rotation documented in `credentials_rotation.md` §2.7. |
| 9. Storage Account public endpoint | None | Unchanged. RR-01 (`allowSharedKeyAccess: true`) still residual. |
| 10. GitHub Actions runners | E8 added `iac-validate` job's `psrule-for-azure` + `checkov` (no new third-party action SHAs beyond `astral-sh/setup-uv` and `gitleaks/gitleaks-action`, both still pinned) | **Improved.** Two additional static-analysis gates (PSRule + Checkov) on the IaC; no new permissions write requirement; `azure/setup-bicep@v1` is the only un-SHA-pinned action and is from a verified Microsoft publisher. |
| 11. Developer workstation | None | Unchanged. |

No STRIDE cell regressed from "STRONG" to "ADEQUATE" or below. The two surfaces with the most E8 activity (surface 1 and surface 3) both moved upward.

---

## OWASP Top 10 re-mapping

| Item | E6 verdict | E10 verdict | Delta justification |
|---|---|---|---|
| A01 Broken Access Control | STRONG | **STRONG** | RLS BLOCK predicate unchanged; `dim_UserRoles` still excluded from `safe_query` allowlist; `tcp_ai_assistant` SELECT-only; `tcp_bi_reader` SELECT-only on `v_*` views. |
| A02 Cryptographic Failures | STRONG | **STRONG** | TLS 1.2 floor unchanged; KV+MI unchanged; SHA-256 audit hashing (no salt) is correctly scoped to the audit use-case per sec10-ST-05. |
| A03 Injection | STRONG | **STRONG** | `safe_query` three independent gates intact (sec10-ST-03); no relaxation in E5..E9. |
| A04 Insecure Design | STRONG | **STRONG** | Rate limit, response envelope, generic error messages unchanged. |
| A05 Security Misconfiguration | ADEQUATE | **ADEQUATE** | HSTS + X-Frame-Options (MJ-02) still residual; no regression. |
| A06 Vulnerable and Outdated Components | ADEQUATE | **ADEQUATE** | `pip-audit --strict` + `bandit` + Dependabot SHA-pin renewal on third-party actions; no new dependency added by E7/E8/E9 (PowerBI deploy uses `az rest` + native PS modules, not a new Python package). |
| A07 Identification and Authentication Failures | STRONG | **STRONG** | AAD-only post-bootstrap; OIDC federation across CI/CD; PowerBI SP federated credential preferred over client secret. |
| A08 Software and Data Integrity | STRONG | **STRONG** | RR-09 closure verified (sec10-ST-01); `WEBSITE_RUN_FROM_PACKAGE=1`; new IaC gates (Checkov, PSRule) raise the integrity bar; new RR-09 CD smoke assertion catches sentinel placeholders. |
| A09 Logging and Monitoring Failures | STRONG | **STRONG** | 8 alert rules + PII test 8-path coverage + audit event CI-enforced; oid_suffix discipline unchanged. |
| A10 SSRF | STRONG | **STRONG** | No user-controlled URL paths introduced by E7/E8/E9; PowerBI deploy hits hardcoded `api.powerbi.com` endpoints; alerts use hardcoded `scopes:` blocks. |

No item moved up or down a band. The verdicts are stable from Etapa 6 with two improvements within bands (A08 and A09 both got internally stronger).

---

## Credentials rotation re-validation

Confirmed presence in `credentials_rotation.md` §2 of all seven secrets:

1. `ANTHROPIC-API-KEY` (§2.1)
2. `SQL-ADMIN-PASSWORD-EXPORT` (§2.2)
3. `SWA-FORWARDED-SECRET` (§2.3)
4. `STORAGE-CONNECTION-STRING` (§2.4)
5. OIDC federated credentials (§2.5; no static secret to rotate)
6. `SQL-ADMIN-PASSWORD-BOOTSTRAP` (§2.6; single-use, deleted post-flip)
7. `POWERBI-SP-CLIENT-SECRET` (§2.7; only present if the fallback path is used; federated credential preferred)

E8 did not introduce a new secret. The `NOTIFICATION_EMAILS` parameter is an azd env var (an array of email addresses), not a secret — it is not rotatable in the credential sense and is correctly NOT listed in §2. Email addresses are not credentials.

The Q1..Q4 schedule covers `ANTHROPIC-API-KEY`, `SWA-FORWARDED-SECRET`, `STORAGE-CONNECTION-STRING`, `POWERBI-SP-CLIENT-SECRET` (under §2.7's "fallback path" caveat), and `SQL-ADMIN-PASSWORD-EXPORT`. See sec10-MN-01 above for the one observation on the PowerBI fallback's visibility in the schedule.

---

## CI/CD supply chain re-check

`ci.yml` third-party action SHAs (all verified pinned in this audit):

- `astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b` (v8.1.0) — 4 occurrences, all pinned.
- `gitleaks/gitleaks-action@ff98106e4c7b2bc287b24eaf42907196329070c7` (v2.3.9) — pinned.

Microsoft-published verified-publisher actions (intentionally not SHA-pinned per project policy):

- `actions/checkout@v4`
- `actions/setup-python@v5`
- `actions/upload-artifact@v4`
- `actions/github-script@v7`
- `azure/setup-bicep@v1`
- `azure/login@v2`
- `Azure/setup-azd@v1.0.0`

`cd.yml` smoke job (E8 additions): the new steps `set up sqlcmd`, `wait for function app`, `ping function app`, `smoke test database`, `assert checksum integrity`, `emit summary` — none introduce a third-party Action. They are all inline shell scripts plus the pinned-by-OS-version `mssql-tools18` apt install. The new steps DO require `id-token: write` (already present at `cd.yml:130`), but the existing `provision` and `deploy` jobs already had it — no permissions escalation introduced by E8.

The two `permissions: contents: read` blocks on `secret-scan` and `iac-validate` jobs in `ci.yml` (lines 124, 162) are minimum-privilege; no `write` permissions on these jobs. The `iac-whatif` job has `pull-requests: write` (line 222) to comment back on PRs, which is the documented intentional minimum for that flow.

No supply-chain regression. All E8 additions stay within the established pin/permission discipline.

---

## RR-09 closure verification (deeper dive)

Verified the closure across all four artefacts (the brief asked for three legs; there are actually four):

1. **Migration file** — `db/migrations/V001__init.sql:1304` and `V002__synth_logic.sql:283` carry the literal placeholder `N'__V001_CHECKSUM__'` and `N'__V002_CHECKSUM__'` respectively, used in `MERGE … WITH (HOLDLOCK)` upserts. The `WITH (HOLDLOCK)` closes the IF NOT EXISTS / INSERT / ELSE UPDATE race (code-MA-03 from Etapa 8).
2. **CI gate** — `scripts/compute_migration_checksum.py --ci` runs in `ci.yml:114-119`, asserts each migration canonicalises to a 64-char lowercase hex SHA-256, returns exit 2 on any non-hex value. The script handles missing files cleanly (`code-MA-01` from Etapa 8 fix at lines 152-156).
3. **Postprovision substitution** — both PS1 and SH variants invoke `scripts/render_migration.py` (lines 107-115 PS1, 107-117 SH), which applies the same canonicaliser as the checksum compute. The substitution writes to stdout and is piped to `sqlcmd -b`. The `code-MA-04` fix at PS1 line 108-110 captures `$LASTEXITCODE` *before* the pipe so a Python crash producing an empty stream cannot silently succeed in `sqlcmd -b`. The SH variant has the equivalent fix at line 107-115 (`if ! rendered=$(python3 …)` + `if [[ -z "$rendered" ]]`).
4. **CD smoke** — `cd.yml:194-209` queries `dbo.schema_history`, asserts ≥ 1 row exists (closes `arch-CR-03` from Etapa 8 — empty results no longer trivially pass the placeholder grep), and uses `grep -Eq "__V[0-9]+_CHECKSUM__|TODO-checksum-set-by-CI|sentinel-no-checksum-supplied"` to fail on any unsubstituted placeholder. The `grep -Eq` (ERE) is the fix for the BRE pipe-literal bug; the deny-pattern now actually triggers.

The setup.md walkthrough (line 92) explicitly notes that local dev applies leave the placeholder intact — this is harmless because the CD smoke job assertion runs only against production `schema_history`, never against local Docker.

RR-09 is fully closed. Threat model's table entry at §7 RR-09 (the strikethrough + "CLOSED in Etapa 8" annotation) is accurate.

---

## What I did NOT do (out-of-scope)

- I did not re-litigate RR-01 (`allowSharedKeyAccess: true`), RR-02 (KV `defaultAction: 'Allow'`), RR-03 (`enablePurgeProtection: false`), RR-04 (`AllowAllAzureServices` firewall), RR-05 (single-instance rate limit), RR-06 (App Insights custom metrics deferred), RR-07 (ATP absent), or RR-08 (bootstrap window). None of E7/E8/E9 made the residual posture of any of these worse. RR-08's marginal doc-discoverability increase is captured in sec10-MN-02 as a recommended hygiene item, not as a residual-posture downgrade.
- I did not perform live runtime auditing — no Azure subscription is available for this review. The audit is based entirely on the on-disk artefacts (code, IaC, docs, CI/CD workflows, tests). The `obs-MI-01` ACCEPTED RESIDUAL from Etapa 8 (`customDimensions["Category"] == "Host.Startup"` may not match Python v2 runtime) requires live-deploy verification and remains open by design.
- I did not attempt to break `safe_query` with novel adversarial payloads beyond what is already in the 23-prompt fixture at `tests/unit/test_safe_query.py:155-221`. The fixture coverage matches the deny-list breadth; a future hardening pass should bump it to ≥ 40 prompts including the recently-added EXCEPT/INTERSECT AST-walk rejections.

---

## Recommendation

**APPROVED FOR `v1.0-mvp` TAG.**

Address sec10-MN-01 (one sentence in the rotation schedule table) and sec10-MN-02 (two-line addition to `bootstrap_window.md` + thesis posture note) in Etapa 12. Neither blocks the tag; both are doc-hygiene improvements.

The security posture matches a thesis-grade defence narrative:

- Zero credentials in code or git history (gitleaks-enforced).
- All data-plane access via MI + AAD-only auth.
- Three independent layers between an LLM-emitted query and the database (token deny-list, AST walk, re-serialisation), plus RLS BLOCK predicate at the DB engine.
- Three independent layers between a forged request and `/api/ask` (SWA AAD gate, SWA forwarded-secret check, AAD principal claim parse).
- 8 alert rules with academic-phase fail-open behaviour documented and intentional.
- PII redaction enforced by 8-path CI test.
- RR-09 integrity chain closed end-to-end through 4 artefacts.

This is a strong v1.0 posture for an academic project deployed on $0/month Azure free tiers. The thesis defence narrative ("STRIDE × 11 surfaces, 9 RRs accepted, 1 closed, OWASP A01..A10 all STRONG/ADEQUATE") holds up to scrutiny.

---

## Change history

| Version | Date | Author | Notes |
|---|---|---|---|
| 1.0 | 2026-05-16 | TODO | Initial Etapa 10 cross-cutting security re-validation. |
