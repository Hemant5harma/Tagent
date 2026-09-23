"""Channel-independent contracts for trade automation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    BUY_LIMIT = "buy_limit"
    BUY_STOP = "buy_stop"
    SELL_LIMIT = "sell_limit"
    SELL_STOP = "sell_stop"


class ProposalStatus(StrEnum):
    PENDING_CONFIRMATION = "pending_confirmation"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
    DEADLINE_COMPLETED = "deadline_completed"
    CLOSED = "closed"


class ExecutionStatus(StrEnum):
    PREPARED = "prepared"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"
    CLOSED = "closed"


class ActionType(StrEnum):
    MODIFY_PROTECTION = "modify_protection"


class ActionStatus(StrEnum):
    PENDING_CONFIRMATION = "pending_confirmation"
    EXECUTING = "executing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class TradeInstruction(BaseModel):
    """An explicit user request. A missing entry price means market order."""

    model_config = ConfigDict(extra="forbid")

    owner_id: str
    account_id: str
    symbol: str = Field(min_length=1, max_length=32)
    side: Side
    volume: Decimal = Field(gt=Decimal("0"))
    entry_price: Decimal | None = Field(default=None, gt=Decimal("0"))
    stop_loss: Decimal = Field(gt=Decimal("0"))
    take_profit: Decimal = Field(gt=Decimal("0"))
    deadline_at: datetime | None = None
    note: str | None = Field(default=None, max_length=512)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("deadline_at")
    @classmethod
    def require_aware_deadline(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("deadline_at must include a timezone offset")
        return value

    @model_validator(mode="after")
    def check_protective_levels(self) -> TradeInstruction:
        reference = self.entry_price
        if reference is not None:
            if self.side is Side.BUY and not (self.stop_loss < reference < self.take_profit):
                raise ValueError("buy requires stop_loss < entry_price < take_profit")
            if self.side is Side.SELL and not (self.take_profit < reference < self.stop_loss):
                raise ValueError("sell requires take_profit < entry_price < stop_loss")
        return self


class SymbolSpec(BaseModel):
    symbol: str
    point: Decimal = Field(gt=Decimal("0"))
    digits: int = Field(ge=0, le=12)
    volume_min: Decimal = Field(gt=Decimal("0"))
    volume_max: Decimal = Field(gt=Decimal("0"))
    volume_step: Decimal = Field(gt=Decimal("0"))
    min_stop_distance_points: Decimal = Field(ge=Decimal("0"))
    trade_enabled: bool = True
    filling_modes: list[str] = Field(default_factory=list)
    trade_tick_size: Decimal | None = Field(default=None, gt=Decimal("0"))
    trade_tick_value: Decimal | None = Field(default=None, gt=Decimal("0"))


class Quote(BaseModel):
    symbol: str
    bid: Decimal = Field(gt=Decimal("0"))
    ask: Decimal = Field(gt=Decimal("0"))
    observed_at: datetime

    @model_validator(mode="after")
    def valid_spread(self) -> Quote:
        if self.ask < self.bid:
            raise ValueError("ask cannot be below bid")
        return self


class AccountSnapshot(BaseModel):
    account_id: str
    currency: str
    balance: Decimal
    equity: Decimal
    margin_free: Decimal
    is_demo: bool
    trade_allowed: bool


class PreflightResult(BaseModel):
    accepted: bool
    reason: str | None = None
    estimated_margin: Decimal | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class SubmissionResult(BaseModel):
    accepted: bool
    order_id: str | None = None
    deal_id: str | None = None
    position_id: str | None = None
    status: ExecutionStatus
    reason: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class OrderSnapshot(BaseModel):
    order_id: str
    symbol: str
    side: Side
    order_type: OrderType
    volume: Decimal
    entry_price: Decimal | None
    stop_loss: Decimal | None
    take_profit: Decimal | None
    status: str
    created_at: datetime


class PositionSnapshot(BaseModel):
    position_id: str
    symbol: str
    side: Side
    volume: Decimal
    entry_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    profit: Decimal
    opened_at: datetime


class BrokerState(BaseModel):
    orders: list[OrderSnapshot] = Field(default_factory=list)
    positions: list[PositionSnapshot] = Field(default_factory=list)


class BrokerOperationResult(BaseModel):
    status: ExecutionStatus
    order_id: str | None = None
    deal_id: str | None = None
    position_id: str | None = None
    reason: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ActionProposalView(BaseModel):
    proposal_id: str
    owner_id: str
    version: int
    action_type: ActionType
    target_kind: str
    target_id: str
    payload: dict[str, Any]
    status: ActionStatus
    confirmation_token: str
    expires_at: datetime


class ProposalView(BaseModel):
    proposal_id: str
    version: int
    status: ProposalStatus
    instruction: TradeInstruction
    order_type: OrderType
    expires_at: datetime
    validation: dict[str, Any]
    confirmation_token: str
