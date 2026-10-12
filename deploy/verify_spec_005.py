"""Read-only gate inspection and approval-routing check. No order or policy mutations."""

import asyncio
import json

from trade_agent.app import TelegramApplicationFacade
from trade_agent.db import SystemStateRecord
from trade_agent.main import build_runtime


async def main():
    app, _ = build_runtime()
    facade = TelegramApplicationFacade(app)
    account = app.service.broker.account(app.settings.account_id)
    state = app.service.broker.state(app.settings.account_id)
    with app.service.database.session() as session:
        record = session.get(SystemStateRecord, f"demo_budget:{app.settings.account_id}")
        budget = json.loads(record.value) if record else {}
    print(
        json.dumps(
            {
                "demo": account.is_demo,
                "equity": str(account.equity),
                "positions": len(state.positions),
                "orders": len(state.orders),
                "paused": app.service._paused(),
                "budget": budget,
            }
        )
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Approval follow-up must not recalculate or mutate broker state")

    for name in ("quote", "profit_target", "submit", "close", "modify", "cancel"):
        setattr(app.service.broker, name, forbidden)
    try:
        reply = await facade.create_trade_from_text(
            owner_id=app.settings.telegram_owner_id, text="okay open this trade"
        )
        assert reply.confirmation_token is None
        print(reply.text)
        print("Read-only approval routing passed; zero broker mutations")
    finally:
        await facade.aclose()


if __name__ == "__main__":
    asyncio.run(main())
