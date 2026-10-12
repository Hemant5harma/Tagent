from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_agent.replay import replay


def bars(count=55):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        {
            "time": (start + timedelta(minutes=15 * i)).isoformat(),
            "open": str(100 + i),
            "high": str(101 + i),
            "low": str(99 + i),
            "close": str(100 + i),
        }
        for i in range(count)
    ]


def test_replay_costs_and_pessimistic_ambiguous_stop():
    sample = bars(51)
    sample[50]["high"], sample[50]["low"] = "200", "100"
    result = replay(sample, spread="0.1", slippage="0.05", fee_per_unit="0.01")
    trade = result["trades"][0]
    assert trade["reason"] == "stop_ambiguous"
    assert Decimal(trade["net_pnl"]) < -50  # risk plus exit slippage and commissions
    assert Decimal(trade["commission"]) > 0
    assert trade["signal_bar_at"] == sample[49]["time"]
    assert "not broker lots" in result["limitations"]


def test_replay_signal_does_not_read_current_or_future_close():
    sample = bars()
    original = replay(sample, spread="0", slippage="0")
    # Change the entry bar's close and later bars; first entry remains identical.
    sample[50]["close"], sample[50]["low"] = "120", "119"
    changed = replay(sample, spread="0", slippage="0")
    assert original["trades"][0]["entry"] == changed["trades"][0]["entry"]
    assert original["trades"][0]["side"] == changed["trades"][0]["side"] == "buy"
    assert replay(bars()) == replay(bars())


def test_replay_rejects_bad_data_and_unknown_strategy():
    with pytest.raises(ValueError, match="registered"):
        replay(bars(), strategy="llm-backtest")
    sample = bars()
    sample[51]["time"] = sample[50]["time"]
    with pytest.raises(ValueError, match="chronological"):
        replay(sample)
    with pytest.raises(ValueError, match="nonnegative"):
        replay(bars(), spread="-1")
