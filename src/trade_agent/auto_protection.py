"""Deterministic, opt-in demo proposal defaults; no execution authority."""

from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from trade_agent.ai import ParsedEntry, TradeProposalParse
from trade_agent.broker import BrokerAdapter
from trade_agent.config import Settings


def complete_protection(
    parsed: TradeProposalParse, settings: Settings, broker: BrokerAdapter
) -> TradeProposalParse:
    if not settings.auto_demo_protection or (
        parsed.entry.kind != "missing" and parsed.stop_loss is not None
    ):
        return parsed
    if not parsed.symbol or not parsed.side or parsed.volume is None:
        return parsed
    if settings.trading_mode != "demo" or parsed.symbol not in settings.allowed_symbols:
        raise ValueError(
            "Automatic protection requires an allowed symbol on the configured demo account"
        )
    account = broker.account(settings.account_id)
    if (
        account.account_id != settings.account_id
        or not account.is_demo
        or account.currency != "USD"
    ):
        raise ValueError("Automatic protection requires the configured USD demo account")
    quote, spec = broker.quote(parsed.symbol), broker.symbol(parsed.symbol)
    observed = (
        quote.observed_at.replace(tzinfo=UTC)
        if quote.observed_at.tzinfo is None
        else quote.observed_at
    )
    if not -5 <= (datetime.now(UTC) - observed).total_seconds() <= settings.max_quote_age_seconds:
        raise ValueError("A fresh broker quote is required for automatic protection")
    tick, value = spec.trade_tick_size, spec.trade_tick_value
    if (
        tick is None
        or value is None
        or not tick.is_finite()
        or not value.is_finite()
        or tick <= 0
        or value <= 0
    ):
        raise ValueError(
            "Broker tick size and loss tick value are required for automatic protection"
        )
    if (
        not spec.volume_min <= parsed.volume <= spec.volume_max
        or (parsed.volume - spec.volume_min) % spec.volume_step
    ):
        raise ValueError("Requested lot size does not match broker limits")
    entry = ParsedEntry(kind="market") if parsed.entry.kind == "missing" else parsed.entry
    reference = (
        entry.price if entry.kind == "price" else (quote.ask if parsed.side == "buy" else quote.bid)
    )
    if reference is None or reference <= 0 or reference % tick or quote.ask < quote.bid:
        raise ValueError("Invalid broker price for automatic protection")
    stop = parsed.stop_loss
    notes = []
    if parsed.entry.kind == "missing":
        notes.append("Proposed default: market entry at the current broker quote")
    if stop is None:
        budget = min(Decimal("10"), settings.max_estimated_loss)
        ticks = (budget / (value * parsed.volume)).to_integral_value(rounding=ROUND_FLOOR)
        if entry.kind == "market":
            # The risk validator obtains a fresh quote. Reserve the full
            # configured quote movement tolerance plus one price tick so a
            # slightly newer market quote cannot push a $10 stop over $10.
            buffer_ticks = (settings.max_slippage_points * spec.point / tick).to_integral_value(
                rounding=ROUND_CEILING
            ) + 1
            ticks -= buffer_ticks
        distance = ticks * tick
        stop = reference - distance if parsed.side == "buy" else reference + distance
        # Market stops trigger on the opposite quote: allow for spread, not just entry distance.
        trigger = (
            reference
            if entry.kind == "price"
            else (quote.bid if parsed.side == "buy" else quote.ask)
        )
        available = trigger - stop if parsed.side == "buy" else stop - trigger
        if (
            ticks < 1
            or stop <= 0
            or available < max(tick, spec.min_stop_distance_points * spec.point)
        ):
            raise ValueError(
                "Requested lot size cannot fit an SL within the $10 risk cap and broker spread/stop distance. Choose a smaller size; no size was changed automatically"
            )
        notes.append(
            f"Calculated mandatory SL: estimated price risk ${ticks * value * parsed.volume} (cap ${budget}); not a guaranteed maximum loss; gaps, slippage and costs can add loss"
        )
    return parsed.model_copy(
        update={
            "entry": entry,
            "stop_loss": stop,
            "note": "; ".join(filter(None, [parsed.note, *notes])),
        }
    )
