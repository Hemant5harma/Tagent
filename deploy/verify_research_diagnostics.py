"""Read-only verification of the research caches and service-level summary."""

import json

from trade_agent.config import Settings
from trade_agent.db import DashboardCacheRecord, Database


def main() -> None:
    settings = Settings()
    with Database(settings.database_url).session() as session:
        diagnostics = session.get(
            DashboardCacheRecord, f"research-diagnostics:{settings.account_id}"
        )
        screen = session.get(DashboardCacheRecord, f"research-screen:{settings.account_id}")
        if diagnostics is None:
            raise RuntimeError("reporting worker has not published diagnostics")
        payload = diagnostics.payload
        result = {
            "updated_at": diagnostics.updated_at.isoformat(),
            "observed_runs": payload.get("observed_runs"),
            "recent_24h_runs": payload.get("recent_24h_runs"),
            "plan_ready_runs": payload.get("plan_ready_runs"),
            "top_blockers": payload.get("top_blockers", [])[:3],
            "screen_published": screen is not None,
            "screen_decision": screen.payload.get("decision") if screen else None,
        }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
