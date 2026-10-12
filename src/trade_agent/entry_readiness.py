"""Read-only early diagnostics; never replaces the execution risk checks."""

import json
from typing import TYPE_CHECKING

from trade_agent.broker import BrokerUnavailable
from trade_agent.db import SystemStateRecord

if TYPE_CHECKING:
    from trade_agent.service import TradeService


def entry_blockers(service: "TradeService") -> list[str]:
    reasons = []
    if service._paused():
        reasons.append(
            "New entries are paused. Resume requires an explicit operator decision after the other blockers are resolved."
        )
    with service.database.session() as session:
        row = session.get(SystemStateRecord, f"demo_budget:{service.settings.account_id}")
        if row:
            try:
                breached = json.loads(row.value)["breached"]
            except (ValueError, KeyError, TypeError):
                reasons.append(
                    "The loss-limit record could not be verified; operator review is required."
                )
            else:
                if breached:
                    reasons.append(
                        "The demo loss-limit breaker is latched. An operator must review the loss history and explicitly authorize a reset/new evaluation; saying 'open' cannot reset it."
                    )
    if service.settings.broker_mode != "fake":
        try:
            state = service.broker.state(service.settings.account_id)
        except (BrokerUnavailable, ValueError):
            reasons.append(
                "Current broker exposure could not be verified. Retry /status when the connector is available."
            )
        else:
            if (
                len(state.positions) + len(state.orders)
                >= service.settings.max_concurrent_exposures
            ):
                reasons.append(
                    f"The account has {len(state.positions)} open position(s) and {len(state.orders)} pending order(s). "
                    f"The approved limit is {service.settings.max_concurrent_exposures} total exposure(s). Check /positions and /orders; wait for closure or explicitly request a separately confirmed close/cancel. Existing exposure will not be changed automatically."
                )
    return reasons


def blocker_message(reasons: list[str]) -> str:
    return (
        "Cannot open a new trade yet:\n"
        + "\n".join(f"• {reason}" for reason in reasons)
        + "\nNo new prices were calculated, no confirmation was created, and no order was submitted. "
        "Repeating 'open this trade' will not bypass these blocks. Once resolved, send the trade details again for one fresh Confirm trade button."
    )
