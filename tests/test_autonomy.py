from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from trade_agent.api import create_api
from trade_agent.autonomy import AutonomyController
from trade_agent.autonomy_policy import GrantRequest
from trade_agent.config import Settings
from trade_agent.main import build_runtime
from trade_agent.market_data import MarketDataCollector


class Research:
    calls = 0

    async def analyze(self, snapshot: dict) -> dict:
        self.calls += 1
        return {
            "direction": "buy",
            "confidence": 0.9,
            "reason": "test setup",
            "evidence_ids": ["price"],
            "reports": [{"role": "judge"}],
        }


class Collector:
    def __init__(self, broker):
        self.snapshot = MarketDataCollector(broker).collect("EURUSD", "M15", 200)
        self.snapshot["indicators"].update(
            ema_fast="1.081", ema_slow="1.080", rsi="60", atr="0.0005"
        )

    def collect(self, *args):
        return dict(self.snapshot)


def setup(tmp_path):
    cfg = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'autonomy.db'}",
        auto_create_schema=True,
        telegram_owner_id="100",
        allowed_symbols=["EURUSD"],
        autonomy_mode="demo",
        http_api_token="test-token",
    )
    facade, _ = build_runtime(cfg)
    research = Research()
    controller = AutonomyController(facade.service, Collector(facade.service.broker), research)
    return facade, controller, research


def grant(controller):
    return controller.create_grant(
        GrantRequest(
            owner_id="100",
            symbols=["EURUSD"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            max_risk_amount="5",
            max_volume="0.10",
        )
    )


@pytest.mark.asyncio
async def test_shadow_is_recorded_once_without_orders(tmp_path):
    facade, controller, research = setup(tmp_path)
    result = await controller.run("100", "EURUSD")
    assert result["status"] == "shadow"
    assert result["decision"]["hypothetical_instruction"]["stop_loss"]
    assert not facade.service.broker.state("demo-mt5").positions
    again = await controller.run("100", "EURUSD")
    assert again["duplicate"] and research.calls == 1
    assert len(controller.runs()) == 1


@pytest.mark.asyncio
async def test_demo_requires_grant_and_submits_once_with_origin(tmp_path):
    facade, controller, research = setup(tmp_path)
    with pytest.raises(ValueError, match="grant"):
        await controller.run("100", "EURUSD", mode="demo")
    grant(controller)
    result = await controller.run("100", "EURUSD", mode="demo")
    assert result["status"] == "submitted", result
    assert len(facade.service.broker.state("demo-mt5").positions) == 1
    with facade.service.database.session() as session:
        from trade_agent.db import ProposalRecord

        record = session.get(ProposalRecord, result["proposal_id"])
        assert record.origin == "autonomous"
        assert record.decision_run_id == result["id"]
    assert (await controller.run("100", "EURUSD", mode="demo"))["duplicate"]
    assert research.calls == 1


@pytest.mark.asyncio
async def test_revocation_during_research_prevents_submission(tmp_path):
    facade, controller, _ = setup(tmp_path)
    grant(controller)
    original = controller.researcher.analyze

    async def revoke_first(snapshot):
        controller.revoke("100")
        return await original(snapshot)

    controller.researcher.analyze = revoke_first
    result = await controller.run("100", "EURUSD", mode="demo")
    assert result["status"] in {"blocked", "rejected"}, result
    assert result["reports"]
    assert not facade.service.broker.state("demo-mt5").positions


@pytest.mark.asyncio
async def test_registered_strategy_disagrees_and_waits(tmp_path):
    _, controller, _ = setup(tmp_path)
    controller.collector.snapshot["indicators"]["rsi"] = "90"
    result = await controller.run("100", "EURUSD")
    assert result["status"] == "wait"
    assert result["decision"]["direction"] == "wait"


@pytest.mark.asyncio
async def test_market_failure_is_wait_without_model_call(tmp_path):
    _, controller, research = setup(tmp_path)

    def missing(*args):
        raise ValueError("no candles")

    controller.collector.collect = missing
    result = await controller.run("100", "EURUSD")
    assert result["status"] == "data_unavailable" and research.calls == 0
    assert controller.runs() == []


@pytest.mark.asyncio
async def test_incomplete_research_is_not_a_market_wait_decision(tmp_path):
    _, controller, _ = setup(tmp_path)

    async def failed(snapshot):
        return {
            "direction": "wait",
            "reason": "Research abstained: invalid response",
            "reports": [],
            "research_health": {"judge": {"status": "failed"}},
        }

    controller.researcher.analyze = failed
    result = await controller.run("100", "EURUSD")
    assert result["status"] == "research_failed"
    assert "did not complete" in result["decision"]["execution_gate"]


@pytest.mark.asyncio
async def test_repeated_data_outage_is_one_observation_and_recovers(tmp_path):
    from trade_agent.autonomy_health import data_health
    from trade_agent.market_data import MarketDataError

    facade, controller, research = setup(tmp_path)
    collect = controller.collector.collect

    def unavailable(*args):
        raise MarketDataError("stale", code="stale_quote", quote_at="2026-10-02T21:00:00+00:00")

    controller.collector.collect = unavailable
    first = await controller.run("100", "EURUSD")
    for _ in range(3):
        again = await controller.run("100", "EURUSD")
    assert first["market_data"]["unavailable_since"] == again["market_data"]["unavailable_since"]
    assert research.calls == 0 and controller.runs() == []
    health = data_health(facade.service.database, facade.settings.account_id)
    assert len(health) == 1 and health[0]["status"] == "unavailable"
    assert data_health(facade.service.database, "another-account") == []
    controller.collector.collect = collect
    result = await controller.run("100", "EURUSD")
    assert result["status"] == "shadow" and research.calls == 1
    health = data_health(facade.service.database, facade.settings.account_id)
    assert health[0]["status"] == "available" and health[0]["unavailable_since"] is None


@pytest.mark.asyncio
async def test_legacy_missing_data_rows_do_not_bury_research(tmp_path):
    from trade_agent.autonomy_policy import STRATEGY_VERSION
    from trade_agent.db import AutonomousRunRecord
    from trade_agent.reporting import Reporter, dashboard_data

    facade, controller, _ = setup(tmp_path)
    result = await controller.run("100", "EURUSD")
    stamp = datetime.now(UTC)
    with facade.service.database.session() as session:
        session.add(AutonomousRunRecord(id="legacy-outage", account_id=facade.settings.account_id,
            owner_id="100", symbol="EURUSD", timeframe="M15", bar_at=stamp,
            strategy_version=STRATEGY_VERSION, mode="shadow", status="wait",
            snapshot={"data_status": "unavailable"}, decision={"direction": "wait"}, reports=[],
            created_at=stamp, updated_at=stamp))
    assert [r["id"] for r in controller.runs()] == [result["id"]]
    Reporter(facade.service).collect()
    assert [r["id"] for r in dashboard_data(facade.service.database)["autonomy_runs"]] == [result["id"]]


def test_grant_boundaries_and_api_auth(tmp_path):
    facade, controller, _ = setup(tmp_path)
    request = GrantRequest(
        owner_id="100",
        symbols=["EURUSD"],
        expires_at=datetime.now(UTC) + timedelta(days=2),
        max_risk_amount="5",
        max_volume="0.10",
    )
    with pytest.raises(ValueError, match="24 hours"):
        controller.create_grant(request)
    with pytest.raises(ValueError, match="owner"):
        controller.create_grant(request.model_copy(update={"owner_id": "other"}))
    with TestClient(create_api(facade)) as client:
        assert client.get("/v1/autonomy/status").status_code == 401
        headers = {"X-Trade-Agent-Token": "test-token"}
        assert client.get("/v1/autonomy/status", headers=headers).status_code == 200
        assert (
            client.post(
                "/v1/autonomy/revoke", headers=headers, json={"owner_id": "other"}
            ).status_code
            == 403
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expiry", "account", "profile", "mode", "risk"])
async def test_authorization_changes_during_research_fail_closed(tmp_path, change):
    facade, controller, _ = setup(tmp_path)
    authorization = grant(controller)
    original = controller.researcher.analyze

    async def change_first(snapshot):
        if change in {"expiry", "account"}:
            from trade_agent.db import AutonomyGrantRecord

            with facade.service.database.session() as session:
                row = session.get(AutonomyGrantRecord, authorization["id"])
                if change == "expiry":
                    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                else:
                    row.account_id = "other-account"
        elif change == "profile":
            controller.settings.codex_model = "changed-profile"
        elif change == "mode":
            controller.settings.autonomy_mode = "shadow"
        else:
            from decimal import Decimal

            controller.settings.max_estimated_loss = Decimal("0.01")
        return await original(snapshot)

    controller.researcher.analyze = change_first
    result = await controller.run("100", "EURUSD", mode="demo")
    assert result["status"] in {"blocked", "rejected"}, result
    assert not facade.service.broker.state("demo-mt5").positions


@pytest.mark.asyncio
async def test_unknown_submission_is_not_retried(tmp_path):
    facade, controller, research = setup(tmp_path)
    grant(controller)
    from trade_agent.broker import BrokerUnavailable

    calls = []

    def timeout(*args):
        calls.append(args)
        raise BrokerUnavailable("outcome uncertain")

    facade.service.broker.submit = timeout
    result = await controller.run("100", "EURUSD", mode="demo")
    assert result["status"] == "unknown", result
    assert (await controller.run("100", "EURUSD", mode="demo"))["duplicate"]
    assert len(calls) == research.calls == 1


@pytest.mark.asyncio
async def test_daily_submission_quota_counts_previous_entry(tmp_path):
    facade, controller, _ = setup(tmp_path)
    authorization = grant(controller)
    from trade_agent.db import AutonomyGrantRecord

    with facade.service.database.session() as session:
        row = session.get(AutonomyGrantRecord, authorization["id"])
        row.payload = {**row.payload, "max_trades_per_day": 1}
    result = await controller.run("100", "EURUSD", mode="demo")
    assert result["status"] == "submitted"
    facade.service.broker._positions.clear()
    previous = datetime.fromisoformat(controller.collector.snapshot["closed_bar_at"])
    controller.collector.snapshot["closed_bar_at"] = (previous + timedelta(minutes=15)).isoformat()
    result = await controller.run("100", "EURUSD", mode="demo")
    assert result["status"] == "blocked", result
    assert "quota" in result["error"]
    assert not facade.service.broker.state("demo-mt5").positions


def test_autonomy_migration_roundtrip(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("TRADE_AGENT_DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(url)
    assert "autonomous_runs" in inspect(engine).get_table_names()
    assert "origin" in {c["name"] for c in inspect(engine).get_columns("proposals")}
    command.downgrade(config, "20260930_04")
    assert "autonomous_runs" not in inspect(engine).get_table_names()
    assert "origin" not in {c["name"] for c in inspect(engine).get_columns("proposals")}
    command.upgrade(config, "head")
    assert "autonomy_grants" in inspect(engine).get_table_names()
    engine.dispose()


@pytest.mark.asyncio
async def test_exposure_opened_during_research_blocks_entry(tmp_path):
    facade, controller, _ = setup(tmp_path)
    controller.settings.max_concurrent_exposures = 5
    grant(controller)
    original = controller.researcher.analyze

    async def open_first(snapshot):
        from trade_agent.domain import OrderType

        instruction = controller._instruction("100", "EURUSD", "buy", snapshot, None)
        facade.service.broker.submit(instruction, OrderType.MARKET, "external-trade")
        return await original(snapshot)

    controller.researcher.analyze = open_first
    result = await controller.run("100", "EURUSD", mode="demo")
    assert result["status"] == "rejected", result
    assert "same-symbol" in result["decision"]["execution"]["message"]
    assert len(facade.service.broker.state("demo-mt5").positions) == 1
