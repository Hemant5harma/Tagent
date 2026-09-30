"""Commit authorized Telegram payloads before advancing getUpdates offset."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

from sqlalchemy import delete, select
from telegram import Update
from telegram.ext import Application

from trade_agent.db import Database, IncomingUpdateRecord, SystemStateRecord
from trade_agent.reporting import now, put_cache
from trade_agent.telegram import _confirmation_markup

logger = logging.getLogger(__name__)


async def receive(application: Application, db: Database, owner_id: str) -> None:
    while True:
        try:
            with db.session() as session:
                cursor = session.get(SystemStateRecord, "telegram_offset")
                offset = int(cursor.value) if cursor else 0
            updates = await application.bot.get_updates(
                offset=offset, timeout=25, allowed_updates=["message", "callback_query"]
            )
            with db.session() as session:
                for update in updates:
                    user, chat = update.effective_user, update.effective_chat
                    if user and chat and str(user.id) == owner_id and chat.type == "private":
                        row = session.get(IncomingUpdateRecord, str(update.update_id))
                        if row is None:
                            session.add(
                                IncomingUpdateRecord(
                                    update_id=str(update.update_id),
                                    owner_id=owner_id,
                                    status="received",
                                    received_at=now(),
                                    payload=json.loads(update.to_json()),
                                )
                            )
                    offset = max(offset, update.update_id + 1)
                cursor = session.get(SystemStateRecord, "telegram_offset")
                if cursor:
                    cursor.value, cursor.updated_at = str(offset), now()
                else:
                    session.add(
                        SystemStateRecord(
                            key="telegram_offset", value=str(offset), updated_at=now()
                        )
                    )
            put_cache(db, "telegram", {"status": "ok"})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram receiver unavailable")
            await asyncio.sleep(5)


async def process(application: Application, db: Database) -> None:
    while True:
        try:
            with db.session() as session:
                rows = session.scalars(
                    select(IncomingUpdateRecord)
                    .where(
                        IncomingUpdateRecord.status != "completed",
                        IncomingUpdateRecord.payload.is_not(None),
                    )
                    .order_by(IncomingUpdateRecord.received_at)
                    .limit(20)
                ).all()
            for row in rows:
                if row.payload is None:
                    continue
                update = Update.de_json(row.payload, application.bot)
                await application.process_update(update)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram inbox processing failed; retained for recovery")
        await asyncio.sleep(0.5)


async def deliver(application: Application, db: Database) -> None:
    while True:
        try:
            with db.session() as session:
                rows = session.scalars(
                    select(IncomingUpdateRecord)
                    .where(
                        IncomingUpdateRecord.reply.is_not(None),
                        IncomingUpdateRecord.delivered_at.is_(None),
                    )
                    .order_by(IncomingUpdateRecord.received_at)
                    .limit(20)
                ).all()
            for row in rows:
                reply = row.reply
                if reply is None:
                    continue
                await application.bot.send_message(
                    chat_id=row.owner_id,
                    text=reply["text"],
                    reply_markup=_confirmation_markup(
                        reply.get("confirmation_token"), reply.get("confirmation_kind")
                    ),
                )
                with db.session() as session:
                    stored = session.get(IncomingUpdateRecord, row.update_id)
                    if stored is None:
                        continue
                    stored.delivered_at = now()
                    if "One-use login code:" in reply["text"]:
                        stored.reply = {"text": "Dashboard login code delivered."}
            with db.session() as session:
                session.execute(
                    delete(IncomingUpdateRecord).where(
                        IncomingUpdateRecord.delivered_at < now() - timedelta(days=7)
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram reply delivery failed; retained for retry")
        await asyncio.sleep(1)
