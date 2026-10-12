"""Deterministic, timestamped trade hypotheses; never submits an order."""

from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from trade_agent.market_data import TIMEFRAME_SECONDS


def number(value):
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError("invalid price or contract value")
    return result


def bias(snapshot):
    close = number(snapshot["bars"][-1]["close"])
    fast = number(snapshot["indicators"]["ema_fast"])
    slow = number(snapshot["indicators"]["ema_slow"])
    return "buy" if close > fast > slow else "sell" if close < fast < slow else "mixed"


def pivots(bars, key):
    values = [number(b[key]) for b in bars]
    return [
        values[i]
        for i in range(2, len(values) - 2)
        if (
            values[i] < min(values[i - 2 : i] + values[i + 1 : i + 3])
            if key == "low"
            else values[i] > max(values[i - 2 : i] + values[i + 1 : i + 3])
        )
    ]


def build_plans(snapshot, higher, spec, settings, *, context_timeframes=("H1", "H4")):
    """Only closed bars at/before the decision bar may inform a plan.

    Ready means the deterministic setup passed, not permission to execute.
    The judge, confidence gate, grant and all broker risk checks still apply.
    """
    bars = snapshot["bars"]
    if len(bars) < 50:
        raise ValueError("insufficient planning history")
    cutoff = datetime.fromisoformat(snapshot["closed_bar_at"])
    contexts = {}
    for timeframe in context_timeframes:
        data = higher.get(timeframe)
        if not data:
            contexts[timeframe] = {"status": "unavailable"}
            continue
        closed = datetime.fromisoformat(data["closed_bar_at"])
        if (
            data["timeframe"] != timeframe
            or data["symbol"] != snapshot["symbol"]
            or closed > cutoff
            or cutoff - closed > timedelta(seconds=2 * TIMEFRAME_SECONDS[timeframe])
        ):
            contexts[timeframe] = {"status": "unavailable"}
            continue
        contexts[timeframe] = {
            "status": "available",
            "bias": bias(data),
            "closed_bar_at": data["closed_bar_at"],
            "indicators": data["indicators"],
            "bar_count": len(data["bars"]),
        }
    atr = number(snapshot["indicators"]["atr"])
    fast = number(snapshot["indicators"]["ema_fast"])
    close = number(bars[-1]["close"])
    bid, ask = number(snapshot["quote"]["bid"]), number(snapshot["quote"]["ask"])
    spread = ask - bid
    if spread < 0:
        raise ValueError("crossed quote")
    tick, value = number(spec.trade_tick_size), number(spec.trade_tick_value)
    slip = spec.point * settings.max_slippage_points
    minimum = spec.point * (spec.min_stop_distance_points + settings.max_slippage_points + 1)
    lows, highs = pivots(bars[-60:], "low"), pivots(bars[-60:], "high")
    stop_lows, stop_highs = pivots(bars[-20:], "low"), pivots(bars[-20:], "high")
    expiry = cutoff + timedelta(seconds=TIMEFRAME_SECONDS[snapshot["timeframe"]])
    plans = []
    for side in ("buy", "sell"):
        sign = Decimal(1) if side == "buy" else Decimal(-1)
        entry = ask if side == "buy" else bid
        structural = (
            [p for p in stop_lows if p < bid]
            if side == "buy"
            else [p for p in stop_highs if p > ask]
        )
        anchor = (
            structural[-1]
            if structural
            else (
                min(number(b["low"]) for b in bars[-10:])
                if side == "buy"
                else max(number(b["high"]) for b in bars[-10:])
            )
        )
        structure_stop = (
            anchor - atr * Decimal("0.2")
            if side == "buy"
            else anchor + spread + atr * Decimal("0.2")
        )
        distance = max(
            sign * (entry - structure_stop), atr * settings.autonomy_stop_atr, minimum + spread
        )
        stop = ((entry - sign * distance) / tick).to_integral_value(
            rounding=ROUND_FLOOR if side == "buy" else ROUND_CEILING
        ) * tick
        risk = abs(entry - stop) + slip
        projected = entry + sign * (risk * settings.autonomy_reward_risk + slip + tick)
        obstacles = (
            [p for p in highs if p > entry] if side == "buy" else [p for p in lows if p < entry]
        )
        obstacle = (
            min(obstacles) if side == "buy" and obstacles else max(obstacles) if obstacles else None
        )
        target = projected
        target_basis = "Projected reward/risk target; no nearer confirmed opposing pivot"
        if obstacle is not None:
            boundary = (
                obstacle - atr * Decimal("0.1")
                if side == "buy"
                else obstacle + spread + atr * Decimal("0.1")
            )
            if sign * (boundary - projected) < 0:
                target, target_basis = boundary, "Nearest confirmed opposing pivot, with buffer"
        target = (target / tick).to_integral_value(
            rounding=ROUND_FLOOR if side == "buy" else ROUND_CEILING
        ) * tick
        reward = max(Decimal(0), sign * (target - entry) - slip)
        rr = reward / risk
        blockers = []
        if stop <= 0 or target <= 0 or sign * (target - entry) < minimum + spread:
            blockers.append("Target or stop violates the broker price/distance rules")
        if any(c.get("bias") != side for c in contexts.values()):
            blockers.append(
                "/".join(context_timeframes) + " trend alignment is missing or conflicting"
            )
        if bias(snapshot) != side:
            blockers.append("Entry timeframe price and EMA trend are not aligned")
        rsi = Decimal(str(snapshot["indicators"]["rsi"]))
        if not (50 <= rsi <= 75 if side == "buy" else 25 <= rsi <= 50):
            blockers.append("RSI is outside the registered entry range")
        if spread > atr * Decimal("0.15"):
            blockers.append("Spread exceeds fifteen percent of ATR")
        if distance > atr * 4:
            blockers.append("Structural stop exceeds four ATR")
        if abs(entry - close) > atr * Decimal("0.5") or abs(close - fast) > atr:
            blockers.append("Entry is extended; wait for a fresh setup")
        if rr < settings.autonomy_reward_risk:
            blockers.append(
                "Available target fails the required reward/risk after spread and slippage"
            )
        previous = bars[-21:-1]
        breakout = (
            close > max(number(b["high"]) for b in previous)
            if side == "buy"
            else close < min(number(b["low"]) for b in previous)
        )
        touch = (
            min(number(b["low"]) for b in bars[-5:]) <= fast <= close
            if side == "buy"
            else close <= fast <= max(number(b["high"]) for b in bars[-5:])
        )
        directional = (
            close > number(bars[-1]["open"]) if side == "buy" else close < number(bars[-1]["open"])
        )
        trigger = (
            "closed-bar breakout"
            if breakout
            else "pullback reclaim"
            if touch and directional
            else "awaiting closed-bar breakout or pullback reclaim"
        )
        if not (breakout or touch and directional):
            blockers.append("Entry trigger has not occurred on a closed candle")
        budget = min(settings.autonomy_risk_amount, settings.max_estimated_loss)
        per_lot = risk / tick * value
        cap = min(
            budget / per_lot, settings.autonomy_max_volume, settings.max_volume, spec.volume_max
        )
        volume = (
            (
                spec.volume_min
                + ((cap - spec.volume_min) / spec.volume_step).to_integral_value(
                    rounding=ROUND_FLOOR
                )
                * spec.volume_step
            )
            if cap >= spec.volume_min
            else Decimal(0)
        )
        if volume == 0:
            blockers.append("Risk budget cannot cover the broker minimum volume")
        plans.append(
            {
                "side": side,
                "status": "ready" if not blockers else "wait",
                "entry": str(entry),
                "entry_basis": "Snapshot ask for buy / bid for sell; hypothetical market entry",
                "entry_zone_low": str(fast - atr * Decimal("0.25")),
                "entry_zone_high": str(fast + atr * Decimal("0.25")),
                "stop_loss": str(stop),
                "take_profit": str(target)
                if target > 0 and sign * (target - entry) >= minimum + spread
                else None,
                "opposing_obstacle": str(obstacle) if obstacle is not None else None,
                "reward_risk": str(rr),
                "required_reward_risk": str(settings.autonomy_reward_risk),
                "volume": str(volume),
                "estimated_risk": str(volume * per_lot),
                "minimum_lot_risk": str(spec.volume_min * per_lot),
                "risk_budget": str(budget),
                "trigger": trigger,
                "stop_basis": "Confirmed swing or recent range, ATR buffer and broker minimum distance",
                "target_basis": target_basis,
                "invalidation": "Stop level breached, trend alignment lost, or plan expired",
                "expires_at": expiry.isoformat(),
                "blockers": blockers,
            }
        )
    return {
        "version": "structure-v1",
        "as_of": snapshot["as_of"],
        "timeframes": contexts,
        "plans": plans,
        "limitations": [
            "Hypothetical levels, not orders",
            "Spread/slippage included; commissions, swaps and gaps not modeled",
            "No complete economic-event calendar",
        ],
    }
