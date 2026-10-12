"""Data availability observations are distinct from market research decisions."""

from datetime import UTC, datetime

from sqlalchemy import select

from trade_agent.db import DashboardCacheRecord


def record_data_health(db, account_id, symbol, *, available, code=None, quote_at=None):
    stamp = datetime.now(UTC).isoformat()
    key = f"autonomy-data:{account_id}:{symbol}"
    with db.session() as session:
        row = session.get(DashboardCacheRecord, key)
        previous = row.payload if row else {}
        payload = {
            "account_id": account_id,
            "symbol": symbol,
            "status": "available" if available else "unavailable",
            "code": code,
            "last_checked_at": stamp,
            "quote_at": quote_at,
            "last_fresh_at": stamp if available else previous.get("last_fresh_at"),
            "unavailable_since": None if available else previous.get("unavailable_since") or stamp,
            "unavailable_checks": int(previous.get("unavailable_checks") or 0)
            + (0 if available else 1),
            "consecutive_unavailable_checks": 0
            if available
            else int(previous.get("consecutive_unavailable_checks") or 0) + 1,
            "last_unavailable_at": previous.get("last_unavailable_at") if available else stamp,
            "reason": (
                "Fresh market data available"
                if available
                else "No fresh quote. The market may be closed or the feed may be stalled; research and new entries are suspended."
                if code == "stale_quote"
                else "Market data is unavailable or invalid; research and new entries are suspended."
            ),
        }
        if row is None:
            session.add(
                DashboardCacheRecord(key=key, payload=payload, updated_at=datetime.now(UTC))
            )
        else:
            row.payload, row.updated_at = payload, datetime.now(UTC)
    return payload


def data_health(db, account_id):
    with db.session() as session:
        rows = session.scalars(
            select(DashboardCacheRecord).where(
                DashboardCacheRecord.key.startswith(f"autonomy-data:{account_id}:")
            )
        ).all()
        return [row.payload for row in rows if row.payload.get("account_id") == account_id]
