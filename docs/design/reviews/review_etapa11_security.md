# Review — Etapa 11: Light-Touch Security Re-Validation (Post-`v1.0-mvp` Maintenance Pass)

| Field | Value |
|---|---|
| **Date** | 2026-05-16 |
| **Reviewer role** | security-auditor (post-`v1.0-mvp` maintenance check) |
| **Scope** | The 6 Etapa-11 changes itemised in the task brief. No live runtime auditing (no Azure subscription). Etapa-10 Minors out of scope. |
| **Verdict** | **APPROVED** with 3 Minor follow-ups |

---

## Verdict summary

Etapa 11 is a genuine light-touch maintenance pass: no new runtime dependencies, no new credentials, no new public surfaces, no change to the canonical security contracts (ADR-003 SESSION_CONTEXT, ADR-005 admin-bypass scope lookup, `safe_query` deny-list, PII redaction discipline). The two structural additions — the `.pre-commit-config.yaml` baseline and the `bypass` conditional in Bicep — are both **net-positive** for the security posture and neither regresses any previously-cleared control.

The `swa-config-placeholder-guard` hook correctly closes the `arch10-MJ-03` developer-workstation footgun for the canonical case (developer runs `azd up`, file is substituted, attempts `git commit -a`). Three Minor observations are called out below: a small fail-open class on the guard, tag-pinning rather than SHA-pinning of third-party pre-commit hooks, and the `attr-defined` silencing in `tests/*` not undermining the PII contract but slightly weakening monkeypatch-target verification.

Zero Critical findings. Zero Major findings. Three Minor findings. Five Strengths called out.

---

## Critical (0)

None.

---

## Major (0)

None.

---

## Minor (3)

### sec11-MN-01 — `swa-config-placeholder-guard` has three narrow fail-open paths

**Location**: `.pre-commit-config.yaml:82-92`.

**Summary**: the inline-Python guard reads the working tree of `swa/staticwebapp.config.json` and exits 0 (allow commit) iff `'<value-set-by-postprovision>'` is present in the file OR the file content is the empty string. Three fail-open paths exist within that logic:

1. **File missing** (`os.path.exists(path)` is false) → `content = ''` → exits 0. If a developer deletes the file (e.g., `git rm swa/staticwebapp.config.json` in a refactor branch), the guard passes silently. The `files: ^swa/staticwebapp\.config\.json$` matcher still fires on a staged deletion because `pass_filenames: false` runs the hook whenever any matching file is staged, regardless of whether the working-tree file exists.
2. **Zero-byte file** (e.g., truncated by a bad merge tool, or a developer who `printf '' > swa/staticwebapp.config.json` to reset it) → `content == ''` → exits 0. The truncation case is benign (no secret material is present), but it would also let a separate refactor that *replaces* the SWA config with an empty stub slip past.
3. **Legitimate reshape with no placeholder** (developer rewrites the config to a different shape, e.g., a new route schema, without re-adding the placeholder) → the guard exits 1 with a misleading error message ("postprovision substitution applied") even though no substitution happened. This is **fail-closed** for security (good) but produces a misleading operator message.

Path 1 is the most concerning for the security contract because it is the canonical way a secret-leak regression could land: a developer rebases two branches, the SWA config is renamed or moved to a new path (e.g., `frontend/staticwebapp.config.json`) without updating the hook's `files:` regex, and the postprovision substitution silently lands on a path the hook does not check. The hook's tightly-coupled hardcoded path is a structural weakness here.

**Why it matters**: the close of `arch10-MJ-03` rests on a working hook. The fail-open on missing-file is a narrow but real regression class — particularly because the threat model assumes the SWA-forwarded secret is in Key Vault, not git. A second leg of defence is already provided by `gitleaks` (which scans the staged diff and would detect a 32-char high-entropy secret in a JSON file), so the residual risk is small. But the hook's intent is *fail-closed* per its comment block, and the implementation diverges from that intent.

**Suggested fix**: tighten the predicate to require the placeholder to be present, not "present OR empty". Replace `or content == ''` with explicit handling of "file deliberately removed" (skip) versus "file present but empty" (fail). A roughly equivalent one-liner:

```python
sys.exit(0) if '<value-set-by-postprovision>' in content else \
    (print('ERROR: …', file=sys.stderr) or sys.exit(1))
```

Additionally, move the hook from an inline-Python `entry:` to a dedicated `scripts/check_swa_placeholder.py` so the code is grep-able, testable, and has its own pytest coverage. Inline-Python entries are awkward to maintain and produce poor diffs when the predicate evolves.

---

### sec11-MN-02 — Third-party `.pre-commit-config.yaml` hooks are tag-pinned, not SHA-pinned

**Location**: `.pre-commit-config.yaml:31, 41, 52, 70` (the `rev:` lines).

**Summary**: the four third-party hook repositories (`astral-sh/ruff-pre-commit @ v0.6.9`, `sqlfluff/sqlfluff @ 3.4.2`, `pre-commit/pre-commit-hooks @ v5.0.0`, `gitleaks/gitleaks @ v8.21.2`) are pinned to git tags, not commit SHAs. Tags are mutable — a maintainer (or a compromised maintainer account) could re-tag a different commit under the same label, and the next `pre-commit install` or `pre-commit autoupdate` on a developer workstation would silently pull the new commit. The CI workflow policy (per Etapa-10 review §"CI/CD supply chain re-check") SHA-pins third-party GitHub Actions specifically to defeat this class. The pre-commit baseline does not extend that discipline.

**Why it matters**: the threat model classifies the developer workstation as a trusted side, but the workstation is *also* the side that holds long-lived Azure CLI tokens and Anthropic API keys (per `docs/security/credentials_rotation.md` §1.1). A compromised pre-commit hook can exfiltrate either of those on the next commit. The supply-chain blast radius is similar to a compromised GitHub Action, but the pin discipline diverges.

The actual risk in practice is mitigated by two facts:

1. `pre-commit` resolves the tag once on first install and caches the resolved commit in `~/.cache/pre-commit/` — subsequent runs use the cached revision. The mutable-tag attack window is the moment `pre-commit install` (or `autoupdate`) runs, not every commit.
2. All four upstreams (`astral-sh`, `sqlfluff`, `pre-commit`, `gitleaks`) are well-known organisations with public release histories. A re-tag would be visible in `git log` on the upstream repo within minutes.

The risk is real but low. The fix is purely hygiene.

**Suggested fix**: convert each `rev: v0.6.9`-style line to the resolved commit SHA, with a comment indicating the tag for human readability. Example:

```yaml
- repo: https://github.com/astral-sh/ruff-pre-commit
  rev: 04eb2eedd84c2c7c2e6cd80d3aa3525c8c79e2a5  # v0.6.9
  hooks:
    - id: ruff-format
    - id: ruff
```

Apply uniformly to all four third-party repos. The local repo (`swa-config-placeholder-guard`) is unaffected.

A complementary defence: enable Dependabot for `package-ecosystem: "pre-commit"` so that tag bumps land via PR with a CI run, matching the established discipline for third-party Actions.

---

### sec11-MN-03 — `tests.*` `attr-defined` silencing weakens monkeypatch-target verification (but does not undermine the PII contract)

**Location**: `pyproject.toml:146-153` (the `[[tool.mypy.overrides]] module = ["tests.*"]` block).

**Summary**: the relaxation disables five mypy error codes on the test suite: `no-untyped-def`, `unused-ignore`, `attr-defined`, `operator`, `no-any-return`. The brief specifically asked whether silencing `attr-defined` undermines `tests/integration/test_telemetry_no_pii.py`. The answer is: **no**, the PII contract holds, but `attr-defined` silencing does weaken a secondary check.

The PII test's core assertions (lines 165-195) check the rendered string content of captured telemetry — `_TEST_QUESTION not in flattened`, `_TEST_OID_FULL_STR not in flattened`, etc. These are *runtime substring searches on stringified data*. mypy never had visibility into these checks at static-analysis time; `attr-defined` silencing changes nothing here. The positive assertion that `_TEST_OID_SUFFIX in flattened` (line 276) and the `tcp.ask.audit` event check (lines 285-290) are likewise runtime-only.

However, the test uses heavy monkeypatching: `monkeypatch.setattr(ask_module, "_resolve_scope", …)`, `ask_module._RATE_LIMIT_BUCKETS.clear()`, `monkeypatch.setattr(ask_module, "ask_claude", …)`. Under strict mypy with `attr-defined` enabled, a refactor of `function_app/triggers/ask.py` that renames `_resolve_scope` to `_resolve_user_scope` would produce an `attr-defined` error on the test's `setattr` line — alerting the developer. With `attr-defined` silenced, the rename produces a runtime no-op: `monkeypatch.setattr(ask_module, "_resolve_scope", …)` adds a new attribute to the module instead of replacing the function, the test's stub is never reached, the real `_resolve_scope` runs and (likely) raises because the test's SQL fixtures are absent — but if the real function happens to short-circuit on the test's principal header, the test might *pass* without exercising the intended code path. The PII assertion would still hold (no leak), but the *coverage signal* would be silently degraded.

**Why it matters**: the PII contract itself is still enforced — the test still fails if a real leak happens. The risk is "silent test rot": a refactor breaks the test's intended path coverage without breaking the test's pass/fail signal. The threat model's `obs-MI-06` close depends on the test exercising all eight paths; a silently-skipped path would mean the close is illusory.

The risk is low in practice because:

1. The test's monkeypatch targets are public-ish names (`ask_claude`, `_resolve_scope`, `_execute_validated_sql`, `_RATE_LIMIT_BUCKETS`). A rename would be a deliberate refactor, not an accidental drift, and would surface in code review.
2. The test's positive assertions (audit event count, `oid_suffix` presence, status code) act as canaries — a silently-skipped path would likely fail one of those.
3. `attr-defined` is silenced for `tests.*` only; the production code in `function_app/triggers/ask.py` is still under `strict = true`, so a refactor that breaks the monkeypatch target would also need to internally pass mypy on the production side.

**Suggested fix**: two low-cost options, pick one:

1. Add an explicit `from function_app.triggers.ask import _resolve_scope, _execute_validated_sql, ask_claude, _RATE_LIMIT_BUCKETS  # noqa: F401` at the top of the test file. This makes the monkeypatch targets visible to a static checker even with `attr-defined` silenced — a refactor that removes any of the four would now produce an `ImportError` at collection time.
2. Reduce the silenced set: keep `no-untyped-def`, `unused-ignore`, `operator`, `no-any-return` silenced (the brief's rationale for them is sound), but re-enable `attr-defined` on `tests/integration/*` only by adding a more specific override that overrides the broader one.

Either fix preserves the readability trade-off the comment defends while restoring the static-time coverage signal for the highest-value test in the suite.

---

## Strengths (5)

### sec11-ST-01 — `bypass` conditional is behaviourally identical AND makes the future Deny-flip a one-parameter change

`infra/modules/keyvault.bicep:130-131` and `infra/modules/storage.bicep:73-74` derive `bypass` via `kvDefaultAction == 'Deny' ? 'AzureServices' : 'None'` (and the storage equivalent). Verified the ARM contract:

- When `defaultAction = 'Allow'`, Azure ignores `bypass` entirely — the property's only role is to whitelist Microsoft services so they bypass the *Deny* default. With `Allow` as the baseline, every caller is already allowed; `bypass` is inert. The previous hard-coded `'AzureServices'` and the new conditional-emitted `'None'` produce **byte-identical runtime behaviour** in Azure today.
- When `defaultAction = 'Deny'`, the conditional emits `'AzureServices'`, matching the previous static value. A future operator who flips `kvDefaultAction` / `storageDefaultAction` to `'Deny'` (e.g., once a stable runner egress IP is available — see `keyvault.bicep:81-86` comment) gets the correct behaviour with a single parameter change. No code edit needed.

A future-proofing risk to acknowledge: if Microsoft ever changed the ARM contract to make `bypass: 'None'` *meaningful* when `defaultAction = 'Allow'` (e.g., to mean "block Azure services even though everything else is allowed"), the conditional would suddenly start blocking trusted Microsoft services. This would be a breaking change to the ARM contract that Microsoft would announce in the deprecations channel; the probability over the thesis-cycle horizon is effectively zero. The comment at `keyvault.bicep:123-131` documents the inert semantics correctly.

RR-02 in `docs/security/threat_model.md` remains ACCEPTED RESIDUAL with the same justification. The cosmetic close of `arch10-MJ-04` is genuine.

### sec11-ST-02 — `swa-config-placeholder-guard` correctly closes the canonical `arch10-MJ-03` footgun

For the canonical case the hook is designed for — a developer runs `azd up` locally, the postprovision script substitutes `<value-set-by-postprovision>` with the live SWA forwarded secret in the working tree, the developer then attempts `git commit -a` — the hook **fails-closed correctly**:

- The working-tree file no longer contains the placeholder.
- `content != ''` and `'<value-set-by-postprovision>' not in content` → the `else` branch fires.
- `sys.exit(1)` aborts the commit with a clear operator message including the `git restore` recovery hint.

This is the threat the hook exists to catch, and the implementation catches it. Combined with `gitleaks` (which would independently flag a 32+ char high-entropy secret in a JSON file), there are two independent gates against committing the resolved SWA secret. The `sec11-MN-01` fail-open paths are narrow edge cases, not the canonical attack surface.

### sec11-ST-03 — Etapa-11 introduces zero new credentials, zero new public endpoints, zero new third-party runtime dependencies

The `pyproject.toml` audit removed a never-existed entry (`types-pyodbc`) and tightened mypy on tests. No package was added or upgraded in the runtime dependency list. The Bicep change is a pure refactor of an existing parameter. The pre-commit baseline does not ship with the deployed Function App. The `tests/unit/test_function_app_imports.py` is a smoke test against a controlled circular-import contract, not a new code path. The mypy source-level fixes to `tcp/db.py`, `tcp/synth/trades.py`, `tcp/synth/runner.py`, `function_app/triggers/bacpac_export.py`, `function_app/triggers/ask.py` are typing-only changes verified to preserve the runtime contracts of ADR-003 SESSION_CONTEXT, ADR-005 admin-bypass scope lookup, `safe_query` deny-list, and PII redaction.

**Credentials rotation impact**: zero. No new entry needed in `docs/security/credentials_rotation.md` §2. Existing entries are unaffected.

**Incident-response impact**: zero new surface. The existing `docs/security/threat_model.md` 11 STRIDE surfaces are unchanged in count and in posture.

### sec11-ST-04 — `gitleaks v8.21.2` is a current-enough pin for the dev-workstation gate

`gitleaks v8.21.2` was released in October 2024. The 8.x line has continued through 8.24.x with no security advisories that affect the detection efficacy of the default ruleset for the secret classes this project cares about (AWS keys, Azure connection strings, Anthropic API keys, generic high-entropy strings ≥ 32 chars, JWT tokens, RSA private keys). The newer 8.x releases add detection rules for emerging providers (Cloudflare, Vercel, Modal, etc.) and tighten some allowlist defaults, but none of those changes are load-bearing for the TCP threat model.

The CI workflow uses `gitleaks/gitleaks-action@ff98106…` (v2.3.9), which itself runs a more recent gitleaks binary on the server side. The pre-commit pin is the *workstation* gate; the CI gate is the authoritative one. A workstation-only bypass that lands in CI would still get caught.

No CVE in the 8.x line affects the detection efficacy. The pin is acceptable. (A bump to 8.24.x is a Q3-2026 hygiene item, not a security blocker.)

### sec11-ST-05 — `tests/unit/test_function_app_imports.py` raises the integrity bar for the Function App boot-time contract

The new smoke test pins a previously-implicit contract: `function_app/function_app.py` must instantiate `app = func.FunctionApp(...)` BEFORE importing the trigger modules, because each trigger module imports `app` back to register its decorators. A reordering of the imports (cosmetic refactor) would break trigger registration at Function App boot, AFTER the Bicep deploy succeeded but BEFORE the first cron invocation — a silent integrity failure that the existing test suite did not catch.

The test exercises a fresh import via `sys.modules` invalidation in a fixture, then asserts (a) clean import, (b) all five expected triggers (`daily_generator`, `warmup`, `bacpac_export`, `ping`, `ask`) are registered. This is a small but high-value addition: a future refactor that drops `ask` from the registration block would now fail CI instead of producing a deployed Function App that silently lacks the `/api/ask` route.

No security surface change, but a clear integrity-availability improvement that aligns with OWASP A08 (Software and Data Integrity).

---

## Re-check of the 4 Etapa-11 change classes

| Change | Security delta | Posture |
|---|---|---|
| `pyproject.toml` (remove `types-pyodbc`, relax mypy on `tests.*`) | Neutral on contracts. Minor coverage-signal weakening (sec11-MN-03). | **Unchanged.** |
| `.pre-commit-config.yaml` (NEW) | Net-positive: adds workstation-side `gitleaks` + the `arch10-MJ-03` close. Two Minors (sec11-MN-01, sec11-MN-02). | **Improved.** |
| Bicep `bypass` conditional | Cosmetic + future-proofing. Behaviourally identical to prior ARM. | **Unchanged at deploy time, improved for future Deny-flip.** |
| `test_function_app_imports.py` (NEW) + mypy source fixes | Integrity bar raised (sec11-ST-05). No security contract touched. | **Improved.** |
| Removed empty `app/` + `data/` dirs | None. | **Unchanged.** |

No STRIDE surface regressed. No OWASP item moved bands. No threat-model residual-risk classification changed.

---

## Out of scope (per brief)

- Etapa-10 Minors (`sec10-MN-01` PowerBI SP Q4 cadence + `sec10-MN-02` bootstrap-window doc visibility) — explicitly out of scope per the brief. Both remain open and tracked for Etapa-12 polish.
- Live runtime auditing — no Azure subscription. The audit rests entirely on on-disk artefacts.
- `azd up` execution — explicitly excluded.

---

## Recommendation

**APPROVED.** The Etapa-11 maintenance pass holds the `v1.0-mvp` security posture and improves it in three observable ways (workstation pre-commit baseline, Bicep cosmetic close, boot-time integrity test). The three Minor findings are hygiene-grade and should land in the Etapa-12 polish window alongside the two Etapa-10 Minors.

No re-tag of `v1.0-mvp` is required; no urgent fixes are required; no credentials rotation is triggered by Etapa 11.

The thesis-defence narrative remains intact: zero credentials in code or git history, MI + AAD-only data-plane, three independent gates on `safe_query`, three gates on `/api/ask`, 8 alert rules, 8-path PII redaction CI test, RR-09 closed end-to-end. Etapa 11 adds: a workstation-side gitleaks gate, a placeholder-leak guard, a boot-time trigger-registration test, and a future-proofed Bicep `bypass` conditional. None of these introduce new attack surface; each closes a previously-acknowledged residual.

---

## Change history

| Version | Date | Author | Notes |
|---|---|---|---|
| 1.0 | 2026-05-16 | TODO | Initial Etapa 11 light-touch security re-validation. |
