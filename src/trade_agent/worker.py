"""Background monitoring. No AI is involved in this loop."""

from __future__ import annotations

import asyncio
import logging
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

    async def start(self) -> None:
        if self._task is None:
            self._stopped.clear()
            self._task = asyncio.create_task(self._run(), name="trade-agent-monitor")

    async def stop(self) -> None:
        if self._task is not None:
            self._stopped.set()
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        deadline_elapsed = 0.0
        while not self._stopped.is_set():
            try:
                self.service.reconcile()
                if deadline_elapsed <= 0:
                    self.service.process_deadlines()
                    deadline_elapsed = self.service.settings.deadline_interval_seconds
                await self._deliver_notifications()
            except Exception:
                logger.exception("monitor cycle failed")
            sleep_for = min(
                self.service.settings.monitor_interval_seconds, max(deadline_elapsed, 0.1)
            )
            await asyncio.sleep(sleep_for)
            deadline_elapsed -= sleep_for

    async def _deliver_notifications(self) -> None:
        if self.notify is None:
            return
        for item in self.service.pending_notifications():
            try:
                await self.notify(item.owner_id, item.text)
                self.service.mark_notification_delivered(item.id)
            except Exception:
                logger.exception("notification delivery failed", extra={"outbox_id": item.id})
