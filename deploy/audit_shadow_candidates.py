"""Read-only, aggregate shadow trade-plan diagnostics without prompts or secrets."""

import json
from collections import Counter
from datetime import UTC

from sqlalchemy import select

from trade_agent.db import AutonomousRunRecord
from trade_agent.main import build_runtime


def main() -> None:
    app, _ = build_runtime()
    with app.service.database.session() as session:
        rows = session.scalars(
            select(AutonomousRunRecord).where(
                AutonomousRunRecord.account_id == app.settings.account_id,
                AutonomousRunRecord.mode == "shadow",
            )
        ).all()
    statuses = Counter()
    directions = Counter()
    plan_status = Counter()
    blockers = Counter()
    symbols = Counter()
    earliest = latest = None
    for row in rows:
        stamp = row.created_at.replace(tzinfo=UTC)
        earliest = min(earliest, stamp) if earliest else stamp
        latest = max(latest, stamp) if latest else stamp
        symbols[row.symbol] += 1
        statuses[row.status] += 1
        directions[str((row.decision or {}).get("direction", "none"))] += 1
        for plan in (row.snapshot or {}).get("trade_plans", {}).get("plans", []):
            plan_status[(row.symbol, plan.get("side"), plan.get("status"))] += 1
            for blocker in plan.get("blockers", []):
                blockers[(row.symbol, blocker)] += 1
    print(
        json.dumps(
            {
                "runs": len(rows),
                "first_at": earliest.isoformat() if earliest else None,
                "last_at": latest.isoformat() if latest else None,
                "symbols": dict(symbols),
                "run_statuses": dict(statuses),
                "directions": dict(directions),
                "plan_statuses": [
                    {"symbol": a, "side": b, "status": c, "count": n}
                    for (a, b, c), n in plan_status.items()
                ],
                "top_blockers": [
                    {"symbol": a, "reason": b, "count": n} for (a, b), n in blockers.most_common(12)
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
