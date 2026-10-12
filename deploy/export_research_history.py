"""Save a bounded, immutable Vantage MT5 market-data slice for offline research.

This calls read-only connector routes. It never imports MT5 or sends an order.
Output includes the connector's coverage report and SHA-256 digest. A second
invocation cannot overwrite an existing snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from trade_agent.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", choices=["EURUSD", "XAUUSD"], required=True)
    parser.add_argument("--kind", choices=["bars", "ticks"], required=True)
    parser.add_argument(
        "--timeframe", choices=["M5", "M15", "M30", "H1", "H4", "D1"], default="M15"
    )
    parser.add_argument("--since", type=datetime.fromisoformat, required=True)
    parser.add_argument("--until", type=datetime.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.since.tzinfo is None or args.until.tzinfo is None or args.until <= args.since:
        parser.error("--since and --until must be ordered timezone-aware ISO timestamps")
    if args.output.exists():
        parser.error("output already exists; research snapshots must not be overwritten")
    settings = Settings()
    if settings.broker_mode != "mt5_http" or not settings.mt5_connector_token:
        parser.error("authenticated MT5 connector is not configured")
    if urlparse(settings.mt5_connector_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("research export requires a loopback-only connector")
    url = f"{settings.mt5_connector_url.rstrip('/')}/v1/research/{args.kind}/{args.symbol}"
    params = {"since": args.since.isoformat(), "until": args.until.isoformat()}
    if args.kind == "bars":
        params["timeframe"] = args.timeframe
    with httpx.Client(timeout=90, trust_env=False) as client:
        headers = {"X-Connector-Token": settings.mt5_connector_token}
        response = client.get(url, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        spec_response = client.get(
            f"{settings.mt5_connector_url.rstrip('/')}/v1/symbols/{args.symbol}",
            headers=headers,
        )
        spec_response.raise_for_status()
        instrument_spec = spec_response.json()
    if payload.get("count") != len(payload.get("rows", [])):
        raise RuntimeError("connector research count does not match rows")
    canonical = json.dumps(
        {key: payload.get(key) for key in ("symbol", "kind", "timeframe", "rows")},
        sort_keys=True,
        separators=(",", ":"),
    )
    if hashlib.sha256(canonical.encode()).hexdigest() != payload.get("sha256"):
        raise RuntimeError("connector research digest mismatch")
    if instrument_spec.get("symbol") != args.symbol:
        raise RuntimeError("connector returned a different symbol specification")
    payload["instrument_spec"] = instrument_spec
    payload["snapshot_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(payload, destination, indent=2)
        destination.write("\n")
    print(
        json.dumps(
            {
                key: payload.get(key)
                for key in (
                    "symbol",
                    "kind",
                    "count",
                    "first_at",
                    "last_at",
                    "coverage_verified",
                    "sha256",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
