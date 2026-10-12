from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from mt5_connector.contracts import TradeInstructionPayload
from mt5_connector.service import MT5Gateway, MT5Service, TerminalUnavailable
from mt5_connector.settings import ConnectorSettings


class Result(SimpleNamespace):
    def _asdict(self) -> dict[str, object]:
        return vars(self)


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    SYMBOL_TRADE_MODE_DISABLED = 0
    SYMBOL_FILLING_FOK = 1
    SYMBOL_FILLING_IOC = 2
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4
    ORDER_TYPE_SELL_STOP = 5
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    ORDER_TIME_GTC = 0
    ORDER_FILLING_RETURN = 2
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE_PARTIAL = 10010
    POSITION_TYPE_BUY = 0

    def __init__(self) -> None:
        self.checks: list[dict[str, object]] = []
        self.submissions: list[dict[str, object]] = []

    def terminal_info(self) -> SimpleNamespace:
        return SimpleNamespace(trade_allowed=True)

    def initialize(self, *_: object, **__: object) -> bool:
        return True

    def shutdown(self) -> None:
        return None

    def last_error(self) -> tuple[int, str]:
        return (0, "ok")

    def account_info(self) -> SimpleNamespace:
        return SimpleNamespace(
            login=12345,
            currency="USD",
            balance=10000,
            equity=10000,
            margin_free=9000,
            trade_mode=self.ACCOUNT_TRADE_MODE_DEMO,
            trade_allowed=True,
            server="demo-server",
        )

    def symbol_select(self, symbol: str, enabled: bool) -> bool:
        return symbol == "EURUSD" and enabled

    def symbol_info(self, symbol: str) -> SimpleNamespace | None:
        if symbol != "EURUSD":
            return None
        return SimpleNamespace(
            point=0.00001,
            digits=5,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_stops_level=10,
            trade_mode=1,
            filling_mode=self.SYMBOL_FILLING_FOK,
            visible=True,
        )

    def symbol_info_tick(self, symbol: str) -> SimpleNamespace | None:
        if symbol != "EURUSD":
            return None
        return SimpleNamespace(bid=1.08000, ask=1.08010, time=1_700_000_000)

    def order_check(self, request: dict[str, object]) -> Result:
        self.checks.append(request)
        return Result(retcode=0, margin=12.5, comment="Done")

    def order_send(self, request: dict[str, object]) -> Result:
        self.submissions.append(request)
        return Result(retcode=self.TRADE_RETCODE_DONE, order=5001, deal=6001, comment="Done")

    def orders_get(self, *_: object, **__: object) -> list[SimpleNamespace]:
        return []

    def positions_get(self, *_: object, **__: object) -> list[SimpleNamespace]:
        return []

    def history_orders_get(self, *_: object) -> list[SimpleNamespace]:
        return []

    def history_deals_get(self, *_: object, ticket: int | None = None) -> list[SimpleNamespace]:
        if ticket is not None:
            assert ticket == 6001
            return [SimpleNamespace(position_id=7001)]
        return [SimpleNamespace(ticket=6001, position_id=7001, comment="ta:" + "a" * 16)]


def test_market_submission_uses_broker_protection_and_returns_terminal_ids(tmp_path: Path) -> None:
    settings = ConnectorSettings(token="t" * 24, journal_path=tmp_path / "journal.jsonl")
    mt5 = FakeMT5()
    service = MT5Service(settings, MT5Gateway(settings, module=mt5))
    instruction = TradeInstructionPayload(
        owner_id="owner",
        account_id="12345",
        symbol="eurusd",
        side="buy",
        volume=Decimal("0.10"),
        stop_loss=Decimal("1.07900"),
        take_profit=Decimal("1.08100"),
    )

    result = service.submit(instruction, "market", "a" * 16)

    assert result.accepted is True
    assert (result.order_id, result.deal_id, result.position_id) == ("5001", "6001", "7001")
    assert len(mt5.checks) == 1
    assert len(mt5.submissions) == 1
    assert mt5.submissions[0]["sl"] == 1.079
    assert mt5.submissions[0]["tp"] == 1.081


def test_connected_terminal_must_match_configured_login_and_server(tmp_path: Path) -> None:
    mt5 = FakeMT5()
    wrong_login = ConnectorSettings(
        token="t" * 24, journal_path=tmp_path / "journal.jsonl", login=99999, server="demo-server"
    )

    with pytest.raises(TerminalUnavailable, match="MT5_CONNECTOR_LOGIN"):
        MT5Gateway(wrong_login, module=mt5).connect()

    wrong_server = ConnectorSettings(
        token="t" * 24, journal_path=tmp_path / "journal.jsonl", login=12345, server="other-server"
    )
    with pytest.raises(TerminalUnavailable, match="MT5_CONNECTOR_SERVER"):
        MT5Gateway(wrong_server, module=mt5).connect()


@pytest.mark.parametrize("supported", [False, True])
def test_pending_expiry_rejects_unsupported_without_gtc_fallback(tmp_path, supported):
    settings = ConnectorSettings(token="t" * 24, journal_path=tmp_path / "journal.jsonl")
    mt5 = FakeMT5()
    mt5.ORDER_TIME_SPECIFIED = 2
    original = mt5.symbol_info

    def info(symbol):
        value = original(symbol)
        value.expiration_mode = 4 if supported else 0
        return value

    mt5.symbol_info = info
    service = MT5Service(settings, MT5Gateway(settings, module=mt5))
    expiry = datetime.now(UTC) + timedelta(hours=2)
    instruction = TradeInstructionPayload(
        owner_id="owner",
        account_id="12345",
        symbol="EURUSD",
        side="buy",
        volume=Decimal(".1"),
        entry_price=Decimal("1.07"),
        stop_loss=Decimal("1.06"),
        take_profit=Decimal("1.08"),
        pending_expires_at=expiry,
        requested_order_type="buy_limit",
    )
    _, request, _, error = service._request_for(instruction, "buy_limit", "a" * 16)
    if supported:
        assert error is None and request["type_time"] == 2 and request["expiration"]
    else:
        assert "GTC was not substituted" in error
    assert mt5.submissions == []


def test_reconciliation_finds_historical_deal_by_idempotency_comment(tmp_path: Path) -> None:
    settings = ConnectorSettings(token="t" * 24, journal_path=tmp_path / "journal.jsonl")
    service = MT5Service(settings, MT5Gateway(settings, module=FakeMT5()))

    resolved = service.reconcile_submission("a" * 16, {"status": "started"})

    assert resolved["status"] == "accepted"
    assert resolved["deal_ids"] == ["6001"]
    assert resolved["position_ids"] == ["7001"]
