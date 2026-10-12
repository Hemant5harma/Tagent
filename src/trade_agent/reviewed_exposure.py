"""Explicit operator-reviewed external IDs, separate from trade attribution."""

import json

from trade_agent.db import Database, SystemStateRecord


def reviewed_references(database: Database, account_id: str) -> set[str]:
    with database.session() as session:
        row = session.get(SystemStateRecord, f"reviewed_external_exposure:{account_id}")
        if row is None:
            return set()
        try:
            values = json.loads(row.value)["references"]
        except (ValueError, KeyError, TypeError):
            return set()
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            return set()
        return set(values)
