"""Read-only archived-evidence smoke test, optionally importing staged code."""

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
    from trade_agent.intelligence import ResearchWorkflow
    from trade_agent.main import build_runtime

    facade, _ = build_runtime()
    runs = AutonomyController(facade.service).runs(30)
    snapshot = next(
        r["snapshot"] for r in runs if r["symbol"] == "EURUSD" and len(r["reports"]) == 5
    )
    result = await ResearchWorkflow(facade.settings).analyze(snapshot)
    print(
        json.dumps(
            {
                "symbol": snapshot["symbol"],
                "direction": result["direction"],
                "reason": result["reason"],
                "reports": result["reports"],
            }
        ),
        flush=True,
    )
    assert len(result["reports"]) == 5, "Model smoke test did not complete five validated roles"
    assert "Verified by code:" in result["reports"][0]["output"]["summary"]


if __name__ == "__main__":
    asyncio.run(main())
