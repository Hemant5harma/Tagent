"""Read-only actual XAUUSD calculation, plus explicit opt-in deployment settings."""

import asyncio
import json
import shutil
import sys
from pathlib import Path

from trade_agent.app import TelegramApplicationFacade
from trade_agent.db import SystemStateRecord
from trade_agent.draft_extract import explicit_draft
from trade_agent.main import build_runtime


async def main():
    app, _ = build_runtime()
    assert app.service._paused(), "Keep entries paused"
    assert app.settings.trading_mode == "demo"
    # Read-only verification first; do not touch the owner's chat checkpoint.
    app.settings.allowed_symbols = list(dict.fromkeys([*app.settings.allowed_symbols, "XAUUSD"]))
    app.settings.auto_demo_protection = True
    broker = app.service.broker

    def forbidden(*args, **kwargs):
        raise AssertionError("No trading mutations authorized by this verification")

    for method in ("submit", "modify", "cancel", "close"):
        setattr(broker, method, forbidden)
    facade = TelegramApplicationFacade(app)
    try:
        parsed = explicit_draft("open a sell trade XAUUSD lot 0.05 take $10 profit")
        instruction = facade._instruction_from_parse(
            app.settings.telegram_owner_id, parsed.model_dump()
        )
        assert instruction.symbol == "XAUUSD" and instruction.stop_loss > instruction.take_profit
        state = broker.state(app.settings.account_id)
        with app.service.database.session() as session:
            row = session.get(SystemStateRecord, f"demo_budget:{app.settings.account_id}")
            breached = json.loads(row.value).get("breached") if row else None
        print(
            json.dumps(
                {
                    "actual_broker_calculation": "passed",
                    "symbol": instruction.symbol,
                    "lots": str(instruction.volume),
                    "sl": str(instruction.stop_loss),
                    "tp": str(instruction.take_profit),
                    "assumptions": instruction.note,
                    "open_positions": len(state.positions),
                    "pending_orders": len(state.orders),
                    "loss_breaker_latched": breached,
                    "entries_paused": app.service._paused(),
                    "broker_mutations": 0,
                }
            )
        )
        if "--activate-settings" in sys.argv:
            path = Path(".env")
            backup = Path(".env.pre-spec004")
            if not backup.exists():
                shutil.copy2(path, backup)
                backup.chmod(0o600)
            updates = {
                "TRADE_AGENT_ALLOWED_SYMBOLS": ",".join(app.settings.allowed_symbols),
                "TRADE_AGENT_AUTO_DEMO_PROTECTION": "true",
            }
            lines = [
                line
                for line in path.read_text().splitlines()
                if line.split("=", 1)[0].strip() not in updates
            ]
            lines.extend(f"{key}={value}" for key, value in updates.items())
            path.write_text("\n".join(lines) + "\n")
            path.chmod(0o600)
            print(
                "Enabled XAUUSD and automatic demo proposal protection; pause and limits unchanged"
            )
    finally:
        await facade.aclose()


if __name__ == "__main__":
    asyncio.run(main())
