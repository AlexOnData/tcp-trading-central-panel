# Architecture Decision Records — Index

This index summarises every ADR in chronological order. The full record for each decision lives in `ADR-NNN-*.md`; this page is the at-a-glance lookup used by reviewers, the top-level README, and the academic thesis chapter.

| ADR | Title | Status | Stage filed | Outcome (one line) |
|---|---|---|---|---|
| [001](ADR-001-powerbi-deployment.md) | PowerBI report deployment strategy | Accepted | Etapa 0 → informs E7 | Use the PowerBI REST API via `az rest` for unattended deploys; keep `.mcp.json` as a placeholder for a future MCP path. |
| [002](ADR-002-daily-pnl-materialisation.md) | Daily PnL materialisation strategy | Accepted | Etapa 1 → informs E2/E8 | Materialise `fact_DailyTraderPnL` via a per-day `MERGE` inside `usp_GenerateDailyTrades` rather than re-evaluating an 8-table view stack on every Sharpe / Sortino visual. |
| [003](ADR-003-rls-session-context.md) | Row-Level Security via `SESSION_CONTEXT('aad_object_id')` | Accepted | Etapa 1 → enforced E2/E5 | The Function App writes the caller's AAD `oid` into `SESSION_CONTEXT` on every connection check-out; RLS predicates join `dim_UserRoles` on that value; deny-by-default when unset. |
| [004](ADR-004-bacpac-export-schedule.md) | BACPAC export schedule + ownership | Accepted | Etapa 1 → implemented E4/E5 | The Function App `TimerTrigger_BacpacExport` is the single owner of the weekly BACPAC export on NCRONTAB `0 0 8 * * 0` (Sunday 08:00 Europe/Bucharest). The previously-proposed GHA-cron path is dropped. |
| [005](ADR-005-scope-resolution-rls-bypass.md) | Scope resolution via bounded admin-bypass connection + in-process rate limit | Accepted | Etapa 5 → informs E6 security review | Look up the caller's `scope` with a single parameterised `SELECT TOP 1 scope FROM dbo.dim_UserRoles WHERE aad_object_id = ?` on an admin-bypass connection; close immediately. Per-OID 10 req/min sliding-window in-process. Documented residual: single-instance only. |

---

## How to read an ADR

Every ADR follows the same four sections:

1. **Context** — what problem we were trying to solve, what reviews / requirements drove the decision, what the alternatives were.
2. **Decision** — the actual rule, in normative language ("MUST", "MUST NOT").
3. **Consequences** — both the desired outcomes and the residual risks / follow-up costs accepted in exchange.
4. **Status notes** — supersession history if any. ADR-NNN never gets edited substantively after Accepted; new ADRs supersede it.

A few cross-cutting threads to watch:

- **ADR-003 ↔ ADR-005** are paired. The RLS contract (ADR-003) only works if the caller's scope is known *before* `SESSION_CONTEXT` is set on the user-facing connection; ADR-005 documents the bounded bypass that resolves the scope without weakening ADR-003.
- **ADR-002 ↔ ADR-008 (future)** — the materialisation choice in ADR-002 has cost-budget implications that the (not-yet-filed) error-budget policy ADR will reference once 30 days of telemetry exist.
- **ADR-004 ↔ residual RR-08** — the bootstrap window (3-8 minutes between `azd provision` and the AAD-only flip) is documented as RR-08 in [`../security/threat_model.md`](../security/threat_model.md). ADR-004 retains the bootstrap SQL admin password specifically because the BACPAC Export REST API requires SQL auth.

## Filing a new ADR

When a future change introduces a load-bearing decision (one that future contributors should not silently re-litigate), copy the most-recent ADR as a template, increment the number, and link it from this index. Decisions that are easy to reverse, scoped to a single file, or follow established repo convention do **not** need an ADR — the commit message is sufficient.

ADRs are intentionally short. Aim for ~300-500 lines. The full design narrative belongs in `docs/design/*.md`; the ADR captures the decision and the residual posture.
