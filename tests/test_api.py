from __future__ import annotations

from fastapi.testclient import TestClient

from trade_agent.api import create_api
from trade_agent.config import Settings
from trade_agent.main import build_runtime


def test_api_creates_then_confirms_a_human_reviewed_trade(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        auto_create_schema=True,
        account_id="demo-mt5",
        allowed_symbols=["EURUSD"],
        telegram_owner_id="100",
        http_api_token="test-http-token",
    )
    facade, _ = build_runtime(settings)

    with TestClient(create_api(facade)) as client:
        headers = {"X-Trade-Agent-Token": "test-http-token"}
        forbidden = client.get("/v1/account")
        assert forbidden.status_code == 401
        created = client.post(
            "/v1/proposals",
            headers=headers,
            json={
                "owner_id": "100",
                "symbol": "EURUSD",
                "side": "buy",
                "volume": "0.10",
                "entry_price": None,
                "stop_loss": "1.07900",
                "take_profit": "1.08200",
            },
        )
        assert created.status_code == 200
        proposal = created.json()

        confirmed = client.post(
            f"/v1/proposals/{proposal['proposal_id']}/confirm",
            headers=headers,
            json={
                "owner_id": "100",
                "version": proposal["version"],
                "confirmation_token": proposal["confirmation_token"],
            },
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "submitted"
        assert len(client.get("/v1/positions", headers=headers).json()) == 1
        read_back = client.get(f"/v1/proposals/{proposal['proposal_id']}", headers=headers)
        assert "confirmation_token" not in read_back.json()


def test_api_protection_change_needs_a_separate_confirmation(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'api-protection.db'}",
        auto_create_schema=True,
        account_id="demo-mt5",
        allowed_symbols=["EURUSD"],
        telegram_owner_id="100",
        http_api_token="test-http-token",
    )
    facade, _ = build_runtime(settings)
    headers = {"X-Trade-Agent-Token": "test-http-token"}

    with TestClient(create_api(facade)) as client:
        created = client.post(
            "/v1/proposals",
            headers=headers,
            json={
                "owner_id": "100",
                "symbol": "EURUSD",
                "side": "buy",
                "volume": "0.10",
                "entry_price": None,
                "stop_loss": "1.07900",
                "take_profit": "1.08200",
            },
        ).json()
        client.post(
            f"/v1/proposals/{created['proposal_id']}/confirm",
            headers=headers,
            json={
                "owner_id": "100",
                "version": created["version"],
                "confirmation_token": created["confirmation_token"],
            },
        )
        position = client.get("/v1/positions", headers=headers).json()[0]
        protection = client.post(
            "/v1/protection/proposals",
            headers=headers,
            json={
                "owner_id": "100",
                "position_id": position["position_id"],
                "stop_loss": "1.07800",
                "take_profit": "1.08300",
            },
        )
        assert protection.status_code == 200
        proposal = protection.json()
        before_confirmation = client.get("/v1/positions", headers=headers).json()[0]
        assert before_confirmation["stop_loss"] == "1.07900"

        confirmed = client.post(
            f"/v1/protection/proposals/{proposal['proposal_id']}/confirm",
            headers=headers,
            json={
                "owner_id": "100",
                "version": proposal["version"],
                "confirmation_token": proposal["confirmation_token"],
            },
        )
        assert confirmed.json()["status"] == "completed"
        after_confirmation = client.get("/v1/positions", headers=headers).json()[0]
        assert after_confirmation["stop_loss"] == "1.07800"
