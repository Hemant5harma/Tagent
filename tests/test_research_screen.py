import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from trade_agent.research_screen import screen


def sample():
    base = datetime(2026, 9, 10, tzinfo=UTC)
    rows = []
    for index in range(220):
        opened = 4000 + index * 0.1
        rows.append(
            {
                "time": (base + timedelta(minutes=15 * index)).isoformat(),
                "closed_at": (base + timedelta(minutes=15 * (index + 1))).isoformat(),
                "open": str(opened),
                "high": str(opened + 0.3),
                "low": str(opened - 0.2),
                "close": str(opened + 0.1),
                "spread_points": 25,
                "tick_volume": 100,
            }
        )
    snapshot = {
        "symbol": "XAUUSD",
        "kind": "bars",
        "timeframe": "M15",
        "rows": rows,
        "instrument_spec": {
            "symbol": "XAUUSD",
            "point": "0.01",
            "trade_tick_size": "0.01",
            "trade_tick_value": "1",
            "volume_min": "0.01",
            "volume_step": "0.01",
            "volume_max": "100",
        },
        "coverage_verified": True,
        "gaps": [],
    }
    canonical = json.dumps(
        {key: snapshot[key] for key in ("symbol", "kind", "timeframe", "rows")},
        sort_keys=True,
        separators=(",", ":"),
    )
    snapshot["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return snapshot


def test_screen_uses_broker_lots_next_bar_and_explicit_costs():
    source = sample()
    without_fees = screen(source, commission_per_lot_per_side="0")
    with_fees = screen(source, commission_per_lot_per_side="10")
    assert without_fees["all"]["trades"] > 0
    assert without_fees["signals"] > 0
    assert all(item["lots"] == "0.10" for item in without_fees["trades"])
    assert all(item["signal_bar_at"] < item["opened_at"] for item in without_fees["trades"])
    assert float(with_fees["all"]["net_pnl"]) < float(without_fees["all"]["net_pnl"])
    assert with_fees["limitations"]


def test_screen_rejects_tampered_history_and_missing_spec():
    source = sample()
    source["rows"][100]["close"] = "9999"
    with pytest.raises(ValueError, match="hash"):
        screen(source)
    source = sample()
    source.pop("instrument_spec")
    with pytest.raises(ValueError, match="specification"):
        screen(source)
