"""Read-only check of the owner's latest submitted trade on the configured broker."""

import json

from trade_agent.main import build_runtime


def main() -> None:
    app, _ = build_runtime()
    settings = app.settings
    state = app.service.broker.state(settings.account_id)
    print(
        json.dumps(
            {
                "account_id": settings.account_id,
                "positions": [
                    {
                        "id": p.position_id,
                        "symbol": p.symbol,
                        "side": p.side.value,
                        "volume": str(p.volume),
                        "stop_loss": str(p.stop_loss) if p.stop_loss is not None else None,
                        "take_profit": str(p.take_profit) if p.take_profit is not None else None,
                        "profit": str(p.profit),
                    }
                    for p in state.positions
                ],
                "pending_order_count": len(state.orders),
            }
        )
    )
    print(app.trade_detail_text(settings.telegram_owner_id, "tell me about this order"))


if __name__ == "__main__":
    main()
