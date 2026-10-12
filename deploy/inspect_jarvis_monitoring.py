"""Read-only Jarvis readiness inventory; never print credentials or submit orders."""

import json

from trade_agent.config import Settings
from trade_agent.db import DashboardCacheRecord, Database
from trade_agent.main import build_runtime


def main() -> None:
    settings = Settings()
    facade, _ = build_runtime(settings)
    account = facade.service.broker.account(settings.account_id)
    state = facade.service.broker.state(settings.account_id)
    db = Database(settings.database_url)
    with db.session() as session:
        cached = session.get(DashboardCacheRecord, "dashboard_url")
        url = cached.payload.get("url") if cached else settings.dashboard_url
    print(
        json.dumps(
            {
                "demo_account": account.is_demo,
                "currency": account.currency,
                "trading_allowed": account.trade_allowed,
                "open_positions": len(state.positions),
                "pending_orders": len(state.orders),
                "entries_paused": facade.service._paused(),
                "provider": settings.ai_provider,
                "model": settings.openai_model,
                "symbols": settings.allowed_symbols,
                "postgres": settings.database_url.startswith("postgresql"),
                "telegram_configured": bool(settings.telegram_bot_token),
                "dashboard_url": url,
                "autonomy_mode": getattr(settings, "autonomy_mode", "not installed"),
                "read_only": True,
            }
        )
    )


if __name__ == "__main__":
    main()
