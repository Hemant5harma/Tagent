from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mt5_connector.app import create_app
from mt5_connector.contracts import ProfitTargetRequest
from mt5_connector.service import ConnectorError, MT5Gateway, MT5Service
from mt5_connector.settings import ConnectorSettings


class CalculatorMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self):
        self.currency = "USD"
        self.demo = 0
        self.calls = 0
        self.result_override = "normal"

    def terminal_info(self):
        return SimpleNamespace(trade_allowed=True)

    def account_info(self):
        return SimpleNamespace(
            login=123,
            currency=self.currency,
            balance=10000,
            equity=10000,
            margin_free=10000,
            trade_mode=self.demo,
        )

    def symbol_select(self, symbol, enabled):
        return symbol == "EURUSD"

    def symbol_info(self, symbol):
        return SimpleNamespace(
            trade_tick_size=0.00001, volume_step=0.01, volume_min=0.01, volume_max=100
        )

    def order_calc_profit(self, side, symbol, volume, entry, close):
        self.calls += 1
        if self.result_override != "normal":
            return self.result_override
        sign = 1 if side == 0 else -1
        return float(
            (Decimal(str(close)) - Decimal(str(entry))) * Decimal(str(volume)) * 100000 * sign
        )

    def order_send(self, request):
        raise AssertionError("profit calculation must never submit a trade")

    def shutdown(self):
        pass


def setup(tmp_path):
    mt5 = CalculatorMT5()
    settings = ConnectorSettings(token="test-token", journal_path=tmp_path / "journal.jsonl")
    return MT5Service(settings, MT5Gateway(settings, mt5)), mt5, settings


def request(**changes):
    values = dict(
        account_id="123",
        symbol="EURUSD",
        side="sell",
        volume="0.05",
        entry_price="1.10000",
        target_usd="10",
    )
    values.update(changes)
    return ProfitTargetRequest(**values)


@pytest.mark.parametrize("side,expected", [("sell", "1.09800"), ("buy", "1.10200")])
def test_tick_aligned_gross_target_no_order(tmp_path, side, expected):
    service, mt5, _ = setup(tmp_path)
    result = service.profit_target(request(side=side))
    assert Decimal(result["take_profit"]) == Decimal(expected)
    assert Decimal(result["estimated_gross_profit"]) == 10
    assert mt5.calls < 84


def test_rounds_to_first_profitable_broker_tick(tmp_path):
    service, _, _ = setup(tmp_path)
    result = service.profit_target(request(target_usd="10.01"))
    assert Decimal(result["take_profit"]) == Decimal("1.09799")
    assert Decimal(result["estimated_gross_profit"]) == Decimal("10.05")


@pytest.mark.parametrize(
    "changes",
    [
        {"volume": "0.015"},
        {"entry_price": "1.100001"},
        {"account_id": "999"},
        {"symbol": "UNKNOWN"},
    ],
)
def test_invalid_broker_input_rejected(tmp_path, changes):
    service, _, _ = setup(tmp_path)
    with pytest.raises(ConnectorError):
        service.profit_target(request(**changes))


@pytest.mark.parametrize(
    "field,value",
    [
        ("target_usd", "NaN"),
        ("target_usd", "0"),
        ("target_usd", "100001"),
        ("side", "hold"),
        ("volume", "Infinity"),
    ],
)
def test_contract_rejects_invalid_values(field, value):
    with pytest.raises(ValidationError):
        request(**{field: value})


@pytest.mark.parametrize("currency,demo", [("EUR", 0), ("USD", 2)])
def test_wrong_currency_or_live_account_rejected(tmp_path, currency, demo):
    service, mt5, _ = setup(tmp_path)
    mt5.currency, mt5.demo = currency, demo
    with pytest.raises(ConnectorError):
        service.profit_target(request())
    assert mt5.calls == 0


@pytest.mark.parametrize("bad_result", [None, float("nan"), float("inf"), -1])
def test_unavailable_invalid_calculation_fails_closed(tmp_path, bad_result):
    service, mt5, _ = setup(tmp_path)
    mt5.result_override = bad_result
    with pytest.raises(ConnectorError):
        service.profit_target(request())


def test_authenticated_read_only_route(tmp_path: Path):
    service, _, settings = setup(tmp_path)
    with TestClient(create_app(settings, service)) as client:
        body = request().model_dump(mode="json")
        assert client.post("/v1/profit-target", json=body).status_code == 401
        response = client.post(
            "/v1/profit-target", json=body, headers={"X-Connector-Token": "test-token"}
        )
        assert response.status_code == 200
        assert Decimal(response.json()["estimated_gross_profit"]) == 10
