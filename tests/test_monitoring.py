import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from trade_agent.app import TelegramApplicationFacade
from trade_agent.autonomy import AutonomyController, scheduled
from trade_agent.config import Settings
from trade_agent.db import DashboardCacheRecord
from trade_agent.main import build_runtime
from trade_agent.monitoring import worker_status
from trade_agent.reporting import Reporter, dashboard_data, put_cache


def runtime(tmp_path):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'monitor.db'}",
        auto_create_schema=True,
        telegram_owner_id="100",
    )
    facade, _ = build_runtime(settings)
    return facade, TelegramApplicationFacade(facade)


@pytest.mark.asyncio
async def test_monitoring_commands_are_owner_only_and_do_not_trade(tmp_path):
    facade, chat = runtime(tmp_path)
    try:
        for name in ("autonomy", "performance", "decisions", "diagnostics"):
            with pytest.raises(PermissionError):
                await getattr(chat, name)(owner_id="other")
        Reporter(facade.service).collect()
        assert "not_observed" in (await chat.autonomy(owner_id="100")).text
        text = (await chat.performance(owner_id="100")).text
        assert "Manual:" in text and "Autonomous:" in text
        assert "No autonomous" in (await chat.decisions(owner_id="100")).text
        diagnosis = (await chat.diagnostics(owner_id="100")).text
        assert "Recorded decisions: 0" in diagnosis
        assert "No trade was placed" in diagnosis
        assert "Usage:" in (await chat.performance(owner_id="100", scope="bad")).text
        assert not facade.service.broker.state(facade.settings.account_id).positions
    finally:
        await chat.aclose()


def test_heartbeat_is_fresh_stale_stopped_and_account_bound(tmp_path):
    facade, _ = runtime(tmp_path)
    db = facade.service.database
    payload = {"account_id": facade.settings.account_id, "status": "running", "mode": "shadow"}
    put_cache(db, "autonomy", payload)
    assert worker_status(db, facade.settings.account_id)["state"] == "running"
    assert worker_status(db, "other")["state"] == "not_observed"
    with db.session() as session:
        row = session.get(DashboardCacheRecord, "autonomy")
        row.updated_at = datetime.now(UTC) - timedelta(seconds=40)
    assert worker_status(db, facade.settings.account_id)["state"] == "stale"
    put_cache(db, "autonomy", {**payload, "status": "stopped"})
    assert worker_status(db, facade.settings.account_id)["state"] == "stopped"
    Reporter(facade.service).collect()
    assert dashboard_data(db)["autonomy_worker"]["state"] == "stopped"


@pytest.mark.asyncio
async def test_scheduled_worker_heartbeat_does_not_depend_on_model_completion(tmp_path):
    facade, _ = runtime(tmp_path)
    facade.settings.autonomy_mode = "shadow"
    facade.settings.allowed_symbols = ["EURUSD"]
    controller = AutonomyController(facade.service)
    entered = asyncio.Event()

    async def stalled(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    controller.run = stalled
    task = asyncio.create_task(scheduled(controller))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        for _ in range(40):
            observed = worker_status(facade.service.database, facade.settings.account_id)
            if observed["state"] == "running":
                break
            await asyncio.sleep(0.05)
        assert observed["state"] == "running"
        assert observed["phase"] == "research"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert worker_status(facade.service.database, facade.settings.account_id)["state"] == "stopped"
