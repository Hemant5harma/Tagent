from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_agent.ai import ParsedEntry, TradeProposalParse
from trade_agent.auto_protection import complete_protection
from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.domain import TradeInstruction
from trade_agent.draft_extract import explicit_draft
from trade_agent.risk import RiskPolicy, RiskRejected
from trade_agent.workflow import ConversationRunner


@pytest.mark.parametrize(
    "words,kind",
    [
        ("buy limit", "buy_limit"),
        ("buy stop", "buy_stop"),
        ("sell limit", "sell_limit"),
        ("sell stop", "sell_stop"),
    ],
)
def test_p1_p2_explicit_pending_type_and_expiry(words, kind):
    parsed = explicit_draft(
        f"{words} at 4250 XAUUSD 0.05 lots target $10 profit expires in 2 hours"
    )
    assert parsed.entry.price == Decimal(4250) and parsed.requested_order_type == kind
    assert parsed.deadline_at is None
    assert (
        datetime.now(UTC) + timedelta(minutes=119)
        < datetime.fromisoformat(parsed.pending_expires_at)
        < datetime.now(UTC) + timedelta(minutes=121)
    )


def test_p1_rise_intent_is_sell_limit():
    parsed = explicit_draft(
        "Sell XAUUSD 0.05 lots when price rises to 4250 target $10 profit expires in 2 hours"
    )
    assert parsed.entry.price == 4250 and parsed.requested_order_type == "sell_limit"


def test_p2_unclear_expiry_is_not_silently_discarded():
    parsed = explicit_draft(
        "sell limit at 4250 XAUUSD 0.05 lots target $10 profit expires tomorrow"
    )
    assert parsed.intent == "clarification" and parsed.clarification


@pytest.mark.asyncio
async def test_p2_expiry_survives_clarification_without_whole_trade_deadline():
    async def parser(message):
        return TradeProposalParse(
            intent="trade", entry=ParsedEntry(kind="missing"), stop_loss=Decimal(4260)
        )

    runner = ConversationRunner(parser, "sqlite://")
    first = await runner.run(
        "test", "sell limit at 4250 XAUUSD 0.05 lots target $10 profit expires in 2 hours"
    )
    second = await runner.run("test", "SL 4260")
    assert second["parsed"]["pending_expires_at"] == first["parsed"]["pending_expires_at"]
    assert second["parsed"]["deadline_at"] is None
    await runner.aclose()


def test_p3_pending_auto_sl_uses_entry_not_current_trigger():
    settings = Settings(_env_file=None, auto_demo_protection=True, max_estimated_loss=Decimal(10))
    broker = FakeBroker(settings.account_id)
    parsed = TradeProposalParse(
        intent="trade",
        symbol="EURUSD",
        side="sell",
        volume=Decimal(".05"),
        entry=ParsedEntry(kind="price", price=Decimal("1.09")),
        profit_target_usd=Decimal(10),
    )
    result = complete_protection(parsed, settings, broker)
    assert result.stop_loss > Decimal("1.09")


def test_p1_crossed_explicit_pending_type_rejected():
    settings = Settings(_env_file=None)
    broker = FakeBroker(settings.account_id)
    instruction = TradeInstruction(
        owner_id="test",
        account_id=settings.account_id,
        symbol="EURUSD",
        side="sell",
        volume=Decimal(".01"),
        entry_price=Decimal("1.07"),
        stop_loss=Decimal("1.075"),
        take_profit=Decimal("1.06"),
        requested_order_type="sell_limit",
    )
    with pytest.raises(RiskRejected, match="Market crossed"):
        RiskPolicy(settings, broker).validate(instruction)
