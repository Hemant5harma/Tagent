"""Chronological technical baseline; no LLM, backfilled news, or broker orders.

OHLC is interpreted as bid prices with a configurable constant ask spread. This
is an approximation, not a tick-accurate simulation of the autonomous LLM agent.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from trade_agent.market_data import indicators

STRATEGY_VERSION = "ema-rsi-atr-baseline-v1"


def number(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Replay values must be finite")
    return result


def timestamp(value: object) -> datetime:
    result = (
        datetime.fromtimestamp(value, UTC)
        if isinstance(value, (int, float))
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if result.utcoffset() is None:
        raise ValueError("Replay timestamps must include a timezone")
    return result.astimezone(UTC)


def replay(
    bars: list[dict],
    *,
    strategy: str = STRATEGY_VERSION,
    initial_cash: str = "10000",
    risk_fraction: str = "0.005",
    spread: str = "0.0001",
    slippage: str = "0.00002",
    fee_per_unit: str = "0",
    max_holding_bars: int = 16,
) -> dict:
    """Risk-sized, single-position baseline, entered at the next bar's open.

    Quantity is price units (not MT5 lots), with notional exposure capped at cash.
    Commissions are charged per unit per side. Spread/slippage are embedded in fill
    prices; they must not be deducted twice. Intrabar ambiguity chooses the stop.
    """
    if strategy != STRATEGY_VERSION:
        raise ValueError("Unknown registered replay strategy")
    cash, fraction = number(initial_cash), number(risk_fraction)
    width, slip, fee = number(spread), number(slippage), number(fee_per_unit)
    if cash <= 0 or not 0 < fraction <= Decimal("0.02"):
        raise ValueError("Initial cash must be positive; risk fraction must be in (0, 0.02]")
    if min(width, slip, fee) < 0 or max_holding_bars < 1:
        raise ValueError("Costs must be nonnegative and holding limit positive")
    if len(bars) < 51:
        raise ValueError("At least 51 chronological closed candles are required")
    normalized: list[dict] = []
    prior = None
    for bar in bars:
        candle_time = timestamp(bar["time"])
        if prior is not None and candle_time <= prior:
            raise ValueError("Replay candles must be strictly chronological")
        values = {key: number(bar[key]) for key in ("open", "high", "low", "close")}
        if not (
            0
            < values["low"]
            <= min(values["open"], values["close"])
            <= max(values["open"], values["close"])
            <= values["high"]
        ):
            raise ValueError("Invalid replay OHLC")
        normalized.append({"time": candle_time.isoformat(), **values})
        prior = candle_time
    trades: list[dict] = []
    equity: list[dict] = []
    position: dict | None = None
    peak, drawdown = cash, Decimal(0)
    signals = 0
    for index in range(50, len(normalized)):
        bar = normalized[index]
        # Only earlier candles inform the signal. No current-bar close is visible.
        if position is None and cash > 0:
            evidence = indicators(normalized[max(0, index - 200) : index])
            fast, slow, rsi, atr = (
                number(evidence[key]) for key in ("ema_fast", "ema_slow", "rsi", "atr")
            )
            side = (
                "buy" if fast > slow and rsi > 50 else "sell" if fast < slow and rsi < 50 else None
            )
            if side:
                signals += 1
                direction = Decimal(1) if side == "buy" else Decimal(-1)
                entry = bar["open"] + (width if side == "buy" else 0) + direction * slip
                distance = atr * Decimal("1.5")
                quantity = min(cash * fraction / distance, cash / entry)
                position = {
                    "side": side,
                    "direction": direction,
                    "entry": entry,
                    "stop": entry - direction * distance,
                    "target": entry + direction * distance * 2,
                    "quantity": quantity,
                    "index": index,
                    "opened_at": bar["time"],
                    "signal_bar_at": normalized[index - 1]["time"],
                }
        if position:
            buy = position["side"] == "buy"
            offset = Decimal(0) if buy else width
            opened, high, low = (bar[key] + offset for key in ("open", "high", "low"))
            stop, target = position["stop"], position["target"]
            stop_hit = low <= stop if buy else high >= stop
            target_hit = high >= target if buy else low <= target
            reason, exit_price = None, None
            if stop_hit:
                exit_price = min(opened, stop) if buy else max(opened, stop)
                reason = "stop_ambiguous" if target_hit else "stop"
            elif target_hit:
                exit_price, reason = target, "target"
            elif index - position["index"] + 1 >= max_holding_bars:
                exit_price, reason = bar["close"] + offset, "holding_limit"
            elif index == len(normalized) - 1:
                exit_price, reason = bar["close"] + offset, "end_of_sample"
            if exit_price is not None:
                exit_price -= position["direction"] * slip
                quantity = position["quantity"]
                commission = quantity * fee * 2
                pnl = (exit_price - position["entry"]) * position[
                    "direction"
                ] * quantity - commission
                cash += pnl
                trades.append(
                    {
                        "side": position["side"],
                        "opened_at": position["opened_at"],
                        "signal_bar_at": position["signal_bar_at"],
                        "closed_at": bar["time"],
                        "entry": str(position["entry"]),
                        "exit": str(exit_price),
                        "quantity_units": str(quantity),
                        "net_pnl": str(pnl),
                        "commission": str(commission),
                        "spread_slippage_estimate": str(quantity * (width + 2 * slip)),
                        "reason": reason,
                    }
                )
                position = None
        marked = cash
        if position:
            liquidation = bar["close"] + (width if position["side"] == "sell" else 0)
            liquidation -= position["direction"] * slip
            marked += (liquidation - position["entry"]) * position["direction"] * position[
                "quantity"
            ] - position["quantity"] * fee * 2
        peak = max(peak, marked)
        drawdown = max(drawdown, peak - marked)
        equity.append({"time": bar["time"], "equity": str(marked)})
    pnls = [number(t["net_pnl"]) for t in trades]
    wins, losses = [p for p in pnls if p > 0], [p for p in pnls if p < 0]
    return {
        "strategy_version": strategy,
        "initial_cash": initial_cash,
        "final_cash": str(cash),
        "net_pnl": str(cash - number(initial_cash)),
        "closed_trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100 if trades else None,
        "profit_factor": str(sum(wins, Decimal(0)) / -sum(losses, Decimal(0))) if losses else None,
        "sampled_drawdown": str(drawdown),
        "signals": signals,
        "trades": trades,
        "equity": equity,
        "assumptions": {
            "spread": spread,
            "slippage": slippage,
            "fee_per_unit_per_side": fee_per_unit,
            "risk_fraction": risk_fraction,
            "max_holding_bars": max_holding_bars,
            "entry": "Next candle open after signal; one position; notional capped at cash",
            "intrabar": "Stop first when SL and TP are both touched; adverse gaps honored",
        },
        "limitations": "Technical baseline only; no LLM or historical news evaluation. Bid OHLC with constant spread/slippage; price units, not broker lots. Excludes swaps, variable liquidity and margin rules. Close-sampled equity is not intrabar maximum drawdown.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "snapshot", type=Path, help="JSON list of candles or archived snapshot containing bars"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--spread", default="0.0001")
    parser.add_argument("--slippage", default="0.00002")
    parser.add_argument("--fee-per-unit", default="0")
    args = parser.parse_args()
    payload = json.loads(args.snapshot.read_text(encoding="utf-8"))
    bars = payload["bars"] if isinstance(payload, dict) else payload
    result = replay(
        bars, spread=args.spread, slippage=args.slippage, fee_per_unit=args.fee_per_unit
    )
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
