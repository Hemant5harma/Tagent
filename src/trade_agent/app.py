"""Interface-neutral façade used by Telegram, HTTP, and future channels."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from trade_agent.ai import ParsedEntry, TradeProposalParse, build_ai_backend
from trade_agent.config import Settings
from trade_agent.domain import ProposalView, Side, TradeInstruction
from trade_agent.service import ProposalError, TradeService
from trade_agent.workflow import ConversationRunner

if TYPE_CHECKING:
    from trade_agent.telegram import TelegramReply


class InputIncomplete(ValueError):
    pass


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
        f"{trade.side.value.upper()} {trade.symbol} · {trade.volume} lots\n"
        f"Order: {proposal.order_type.value} · entry {entry}\n"
        f"SL {trade.stop_loss} · TP {trade.take_profit}{deadline}\n"
        f"Current bid/ask: {proposal.validation['bid']} / {proposal.validation['ask']}\n"
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
        return self.application.service.accept_telegram_update(update_id, owner_id)

    async def complete_telegram_update(self, *, update_id: str) -> None:
        self.application.service.complete_telegram_update(update_id)

    async def create_trade_from_text(self, *, owner_id: str, text: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        try:
            outcome = await self._conversation.run(owner_id, text)
            if outcome["stage"] != "ready_for_confirmation" or outcome["parsed"] is None:
                return TelegramReply(outcome["response"])
            instruction = self._instruction_from_parse(owner_id, outcome["parsed"])
            proposal = self.application.service.create_proposal(instruction)
            return TelegramReply(
                format_proposal(proposal),
                confirmation_token=proposal.confirmation_token,
                confirmation_kind="trade",
            )
        except (InputIncomplete, ValueError) as exc:
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
        return TelegramReply(self.application.account_text())

    async def orders(self, *, owner_id: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        return TelegramReply(self.application.orders_text())

    async def positions(self, *, owner_id: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        return TelegramReply(self.application.positions_text())

    async def status(self, *, owner_id: str) -> TelegramReply:
        from trade_agent.telegram import TelegramReply

        self._authorize(owner_id)
        return TelegramReply(self.application.status_text())

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
            result = self.application.service.confirm(
                proposal.proposal_id, owner_id, proposal.version, confirmation_token
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
