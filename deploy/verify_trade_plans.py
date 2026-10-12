"""Read-only staged planner and real-model check; no grants, ledger writes or orders."""

import asyncio
import json
import os
import sys


async def main():
    os.chdir("/home/jarvis/trade-agent")
    import trade_agent

    if len(sys.argv) > 1:
        trade_agent.__path__.insert(0, sys.argv[1])
    from trade_agent.autonomy import AutonomyController
    from trade_agent.config import Settings
    from trade_agent.intelligence import ResearchWorkflow
    from trade_agent.main import build_runtime
    from trade_agent.market_data import MarketDataCollector
    from trade_agent.trade_plans import build_plans

    f, _ = build_runtime(Settings(autonomy_trade_plans_enabled=True))
    runs = AutonomyController(f.service).runs(30)
    collect = MarketDataCollector(f.service.broker, f.settings.max_quote_age_seconds)
    for symbol in f.settings.allowed_symbols:
        s = collect.collect(symbol)
        higher = {tf: collect.collect(symbol, tf) for tf in ("H1", "H4")}
        s["trade_plans"] = build_plans(s, higher, f.service.broker.symbol(symbol), f.settings)
        archived = next(
            r["snapshot"] for r in runs if r["symbol"] == symbol and r["snapshot"].get("context")
        )
        s["evidence"] += archived.get("context", {}).get("evidence", [])
        s["macro_available"] = archived.get("macro_available", False)
        s["evidence"].append(
            {
                "id": "trade-plans",
                "source": "code-calculated trade hypotheses",
                "observed_at": s["as_of"],
                "text": json.dumps(s["trade_plans"]),
            }
        )
        result = await ResearchWorkflow(f.settings).analyze(s)
        print(
            json.dumps(
                {
                    "symbol": symbol,
                    "timeframes": s["trade_plans"]["timeframes"],
                    "plans": s["trade_plans"]["plans"],
                    "roles": len(result["reports"]),
                    "direction": result["direction"],
                    "health": result.get("research_health"),
                    "reason": result["reason"],
                }
            ),
            flush=True,
        )
        assert len(result["reports"]) == 5, "Model did not complete five validated roles"
        assert all(c["status"] == "available" for c in s["trade_plans"]["timeframes"].values())


if __name__ == "__main__":
    asyncio.run(main())
