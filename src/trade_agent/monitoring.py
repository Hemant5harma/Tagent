"""Read-only monitoring shared by owner interfaces."""

from datetime import UTC, datetime

from trade_agent.db import DashboardCacheRecord, Database
from trade_agent.reporting import dashboard_data, scrub


def worker_status(db: Database, account_id: str) -> dict:
    with db.session() as session:
        row = session.get(DashboardCacheRecord, "autonomy")
        if row is None or row.payload.get("account_id") != account_id:
            return {"state": "not_observed", "observed_at": None}
        stamp = row.updated_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - stamp).total_seconds()
        state = row.payload.get("status", "unknown")
        if age < -5 or age > 35:
            state = "stale"
        return {**row.payload, "state": state, "observed_at": stamp.isoformat()}


def performance_text(db: Database, scope: str) -> str:
    if scope not in {"manual", "autonomous", "all"}:
        return "Usage: /performance manual|autonomous|all"
    data = dashboard_data(db, period="7d", scope="agent")
    lines = ["Realized performance · last 7 days (UTC)"]
    for origin in ("manual", "autonomous") if scope == "all" else (scope,):
        item = data["origin_metrics"][origin]
        rate = item["win_rate"]
        lines.append(
            f"{origin.capitalize()}: {item['closed_trades']} closed trades · "
            f"net P/L {item['net_realized_pnl']} · costs {item['costs']} · "
            f"win rate {rate:.1f}%"
            if rate is not None
            else f"{origin.capitalize()}: {item['closed_trades']} closed trades · "
            f"net P/L {item['net_realized_pnl']} · costs {item['costs']} · win rate unavailable"
        )
    broker = data.get("broker") or {}
    positions = (broker.get("state") or {}).get("positions", [])
    lines.append(
        "Open positions: "
        + " · ".join(
            f"{origin} {sum(p.get('origin') == origin for p in positions)}"
            for origin in ("manual", "autonomous", "external", "unknown")
        )
    )
    lines.append(f"Currency: {(broker.get('account') or {}).get('currency', 'not observed')}")
    lines.append(f"Broker snapshot: {broker.get('observed_at', 'not observed')}")
    lines.append(
        f"History coverage: {'complete' if data['coverage'].get('complete') else 'incomplete'}"
    )
    lines.append("Equity/drawdown are account-wide. External/unknown origins are excluded.")
    return "\n".join(lines)


def decisions_text(runs: list[dict]) -> str:
    if not runs:
        return "No autonomous research runs recorded for this account."
    lines = ["Latest autonomous decisions"]
    for run in runs[:5]:
        decision = run.get("decision") or {}
        reason = scrub(str(decision.get("reason") or run.get("error") or "No reason recorded"))[
            :250
        ]
        lines.append(
            f"{run['symbol']} {run['timeframe']} · {run['mode']} · {run['status']}\n"
            f"Bar {run['bar_at']} · {decision.get('direction', 'unavailable')}\n{reason}"
        )
    return "\n\n".join(lines)


def diagnostics_text(db: Database, account_id: str) -> str:
    """Owner-facing, read-only explanation of the recent research funnel."""
    from trade_agent.research_diagnostics import collect

    report = collect(db, account_id)
    statuses = report["status_counts"]
    lines = [
        "Shadow research diagnosis · last 30 days (UTC)",
        f"Recorded decisions: {report['observed_runs']}"
        + (" (most recent 5,000 only)" if report["truncated"] else ""),
        f"Last decision: {report['last_decision_at'] or 'none'} · past 24h: {report['recent_24h_runs']}",
        f"WAIT: {statuses.get('wait', 0)} · research failed: {statuses.get('research_failed', 0)} · ready plans: {report['plan_ready_runs']}",
    ]
    if report["top_blockers"]:
        lines.append("Closest-plan blockers (runs; reasons can overlap):")
        lines.extend(
            f"• {item['runs']} — {scrub(item['reason'])}" for item in report["top_blockers"][:4]
        )
    if report["model_failures"]:
        lines.append("Model failures:")
        lines.extend(
            f"• {item['occurrences']} — {scrub(item['reason'])}"
            for item in report["model_failures"][:3]
        )
    with db.session() as session:
        row = session.get(DashboardCacheRecord, f"research-screen:{account_id}")
        study = row.payload if row else None
    if study and study.get("scenarios"):
        first = study["scenarios"][0]
        lines.append(
            f"Offline {study.get('symbol', 'unknown')} comparator: "
            f"{first['all']['trades']} simulated trades, net {first['all']['net_pnl']} USD; "
            f"{study['decision']}. Bar-level screening only, not broker-realized profit."
        )
    lines.append(
        "These counts explain the current research record; relaxing a blocker is not proof of an edge. No trade was placed by this command."
    )
    return "\n".join(lines)
