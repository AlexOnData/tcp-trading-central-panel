# Bootstrap window — operator runbook

- **Date**: 2026-05-16
- **Audience**: deploy operator (the developer running `azd up`)
- **Owners**: project maintainer; reviewed during Etapa 6 security sweep
- **Cross-references**: `threat_model.md` §6, `credentials_rotation.md` "lost / compromised secret playbook", `incident_response.md` Scenario D, `docs/decisions/ADR-004-bacpac-export-schedule.md`, `infra/scripts/postprovision.{ps1,sh}`.

## What is the bootstrap window

The interval between the moment `azd provision` finishes creating the Azure SQL Server resource (with SQL-auth admin enabled) and the moment the post-provision script finalizes the `azureADOnlyAuthentication: true` flip. During this window:

- The SQL server is reachable on its public endpoint (`<server>.database.windows.net:1433`).
- Firewall rule `AllowAllAzureServices` (`0.0.0.0/0.0.0.0`) is in effect — every Azure tenant can reach the server.
- SQL-authentication is **enabled** alongside AAD authentication.
- The bootstrap SQL admin (`tcpadmin`) exists with a `newGuid()`-derived password persisted in Key Vault as `SQL-ADMIN-PASSWORD-BOOTSTRAP`.

This is the system's highest-residual security state, called out explicitly by the Etapa-6 security-auditor pass.

## Why the window cannot be zero

The Azure SQL Free Offer does not support deploying a database in AAD-only mode in the same `azd provision` call that creates the server. The flip is an imperative operation (`az sql server ad-only-auth enable`) that requires:

1. The server resource to be fully provisioned.
2. The Function App MI to be registered in `dim_UserRoles` with `scope='admin'` so a future AAD-only connection can reach the data.
3. Schema migrations (V001 + V002) to have applied so the `dim_UserRoles` table exists.

Steps 2 and 3 happen inside the post-provision script. The script runs synchronously after `azd provision` returns control to the operator, so the window's duration is bounded by the script's wall-clock time.

## Expected duration

Typical end-to-end duration on a clean deploy: **3 to 8 minutes**.

Breakdown:

| Phase | Typical time |
| --- | --- |
| `azd provision` finishes (SQL server created) → postprovision starts | < 5 s (handoff) |
| Step 0: apply V001 + V002 (sqlcmd, ~3 MB total SQL) | 30 s – 90 s |
| Step 1: register Function MI in `dim_UserRoles` | 5 s – 15 s |
| Step 2 / 2b: set `TCP_GENERATOR_OID` + restart Function App | 30 s – 60 s |
| Step 2c: substitute `staticwebapp.config.json` placeholders | < 5 s |
| Step 3: `az sql server ad-only-auth enable` + 10 s propagation sleep | 15 s – 30 s |
| Step 4 / 5: KV cleanup + verification | 10 s – 30 s |

If the SQL server's auto-resume happens to fire during a migration step (rare on first deploy because the DB is freshly created), add another 30 to 60 seconds.

## Mitigations in place

| Mitigation | Implementation |
| --- | --- |
| Strong bootstrap admin password | `newGuid()` in Bicep produces ~120 bits of entropy. Never appears in deployment outputs, logs, or templates. |
| Password stored only in Key Vault | KV is RBAC-only; only the OIDC SP (`Key Vault Secrets Officer`) and the Function MI (`Key Vault Secrets User`) have access during this window. |
| Operator workstation has the only interactive copy | `azd env get-values` exposes it only to the running shell; never echoed to stdout. |
| AAD admin pre-registered | Bicep declares the OIDC SP as AAD admin, so the developer can use `sqlcmd -G` immediately without SQL-auth. |
| Verification at the end of postprovision | Step 5 asserts `azureADOnlyAuthentication == true` AND the bootstrap KV secret was deleted. If either check fails the script exits non-zero. |
| Short duration | The whole window is minutes, not hours or days. |

## Recommended operator behaviour

Before running `azd provision`:

1. **Choose a quiet network** — not a coffee-shop Wi-Fi. The window is short, but the SQL admin password traverses your shell history if you accidentally `echo $SQL_ADMIN_PASSWORD`.
2. **Make sure the workstation is patched** — current OS, current `az` CLI, current `azd` CLI.
3. **Block interruptions** — do not run `azd provision` if you have to leave the laptop in the next 15 minutes. The postprovision script needs to complete in one go.

During the window:

4. **Do not pause / suspend the laptop** between `azd provision` and the postprovision verification. The script must complete uninterrupted.
5. **Do not open new shells** that read `azd env get-values` — the SQL admin password is in there until Step 5 deletes its KV copy.
6. **Watch the output** — every `[INFO]` line is significant; on `[ERROR]` stop and read.

After the window closes (Step 5 success):

7. **Verify in the Azure Portal** that the SQL server's "Active Directory admin" section shows AAD-only is on.
8. **Verify in Key Vault** that `SQL-ADMIN-PASSWORD-BOOTSTRAP` is gone (soft-deleted; it stays recoverable for 7 days per KV soft-delete policy — that's acceptable since the value was rotated by `newGuid()` and is no longer in use).
9. **Run the CD smoke test manually** (`curl -fsS https://<func-app>.azurewebsites.net/api/ping`) to confirm the Function App is reachable.

If the postprovision script fails mid-window, see `incident_response.md` Scenario D and the rollback procedure in `infra/scripts/postprovision.{ps1,sh}` (the script's `finally` / `trap` blocks always re-enable the RLS policy even on partial failure).

## When the window re-opens

The window re-opens any time you:

- Re-provision the SQL server (`azd down` then `azd up` — destroys the existing AAD-only state and re-creates from Bicep with `azureADOnlyAuthentication: false` as the initial value).
- Manually disable AAD-only (`az sql server ad-only-auth disable`) for an emergency SQL-auth login (e.g., to debug a stuck migration). Re-enable immediately afterwards.

Both cases follow the same runbook. There is **no** scheduled re-bootstrap — the system is designed to flip AAD-only once and stay there.

## Residual risk acceptance

Per Etapa-6 threat model RR-08, this window is an accepted residual risk for the thesis-grade posture. Production hardening would either:

- Use the Bicep deployment-script extension to flip AAD-only inside the same `azd provision` run (eliminates the window but adds an indirect dependency).
- Provision the SQL server in a private VNet with a private endpoint (incompatible with the Y1 Consumption Functions free tier).
- Use Azure SQL Managed Instance (paid SKU; out of scope for the $0/month commitment).

None of these are adopted for v1.0. Re-evaluate before any production deployment with real PII.

## Change history

- 2026-05-16: initial version (Etapa 6 security hardening pass).
