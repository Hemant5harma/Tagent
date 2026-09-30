"""Unsupported Telegram updates must never starve the durable inbox."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select
from telegram import Update

from trade_agent.config import Settings
from trade_agent.db import Database, IncomingUpdateRecord
from trade_agent.polling import process
from trade_agent.telegram import TelegramGateway, TelegramReply


class DurableOperations:
    def __init__(self, db):
        self.db = db
        self.status_calls = 0

    async def record_telegram_update(self, *, update_id, owner_id):
        with self.db.session() as session:
            row = session.get(IncomingUpdateRecord, update_id)
            return row.status != "completed"

    async def complete_telegram_update(self, *, update_id):
        pass  # Real facade commits completion together with its reply.

    async def queue_reply(self, update_id, reply):
        with self.db.session() as session:
            row = session.get(IncomingUpdateRecord, update_id)
            row.status = "completed"
            row.reply = {"text": reply.text}
            row.completed_at = datetime.now(UTC)

    async def status(self, *, owner_id):
        self.status_calls += 1
        return TelegramReply("Entries paused")


def message(update_id, **content):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1,
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 100, "first_name": "Owner", "is_bot": False},
            **content,
        },
    }


def test_attachments_and_malformed_callbacks_do_not_starve_status(tmp_path):
    async def run():
        db = Database(f"sqlite:///{tmp_path / 'inbox.db'}")
        db.create_all()
        operations = DurableOperations(db)
        handlers = []
        gateway = TelegramGateway(
            settings=Settings(_env_file=None, telegram_owner_id="100"), operations=operations
        )
        gateway.install(SimpleNamespace(add_handler=handlers.append))
        bot = AsyncMock()
        bot.defaults = None
        bot.username = "test_bot"
        bot.answer_callback_query.side_effect = RuntimeError("expired callback")
        payloads = [
            message(i, photo=[{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}])
            for i in range(1, 21)
        ]
        for index, data in enumerate(("confirm:trade:bad token", "unknown_button"), 21):
            msg = message(index)["message"]
            payloads.append(
                {
                    "update_id": index,
                    "callback_query": {
                        "id": str(index),
                        "from": msg["from"],
                        "message": msg,
                        "chat_instance": "test",
                        "data": data,
                    },
                }
            )
        payloads.append(
            message(
                23, text="/status", entities=[{"type": "bot_command", "offset": 0, "length": 7}]
            )
        )
        with db.session() as session:
            for payload in payloads:
                session.add(
                    IncomingUpdateRecord(
                        update_id=str(payload["update_id"]),
                        owner_id="100",
                        status="received",
                        received_at=datetime.now(UTC),
                        payload=payload,
                    )
                )
        done = asyncio.Event()

        async def dispatch(update: Update):
            for handler in handlers:
                check = handler.check_update(update)
                if check is not None and check is not False:
                    await handler.callback(update, SimpleNamespace(args=[]))
                    break
            if update.update_id == 23:
                done.set()

        application = SimpleNamespace(bot=bot, process_update=dispatch)
        task = asyncio.create_task(process(application, db))
        try:
            await asyncio.wait_for(done.wait(), timeout=3)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        with db.session() as session:
            rows = session.scalars(select(IncomingUpdateRecord)).all()
            assert len(rows) == 23
            assert all(row.status == "completed" and row.reply for row in rows)
            assert "No action was taken" in session.get(IncomingUpdateRecord, "21").reply["text"]
        assert operations.status_calls == 1
        assert bot.answer_callback_query.await_count == 2

    asyncio.run(run())
