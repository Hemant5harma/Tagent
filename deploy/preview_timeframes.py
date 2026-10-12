"""Save explicitly historical planner previews; never calls an execution path."""

import json
import os
from datetime import datetime, timedelta


def main():
    os.chdir("/home/jarvis/trade-agent")
    from trade_agent.autonomy import AutonomyController
    from trade_agent.main import build_runtime
    from trade_agent.market_data import TIMEFRAME_SECONDS, _time, indicators
    from trade_agent.reporting import put_cache
    from trade_agent.timeframe_comparison import COMPARISON_COUNTS, compare_timeframes

    f, _ = build_runtime()
    c = AutonomyController(f.service)
    state = c.status()
    assert state["mode"] == "shadow" and state["entries_paused"] and state["active_grant"] is None
    runs = c.runs(100)
    results = []
    for symbol in f.settings.allowed_symbols:
        archived = next(
            r
            for r in runs
            if r["symbol"] == symbol and r["snapshot"].get("higher_timeframe_bars", {}).get("H1")
        )
        source = archived["snapshot"]
        cutoff = source["closed_bar_at"]
        frames = {}
        for tf, count in COMPARISON_COUNTS.items():
            raw = f.service.broker.bars(symbol, tf, min(count + 50, 1000))
            bars = []
            for bar in raw:
                opened = _time(bar["time"])
                closed = opened + timedelta(seconds=TIMEFRAME_SECONDS[tf])
                if closed <= datetime.fromisoformat(cutoff):
                    bars.append(
                        {
                            "time": opened.isoformat(),
                            "closed_at": closed.isoformat(),
                            **{k: str(bar[k]) for k in ("open", "high", "low", "close")},
                        }
                    )
            bars = bars[-count:]
            assert len(bars) == count, "Insufficient historical coverage"
            frames[tf] = {
                "symbol": symbol,
                "timeframe": tf,
                "as_of": source["as_of"],
                "closed_bar_at": bars[-1]["closed_at"],
                "bars": bars,
                "quote": source["quote"],
                "indicators": indicators(bars),
            }
        comparison = compare_timeframes(
            frames,
            source["higher_timeframe_bars"],
            f.service.broker.symbol(symbol),
            f.settings,
            cutoff,
        )
        assert all(r["status"] == "evaluated" for r in comparison["frames"])
        results.append(
            {"symbol": symbol, "source_run_id": archived["id"], "comparison": comparison}
        )
    put_cache(
        f.service.database,
        f"autonomy-comparison-preview:{f.settings.account_id}",
        {"kind": "historical_preview", "symbols": results},
    )
    print(
        json.dumps(
            {
                "kind": "historical_preview",
                "results": [
                    {
                        "symbol": x["symbol"],
                        "as_of": x["comparison"]["evaluation_cutoff"],
                        "frames": [
                            {
                                "timeframe": r["timeframe"],
                                "candles": r["bar_count"],
                                "ready": r["ready_directions"],
                            }
                            for r in x["comparison"]["frames"]
                        ],
                    }
                    for x in results
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
