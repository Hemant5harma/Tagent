from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_agent.ai import ParsedEntry, TradeProposalParse
from trade_agent.app import ApplicationFacade, TelegramApplicationFacade
from trade_agent.auto_protection import complete_protection
from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.db import Database
from trade_agent.service import TradeService


@pytest.fixture
def setup(tmp_path):
    settings = Settings(
        _env_file=None,
        auto_demo_protection=True,
        allowed_symbols=["EURUSD", "XAUUSD"],
        max_estimated_loss=Decimal(10),
        telegram_owner_id="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )
    broker = FakeBroker(settings.account_id)
    quote, spec = broker.quote("EURUSD"), broker.symbol("EURUSD")
    broker.quote = lambda symbol: quote.model_copy(
        update={
            "symbol": symbol,
            "bid": Decimal("2600"),
            "ask": Decimal("2600.20"),
            "observed_at": datetime.now(UTC),
        }
    )
    broker.symbol = lambda symbol: spec.model_copy(
        update={
            "symbol": symbol,
            "point": Decimal(".01"),
            "trade_tick_size": Decimal(".01"),
            "trade_tick_value": Decimal("1"),
            "min_stop_distance_points": Decimal("10"),
        }
    )
    broker.profit_target = lambda *args: {"take_profit": "2598", "estimated_gross_profit": "10"}
    db = Database(settings.database_url)
    db.create_all()
    facade = TelegramApplicationFacade(
        ApplicationFacade(settings, TradeService(settings, db, broker))
    )
    return settings, broker, facade


def draft(side="sell"):
    return TradeProposalParse(
        intent="trade",
        symbol="XAUUSD",
        side=side,
        volume=Decimal(".05"),
        entry=ParsedEntry(kind="missing"),
        profit_target_usd=Decimal(10),
    )


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_a1_a2_stop_is_capped_and_directional(setup, side):
    settings, broker, _ = setup
    result = complete_protection(draft(side), settings, broker)
    reference = broker.quote("XAUUSD").ask if side == "buy" else broker.quote("XAUUSD").bid
    assert Decimal("1.7") <= abs(result.stop_loss - reference) < Decimal(2)
    assert result.stop_loss < reference if side == "buy" else result.stop_loss > reference
    assert result.entry.kind == "market" and "cap $10" in result.note


def test_a1_explicit_levels_and_disabled_feature_unchanged(setup):
    settings, broker, _ = setup
    parsed = draft().model_copy(
        update={"entry": ParsedEntry(kind="price", price=Decimal(2601)), "stop_loss": Decimal(2603)}
    )
    assert complete_protection(parsed, settings, broker) == parsed
    settings.auto_demo_protection = False
    assert complete_protection(draft(), settings, broker) == draft()


@pytest.mark.parametrize(
    "failure", ["stale", "live", "currency", "tick", "spread", "volume", "allowlist"]
)
def test_a2_invalid_calculation_is_blocked(setup, failure):
    settings, broker, _ = setup
    original_quote, original_account, original_spec = broker.quote, broker.account, broker.symbol
    parsed = draft()
    if failure == "stale":
        broker.quote = lambda symbol: original_quote(symbol).model_copy(
            update={"observed_at": datetime.now(UTC) - timedelta(minutes=1)}
        )
    elif failure in {"live", "currency"}:
        broker.account = lambda account: original_account(account).model_copy(
            update={"is_demo": False} if failure == "live" else {"currency": "EUR"}
        )
    elif failure == "tick":
        broker.symbol = lambda symbol: original_spec(symbol).model_copy(
            update={"trade_tick_value": None}
        )
    elif failure == "spread":
        broker.quote = lambda symbol: original_quote(symbol).model_copy(
            update={"ask": Decimal(2603)}
        )
    elif failure == "volume":
        parsed = parsed.model_copy(update={"volume": Decimal(".001")})
    else:
        settings.allowed_symbols = ["EURUSD"]
    with pytest.raises(ValueError):
        complete_protection(parsed, settings, broker)


@pytest.mark.asyncio
async def test_a3_a5_exact_request_preview_while_paused(setup):
    _, broker, facade = setup
    facade.application.service.set_paused(True)
    reply = await facade.create_trade_from_text(
        owner_id="test",
        text="i want you to open a trade for sell side lot 0.05 and take 10$ profit. symbol is XAUUSD. don't need to add stop_loss for now.",
    )
    assert "SL 2602" not in reply.text and "TP 2598" not in reply.text
    assert "paused" in reply.text and "No new prices" in reply.text
    assert reply.confirmation_token is None
    assert broker.state("demo-mt5").positions == []
    await facade.aclose()


@pytest.mark.asyncio
async def test_a1_pending_request_is_not_changed_to_market(setup):
    _, _, facade = setup
    reply = await facade.create_trade_from_text(
        owner_id="test", text="open a sell limit trade XAUUSD lot 0.05 take $10 profit"
    )
    assert "pending entry price" in reply.text and reply.confirmation_token is None
    await facade.aclose()


@pytest.mark.asyncio
async def test_a3_one_proposal_no_submission_before_confirmation(setup):
    _, broker, facade = setup
    reply = await facade.create_trade_from_text(
        owner_id="test", text="open a sell trade XAUUSD lot 0.05 take $10 profit"
    )
    assert reply.confirmation_token and "SL 2601.79" in reply.text
    assert "Proposed default: market" in reply.text and "cap $10" in reply.text
    assert not broker.state("demo-mt5").positions
    assert (await facade._conversation.current("test"))["stage"] == "not_trade"
    await facade.aclose()
