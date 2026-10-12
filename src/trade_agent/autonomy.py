"""Recorded research runs and policy-authorized demo entries; off by default."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from trade_agent.autonomy_policy import STRATEGY_VERSION, GrantRequest, aware
from trade_agent.db import AutonomousRunRecord, AutonomyGrantRecord, ExecutionIntentRecord
from trade_agent.domain import Side, TradeInstruction
from trade_agent.service import TradeService, to_db_time

logger = logging.getLogger(__name__)


class AutonomyController:
    def __init__(
        self, service: TradeService, collector: Any = None, researcher: Any = None
    ) -> None:
        from trade_agent.intelligence import ResearchWorkflow
        from trade_agent.market_data import MarketDataCollector

        self.service = service
        self.settings = service.settings
        self.collector = collector or MarketDataCollector(
            service.broker, self.settings.max_quote_age_seconds
        )
        self.researcher = researcher or ResearchWorkflow(self.settings)

    def _owner(self, owner_id: str) -> None:
        if not self.settings.telegram_owner_id or owner_id != str(self.settings.telegram_owner_id):
            raise ValueError("autonomy requires the configured trading owner")

    def create_grant(self, request: GrantRequest) -> dict:
        self._owner(request.owner_id)
        cfg = self.settings
        stamp = datetime.now(UTC)
        if cfg.autonomy_mode != "demo" or cfg.trading_mode != "demo":
            raise ValueError("set autonomy mode to demo before authorizing demo execution")
        if not stamp < request.expires_at <= stamp + timedelta(hours=24):
            raise ValueError("grant must expire within the next 24 hours")
        if not set(request.symbols) <= set(cfg.allowed_symbols):
            raise ValueError("grant cannot add symbols to the deployment allowlist")
        if request.max_risk_amount > min(cfg.autonomy_risk_amount, cfg.max_estimated_loss):
            raise ValueError("grant cannot increase configured estimated-loss limits")
        if request.max_volume > min(cfg.autonomy_max_volume, cfg.max_volume):
            raise ValueError("grant cannot increase configured volume limits")
        account = self.service.broker.account(cfg.account_id)
        if not account.is_demo or not account.trade_allowed:
            raise ValueError("connected account must allow demo trading")
        if cfg.broker_mode != "fake" and (
            not cfg.assisted_demo_accepted or account.currency != "USD"
        ):
            raise ValueError("connected autonomy requires the accepted USD demo deployment")
        payload = request.model_dump(mode="json")
        payload.update(
            strategy_version=STRATEGY_VERSION,
            timeframe=cfg.autonomy_timeframe,
            provider=cfg.ai_provider,
            model=self._model_identity(),
            environment="demo",
            account_currency=account.currency,
            trade_plans_enabled=cfg.autonomy_trade_plans_enabled,
        )
        row = AutonomyGrantRecord(
            id=str(uuid4()),
            owner_id=request.owner_id,
            account_id=cfg.account_id,
            payload=payload,
            expires_at=request.expires_at,
            revoked_at=None,
            created_at=stamp,
        )
        with self.service.database.session() as session:
            # A new grant replaces older grants, rather than stacking their permissions.
            for old in session.scalars(
                select(AutonomyGrantRecord).where(
                    AutonomyGrantRecord.account_id == cfg.account_id,
                    AutonomyGrantRecord.revoked_at.is_(None),
                )
            ):
                old.revoked_at = stamp
            session.add(row)
        self.service._event(None, None, "autonomy_granted", {"grant_id": row.id, **payload})
        return self._grant_view(row)

    def _model_identity(self) -> str:
        cfg = self.settings
        return json.dumps(
            {
                "provider": cfg.ai_provider,
                "openai_model": cfg.openai_model,
                "opencode_provider": cfg.opencode_model_provider,
                "opencode_model": cfg.opencode_model,
                "codex_model": cfg.codex_model,
            },
            sort_keys=True,
        )

    @staticmethod
    def _grant_view(row: AutonomyGrantRecord) -> dict:
        return {
            "id": row.id,
            "account_id": row.account_id,
            **row.payload,
            "expires_at": aware(row.expires_at).isoformat(),
            "revoked": row.revoked_at is not None,
        }

    def revoke(self, owner_id: str) -> dict:
        self._owner(owner_id)
        with self.service.database.session() as session:
            rows = session.scalars(
                select(AutonomyGrantRecord).where(
                    AutonomyGrantRecord.account_id == self.settings.account_id,
                    AutonomyGrantRecord.owner_id == owner_id,
                    AutonomyGrantRecord.revoked_at.is_(None),
                )
            ).all()
            for row in rows:
                row.revoked_at = datetime.now(UTC)
        self.service._event(None, None, "autonomy_revoked", {"owner_id": owner_id})
        return {"revoked": len(rows), "protective_exits_continue": True}

    def active_grant(self, owner_id: str) -> AutonomyGrantRecord | None:
        with self.service.database.session() as session:
            row = session.scalar(
                select(AutonomyGrantRecord)
                .where(
                    AutonomyGrantRecord.account_id == self.settings.account_id,
                    AutonomyGrantRecord.owner_id == owner_id,
                    AutonomyGrantRecord.revoked_at.is_(None),
                    AutonomyGrantRecord.expires_at > datetime.now(UTC),
                )
                .order_by(AutonomyGrantRecord.created_at.desc())
            )
            return row

    def status(self) -> dict:
        from trade_agent.autonomy_health import data_health
        from trade_agent.monitoring import worker_status

        owner = str(self.settings.telegram_owner_id or "")
        grant = self.active_grant(owner)
        return {
            "mode": self.settings.autonomy_mode,
            "strategy_version": STRATEGY_VERSION,
            "timeframe": self.settings.autonomy_timeframe,
            "provider": self.settings.ai_provider,
            "symbols": self.settings.allowed_symbols,
            "entries_paused": self.service._paused(),
            "active_grant": self._grant_view(grant) if grant else None,
            "worker": worker_status(self.service.database, self.settings.account_id),
            "market_data": data_health(self.service.database, self.settings.account_id),
            "note": "Shadow never sends orders. Demo needs a grant and passing execution checks. Protective exits are independent.",
        }

    def runs(self, limit: int = 30) -> list[dict]:
        with self.service.database.session() as session:
            rows = session.scalars(
                select(AutonomousRunRecord)
                .where(
                    AutonomousRunRecord.account_id == self.settings.account_id,
                    AutonomousRunRecord.snapshot["data_status"].as_string().is_(None),
                )
                .order_by(AutonomousRunRecord.created_at.desc())
                .limit(min(max(limit, 1), 100))
            ).all()
        return [self._run_view(row) for row in rows]

    @staticmethod
    def _run_view(row: AutonomousRunRecord) -> dict:
        return {
            "id": row.id,
            "symbol": row.symbol,
            "timeframe": row.timeframe,
            "bar_at": aware(row.bar_at).isoformat(),
            "strategy_version": row.strategy_version,
            "mode": row.mode,
            "status": row.status,
            "snapshot": row.snapshot,
            "reports": row.reports,
            "decision": row.decision,
            "proposal_id": row.proposal_id,
            "grant_id": row.grant_id,
            "error": row.error,
            "created_at": aware(row.created_at).isoformat(),
        }

    def _finish(
        self,
        run_id: str,
        status: str,
        *,
        decision: dict | None = None,
        reports: list | None = None,
        error: str | None = None,
        proposal_id: str | None = None,
    ) -> dict:
        with self.service.database.session() as session:
            row = session.get(AutonomousRunRecord, run_id)
            assert row is not None
            row.status, row.error, row.updated_at = status, error, datetime.now(UTC)
            if decision is not None:
                row.decision = decision
            if reports is not None:
                row.reports = reports
            if proposal_id is not None:
                row.proposal_id = proposal_id
            result = self._run_view(row)
            mode, owner = row.mode, row.owner_id
        if mode == "demo" and status == "blocked":
            from trade_agent.reporting import scrub

            self.service._notify(
                owner,
                f"Autonomous demo entry blocked. Run {run_id}: {scrub(error or 'review required')}",
                f"autonomy-blocked:{run_id}",
            )
        return result

    async def run(self, owner_id: str, symbol: str, *, mode: str = "shadow") -> dict:
        self._owner(owner_id)
        symbol = symbol.strip().upper()
        if mode == "demo" and self.settings.autonomy_comparison_enabled:
            raise ValueError("timeframe comparison is shadow-only; no execution path is enabled")
        if symbol not in self.settings.allowed_symbols or mode not in {"shadow", "demo"}:
            raise ValueError("symbol or research mode is not allowed")
        if mode == "demo" and self.settings.autonomy_mode != "demo":
            raise ValueError("autonomous demo execution is disabled")
        grant = self.active_grant(owner_id) if mode == "demo" else None
        if mode == "demo" and (grant is None or symbol not in grant.payload["symbols"]):
            raise ValueError("an active grant for this symbol is required")
        stamp = datetime.now(UTC)
        from trade_agent.autonomy_health import record_data_health

        try:
            snapshot = await asyncio.to_thread(
                self.collector.collect,
                symbol,
                self.settings.autonomy_timeframe,
                self.settings.autonomy_bars,
            )
            bar_at = aware(datetime.fromisoformat(snapshot["closed_bar_at"]))
        except Exception as exc:
            health = record_data_health(
                self.service.database,
                self.settings.account_id,
                symbol,
                available=False,
                code=getattr(exc, "code", "market_data_unavailable"),
                quote_at=getattr(exc, "quote_at", None),
            )
            # No closed candle was analyzed. Update a single outage observation,
            # not a new research/WAIT ledger row on every scheduler tick.
            return {
                "id": None,
                "symbol": symbol,
                "mode": mode,
                "status": "data_unavailable",
                "decision": {"direction": "wait", "reason": health["reason"]},
                "reports": [],
                "market_data": health,
            }
        record_data_health(
            self.service.database,
            self.settings.account_id,
            symbol,
            available=True,
            quote_at=snapshot.get("quote", {}).get("observed_at"),
        )
        row = AutonomousRunRecord(
            id=str(uuid4()),
            account_id=self.settings.account_id,
            owner_id=owner_id,
            symbol=symbol,
            timeframe=self.settings.autonomy_timeframe,
            bar_at=to_db_time(bar_at),
            strategy_version=STRATEGY_VERSION,
            mode=mode,
            status="analyzing",
            snapshot=snapshot,
            reports=[],
            decision={},
            proposal_id=None,
            grant_id=grant.id if grant else None,
            error=None,
            created_at=stamp,
            updated_at=stamp,
        )
        try:
            with self.service.database.session() as session:
                session.add(row)
        except IntegrityError:
            with self.service.database.session() as session:
                existing = session.scalar(
                    select(AutonomousRunRecord).where(
                        AutonomousRunRecord.account_id == row.account_id,
                        AutonomousRunRecord.strategy_version == row.strategy_version,
                        AutonomousRunRecord.symbol == symbol,
                        AutonomousRunRecord.timeframe == row.timeframe,
                        AutonomousRunRecord.bar_at == row.bar_at,
                    )
                )
                assert existing is not None
                return {**self._run_view(existing), "duplicate": True}
        decision: dict = {}
        reports: list = []
        try:
            account = await asyncio.to_thread(self.service.broker.account, self.settings.account_id)
            book = await asyncio.to_thread(self.service.broker.state, self.settings.account_id)
            snapshot["portfolio"] = {
                "balance": str(account.balance),
                "equity": str(account.equity),
                "currency": account.currency,
                "positions": [p.model_dump(mode="json") for p in book.positions],
                "orders": [p.model_dump(mode="json") for p in book.orders],
            }
            if self.settings.autonomy_context_enabled:
                from trade_agent.free_context import FreeContextCollector

                try:
                    snapshot["context"] = await asyncio.wait_for(
                        FreeContextCollector(
                            enabled=True, fred_api_key=self.settings.fred_api_key
                        ).collect(),
                        25,
                    )
                except Exception:
                    snapshot["context"] = {
                        "status": "unavailable",
                        "reason": "context collection failed",
                    }
            else:
                snapshot["context"] = {
                    "status": "disabled",
                    "reason": "technical-only research; no news/calendar coverage claimed",
                }
            context = snapshot["context"]
            snapshot["evidence"] = list(snapshot.get("evidence", [])) + list(
                context.get("evidence", [])
            )
            snapshot["macro_available"] = any(
                str(e.get("source", "")).startswith("FRED ") for e in context.get("evidence", [])
            )
            snapshot["news_available"] = any(
                str(e.get("source", "")).startswith("https://") for e in context.get("evidence", [])
            )
            if self.settings.autonomy_trade_plans_enabled:
                from trade_agent.trade_plans import build_plans

                higher = {}
                context_frames = (
                    ("H1",) if self.settings.autonomy_comparison_enabled else ("H1", "H4")
                )
                for timeframe in context_frames:
                    try:
                        higher[timeframe] = await asyncio.to_thread(
                            self.collector.collect, symbol, timeframe, self.settings.autonomy_bars
                        )
                    except Exception:
                        higher[timeframe] = None
                # Full context is archived; only calculated summaries go to AI/UI.
                snapshot["higher_timeframe_bars"] = higher
                try:
                    spec = await asyncio.to_thread(self.service.broker.symbol, symbol)
                    snapshot["trade_plans"] = build_plans(
                        snapshot, higher, spec, self.settings, context_timeframes=context_frames
                    )
                    if self.settings.autonomy_comparison_enabled:
                        from trade_agent.timeframe_comparison import (
                            COMPARISON_COUNTS,
                            compare_timeframes,
                        )

                        frames = {}
                        for tf, count in COMPARISON_COUNTS.items():
                            try:
                                frames[tf] = (
                                    snapshot
                                    if tf == snapshot["timeframe"]
                                    and len(snapshot["bars"]) == count
                                    else await asyncio.to_thread(
                                        self.collector.collect, symbol, tf, count
                                    )
                                )
                            except Exception:
                                frames[tf] = None
                        comparison = compare_timeframes(
                            frames, higher, spec, self.settings, snapshot["closed_bar_at"]
                        )
                        snapshot["timeframe_comparison"] = comparison
                        # Archive only additional frames; avoid a self-reference.
                        snapshot["comparison_bars"] = {
                            tf: value for tf, value in frames.items() if value is not snapshot
                        }
                        snapshot["evidence"].append(
                            {
                                "id": "timeframe-comparison",
                                "source": "code-calculated M5/M15/M30 shadow comparison",
                                "observed_at": snapshot["as_of"],
                                "text": json.dumps(
                                    {
                                        "context": "H1",
                                        "coverage": comparison["coverage"],
                                        "frames": [
                                            {
                                                "timeframe": r["timeframe"],
                                                "status": r["status"],
                                                "ready_directions": r.get("ready_directions", []),
                                                "plans": [
                                                    {
                                                        "side": p["side"],
                                                        "status": p["status"],
                                                        "blockers": p["blockers"],
                                                    }
                                                    for p in r["plans"]
                                                ],
                                            }
                                            for r in comparison["frames"]
                                        ],
                                    }
                                ),
                            }
                        )
                except Exception as exc:
                    snapshot["trade_plans"] = {
                        "plans": [],
                        "error": f"Planning unavailable ({type(exc).__name__})",
                    }
                snapshot["evidence"].append(
                    {
                        "id": "trade-plans",
                        "source": "code-calculated trade hypotheses",
                        "observed_at": snapshot["as_of"],
                        "text": json.dumps(snapshot["trade_plans"]),
                    }
                )
            with self.service.database.session() as session:
                stored = session.get(AutonomousRunRecord, row.id)
                assert stored is not None
                stored.snapshot = snapshot
            if self.settings.autonomy_trade_plans_enabled:
                plans = snapshot.get("trade_plans", {})
                if plans.get("error") or not plans.get("plans"):
                    return self._finish(
                        row.id,
                        "research_failed",
                        decision={
                            "direction": "wait",
                            "confidence": 0,
                            "reason": "Deterministic trade planning is unavailable; no entry authorized",
                            "execution_gate": "Trade planning unavailable; model not invoked",
                            "trade_plans": plans,
                            "timeframe_comparison": snapshot.get("timeframe_comparison", {}),
                        },
                    )
                if not any(plan.get("status") == "ready" for plan in plans["plans"]):
                    return self._finish(
                        row.id,
                        "wait",
                        decision={
                            "direction": "wait",
                            "confidence": 0,
                            "reason": "No deterministic trade setup meets the configured broker and risk checks",
                            "execution_gate": "No ready trade plan; model not invoked",
                            "trade_plans": plans,
                            "timeframe_comparison": snapshot.get("timeframe_comparison", {}),
                        },
                    )
            decision = await self.researcher.analyze(snapshot)
            reports = decision.get("reports", [])
            direction = decision.get("direction", "wait")
            decision["trade_plans"] = snapshot.get("trade_plans", {})
            decision["timeframe_comparison"] = snapshot.get("timeframe_comparison", {})
            if str(decision.get("reason", "")).startswith("Research abstained:") or any(
                item.get("status") == "failed"
                for item in decision.get("research_health", {}).values()
            ):
                return self._finish(
                    row.id,
                    "research_failed",
                    decision={
                        **decision,
                        "execution_gate": "Research did not complete; no entry authorized",
                    },
                    reports=reports,
                )
            if (
                direction not in {"buy", "sell"}
                or float(decision.get("confidence", 0)) < self.settings.autonomy_min_confidence
            ):
                return self._finish(
                    row.id,
                    "wait",
                    decision={
                        **decision,
                        "execution_gate": "Model abstained"
                        if direction not in {"buy", "sell"}
                        else "Model confidence below the configured threshold",
                    },
                    reports=reports,
                )
            if self.settings.autonomy_trade_plans_enabled:
                plan = next(
                    (
                        p
                        for p in snapshot["trade_plans"].get("plans", [])
                        if p["side"] == direction and p["status"] == "ready"
                    ),
                    None,
                )
                if plan is None:
                    return self._finish(
                        row.id,
                        "wait",
                        decision={
                            **decision,
                            "execution_gate": "Trade-plan conditions are not satisfied",
                        },
                        reports=reports,
                    )
            # A language model may abstain, but cannot invent the registered setup.
            indicators = snapshot["indicators"]
            fast, slow = Decimal(str(indicators["ema_fast"])), Decimal(str(indicators["ema_slow"]))
            rsi = Decimal(str(indicators["rsi"]))
            agrees = (direction == "buy" and fast > slow and 50 <= rsi <= 75) or (
                direction == "sell" and fast < slow and 25 <= rsi <= 50
            )
            if not agrees:
                return self._finish(
                    row.id,
                    "wait",
                    decision={
                        **decision,
                        "direction": "wait",
                        "reason": "Registered EMA/RSI setup does not support the proposed direction",
                    },
                    reports=reports,
                )
            if mode == "shadow":
                instruction = await asyncio.to_thread(
                    self._instruction, owner_id, symbol, direction, snapshot, None
                )
                return self._finish(
                    row.id,
                    "shadow",
                    decision={
                        **decision,
                        "hypothetical_instruction": instruction.model_dump(mode="json"),
                    },
                    reports=reports,
                )
            if self.service._paused():
                raise ValueError("new entries are paused")
            if snapshot.get("data_mode") == "synthetic" and self.settings.broker_mode != "fake":
                raise ValueError("synthetic evidence cannot authorize connected execution")
            if any(p.symbol == symbol for p in book.positions) or any(
                p.symbol == symbol for p in book.orders
            ):
                raise ValueError(
                    "existing same-symbol exposure is not changed or added to by this strategy"
                )
            with self.service.database.session() as session:
                unresolved = session.scalar(
                    select(ExecutionIntentRecord).where(
                        ExecutionIntentRecord.status.in_(["prepared", "unknown"])
                    )
                )
                if unresolved:
                    raise ValueError("an unresolved broker submission blocks autonomous entries")
            instruction = await asyncio.to_thread(
                self._instruction, owner_id, symbol, direction, snapshot, grant
            )
            proposal = await asyncio.to_thread(
                self.service.create_proposal,
                instruction,
                origin="autonomous",
                strategy_version=STRATEGY_VERSION,
                decision_run_id=row.id,
            )
            self._finish(
                row.id,
                "proposed",
                decision=decision,
                reports=reports,
                proposal_id=proposal.proposal_id,
            )
            assert grant is not None
            result = await asyncio.to_thread(
                self.service.confirm_autonomous, proposal.proposal_id, owner_id, grant.id
            )
            self.service._notify(
                owner_id,
                f"Autonomous demo {symbol}: {result['status']}. Run {row.id}. {result.get('message', '')}",
                f"autonomy:{row.id}",
            )
            return self._finish(
                row.id,
                result["status"],
                decision={**decision, "execution": result},
                reports=reports,
            )
        except Exception as exc:
            # Never retry a failed/uncertain bar automatically. The ledger remains authoritative.
            return self._finish(
                row.id,
                "blocked",
                decision=decision,
                reports=reports,
                error=f"{type(exc).__name__}: {str(exc)[:400]}",
            )

    def _instruction(
        self,
        owner_id: str,
        symbol: str,
        direction: str,
        snapshot: dict,
        grant: AutonomyGrantRecord | None,
    ) -> TradeInstruction:
        cfg = self.settings
        spec, quote = self.service.broker.symbol(symbol), self.service.broker.quote(symbol)
        tick, value = spec.trade_tick_size, spec.trade_tick_value
        if tick is None or value is None:
            raise ValueError("broker tick size/value needed for deterministic sizing")
        atr = Decimal(str(snapshot["indicators"]["atr"]))
        if not atr.is_finite() or atr <= 0:
            raise ValueError("ATR is unavailable")
        distance = max(
            atr * cfg.autonomy_stop_atr,
            spec.point * (spec.min_stop_distance_points + cfg.max_slippage_points + 1),
        )
        reference = quote.ask if direction == "buy" else quote.bid
        sign = Decimal(1) if direction == "buy" else Decimal(-1)
        sl = ((reference - sign * distance) / tick).to_integral_value(
            rounding=ROUND_FLOOR if direction == "buy" else ROUND_CEILING
        ) * tick
        tp = ((reference + sign * distance * cfg.autonomy_reward_risk) / tick).to_integral_value(
            rounding=ROUND_CEILING if direction == "buy" else ROUND_FLOOR
        ) * tick
        if cfg.autonomy_trade_plans_enabled:
            plan = next(
                (
                    p
                    for p in snapshot.get("trade_plans", {}).get("plans", [])
                    if p["side"] == direction and p["status"] == "ready"
                ),
                None,
            )
            if plan is None or datetime.fromisoformat(plan["expires_at"]) <= datetime.now(UTC):
                raise ValueError("no unexpired ready trade plan")
            age = (datetime.now(UTC) - aware(quote.observed_at)).total_seconds()
            if age < -5 or age > cfg.max_quote_age_seconds:
                raise ValueError("trade-plan execution quote is stale")
            if abs(reference - Decimal(plan["entry"])) > atr * Decimal("0.25"):
                raise ValueError("price moved beyond the planned entry tolerance")
            sl, tp = Decimal(plan["stop_loss"]), Decimal(plan["take_profit"])
            risk = sign * (reference - sl) + cfg.max_slippage_points * spec.point
            reward = sign * (tp - reference) - cfg.max_slippage_points * spec.point
            minimum = spec.point * (spec.min_stop_distance_points + cfg.max_slippage_points + 1)
            if (
                risk <= 0
                or reward / risk < cfg.autonomy_reward_risk
                or sign * (reference - sl) < minimum + quote.ask - quote.bid
                or sign * (tp - reference) < minimum + quote.ask - quote.bid
            ):
                raise ValueError("fresh quote invalidates planned stop/target or reward/risk")
        budget = min(
            cfg.autonomy_risk_amount,
            cfg.max_estimated_loss,
            Decimal(grant.payload["max_risk_amount"]) if grant else cfg.autonomy_risk_amount,
        )
        # Reserve the permitted adverse quote movement before estimating lot size.
        per_lot = (abs(reference - sl) + cfg.max_slippage_points * spec.point) / tick * value
        cap = min(
            budget / per_lot,
            cfg.autonomy_max_volume,
            cfg.max_volume,
            spec.volume_max,
            Decimal(grant.payload["max_volume"]) if grant else cfg.autonomy_max_volume,
        )
        if cap < spec.volume_min:
            raise ValueError("risk budget is smaller than the broker minimum lot")
        volume = (
            spec.volume_min
            + ((cap - spec.volume_min) / spec.volume_step).to_integral_value(rounding=ROUND_FLOOR)
            * spec.volume_step
        )
        return TradeInstruction(
            owner_id=owner_id,
            account_id=cfg.account_id,
            symbol=symbol,
            side=Side(direction),
            volume=volume,
            stop_loss=sl,
            take_profit=tp,
            deadline_at=datetime.now(UTC) + timedelta(minutes=cfg.autonomy_max_holding_minutes),
            note=f"Autonomous {STRATEGY_VERSION}; closed bar {snapshot['closed_bar_at']}",
        )


async def scheduled(controller: AutonomyController) -> None:
    from trade_agent.reporting import put_cache

    cfg = controller.settings
    observation: dict = {
        "account_id": cfg.account_id,
        "status": "running",
        "mode": cfg.autonomy_mode,
        "strategy_version": STRATEGY_VERSION,
        "phase": "starting",
        "last_cycle_at": None,
        "comparison_enabled": cfg.autonomy_comparison_enabled,
        "comparison_timeframes": ["M5", "M15", "M30"] if cfg.autonomy_comparison_enabled else [],
    }

    async def heartbeat() -> None:
        while True:
            try:
                await asyncio.to_thread(
                    put_cache, controller.service.database, "autonomy", dict(observation)
                )
            except Exception:
                logger.exception("autonomy heartbeat write failed")
            await asyncio.sleep(10)

    task = asyncio.create_task(heartbeat())
    try:
        while True:
            observation["last_cycle_at"] = datetime.now(UTC).isoformat()
            observation["entries_paused"] = controller.service._paused()
            observation["active_grant"] = (
                controller.active_grant(str(cfg.telegram_owner_id or "")) is not None
            )
            if cfg.autonomy_mode != "off" and cfg.telegram_owner_id:
                for symbol in cfg.allowed_symbols:
                    observation.update(phase="research", symbol=symbol)
                    try:
                        result = await controller.run(
                            str(cfg.telegram_owner_id),
                            symbol,
                            mode="demo" if cfg.autonomy_mode == "demo" else "shadow",
                        )
                        observation["last_result"] = result["status"]
                    except Exception:
                        observation["last_result"] = "blocked_before_research"
                        logger.exception("autonomy cycle stopped before entry")
            observation["phase"] = (
                "awaiting_market_data"
                if observation.get("last_result") == "data_unavailable"
                else "waiting"
            )
            await asyncio.sleep(cfg.autonomy_interval_seconds)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        observation.update(status="stopped", phase="stopped")
        await asyncio.to_thread(
            put_cache, controller.service.database, "autonomy", dict(observation)
        )


def main() -> None:
    from trade_agent.logging_config import configure_logging
    from trade_agent.main import build_runtime

    facade, _ = build_runtime()
    configure_logging(facade.settings)
    if facade.settings.autonomy_mode == "off":
        raise SystemExit(
            "Set TRADE_AGENT_AUTONOMY_MODE=shadow to begin research; demo also needs a grant."
        )
    asyncio.run(scheduled(AutonomyController(facade.service)))


if __name__ == "__main__":
    main()
