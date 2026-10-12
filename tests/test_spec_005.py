import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from trade_agent.app import ApplicationFacade, TelegramApplicationFacade
from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.db import Database, SystemStateRecord
from trade_agent.entry_readiness import entry_blockers
from trade_agent.service import TradeService


@pytest.fixture
def facade(tmp_path):
    settings = Settings(
        _env_file=None, telegram_owner_id="test", database_url=f"sqlite:///{tmp_path / 'test.db'}"
    )
    db = Database(settings.database_url)
    db.create_all()
    return TelegramApplicationFacade(
        ApplicationFacade(settings, TradeService(settings, db, FakeBroker(settings.account_id)))
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "open this trade",
        "okay open this trade",
        "open that trade please",
        "execute this order",
        "yes",
        "okay",
    ],
)
async def test_b1_blocked_approval_never_parses_or_recalculates(facade, monkeypatch, message):
    facade.application.service.set_paused(True)
    parser = AsyncMock(side_effect=AssertionError("must not parse"))
    monkeypatch.setattr(facade._conversation, "run", parser)
    monkeypatch.setattr(
        facade.application.service.broker, "quote", lambda symbol: pytest.fail("must not calculate")
    )
    reply = await facade.create_trade_from_text(owner_id="test", text=message)
    assert "paused" in reply.text and "No new prices" in reply.text
    assert reply.confirmation_token is None and not parser.called
    await facade.aclose()


@pytest.mark.asyncio
async def test_b1_unblocked_text_approval_still_needs_button(facade, monkeypatch):
    monkeypatch.setattr(
        facade._conversation, "run", AsyncMock(side_effect=AssertionError("must not parse"))
    )
    reply = await facade.create_trade_from_text(owner_id="test", text="okay open this trade")
    assert "Confirm trade button" in reply.text and reply.confirmation_token is None
    await facade.aclose()


def test_b2_latched_budget_reported_without_modification(facade):
    service = facade.application.service
    with service.database.session() as session:
        session.add(
            SystemStateRecord(
                key=f"demo_budget:{service.settings.account_id}",
                value=json.dumps({"breached": True}),
                updated_at=datetime.now(UTC),
            )
        )
    assert any("latched" in item for item in entry_blockers(service))
    with service.database.session() as session:
        assert (
            json.loads(
                session.get(SystemStateRecord, f"demo_budget:{service.settings.account_id}").value
            )["breached"]
            is True
        )
