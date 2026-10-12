"""Bounded, read-only diagnostic summary of recorded shadow decisions.

Counts describe why a setup did or did not advance. They are not a forecast,
win rate, or evidence that relaxing a blocker would improve returns.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from trade_agent.db import AutonomousRunRecord, Database

MAX_RUNS = 5000
WINDOW = timedelta(days=30)


def summarize(rows: list[tuple], *, as_of: datetime, truncated: bool) -> dict:
    statuses: Counter[str] = Counter()
    symbols: dict[str, Counter[str]] = defaultdict(Counter)
    blockers: Counter[str] = Counter()
    gates: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    ready_runs = 0
    planned_runs = 0
    tied_closest_runs = 0
    model_observed = 0
    recent_24h_runs = 0
    last_decision_at = None
    for status, symbol, decision, created_at in rows:
        status, symbol = str(status), str(symbol)
        detail = decision if isinstance(decision, dict) else {}
        statuses[status] += 1
        symbols[symbol][status] += 1
        stamp = (
            created_at.replace(tzinfo=UTC)
            if created_at.tzinfo is None
            else created_at.astimezone(UTC)
        )
        last_decision_at = max(last_decision_at, stamp) if last_decision_at else stamp
        if stamp >= as_of - timedelta(hours=24):
            recent_24h_runs += 1
        gate = detail.get("execution_gate")
        if isinstance(gate, str) and gate:
            gates[gate[:160]] += 1
        plans = (
            detail.get("trade_plans", {}).get("plans", [])
            if isinstance(detail.get("trade_plans"), dict)
            else []
        )
        if isinstance(plans, list):
            valid_plans = [plan for plan in plans if isinstance(plan, dict)]
            if valid_plans:
                planned_runs += 1
            if any(plan.get("status") == "ready" for plan in valid_plans):
                ready_runs += 1
            selected = [plan for plan in valid_plans if plan.get("side") == detail.get("direction")]
            if not selected and valid_plans:
                fewest = min(len(plan.get("blockers") or []) for plan in valid_plans)
                selected = [
                    plan for plan in valid_plans if len(plan.get("blockers") or []) == fewest
                ]
            if len(selected) > 1:
                tied_closest_runs += 1
            if selected:
                reasons = [
                    {
                        str(reason)[:160]
                        for reason in plan.get("blockers", [])
                        if isinstance(reason, str)
                    }
                    for plan in selected
                ]
                blockers.update(set.intersection(*reasons))
        health = detail.get("research_health")
        if isinstance(health, dict) and health:
            model_observed += 1
            for role, result in health.items():
                if isinstance(result, dict) and result.get("status") == "failed":
                    failures[
                        f"{str(role)[:32]}: {str(result.get('error_type') or 'unknown')[:64]}"
                    ] += 1
    return {
        "as_of": as_of.astimezone(UTC).isoformat(),
        "window_start": (as_of - WINDOW).astimezone(UTC).isoformat(),
        "window_days": 30,
        "observed_runs": len(rows),
        "recent_24h_runs": recent_24h_runs,
        "truncated": truncated,
        "last_decision_at": last_decision_at.isoformat() if last_decision_at else None,
        "status_counts": dict(statuses),
        "by_symbol": {symbol: dict(counts) for symbol, counts in sorted(symbols.items())},
        "plan_ready_runs": ready_runs,
        "planned_runs": planned_runs,
        "tied_closest_runs": tied_closest_runs,
        "model_health_observed_runs": model_observed,
        "top_blockers": [
            {"reason": reason, "runs": count} for reason, count in blockers.most_common(8)
        ],
        "top_gates": [{"reason": reason, "runs": count} for reason, count in gates.most_common(6)],
        "model_failures": [
            {"reason": reason, "occurrences": count} for reason, count in failures.most_common(6)
        ],
        "note": "Counts are recorded decisions, not trades or predictive accuracy. Blockers come from the model-selected side, otherwise the closest-to-ready side; tied sides contribute only shared blockers. Relaxing a rule is not a proven improvement.",
    }


def collect(db: Database, account_id: str, *, as_of: datetime | None = None) -> dict:
    stamp = as_of or datetime.now(UTC)
    if stamp.tzinfo is None:
        raise ValueError("diagnostic cutoff must be timezone-aware")
    with db.session() as session:
        rows = session.execute(
            select(
                AutonomousRunRecord.status,
                AutonomousRunRecord.symbol,
                AutonomousRunRecord.decision,
                AutonomousRunRecord.created_at,
            )
            .where(
                AutonomousRunRecord.account_id == account_id,
                AutonomousRunRecord.created_at >= stamp - WINDOW,
                AutonomousRunRecord.snapshot["data_status"].as_string().is_(None),
            )
            .order_by(AutonomousRunRecord.created_at.desc())
            .limit(MAX_RUNS + 1)
        ).all()
    return summarize(rows[:MAX_RUNS], as_of=stamp, truncated=len(rows) > MAX_RUNS)
