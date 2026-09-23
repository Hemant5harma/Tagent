from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mt5_connector.app import _fingerprint, create_app
from mt5_connector.contracts import PreflightResponse, SubmissionResponse, SubmitOrderRequest
from mt5_connector.service import TerminalUnavailable
from mt5_connector.settings import ConnectorSettings


class StubService:
    def __init__(self) -> None:
        self.submissions = 0
        self.modifications = 0
        self.cancellations = 0
        self.closures = 0

    def health(self) -> dict[str, object]:
        return {"status": "ok", "mt5_connected": True, "last_error": None}

    def preflight(self, *_: object) -> PreflightResponse:
        return PreflightResponse(accepted=True, estimated_margin="12.50")

    def submit(self, *_: object) -> SubmissionResponse:
        self.submissions += 1
        return SubmissionResponse(
            accepted=True,
            status="accepted",
            order_id="1001",
            raw={"retcode": 10008},
        )

    def modify(self, *_: object) -> None:
        self.modifications += 1

    def cancel(self, *_: object) -> None:
        self.cancellations += 1

    def close(self, *_: object) -> None:
        self.closures += 1


def _client(path: Path) -> tuple[TestClient, StubService]:
    settings = ConnectorSettings(token="t" * 24, journal_path=path)
    service = StubService()
    return TestClient(create_app(settings, service=service)), service  # type: ignore[arg-type]


def _order(key: str = "a" * 16) -> dict[str, object]:
    return {
        "instruction": {
            "owner_id": "owner",
            "account_id": "12345",
            "symbol": "EURUSD",
            "side": "buy",
            "volume": "0.10",
            "entry_price": "1.08000",
            "stop_loss": "1.07000",
            "take_profit": "1.09000",
        },
        "order_type": "buy_limit",
        "idempotency_key": key,
    }


def test_token_and_mutation_responses_are_compatible_with_http_broker(tmp_path: Path) -> None:
    client, service = _client(tmp_path / "requests.jsonl")
    headers = {"X-Connector-Token": "t" * 24}

    assert client.get("/healthz").status_code == 401
    assert client.post("/v1/modify", json={"order_id": "1"}, headers=headers).json() == {
        "status": "accepted"
    }
    assert client.post("/v1/orders/1/cancel", headers=headers).json() == {"status": "accepted"}
    assert client.post("/v1/positions/2/close", json={}, headers=headers).json() == {
        "status": "accepted"
    }
    assert (service.modifications, service.cancellations, service.closures) == (1, 1, 1)


def test_order_idempotency_replays_completed_result_and_never_resubmits(tmp_path: Path) -> None:
    client, service = _client(tmp_path / "requests.jsonl")
    headers = {"X-Connector-Token": "t" * 24}

    first = client.post("/v1/orders", json=_order(), headers=headers)
    second = client.post("/v1/orders", json=_order(), headers=headers)
    changed = _order()
    changed["instruction"] = {**changed["instruction"], "volume": "0.20"}
    conflict = client.post("/v1/orders", json=changed, headers=headers)

    assert first.status_code == 200
    assert first.json() == second.json()
    assert service.submissions == 1
    assert conflict.status_code == 409


def test_incomplete_journal_entry_is_returned_as_unknown(tmp_path: Path) -> None:
    journal_path = tmp_path / "requests.jsonl"
    client, service = _client(journal_path)
    payload = _order()
    request = SubmitOrderRequest.model_validate(payload)
    client.app.state.journal.begin("a" * 16, _fingerprint(request))  # type: ignore[attr-defined]

    response = client.post("/v1/orders", json=payload, headers={"X-Connector-Token": "t" * 24})

    assert response.json()["status"] == "unknown"
    assert service.submissions == 0


def test_mutation_replay_uses_journal_and_does_not_repeat_the_exit(tmp_path: Path) -> None:
    client, service = _client(tmp_path / "requests.jsonl")
    headers = {"X-Connector-Token": "t" * 24}
    payload = {"idempotency_key": "c" * 16}

    first = client.post("/v1/orders/1/cancel", json=payload, headers=headers)
    second = client.post("/v1/orders/1/cancel", json=payload, headers=headers)

    assert first.json() == second.json() == {"status": "accepted"}
    assert service.cancellations == 1


def test_unknown_mutation_is_retained_and_not_sent_again(tmp_path: Path) -> None:
    class UnknownCloseService(StubService):
        def close(self, *_: object) -> None:
            self.closures += 1
            raise TerminalUnavailable("terminal did not return close outcome")

    settings = ConnectorSettings(token="t" * 24, journal_path=tmp_path / "requests.jsonl")
    service = UnknownCloseService()
    client = TestClient(create_app(settings, service=service))  # type: ignore[arg-type]
    headers = {"X-Connector-Token": "t" * 24}
    payload = {"idempotency_key": "d" * 16}

    first = client.post("/v1/positions/2/close", json=payload, headers=headers)
    second = client.post("/v1/positions/2/close", json=payload, headers=headers)

    assert first.json()["status"] == second.json()["status"] == "unknown"
    assert service.closures == 1
