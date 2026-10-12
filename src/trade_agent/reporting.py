"""Broker-grounded reporting. No model output contributes financial values."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import load_only

from trade_agent.db import (
    AgentActivityRecord,
    AutonomousRunRecord,
    BrokerDealRecord,
    DashboardCacheRecord,
    Database,
    EquityRecord,
    ExecutionIntentRecord,
    LedgerEventRecord,
    ProposalRecord,
)


def now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def scrub(text: str) -> str:
    text = re.sub(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b", "[redacted token]", text)
    text = re.sub(r"(?i)(password|token|api[_ -]?key|secret)\s*[:=]?\s*\S+", r"\1 [redacted]", text)
    text = re.sub(r"https?://\S+", "[link]", text)
    return text[:1000]


def activity(db: Database, kind: str, summary: str, latency_ms: int | None = None) -> None:
    with db.session() as session:
        session.add(
            AgentActivityRecord(
                kind=kind, summary=scrub(summary), latency_ms=latency_ms, created_at=now()
            )
        )


def put_cache(db: Database, key: str, payload: dict) -> None:
    with db.session() as session:
        row = session.get(DashboardCacheRecord, key)
        if row is None:
            session.add(DashboardCacheRecord(key=key, payload=payload, updated_at=now()))
        else:
            row.payload, row.updated_at = payload, now()


def closed_trades(
    deals: list[dict],
    managed: set[str],
    open_ids: set[str],
    origins: dict[str, set[str]] | None = None,
) -> list[dict]:
    """Only balanced, fully closed, non-reversal lifecycles are eligible for win rate."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for deal in deals:
        if deal.get("type") in {"0", "1"} and deal.get("position_id") not in {"", "0", None}:
            groups[str(deal["position_id"])].append(deal)
    result = []
    for position, items in groups.items():
        items.sort(key=lambda d: (int(d.get("time_msc") or 0), int(d["ticket"])))
        entries = [d for d in items if d.get("entry") == "0"]
        exits = [d for d in items if d.get("entry") in {"1", "3"}]
        in_volume = sum((Decimal(d["volume"]) for d in entries), Decimal(0))
        out_volume = sum((Decimal(d["volume"]) for d in exits), Decimal(0))
        if (
            not entries
            or not exits
            or in_volume != out_volume
            or position in open_ids
            or any(d.get("entry") == "2" for d in items)
        ):
            continue
        # Never treat another strategy's scale-in on a netted position as our performance.
        attribution = (
            "agent"
            if position in managed
            and all(d.get("magic") == entries[0].get("magic") for d in entries)
            else "external"
        )
        candidates = origins.get(position, set()) if origins is not None else {"manual"}
        origin = (
            next(iter(candidates))
            if attribution == "agent"
            and len(candidates) == 1
            and candidates <= {"manual", "autonomous"}
            else "unknown"
            if position in managed
            else "external"
        )

        def value(field: str, records: list[dict] = items) -> Decimal:
            return sum((Decimal(d.get(field) or "0") for d in records), Decimal(0))

        fees = value("commission") + value("swap") + value("fee")
        pnl = value("profit") + fees
        result.append(
            {
                "position_id": position,
                "symbol": entries[0]["symbol"],
                "side": "buy" if entries[0]["type"] == "0" else "sell",
                "volume": str(in_volume),
                "entry_price": entries[0]["price"],
                "exit_price": exits[-1]["price"],
                "opened_at": datetime.fromtimestamp(int(entries[0]["time"]), UTC).isoformat(),
                "closed_at": datetime.fromtimestamp(int(exits[-1]["time"]), UTC).isoformat(),
                "net_pnl": str(pnl),
                "costs": str(fees),
                "source": attribution,
                "origin": origin,
                "deal_ids": [d["ticket"] for d in items],
                "status": "closed",
            }
        )
    return sorted(result, key=lambda item: item["closed_at"], reverse=True)


def metrics(trades: list[dict]) -> dict:
    values = [Decimal(t["net_pnl"]) for t in trades]
    wins, losses = [v for v in values if v > 0], [v for v in values if v < 0]
    winning, losing = sum(wins, Decimal(0)), sum(losses, Decimal(0))
    return {
        "net_realized_pnl": str(sum(values, Decimal(0))),
        "winning_pnl": str(winning),
        "losing_pnl": str(losing),
        "closed_trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(values) - len(wins) - len(losses),
        "win_rate": len(wins) / len(values) * 100 if values else None,
        "profit_factor": float(winning / abs(losing)) if losses else None,
        "costs": str(sum((Decimal(t.get("costs") or "0") for t in trades), Decimal(0))),
        "average_win": str(winning / len(wins)) if wins else None,
        "average_loss": str(losing / len(losses)) if losses else None,
    }


class Reporter:
    def __init__(self, service: Any) -> None:
        self.service, self.db = service, service.database
        self._history_at: datetime | None = None
        self._equity_at: datetime | None = None
        self._diagnostics_at: datetime | None = None

    def collect(self) -> None:
        timestamp = now()
        account_id = self.service.settings.account_id
        if self._diagnostics_at is None or (timestamp - self._diagnostics_at).total_seconds() >= 60:
            from trade_agent.research_diagnostics import collect as collect_research_diagnostics

            put_cache(
                self.db,
                f"research-diagnostics:{account_id}",
                collect_research_diagnostics(self.db, account_id, as_of=timestamp),
            )
            self._diagnostics_at = timestamp
        state = self.service.broker_state()
        put_cache(
            self.db,
            "broker",
            {
                **state,
                "broker": "Vantage"
                if self.service.settings.broker_mode == "mt5_http"
                else "Fake broker",
                "mode": self.service.settings.broker_mode,
                "observed_at": timestamp.isoformat(),
                "assisted_demo_accepted": self.service.settings.assisted_demo_accepted,
            },
        )
        if self._equity_at is None or (timestamp - self._equity_at).total_seconds() >= 60:
            with self.db.session() as session:
                session.add(
                    EquityRecord(
                        account_id=account_id, observed_at=timestamp, payload=state["account"]
                    )
                )
            self._equity_at = timestamp
        if self._history_at is None or (timestamp - self._history_at).total_seconds() >= 60:
            with self.db.session() as session:
                old = session.get(DashboardCacheRecord, "history")
                coverage = old.payload.get("since") if old else None
                prior_until = old.payload.get("until") if old else None
                # Reimport the complete coverage once when introducing UTC normalization.
                if old and old.payload.get("clock_version") != 1:
                    prior_until = None
            start = (
                datetime.fromisoformat(prior_until) - timedelta(days=2)
                if prior_until
                else timestamp - timedelta(days=30)
            )
            history = self.service.broker.history(account_id, start, timestamp)
            with self.db.session() as session:
                for deal in history["deals"]:
                    key = f"{account_id}:{deal['ticket']}"
                    row = session.get(BrokerDealRecord, key)
                    if row is None:
                        session.add(
                            BrokerDealRecord(
                                id=key,
                                account_id=account_id,
                                position_id=str(deal.get("position_id", "0")),
                                occurred_at=datetime.fromtimestamp(int(deal["time"]), UTC),
                                payload=deal,
                            )
                        )
                    else:
                        row.payload = deal
                        row.occurred_at = datetime.fromtimestamp(int(deal["time"]), UTC)
            put_cache(
                self.db,
                "history",
                {
                    "since": coverage or start.isoformat(),
                    "until": timestamp.isoformat(),
                    "complete": True,
                    "clock_version": 1,
                    "clock_offset_seconds": history.get("clock_offset_seconds", 0),
                    "note": "30-day initial import; only balanced closed lifecycles enter win-rate statistics. Unattributed adjustments are reported separately.",
                },
            )
            self._history_at = timestamp
        if (
            self.service.settings.assisted_demo_accepted
            and self.service.settings.broker_mode != "fake"
        ):
            from trade_agent.domain import AccountSnapshot
            from trade_agent.limits import check_demo_budget
            from trade_agent.risk import RiskRejected

            try:
                check_demo_budget(self.service, AccountSnapshot.model_validate(state["account"]))
                put_cache(self.db, "limits", {"status": "ok"})
            except RiskRejected as exc:
                put_cache(self.db, "limits", {"status": "blocked", "reason": str(exc)})


def main() -> None:
    import logging
    import time

    from trade_agent.logging_config import configure_logging
    from trade_agent.main import build_runtime

    facade, _ = build_runtime()
    configure_logging(facade.settings)
    reporter = Reporter(facade.service)
    while True:
        try:
            reporter.collect()
        except Exception:
            logging.getLogger(__name__).exception(
                "Reporting collection failed; prior snapshot retained with its original timestamp"
            )
        time.sleep(5)


def dashboard_data(db: Database, period: str = "7d", scope: str = "agent") -> dict:
    from trade_agent.autonomy_health import data_health
    from trade_agent.monitoring import worker_status

    timestamp = now()
    since = (
        timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "today"
        else timestamp - timedelta(days={"7d": 7, "30d": 30, "all": 36500}[period])
    )
    with db.session() as session:
        cached = session.get(DashboardCacheRecord, "broker")
        broker = cached.payload if cached else None
        history = session.get(DashboardCacheRecord, "history")
        proposals = session.scalars(
            select(ProposalRecord)
            .options(
                load_only(
                    ProposalRecord.id,
                    ProposalRecord.status,
                    ProposalRecord.created_at,
                    ProposalRecord.instruction,
                    ProposalRecord.origin,
                    ProposalRecord.strategy_version,
                    ProposalRecord.decision_run_id,
                )
            )
            .order_by(ProposalRecord.created_at.desc())
            .limit(200)
        ).all()
        intents = session.scalars(
            select(ExecutionIntentRecord).options(
                load_only(
                    ExecutionIntentRecord.id,
                    ExecutionIntentRecord.status,
                    ExecutionIntentRecord.proposal_id,
                    ExecutionIntentRecord.broker_position_id,
                    ExecutionIntentRecord.broker_order_id,
                )
            )
        ).all()
        events = session.scalars(
            select(LedgerEventRecord)
            .options(
                load_only(
                    LedgerEventRecord.id,
                    LedgerEventRecord.proposal_id,
                    LedgerEventRecord.kind,
                    LedgerEventRecord.created_at,
                )
            )
            .order_by(LedgerEventRecord.id.desc())
            .limit(200)
        ).all()
        activities = session.scalars(
            select(AgentActivityRecord).order_by(AgentActivityRecord.id.desc()).limit(100)
        ).all()
        account_id = broker["account"]["account_id"] if broker else ""
        diagnostic_cache = session.get(DashboardCacheRecord, f"research-diagnostics:{account_id}")
        screen_cache = session.get(DashboardCacheRecord, f"research-screen:{account_id}")
        deals = [
            row.payload
            for row in session.scalars(
                select(BrokerDealRecord).where(BrokerDealRecord.account_id == account_id)
            )
        ]
        equity = session.scalars(
            select(EquityRecord)
            .where(EquityRecord.account_id == account_id, EquityRecord.observed_at >= since)
            .order_by(EquityRecord.observed_at)
        ).all()
        checkpoints = {
            row.key: {**row.payload, "observed_at": aware(row.updated_at).isoformat()}
            for row in session.scalars(
                select(DashboardCacheRecord).where(
                    DashboardCacheRecord.key.in_(
                        ["telegram", "protection", "model", "dashboard_url", "limits", "autonomy"]
                    )
                )
            )
        }
        managed = {str(row.broker_position_id) for row in intents if row.broker_position_id}
        # Fetch attribution independently of the recent-proposal display limit.
        proposal_origins = {
            proposal_id: origin
            for proposal_id, origin in session.execute(
                select(ProposalRecord.id, ProposalRecord.origin)
            ).all()
        }
        origins: dict[str, set[str]] = defaultdict(set)
        for intent in intents:
            if intent.broker_position_id:
                origins[str(intent.broker_position_id)].add(
                    proposal_origins.get(intent.proposal_id, "unknown")
                )
        if broker:
            order_origins: dict[str, set[str]] = defaultdict(set)
            for intent in intents:
                if intent.broker_order_id:
                    order_origins[str(intent.broker_order_id)].add(
                        proposal_origins.get(intent.proposal_id, "unknown")
                    )

            def identified(rows: list[dict], key: str, mapping: dict[str, set[str]]) -> list[dict]:
                result = []
                for row in rows:
                    choices = mapping.get(str(row[key]), set())
                    origin = (
                        next(iter(choices))
                        if len(choices) == 1
                        else "unknown"
                        if choices
                        else "external"
                    )
                    result.append({**row, "origin": origin})
                return result

            broker = {
                **broker,
                "state": {
                    **broker["state"],
                    "positions": identified(broker["state"]["positions"], "position_id", origins),
                    "orders": identified(broker["state"]["orders"], "order_id", order_origins),
                },
            }
        runs = session.scalars(
            select(AutonomousRunRecord)
            .where(
                AutonomousRunRecord.account_id == account_id,
                AutonomousRunRecord.snapshot["data_status"].as_string().is_(None),
            )
            .order_by(AutonomousRunRecord.created_at.desc())
            .limit(50)
        ).all()
        open_ids = (
            {str(p["position_id"]) for p in broker["state"]["positions"]} if broker else set()
        )
        all_closed = closed_trades(deals, managed, open_ids, origins)
        selected = [
            t
            for t in all_closed
            if datetime.fromisoformat(t["closed_at"]) >= since
            and (
                scope == "account"
                or (scope == "agent" and t["source"] == "agent")
                or t["origin"] == scope
            )
        ]
        cash = [d for d in deals if d.get("type") not in {"0", "1"}]
        points = []
        high, drawdown = None, Decimal(0)
        for row in equity:
            observed = aware(row.observed_at)
            # Remove broker balance/credit transfers from the sampled equity change.
            flows = sum(
                (
                    Decimal(d.get("profit") or "0")
                    for d in cash
                    if d.get("type") in {"2", "3"} and int(d["time"]) <= observed.timestamp()
                ),
                Decimal(0),
            )
            adjusted = Decimal(row.payload["equity"]) - flows
            high = max(high, adjusted) if high is not None else adjusted
            drawdown = max(drawdown, high - adjusted)
            points.append(
                {
                    "time": observed.isoformat(),
                    "equity": row.payload["equity"],
                    "adjusted_equity": str(adjusted),
                }
            )
        outcomes = {
            status: sum(i.status == status for i in intents)
            for status in ("prepared", "unknown", "rejected", "accepted", "closed", "cancelled")
        }
        agent_counts: dict[str, int] = {
            kind: count
            for kind, count in session.execute(
                select(AgentActivityRecord.kind, func.count(AgentActivityRecord.id)).group_by(
                    AgentActivityRecord.kind
                )
            ).all()
        }
        return {
            "generated_at": timestamp.isoformat(),
            "autonomy_comparison_preview": (
                session.get(
                    DashboardCacheRecord, f"autonomy-comparison-preview:{account_id}"
                ).payload
                if session.get(DashboardCacheRecord, f"autonomy-comparison-preview:{account_id}")
                else None
            ),
            "broker": broker,
            "period": period,
            "scope": scope,
            "reporting_timezone": "UTC",
            "metrics": metrics(selected),
            "origin_metrics": {
                origin: metrics(
                    [
                        t
                        for t in all_closed
                        if t["origin"] == origin and datetime.fromisoformat(t["closed_at"]) >= since
                    ]
                )
                for origin in ("manual", "autonomous", "external", "unknown")
            },
            "closed_trades": selected[:200],
            "equity": points[:: max(1, len(points) // 500)]
            + ([points[-1]] if len(points) > 500 else []),
            "drawdown": str(drawdown) if len(points) > 1 else None,
            "drawdown_scope": "Account-wide, sampled since first observation in selected period; cash-flow adjusted, not historical intraday maximum",
            "coverage": {
                **(history.payload if history else {"complete": False}),
                "equity_since": points[0]["time"] if points else None,
                "unclassified_cash_events": len(cash),
                "unattributed_adjustments": str(
                    sum(
                        (
                            Decimal(d.get("profit") or "0")
                            for d in cash
                            if d.get("type") not in {"2", "3"}
                        ),
                        Decimal(0),
                    )
                ),
            },
            "proposals": [
                {
                    "id": p.id,
                    "status": p.status,
                    "created_at": aware(p.created_at).isoformat(),
                    "instruction": {
                        k: v
                        for k, v in p.instruction.items()
                        if k
                        in {"symbol", "side", "volume", "entry_price", "stop_loss", "take_profit"}
                    },
                    "source": "agent",
                    "origin": p.origin,
                    "strategy_version": p.strategy_version,
                    "decision_run_id": p.decision_run_id,
                }
                for p in proposals
            ],
            "events": [
                {
                    "id": e.id,
                    "proposal_id": e.proposal_id,
                    "kind": e.kind,
                    "time": aware(e.created_at).isoformat(),
                }
                for e in events
            ],
            "activity": [
                {
                    "id": a.id,
                    "kind": a.kind,
                    "summary": a.summary,
                    "latency_ms": a.latency_ms,
                    "time": aware(a.created_at).isoformat(),
                }
                for a in activities
            ],
            "execution": outcomes,
            "autonomy_runs": [
                {
                    "id": r.id,
                    "symbol": r.symbol,
                    "timeframe": r.timeframe,
                    "bar_at": aware(r.bar_at).isoformat(),
                    "mode": r.mode,
                    "status": r.status,
                    "strategy_version": r.strategy_version,
                    "decision": r.decision,
                    "reports": r.reports,
                    "snapshot": {
                        **{
                            key: value
                            for key, value in r.snapshot.items()
                            if key not in {"bars", "higher_timeframe_bars", "comparison_bars"}
                        },
                        "archived_bar_count": len(r.snapshot.get("bars", [])),
                    },
                    "proposal_id": r.proposal_id,
                    "error": scrub(r.error) if r.error else None,
                    "created_at": aware(r.created_at).isoformat(),
                }
                for r in runs
            ],
            "agent_counts": agent_counts,
            "services": checkpoints,
            "autonomy_worker": worker_status(db, account_id),
            "autonomy_market_data": data_health(db, account_id),
            "research_diagnostics": diagnostic_cache.payload if diagnostic_cache else None,
            "research_screen": screen_cache.payload if screen_cache else None,
            "instruction_accuracy": "Not evaluated",
            "prediction_accuracy": (
                "Not evaluated; use attributed realized outcomes and replay benchmarks"
                if runs
                else "Not available in assisted mode"
            ),
        }


if __name__ == "__main__":
    main()
