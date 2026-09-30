from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.db import ActionProposalRecord, Database, MutationIntentRecord
from trade_agent.domain import Side, TradeInstruction
from trade_agent.service import TradeService, to_db_time


@pytest.mark.parametrize("change", ["none", "volume", "expired"])
def test_recovered_manual_close_preserves_exact_approval(tmp_path, change):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'recovery.db'}")
    db = Database(settings.database_url)
    db.create_all()
    broker = FakeBroker(settings.account_id)
    service = TradeService(settings, db, broker)
    instruction = TradeInstruction(
        owner_id="100",
        account_id=settings.account_id,
        symbol="EURUSD",
        side=Side.BUY,
        volume=Decimal(".1"),
        entry_price=None,
        stop_loss=Decimal("1.07"),
        take_profit=Decimal("1.09"),
    )
    entry = service.create_proposal(instruction)
    service.confirm(entry.proposal_id, "100", 1, entry.confirmation_token)
    position = broker.state(settings.account_id).positions[0]
    action = service.create_exit_proposal("100", position.position_id, "position")
    key = str(uuid4())
    stamp = to_db_time(datetime.now(UTC))
    with db.session() as session:
        record = session.get(ActionProposalRecord, action.proposal_id)
        record.status = "executing"
        if change == "volume":
            record.payload = {**record.payload, "volume": "0.05"}
        if change == "expired":
            record.expires_at = stamp - timedelta(minutes=1)
        row = MutationIntentRecord(
            id=str(uuid4()),
            action_proposal_id=record.id,
            operation="close",
            target_id=position.position_id,
            idempotency_key=key,
            status="prepared",
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(row)
    original = broker.close
    calls = []

    def close(target, volume=None, idempotency_key=None):
        calls.append(volume)
        return original(target, volume, idempotency_key)

    broker.close = close
    assert service._retry_prepared_mutation(row)
    assert calls == ([Decimal(".1")] if change == "none" else [])
    assert len(broker.state(settings.account_id).positions) == (0 if change == "none" else 1)
    with db.session() as session:
        assert session.get(ActionProposalRecord, action.proposal_id).status == (
            "completed" if change == "none" else "rejected"
        )
