from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from mt5_connector.service import ConnectorError, MT5Service
from mt5_connector.settings import ConnectorSettings


def test_explicit_broker_clock_roundtrip_and_expiry():
    settings = ConnectorSettings(
        token="test",
        journal_path=Path("unused"),
        server_time_offset_seconds=10800,
        server_time_valid_from="2026-03-09T00:00:00+00:00",
        server_time_valid_until="2026-10-31T00:00:00+00:00",
    )
    service = MT5Service(settings)
    utc = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
    server = service._server_bound(utc)
    assert server.hour == 12
    assert service._server_timestamp(server.timestamp()) == utc
    with pytest.raises(ConnectorError, match="clock profile"):
        service._server_bound(datetime(2026, 11, 1, tzinfo=UTC))


def test_history_normalizes_times_and_query_bounds():
    utc = datetime(2026, 9, 30, 9, tzinfo=UTC)
    raw = int(utc.timestamp()) + 10800
    calls = []

    def history(since, until):
        calls.append((since, until))
        return [SimpleNamespace(ticket=123, time=raw, time_msc=raw * 1000 + 42)]

    mt5 = SimpleNamespace(history_deals_get=history)
    gateway = SimpleNamespace(connect=lambda: mt5)
    service = MT5Service(
        ConnectorSettings(
            token="test", journal_path=Path("unused"), server_time_offset_seconds=10800
        ),
        gateway,
    )
    service.account = lambda _: {}
    result = service.history("demo", utc.replace(hour=8), utc.replace(hour=10))
    assert [v.hour for v in calls[0]] == [11, 13]
    assert result["deals"][0]["time"] == str(int(utc.timestamp()))
    assert result["deals"][0]["time_msc"] == str(int(utc.timestamp()) * 1000 + 42)
    assert result["deals"][0]["server_time_raw"] == str(raw)
