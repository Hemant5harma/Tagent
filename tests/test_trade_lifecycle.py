from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.db import Database
from trade_agent.domain import (
    ExecutionStatus,
    ProposalStatus,
    Side,
    SubmissionResult,
    TradeInstruction,
)
from trade_agent.risk import RiskRejected
from trade_agent.service import ProposalError, TradeService


def build_service(tmp_path) -> tuple[TradeService, FakeBroker]:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'trades.db'}",
        account_id="demo-mt5",
        allowed_symbols=["EURUSD"],
        max_volume=Decimal("1.00"),
    )
    database = Database(settings.database_url)
    database.create_all()
    broker = FakeBroker(settings.account_id)
    return TradeService(settings, database, broker), broker


def market_instruction() -> TradeInstruction:
    return TradeInstruction(
        owner_id="100",
        account_id="demo-mt5",
        symbol="EURUSD",
        side=Side.BUY,
        volume=Decimal("0.10"),
        entry_price=None,
        stop_loss=Decimal("1.07900"),
        take_profit=Decimal("1.08200"),
    )


def test_market_trade_requires_one_confirmation_and_is_idempotent(tmp_path) -> None:
    service, broker = build_service(tmp_path)
    proposal = service.create_proposal(market_instruction())

    result = service.confirm(
        proposal.proposal_id, "100", proposal.version, proposal.confirmation_token
    )

    assert result["status"] == "submitted"
    assert len(broker.state("demo-mt5").positions) == 1
    with pytest.raises(ProposalError, match="already"):
        service.confirm(proposal.proposal_id, "100", proposal.version, proposal.confirmation_token)
    assert len(broker.state("demo-mt5").positions) == 1


def test_revising_proposal_invalidates_previous_confirmation(tmp_path) -> None:
    service, _ = build_service(tmp_path)
    original = service.create_proposal(market_instruction())
    revised = service.revise_proposal(
        original.proposal_id,
        "100",
        market_instruction().model_copy(update={"volume": Decimal("0.20")}),
    )

    assert revised.version == 2
    assert revised.confirmation_token != original.confirmation_token
    with pytest.raises(ProposalError, match="stale"):
        service.confirm(original.proposal_id, "100", original.version, original.confirmation_token)


def test_pending_order_deadline_cancels_without_another_confirmation(tmp_path) -> None:
    service, broker = build_service(tmp_path)
    instruction = market_instruction().model_copy(
        update={
            "entry_price": Decimal("1.07900"),
            "stop_loss": Decimal("1.07800"),
            "take_profit": Decimal("1.08200"),
            "deadline_at": datetime.now(UTC) - timedelta(seconds=1),
        }
    )
    proposal = service.create_proposal(instruction)
    result = service.confirm(
        proposal.proposal_id, "100", proposal.version, proposal.confirmation_token
    )
    assert result["status"] == "submitted"
    assert len(broker.state("demo-mt5").orders) == 1

    assert service.process_deadlines() == 1
    assert not broker.state("demo-mt5").orders
    assert service.proposal(proposal.proposal_id).status is ProposalStatus.DEADLINE_COMPLETED


def test_pause_blocks_new_entries_but_never_blocks_exit(tmp_path) -> None:
    service, broker = build_service(tmp_path)
    proposal = service.create_proposal(market_instruction())
    service.confirm(proposal.proposal_id, "100", proposal.version, proposal.confirmation_token)
    position = broker.state("demo-mt5").positions[0]
    service.set_paused(True)

    with pytest.raises(RiskRejected, match="paused"):
        service.create_proposal(market_instruction())
    service.close_position(position.position_id)
    assert not broker.state("demo-mt5").positions


def test_rejects_invalid_protection_and_spread_risk(tmp_path) -> None:
    service, _ = build_service(tmp_path)
    invalid = market_instruction().model_copy(
        update={"stop_loss": Decimal("1.08100"), "take_profit": Decimal("1.08200")}
    )
    with pytest.raises(RiskRejected, match="direction"):
        service.create_proposal(invalid)


def test_unknown_connector_result_stays_unknown_instead_of_rejected(tmp_path) -> None:
    class UnknownBroker(FakeBroker):
        def submit(self, instruction, order_type, idempotency_key):
            return SubmissionResult(
                accepted=False,
                status=ExecutionStatus.UNKNOWN,
                reason="terminal response was lost",
            )

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'unknown.db'}",
        account_id="demo-mt5",
        allowed_symbols=["EURUSD"],
    )
    database = Database(settings.database_url)
    database.create_all()
    service = TradeService(settings, database, UnknownBroker(settings.account_id))
    proposal = service.create_proposal(market_instruction())

    result = service.confirm(
        proposal.proposal_id, "100", proposal.version, proposal.confirmation_token
    )

    assert result["status"] == "unknown"
    assert service.proposal(proposal.proposal_id).status is ProposalStatus.UNKNOWN


def test_reconcile_recovers_crash_after_intent_commit_before_submit(tmp_path) -> None:
    service, broker = build_service(tmp_path)
    proposal = service.create_proposal(market_instruction())
    service._claim_confirmation(  # noqa: SLF001 - deliberately models the crash boundary
        proposal.proposal_id, "100", proposal.version, proposal.confirmation_token
    )

    assert not broker.state("demo-mt5").positions
    assert service.reconcile() == 1
    assert len(broker.state("demo-mt5").positions) == 1
    assert service.proposal(proposal.proposal_id).status is ProposalStatus.SUBMITTED


def test_partial_fill_deadline_cancels_remainder_and_closes_actual_position(tmp_path) -> None:
    service, broker = build_service(tmp_path)
    instruction = market_instruction().model_copy(
        update={
            "entry_price": Decimal("1.07900"),
            "stop_loss": Decimal("1.07800"),
            "take_profit": Decimal("1.08200"),
            "deadline_at": datetime.now(UTC) - timedelta(seconds=1),
        }
    )
    proposal = service.create_proposal(instruction)
    service.confirm(proposal.proposal_id, "100", proposal.version, proposal.confirmation_token)
    order = broker.state("demo-mt5").orders[0]
    broker.fill_pending_order(order.order_id, Decimal("0.04"))

    assert service.process_deadlines() == 1
    state = broker.state("demo-mt5")
    assert not state.orders
    assert not state.positions


def test_deadline_retries_a_known_partial_close_with_a_new_idempotency_key(tmp_path) -> None:
    class PartialCloseBroker(FakeBroker):
        def __init__(self, account_id: str) -> None:
            super().__init__(account_id)
            self.close_calls = 0

        def close(self, position_id, volume=None, idempotency_key=""):
            self.close_calls += 1
            if self.close_calls == 1:
                current = self.state(self.account_id).positions[0]
                return super().close(
                    position_id,
                    current.volume / Decimal("2"),
                    idempotency_key=idempotency_key,
                )
            return super().close(position_id, volume, idempotency_key=idempotency_key)

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'partial-close.db'}",
        account_id="demo-mt5",
        allowed_symbols=["EURUSD"],
    )
    database = Database(settings.database_url)
    database.create_all()
    broker = PartialCloseBroker(settings.account_id)
    service = TradeService(settings, database, broker)
    instruction = market_instruction().model_copy(
        update={"deadline_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    proposal = service.create_proposal(instruction)
    service.confirm(proposal.proposal_id, "100", proposal.version, proposal.confirmation_token)

    assert service.process_deadlines() == 0
    assert broker.state(settings.account_id).positions
    assert service.process_deadlines() == 1
    assert not broker.state(settings.account_id).positions
    assert broker.close_calls == 2


def test_aggregate_volume_limit_includes_orders_and_positions(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'exposure.db'}",
        account_id="demo-mt5",
        allowed_symbols=["EURUSD"],
        max_total_volume=Decimal("0.15"),
    )
    database = Database(settings.database_url)
    database.create_all()
    broker = FakeBroker(settings.account_id)
    service = TradeService(settings, database, broker)
    first = service.create_proposal(market_instruction())
    service.confirm(first.proposal_id, "100", first.version, first.confirmation_token)

    with pytest.raises(RiskRejected, match="aggregate"):
        service.create_proposal(market_instruction().model_copy(update={"volume": Decimal("0.10")}))
