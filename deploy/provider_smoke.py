"""Read-only structured-parsing smoke test for the selected AI provider.

The script imports no broker or execution service. It sends a fully specified
sample message to the configured provider and accepts only a valid structured
parse; it never creates a proposal or submits a trade.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from trade_agent.ai import AIBackendError, build_ai_backend
from trade_agent.config import Settings

SAMPLE_TRADE = "Buy EURUSD 0.10 lots at market, stop loss 1.07000, take profit 1.09000."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Protected environment file to read instead of the default .env",
    )
    return parser.parse_args()


async def check(settings: Settings) -> None:
    if settings.ai_provider == "none":
        raise RuntimeError("set TRADE_AGENT_AI_PROVIDER to a configured provider before running")
    parsed = await build_ai_backend(settings).parse_trade(SAMPLE_TRADE)
    if parsed.intent != "trade" or parsed.missing_fields:
        raise RuntimeError("provider did not return a complete structured trade parse")
    if (
        parsed.symbol != "EURUSD"
        or parsed.side != "buy"
        or parsed.volume is None
        or parsed.stop_loss is None
        or parsed.take_profit is None
        or parsed.entry.kind == "missing"
    ):
        raise RuntimeError("provider returned an incomplete or inconsistent structured trade parse")
    print(f"{settings.ai_provider} structured parsing readiness passed")


def main() -> None:
    args = parse_args()
    settings = Settings(_env_file=args.env_file) if args.env_file else Settings()
    try:
        asyncio.run(check(settings))
    except (AIBackendError, RuntimeError, ValueError):
        print(
            "provider readiness failed; inspect provider configuration and authentication",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
