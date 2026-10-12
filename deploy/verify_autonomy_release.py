"""Verify release and restore its website tunnel; no credentials/trades/messages emitted."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import httpx
from sqlalchemy import text

from trade_agent.app import TelegramApplicationFacade
from trade_agent.autonomy import AutonomyController
from trade_agent.config import Settings
from trade_agent.dashboard import issue_code
from trade_agent.db import Database
from trade_agent.main import build_runtime
from trade_agent.market_data import MarketDataCollector
from trade_agent.reporting import dashboard_data, scrub


async def main() -> None:
    os.chdir("/home/jarvis/trade-agent")
    subprocess.run(["systemctl", "start", "trade-agent-tunnel"], check=True)
    settings = Settings()
    facade, _ = build_runtime(settings)
    controller = AutonomyController(facade.service)
    status = controller.status()
    account = facade.service.broker.account(settings.account_id)
    assert account.is_demo and account.currency == "USD"
    assert settings.autonomy_mode == "shadow" and status["active_grant"] is None
    assert status["worker"]["state"] == "running"
    with facade.service.database.engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    candles = {}
    for symbol in settings.allowed_symbols:
        try:
            snapshot = MarketDataCollector(
                facade.service.broker, settings.max_quote_age_seconds
            ).collect(symbol)
            candles[symbol] = {
                "bars": len(snapshot["bars"]),
                "source": snapshot["data_mode"],
                "closed_bar_at": snapshot["closed_bar_at"],
                "indicators": snapshot["indicators"],
            }
        except Exception as exc:
            candles[symbol] = {"error": scrub(str(exc))}
    runs = controller.runs(5)
    run_summary = [
        {
            "symbol": r["symbol"],
            "status": r["status"],
            "mode": r["mode"],
            "reports": len(r["reports"]),
            "direction": r["decision"].get("direction"),
            "reason": scrub(str(r["decision"].get("reason") or r["error"] or ""))[:500],
            "sources": r["snapshot"].get("context", {}).get("sources"),
            "proposal_id": r["proposal_id"],
        }
        for r in runs
    ]
    # Use the actual read-only website database role, not the trading role.
    web_settings = Settings(_env_file=Path("/opt/trade-agent-dashboard/dashboard.env"))
    web_data = dashboard_data(Database(web_settings.database_url))
    assert web_data["autonomy_worker"]["state"] == "running"
    assert web_data["autonomy_runs"]
    async with httpx.AsyncClient(timeout=20) as client:
        headers = {"X-Trade-Agent-Token": settings.http_api_token}
        response = await client.get("http://127.0.0.1:8100/v1/autonomy/status", headers=headers)
        response.raise_for_status()
        assert response.json()["mode"] == "shadow"
        from trade_agent.db import DashboardCacheRecord

        with facade.service.database.session() as session:
            url = session.get(DashboardCacheRecord, "dashboard_url").payload["url"]
        # Wait for the replacement quick-tunnel URL and live origin.
        for _ in range(30):
            with facade.service.database.session() as session:
                url = session.get(DashboardCacheRecord, "dashboard_url").payload["url"]
            try:
                check = await client.get(url + "/healthz")
                if check.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
        # One-use test login code stays inside this process and is consumed immediately.
        code = issue_code(facade.service.database)
        response = await client.post(url + "/dashboard-api/login", json={"code": code})
        response.raise_for_status()
        response = await client.get(url + "/dashboard-api/snapshot?scope=autonomous")
        response.raise_for_status()
        assert response.json()["autonomy_runs"]
        page = await client.get(url)
        page.raise_for_status()
        await client.post(url + "/dashboard-api/logout")
    chat = TelegramApplicationFacade(facade)
    try:
        replies = {}
        for name in ("autonomy", "performance", "decisions"):
            reply = await getattr(chat, name)(owner_id=str(settings.telegram_owner_id))
            replies[name] = scrub(reply.text)[:500]
    finally:
        await chat.aclose()
    print(
        json.dumps(
            {
                "revision": revision,
                "mode": status["mode"],
                "worker": status["worker"],
                "entries_paused": status["entries_paused"],
                "demo_account": account.is_demo,
                "candles": candles,
                "runs": run_summary,
                "website": url,
                "website_authenticated_snapshot": "passed",
                "telegram_facade": replies,
                "no_trade_submission": True,
            },
            default=str,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
