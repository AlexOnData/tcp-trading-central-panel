"""TimerTrigger_DailyGenerator — invokes tcp.synth.run_daily once per weekday at 07:00 RO.

Cron: ``0 0 7 * * 1-5`` (Mon-Fri 07:00 in ``WEBSITE_TIME_ZONE = E. Europe Standard Time``).
The trigger fires regardless of RO public holidays; ``tcp.synth.run_daily`` short-circuits
to ``status='skipped_holiday'`` (resolved via ``dim_Date``) when the target date is a
non-trading day. The proc itself additionally returns ``status='skipped_non_trading_day'``
as a defence-in-depth guard.
"""

from __future__ import annotations

import azure.functions as func
import structlog

from function_app import app
from tcp.synth import run_daily

_log = structlog.get_logger(__name__)


@app.timer_trigger(
    schedule="0 0 7 * * 1-5",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def daily_generator(timer: func.TimerRequest) -> None:
    """Run the daily synthetic-trade generator under the admin-scoped MI session.

    The runner reads ``TCP_GENERATOR_OID`` from the environment, sets the ADR-003
    ``SESSION_CONTEXT('aad_object_id')`` on its own connection, and persists the
    day's rows via ``dbo.usp_GenerateDailyTrades``. All failure modes are logged
    and re-raised so the platform retry policy and App Insights alerting fire.

    Args:
        timer: The Functions-runtime ``TimerRequest`` (carries ``past_due``).
    """
    if timer.past_due:
        _log.warning("tcp.func.daily_generator.past_due")
    try:
        result = run_daily()
        _log.info("tcp.func.daily_generator.complete", **result)
    except Exception:
        _log.exception("tcp.func.daily_generator.failed")
        raise
