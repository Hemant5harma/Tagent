from __future__ import annotations

from decimal import Decimal

from trade_agent.app import ApplicationFacade, InputIncomplete, parse_trade_text
from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.db import Database
from trade_agent.service import TradeService


def facade(tmp_path) -> ApplicationFacade:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'trade.db'}",
        account_id="demo-mt5",
        allowed_symbols=["EURUSD"],
        max_volume=Decimal("1"),
    )
    database = Database(settings.database_url)
    database.create_all()
    return ApplicationFacade(settings, TradeService(settings, database, FakeBroker("demo-mt5")))


def test_text_input_creates_confirmable_proposal(tmp_path) -> None:
    app = facade(tmp_path)
    text = "Buy EURUSD, 0.10 lots, entry market, SL 1.07900, TP 1.08200"
    confirmation = app.create_trade_from_text("100", text)
    assert "Confirm trade" in confirmation
    assert (
        "MARKET" not in confirmation
    )  # confirmation uses the intentionally readable lower-case type


def test_text_parser_requires_all_trade_parameters() -> None:
    try:
        parse_trade_text("100", "demo-mt5", "buy EURUSD 0.1 lots", "Asia/Kolkata")
    except InputIncomplete as exc:
        assert "entry" in str(exc)
    else:
        raise AssertionError("parser unexpectedly accepted incomplete trade")
