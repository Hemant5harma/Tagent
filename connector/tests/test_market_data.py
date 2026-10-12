from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mt5_connector.app import create_app
from mt5_connector.service import ConnectorError, MT5Service
from mt5_connector.settings import ConnectorSettings


class CandleTerminal:
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30

    def symbol_select(self, *args):
        return True

    def symbol_info(self, *args):
        return SimpleNamespace()

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        self.request = (symbol, timeframe, start, count)
        now = int(datetime.now(UTC).timestamp())
        duration = timeframe * 60
        boundary = now // duration * duration
        return [{"time": boundary - duration, "open": 1.1, "high": 1.2, "low": 1.0,
                 "close": 1.15, "tick_volume": 50, "spread": 10},
                {"time": boundary, "open": 1.1, "high": 1.2, "low": 1.0,
                 "close": 1.15, "tick_volume": 50, "spread": 10}]


class Gateway:
    def __init__(self):
        self.terminal = CandleTerminal()

    def connect(self):
        return self.terminal


@pytest.mark.parametrize("timeframe,constant", [("M15", 15), ("M30", 30)])
def test_connector_excludes_forming_bar_and_starts_at_one(tmp_path, timeframe, constant):
    gateway = Gateway()
    settings = ConnectorSettings(token="t" * 24, journal_path=tmp_path / "journal")
    service = MT5Service(settings, gateway)
    bars = service.bars("EURUSD", timeframe, 50)
    assert len(bars) == 1
    assert gateway.terminal.request == ("EURUSD", constant, 1, 50)
    with pytest.raises(ConnectorError):
        service.bars("EURUSD", "M1", 50)
    with pytest.raises(ConnectorError):
        service.bars("EURUSD", "M15", 1001)


def test_endpoint_requires_authentication_and_bounds_count(tmp_path):
    settings = ConnectorSettings(token="t" * 24, journal_path=tmp_path / "journal")
    service = MT5Service(settings, Gateway())
    client = TestClient(create_app(settings, service))
    assert client.get("/v1/bars/EURUSD").status_code == 401
    headers = {"X-Connector-Token": "t" * 24}
    assert client.get("/v1/bars/EURUSD?count=1001", headers=headers).status_code == 422
    response = client.get("/v1/bars/EURUSD?count=50", headers=headers)
    assert response.status_code == 200
    assert len(response.json()["bars"]) == 1
