"""Read-only broker-history export never submits an order or hides data gaps."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from mt5_connector.service import ConnectorError, MT5Service
from mt5_connector.settings import ConnectorSettings


class Terminal:
    TIMEFRAME_M15 = 15
    COPY_TICKS_INFO = 1

    def __init__(self):
        self.calls = []

    def symbol_select(self, symbol, enabled):
        return symbol == "XAUUSD" and enabled

    def symbol_info(self, symbol):
        return SimpleNamespace() if symbol == "XAUUSD" else None

    def copy_rates_range(self, symbol, timeframe, since, until):
        self.calls.append("bars")
        base = int(datetime(2026, 10, 9, 12, tzinfo=UTC).timestamp())
        return [
            {
                "time": base,
                "open": 4000,
                "high": 4002,
                "low": 3999,
                "close": 4001,
                "tick_volume": 5,
                "spread": 30,
            },
            {
                "time": base + 1800,
                "open": 4001,
                "high": 4003,
                "low": 4000,
                "close": 4002,
                "tick_volume": 6,
                "spread": 35,
            },
        ]

    def copy_ticks_range(self, symbol, since, until, flags):
        self.calls.append("ticks")
        base = int(datetime(2026, 10, 9, 12, tzinfo=UTC).timestamp()) * 1000
        return [
            {"time_msc": base, "bid": 4000, "ask": 4000.25},
            {"time_msc": base + 500, "bid": 4000.1, "ask": 4000.4},
        ]


class Gateway:
    def __init__(self):
        self.terminal = Terminal()

    def connect(self):
        return self.terminal


def test_research_export_is_authenticated_ordered_and_gap_explicit(tmp_path):
    gateway = Gateway()
    settings = ConnectorSettings(token="t" * 24, journal_path=tmp_path / "journal")
    service = MT5Service(settings, gateway)
    since = datetime(2026, 10, 9, 12, tzinfo=UTC)
    until = since + timedelta(hours=1)
    body = service.historical_bars("XAUUSD", "M15", since, until)
    assert body["count"] == 2 and body["complete"] is False and len(body["gaps"]) == 1
    assert len(body["sha256"]) == 64 and body["rows"][0]["spread_points"] == 30
    ticks = service.historical_ticks("XAUUSD", since, until)["rows"]
    assert ticks[0]["spread"] == "0.25" and ticks[1]["spread"] == "0.3"
    assert gateway.terminal.calls == ["bars", "ticks"]


def test_research_export_rejects_future_naive_and_oversized_ranges(tmp_path):
    service = MT5Service(
        ConnectorSettings(token="t" * 24, journal_path=tmp_path / "journal"), Gateway()
    )
    since = datetime(2026, 10, 9, 12, tzinfo=UTC)
    with pytest.raises(ConnectorError):
        service.historical_bars("XAUUSD", "M15", since, since + timedelta(days=32))
    with pytest.raises(ConnectorError):
        service.historical_ticks("XAUUSD", since.replace(tzinfo=None), since)
    with pytest.raises(ConnectorError):
        service.historical_ticks("XAUUSD", since, datetime.now(UTC) + timedelta(hours=1))


def test_research_export_does_not_truncate_large_tick_history(tmp_path):
    gateway = Gateway()
    gateway.terminal.copy_ticks_range = lambda *_: [{"time_msc": 0, "bid": 1, "ask": 2}] * 10001
    service = MT5Service(
        ConnectorSettings(token="t" * 24, journal_path=tmp_path / "journal"), gateway
    )
    since = datetime(2026, 10, 9, 12, tzinfo=UTC)
    with pytest.raises(ConnectorError, match="shorter range"):
        service.historical_ticks("XAUUSD", since, since + timedelta(hours=1))
