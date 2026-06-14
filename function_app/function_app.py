"""Function App entry point — wires every trigger in this app.

Five triggers:
- TimerTrigger_DailyGenerator    (NCRONTAB '0 0 7 * * 1-5')  — calls tcp.synth.run_daily.
- WarmupTrigger                  (NCRONTAB '0 55 6 * * 1-5') — runs SELECT 1 to resume SQL.
- TimerTrigger_BacpacExport      (NCRONTAB '0 0 8 * * 0')    — weekly BACPAC export (ADR-004).
- HttpTrigger_Ping               (GET /api/ping)             — anonymous warm-up endpoint.
- HttpTrigger_AskAssistant       (POST /api/ask)             — stub returning 501; real impl in Etapa 5.

The app trusts the WEBSITE_TIME_ZONE env var (set to ``E. Europe Standard Time``
in the App Service configuration) to interpret NCRONTAB expressions in
Europe/Bucharest. DST transitions are handled by the Functions runtime, not by
this code — see ADR-004 §"Why Sunday 08:00 RO" and ``03_architecture.md §4.2``.

All HTTP routes are automatically prefixed with '/api/' per host.json's
``extensions.http.routePrefix`` setting. Triggers must declare ``route='ping'``,
``route='ask'``, etc., which resolve to ``/api/ping``, ``/api/ask``, etc.
"""

from __future__ import annotations

import logging

import azure.functions as func
import structlog

# -----------------------------------------------------------------------------
# Structlog configuration — Etapa-10 arch10-MJ-01 fix.
#
# Without an explicit `structlog.configure()` call, `structlog.get_logger()`
# returns a lazy proxy whose `.info(name, **kwargs)` falls back to stdlib
# logging. In that path the positional `event` argument becomes the log
# `message` and there is no `customDimensions["event"]` key produced by the
# App Insights ingestion. Three KQL queries (03 anthropic tokens, 07 audit,
# 08 BACPAC health) filter on `customDimensions["event"] == "tcp.*"` — they
# would return zero rows in production even though the events are emitted.
#
# `EventRenamer("event")` explicitly carries the positional event name into a
# field literally named `event`, which the stdlib bridge then surfaces as
# `customDimensions["event"]` in App Insights. JSONRenderer keeps the output
# machine-parseable for the Functions log pipeline.
# -----------------------------------------------------------------------------
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.EventRenamer("event"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)
# The Functions Python worker installs its own root logger; structlog's
# stdlib bridge inherits its level, so we set the root level once here so
# `_log.info(...)` calls actually emit at default verbosity.
logging.getLogger().setLevel(logging.INFO)

app: func.FunctionApp = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Importing the trigger modules executes their ``@app.<trigger>`` decorators,
# which register the functions against the ``app`` instance above. The imports
# are intentionally placed AFTER the FunctionApp() instantiation and AFTER the
# ``from __future__`` import; nothing else may run at module-import time so the
# Azure Functions worker can probe the app cheaply.
from triggers import (  # noqa: E402, F401  (decorator side effects)
    ask,
    bacpac_export,
    daily_generator,
    ping,
    warmup,
)
