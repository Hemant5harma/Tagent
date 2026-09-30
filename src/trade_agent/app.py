"""Interface-neutral façade used by Telegram, HTTP, and future channels."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import select

from trade_agent.ai import ParsedEntry, TradeProposalParse, build_ai_backend
from trade_agent.config import Settings
from trade_agent.db import DashboardCacheRecord, IncomingUpdateRecord, ProposalRecord
from trade_agent.delivery import current_update_id
from trade_agent.domain import ProposalView, Side, TradeInstruction
from trade_agent.reporting import activity, now, put_cache
from trade_agent.service import ProposalError, TradeService
from trade_agent.workflow import ConversationRunner

if TYPE_CHECKING:
    from trade_agent.telegram import TelegramReply


class InputIncomplete(ValueError):
    pass


def _read_only_route(text: str) -> str | None:
    """Recognize account questions without asking an LLM to parse a trade.

    Action words take precedence: a question phrased as "can you buy" is still
    an instruction candidate, never a read-only account query.
    """
    if re.match(
        r"(?:explain|define)\b|what (?:is|does) (?:a |an |the )?(?:balance|equity|free margin)\b",
        text,
    ):
        return None
    if re.search(
        r"\b(buy|sell|close|cancel|modify|change|replace|remove|set|execute|submit)\b", text
    ):
        return None
    if (
        re.search(r"\b(?:place|create|prepare)\b|\bopen\s+(?:a|an|one|new|another)\b", text)
        or re.match(r"(?:(?:please|can you|could you|will you)\s+)?open\b", text)
        and not re.fullmatch(r"open (?:trades?|positions?|orders?)", text)
    ):
        return None
    if re.search(r"\b(balance|equity|free margin|account details|account information)\b", text):
        return "account"
    if re.search(
        r"\b(positions?|open trades?|active trades?|running trades?)\b", text
    ) or re.search(r"\btrades?\b.*\b(open|active|running)\b", text):
        return "positions"
    if re.search(r"\b(pending orders?|open orders?|orders? pending)\b", text):
        return "orders"
    if re.fullmatch(
        r"(?:(?:please |can you |could you )?(?:show|check|tell)(?: me)?(?: my| our| the)? |(?:what is|what's) (?:my|our|the) )?(account|orders|status)",
        text,
    ):
        return text.split()[-1]
    if re.search(
        r"\b(system|bot|agent) status\b|\b(?:are|is) (?:new )?entries (?:paused|enabled)\b", text
    ):
        return "status"
    return None


def _looks_like_trade_request(text: str) -> bool:
    """Conservative routing only; extraction and authorization stay downstream."""
    if re.match(r"(?:what|why|how|when|where|should|can i|could i)\b", text):
        return False
    return bool(
        re.search(r"\b(buy|sell|close|cancel|modify|replace|execute|submit)\b", text)
        or re.search(r"\b(?:open|place|create|prepare|new)\b.*\b(?:trade|order|position)\b", text)
        or re.search(
            r"\b(?:sl|tp|stop[ -]?loss|take[ -]?profit|entry|lots?|volume|size|deadline)\b.*\d",
            text,
        )
        or re.search(r"\d+(?:\.\d+)?\s*lots?\b", text)
        or re.fullmatch(
            r"(?:entry\s+)?market|(?:eur|usd|gbp|jpy|chf|cad|aud|nzd|xau|xag){2}(?:[._][a-z]+)?",
            text,
        )
        or re.match(r"\d+(?:\.\d+)?\b", text)
        or re.match(r"(?:change|make|use|set|instead|actually)\b", text)
    )


def _decimal_match(pattern: str, text: str, label: str) -> Decimal:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match is None:
        raise InputIncomplete(f"missing {label}")
    try:
        return Decimal(match.group(1))
    except InvalidOperation as exc:
        raise InputIncomplete(f"invalid {label}") from exc


def parse_trade_text(owner_id: str, account_id: str, text: str, timezone: str) -> TradeInstruction:
    """Strict parser for the deterministic Phase 1 command format.

    Conversational providers may produce this same payload, but their output is never
    used until it passes this parser/Pydantic/risk boundary.
    """
    side_match = re.search(r"\b(buy|sell)\b", text, flags=re.IGNORECASE)
    if side_match is None:
        raise InputIncomplete("missing buy or sell direction")
    symbol_match = re.search(r"\b([A-Za-z]{6,12})\b", text)
    if symbol_match is None:
        raise InputIncomplete("missing symbol")
    volume = _decimal_match(r"\b(\d+(?:\.\d+)?)\s*(?:lots?|lot)\b", text, "lot size")
    entry_match = re.search(r"\bentry\s*(?:at|=|:)?\s*(market|\d+(?:\.\d+)?)\b", text, re.I)
    if entry_match is None:
        raise InputIncomplete("missing entry price or 'entry market'")
    entry_price = (
        None if entry_match.group(1).lower() == "market" else Decimal(entry_match.group(1))
    )
    stop_loss = _decimal_match(
        r"\b(?:sl|stop[ -]?loss)\s*(?:at|=|:)?\s*(\d+(?:\.\d+)?)\b", text, "SL"
    )
    take_profit = _decimal_match(
        r"\b(?:tp|take[ -]?profit)\s*(?:at|=|:)?\s*(\d+(?:\.\d+)?)\b", text, "TP"
    )
    deadline_at = None
    deadline_match = re.search(r"\bdeadline\s*(?:at|=|:)?\s*([^,;]+)", text, re.I)
    if deadline_match:
        raw_deadline = deadline_match.group(1).strip()
        try:
            parsed = datetime.fromisoformat(raw_deadline.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InputIncomplete(
                "deadline must be an ISO timestamp with timezone, e.g. 2026-09-22T18:00:00+05:30"
            ) from exc
        if parsed.tzinfo is None:
            raise InputIncomplete("deadline must include a timezone offset")
        deadline_at = parsed.astimezone(ZoneInfo(timezone))
    return TradeInstruction(
        owner_id=owner_id,
        account_id=account_id,
        symbol=symbol_match.group(1),
        side=Side(side_match.group(1).lower()),
        volume=volume,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        deadline_at=deadline_at,
    )


def format_proposal(proposal: ProposalView) -> str:
    trade = proposal.instruction
    entry = "market" if trade.entry_price is None else str(trade.entry_price)
    deadline = f"\nDeadline: {trade.deadline_at.isoformat()}" if trade.deadline_at else ""
    return (
        f"Confirm trade\n"
        f"Account {trade.account_id} · {'DEMO' if proposal.validation.get('account_is_demo') else 'LIVE'}\n"
        f"{trade.side.value.upper()} {trade.symbol} · {trade.volume} lots\n"
        f"Order: {proposal.order_type.value} · entry {entry}\n"
        f"SL {trade.stop_loss} · TP {trade.take_profit}{deadline}\n"
        f"Current bid/ask: {proposal.validation['bid']} / {proposal.validation['ask']}\n"
        f"Estimated loss at SL: {proposal.validation.get('estimated_loss', 'unavailable')} {proposal.validation.get('currency', '')}\n"
        f"Estimated margin: {proposal.validation.get('estimated_margin', 'unavailable')}\n"
        "Stops do not guarantee a maximum loss.\n"
        f"Proposal: {proposal.proposal_id} v{proposal.version}\n"
        f"This confirmation expires at {proposal.expires_at.isoformat()}"
    )


def format_protection_proposal(proposal: object) -> str:
    """Render an action proposal without depending on its storage model here."""

    if hasattr(proposal, "model_dump"):
        payload = proposal.model_dump(mode="json")
    else:
        payload = vars(proposal)
    details = payload.get("payload") or payload.get("action_payload") or {}
    target = payload.get("target_id") or "unknown target"
    target_kind = payload.get("target_kind") or "unknown"
    return (
        "Confirm protection change\n"
        f"Target: {target_kind} {target}\n"
        f"SL {details.get('stop_loss')} · TP {details.get('take_profit')}\n"
        f"Proposal: {payload['proposal_id']} v{payload['version']}\n"
        f"This confirmation expires at {payload['expires_at']}"
    )


class ApplicationFacade:
    def __init__(self, settings: Settings, service: TradeService) -> None:
        self.settings = settings
        self.service = service

    def create_trade_from_text(self, owner_id: str, text: str) -> str:
        instruction = parse_trade_text(
            owner_id, self.settings.account_id, text, self.settings.timezone
        )
        return format_proposal(self.service.create_proposal(instruction))

    def create_trade(self, instruction: TradeInstruction) -> ProposalView:
        return self.service.create_proposal(instruction)

    def confirm(self, proposal_id: str, token: str, owner_id: str | None = None) -> str:
        proposal = self.service.proposal(proposal_id)
        result = self.service.confirm(
            proposal_id, owner_id or proposal.instruction.owner_id, proposal.version, token
        )
        return result.get("message") or f"Trade {result['status']}."

    def reject(self, proposal_id: str, token: str, owner_id: str | None = None) -> str:
        proposal = self.service.proposal(proposal_id)
        self.service.reject(
            proposal_id, owner_id or proposal.instruction.owner_id, proposal.version, token
        )
        return "Trade proposal rejected."

    def account_text(self) -> str:
        account = self.service.broker_state()["account"]
        return (
            f"Account {account['account_id']} ({'demo' if account['is_demo'] else 'live'})\n"
            f"Balance: {account['balance']} {account['currency']}\n"
            f"Equity: {account['equity']} · Free margin: {account['margin_free']}"
        )

    def orders_text(self) -> str:
        orders = self.service.broker_state()["state"]["orders"]
        if not orders:
            return "No pending orders."
        return "\n".join(
            f"{item['order_id']}: {item['side']} {item['volume']} {item['symbol']} @ {item['entry_price']} ({item['status']})"
            for item in orders
        )

    def positions_text(self) -> str:
        positions = self.service.broker_state()["state"]["positions"]
        if not positions:
            return "No open positions."
        return "\n".join(
            f"{item['position_id']}: {item['side']} {item['volume']} {item['symbol']} · P/L {item['profit']}"
            for item in positions
        )

    def status_text(self) -> str:
        state = self.service.broker_state()
        return (
            f"Entries: {'paused' if state['paused'] else 'enabled'}\n"
            f"Pending orders: {len(state['state']['orders'])}\n"
            f"Open positions: {len(state['state']['positions'])}\n"
            f"Broker mode: {self.settings.broker_mode} · Account mode: {self.settings.trading_mode}"
        )

    def pause_entries(self) -> str:
        self.service.set_paused(True)
        return "New entries paused. Existing orders, monitoring, and exits remain active."

    def resume_entries(self) -> str:
        self.service.set_paused(False)
        return "New entries resumed."

    def set_ai_provider(self, provider: str) -> str:
        if provider not in {"none", "openai_compatible", "opencode", "codex"}:
            raise ValueError("provider must be none, openai_compatible, opencode, or codex")
        self.settings.ai_provider = provider  # type: ignore[assignment]
        return f"AI provider set to {provider}. It cannot execute or confirm trades."

    def cancel_order(self, order_id: str) -> str:
        self.service.cancel_order(order_id)
        return f"Pending order {order_id} cancelled."

    def close_position(self, position_id: str) -> str:
        self.service.close_position(position_id)
        return f"Position {position_id} closed."


class TelegramApplicationFacade:
    """Async private-channel adapter around the synchronous application façade.

    Importing Telegram reply types here keeps all Telegram-specific details out
    of the execution service. This is also a second owner check behind the
    gateway's private-chat filtering.
    """

    def __init__(self, application: ApplicationFacade) -> None:
        self.application = application
        self._conversation = ConversationRunner(
            self._parse_conversation_message, application.settings.database_url
        )

    def _authorize(self, owner_id: str) -> None:
        expected = self.application.settings.telegram_owner_id
        if not expected or str(expected) != str(owner_id):
            raise PermissionError("Telegram owner is not authorized")

    async def record_telegram_update(self, *, update_id: str, owner_id: str) -> bool:
        self._authorize(owner_id)
        current_update_id.set(update_id)
        return self.application.service.accept_telegram_update(update_id, owner_id)

    async def complete_telegram_update(self, *, update_id: str) -> None:
        # Completion and the reply are committed together by queue_reply.
        return None

    async def queue_reply(self, update_id: str, reply: TelegramReply) -> None:
        with self.application.service.database.session() as session:
            row = session.get(IncomingUpdateRecord, update_id)
            if row:
                row.reply = asdict(reply)
                row.status = "completed"
                row.completed_at = now()

    async def help(self, *, owner_id: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        return TelegramReply(
            "Hi! I can help you prepare a Vantage MT5 demo trade, check your account, and explain the trading workflow. You choose the trade; I ask for missing details, then show one Confirm trade button.\n\nSend: buy/sell, symbol, lot size, entry market or price, SL and TP. I never invent missing values.\n\n/account /positions /orders /status /pause\n/new /cancel_draft /dashboard\n/close <position-id> prepares a confirmed full close. Autonomous trading is off."
        )

    async def reset_draft(self, *, owner_id: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        await self._conversation.reset(owner_id)
        self._cancel_pending(owner_id)
        return TelegramReply(
            "Draft and pending trade confirmations cleared. No broker orders were cancelled. Send a new trade when ready."
        )

    async def exit_proposal(self, *, owner_id: str, target_id: str, kind: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        proposal = await asyncio.to_thread(
            self.application.service.create_exit_proposal, owner_id, target_id, kind
        )
        details = proposal.payload
        return TelegramReply(
            f"Confirm {'full close' if kind == 'position' else 'cancellation'}\nAccount {self.application.settings.account_id} · {self.application.settings.trading_mode.upper()}\n{kind} #{target_id} · {details['symbol']} · {details['side']} · {details['volume']} lots\nExpires {proposal.expires_at.isoformat()}\nThis is not a new entry.",
            confirmation_token=proposal.confirmation_token,
            confirmation_kind="protection",
        )

    def _cancel_pending(self, owner_id: str) -> None:
        with self.application.service.database.session() as session:
            for row in session.scalars(
                select(ProposalRecord).where(
                    ProposalRecord.owner_id == owner_id,
                    ProposalRecord.status == "pending_confirmation",
                )
            ):
                if row.source_update_id != current_update_id.get():
                    row.status = "cancelled"

    async def dashboard(self, *, owner_id: str) -> TelegramReply:
        from trade_agent.dashboard import issue_code
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        with self.application.service.database.session() as session:
            cached = session.get(DashboardCacheRecord, "dashboard_url")
            url = (
                cached.payload.get("url", "") if cached else self.application.settings.dashboard_url
            )
        if not url:
            return TelegramReply(
                "The dashboard link is not available yet. Trading protection is independent of dashboard availability."
            )
        code = issue_code(self.application.service.database)
        return TelegramReply(
            f"Your private dashboard:\n{url}\n\nOne-use login code: {code}\nExpires in 5 minutes. Session lasts 12 hours. Keep the code private."
        )

    async def create_trade_from_text(self, *, owner_id: str, text: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        normalized = re.sub(r"\s+", " ", text.strip().lower()).rstrip("?!. ")
        if re.search(
            r"\b\d{6,12}:[A-Za-z0-9_-]{20,}|(?i:password|api[_ -]?key|token|secret)\s*(?:[:=]|is\b)\s*\S+",
            text,
        ) or any(
            secret and len(secret) > 8 and secret in text
            for secret in (
                self.application.settings.telegram_bot_token,
                self.application.settings.openai_api_key,
                self.application.settings.mt5_connector_token,
            )
        ):
            return TelegramReply(
                "Please don't send passwords or API keys here. Configure secrets through the protected server setup."
            )
        exit_match = re.fullmatch(
            r"(?:please\s+)?(close|cancel)\s+(?:my\s+)?(?:(position|order)\s+)?#?(\d+)\s*",
            normalized,
        )
        if exit_match:
            return await self.exit_proposal(
                owner_id=owner_id,
                target_id=exit_match[3],
                kind=exit_match[2] or ("position" if exit_match[1] == "close" else "order"),
            )
        routes = {
            "account": self.account,
            "show my account": self.account,
            "balance": self.account,
            "what is my balance": self.account,
            "positions": self.positions,
            "show my positions": self.positions,
            "orders": self.orders,
            "status": self.status,
            "stop new trades": self.pause_entries,
            "pause": self.pause_entries,
            "hi": self.help,
            "hello": self.help,
            "hey": self.help,
            "help": self.help,
            "what can you do": self.help,
            "how are you": self.help,
        }
        if normalized in routes:
            return await routes[normalized](owner_id=owner_id)
        read_only_route = _read_only_route(normalized)
        if read_only_route:
            return await getattr(self, read_only_route)(owner_id=owner_id)
        if normalized in {
            "yes",
            "yes please",
            "confirm",
            "confirmed",
            "go ahead",
            "do it",
            "okay",
            "ok",
        }:
            return TelegramReply(
                "Text replies do not authorize an order. Use the Confirm trade button on the exact proposal you reviewed, or send the missing trade details. No trade was submitted."
            )
        source = current_update_id.get()
        if source:
            with self.application.service.database.session() as session:
                existing = session.scalar(
                    select(ProposalRecord).where(ProposalRecord.source_update_id == source)
                )
                if existing:
                    view = self.application.service._view(existing)
                    return TelegramReply(
                        format_proposal(view),
                        confirmation_token=view.confirmation_token
                        if existing.status == "pending_confirmation"
                        else None,
                        confirmation_kind="trade",
                    )
        started = time.monotonic()
        try:
            if _looks_like_trade_request(normalized):
                outcome = await self._conversation.run(owner_id, text)
            else:
                # Ordinary conversation must not depend on structured trade
                # extraction, and must not replace or cancel an active draft.
                outcome = {
                    "stage": "not_trade",
                    "response": (
                        "I can answer account questions and help prepare a trade for your review. "
                        "Use /help for commands, or send buy/sell, symbol, size, entry, SL and TP. "
                        "No trade was submitted."
                    ),
                    "requires_trade_fields": [],
                    "parsed": None,
                }
            if outcome["stage"] in {"clarifying", "ready_for_confirmation", "failed"}:
                self._cancel_pending(owner_id)
            if outcome["stage"] == "not_trade":
                backend = build_ai_backend(self.application.settings)
                if hasattr(backend, "explain"):
                    from trade_agent.ai import AIBackendError

                    with self.application.service.database.session() as session:
                        cached = session.get(DashboardCacheRecord, "broker")
                        snapshot = cached.payload if cached else {}
                        account = snapshot.get("account", {})
                        evidence = {
                            "observed_at": snapshot.get("observed_at"),
                            "current_time": now().isoformat(),
                            "account": {
                                key: account.get(key)
                                for key in (
                                    "balance",
                                    "equity",
                                    "margin_free",
                                    "currency",
                                    "is_demo",
                                )
                            },
                            "masked_account_id": "***" + str(account.get("account_id", ""))[-4:],
                            "state": snapshot.get("state", {}),
                            "entries_paused": snapshot.get("paused", True),
                        }
                    try:
                        outcome["response"] = await backend.explain(text, json.dumps(evidence))
                    except AIBackendError:
                        outcome["stage"] = "failed"
                        outcome["response"] = (
                            "The assistant is temporarily unavailable. /account, /status and /pause still work. No trade was submitted."
                        )
            elapsed = int((time.monotonic() - started) * 1000)
            activity(
                self.application.service.database,
                outcome["stage"],
                f"User-directed: {text}\n{outcome['response']}",
                elapsed,
            )
            put_cache(
                self.application.service.database,
                "model",
                {
                    "status": "error" if outcome["stage"] == "failed" else "ok",
                    "latency_ms": elapsed,
                    "model": self.application.settings.openai_model
                    or self.application.settings.ai_provider,
                },
            )
            if outcome["stage"] != "ready_for_confirmation" or outcome["parsed"] is None:
                return TelegramReply(outcome["response"])
            instruction = self._instruction_from_parse(owner_id, outcome["parsed"])
            proposal = await asyncio.to_thread(
                self.application.service.create_proposal, instruction
            )
            return TelegramReply(
                format_proposal(proposal),
                confirmation_token=proposal.confirmation_token,
                confirmation_kind="trade",
            )
        except (InputIncomplete, ValueError) as exc:
            activity(self.application.service.database, "rejected_proposal", str(exc))
            return TelegramReply(f"Trade was not created: {exc}")

    async def _parse_conversation_message(self, text: str) -> TradeProposalParse:
        if self.application.settings.ai_provider == "none":
            instruction = parse_trade_text(
                "conversation-parser",
                self.application.settings.account_id,
                text,
                self.application.settings.timezone,
            )
            return TradeProposalParse(
                intent="trade",
                symbol=instruction.symbol,
                side=instruction.side.value,
                volume=instruction.volume,
                entry=ParsedEntry(
                    kind="price" if instruction.entry_price is not None else "market",
                    price=instruction.entry_price,
                ),
                stop_loss=instruction.stop_loss,
                take_profit=instruction.take_profit,
                deadline_at=instruction.deadline_at.isoformat()
                if instruction.deadline_at
                else None,
            )
        return await build_ai_backend(self.application.settings).parse_trade(text)

    def _instruction_from_parse(
        self, owner_id: str, payload: dict[str, object]
    ) -> TradeInstruction:
        parsed = TradeProposalParse.model_validate(payload)
        if (
            parsed.symbol is None
            or parsed.side is None
            or parsed.volume is None
            or parsed.stop_loss is None
            or parsed.take_profit is None
            or parsed.entry.kind == "missing"
        ):
            raise InputIncomplete("AI parser did not return all required explicit trade details")
        entry_price = parsed.entry.price if parsed.entry.kind == "price" else None
        deadline_at = (
            datetime.fromisoformat(parsed.deadline_at.replace("Z", "+00:00"))
            if parsed.deadline_at is not None
            else None
        )
        return TradeInstruction(
            owner_id=owner_id,
            account_id=self.application.settings.account_id,
            symbol=parsed.symbol,
            side=Side(parsed.side),
            volume=parsed.volume,
            entry_price=entry_price,
            stop_loss=parsed.stop_loss,
            take_profit=parsed.take_profit,
            deadline_at=deadline_at,
            note=parsed.note,
        )

    async def aclose(self) -> None:
        await self._conversation.aclose()

    async def account(self, *, owner_id: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        return TelegramReply(await asyncio.to_thread(self.application.account_text))

    async def orders(self, *, owner_id: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        return TelegramReply(await asyncio.to_thread(self.application.orders_text))

    async def positions(self, *, owner_id: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        return TelegramReply(await asyncio.to_thread(self.application.positions_text))

    async def status(self, *, owner_id: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        return TelegramReply(await asyncio.to_thread(self.application.status_text))

    async def pause_entries(self, *, owner_id: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        return TelegramReply(self.application.pause_entries())

    async def resume_entries(self, *, owner_id: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        return TelegramReply(self.application.resume_entries())

    async def select_ai_provider(self, *, owner_id: str, provider: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        return TelegramReply(self.application.set_ai_provider(provider))

    async def create_protection_modification(
        self,
        *,
        owner_id: str,
        order_id: str | None,
        position_id: str | None,
        stop_loss: str,
        take_profit: str,
    ) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        try:
            proposal = self.application.service.create_protection_modification_proposal(
                owner_id,
                order_id,
                position_id,
                Decimal(stop_loss),
                Decimal(take_profit),
            )
            return TelegramReply(
                format_protection_proposal(proposal),
                confirmation_token=proposal.confirmation_token,
                confirmation_kind="protection",
            )
        except (ProposalError, ValueError) as exc:
            return TelegramReply(f"Protection change was not created: {exc}")

    async def confirm_trade(self, *, owner_id: str, confirmation_token: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        try:
            proposal = self.application.service.proposal_by_token(confirmation_token)
            if proposal.status.value != "pending_confirmation":
                return TelegramReply(
                    f"This proposal is already {proposal.status.value}. No duplicate order was sent."
                )
            result = await asyncio.to_thread(
                self.application.service.confirm,
                proposal.proposal_id,
                owner_id,
                proposal.version,
                confirmation_token,
            )
            return TelegramReply(result.get("message") or f"Trade {result['status']}.")
        except ProposalError as exc:
            return TelegramReply(f"Confirmation was not accepted: {exc}")

    async def reject_trade(self, *, owner_id: str, confirmation_token: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        try:
            proposal = self.application.service.proposal_by_token(confirmation_token)
            self.application.service.reject(
                proposal.proposal_id, owner_id, proposal.version, confirmation_token
            )
            return TelegramReply("Trade proposal rejected.")
        except ProposalError as exc:
            return TelegramReply(f"Rejection was not accepted: {exc}")

    async def confirm_protection_modification(
        self, *, owner_id: str, confirmation_token: str
    ) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        try:
            proposal = self.application.service.action_proposal_by_token(confirmation_token)
            if proposal.action_type.value in {"close_position", "cancel_order"}:
                result = await asyncio.to_thread(
                    self.application.service.confirm_exit,
                    proposal.proposal_id,
                    owner_id,
                    proposal.version,
                    confirmation_token,
                )
                return TelegramReply(result["message"])
            result = self.application.service.confirm_protection_modification(
                proposal.proposal_id, owner_id, proposal.version, confirmation_token
            )
            return TelegramReply(result.get("message") or "Protection change submitted.")
        except ProposalError as exc:
            return TelegramReply(f"Protection confirmation was not accepted: {exc}")

    async def reject_protection_modification(
        self, *, owner_id: str, confirmation_token: str
    ) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        try:
            proposal = self.application.service.action_proposal_by_token(confirmation_token)
            self.application.service.reject_action_proposal(
                proposal.proposal_id, owner_id, proposal.version, confirmation_token
            )
            return TelegramReply("Protection change rejected.")
        except ProposalError as exc:
            return TelegramReply(f"Protection rejection was not accepted: {exc}")
