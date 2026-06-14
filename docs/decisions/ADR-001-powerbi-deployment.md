# ADR-001: PowerBI report deployment strategy

- **Status**: Accepted
- **Date**: 2026-05-15
- **Stage**: Etapa 0 (foundation), informs Etapa 7 (PowerBI build)

## Context

Etapa 7 of the project plan calls for building the PowerBI semantic model and pages **programmatically** so the report is versioned in git and reproducible via `azd up`. The user installed a PowerBI extension in VS Code, which we initially intended to wire as a Claude Code MCP server.

Investigation in Etapa 0 found:

- No MCP server config exists in `~/.claude/settings.json` (global) or `.claude/settings.json` (project).
- The Claude Code settings.json schema does not accept arbitrary `mcpServers` keys; MCP servers live in `.mcp.json` (project-level) or are managed by the Claude Code MCP CLI.
- The installed VS Code extension does not currently expose a stdio MCP endpoint that Claude Code can spawn — it is a regular VS Code extension that operates through VS Code commands, not a standalone MCP server binary.

We need a deployment path that works **today** and can be upgraded to a true MCP-based path later if a suitable server becomes available.

## Decision

Use the **PowerBI REST API via `az rest`** as the primary, automation-friendly path for Etapa 7. Keep a placeholder `.mcp.json` so a future MCP server can be wired in without restructuring.

### Primary path: REST API through `az rest`

All Etapa 7 actions use authenticated REST calls to `https://api.powerbi.com/v1.0/myorg/...` via `az rest`:

| Action | Endpoint |
|---|---|
| List workspaces | `GET /groups` |
| Create workspace | `POST /groups?workspaceV2=true` |
| Import / update dataset (TMDL) | `POST /groups/{groupId}/imports?datasetDisplayName=...` (XMLA endpoint + `Microsoft.AnalysisServices.Tabular` for full TMDL fidelity) |
| Bind to data source | `POST /groups/{groupId}/datasets/{datasetId}/Default.UpdateDatasources` |
| Take dataset ownership (service principal) | `POST /groups/{groupId}/datasets/{datasetId}/Default.TakeOver` |
| Configure scheduled refresh | `PATCH /groups/{groupId}/datasets/{datasetId}/refreshSchedule` |
| Trigger refresh | `POST /groups/{groupId}/datasets/{datasetId}/refreshes` |
| Get refresh history | `GET /groups/{groupId}/datasets/{datasetId}/refreshes` |

`az rest` uses the Azure CLI AAD token, scoped to `https://analysis.windows.net/powerbi/api`. Service principal credentials live in Key Vault and are surfaced to GitHub Actions via OIDC federated credentials.

The TMDL and PBIR sources are versioned under `powerbi/` in the repo:

```
powerbi/
├── model/
│   ├── database.tmdl
│   ├── tables/
│   │   ├── fact_Trades.tmdl
│   │   ├── dim_*.tmdl
│   │   └── ...
│   ├── relationships.tmdl
│   ├── measures.tmdl
│   ├── calculation_groups.tmdl
│   └── roles.tmdl              # RLS (floor managers see own floor, etc.)
├── pages/
│   ├── 01_calendar.json        # PBIR layout
│   ├── 02_database.json
│   ├── 03_overview.json
│   ├── 04_performance.json
│   └── 05_edge_analysis.json
└── deploy.sh                   # bash wrapper around az rest calls
```

### Fallback / future MCP path

`.mcp.json` is created with an empty `mcpServers` object as a placeholder. When a production-grade PowerBI MCP server becomes available, populate the entry there; `az rest` calls in `powerbi/deploy.sh` can be replaced by MCP `Skill` invocations without touching the TMDL/PBIR sources.

### Final visual polish

PBIR is still in preview (as of the project start date); a small portion of the layout (decorative spacing, custom tooltips, theme tweaks) is expected to require a one-time pass in PowerBI Desktop. This is documented in `docs/operations.md` under "PowerBI build" and is the only non-automated step in Etapa 7.

## Consequences

**Positive:**

- Deployment is fully automatable from day one — no dependency on an external MCP server.
- The semantic model (the highest-value part) is 100 % code-versioned via TMDL.
- Service principal authentication flows through the same Azure / Key Vault / OIDC pipeline as the rest of the stack — no new identity primitives.
- Migrating to MCP later is a localized change in `powerbi/deploy.sh`.

**Negative:**

- `az rest` calls are more verbose than equivalent MCP tool invocations.
- PowerBI service principal must be granted "Allow service principals to use Power BI APIs" in the PowerBI admin portal — a one-time manual step, documented in `docs/powerbi/setup_guide.md`.

**Neutral:**

- Final visual polish in Desktop is required regardless of the deployment mechanism (REST or MCP) because PBIR support for visual fidelity is still maturing.

## References

- Plan: `C:\Users\Admin\.claude\plans\vreau-sa-te-uiti-cryptic-twilight.md` (Etapa 7)
- PowerBI REST API: https://learn.microsoft.com/en-us/rest/api/power-bi/
- TMDL specification: https://learn.microsoft.com/en-us/analysis-services/tmdl/tmdl-overview
- PBIR format: https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-report
