from decimal import Decimal

import pytest

from trade_agent.app import ApplicationFacade, TelegramApplicationFacade
from trade_agent.broker import BrokerUnavailable, FakeBroker
from trade_agent.config import Settings
from trade_agent.db import Database, ProposalRecord
from trade_agent.risk import RiskRejected
from trade_agent.service import TradeService


@pytest.fixture
def setup(tmp_path):
    settings = Settings(
        _env_file=None,
        telegram_owner_id="test",
        auto_demo_protection=True,
        max_estimated_loss=Decimal(10),
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )
    db = Database(settings.database_url)
    db.create_all()
    broker = FakeBroker(settings.account_id)
    broker.profit_target = lambda *args: {
        "take_profit": str(
            args[-2] - Decimal("0.01") if args[2].value == "sell" else args[-2] + Decimal("0.01")
        ),
        "estimated_gross_profit": "10",
    }
    facade = TelegramApplicationFacade(
        ApplicationFacade(settings, TradeService(settings, db, broker))
    )
    return facade, broker


@pytest.mark.asyncio
async def test_q2_q5_price_rejection_gets_one_fresh_button_without_submission(setup, monkeypatch):
    facade, broker = setup
    first = await facade.create_trade_from_text(
        owner_id="test", text="sell EURUSD 0.05 lots target $10 profit"
    )
    assert first.confirmation_token
    first_proposal = facade.application.service.proposal_by_token(first.confirmation_token)
    assert first_proposal.instruction.auto_market_protection

    def reject_once(*args):
        with facade.application.service.database.session() as session:
            row = session.get(ProposalRecord, first_proposal.proposal_id)
            row.status = "rejected"
        return {
            "status": "rejected",
            "message": "quote moved beyond approved tolerance; create a fresh proposal",
        }

    monkeypatch.setattr(facade.application.service, "confirm", reject_once)
    reply = await facade.confirm_trade(owner_id="test", confirmation_token=first.confirmation_token)
    assert reply.confirmation_token and reply.confirmation_token != first.confirmation_token
    assert "order was not sent" in reply.text and "Review this fresh proposal" in reply.text
    assert not broker.state(facade.application.settings.account_id).positions
    repeated = await facade.confirm_trade(
        owner_id="test", confirmation_token=first.confirmation_token
    )
    assert repeated.confirmation_token is None and "already rejected" in repeated.text
    await facade.aclose()


@pytest.mark.asyncio
async def test_q3_retries_only_auto_market_quote_race(setup, monkeypatch):
    facade, _ = setup
    service = facade.application.service
    actual = service.create_proposal
    count = 0

    def moving_quote(instruction):
        nonlocal count
        count += 1
        if count == 1:
            raise RiskRejected("estimated stop-loss amount exceeds configured maximum")
        return actual(instruction)

    monkeypatch.setattr(service, "create_proposal", moving_quote)
    reply = await facade.create_trade_from_text(
        owner_id="test", text="sell EURUSD 0.05 lots target $10 profit"
    )
    assert count == 2 and reply.confirmation_token
    await facade.aclose()


@pytest.mark.asyncio
async def test_q2_explicit_stop_does_not_reprice(setup, monkeypatch):
    facade, _ = setup
    service = facade.application.service
    monkeypatch.setattr(
        service,
        "create_proposal",
        lambda instruction: (_ for _ in ()).throw(
            RiskRejected("estimated stop-loss amount exceeds configured maximum")
        ),
    )
    reply = await facade.create_trade_from_text(
        owner_id="test", text="sell EURUSD 0.05 lots market SL 1.09 target $10 profit"
    )
    assert reply.confirmation_token is None and "Trade was not created" in reply.text
    await facade.aclose()


@pytest.mark.asyncio
async def test_q5_read_only_quote_failure_is_not_unknown_submission(setup, monkeypatch):
    facade, broker = setup
    first = await facade.create_trade_from_text(
        owner_id="test", text="sell EURUSD 0.05 lots target $10 profit"
    )
    proposal = facade.application.service.proposal_by_token(first.confirmation_token)
    monkeypatch.setattr(
        broker, "quote", lambda symbol: (_ for _ in ()).throw(BrokerUnavailable("offline"))
    )
    result = facade.application.service.confirm(
        proposal.proposal_id, "test", proposal.version, proposal.confirmation_token
    )
    assert result["status"] == "rejected" and "No order was sent" in result["message"]
    assert facade.application.service.proposal(proposal.proposal_id).status.value == "rejected"
    await facade.aclose()
