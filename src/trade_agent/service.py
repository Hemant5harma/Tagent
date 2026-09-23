"""Application operations for drafts, confirmation, execution, and recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import RLock
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from trade_agent.broker import BrokerAdapter, BrokerUnavailable
from trade_agent.config import Settings
from trade_agent.db import (
    ActionProposalRecord,
    Database,
    ExecutionIntentRecord,
    IncomingUpdateRecord,
    LedgerEventRecord,
    MutationIntentRecord,
    NotificationOutboxRecord,
    ProposalRecord,
    SystemStateRecord,
)
from trade_agent.domain import (
    ActionProposalView,
    ActionStatus,
    ActionType,
    ExecutionStatus,
    OrderType,
    ProposalStatus,
    ProposalView,
    TradeInstruction,
)
from trade_agent.risk import RiskPolicy, RiskRejected, ValidatedTrade


class ProposalError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_db_time(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def from_db_time(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class TradeService:
    def __init__(self, settings: Settings, database: Database, broker: BrokerAdapter) -> None:
        self.settings = settings
        self.database = database
        self.broker = broker
        self.risk = RiskPolicy(settings, broker)
        self._mutation_lock = RLock()

    def _paused(self) -> bool:
        with self.database.session() as session:
            record = session.get(SystemStateRecord, "entries_paused")
            return record is not None and record.value == "true"

    def set_paused(self, paused: bool) -> None:
        now = to_db_time(utc_now())
        with self.database.session() as session:
            record = session.get(SystemStateRecord, "entries_paused")
            if record is None:
                session.add(
                    SystemStateRecord(
                        key="entries_paused", value=str(paused).lower(), updated_at=now
                    )
                )
            else:
                record.value = str(paused).lower()
                record.updated_at = now
        self._event(None, None, "entries_paused" if paused else "entries_resumed", {})

    def _event(
        self, proposal_id: str | None, intent_id: str | None, kind: str, payload: dict[str, Any]
    ) -> None:
        with self.database.session() as session:
            session.add(
                LedgerEventRecord(
                    proposal_id=proposal_id,
                    execution_intent_id=intent_id,
                    kind=kind,
                    payload=payload,
                    created_at=to_db_time(utc_now()),
                )
            )

    def _notify(self, owner_id: str, text: str, dedupe_key: str) -> None:
        with self.database.session() as session:
            self._add_notification(session, owner_id, text, dedupe_key)

    @staticmethod
    def _add_notification(session: Any, owner_id: str, text: str, dedupe_key: str) -> None:
        exists = session.scalar(
            select(NotificationOutboxRecord.id).where(
                NotificationOutboxRecord.dedupe_key == dedupe_key
            )
        )
        if exists is None:
            session.add(
                NotificationOutboxRecord(
                    owner_id=owner_id,
                    text=text,
                    dedupe_key=dedupe_key,
                    created_at=to_db_time(utc_now()),
                )
            )

    def validate(self, instruction: TradeInstruction) -> ValidatedTrade:
        if self._paused():
            raise RiskRejected("new entries are paused")
        return self.risk.validate(instruction)

    def create_proposal(self, instruction: TradeInstruction) -> ProposalView:
        validated = self.validate(instruction)
        now = utc_now()
        proposal_id = str(uuid4())
        token = str(uuid4())
        expires = now + timedelta(seconds=self.settings.confirmation_ttl_seconds)
        with self.database.session() as session:
            session.add(
                ProposalRecord(
                    id=proposal_id,
                    owner_id=instruction.owner_id,
                    account_id=instruction.account_id,
                    version=1,
                    status=ProposalStatus.PENDING_CONFIRMATION.value,
                    instruction=instruction.model_dump(mode="json"),
                    validation=validated.summary(),
                    order_type=validated.order_type.value,
                    confirmation_token=token,
                    expires_at=to_db_time(expires),
                    deadline_at=to_db_time(instruction.deadline_at)
                    if instruction.deadline_at
                    else None,
                    created_at=to_db_time(now),
                    updated_at=to_db_time(now),
                )
            )
        self._event(proposal_id, None, "proposal_created", validated.summary())
        return ProposalView(
            proposal_id=proposal_id,
            version=1,
            status=ProposalStatus.PENDING_CONFIRMATION,
            instruction=instruction,
            order_type=validated.order_type,
            expires_at=expires,
            validation=validated.summary(),
            confirmation_token=token,
        )

    @staticmethod
    def _view(record: ProposalRecord) -> ProposalView:
        return ProposalView(
            proposal_id=record.id,
            version=record.version,
            status=ProposalStatus(record.status),
            instruction=TradeInstruction.model_validate(record.instruction),
            order_type=OrderType(record.order_type),
            expires_at=from_db_time(record.expires_at),
            validation=record.validation,
            confirmation_token=record.confirmation_token,
        )

    def proposal(self, proposal_id: str) -> ProposalView:
        with self.database.session() as session:
            record = session.get(ProposalRecord, proposal_id)
            if record is None:
                raise ProposalError("proposal not found")
            return self._view(record)

    def revise_proposal(
        self, proposal_id: str, owner_id: str, instruction: TradeInstruction
    ) -> ProposalView:
        if instruction.owner_id != owner_id:
            raise ProposalError("revised instruction owner does not match requester")
        validated = self.validate(instruction)
        now = utc_now()
        expires = now + timedelta(seconds=self.settings.confirmation_ttl_seconds)
        with self.database.session() as session:
            record = session.get(ProposalRecord, proposal_id, with_for_update=True)
            if record is None:
                raise ProposalError("proposal not found")
            if record.owner_id != owner_id:
                raise ProposalError("only the proposal owner can revise it")
            if record.status != ProposalStatus.PENDING_CONFIRMATION.value:
                raise ProposalError("only a pending proposal can be revised")
            record.version += 1
            record.instruction = instruction.model_dump(mode="json")
            record.validation = validated.summary()
            record.order_type = validated.order_type.value
            record.confirmation_token = str(uuid4())
            record.expires_at = to_db_time(expires)
            record.deadline_at = (
                to_db_time(instruction.deadline_at) if instruction.deadline_at else None
            )
            record.updated_at = to_db_time(now)
            view = self._view(record)
        self._event(proposal_id, None, "proposal_revised", {"version": view.version})
        return view

    def proposal_by_token(self, confirmation_token: str) -> ProposalView:
        with self.database.session() as session:
            record = session.scalar(
                select(ProposalRecord).where(
                    ProposalRecord.confirmation_token == confirmation_token
                )
            )
            if record is None:
                raise ProposalError("proposal not found")
            return self._view(record)

    def accept_telegram_update(self, update_id: str, owner_id: str) -> bool:
        """Persist a Telegram delivery before processing it, for retry-safe polling."""
        with self.database.session() as session:
            existing = session.get(IncomingUpdateRecord, update_id, with_for_update=True)
            if existing is not None:
                return existing.status != "completed"
            session.add(
                IncomingUpdateRecord(
                    update_id=update_id,
                    owner_id=owner_id,
                    status="received",
                    received_at=to_db_time(utc_now()),
                    completed_at=None,
                )
            )
        return True

    def complete_telegram_update(self, update_id: str) -> None:
        with self.database.session() as session:
            record = session.get(IncomingUpdateRecord, update_id, with_for_update=True)
            if record is not None:
                record.status = "completed"
                record.completed_at = to_db_time(utc_now())

    def _claim_confirmation(
        self, proposal_id: str, owner_id: str, version: int, confirmation_token: str
    ) -> tuple[TradeInstruction, str, str]:
        now = utc_now()
        intent_id = str(uuid4())
        key = str(uuid4())
        with self.database.session() as session:
            record = session.get(ProposalRecord, proposal_id, with_for_update=True)
            if record is None:
                raise ProposalError("proposal not found")
            if record.owner_id != owner_id:
                raise ProposalError("only the proposal owner can confirm it")
            if record.version != version or record.confirmation_token != confirmation_token:
                raise ProposalError("proposal version or confirmation token is stale")
            if record.status != ProposalStatus.PENDING_CONFIRMATION.value:
                raise ProposalError(f"proposal is already {record.status}")
            if from_db_time(record.expires_at) <= now:
                record.status = ProposalStatus.EXPIRED.value
                record.updated_at = to_db_time(now)
                raise ProposalError("proposal confirmation has expired")
            if self._paused():
                raise ProposalError("new entries are paused")
            reserved = session.scalars(
                select(ExecutionIntentRecord).where(
                    ExecutionIntentRecord.status.in_(
                        [ExecutionStatus.PREPARED.value, ExecutionStatus.UNKNOWN.value]
                    )
                )
            ).all()
            if len(reserved) >= self.settings.max_pending_submissions:
                raise ProposalError("maximum pending submission reservation reached")
            record.status = ProposalStatus.SUBMITTING.value
            record.updated_at = to_db_time(now)
            session.add(
                ExecutionIntentRecord(
                    id=intent_id,
                    proposal_id=proposal_id,
                    idempotency_key=key,
                    status=ExecutionStatus.PREPARED.value,
                    broker_order_id=None,
                    broker_deal_id=None,
                    broker_position_id=None,
                    result=None,
                    created_at=to_db_time(now),
                    updated_at=to_db_time(now),
                )
            )
            return TradeInstruction.model_validate(record.instruction), record.order_type, key

    def confirm(
        self, proposal_id: str, owner_id: str, version: int, confirmation_token: str
    ) -> dict[str, Any]:
        with self._mutation_lock:
            instruction, stored_order_type, key = self._claim_confirmation(
                proposal_id, owner_id, version, confirmation_token
            )
            self._event(proposal_id, None, "confirmation_claimed", {"idempotency_key": key})
            try:
                validated = self.validate(instruction)
                state = self.broker.state(instruction.account_id)
                active_volume = sum(
                    (item.volume for item in state.orders), start=Decimal("0")
                ) + sum((item.volume for item in state.positions), start=Decimal("0"))
                reserved_volume = self._reserved_submission_volume(excluding=proposal_id)
                if (
                    active_volume + reserved_volume + instruction.volume
                    > self.settings.max_total_volume
                ):
                    raise RiskRejected("reserved submissions exceed aggregate volume limit")
                if validated.order_type.value != stored_order_type:
                    raise RiskRejected(
                        "market movement changed pending order type; create a fresh proposal"
                    )
                result = self.broker.submit(instruction, validated.order_type, key)
            except BrokerUnavailable as exc:
                self._finish_unknown(proposal_id, key, str(exc))
                return {"status": "unknown", "message": str(exc)}
            except (RiskRejected, ValueError) as exc:
                self._finish_rejected(proposal_id, key, str(exc))
                return {"status": "rejected", "message": str(exc)}
        if result.status is ExecutionStatus.UNKNOWN:
            self._finish_unknown(
                proposal_id, key, result.reason or "broker submission outcome is unknown"
            )
            return {"status": "unknown", "message": result.reason}
        if not result.accepted:
            self._finish_rejected(
                proposal_id,
                key,
                result.reason or "broker rejected request",
                result.model_dump(mode="json"),
            )
            return {"status": "rejected", "message": result.reason}
        with self.database.session() as session:
            record = session.get(ProposalRecord, proposal_id)
            intent = session.scalar(
                select(ExecutionIntentRecord).where(ExecutionIntentRecord.idempotency_key == key)
            )
            assert record is not None and intent is not None
            record.status = ProposalStatus.SUBMITTED.value
            record.updated_at = to_db_time(utc_now())
            intent.status = result.status.value
            intent.broker_order_id = result.order_id
            intent.broker_deal_id = result.deal_id
            intent.broker_position_id = result.position_id
            intent.result = result.model_dump(mode="json")
            intent.updated_at = to_db_time(utc_now())
            self._add_notification(
                session,
                owner_id,
                self._format_submission(instruction, result),
                f"submitted:{proposal_id}",
            )
        self._event(proposal_id, None, "submission_accepted", result.model_dump(mode="json"))
        return {"status": "submitted", "result": result.model_dump(mode="json")}

    def _reserved_submission_volume(self, excluding: str | None = None) -> Decimal:
        with self.database.session() as session:
            rows = session.execute(
                select(ExecutionIntentRecord, ProposalRecord)
                .join(ProposalRecord, ProposalRecord.id == ExecutionIntentRecord.proposal_id)
                .where(
                    ExecutionIntentRecord.status.in_(
                        [ExecutionStatus.PREPARED.value, ExecutionStatus.UNKNOWN.value]
                    )
                )
            ).all()
        return sum(
            (
                TradeInstruction.model_validate(proposal.instruction).volume
                for intent, proposal in rows
                if intent.proposal_id != excluding
            ),
            start=Decimal("0"),
        )

    def _finish_rejected(
        self, proposal_id: str, key: str, message: str, result: dict[str, Any] | None = None
    ) -> None:
        with self.database.session() as session:
            record = session.get(ProposalRecord, proposal_id)
            intent = session.scalar(
                select(ExecutionIntentRecord).where(ExecutionIntentRecord.idempotency_key == key)
            )
            assert record is not None and intent is not None
            record.status = ProposalStatus.REJECTED.value
            record.updated_at = to_db_time(utc_now())
            intent.status = ExecutionStatus.REJECTED.value
            intent.result = result or {"reason": message}
            intent.updated_at = to_db_time(utc_now())
            self._add_notification(
                session, record.owner_id, f"Trade rejected: {message}", f"rejected:{proposal_id}"
            )
        self._event(proposal_id, None, "submission_rejected", {"reason": message})

    def _finish_unknown(self, proposal_id: str, key: str, message: str) -> None:
        with self.database.session() as session:
            record = session.get(ProposalRecord, proposal_id)
            intent = session.scalar(
                select(ExecutionIntentRecord).where(ExecutionIntentRecord.idempotency_key == key)
            )
            assert record is not None and intent is not None
            record.status = ProposalStatus.UNKNOWN.value
            record.updated_at = to_db_time(utc_now())
            intent.status = ExecutionStatus.UNKNOWN.value
            intent.result = {"reason": message}
            intent.updated_at = to_db_time(utc_now())
            self._add_notification(
                session,
                record.owner_id,
                "Trade outcome unknown; reconciling broker state.",
                f"unknown:{proposal_id}",
            )
        self._event(proposal_id, None, "submission_unknown", {"reason": message})

    @staticmethod
    def _format_submission(instruction: TradeInstruction, result: Any) -> str:
        target = result.position_id or result.order_id or "broker reference unavailable"
        return f"Trade submitted: {instruction.side.value} {instruction.volume} {instruction.symbol} ({target})."

    def reject(
        self, proposal_id: str, owner_id: str, version: int, confirmation_token: str
    ) -> None:
        with self.database.session() as session:
            record = session.get(ProposalRecord, proposal_id, with_for_update=True)
            if record is None:
                raise ProposalError("proposal not found")
            if (
                record.owner_id != owner_id
                or record.version != version
                or record.confirmation_token != confirmation_token
            ):
                raise ProposalError("proposal ownership or version mismatch")
            if record.status != ProposalStatus.PENDING_CONFIRMATION.value:
                raise ProposalError("proposal is no longer pending")
            record.status = ProposalStatus.CANCELLED.value
            record.updated_at = to_db_time(utc_now())
        self._event(proposal_id, None, "proposal_rejected", {})

    def cancel_order(self, order_id: str) -> None:
        key = str(uuid4())
        self._record_mutation(None, None, "cancel", order_id, key)
        try:
            with self._mutation_lock:
                self.broker.cancel(order_id, key)
        except BrokerUnavailable as exc:
            self._complete_mutation(key, ExecutionStatus.UNKNOWN, {"reason": str(exc)})
            raise
        except ValueError as exc:
            self._complete_mutation(key, ExecutionStatus.REJECTED, {"reason": str(exc)})
            raise
        self._complete_mutation(key, ExecutionStatus.CANCELLED)
        self._event(None, None, "order_cancelled", {"order_id": order_id})

    def close_position(self, position_id: str) -> None:
        for _ in range(10):
            key = str(uuid4())
            self._record_mutation(None, None, "close", position_id, key)
            try:
                with self._mutation_lock:
                    self.broker.close(position_id, idempotency_key=key)
            except BrokerUnavailable as exc:
                self._complete_mutation(key, ExecutionStatus.UNKNOWN, {"reason": str(exc)})
                raise
            except ValueError as exc:
                self._complete_mutation(key, ExecutionStatus.REJECTED, {"reason": str(exc)})
                raise
            self._complete_mutation(key, ExecutionStatus.CLOSED)
            active = {
                item.position_id for item in self.broker.state(self.settings.account_id).positions
            }
            if position_id not in active:
                self._event(None, None, "position_closed", {"position_id": position_id})
                return
        raise BrokerUnavailable("position remained active after repeated confirmed partial closes")

    def _record_mutation(
        self,
        proposal_id: str | None,
        action_proposal_id: str | None,
        operation: str,
        target_id: str,
        key: str,
    ) -> None:
        now = to_db_time(utc_now())
        with self.database.session() as session:
            session.add(
                MutationIntentRecord(
                    id=str(uuid4()),
                    proposal_id=proposal_id,
                    action_proposal_id=action_proposal_id,
                    operation=operation,
                    target_id=target_id,
                    idempotency_key=key,
                    status=ExecutionStatus.PREPARED.value,
                    result=None,
                    created_at=now,
                    updated_at=now,
                )
            )

    def _complete_mutation(
        self, key: str, status: ExecutionStatus, result: dict[str, Any] | None = None
    ) -> None:
        with self.database.session() as session:
            record = session.scalar(
                select(MutationIntentRecord).where(MutationIntentRecord.idempotency_key == key)
            )
            if record is not None:
                record.status = status.value
                record.result = result
                record.updated_at = to_db_time(utc_now())

    @staticmethod
    def _action_view(record: ActionProposalRecord) -> ActionProposalView:
        return ActionProposalView(
            proposal_id=record.id,
            owner_id=record.owner_id,
            version=record.version,
            action_type=ActionType(record.action_type),
            target_kind=record.target_kind,
            target_id=record.target_id,
            payload=record.payload,
            status=ActionStatus(record.status),
            confirmation_token=record.confirmation_token,
            expires_at=from_db_time(record.expires_at),
        )

    def action_proposal_by_token(self, confirmation_token: str) -> ActionProposalView:
        with self.database.session() as session:
            record = session.scalar(
                select(ActionProposalRecord).where(
                    ActionProposalRecord.confirmation_token == confirmation_token
                )
            )
            if record is None:
                raise ProposalError("action proposal not found")
            return self._action_view(record)

    def create_protection_modification_proposal(
        self,
        owner_id: str,
        order_id: str | None,
        position_id: str | None,
        stop_loss: Decimal | None,
        take_profit: Decimal | None,
    ) -> ActionProposalView:
        if bool(order_id) == bool(position_id):
            raise ProposalError("provide exactly one of order_id or position_id")
        if stop_loss is None and take_profit is None:
            raise ProposalError("provide a stop loss or take profit")
        state = self.broker.state(self.settings.account_id)
        target_kind = "order" if order_id else "position"
        target_id = order_id or position_id
        assert target_id is not None
        candidates = state.orders if order_id else state.positions
        target = next(
            (item for item in candidates if getattr(item, f"{target_kind}_id") == target_id), None
        )
        if target is None:
            raise ProposalError(f"{target_kind} is not active")
        final_sl = stop_loss if stop_loss is not None else target.stop_loss
        final_tp = take_profit if take_profit is not None else target.take_profit
        if final_sl is None or final_tp is None:
            raise ProposalError("both stop loss and take profit must remain configured")
        assert isinstance(final_sl, Decimal) and isinstance(final_tp, Decimal)
        reference = target.entry_price
        if reference is None:
            raise ProposalError("active target has no entry price")
        valid = (
            final_sl < reference < final_tp
            if target.side.value == "buy"
            else final_tp < reference < final_sl
        )
        if not valid:
            raise ProposalError("modified SL/TP would not protect the position correctly")
        spec = self.broker.symbol(target.symbol)
        if (
            min(abs(reference - final_sl), abs(final_tp - reference)) / spec.point
            < spec.min_stop_distance_points
        ):
            raise ProposalError("modified SL/TP violates broker minimum stop distance")
        now = utc_now()
        expires = now + timedelta(seconds=self.settings.confirmation_ttl_seconds)
        record = ActionProposalRecord(
            id=str(uuid4()),
            owner_id=owner_id,
            account_id=self.settings.account_id,
            version=1,
            action_type=ActionType.MODIFY_PROTECTION.value,
            target_kind=target_kind,
            target_id=target_id,
            payload={"stop_loss": str(final_sl), "take_profit": str(final_tp)},
            status=ActionStatus.PENDING_CONFIRMATION.value,
            confirmation_token=str(uuid4()),
            expires_at=to_db_time(expires),
            created_at=to_db_time(now),
            updated_at=to_db_time(now),
        )
        with self.database.session() as session:
            session.add(record)
        self._event(None, None, "action_proposal_created", {"action_proposal_id": record.id})
        return self._action_view(record)

    def confirm_protection_modification(
        self, proposal_id: str, owner_id: str, version: int, confirmation_token: str
    ) -> dict[str, Any]:
        now = utc_now()
        key = str(uuid4())
        with self._mutation_lock, self.database.session() as session:
            record = session.get(ActionProposalRecord, proposal_id, with_for_update=True)
            if record is None:
                raise ProposalError("action proposal not found")
            if (
                record.owner_id != owner_id
                or record.version != version
                or record.confirmation_token != confirmation_token
            ):
                raise ProposalError("action proposal ownership or version mismatch")
            if record.status != ActionStatus.PENDING_CONFIRMATION.value:
                raise ProposalError(f"action proposal is already {record.status}")
            if from_db_time(record.expires_at) <= now:
                record.status = ActionStatus.EXPIRED.value
                raise ProposalError("action confirmation has expired")
            record.status = ActionStatus.EXECUTING.value
            record.updated_at = to_db_time(now)
            session.add(
                MutationIntentRecord(
                    id=str(uuid4()),
                    proposal_id=None,
                    action_proposal_id=record.id,
                    operation="modify",
                    target_id=record.target_id,
                    idempotency_key=key,
                    status=ExecutionStatus.PREPARED.value,
                    result=None,
                    created_at=to_db_time(now),
                    updated_at=to_db_time(now),
                )
            )
            target_kind = record.target_kind
            payload = dict(record.payload)
        try:
            self.broker.modify(
                order_id=record.target_id if target_kind == "order" else None,
                position_id=record.target_id if target_kind == "position" else None,
                stop_loss=Decimal(payload["stop_loss"]),
                take_profit=Decimal(payload["take_profit"]),
                idempotency_key=key,
            )
        except BrokerUnavailable as exc:
            self._complete_mutation(key, ExecutionStatus.UNKNOWN, {"reason": str(exc)})
            with self.database.session() as session:
                current = session.get(ActionProposalRecord, proposal_id)
                assert current is not None
                current.status = ActionStatus.UNKNOWN.value
            return {"status": "unknown", "message": str(exc)}
        self._complete_mutation(key, ExecutionStatus.ACCEPTED)
        with self.database.session() as session:
            current = session.get(ActionProposalRecord, proposal_id)
            assert current is not None
            current.status = ActionStatus.COMPLETED.value
            current.updated_at = to_db_time(utc_now())
        self._event(None, None, "protection_modified", {"action_proposal_id": proposal_id})
        return {"status": "completed"}

    def reject_action_proposal(
        self, proposal_id: str, owner_id: str, version: int, confirmation_token: str
    ) -> None:
        with self.database.session() as session:
            record = session.get(ActionProposalRecord, proposal_id, with_for_update=True)
            if record is None:
                raise ProposalError("action proposal not found")
            if (
                record.owner_id != owner_id
                or record.version != version
                or record.confirmation_token != confirmation_token
            ):
                raise ProposalError("action proposal ownership or version mismatch")
            if record.status != ActionStatus.PENDING_CONFIRMATION.value:
                raise ProposalError("action proposal is no longer pending")
            record.status = ActionStatus.REJECTED.value
            record.updated_at = to_db_time(utc_now())

    def broker_state(self) -> dict[str, Any]:
        account = self.broker.account(self.settings.account_id)
        state = self.broker.state(self.settings.account_id)
        return {
            "account": account.model_dump(mode="json"),
            "state": state.model_dump(mode="json"),
            "paused": self._paused(),
        }

    def reconcile(self) -> int:
        """Recover prepared/uncertain work and suspend unexpected account activity."""
        resolved = 0
        initial_state = self.broker.state(self.settings.account_id)
        initially_active_orders = {item.order_id: item for item in initial_state.orders}
        initially_active_positions = {item.position_id for item in initial_state.positions}
        with self.database.session() as session:
            intents = session.scalars(
                select(ExecutionIntentRecord).where(
                    ExecutionIntentRecord.status.in_(
                        [
                            ExecutionStatus.PREPARED.value,
                            ExecutionStatus.ACCEPTED.value,
                            ExecutionStatus.UNKNOWN.value,
                        ]
                    )
                )
            ).all()
        for intent in intents:
            if intent.status == ExecutionStatus.ACCEPTED.value:
                if intent.broker_position_id in initially_active_positions:
                    continue
                active_order = initially_active_orders.get(intent.broker_order_id or "")
                if active_order is not None:
                    original_volume = self.proposal(intent.proposal_id).instruction.volume
                    if active_order.volume == original_volume:
                        continue
            try:
                evidence = self.broker.reconcile_operation(intent.idempotency_key)
                if evidence is None and intent.status == ExecutionStatus.PREPARED.value:
                    proposal = self.proposal(intent.proposal_id)
                    validated = self.risk.validate(proposal.instruction)
                    submitted = self.broker.submit(
                        proposal.instruction, validated.order_type, intent.idempotency_key
                    )
                    from trade_agent.domain import BrokerOperationResult

                    evidence = BrokerOperationResult(
                        status=submitted.status,
                        order_id=submitted.order_id,
                        deal_id=submitted.deal_id,
                        position_id=submitted.position_id,
                        reason=submitted.reason,
                        raw=submitted.raw,
                    )
            except (BrokerUnavailable, RiskRejected, ValueError):
                continue
            if evidence is None or evidence.status is ExecutionStatus.UNKNOWN:
                continue
            with self.database.session() as session:
                current = session.get(ExecutionIntentRecord, intent.id)
                proposal_record = session.get(ProposalRecord, intent.proposal_id)
                if current is None:
                    continue
                discovered_position = (
                    current.broker_position_id is None and evidence.position_id is not None
                )
                current.status = evidence.status.value
                current.broker_order_id = evidence.order_id or current.broker_order_id
                current.broker_deal_id = evidence.deal_id or current.broker_deal_id
                current.broker_position_id = evidence.position_id or current.broker_position_id
                current.result = evidence.model_dump(mode="json")
                current.updated_at = to_db_time(utc_now())
                if proposal_record is not None:
                    proposal_record.status = (
                        ProposalStatus.REJECTED.value
                        if evidence.status is ExecutionStatus.REJECTED
                        else ProposalStatus.SUBMITTED.value
                    )
                    proposal_record.updated_at = to_db_time(utc_now())
                    if discovered_position:
                        self._add_notification(
                            session,
                            proposal_record.owner_id,
                            f"Order filled; position {evidence.position_id} is now tracked.",
                            f"filled:{intent.proposal_id}:{evidence.position_id}",
                        )
                resolved += 1
        resolved += self._reconcile_mutations()
        state = self.broker.state(self.settings.account_id)
        active_order_ids = {item.order_id for item in state.orders}
        active_position_ids = {item.position_id for item in state.positions}
        with self.database.session() as session:
            known_orders = set(
                session.scalars(
                    select(ExecutionIntentRecord.broker_order_id).where(
                        ExecutionIntentRecord.broker_order_id.is_not(None)
                    )
                ).all()
            )
            known_positions = set(
                session.scalars(
                    select(ExecutionIntentRecord.broker_position_id).where(
                        ExecutionIntentRecord.broker_position_id.is_not(None)
                    )
                ).all()
            )
            submitted_rows = session.execute(
                select(ExecutionIntentRecord, ProposalRecord)
                .join(ProposalRecord, ProposalRecord.id == ExecutionIntentRecord.proposal_id)
                .where(ProposalRecord.status == ProposalStatus.SUBMITTED.value)
            ).all()
            for ledger_intent, proposal_record in submitted_rows:
                if (
                    ledger_intent.broker_position_id
                    and ledger_intent.broker_position_id not in active_position_ids
                ):
                    ledger_intent.status = ExecutionStatus.CLOSED.value
                    proposal_record.status = ProposalStatus.CLOSED.value
                    self._add_notification(
                        session,
                        proposal_record.owner_id,
                        f"Position {ledger_intent.broker_position_id} exited.",
                        f"exited:{proposal_record.id}:{ledger_intent.broker_position_id}",
                    )
                elif (
                    ledger_intent.broker_order_id
                    and not ledger_intent.broker_position_id
                    and ledger_intent.broker_order_id not in active_order_ids
                ):
                    ledger_intent.status = ExecutionStatus.CANCELLED.value
                    proposal_record.status = ProposalStatus.CANCELLED.value
                    self._add_notification(
                        session,
                        proposal_record.owner_id,
                        f"Pending order {ledger_intent.broker_order_id} is no longer active.",
                        f"order-ended:{proposal_record.id}:{ledger_intent.broker_order_id}",
                    )
        unexpected = [
            *(
                f"order:{item.order_id}"
                for item in state.orders
                if item.order_id not in known_orders
            ),
            *(
                f"position:{item.position_id}"
                for item in state.positions
                if item.position_id not in known_positions
            ),
        ]
        if unexpected and not self._paused():
            self.set_paused(True)
            self._event(None, None, "unexpected_broker_activity", {"references": unexpected})
            if self.settings.telegram_owner_id:
                self._notify(
                    self.settings.telegram_owner_id,
                    "Unexpected external broker activity detected; new entries were paused: "
                    + ", ".join(unexpected),
                    "unexpected-broker-activity:" + ":".join(unexpected),
                )
        return resolved

    def _retry_prepared_mutation(self, row: MutationIntentRecord) -> bool:
        """Replay only when the connector proves it never journaled the operation."""
        try:
            with self._mutation_lock:
                if row.operation == "cancel":
                    self.broker.cancel(row.target_id, row.idempotency_key)
                    status = ExecutionStatus.CANCELLED
                elif row.operation == "close":
                    self.broker.close(row.target_id, idempotency_key=row.idempotency_key)
                    status = ExecutionStatus.CLOSED
                elif row.operation == "modify" and row.action_proposal_id:
                    with self.database.session() as session:
                        action = session.get(ActionProposalRecord, row.action_proposal_id)
                        if action is None:
                            return False
                        payload = dict(action.payload)
                        target_kind = action.target_kind
                    self.broker.modify(
                        order_id=row.target_id if target_kind == "order" else None,
                        position_id=row.target_id if target_kind == "position" else None,
                        stop_loss=Decimal(payload["stop_loss"]),
                        take_profit=Decimal(payload["take_profit"]),
                        idempotency_key=row.idempotency_key,
                    )
                    status = ExecutionStatus.ACCEPTED
                else:
                    return False
        except (BrokerUnavailable, ValueError) as exc:
            self._complete_mutation(
                row.idempotency_key, ExecutionStatus.UNKNOWN, {"reason": str(exc)}
            )
            return False
        self._complete_mutation(row.idempotency_key, status)
        if row.action_proposal_id:
            with self.database.session() as session:
                action = session.get(ActionProposalRecord, row.action_proposal_id)
                if action is not None:
                    action.status = ActionStatus.COMPLETED.value
                    action.updated_at = to_db_time(utc_now())
        return True

    def _reconcile_mutations(self) -> int:
        resolved = 0
        with self.database.session() as session:
            rows = session.scalars(
                select(MutationIntentRecord).where(
                    MutationIntentRecord.status.in_(
                        [ExecutionStatus.PREPARED.value, ExecutionStatus.UNKNOWN.value]
                    )
                )
            ).all()
        for row in rows:
            try:
                evidence = self.broker.reconcile_operation(row.idempotency_key)
            except BrokerUnavailable:
                continue
            if evidence is None or evidence.status is ExecutionStatus.UNKNOWN:
                journal_missing = (
                    evidence is None or evidence.raw.get("journal_status") == "missing"
                )
                if row.status == ExecutionStatus.PREPARED.value and journal_missing:
                    if self._retry_prepared_mutation(row):
                        resolved += 1
                continue
            self._complete_mutation(
                row.idempotency_key, evidence.status, evidence.model_dump(mode="json")
            )
            if row.action_proposal_id:
                with self.database.session() as session:
                    action = session.get(ActionProposalRecord, row.action_proposal_id)
                    if action is not None:
                        action.status = (
                            ActionStatus.COMPLETED.value
                            if evidence.status is not ExecutionStatus.REJECTED
                            else ActionStatus.REJECTED.value
                        )
                        action.updated_at = to_db_time(utc_now())
            resolved += 1
        return resolved

    def process_deadlines(self) -> int:
        self.reconcile()
        now = to_db_time(utc_now())
        with self.database.session() as session:
            rows = session.execute(
                select(ProposalRecord, ExecutionIntentRecord)
                .join(ExecutionIntentRecord, ExecutionIntentRecord.proposal_id == ProposalRecord.id)
                .where(
                    ProposalRecord.status == ProposalStatus.SUBMITTED.value,
                    ProposalRecord.deadline_at.is_not(None),
                    ProposalRecord.deadline_at <= now,
                )
            ).all()
        completed = 0
        for proposal, intent in rows:
            errors: list[str] = []
            state = self.broker.state(self.settings.account_id)
            active_order_ids = {item.order_id for item in state.orders}
            try:
                if intent.broker_order_id in active_order_ids:
                    self._execute_deadline_mutation(
                        proposal.id, "cancel", str(intent.broker_order_id)
                    )
            except (BrokerUnavailable, ValueError) as exc:
                errors.append(str(exc))
            self.reconcile()
            with self.database.session() as session:
                refreshed = session.get(ExecutionIntentRecord, intent.id)
                assert refreshed is not None
                order_id = refreshed.broker_order_id
                position_id = refreshed.broker_position_id
            state = self.broker.state(self.settings.account_id)
            active_position_ids = {item.position_id for item in state.positions}
            try:
                if position_id in active_position_ids:
                    self._execute_deadline_mutation(proposal.id, "close", str(position_id))
            except (BrokerUnavailable, ValueError) as exc:
                errors.append(str(exc))
            state = self.broker.state(self.settings.account_id)
            still_active = order_id in {item.order_id for item in state.orders} or position_id in {
                item.position_id for item in state.positions
            }
            if errors or still_active:
                self._notify(
                    proposal.owner_id,
                    f"Deadline exit needs retry: {'; '.join(errors) or 'broker object remains active'}",
                    f"deadline-error:{proposal.id}",
                )
                continue
            with self.database.session() as session:
                current = session.get(ProposalRecord, proposal.id)
                current_intent = session.get(ExecutionIntentRecord, intent.id)
                assert current is not None and current_intent is not None
                current.status = ProposalStatus.DEADLINE_COMPLETED.value
                current.updated_at = to_db_time(utc_now())
                current_intent.status = ExecutionStatus.CLOSED.value
                current_intent.updated_at = to_db_time(utc_now())
                self._add_notification(
                    session,
                    proposal.owner_id,
                    "Confirmed trade deadline completed.",
                    f"deadline:{proposal.id}",
                )
            self._event(proposal.id, intent.id, "deadline_completed", {})
            completed += 1
        return completed

    def _execute_deadline_mutation(self, proposal_id: str, operation: str, target_id: str) -> None:
        with self.database.session() as session:
            existing = session.scalar(
                select(MutationIntentRecord)
                .where(
                    MutationIntentRecord.proposal_id == proposal_id,
                    MutationIntentRecord.operation == operation,
                    MutationIntentRecord.target_id == target_id,
                )
                .order_by(MutationIntentRecord.created_at.desc())
            )
        completed_statuses = {
            ExecutionStatus.ACCEPTED.value,
            ExecutionStatus.CANCELLED.value,
            ExecutionStatus.CLOSED.value,
        }
        if existing is None or existing.status in completed_statuses:
            key = str(uuid4())
            self._record_mutation(proposal_id, None, operation, target_id, key)
        else:
            key = existing.idempotency_key
        try:
            with self._mutation_lock:
                if operation == "cancel":
                    self.broker.cancel(target_id, key)
                    status = ExecutionStatus.CANCELLED
                else:
                    self.broker.close(target_id, idempotency_key=key)
                    status = ExecutionStatus.CLOSED
        except BrokerUnavailable as exc:
            self._complete_mutation(key, ExecutionStatus.UNKNOWN, {"reason": str(exc)})
            raise
        self._complete_mutation(key, status)

    def pending_notifications(self) -> list[NotificationOutboxRecord]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(NotificationOutboxRecord)
                    .where(NotificationOutboxRecord.delivered_at.is_(None))
                    .order_by(NotificationOutboxRecord.id)
                    .limit(100)
                ).all()
            )

    def mark_notification_delivered(self, notification_id: int) -> None:
        with self.database.session() as session:
            item = session.get(NotificationOutboxRecord, notification_id)
            if item is not None:
                item.delivered_at = to_db_time(utc_now())
