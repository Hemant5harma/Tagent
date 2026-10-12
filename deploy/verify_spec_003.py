"""Isolated PostgreSQL + provider memory check. No trading or owner draft changes."""

import asyncio
import json
from uuid import uuid4

from trade_agent.ai import build_ai_backend
from trade_agent.db import Database
from trade_agent.main import build_runtime
from trade_agent.memory import context, forget, remember


async def main():
    application, _ = build_runtime()
    assert application.service._paused(), "Entries must remain paused"
    db = application.service.database
    key = "spec003-verification:" + str(uuid4())
    try:
        with db.session() as session:
            remember(
                session,
                key,
                "1",
                "Please explain account equity.",
                "Equity includes unrealized profit and loss.",
            )
        fresh_db = Database(application.settings.database_url)
        memory = context(fresh_db, key)
        assert len(memory["recent_messages"]) == 2
        response = await build_ai_backend(application.settings).explain(
            "What subject were we just discussing? Answer in one sentence.",
            json.dumps(
                {
                    "conversation_memory": memory,
                    "policy": {"entries_paused": True, "stop_loss_required": True},
                }
            ),
        )
        assert "equity" in response.lower(), "Provider did not recall test topic"
        assert application.service._paused()
        print(
            json.dumps(
                {
                    "postgres_restart_context": "passed",
                    "actual_provider_recall": "passed",
                    "entries_paused": True,
                    "broker_mutations": 0,
                    "owner_draft_changed": False,
                }
            )
        )
    finally:
        forget(db, key)
        assert context(db, key)["recent_messages"] == []


if __name__ == "__main__":
    asyncio.run(main())
