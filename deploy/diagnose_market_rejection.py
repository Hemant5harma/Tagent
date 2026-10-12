"""Read-only recent proposal diagnostics; no credentials or broker mutation."""

import json

from sqlalchemy import select

from trade_agent.db import ProposalRecord
from trade_agent.main import build_runtime


def main():
    app, _ = build_runtime()
    with app.service.database.session() as session:
        rows = session.scalars(
            select(ProposalRecord)
            .where(ProposalRecord.account_id == app.settings.account_id)
            .order_by(ProposalRecord.created_at.desc())
            .limit(5)
        ).all()
        for row in rows:
            details = row.instruction
            print(json.dumps({
                "status": row.status,
                "created_at": row.created_at.isoformat(),
                "side": details.get("side"),
                "symbol": details.get("symbol"),
                "volume": details.get("volume"),
                "sl": details.get("stop_loss"),
                "tp": details.get("take_profit"),
                "quote_at_proposal": {k: row.validation.get(k) for k in ("bid", "ask", "estimated_loss")},
                "updated_at": row.updated_at.isoformat(),
            }))


if __name__ == "__main__":
    main()
