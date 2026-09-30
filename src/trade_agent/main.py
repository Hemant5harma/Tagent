"""Process composition. Broker credentials remain inside the connector process."""

from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI

from trade_agent.api import create_api
from trade_agent.app import ApplicationFacade, TelegramApplicationFacade
from trade_agent.broker import FakeBroker, MT5HTTPBroker
from trade_agent.config import Settings
from trade_agent.db import Database
from trade_agent.logging_config import configure_logging
from trade_agent.service import TradeService
from trade_agent.worker import TradeWorker


def build_runtime(settings: Settings | None = None) -> tuple[ApplicationFacade, TradeWorker]:
    config = settings or Settings()
    config.ensure_local_directories()
    database = Database(config.database_url)
    # Schema changes are owned by Alembic. Local fake-broker demonstrations may
    # opt in to this convenience path, but it is deliberately disabled by default.
    if config.auto_create_schema:
        database.create_all()
    broker = (
        FakeBroker(config.account_id)
        if config.broker_mode == "fake"
        else MT5HTTPBroker(config.mt5_connector_url, config.mt5_connector_token)
    )
    service = TradeService(config, database, broker)
    facade = ApplicationFacade(config, service)
    return facade, TradeWorker(service)


def main() -> None:
    settings = Settings()
    configure_logging(settings)
    facade, worker = build_runtime(settings)
    # Protection runs separately so model calls cannot hold up broker monitoring.
    api = create_api(facade)
    if not settings.telegram_bot_token:
        uvicorn.run(api, host=settings.host, port=settings.port)
        return
    asyncio.run(_serve_with_telegram(api, facade, worker, settings))


async def _serve_with_telegram(
    api: FastAPI, facade: ApplicationFacade, worker: TradeWorker, settings: Settings
) -> None:
    from trade_agent.telegram import build_telegram_application

    telegram_facade = TelegramApplicationFacade(facade)
    telegram = build_telegram_application(settings, telegram_facade)

    async def send_notification(owner_id: str, text: str) -> None:
        await telegram.bot.send_message(chat_id=owner_id, text=text)

    worker.notify = send_notification
    server = uvicorn.Server(uvicorn.Config(api, host=settings.host, port=settings.port))
    await telegram.initialize()
    await telegram.start()
    if telegram.updater is None:
        raise RuntimeError("Telegram application does not provide an updater")
    from trade_agent.polling import deliver, process, receive

    db = facade.service.database
    tasks = [
        asyncio.create_task(receive(telegram, db, str(settings.telegram_owner_id))),
        asyncio.create_task(process(telegram, db)),
        asyncio.create_task(deliver(telegram, db)),
    ]
    try:
        await server.serve()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await telegram.stop()
        await telegram.shutdown()
        await telegram_facade.aclose()


if __name__ == "__main__":
    main()
