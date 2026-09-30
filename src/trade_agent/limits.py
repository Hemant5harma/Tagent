"""Persisted USD demo loss circuit breakers, independent of the model."""

import json
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text

from trade_agent.db import BrokerDealRecord, DashboardCacheRecord, SystemStateRecord
from trade_agent.reporting import aware, now
from trade_agent.risk import RiskRejected


def check_demo_budget(service: Any, account: Any) -> None:
    if service.settings.broker_mode == "fake":
        return
    if not account.is_demo or account.currency != "USD":
        raise RiskRejected("This release requires the approved USD demo account")
    stamp = now()
    reason = None
    with service.database.session() as session:
        if service.database.engine.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(830022)"))
        history = session.get(DashboardCacheRecord, "history")
        if history is None or (stamp - aware(history.updated_at)).total_seconds() > 120:
            raise RiskRejected("Broker history must be synchronized before accepting entry risk")
        key = f"demo_budget:{account.account_id}"
        row = session.get(SystemStateRecord, key, with_for_update=True)
        if row is None:
            values: dict[str, Any] = {
                "started_at": stamp.timestamp(),
                "high": str(account.equity),
                "day_high": str(account.equity),
                "day_start": str(account.equity),
                "date": stamp.date().isoformat(),
                "breached": False,
            }
            row = SystemStateRecord(key=key, value="", updated_at=stamp)
            session.add(row)
        else:
            values = json.loads(row.value)
        flows = sum(
            (
                Decimal(d.payload.get("profit") or "0")
                for d in session.scalars(
                    select(BrokerDealRecord).where(
                        BrokerDealRecord.account_id == account.account_id
                    )
                )
                if d.payload.get("type") in {"2", "3"}
                and int(d.payload["time"]) > values["started_at"]
            ),
            Decimal(0),
        )
        equity = account.equity - flows
        if values["date"] != stamp.date().isoformat():
            values.update(
                date=stamp.date().isoformat(), day_start=str(equity), day_high=str(equity)
            )
        values["high"] = str(max(Decimal(values["high"]), equity))
        values["day_high"] = str(max(Decimal(values["day_high"]), equity))
        daily = max(Decimal(values["day_start"]), Decimal(values["day_high"])) - equity
        drawdown = Decimal(values["high"]) - equity
        if (
            daily >= service.settings.demo_daily_loss_limit
            or drawdown >= service.settings.demo_drawdown_limit
        ):
            values["breached"] = True
        if values["breached"]:
            reason = "Demo loss circuit breaker is latched. Entries require an operator review; existing protection remains active."
        row.value, row.updated_at = json.dumps(values), stamp
    if reason:
        if not service._paused():
            service.set_paused(True)
        raise RiskRejected(reason)
