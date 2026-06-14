"""Synthetic trade-data generator for TCP — Trading Central Panel.

Produces deterministic, locale-aware (ro_RO) synthetic trading activity
for the 32-employee org. The daily generator is called by the Function
App Timer Trigger at 07:00 Europe/Bucharest; the one-shot employee
seeder runs at deployment time.
"""

from tcp.db import set_admin_session_context
from tcp.synth.commissions import compute_commission
from tcp.synth.fx_rates import get_fx_rate
from tcp.synth.runner import previous_business_day, run_daily
from tcp.synth.seed_employees import seed_employees
from tcp.synth.trades import (
    MarketRow,
    OrderTypeRow,
    SessionRow,
    TradeRow,
    TraderProfile,
    generate_for_date,
)

__all__ = [
    "MarketRow",
    "OrderTypeRow",
    "SessionRow",
    "TradeRow",
    "TraderProfile",
    "compute_commission",
    "generate_for_date",
    "get_fx_rate",
    "previous_business_day",
    "run_daily",
    "seed_employees",
    "set_admin_session_context",
]
