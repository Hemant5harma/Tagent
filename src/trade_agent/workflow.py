"""Durable LangGraph conversation workflow for untrusted trade-message parsing.

The graph only creates a conversation outcome. It has no broker or service
reference, so a provider result cannot submit or modify a trade. The caller
must still turn a ready outcome into a ``TradeInstruction`` and use the normal
proposal and confirmation boundary.
"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from copy import deepcopy
from importlib import import_module
from typing import Any, Literal, NotRequired, TypedDict, cast

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from trade_agent.ai import AIBackendError, AIBackendResponseError, ParsedEntry, TradeProposalParse
from trade_agent.draft_extract import explicit_draft, has_profit_amount

TradeParser = Callable[[str], Awaitable[TradeProposalParse]]


def requests_no_stop_loss(message: str) -> bool:
    """An explicit removal is not a missing value that can inherit an old SL."""
    text = message.lower().replace("’", "'").replace("_", " ")
    return bool(
        re.search(
            r"\b(?:no|without|remove|omit|skip)\s+(?:the\s+)?(?:sl|stop[ -]?loss)\b"
            r"|\b(?:don't|do not|dont)\s+(?:need\s+to\s+)?(?:add|set|use|need|want)\s+(?:a\s+|the\s+)?(?:sl|stop[ -]?loss)\b",
            text,
        )
    )


def is_draft_followup(message: str) -> bool:
    """Conservative routing hint; use only while a draft is actively clarifying."""
    text = message.strip().replace("_", " ")
    if re.match(r"(?:what|why|how|explain|tell me about|do we have|show|list)\b", text, re.I):
        return False
    return bool(
        requests_no_stop_loss(text)
        or re.search(
            r"\b(?:symbol|lots?|volume|size|entry|sl|tp|expire|expires|expiry|stop[ -]?loss|take[ -]?profit)\b",
            text,
            re.I,
        )
        or re.search(r"\b(?:eur|usd|gbp|jpy|chf|cad|aud|nzd|xau|xag){2}\b", text, re.I)
        or re.fullmatch(r"(?:buy|sell|market|at market|\d+(?:\.\d+)?)", text, re.I)
        or re.search(r"\b(?:change|make|set)\s+(?:that|it)\b", text, re.I)
    )


class ConversationState(TypedDict):
    """JSON-serializable state persisted by the graph checkpointer."""

    owner_id: str
    message: str
    parsed: NotRequired[dict[str, Any]]
    stage: NotRequired[Literal["clarifying", "ready_for_confirmation", "not_trade", "failed"]]
    response: NotRequired[str]
    requires_trade_fields: NotRequired[list[str]]


class ConversationOutcome(TypedDict):
    stage: Literal["clarifying", "ready_for_confirmation", "not_trade", "failed"]
    response: str
    requires_trade_fields: list[str]
    parsed: dict[str, Any] | None


class ConversationRunner:
    """Runs the parsing/clarification graph with a stable per-owner thread id."""

    def __init__(self, parser: TradeParser, database_url: str) -> None:
        self._parser = parser
        self._database_url = database_url
        self._graph: CompiledStateGraph[Any, Any, Any, Any] | None = None
        self._checkpointer_context: AbstractAsyncContextManager[Any] | None = None

    async def run(self, owner_id: str, message: str) -> ConversationOutcome:
        graph = await self._ensure_graph()
        state = await graph.ainvoke(
            {"owner_id": owner_id, "message": message},
            {"configurable": {"thread_id": f"telegram:{owner_id}"}},
        )
        return {
            "stage": cast(
                Literal["clarifying", "ready_for_confirmation", "not_trade", "failed"],
                state["stage"],
            ),
            "response": str(state["response"]),
            "requires_trade_fields": list(state.get("requires_trade_fields", [])),
            "parsed": state.get("parsed"),
        }

    async def aclose(self) -> None:
        if self._checkpointer_context is not None:
            await self._checkpointer_context.__aexit__(None, None, None)
            self._checkpointer_context = None
        self._graph = None

    async def current(self, owner_id: str) -> dict[str, Any]:
        """Read a detached draft snapshot without changing its checkpoint."""
        graph = await self._ensure_graph()
        snapshot = await graph.aget_state({"configurable": {"thread_id": f"telegram:{owner_id}"}})
        return deepcopy(dict(snapshot.values))

    async def reset(self, owner_id: str) -> None:
        graph = await self._ensure_graph()
        await graph.aupdate_state(
            {"configurable": {"thread_id": f"telegram:{owner_id}"}},
            {"parsed": {}, "stage": "not_trade", "response": "Draft cleared."},
        )

    async def _ensure_graph(self) -> CompiledStateGraph[Any, Any, Any, Any]:
        if self._graph is not None:
            return self._graph

        checkpointer: Any = InMemorySaver()
        if self._database_url.startswith("postgresql"):
            checkpointer = await self._open_postgres_checkpointer()

        async def parse_message(state: ConversationState) -> dict[str, Any]:
            recovered = False
            try:
                previous = state.get("parsed") if state.get("stage") == "clarifying" else None
                message = state["message"]
                # A side-only answer can complete a draft. A fresh instruction
                # (including conversational "can you buy ...") must not inherit
                # the previous trade's size or protection values.
                if (
                    re.search(
                        r"\b(?:open|place|create|prepare)\s+(?:(?:a|an|new|another|one)\s+)*(?:trade|order|position)\b",
                        message,
                        re.I,
                    )
                    or re.match(r"\s*new\b", message, re.I)
                    or re.match(
                        r"\s*(?:(?:please|can you|could you|i want to|i would like to)\s+)*(?:buy|sell)\s+\S",
                        message,
                        re.I,
                    )
                ):
                    previous = None
                if previous:
                    message = (
                        "Continue this existing explicit trade draft. Preserve earlier values unless "
                        "the user explicitly replaces them.\n"
                        f"Existing draft: {previous}\nNew user message: {message}"
                    )
                parsed = (
                    explicit_draft(state["message"].replace("_", " "))
                    if has_profit_amount(state["message"])
                    or re.search(
                        r"\b(?:expire|expires|expiry|buy limit|sell limit|buy stop|sell stop|price rises|price falls|price reaches)\b",
                        state["message"],
                        re.I,
                    )
                    or (
                        previous
                        and is_draft_followup(state["message"])
                        and (
                            requests_no_stop_loss(state["message"])
                            or re.search(
                                r"\b(?:eur|usd|gbp|jpy|chf|cad|aud|nzd|xau|xag){2}\b",
                                state["message"],
                                re.I,
                            )
                        )
                    )
                    else await self._parser(message)
                )
            except AIBackendResponseError:
                recovered = True
                parsed = explicit_draft(state["message"])
                if not _missing_trade_fields(parsed):
                    parsed = parsed.model_copy(
                        update={
                            "intent": "clarification",
                            "clarification": "I couldn't reliably validate the assistant's response. Please restate the trade with symbol, buy/sell, lots, entry, SL and TP. No trade was submitted.",
                        }
                    )
            except AIBackendError:
                return {
                    "stage": "failed",
                    "response": "The trade assistant is temporarily unavailable. Please try again; no trade was submitted. /account and /status still work.",
                    "requires_trade_fields": [],
                    "parsed": {},
                }
            except ValueError as exc:
                parsed = TradeProposalParse(
                    intent="clarification",
                    entry=ParsedEntry(kind="missing"),
                    clarification=str(exc),
                )
            if previous and parsed.intent != "not_trade":
                old = TradeProposalParse.model_validate(previous)
                parsed = parsed.model_copy(
                    update={
                        "intent": "trade",
                        "symbol": parsed.symbol or old.symbol,
                        "side": parsed.side or old.side,
                        "volume": parsed.volume or old.volume,
                        "entry": parsed.entry if parsed.entry.kind != "missing" else old.entry,
                        "stop_loss": parsed.stop_loss or old.stop_loss,
                        "take_profit": parsed.take_profit
                        or (
                            None
                            if parsed.profit_target_usd is not None
                            or has_profit_amount(state["message"])
                            else old.take_profit
                        ),
                        "profit_target_usd": parsed.profit_target_usd
                        or (
                            None
                            if parsed.take_profit is not None or has_profit_amount(state["message"])
                            else old.profit_target_usd
                        ),
                        "deadline_at": parsed.deadline_at or old.deadline_at,
                        "pending_expires_at": parsed.pending_expires_at or old.pending_expires_at,
                        "requested_order_type": parsed.requested_order_type
                        or old.requested_order_type,
                        "note": parsed.note or old.note,
                        "missing_fields": [],
                    }
                )
            if requests_no_stop_loss(state["message"]):
                parsed = parsed.model_copy(update={"stop_loss": None})
            if recovered and not _missing_trade_fields(parsed):
                parsed = parsed.model_copy(
                    update={
                        "intent": "clarification",
                        "clarification": "I couldn't reliably validate the assistant's response. Please restate the trade with symbol, buy/sell, lots, entry, SL and TP. No trade was submitted.",
                    }
                )
            return {"parsed": parsed.model_dump(mode="json"), "stage": "clarifying"}

        def decide(state: ConversationState) -> dict[str, Any]:
            if state.get("stage") == "failed":
                return {"stage": "failed", "response": state["response"]}
            payload = state.get("parsed")
            if not isinstance(payload, dict):
                return {
                    "stage": "failed",
                    "response": "Could not interpret that trade request.",
                    "requires_trade_fields": [],
                }
            try:
                parsed = TradeProposalParse.model_validate(payload)
            except (TypeError, ValueError):
                return {
                    "stage": "failed",
                    "response": "Could not validate the trade details returned by the parser.",
                    "requires_trade_fields": [],
                }
            if parsed.intent == "not_trade":
                return {
                    "stage": "not_trade",
                    "response": parsed.clarification
                    or "Use /account, /orders, /positions, /status, or send a trade instruction.",
                    "requires_trade_fields": [],
                }
            missing = _missing_trade_fields(parsed)
            if parsed.intent != "trade" or missing:
                text = (
                    f"Please provide: {', '.join(missing)}."
                    if missing
                    else (parsed.clarification or "Please clarify your trade details.")
                )
                if parsed.profit_target_usd is not None:
                    text = (
                        f"Understood: {parsed.side or 'direction missing'}, {parsed.volume or 'size missing'} lots; "
                        f"target ${parsed.profit_target_usd} estimated gross profit. "
                        + text
                        + " A dollar target is not a TP price; fees and slippage can change realized profit."
                    )
                elif parsed.clarification and parsed.clarification not in text:
                    text += " " + parsed.clarification
                return {"stage": "clarifying", "response": text, "requires_trade_fields": missing}
            return {
                "stage": "ready_for_confirmation",
                "response": "Trade details are complete. Review the proposal before confirming.",
                "requires_trade_fields": [],
            }

        builder = StateGraph(ConversationState)
        builder.add_node("parse_message", parse_message)
        builder.add_node("decide", decide)
        builder.add_edge(START, "parse_message")
        builder.add_edge("parse_message", "decide")
        builder.add_edge("decide", END)
        self._graph = builder.compile(checkpointer=checkpointer)
        return self._graph

    async def _open_postgres_checkpointer(self) -> Any:
        """Create one checkpointer connection for the Telegram process lifetime."""

        try:
            AsyncPostgresSaver = import_module(
                "langgraph.checkpoint.postgres.aio"
            ).AsyncPostgresSaver
        except ImportError as exc:  # pragma: no cover - installation configuration
            raise RuntimeError(
                "PostgreSQL conversation persistence requires langgraph-checkpoint-postgres"
            ) from exc

        # Prevent a compromised checkpoint row from deserializing arbitrary
        # MessagePack extension values. This preserves an explicitly stricter
        # host setting if one already exists.
        os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
        connection_url = self._database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        context = AsyncPostgresSaver.from_conn_string(connection_url)
        checkpointer = await context.__aenter__()
        await checkpointer.setup()
        self._checkpointer_context = context
        return checkpointer


def _missing_trade_fields(parsed: TradeProposalParse) -> list[str]:
    missing = set(parsed.missing_fields)
    if parsed.symbol is None:
        missing.add("symbol")
    if parsed.side is None:
        missing.add("side")
    if parsed.volume is None:
        missing.add("volume")
    if parsed.entry.kind == "missing":
        missing.add("entry")
    if parsed.stop_loss is None:
        missing.add("stop_loss")
    if parsed.take_profit is None and parsed.profit_target_usd is None:
        missing.add("take_profit")
    else:
        missing.discard("take_profit")
    return sorted(missing)


def parsed_entry(payload: dict[str, Any]) -> ParsedEntry:
    """Restore an entry value after a JSON-only graph checkpoint round trip."""

    return ParsedEntry.model_validate(payload["entry"])
