import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.db import Database, SystemStateRecord
from trade_agent.domain import BrokerState, PositionSnapshot
from trade_agent.entry_readiness import entry_blockers
from trade_agent.service import TradeService


def position(ticket):
    return PositionSnapshot(
        position_id=ticket,
        symbol="EURUSD",
        side="buy",
        volume=Decimal(".01"),
        entry_price=Decimal("1.08"),
        stop_loss=None,
        take_profit=None,
        profit=Decimal(0),
        opened_at=datetime.now(UTC),
    )


@pytest.fixture
def service(tmp_path):
    settings = Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path / 'test.db'}", max_concurrent_exposures=2
    )
    db = Database(settings.database_url)
    db.create_all()
    return TradeService(settings, db, FakeBroker(settings.account_id))


def test_r1_two_exposures_gate(service, monkeypatch):
    service.settings.broker_mode = "mt5_http"
    monkeypatch.setattr(
        service.broker, "state", lambda account: BrokerState(positions=[position("1")])
    )
    assert not entry_blockers(service)
    monkeypatch.setattr(
        service.broker,
        "state",
        lambda account: BrokerState(positions=[position("1"), position("2")]),
    )
    assert "limit is 2" in entry_blockers(service)[0]


def test_r2_reviewed_position_does_not_pause_but_new_unknown_does(service, monkeypatch):
    with service.database.session() as session:
        session.add(
            SystemStateRecord(
                key=f"reviewed_external_exposure:{service.settings.account_id}",
                value=json.dumps({"references": ["position:1"]}),
                updated_at=datetime.now(UTC),
            )
        )
    monkeypatch.setattr(
        service.broker, "state", lambda account: BrokerState(positions=[position("1")])
    )
    service.reconcile()
    assert not service._paused()
    monkeypatch.setattr(
        service.broker,
        "state",
        lambda account: BrokerState(positions=[position("1"), position("2")]),
    )
    service.reconcile()
    assert service._paused()


def test_r1_default_remains_one():
    assert Settings(_env_file=None).max_concurrent_exposures == 1


def test_r1_five_total_exposures_are_the_configured_upper_bound(service, monkeypatch):
    service.settings.max_concurrent_exposures = 5
    service.settings.broker_mode = "mt5_http"
    monkeypatch.setattr(
        service.broker,
        "state",
        lambda account: BrokerState(positions=[position(str(i)) for i in range(4)]),
    )
    assert not entry_blockers(service)
    monkeypatch.setattr(
        service.broker,
        "state",
        lambda account: BrokerState(positions=[position(str(i)) for i in range(5)]),
    )
    assert "limit is 5" in entry_blockers(service)[0]
