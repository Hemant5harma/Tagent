"""Operator-controlled renewal of a breached, flat Vantage DEMO evaluation.

This module never submits, modifies, cancels, or closes a broker order. A new
evaluation requires a separate human authorization reference on every reset.
"""

from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text

from trade_agent.db import (
    AutonomyGrantRecord,
    DashboardCacheRecord,
    ExecutionIntentRecord,
    LedgerEventRecord,
    MutationIntentRecord,
    ProposalRecord,
    SystemStateRecord,
)
from trade_agent.reporting import aware, now
from trade_agent.service import TradeService


class EvaluationBlocked(ValueError):
    """A new demo evaluation cannot be safely prepared or activated."""


LOGGER = logging.getLogger(__name__)


def _require_deployment(service: TradeService) -> None:
    cfg = service.settings
    if (
        cfg.trading_mode != "demo"
        or cfg.broker_mode != "mt5_http"
        or not cfg.assisted_demo_accepted
        or not cfg.database_url.startswith("postgresql")
        or cfg.autonomy_mode not in {"off", "shadow"}
    ):
        raise EvaluationBlocked(
            "Only the accepted PostgreSQL-backed MT5 demo deployment with autonomy off/shadow is eligible"
        )
    if (
        cfg.max_concurrent_exposures != 5
        or cfg.max_estimated_loss != Decimal("20")
        or cfg.demo_daily_loss_limit != Decimal("300")
        or cfg.demo_drawdown_limit != Decimal("1000")
        or "XAUUSD" not in cfg.allowed_symbols
    ):
        raise EvaluationBlocked("Deployment does not match the owner-approved demo limits")


def _broker_snapshot(service: TradeService) -> Any:
    cfg = service.settings
    account = service.broker.account(cfg.account_id)
    state = service.broker.state(cfg.account_id)
    if (
        account.account_id != cfg.account_id
        or not account.is_demo
        or account.currency != "USD"
        or not account.trade_allowed
    ):
        raise EvaluationBlocked("Connected account is not the expected tradable USD demo account")
    if state.positions or state.orders:
        raise EvaluationBlocked(
            "Start a new evaluation only with zero open positions and zero pending orders"
        )
    return account


def _ledger_ready(service: TradeService) -> None:
    stamp = now()
    with service.database.session() as session:
        history = session.get(DashboardCacheRecord, "history")
        if (
            history is None
            or not history.payload.get("complete")
            or (stamp - aware(history.updated_at)).total_seconds() > 120
        ):
            raise EvaluationBlocked("Broker deal history is not fresh and complete")
        unresolved = session.scalar(
            select(ExecutionIntentRecord.id).where(
                ExecutionIntentRecord.status.in_(["prepared", "unknown"])
            )
        )
        if unresolved:
            raise EvaluationBlocked("An execution outcome is unresolved")
        mutation = session.scalar(
            select(MutationIntentRecord.id).where(
                MutationIntentRecord.status.in_(["prepared", "unknown", "executing"])
            )
        )
        if mutation:
            raise EvaluationBlocked("A protection/exit outcome is unresolved")
        grant = session.scalar(
            select(AutonomyGrantRecord.id).where(
                AutonomyGrantRecord.account_id == service.settings.account_id,
                AutonomyGrantRecord.revoked_at.is_(None),
                AutonomyGrantRecord.expires_at > stamp,
            )
        )
        if grant:
            raise EvaluationBlocked("An autonomous execution grant is active")


def _fresh_gold_quote(service: TradeService) -> None:
    quote = service.broker.quote("XAUUSD")
    observed = aware(quote.observed_at)
    age = (now() - observed).total_seconds()
    if age < -5 or age > service.settings.max_quote_age_seconds:
        raise EvaluationBlocked("XAUUSD broker quote is stale; wait for a fresh market feed")
    spec = service.broker.symbol("XAUUSD")
    if not spec.trade_enabled:
        raise EvaluationBlocked("XAUUSD is not currently tradable at the broker")


def inspect(service: TradeService) -> dict[str, Any]:
    """Read-only, sanitized operator status. It never clears a breaker."""
    _require_deployment(service)
    account = _broker_snapshot(service)
    with service.database.session() as session:
        budget = session.get(SystemStateRecord, f"demo_budget:{service.settings.account_id}")
        active = session.get(SystemStateRecord, f"demo_evaluation_active:{service.settings.account_id}")
        if budget is None:
            raise EvaluationBlocked("No durable demo loss evaluation exists")
        values = json.loads(budget.value)
    return {
        "account_id": account.account_id,
        "demo": account.is_demo,
        "equity": str(account.equity),
        "evaluation_high": values["high"],
        "drawdown_from_high": str(Decimal(values["high"]) - account.equity),
        "breaker_latched": bool(values["breached"]),
        "entries_paused": service._paused(),
        "active_evaluation_ref": active.value if active else None,
    }


def prepare(service: TradeService, approval_ref: str) -> dict[str, Any]:
    """Archive one approved breach and baseline at current equity; leave entries paused."""
    _require_deployment(service)
    if not approval_ref or len(approval_ref) > 100 or not approval_ref.isascii():
        raise EvaluationBlocked("A short, explicit operator approval reference is required")
    if not service._paused():
        raise EvaluationBlocked("Entries must already be paused before an evaluation reset")
    account = _broker_snapshot(service)
    _ledger_ready(service)
    # A second broker read narrows the window for an unnoticed external change.
    second = _broker_snapshot(service)
    if second.equity != account.equity or second.balance != account.balance:
        raise EvaluationBlocked("Broker account changed during review; retry from a fresh snapshot")
    stamp = now()
    digest = hashlib.sha256(approval_ref.encode()).hexdigest()[:24]
    archive_key = f"demo_evaluation_archive:{service.settings.account_id}:{digest}"
    with service.database.session() as session:
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(830022)"))
        if session.get(SystemStateRecord, archive_key) is not None:
            raise EvaluationBlocked("This approval reference has already been used")
        budget = session.get(
            SystemStateRecord, f"demo_budget:{service.settings.account_id}", with_for_update=True
        )
        if budget is None:
            raise EvaluationBlocked("No durable demo loss evaluation exists")
        previous = json.loads(budget.value)
        if not previous.get("breached"):
            raise EvaluationBlocked("The current evaluation is not breached; no reset is needed")
        paused = session.get(SystemStateRecord, "entries_paused", with_for_update=True)
        if paused is None or paused.value != "true":
            raise EvaluationBlocked("Entries became enabled during review; no reset performed")
        session.add(
            SystemStateRecord(
                key=archive_key,
                value=json.dumps(
                    {
                        "approval_ref": approval_ref,
                        "previous_budget": previous,
                        "equity_baseline": str(account.equity),
                        "prepared_at": stamp.isoformat(),
                        "limits": {
                            "per_trade": str(service.settings.max_estimated_loss),
                            "daily": str(service.settings.demo_daily_loss_limit),
                            "drawdown": str(service.settings.demo_drawdown_limit),
                            "exposures": service.settings.max_concurrent_exposures,
                        },
                    }
                ),
                updated_at=stamp,
            )
        )
        budget.value = json.dumps(
            {
                "started_at": stamp.timestamp(),
                "high": str(account.equity),
                "day_high": str(account.equity),
                "day_start": str(account.equity),
                "date": stamp.date().isoformat(),
                "breached": False,
            }
        )
        budget.updated_at = stamp
        active_key = f"demo_evaluation_active:{service.settings.account_id}"
        active = session.get(SystemStateRecord, active_key)
        if active is None:
            session.add(SystemStateRecord(key=active_key, value=archive_key, updated_at=stamp))
        else:
            active.value, active.updated_at = archive_key, stamp
        for row in session.scalars(
            select(ProposalRecord).where(
                ProposalRecord.account_id == service.settings.account_id,
                ProposalRecord.status == "pending_confirmation",
            )
        ):
            row.status, row.updated_at = "cancelled", stamp
        session.add(
            LedgerEventRecord(
                proposal_id=None,
                execution_intent_id=None,
                kind="operator_authorized_demo_evaluation_reset",
                payload={"archive_key": archive_key, "equity_baseline": str(account.equity)},
                created_at=stamp,
            )
        )
    return {"prepared": True, "entries_paused": True, "equity_baseline": str(account.equity)}


def resume(service: TradeService) -> dict[str, Any]:
    """Enable assisted entries only after fresh broker and ledger checks."""
    _require_deployment(service)
    if not service._paused():
        raise EvaluationBlocked("Entries are already enabled; no activation performed")
    account = _broker_snapshot(service)
    _ledger_ready(service)
    _fresh_gold_quote(service)
    with service.database.session() as session:
        active = session.get(
            SystemStateRecord, f"demo_evaluation_active:{service.settings.account_id}"
        )
        budget = session.get(SystemStateRecord, f"demo_budget:{service.settings.account_id}")
        if active is None or budget is None or session.get(SystemStateRecord, active.value) is None:
            raise EvaluationBlocked("An archived, approved new evaluation is required")
        if json.loads(budget.value).get("breached"):
            raise EvaluationBlocked("The new evaluation has already breached its loss limit")
        active_ref = active.value
    from trade_agent.limits import check_demo_budget

    check_demo_budget(service, account)
    current = _broker_snapshot(service)
    if current.equity != account.equity or current.balance != account.balance:
        raise EvaluationBlocked("Broker account changed during activation; entries remain paused")
    # No reconcile() here: prepared intents could cause a broker submission.
    service.set_paused(False)
    if service.settings.telegram_owner_id:
        try:
            service._notify(
                str(service.settings.telegram_owner_id),
                "Vantage DEMO evaluation resumed after fresh broker checks. "
                "No order was placed. Each assisted trade still needs your exact Telegram Confirm trade button; "
                "autonomous trading remains off/shadow. Use /status before proposing a trade.",
                f"demo-evaluation-resumed:{active_ref}",
            )
        except Exception:
            # Notification delivery is not a reason to attempt another reset.
            LOGGER.exception("Demo evaluation resumed but Telegram notification could not be queued")
    return {"entries_enabled": not service._paused(), "broker_orders_submitted": 0}
