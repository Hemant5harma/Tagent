"""A reset archives evidence and never submits a demo order by itself."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.db import (
    DashboardCacheRecord,
    Database,
    LedgerEventRecord,
    MutationIntentRecord,
    SystemStateRecord,
)
from trade_agent.demo_evaluation import EvaluationBlocked, inspect, prepare, resume
from trade_agent.domain import Quote
from trade_agent.service import TradeService


@pytest.fixture
def service(tmp_path):
    cfg = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'eval.db'}",
        account_id="demo-mt5",
        broker_mode="mt5_http",
        assisted_demo_accepted=True,
        trading_mode="demo",
        allowed_symbols=["EURUSD", "XAUUSD"],
        max_concurrent_exposures=5,
        max_estimated_loss=Decimal("20"),
        demo_daily_loss_limit=Decimal("300"),
        demo_drawdown_limit=Decimal("1000"),
    )
    db = Database(cfg.database_url)
    db.create_all()
    # The real deployment URL and engine are both PostgreSQL. This fixture
    # substitutes SQLite only for the service's isolated unit test.
    cfg.database_url = "postgresql://unit-test"
    broker = FakeBroker(cfg.account_id)
    original_quote, original_symbol = broker.quote, broker.symbol
    broker.quote = lambda symbol: (
        Quote(
            symbol="XAUUSD",
            bid=Decimal("4000"),
            ask=Decimal("4000.10"),
            observed_at=datetime.now(UTC),
        )
        if symbol == "XAUUSD"
        else original_quote(symbol)
    )
    broker.symbol = lambda symbol: (
        original_symbol("EURUSD").model_copy(update={"symbol": "XAUUSD"})
        if symbol == "XAUUSD"
        else original_symbol(symbol)
    )
    stamp = datetime.now(UTC)
    with db.session() as session:
        session.add_all(
            [
                SystemStateRecord(key="entries_paused", value="true", updated_at=stamp),
                SystemStateRecord(
                    key="demo_budget:demo-mt5",
                    value=json.dumps(
                        {
                            "started_at": stamp.timestamp() - 86400,
                            "high": "12000",
                            "day_high": "10000",
                            "day_start": "10000",
                            "date": stamp.date().isoformat(),
                            "breached": True,
                        }
                    ),
                    updated_at=stamp,
                ),
                DashboardCacheRecord(
                    key="history",
                    payload={"complete": True, "since": (stamp - timedelta(days=1)).isoformat()},
                    updated_at=stamp,
                ),
            ]
        )
    return TradeService(cfg, db, broker)


def test_approved_reset_is_archived_and_does_not_trade(service):
    submitted = []
    service.broker.submit = lambda *args, **kwargs: submitted.append((args, kwargs))
    result = prepare(service, "owner-approved-2026-10-11")
    assert result["equity_baseline"] == "10000"
    assert service._paused()
    with service.database.session() as session:
        budget = json.loads(session.get(SystemStateRecord, "demo_budget:demo-mt5").value)
        assert budget["high"] == "10000" and budget["breached"] is False
        active = session.get(SystemStateRecord, "demo_evaluation_active:demo-mt5")
        archived = json.loads(session.get(SystemStateRecord, active.value).value)
        assert archived["previous_budget"]["high"] == "12000"
        assert session.scalar(select(LedgerEventRecord.kind)) == "operator_authorized_demo_evaluation_reset"
    assert submitted == []
    with pytest.raises(EvaluationBlocked, match="already been used"):
        prepare(service, "owner-approved-2026-10-11")


def test_fresh_feed_can_resume_without_any_order(service):
    prepare(service, "approval-1")
    submitted = []
    service.broker.submit = lambda *args, **kwargs: submitted.append((args, kwargs))
    assert resume(service) == {"entries_enabled": True, "broker_orders_submitted": 0}
    assert not service._paused()
    assert submitted == []
    assert inspect(service)["breaker_latched"] is False


def test_stale_quote_fails_closed_after_reset(service):
    prepare(service, "approval-1")
    service.broker.quote = lambda symbol: Quote(
        symbol=symbol,
        bid=Decimal("4000"),
        ask=Decimal("4000.10"),
        observed_at=datetime.now(UTC) - timedelta(minutes=2),
    )
    with pytest.raises(EvaluationBlocked, match="quote is stale"):
        resume(service)
    assert service._paused()


def test_unresolved_mutation_blocks_reset(service):
    stamp = datetime.now(UTC)
    with service.database.session() as session:
        session.add(
            MutationIntentRecord(
                id="mutation-1",
                proposal_id=None,
                action_proposal_id=None,
                operation="close",
                target_id="123",
                idempotency_key="unique-mutation-1",
                status="unknown",
                result=None,
                created_at=stamp,
                updated_at=stamp,
            )
        )
    with pytest.raises(EvaluationBlocked, match="protection/exit outcome is unresolved"):
        prepare(service, "approval-1")
    assert inspect(service)["breaker_latched"] is True
