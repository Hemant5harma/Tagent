"""Spec 003: conversational context never supplies unstated trade authority."""

import pytest

from trade_agent.workflow import ConversationRunner, is_draft_followup, requests_no_stop_loss

FIRST = "i want you to open a trade for sell side lot 0.05 and take 10$ profit"
FOLLOWUP = (
    "you can set tp according to given price . and symbol is XAUUSD this . "
    "don't need to add stop_loss for now."
)


async def must_not_call_parser(message):
    raise AssertionError("These explicit partial details need no model inference")


@pytest.mark.asyncio
async def test_exact_screenshot_preserves_draft_and_does_not_invent_entry():
    runner = ConversationRunner(must_not_call_parser, "sqlite://")
    try:
        await runner.run("owner", FIRST)
        outcome = await runner.run("owner", FOLLOWUP)
        assert outcome["stage"] == "clarifying"
        assert outcome["requires_trade_fields"] == ["entry", "stop_loss"]
        assert outcome["parsed"]["symbol"] == "XAUUSD"
        assert outcome["parsed"]["side"] == "sell"
        assert outcome["parsed"]["volume"] == "0.05"
        assert outcome["parsed"]["profit_target_usd"] == "10"
        assert outcome["parsed"]["take_profit"] is None
        assert outcome["parsed"]["entry"]["kind"] == "missing"
        assert outcome["parsed"]["stop_loss"] is None
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_explicit_no_stop_removes_old_stop_but_does_not_complete_draft():
    runner = ConversationRunner(must_not_call_parser, "sqlite://")
    try:
        await runner.run("owner", FIRST + " sl 4400")
        outcome = await runner.run("owner", FOLLOWUP)
        assert outcome["parsed"]["stop_loss"] is None
        assert "stop_loss" in outcome["requires_trade_fields"]
        assert outcome["stage"] == "clarifying"
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_draft_snapshot_is_read_only_and_owner_isolated():
    runner = ConversationRunner(must_not_call_parser, "sqlite://")
    try:
        assert await runner.current("owner") == {}
        await runner.run("owner", FIRST)
        state = await runner.current("owner")
        state["parsed"]["side"] = "buy"
        assert (await runner.current("owner"))["parsed"]["side"] == "sell"
        assert await runner.current("other") == {}
        await runner.reset("owner")
        assert (await runner.current("owner"))["stage"] == "not_trade"
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_new_draft_does_not_inherit_previous_followup_fields():
    runner = ConversationRunner(must_not_call_parser, "sqlite://")
    try:
        await runner.run("owner", FIRST)
        await runner.run("owner", FOLLOWUP)
        outcome = await runner.run("owner", "open a new trade buy lot .01 take $5 profit")
        assert outcome["parsed"]["symbol"] is None
        assert outcome["parsed"]["volume"] == "0.01"
        assert outcome["parsed"]["side"] == "buy"
        assert outcome["parsed"]["profit_target_usd"] == "5"
    finally:
        await runner.aclose()


@pytest.mark.parametrize(
    "text", [FOLLOWUP, "XAUUSD", "entry market", "sl 4400", "change that to .02 lots"]
)
def test_explicit_followup_routing(text):
    assert is_draft_followup(text)


@pytest.mark.parametrize(
    "text",
    [
        "what is stop loss?",
        "why do I need SL?",
        "explain TP",
        "do we have any open trades?",
        "show positions",
    ],
)
def test_educational_and_account_questions_are_not_draft_revisions(text):
    assert not is_draft_followup(text)


@pytest.mark.parametrize(
    "text",
    [
        "no SL",
        "without stop loss",
        "remove the stop-loss",
        "don't need to add stop_loss",
        "don’t want a stop loss",
        "do not set sl",
    ],
)
def test_explicit_stop_removal_is_recognized(text):
    assert requests_no_stop_loss(text)
