"""Wire contracts owned by the isolated MT5 connector.

The connector deliberately keeps copies of the small HTTP contracts it needs.  It
must be installable in its own Windows Python environment and therefore never
imports the backend package.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Side = Literal["buy", "sell"]
OrderType = Literal["market", "buy_limit", "buy_stop", "sell_limit", "sell_stop"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TradeInstructionPayload(StrictModel):
    owner_id: str
    account_id: str
    symbol: str = Field(min_length=1, max_length=32)
    side: Side
    volume: Decimal = Field(gt=Decimal("0"))
    entry_price: Decimal | None = Field(default=None, gt=Decimal("0"))
    stop_loss: Decimal = Field(gt=Decimal("0"))
    take_profit: Decimal = Field(gt=Decimal("0"))
    deadline_at: datetime | None = None
    pending_expires_at: datetime | None = None
    requested_order_type: OrderType | None = None
    note: str | None = Field(default=None, max_length=512)

    @field_validator("pending_expires_at")
    @classmethod
    def expiry_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("pending expiry must include timezone")
        return value

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class PreflightRequest(StrictModel):
    instruction: TradeInstructionPayload
    order_type: OrderType


class ProfitTargetRequest(StrictModel):
    """Read-only gross-profit estimate, never an execution instruction."""

    account_id: str
    symbol: str = Field(min_length=1, max_length=32)
    side: Side
    volume: Decimal = Field(gt=0, allow_inf_nan=False)
    entry_price: Decimal = Field(gt=0, allow_inf_nan=False)
    target_usd: Decimal = Field(gt=0, le=100000, allow_inf_nan=False)


class SubmitOrderRequest(PreflightRequest):
    idempotency_key: str = Field(min_length=16, max_length=256)


class ModifyRequest(StrictModel):
    order_id: str | None = None
    position_id: str | None = None
    stop_loss: Decimal | None = Field(default=None, gt=Decimal("0"))
    take_profit: Decimal | None = Field(default=None, gt=Decimal("0"))
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=256)


class CloseRequest(StrictModel):
    volume: Decimal | None = Field(default=None, gt=Decimal("0"))
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=256)


class CancelRequest(StrictModel):
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=256)


class PreflightResponse(BaseModel):
    accepted: bool
    reason: str | None = None
    estimated_margin: Decimal | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class SubmissionResponse(BaseModel):
    accepted: bool
    order_id: str | None = None
    deal_id: str | None = None
    position_id: str | None = None
    status: Literal["prepared", "accepted", "rejected", "unknown", "cancelled", "closed"]
    reason: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class MutationResponse(BaseModel):
    """Result for modification, cancellation, and closure requests."""

    status: Literal["accepted", "rejected", "unknown"]
    reason: str | None = None
