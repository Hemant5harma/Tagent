"""Closed-bar, non-executing comparison over equal trading-bar coverage."""

from datetime import datetime

from trade_agent.trade_plans import build_plans

COMPARISON_COUNTS = {"M5": 600, "M15": 200, "M30": 100}


def compare_timeframes(snapshots, higher, spec, settings, cutoff):
    rows = []
    for timeframe, count in COMPARISON_COUNTS.items():
        snapshot = snapshots.get(timeframe)
        if not snapshot or len(snapshot.get("bars", [])) != count:
            rows.append(
                {
                    "timeframe": timeframe,
                    "status": "unavailable",
                    "reason": "Complete comparable candle coverage unavailable",
                    "plans": [],
                }
            )
            continue
        if snapshot["timeframe"] != timeframe or datetime.fromisoformat(
            snapshot["closed_bar_at"]
        ) > datetime.fromisoformat(cutoff):
            rows.append(
                {
                    "timeframe": timeframe,
                    "status": "unavailable",
                    "reason": "Timeframe mismatch or candle newer than evaluation cutoff",
                    "plans": [],
                }
            )
            continue
        try:
            result = build_plans(snapshot, higher, spec, settings, context_timeframes=("H1",))
            rows.append(
                {
                    "timeframe": timeframe,
                    "status": "evaluated",
                    "closed_bar_at": snapshot["closed_bar_at"],
                    "bar_count": count,
                    "indicators": snapshot["indicators"],
                    "plans": result["plans"],
                    "context": result["timeframes"],
                    "ready_directions": [
                        p["side"] for p in result["plans"] if p["status"] == "ready"
                    ],
                }
            )
        except (ValueError, KeyError, TypeError, ArithmeticError):
            rows.append(
                {
                    "timeframe": timeframe,
                    "status": "unavailable",
                    "reason": "Planning data invalid",
                    "plans": [],
                }
            )
    return {
        "mode": "shadow_comparison",
        "evaluation_cutoff": cutoff,
        "coverage": "50 hours of candles per timeframe; session gaps can extend calendar coverage",
        "cadence": "Compared at each primary research candle, not every M5 candle",
        "context_timeframe": "H1",
        "frames": rows,
        "note": "Ready is a deterministic setup check, not model approval, an order or demonstrated profitability",
    }
