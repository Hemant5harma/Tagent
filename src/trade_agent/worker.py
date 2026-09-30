"""Background monitoring. No AI is involved in this loop."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from trade_agent.service import TradeService

logger = logging.getLogger(__name__)


class TradeWorker:
    def __init__(
        self, service: TradeService, notify: Callable[[str, str], Awaitable[None]] | None = None
    ) -> None:
        self.service = service
        self.notify = notify
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._notifications: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._stopped.clear()
            self._task = asyncio.create_task(self._run(), name="trade-agent-monitor")
            self._notifications = asyncio.create_task(self._notification_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._stopped.set()
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._notifications:
            self._notifications.cancel()
            await asyncio.gather(self._notifications, return_exceptions=True)
            self._notifications = None

    async def _run(self) -> None:
        next_deadline = 0.0
        while not self._stopped.is_set():
            try:
                if time.monotonic() >= next_deadline:
                    await asyncio.to_thread(self.service.process_deadlines)
                    next_deadline = (
                        time.monotonic() + self.service.settings.deadline_interval_seconds
                    )
                from trade_agent.reporting import put_cache

                put_cache(self.service.database, "protection", {"status": "ok"})
            except Exception:
                logger.exception("monitor cycle failed")
            await asyncio.sleep(0.5)

    async def _notification_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await self._deliver_notifications()
            except Exception:
                logger.exception("Notification cycle failed")
            await asyncio.sleep(1)

    async def _deliver_notifications(self) -> None:
        if self.notify is None:
            return
        for item in self.service.pending_notifications():
            try:
                await asyncio.wait_for(self.notify(item.owner_id, item.text), timeout=10)
                self.service.mark_notification_delivered(item.id)
            except Exception:
                logger.exception("notification delivery failed", extra={"outbox_id": item.id})


def main() -> None:
    from telegram import Bot

    from trade_agent.config import Settings
    from trade_agent.logging_config import configure_logging
    from trade_agent.main import build_runtime

    settings = Settings()
    configure_logging(settings)
    _, worker = build_runtime(settings)

    async def run() -> None:
        if settings.telegram_bot_token:
            async with Bot(settings.telegram_bot_token) as bot:

                async def notify(owner_id: str, message: str) -> None:
                    await bot.send_message(chat_id=owner_id, text=message)

                worker.notify = notify
                await worker.start()
                await asyncio.Event().wait()
        else:
            await worker.start()
            await asyncio.Event().wait()

    asyncio.run(run())


if __name__ == "__main__":
    main()
