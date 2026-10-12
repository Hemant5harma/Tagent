"""Offline broker-lot screening baseline; never an autonomous trading signal.

The fixed EMA/RSI/ATR rule is registered before inspecting a dataset. Bid OHLC
and each bar's reported spread approximate ask OHLC. This is not a tick replay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

from trade_agent.market_data import TIMEFRAME_SECONDS, indicators

VERSION = "ema-rsi-atr-mt5-bar-screen-v1"


def _decimal(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite research value")
    return result


def _summary(trades: list[dict], initial_cash: Decimal) -> dict:
    pnl = [_decimal(item["net_pnl"]) for item in trades]
    wins = [amount for amount in pnl if amount > 0]
    losses = [amount for amount in pnl if amount < 0]
    equity = initial_cash
    peak = equity
    drawdown = Decimal(0)
    for amount in pnl:
        equity += amount
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "trades": len(trades),
        "net_pnl": str(sum(pnl, Decimal(0))),
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(pnl) - len(wins) - len(losses),
        "win_rate_pct": str(Decimal(100) * len(wins) / len(trades)) if trades else None,
        "profit_factor": str(sum(wins, Decimal(0)) / -sum(losses, Decimal(0))) if losses else None,
        "closed_trade_drawdown": str(drawdown),
    }


def screen(
    snapshot: dict,
    *,
    initial_cash: str = "10000",
    risk_usd: str = "20",
    max_lots: str = "0.10",
    slippage_points: str = "5",
    commission_per_lot_per_side: str = "0",
    max_holding_bars: int = 16,
) -> dict:
    """Run one fixed candidate with chronological 70/30 reporting, no tuning."""
    if snapshot.get("kind") != "bars" or snapshot.get("timeframe") != "M15":
        raise ValueError("screen requires an M15 bar export")
    rows = snapshot.get("rows")
    spec = snapshot.get("instrument_spec")
    if not isinstance(rows, list) or len(rows) < 200 or not isinstance(spec, dict):
        raise ValueError("insufficient bars or missing broker instrument specification")
    if spec.get("symbol") != snapshot.get("symbol"):
        raise ValueError("broker symbol specification does not match dataset")
    canonical = json.dumps(
        {key: snapshot.get(key) for key in ("symbol", "kind", "timeframe", "rows")},
        sort_keys=True,
        separators=(",", ":"),
    )
    if hashlib.sha256(canonical.encode()).hexdigest() != snapshot.get("sha256"):
        raise ValueError("research dataset hash mismatch")
    point = _decimal(spec["point"])
    tick_size = _decimal(spec["trade_tick_size"])
    tick_value = _decimal(spec["trade_tick_value"])
    min_lot = _decimal(spec["volume_min"])
    lot_step = _decimal(spec["volume_step"])
    cash, budget, lot_cap = map(_decimal, (initial_cash, risk_usd, max_lots))
    slip_points, commission = map(_decimal, (slippage_points, commission_per_lot_per_side))
    if min(point, tick_size, tick_value, min_lot, lot_step, cash, budget, lot_cap) <= 0:
        raise ValueError("broker sizing and screening budget must be positive")
    if min(slip_points, commission) < 0 or max_holding_bars < 1:
        raise ValueError("screening costs must be nonnegative")
    if lot_cap > _decimal(spec["volume_max"]):
        raise ValueError("lot cap exceeds the broker maximum")
    bars = []
    previous = None
    for row in rows:
        instant = datetime.fromisoformat(row["time"])
        if instant.utcoffset() is None or previous is not None and instant <= previous:
            raise ValueError("bars must have strictly ordered aware timestamps")
        prices = {key: _decimal(row[key]) for key in ("open", "high", "low", "close")}
        spread = _decimal(row["spread_points"]) * point
        if (
            min(prices.values()) <= 0
            or spread < 0
            or prices["low"] > min(prices["open"], prices["close"])
            or prices["high"] < max(prices["open"], prices["close"])
        ):
            raise ValueError("invalid bid OHLC or spread")
        bars.append({"time": instant, **prices, "spread": spread})
        previous = instant
    interval = TIMEFRAME_SECONDS["M15"]
    split = int(len(bars) * Decimal("0.7"))
    slip = slip_points * point
    trades = []
    position = None
    skipped_minimum_lot = 0
    signals = 0
    for index in range(50, len(bars)):
        bar = bars[index]
        if (
            position is None
            and cash > 0
            and (bar["time"] - bars[index - 1]["time"]).total_seconds() == interval
        ):
            evidence = indicators(bars[max(0, index - 200) : index])
            fast, slow, rsi, atr = (
                _decimal(evidence[key]) for key in ("ema_fast", "ema_slow", "rsi", "atr")
            )
            side = (
                "buy" if fast > slow and rsi > 50 else "sell" if fast < slow and rsi < 50 else None
            )
            if side:
                signals += 1
                direction = Decimal(1) if side == "buy" else Decimal(-1)
                entry = (
                    bar["open"]
                    + (bar["spread"] if side == "buy" else Decimal(0))
                    + direction * slip
                )
                distance = atr * Decimal("1.5")
                stop = entry - direction * distance
                target = entry + direction * distance * 2
                risk_per_lot = (distance + slip) / tick_size * tick_value + commission * 2
                allowed = min(lot_cap, budget / risk_per_lot)
                lots = (allowed / lot_step).to_integral_value(rounding=ROUND_FLOOR) * lot_step
                if lots < min_lot:
                    skipped_minimum_lot += 1
                else:
                    position = {
                        "side": side,
                        "direction": direction,
                        "entry": entry,
                        "stop": stop,
                        "target": target,
                        "lots": lots,
                        "index": index,
                        "opened_at": bar["time"].isoformat(),
                        "signal_bar_at": bars[index - 1]["time"].isoformat(),
                        "segment": "discovery" if index < split else "later_period",
                    }
        if position is None:
            continue
        buy = position["side"] == "buy"
        # Longs liquidate at bid; shorts liquidate at approximate ask.
        offset = Decimal(0) if buy else bar["spread"]
        opened = bar["open"] + offset
        high = bar["high"] + offset
        low = bar["low"] + offset
        stop, target = position["stop"], position["target"]
        stop_hit = low <= stop if buy else high >= stop
        target_hit = high >= target if buy else low <= target
        reason = None
        exit_price = None
        if stop_hit:
            reason = "stop_ambiguous" if target_hit else "stop"
            exit_price = min(opened, stop) if buy else max(opened, stop)
        elif target_hit:
            reason, exit_price = "target", target
        elif index - position["index"] + 1 >= max_holding_bars:
            reason, exit_price = "holding_limit", bar["close"] + offset
        elif index == len(bars) - 1:
            reason, exit_price = "end_of_sample", bar["close"] + offset
        if exit_price is None:
            continue
        exit_price -= position["direction"] * slip
        gross = (
            (exit_price - position["entry"])
            * position["direction"]
            / tick_size
            * tick_value
            * position["lots"]
        )
        fees = commission * 2 * position["lots"]
        pnl = gross - fees
        cash += pnl
        trades.append(
            {
                "side": position["side"],
                "opened_at": position["opened_at"],
                "signal_bar_at": position["signal_bar_at"],
                "closed_at": bar["time"].isoformat(),
                "entry": str(position["entry"]),
                "exit": str(exit_price),
                "stop": str(stop),
                "target": str(target),
                "lots": str(position["lots"]),
                "gross_pnl": str(gross),
                "fees": str(fees),
                "net_pnl": str(pnl),
                "reason": reason,
                "segment": position["segment"],
            }
        )
        position = None
    discovery = [item for item in trades if item["segment"] == "discovery"]
    later = [item for item in trades if item["segment"] == "later_period"]
    return {
        "version": VERSION,
        "symbol": snapshot["symbol"],
        "source_sha256": snapshot["sha256"],
        "source_snapshot_sha256": snapshot.get("snapshot_sha256"),
        "coverage_verified": snapshot.get("coverage_verified", False),
        "reported_gaps": len(snapshot.get("gaps", [])),
        "bar_count": len(bars),
        "split_at": bars[split]["time"].isoformat(),
        "signals": signals,
        "skipped_minimum_lot": skipped_minimum_lot,
        "all": _summary(trades, _decimal(initial_cash)),
        "discovery": _summary(discovery, _decimal(initial_cash)),
        "later_period": _summary(later, _decimal(initial_cash)),
        "trades": trades,
        "assumptions": {
            "risk_usd": risk_usd,
            "max_lots": max_lots,
            "slippage_points_per_side": slippage_points,
            "commission_per_lot_per_side": commission_per_lot_per_side,
            "entry": "Next bar open; no new entry across a missing-bar interval",
            "exit": "Stop first on ambiguous candles; adverse opening gaps honored",
        },
        "limitations": [
            "Exploratory bar-level screening, not a tick-accurate or MT5 Strategy Tester result",
            "Each bar's reported spread approximates its entire ask OHLC; intrabar spread changes are unknown",
            "Commission, slippage, swaps, margin and partial fills need broker-deal calibration",
            "Drawdown is measured at completed trade exits, not intrabar account equity",
            "Later-period result is not a final untouched holdout or proof of profitability",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--slippage-points", default="5")
    parser.add_argument("--commission-per-lot-per-side", default="0")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("research result already exists")
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    result = screen(
        snapshot,
        slippage_points=args.slippage_points,
        commission_per_lot_per_side=args.commission_per_lot_per_side,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(result, destination, indent=2)
        destination.write("\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "version",
                    "bar_count",
                    "coverage_verified",
                    "reported_gaps",
                    "signals",
                    "all",
                    "discovery",
                    "later_period",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
