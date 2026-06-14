# TCP Function App

> **Component scope.** This README documents the `function_app/` directory only. For project-wide context, deploy walkthrough, troubleshooting, and the full doc index, see the [top-level README](../README.md). For terminology, see the [glossary](../docs/glossary.md).

Single Azure Functions Python (v2 programming model) app hosting every
backend trigger for **TCP — Trading Central Panel**. One `function_app.py`
file with decorator-based registration; trigger bodies live under
`triggers/` and import the shared `app` instance.

## Triggers

| Name | Kind | Schedule / Route | Purpose |
| --- | --- | --- | --- |
| `TimerTrigger_DailyGenerator` | Timer | `0 0 7 * * 1-5` (RO) | Calls `tcp.synth.run_daily` (ADR-003 admin session). |
| `WarmupTrigger` | Timer | `0 55 6 * * 1-5` (RO) | `SELECT 1` to resume SQL before the 07:00 generator. |
| `TimerTrigger_BacpacExport` | Timer | `0 0 8 * * 0` (RO) | Weekly BACPAC export (ADR-004); REST call wired in Etapa 5. |
| `HttpTrigger_Ping` | HTTP | `GET /api/ping` | Anonymous warm-up; emits `tcp.sql.resume_ms`. |
| `HttpTrigger_AskAssistant` | HTTP | `POST /api/ask` | Header-validated stub in Etapa 4; full LLM pipeline in Etapa 5. |

NCRONTAB expressions are interpreted in Europe/Bucharest via
`WEBSITE_TIME_ZONE = E. Europe Standard Time` — DST-safe across EET/EEST.

## Running locally

```powershell
# From the repo root.
cd function_app

# Install runtime deps into the .python_packages tree the Functions Core Tools expect.
uv pip install --target .python_packages/lib/site-packages -r requirements.txt

# Copy the template and fill the dev values.
cp local.settings.json.template local.settings.json

# Start the runtime (Azure Functions Core Tools v4 + Python 3.12).
func start
```

The local dev path expects:

- ODBC Driver 18 for SQL Server installed on the host.
- `docker compose -f ../docker-compose.dev.yml up -d` for a local SQL instance
  matching the `TCP_SQL_*` env vars in `local.settings.json`.
- The schema applied via `sqlcmd -i db/migrations/V001__init.sql -i db/migrations/V002__synth_logic.sql` against the Docker SQL container. See [`db/README.md`](../db/README.md) for the canonical apply procedure (and [`docs/setup.md`](../docs/setup.md) §A.3 for the full Track A walkthrough).

## `local.settings.json` template (DO NOT commit a real one)

```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "WEBSITE_TIME_ZONE": "E. Europe Standard Time",
    "TCP_SQL_SERVER": "localhost,1433",
    "TCP_SQL_DATABASE": "tcp_dev",
    "TCP_SQL_DEV_USER": "sa",
    "TCP_SQL_DEV_PASSWORD": "YourStrong!Passw0rd",
    "TCP_GENERATOR_OID": "00000000-0000-0000-0000-000000000000",
    "SWA_FORWARDED_SECRET": "dev-shared-secret",
    "ANTHROPIC_API_KEY": "set-real-key-for-Etapa-5",
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com"
  }
}
```

The committed `local.settings.json.template` mirrors this block verbatim; copy
it to `local.settings.json` (already in `.gitignore`) and edit the values for
your dev environment.

## Cross-references

- `docs/design/03_architecture.md §3` — logical architecture and per-trigger
  request paths.
- `docs/design/03_architecture.md §4.2` — Function App configuration, app
  settings, RBAC, and observability budgets.
- `docs/decisions/ADR-003-rls-session-context.md` — the SESSION_CONTEXT
  contract that the daily generator and (in Etapa 5) the AI assistant must
  honour on every connection check-out.
- `docs/decisions/ADR-004-bacpac-export-schedule.md` — BACPAC trigger spec
  (REST endpoint, polling cadence, KV credential references).
- `infra/main.bicep` — cloud deploy entry point; the Function App resource is
  defined in `infra/modules/functions.bicep` (Bicep produced by a parallel
  agent in Etapa 4).
