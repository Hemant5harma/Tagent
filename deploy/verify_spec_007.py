"""Connected broker order_check only. Never submit or change risk state."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_agent.ai import ParsedEntry, TradeProposalParse
from trade_agent.app import TelegramApplicationFacade
from trade_agent.domain import OrderType
from trade_agent.main import build_runtime


async def main():
    app, _ = build_runtime()
    broker = app.service.broker
    assert app.service._paused(), "Do not change activation state"
    assert app.settings.max_open_positions == 1, "Do not deploy unapproved activation changes"
    account = broker.account(app.settings.account_id)
    assert account.is_demo

    def forbidden(*args, **kwargs):
        raise AssertionError("Broker mutations are not permitted")

    for name in ("submit", "modify", "close", "cancel"):
        setattr(broker, name, forbidden)
    quote = broker.quote("XAUUSD")
    parsed = TradeProposalParse(
        intent="trade",
        symbol="XAUUSD",
        side="sell",
        volume=Decimal(".05"),
        entry=ParsedEntry(kind="price", price=quote.bid + Decimal(10)),
        profit_target_usd=Decimal(10),
        requested_order_type="sell_limit",
        pending_expires_at=(datetime.now(UTC) + timedelta(hours=2)).isoformat(),
    )
    facade = TelegramApplicationFacade(app)
    try:
        instruction = facade._instruction_from_parse(
            app.settings.telegram_owner_id, parsed.model_dump()
        )
        assert instruction.deadline_at is None
        result = broker.preflight(instruction, OrderType.SELL_LIMIT)
        print(
            json.dumps(
                {
                    "pending_preflight_accepted": result.accepted,
                    "reason": result.reason,
                    "hedging_account": account.hedging,
                    "expiry": instruction.pending_expires_at.isoformat(),
                    "entry": str(instruction.entry_price),
                    "sl": str(instruction.stop_loss),
                    "tp": str(instruction.take_profit),
                    "entries_paused": app.service._paused(),
                    "broker_mutations": 0,
                }
            )
        )
        assert result.accepted, "Pending preflight not accepted; do not claim execution readiness"
    finally:
        await facade.aclose()


if __name__ == "__main__":
    asyncio.run(main())
