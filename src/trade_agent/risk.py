"""Deterministic safety checks; deliberately independent of any LLM."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from trade_agent.broker import BrokerAdapter
from trade_agent.config import Settings
from trade_agent.domain import AccountSnapshot, OrderType, Quote, Side, SymbolSpec, TradeInstruction


class RiskRejected(ValueError):
    """A request is well formed but violates application or broker limits."""


@dataclass(frozen=True)
class ValidatedTrade:
    instruction: TradeInstruction
    order_type: OrderType
    account: AccountSnapshot
    spec: SymbolSpec
    quote: Quote
    estimated_margin: Decimal | None
    estimated_loss: Decimal = Decimal("0")

    def summary(self) -> dict[str, str | bool | None]:
        return {
            "order_type": self.order_type.value,
            "bid": str(self.quote.bid),
            "ask": str(self.quote.ask),
            "estimated_margin": str(self.estimated_margin) if self.estimated_margin else None,
            "account_is_demo": self.account.is_demo,
            "account_id": self.account.account_id,
            "currency": self.account.currency,
            "estimated_loss": str(self.estimated_loss),
        }


class RiskPolicy:
    def __init__(self, settings: Settings, broker: BrokerAdapter) -> None:
        self.settings = settings
        self.broker = broker

    @staticmethod
    def _is_step(value: Decimal, minimum: Decimal, step: Decimal) -> bool:
        units = (value - minimum) / step
        return units >= 0 and units == units.to_integral_value()

    @staticmethod
    def resolve_order_type(instruction: TradeInstruction, quote: Quote) -> OrderType:
        if instruction.entry_price is None:
            return OrderType.MARKET
        entry = instruction.entry_price
        if instruction.side is Side.BUY:
            if entry < quote.ask:
                return OrderType.BUY_LIMIT
            if entry > quote.ask:
                return OrderType.BUY_STOP
            raise RiskRejected("entry equals ask; explicitly request a market order instead")
        if entry > quote.bid:
            return OrderType.SELL_LIMIT
        if entry < quote.bid:
            return OrderType.SELL_STOP
        raise RiskRejected("entry equals bid; explicitly request a market order instead")

    def validate(self, instruction: TradeInstruction) -> ValidatedTrade:
        if instruction.account_id != self.settings.account_id:
            raise RiskRejected("instruction account does not match configured account")
        if instruction.symbol not in self.settings.allowed_symbols:
            raise RiskRejected(f"symbol {instruction.symbol} is not in the configured allowlist")
        account = self.broker.account(instruction.account_id)
        expected_demo = self.settings.trading_mode == "demo"
        if account.is_demo != expected_demo:
            mode = "demo" if expected_demo else "live"
            raise RiskRejected(f"configured {mode} mode does not match connected account")
        if not account.trade_allowed:
            raise RiskRejected("broker account does not allow trading")
        spec = self.broker.symbol(instruction.symbol)
        quote = self.broker.quote(instruction.symbol)
        observed = (
            quote.observed_at.replace(tzinfo=UTC)
            if quote.observed_at.tzinfo is None
            else quote.observed_at
        )
        age = (datetime.now(UTC) - observed).total_seconds()
        if age < -5 or age > self.settings.max_quote_age_seconds:
            raise RiskRejected("quote is stale or clock is inconsistent")
        if not spec.trade_enabled:
            raise RiskRejected("instrument is disabled by broker")
        if instruction.volume > self.settings.max_volume:
            raise RiskRejected("volume exceeds configured maximum")
        if not spec.volume_min <= instruction.volume <= spec.volume_max:
            raise RiskRejected("volume is outside broker minimum/maximum")
        if not self._is_step(instruction.volume, spec.volume_min, spec.volume_step):
            raise RiskRejected("volume does not match broker lot increment")
        spread_points = (quote.ask - quote.bid) / spec.point
        if spread_points > self.settings.max_spread_points:
            raise RiskRejected("current spread exceeds configured maximum")
        connector_deviation = self.broker.capabilities().get("max_slippage_points")
        if (
            isinstance(connector_deviation, int)
            and not isinstance(connector_deviation, bool)
            and Decimal(connector_deviation) > self.settings.max_slippage_points
        ):
            raise RiskRejected("connector deviation exceeds configured slippage limit")
        reference = instruction.entry_price or (
            quote.ask if instruction.side is Side.BUY else quote.bid
        )
        tick_size = spec.trade_tick_size or spec.point
        for label, value in (
            ("entry", reference),
            ("stop loss", instruction.stop_loss),
            ("take profit", instruction.take_profit),
        ):
            units = value / tick_size
            if units != units.to_integral_value():
                raise RiskRejected(f"{label} does not align with broker price tick")
        if instruction.side is Side.BUY:
            correct = instruction.stop_loss < reference < instruction.take_profit
        else:
            correct = instruction.take_profit < reference < instruction.stop_loss
        if not correct:
            raise RiskRejected("SL/TP direction is invalid for current trade reference price")
        stop_distance = abs(reference - instruction.stop_loss) / spec.point
        target_distance = abs(instruction.take_profit - reference) / spec.point
        if min(stop_distance, target_distance) < spec.min_stop_distance_points:
            raise RiskRejected("SL or TP is closer than the broker minimum stop distance")
        state = self.broker.state(instruction.account_id)
        if self.settings.broker_mode != "fake" and len(state.positions) + len(state.orders) >= 1:
            raise RiskRejected("Assisted demo allows only one position or pending entry at a time")
        if len(state.positions) >= self.settings.max_open_positions:
            raise RiskRejected("maximum configured open position count reached")
        if len(state.orders) >= self.settings.max_pending_orders:
            raise RiskRejected("maximum configured pending order count reached")
        active_volume = sum((item.volume for item in state.orders), start=Decimal("0")) + sum(
            (item.volume for item in state.positions), start=Decimal("0")
        )
        if active_volume + instruction.volume > self.settings.max_total_volume:
            raise RiskRejected("aggregate account volume exceeds configured maximum")
        if spec.trade_tick_size is None or spec.trade_tick_value is None:
            raise RiskRejected("broker did not provide tick value needed for loss validation")
        estimated_loss = (
            abs(reference - instruction.stop_loss)
            / spec.trade_tick_size
            * spec.trade_tick_value
            * instruction.volume
        )
        if estimated_loss > self.settings.max_estimated_loss:
            raise RiskRejected("estimated stop-loss amount exceeds configured maximum")
        order_type = self.resolve_order_type(instruction, quote)
        preflight = self.broker.preflight(instruction, order_type)
        if not preflight.accepted:
            raise RiskRejected(preflight.reason or "broker preflight rejected request")
        if (
            preflight.estimated_margin is not None
            and account.margin_free - preflight.estimated_margin
            < self.settings.min_free_margin_after_trade
        ):
            raise RiskRejected("trade would leave less than configured free margin")
        return ValidatedTrade(
            instruction=instruction,
            order_type=order_type,
            account=account,
            spec=spec,
            quote=quote,
            estimated_margin=preflight.estimated_margin,
            estimated_loss=estimated_loss,
        )
