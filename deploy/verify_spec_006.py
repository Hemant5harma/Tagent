"""Read-only verification after authorized demo activation."""

import json
from datetime import UTC, datetime

from sqlalchemy import func, select

from trade_agent.db import BrokerDealRecord, DashboardCacheRecord, EquityRecord, SystemStateRecord
from trade_agent.main import build_runtime


def main():
    app, _ = build_runtime()
    service, settings = app.service, app.settings
    account = service.broker.account(settings.account_id)
    state = service.broker.state(settings.account_id)
    with service.database.session() as session:
        budget = session.get(SystemStateRecord, f"demo_budget:{settings.account_id}")
        archive = session.get(SystemStateRecord, f"spec006_authorized_reset:{settings.account_id}")
        reviewed = session.get(SystemStateRecord, f"reviewed_external_exposure:{settings.account_id}")
        history = session.get(DashboardCacheRecord, "history")
        equity_count = session.scalar(select(func.count()).select_from(EquityRecord).where(EquityRecord.account_id == settings.account_id))
        deal_count = session.scalar(select(func.count()).select_from(BrokerDealRecord).where(BrokerDealRecord.account_id == settings.account_id))
    assert archive and reviewed and budget and history
    elapsed = (datetime.now(UTC) - history.updated_at.replace(tzinfo=UTC)).total_seconds()
    report = {
        "demo": account.is_demo,
        "allowed_symbols": settings.allowed_symbols,
        "max_concurrent_exposures": settings.max_concurrent_exposures,
        "max_estimated_loss_usd": str(settings.max_estimated_loss),
        "daily_loss_stop_usd": str(settings.demo_daily_loss_limit),
        "evaluation_drawdown_stop_usd": str(settings.demo_drawdown_limit),
        "entries_paused": service._paused(),
        "breaker_latched": json.loads(budget.value)["breached"],
        "reviewed_references": json.loads(reviewed.value)["references"],
        "open_positions": len(state.positions),
        "pending_orders": len(state.orders),
        "equity_snapshots": equity_count,
        "broker_deals_recorded": deal_count,
        "history_age_seconds": round(elapsed, 1),
    }
    print(json.dumps(report))
    assert account.is_demo and settings.max_concurrent_exposures == 5
    assert settings.max_estimated_loss == 10 and settings.demo_daily_loss_limit == 300 and settings.demo_drawdown_limit == 1000
    assert not report["entries_paused"] and not report["breaker_latched"]
    assert len(state.positions) + len(state.orders) < 5
    assert equity_count and elapsed < 120


if __name__ == "__main__":
    main()
