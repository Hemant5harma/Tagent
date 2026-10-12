"""Private Telegram boundary for the channel-independent application service.

This module deliberately knows no broker, database, or AI implementation.  It
authenticates a Telegram update, durably asks the application to accept it, and
then delegates an allowed user operation through ``TelegramOperations``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from trade_agent.config import Settings

LOGGER = logging.getLogger(__name__)
_CALLBACK_RE = re.compile(r"^(confirm|reject):(trade|protection):([A-Za-z0-9_-]{1,56})$")
_PROVIDERS = frozenset({"none", "openai_compatible", "opencode", "codex"})


@dataclass(frozen=True)
class TelegramReply:
    """Safe text returned by the application, optionally with a confirmation."""

    text: str
    confirmation_token: str | None = None
    confirmation_kind: str | None = None


@runtime_checkable
class TelegramOperations(Protocol):
    """Small async facade implemented by the application service.

    Every operation receives the authenticated owner id.  The facade, rather
    than this UI adapter, is responsible for validation, durable update
    deduplication, confirmation expiry, and all trade execution.
    """

    async def record_telegram_update(self, *, update_id: str, owner_id: str) -> bool: ...

    async def complete_telegram_update(self, *, update_id: str) -> None: ...

    async def create_trade_from_text(self, *, owner_id: str, text: str) -> TelegramReply: ...

    async def account(self, *, owner_id: str) -> TelegramReply: ...

    async def orders(self, *, owner_id: str) -> TelegramReply: ...

    async def positions(self, *, owner_id: str) -> TelegramReply: ...

    async def status(self, *, owner_id: str) -> TelegramReply: ...

    async def help(self, *, owner_id: str) -> TelegramReply: ...

    async def autonomy(self, *, owner_id: str) -> TelegramReply: ...

    async def performance(self, *, owner_id: str, scope: str) -> TelegramReply: ...

    async def decisions(self, *, owner_id: str) -> TelegramReply: ...

    async def diagnostics(self, *, owner_id: str) -> TelegramReply: ...

    async def exit_proposal(self, *, owner_id: str, target_id: str, kind: str) -> TelegramReply: ...

    async def pause_entries(self, *, owner_id: str) -> TelegramReply: ...

    async def resume_entries(self, *, owner_id: str) -> TelegramReply: ...

    async def select_ai_provider(self, *, owner_id: str, provider: str) -> TelegramReply: ...

    async def confirm_trade(self, *, owner_id: str, confirmation_token: str) -> TelegramReply: ...

    async def reject_trade(self, *, owner_id: str, confirmation_token: str) -> TelegramReply: ...

    async def create_protection_modification(
        self,
        *,
        owner_id: str,
        order_id: str | None,
        position_id: str | None,
        stop_loss: str,
        take_profit: str,
    ) -> TelegramReply: ...

    async def confirm_protection_modification(
        self, *, owner_id: str, confirmation_token: str
    ) -> TelegramReply: ...

    async def reject_protection_modification(
        self, *, owner_id: str, confirmation_token: str
    ) -> TelegramReply: ...


class TelegramGateway:
    """Registers a private-chat, single-owner Telegram UI on a PTB application."""

    def __init__(self, *, settings: Settings, operations: TelegramOperations) -> None:
        self._owner_id = settings.telegram_owner_id
        self._operations = operations

    def install(self, application: Application) -> None:
        for name in ("help", "start", "new", "cancel_draft", "dashboard", "memory", "forget"):
            application.add_handler(CommandHandler(name, self.utility))
        application.add_handler(CommandHandler("account", self.account))
        application.add_handler(CommandHandler("orders", self.orders))
        application.add_handler(CommandHandler("positions", self.positions))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(CommandHandler("autonomy", self.monitoring))
        application.add_handler(CommandHandler("performance", self.monitoring))
        application.add_handler(CommandHandler("decisions", self.monitoring))
        application.add_handler(CommandHandler("diagnostics", self.monitoring))
        application.add_handler(CommandHandler("pause", self.pause))
        application.add_handler(CommandHandler("resume", self.resume))
        application.add_handler(CommandHandler("provider", self.provider))
        application.add_handler(CommandHandler("protection", self.protection))
        application.add_handler(CommandHandler("close", self.exit_trade))
        application.add_handler(CommandHandler("cancel_order", self.exit_trade))
        # Consume even malformed/stale buttons so durable inbox rows cannot poison
        # the processing queue. The full callback grammar is checked below.
        application.add_handler(CallbackQueryHandler(self.confirmation))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.trade_text))
        application.add_handler(MessageHandler(filters.COMMAND, self.utility))
        application.add_handler(MessageHandler(filters.ALL, self.unsupported_message))

    async def unsupported_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context

        async def reply(owner_id: str) -> TelegramReply:
            del owner_id
            return TelegramReply(
                "Please send your request as text. Photos, voice messages and other attachments "
                "cannot place trades. Use /help for examples."
            )

        await self._dispatch(update, reply)

    async def utility(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        if update.effective_message is None:
            return
        command = (update.effective_message.text or "").split()[0].split("@")[0]
        name = (
            command[1:]
            if command in {"/memory", "/forget"}
            else "reset_draft"
            if command in {"/new", "/cancel_draft"}
            else "dashboard"
            if command == "/dashboard"
            else "help"
        )
        await self._dispatch(
            update, lambda owner_id: getattr(self._operations, name)(owner_id=owner_id)
        )

    async def exit_trade(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None or update.effective_message.text is None:
            return
        target_id = context.args[0] if context.args else ""
        kind = "position" if update.effective_message.text.startswith("/close") else "order"
        if not target_id.isdigit():
            await self._dispatch(update, lambda owner_id: self._operations.help(owner_id=owner_id))
            return
        await self._dispatch(
            update,
            lambda owner_id: self._operations.exit_proposal(
                owner_id=owner_id, target_id=target_id, kind=kind
            ),
        )

    async def account(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._dispatch(update, lambda owner_id: self._operations.account(owner_id=owner_id))

    async def orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._dispatch(update, lambda owner_id: self._operations.orders(owner_id=owner_id))

    async def positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._dispatch(update, lambda owner_id: self._operations.positions(owner_id=owner_id))

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._dispatch(update, lambda owner_id: self._operations.status(owner_id=owner_id))

    async def monitoring(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_message is None:
            return
        command = (update.effective_message.text or "").split()[0].split("@")[0]
        if command == "/performance":
            scope = context.args[0].lower() if context.args else "all"
            await self._dispatch(
                update,
                lambda owner_id: self._operations.performance(owner_id=owner_id, scope=scope),
            )
        elif command == "/autonomy":
            await self._dispatch(
                update, lambda owner_id: self._operations.autonomy(owner_id=owner_id)
            )
        elif command == "/diagnostics":
            await self._dispatch(
                update, lambda owner_id: self._operations.diagnostics(owner_id=owner_id)
            )
        else:
            await self._dispatch(
                update, lambda owner_id: self._operations.decisions(owner_id=owner_id)
            )

    async def pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._dispatch(
            update, lambda owner_id: self._operations.pause_entries(owner_id=owner_id)
        )

    async def resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._dispatch(
            update, lambda owner_id: self._operations.resume_entries(owner_id=owner_id)
        )

    async def provider(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        provider = context.args[0].lower() if context.args else ""
        if provider not in _PROVIDERS:
            await self._reply(
                update,
                TelegramReply("Usage: /provider none|openai_compatible|opencode|codex"),
            )
            return
        await self._dispatch(
            update,
            lambda owner_id: self._operations.select_ai_provider(
                owner_id=owner_id, provider=provider
            ),
        )

    async def protection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = context.args or []
        if len(args) != 4 or args[0].lower() not in {"order", "position"}:
            await self._reply(
                update,
                TelegramReply("Usage: /protection order|position <id> <stop-loss> <take-profit>"),
            )
            return
        target_kind, target_id, stop_loss, take_profit = args
        order_id = target_id if target_kind.lower() == "order" else None
        position_id = target_id if target_kind.lower() == "position" else None
        await self._dispatch(
            update,
            lambda owner_id: self._operations.create_protection_modification(
                owner_id=owner_id,
                order_id=order_id,
                position_id=position_id,
                stop_loss=stop_loss,
                take_profit=take_profit,
            ),
        )

    async def trade_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        message = update.effective_message
        text = message.text if message is not None else ""
        if not text:
            return
        await self._dispatch(
            update,
            lambda owner_id: self._operations.create_trade_from_text(owner_id=owner_id, text=text),
        )

    async def confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        query = update.callback_query
        if query is None:
            return
        owner_id = self._owner_for(update)
        if owner_id is None:
            # A callback must be answered to stop Telegram's client-side spinner,
            # but unauthorized users get no account or proposal details.
            await query.answer("This bot is private.", show_alert=True)
            return
        if not await self._accept_once(update, owner_id):
            await query.answer("Already processed.")
            return
        matched = _CALLBACK_RE.fullmatch(query.data or "")
        if matched is None:
            await self._complete(update)
            await self._reply(
                update,
                TelegramReply("Invalid confirmation button. No action was taken. Use /help."),
            )
            try:
                await query.answer("Invalid confirmation.", show_alert=True)
            except Exception:
                pass  # Expired callbacks must not prevent durable completion.
            return
        action, kind, token = matched.groups()
        try:
            if action == "confirm" and kind == "trade":
                reply = await self._operations.confirm_trade(
                    owner_id=owner_id, confirmation_token=token
                )
            elif action == "reject" and kind == "trade":
                reply = await self._operations.reject_trade(
                    owner_id=owner_id, confirmation_token=token
                )
            elif action == "confirm":
                reply = await self._operations.confirm_protection_modification(
                    owner_id=owner_id, confirmation_token=token
                )
            else:
                reply = await self._operations.reject_protection_modification(
                    owner_id=owner_id, confirmation_token=token
                )
            await self._complete(update)
            await self._reply(update, reply)
            try:
                await query.answer()
            except Exception:
                pass  # A replayed callback may have expired; the durable reply still goes out.
        except Exception:  # Boundary must not leak failures or execution details to Telegram.
            LOGGER.exception("Telegram confirmation handling failed")
            await self._reply(
                update,
                TelegramReply(
                    "Confirmation outcome needs checking. Use /status; do not submit a replacement."
                ),
            )

    async def _dispatch(
        self,
        update: Update,
        operation: Callable[[str], Awaitable[TelegramReply]],
    ) -> None:
        owner_id = self._owner_for(update)
        if owner_id is None:
            await self._reply(update, TelegramReply("This bot is private."))
            return
        if not await self._accept_once(update, owner_id):
            return
        try:
            reply = await operation(owner_id)
            await self._complete(update)
            await self._reply(update, reply)
        except Exception:
            LOGGER.exception("Telegram command handling failed")
            await self._reply(
                update,
                TelegramReply(
                    "Could not finish processing. Check /status before retrying any trade; an uncertain broker outcome must be reconciled."
                ),
            )

    def _owner_for(self, update: Update) -> str | None:
        if not self._owner_id:
            return None
        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None or str(chat.type) != "private":
            return None
        if str(user.id) != str(self._owner_id):
            return None
        return str(user.id)

    async def _accept_once(self, update: Update, owner_id: str) -> bool:
        if update.update_id is None:
            return False
        return await self._operations.record_telegram_update(
            update_id=str(update.update_id), owner_id=owner_id
        )

    async def _complete(self, update: Update) -> None:
        if update.update_id is None:
            raise RuntimeError("Telegram update is missing its update id")
        await self._operations.complete_telegram_update(update_id=str(update.update_id))

    async def _reply(self, update: Update, reply: TelegramReply) -> None:
        if self._owner_for(update) is not None and hasattr(self._operations, "queue_reply"):
            await self._operations.queue_reply(str(update.update_id), reply)
            return
        message = update.effective_message
        if message is None:
            return
        markup = _confirmation_markup(reply.confirmation_token, reply.confirmation_kind)
        await message.reply_text(reply.text, reply_markup=markup)


def _confirmation_markup(
    token: str | None, kind: str | None = "trade"
) -> InlineKeyboardMarkup | None:
    if token is None:
        return None
    if kind not in {"trade", "protection"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,56}", token):
        # It is safer to withhold a broken confirmation control than to emit
        # invalid callback data or expose a token through an alternate channel.
        LOGGER.error("application supplied an unsafe Telegram confirmation token")
        return None
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Confirm trade" if kind == "trade" else "Confirm action",
                    callback_data=f"confirm:{kind}:{token}",
                ),
                InlineKeyboardButton("Cancel", callback_data=f"reject:{kind}:{token}"),
            ]
        ]
    )


def build_telegram_application(settings: Settings, operations: TelegramOperations) -> Application:
    """Construct an application without starting polling.

    Starting and supervision remain the responsibility of ``main`` so Windows
    and systemd can share the same lifecycle code.
    """

    if not settings.telegram_bot_token:
        raise ValueError("TRADE_AGENT_TELEGRAM_BOT_TOKEN is required to enable Telegram")
    application = Application.builder().token(settings.telegram_bot_token).build()
    TelegramGateway(settings=settings, operations=operations).install(application)
    return application
