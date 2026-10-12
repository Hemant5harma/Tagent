"""Read-only, sanitized deployment and demo-account status audit."""

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from trade_agent.db import (
    AgentActivityRecord,
    AutonomousRunRecord,
    BrokerDealRecord,
    DashboardCacheRecord,
    EquityRecord,
    ExecutionIntentRecord,
    IncomingUpdateRecord,
    LedgerEventRecord,
    ProposalRecord,
    SystemStateRecord,
)
from trade_agent.main import build_runtime
from trade_agent.reporting import closed_trades, dashboard_data

LAST_CHECK = datetime(2026, 9, 30, 16, 19, tzinfo=UTC)


def stamp(value):
    return (
        value.replace(tzinfo=UTC).isoformat()
        if value and value.tzinfo is None
        else value.isoformat()
        if value
        else None
    )


def main():
    app, _ = build_runtime()
    service, cfg = app.service, app.settings
    account = service.broker.account(cfg.account_id)
    state = service.broker.state(cfg.account_id)
    account_dashboard = dashboard_data(service.database, "all", "account")
    agent_dashboard = dashboard_data(service.database, "all", "agent")
    with service.database.session() as session:
        proposals = session.scalars(
            select(ProposalRecord).order_by(ProposalRecord.created_at)
        ).all()
        intents = session.scalars(
            select(ExecutionIntentRecord).order_by(ExecutionIntentRecord.created_at)
        ).all()
        deals = session.scalars(
            select(BrokerDealRecord).where(BrokerDealRecord.account_id == cfg.account_id)
        ).all()
        updates = session.scalars(
            select(IncomingUpdateRecord).order_by(IncomingUpdateRecord.received_at.desc()).limit(1)
        ).all()
        activities = session.scalars(
            select(AgentActivityRecord).order_by(AgentActivityRecord.created_at.desc()).limit(1)
        ).all()
        runs = session.scalars(
            select(AutonomousRunRecord).order_by(AutonomousRunRecord.created_at.desc())
        ).all()
        snapshots = session.scalars(
            select(EquityRecord)
            .where(EquityRecord.account_id == cfg.account_id)
            .order_by(EquityRecord.observed_at)
        ).all()
        caches = session.scalars(
            select(DashboardCacheRecord).where(
                DashboardCacheRecord.key.in_(
                    ["broker", "history", "telegram", "protection", "model", "limits", "autonomy"]
                )
            )
        ).all()
        system = session.scalars(
            select(SystemStateRecord).where(
                SystemStateRecord.key.in_([f"demo_budget:{cfg.account_id}", "entries_paused"])
            )
        ).all()
        events = session.scalars(
            select(LedgerEventRecord).where(LedgerEventRecord.created_at >= LAST_CHECK)
        ).all()
    all_closed = closed_trades(
        [d.payload for d in deals],
        {str(i.broker_position_id) for i in intents if i.broker_position_id},
        {str(p.position_id) for p in state.positions},
    )
    since = [t for t in all_closed if datetime.fromisoformat(t["closed_at"]) >= LAST_CHECK]
    later_deals = [d for d in deals if d.occurred_at.replace(tzinfo=UTC) >= LAST_CHECK]
    daily = defaultdict(lambda: {"trades": 0, "agent": 0, "external": 0, "net_pnl": Decimal(0)})
    for trade in since:
        row = daily[trade["closed_at"][:10]]
        row["trades"] += 1
        row["agent" if trade["source"] == "agent" else "external"] += 1
        row["net_pnl"] += Decimal(trade["net_pnl"])
    research_role_health = Counter(
        (role, detail.get("status", "unknown"))
        for run in runs
        if run.status == "research_failed"
        for role, detail in (run.decision or {}).get("research_health", {}).items()
    )
    research_reasons = Counter(
        str((run.decision or {}).get("reason", ""))[:100]
        for run in runs
        if run.status == "research_failed"
    )
    result = {
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "last_verified_check_utc": LAST_CHECK.isoformat(),
        "deployment": {
            "account": cfg.account_id,
            "mode": cfg.trading_mode,
            "broker_mode": cfg.broker_mode,
            "autonomy_mode": cfg.autonomy_mode,
            "entries_paused": service._paused(),
            "allowed_symbols": cfg.allowed_symbols,
            "max_concurrent_exposures": cfg.max_concurrent_exposures,
            "max_estimated_loss": str(cfg.max_estimated_loss),
            "daily_loss_limit": str(cfg.demo_daily_loss_limit),
            "drawdown_limit": str(cfg.demo_drawdown_limit),
        },
        "account_now": {
            "is_demo": account.is_demo,
            "currency": account.currency,
            "balance": str(account.balance),
            "equity": str(account.equity),
            "free_margin": str(account.margin_free),
            "open_positions": len(state.positions),
            "pending_orders": len(state.orders),
        },
        "all_time_dashboard": {
            "account_metrics": account_dashboard["metrics"],
            "agent_metrics": agent_dashboard["metrics"],
            "origin_metrics": account_dashboard.get("origin_metrics"),
            "drawdown": account_dashboard["drawdown"],
            "coverage": account_dashboard["coverage"],
        },
        "since_last_check": {
            "closed_trade_count": len(since),
            "daily": {
                date: {**values, "net_pnl": str(values["net_pnl"])}
                for date, values in sorted(daily.items())
            },
            "agent_trades": [
                {
                    key: t.get(key)
                    for key in (
                        "position_id",
                        "symbol",
                        "side",
                        "volume",
                        "opened_at",
                        "closed_at",
                        "net_pnl",
                        "origin",
                    )
                }
                for t in since
                if t["source"] == "agent"
            ],
            "prior_unprotected_position": [
                {key: t.get(key) for key in ("position_id", "closed_at", "net_pnl", "source")}
                for t in all_closed
                if t["position_id"] == "2111993059"
            ],
            "largest_losses": [
                {key: t.get(key) for key in ("position_id", "closed_at", "net_pnl", "source")}
                for t in sorted(since, key=lambda t: Decimal(t["net_pnl"]))[:5]
            ],
            "broker_deal_count": len(later_deals),
            "last_broker_deal_at": stamp(max((d.occurred_at for d in deals), default=None)),
            "new_proposals": len(
                [p for p in proposals if p.created_at.replace(tzinfo=UTC) >= LAST_CHECK]
            ),
            "new_execution_intents": len(
                [i for i in intents if i.created_at.replace(tzinfo=UTC) >= LAST_CHECK]
            ),
            "new_intent_statuses": dict(
                Counter(i.status for i in intents if i.created_at.replace(tzinfo=UTC) >= LAST_CHECK)
            ),
            "new_intent_tickets": [
                {
                    "position_id": i.broker_position_id,
                    "order_id": i.broker_order_id,
                    "status": i.status,
                }
                for i in intents
                if i.created_at.replace(tzinfo=UTC) >= LAST_CHECK
            ],
        },
        "last_activity": {
            "telegram_update_at": stamp(updates[0].received_at) if updates else None,
            "telegram_update_status": updates[0].status if updates else None,
            "agent_activity_at": stamp(activities[0].created_at) if activities else None,
            "agent_activity_kind": activities[0].kind if activities else None,
            "autonomous_run_at": stamp(runs[0].created_at) if runs else None,
            "autonomous_run_mode": runs[0].mode if runs else None,
            "autonomous_run_status": runs[0].status if runs else None,
            "autonomous_runs_since_check": len(
                [r for r in runs if r.created_at.replace(tzinfo=UTC) >= LAST_CHECK]
            ),
            "autonomous_run_statuses_since_check": dict(
                Counter(r.status for r in runs if r.created_at.replace(tzinfo=UTC) >= LAST_CHECK)
            ),
            "first_equity_snapshot_at": stamp(snapshots[0].observed_at) if snapshots else None,
            "first_equity_snapshot_value": snapshots[0].payload.get("equity")
            if snapshots
            else None,
            "last_equity_snapshot_at": stamp(snapshots[-1].observed_at) if snapshots else None,
        },
        "cache_times": {c.key: stamp(c.updated_at) for c in caches},
        "system": {
            s.key: json.loads(s.value) if s.value.startswith("{") else s.value for s in system
        },
        "autonomy_worker": account_dashboard.get("autonomy_worker"),
        "autonomy_market_data": account_dashboard.get("autonomy_market_data"),
        "ledger_events_since_check": dict(Counter(event.kind for event in events)),
        "first_unexpected_broker_activity_at": stamp(
            min(
                (
                    event.created_at
                    for event in events
                    if event.kind == "unexpected_broker_activity"
                ),
                default=None,
            )
        ),
        "unexpected_broker_activity_refs": [
            event.payload.get("references", [])
            for event in events
            if event.kind == "unexpected_broker_activity"
        ],
        "research_failure_categories": dict(
            Counter(
                "timeout"
                if "timeout" in (run.error or "").lower()
                else "rate_limit"
                if "rate limit" in (run.error or "").lower()
                else "provider"
                if "provider" in (run.error or "").lower()
                else "invalid_response"
                if "invalid" in (run.error or "").lower()
                else "other"
                for run in runs
                if run.status == "research_failed"
                and run.created_at.replace(tzinfo=UTC) >= LAST_CHECK
            )
        ),
        "research_failed_role_health": {
            f"{role}:{status}": count for (role, status), count in research_role_health.items()
        },
        "research_failed_top_reasons": research_reasons.most_common(5),
    }
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
