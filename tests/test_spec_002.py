"""Requirement traceability: specs/002-safe-trade-clarification/spec.md."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_agent.ai import AIBackendResponseError, ParsedEntry, TradeProposalParse, build_ai_backend
from trade_agent.app import (
    ApplicationFacade,
    InputIncomplete,
    TelegramApplicationFacade,
    format_proposal,
)
from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.db import Database
from trade_agent.draft_extract import explicit_draft
from trade_agent.service import TradeService
from trade_agent.workflow import ConversationRunner

EXACT = "i want you to open a trade for sell side lot 0.05 and take 10$ profit"


async def broken_parser(message):
    raise AIBackendResponseError("provider returned an invalid structured trade parse")


@pytest.mark.asyncio
async def test_r1_exact_screenshot_groups_only_missing_fields():
    runner = ConversationRunner(broken_parser, "sqlite://")
    try:
        outcome = await runner.run("test", EXACT)
        assert outcome["stage"] == "clarifying"
        assert outcome["requires_trade_fields"] == ["entry", "stop_loss", "symbol"]
        assert outcome["parsed"]["side"] == "sell"
        assert outcome["parsed"]["volume"] == "0.05"
        assert outcome["parsed"]["profit_target_usd"] == "10"
        assert outcome["parsed"]["take_profit"] is None
        assert "$10" in outcome["response"] and "unavailable" not in outcome["response"]
    finally:
        await runner.aclose()


@pytest.mark.parametrize(
    "text", ["take 10$ profit", "take $10 profit", "profit $10", "take profit 10 USD"]
)
def test_r2_money_is_not_price(text):
    parsed = explicit_draft("sell lot 0.05 " + text)
    assert parsed.profit_target_usd == Decimal(10)
    assert parsed.take_profit is None


@pytest.mark.parametrize(
    "text", ["take 10 profit", "take €10 profit", "take £10 profit", "tp 1.05 and take 10$ profit"]
)
def test_r2_ambiguous_or_conflicting_targets_require_clarification(text):
    parsed = explicit_draft("sell lot 0.05 " + text)
    assert parsed.profit_target_usd is None and parsed.take_profit is None
    assert parsed.clarification


@pytest.mark.asyncio
async def test_r3_invalid_response_recovers_explicit_partial_fields():
    runner = ConversationRunner(broken_parser, "sqlite://")
    outcome = await runner.run("test", "sell lot 0.05")
    assert outcome["stage"] == "clarifying"
    assert outcome["parsed"]["side"] == "sell" and outcome["parsed"]["volume"] == "0.05"
    assert "invalid structured" not in outcome["response"]
    await runner.aclose()


@pytest.mark.asyncio
async def test_r3_malformed_complete_response_cannot_become_approval():
    runner = ConversationRunner(broken_parser, "sqlite://")
    outcome = await runner.run("test", "sell EURUSD lot 0.05 entry market sl 1.10 tp 1.06")
    assert outcome["stage"] == "clarifying"
    assert "No trade was submitted" in outcome["response"]
    await runner.aclose()


def test_r3_provider_receives_actual_schema():
    settings = Settings(
        _env_file=None, ai_provider="openai_compatible", openai_response_format="json_object"
    )
    payload = build_ai_backend(settings)._payload("example")
    prompt = payload["messages"][0]["content"]
    assert (
        "Required JSON schema" in prompt
        and "profit_target_usd" in prompt
        and '"properties"' in prompt
    )


@pytest.fixture
def facade(tmp_path):
    settings = Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path / 'spec.db'}", telegram_owner_id="test"
    )
    db = Database(settings.database_url)
    db.create_all()
    return TelegramApplicationFacade(
        ApplicationFacade(settings, TradeService(settings, db, FakeBroker(settings.account_id)))
    )


def complete_money():
    return TradeProposalParse(
        intent="trade",
        symbol="EURUSD",
        side="sell",
        volume=Decimal(".05"),
        entry=ParsedEntry(kind="market"),
        stop_loss=Decimal("1.09"),
        profit_target_usd=Decimal(10),
    )


def test_r4_r5_broker_conversion_is_explicit_in_persisted_confirmation(facade):
    calls = []

    def calculate(*args):
        calls.append(args)
        return {"take_profit": "1.078", "estimated_gross_profit": "10"}

    facade.application.service.broker.profit_target = calculate
    instruction = facade._instruction_from_parse("test", complete_money().model_dump())
    proposal = facade.application.service.create_proposal(instruction)
    text = format_proposal(proposal)
    assert len(calls) == 1 and calls[0][-1] == Decimal(10)
    assert "TP 1.078" in text and "target: $10" in text and "fixed" in text
    assert facade.application.service.proposal(
        proposal.proposal_id
    ).instruction.take_profit == Decimal("1.078")
    assert facade.application.service.broker.state("demo-mt5").positions == []


def test_r4_stale_quote_blocks_calculation(facade):
    broker = facade.application.service.broker
    original = broker.quote
    broker.quote = lambda symbol: original(symbol).model_copy(
        update={"observed_at": datetime.now(UTC) - timedelta(minutes=1)}
    )
    with pytest.raises(InputIncomplete, match="fresh broker quote"):
        facade._instruction_from_parse("test", complete_money().model_dump())


@pytest.mark.asyncio
async def test_r6_fresh_screenshot_draft_does_not_inherit_symbol_or_stop():
    async def parser(message):
        return TradeProposalParse(
            intent="trade",
            symbol="EURUSD",
            side="buy",
            stop_loss=Decimal("1.07"),
            entry=ParsedEntry(kind="missing"),
        )

    runner = ConversationRunner(parser, "sqlite://")
    await runner.run("test", "buy EURUSD")
    outcome = await runner.run("test", EXACT)
    assert outcome["parsed"]["symbol"] is None and outcome["parsed"]["stop_loss"] is None
    await runner.aclose()


@pytest.mark.asyncio
async def test_r6_price_revision_replaces_money_target():
    async def parser(message):
        return TradeProposalParse(
            intent="trade", entry=ParsedEntry(kind="missing"), take_profit=Decimal("1.06")
        )

    runner = ConversationRunner(parser, "sqlite://")
    await runner.run("test", EXACT)
    outcome = await runner.run("test", "tp 1.06")
    assert (
        outcome["parsed"]["take_profit"] == "1.06"
        and outcome["parsed"]["profit_target_usd"] is None
    )
    await runner.aclose()


@pytest.mark.asyncio
async def test_r7_incomplete_message_does_not_unpause_or_create_order(facade):
    facade.application.service.set_paused(True)
    reply = await facade.create_trade_from_text(owner_id="test", text=EXACT)
    assert reply.confirmation_token is None and "Please provide:" in reply.text
    assert facade.application.service._paused()
    assert facade.application.service.broker.state("demo-mt5").positions == []
    await facade.aclose()


@pytest.mark.parametrize("text", ["take -10$ profit", "take $10 profit and €10 profit"])
def test_r2_invalid_monetary_units_cannot_become_positive_usd(text):
    parsed = explicit_draft(text)
    assert parsed.profit_target_usd is None and parsed.take_profit is None
    assert parsed.clarification
