"""Bounded demo authorization. Model output and conversation are never permission."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from trade_agent.db import AutonomousRunRecord, AutonomyGrantRecord, ProposalRecord
from trade_agent.risk import RiskRejected

if TYPE_CHECKING:
    from trade_agent.service import TradeService

STRATEGY_VERSION = "tradingagents-mt5-v4"


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_id: str
    symbols: list[str] = Field(min_length=1, max_length=10)
    expires_at: datetime
    max_risk_amount: Decimal = Field(gt=0, allow_inf_nan=False)
    max_volume: Decimal = Field(gt=0, allow_inf_nan=False)
    max_trades_per_day: int = Field(default=3, ge=1, le=20)

    @field_validator("expires_at")
    @classmethod
    def require_zone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("grant expiry must include timezone")
        return value.astimezone(UTC)

    @field_validator("symbols")
    @classmethod
    def symbols_normalized(cls, values: list[str]) -> list[str]:
        result = list(dict.fromkeys(value.strip().upper() for value in values))
        if any(not value or len(value) > 32 for value in result):
            raise ValueError("invalid grant symbol")
        return result


def authorize_submission(
    session: Session,
    service: TradeService,
    proposal: ProposalRecord,
    grant_id: str,
    estimated_loss: Decimal | None = None,
) -> None:
    """Called under the account claim transaction and again just before the broker call."""
    cfg = service.settings
    if cfg.autonomy_mode != "demo" or cfg.trading_mode != "demo":
        raise RiskRejected("autonomous execution is disabled or is not demo mode")
    grant = session.get(AutonomyGrantRecord, grant_id, with_for_update=True)
    stamp = datetime.now(UTC)
    if grant is None or grant.revoked_at is not None or aware(grant.expires_at) <= stamp:
        raise RiskRejected("autonomy grant is missing, revoked, or expired")
    if grant.owner_id != proposal.owner_id or grant.account_id != cfg.account_id:
        raise RiskRejected("autonomy grant owner/account does not match")
    run = session.get(AutonomousRunRecord, proposal.decision_run_id)
    if (
        proposal.origin != "autonomous"
        or run is None
        or run.grant_id != grant_id
        or run.mode != "demo"
        or run.strategy_version != STRATEGY_VERSION
        or run.timeframe != cfg.autonomy_timeframe
        or run.symbol != proposal.instruction["symbol"]
        or run.owner_id != proposal.owner_id
        or run.account_id != cfg.account_id
    ):
        raise RiskRejected("proposal is outside this autonomous run's authorization")
    payload = grant.payload
    model_identity = json.dumps(
        {
            "provider": cfg.ai_provider,
            "openai_model": cfg.openai_model,
            "opencode_provider": cfg.opencode_model_provider,
            "opencode_model": cfg.opencode_model,
            "codex_model": cfg.codex_model,
        },
        sort_keys=True,
    )
    if payload.get("provider") != cfg.ai_provider or payload.get("model") != model_identity:
        raise RiskRejected("AI profile changed since the autonomy grant")
    if (
        payload.get("strategy_version") != STRATEGY_VERSION
        or payload.get("timeframe") != cfg.autonomy_timeframe
        or payload.get("trade_plans_enabled", False) != cfg.autonomy_trade_plans_enabled
    ):
        raise RiskRejected("strategy or timeframe changed since the grant")
    if proposal.instruction["symbol"] not in payload["symbols"]:
        raise RiskRejected("symbol is outside autonomy grant")
    loss = (
        estimated_loss
        if estimated_loss is not None
        else Decimal(proposal.validation["estimated_loss"])
    )
    if loss > min(
        Decimal(payload["max_risk_amount"]), cfg.autonomy_risk_amount, cfg.max_estimated_loss
    ):
        raise RiskRejected("autonomy grant estimated-loss cap exceeded")
    if Decimal(proposal.instruction["volume"]) > min(
        Decimal(payload["max_volume"]), cfg.autonomy_max_volume, cfg.max_volume
    ):
        raise RiskRejected("autonomy grant volume cap exceeded")
    # Count reserved and unknown attempts too; uncertainty never frees quota.
    start = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    used = session.scalars(
        select(ProposalRecord).where(
            ProposalRecord.account_id == cfg.account_id,
            ProposalRecord.origin == "autonomous",
            ProposalRecord.created_at >= start,
            ProposalRecord.id != proposal.id,
            ProposalRecord.status.in_(
                ["submitting", "submitted", "unknown", "closed", "deadline_completed"]
            ),
        )
    ).all()
    if len(used) >= payload["max_trades_per_day"]:
        raise RiskRejected("autonomy daily submission quota reached")
