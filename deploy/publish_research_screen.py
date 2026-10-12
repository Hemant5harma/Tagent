"""Publish verified, offline-only screening summaries to the read-only dashboard."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from trade_agent.config import Settings
from trade_agent.db import Database
from trade_agent.reporting import put_cache
from trade_agent.research_screen import VERSION, screen


def build_payload(snapshot: dict, results: list[dict]) -> dict:
    if not results or snapshot.get("kind") != "bars":
        raise ValueError("at least one bar-level screening result is required")
    scenarios = []
    for result in results:
        assumptions = result.get("assumptions") or {}
        if result.get("version") != VERSION or result.get("source_sha256") != snapshot.get(
            "sha256"
        ):
            raise ValueError("screening result does not match the registered source and version")
        reproduced = screen(
            snapshot,
            risk_usd=assumptions["risk_usd"],
            max_lots=assumptions["max_lots"],
            slippage_points=assumptions["slippage_points_per_side"],
            commission_per_lot_per_side=assumptions["commission_per_lot_per_side"],
        )
        if reproduced != result:
            raise ValueError("screening result could not be reproduced from the archived bars")
        scenarios.append(
            {
                "slippage_points_per_side": assumptions["slippage_points_per_side"],
                "commission_per_lot_per_side": assumptions["commission_per_lot_per_side"],
                "all": result["all"],
                "discovery": result["discovery"],
                "later_period": result["later_period"],
            }
        )
    return {
        "published_at": datetime.now(UTC).isoformat(),
        "symbol": snapshot["symbol"],
        "version": VERSION,
        "source_since": snapshot.get("since"),
        "source_until": snapshot.get("until"),
        "source_sha256": snapshot["sha256"],
        "snapshot_sha256": snapshot.get("snapshot_sha256"),
        "bar_count": len(snapshot["rows"]),
        "reported_gaps": len(snapshot.get("gaps", [])),
        "coverage_verified": bool(snapshot.get("coverage_verified")),
        "scenarios": scenarios,
        "classification": "exploratory_bar_screen_only",
        "decision": "Not promoted; no demonstrated profitable signal",
        "limitations": "Bar spread approximates ask OHLC. This is not tick-level replay, an untouched holdout, or realized broker P/L.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("results", type=Path, nargs="+")
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    results = [json.loads(path.read_text(encoding="utf-8")) for path in args.results]
    payload = build_payload(snapshot, results)
    settings = Settings()
    if settings.broker_mode != "mt5_http" or settings.trading_mode != "demo":
        parser.error("only the configured MT5 demo deployment may publish this summary")
    put_cache(Database(settings.database_url), f"research-screen:{settings.account_id}", payload)
    print(
        json.dumps(
            {
                "published": True,
                "symbol": payload["symbol"],
                "scenarios": len(results),
                "decision": payload["decision"],
            }
        )
    )


if __name__ == "__main__":
    main()
