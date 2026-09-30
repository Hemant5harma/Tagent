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
from importlib import import_module
from typing import Any, Literal, NotRequired, TypedDict, cast

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from trade_agent.ai import AIBackendError, ParsedEntry, TradeProposalParse

TradeParser = Callable[[str], Awaitable[TradeProposalParse]]


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
            try:
                previous = state.get("parsed") if state.get("stage") == "clarifying" else None
                message = state["message"]
                # A side-only answer can complete a draft. A fresh instruction
                # (including conversational "can you buy ...") must not inherit
                # the previous trade's size or protection values.
                if re.match(r"\s*new\b", message, re.I) or re.match(
                    r"\s*(?:(?:please|can you|could you|i want to|i would like to)\s+)*(?:buy|sell)\s+\S",
                    message,
                    re.I,
                ):
                    previous = None
                if previous:
                    message = (
                        "Continue this existing explicit trade draft. Preserve earlier values unless "
                        "the user explicitly replaces them.\n"
                        f"Existing draft: {previous}\nNew user message: {message}"
                    )
                parsed = await self._parser(message)
            except AIBackendError as exc:
                return {
                    "stage": "failed",
                    "response": f"AI parser unavailable: {exc}",
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
                        "take_profit": parsed.take_profit or old.take_profit,
                        "deadline_at": parsed.deadline_at or old.deadline_at,
                        "note": parsed.note or old.note,
                        "missing_fields": [],
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
    if parsed.take_profit is None:
        missing.add("take_profit")
    return sorted(missing)


def parsed_entry(payload: dict[str, Any]) -> ParsedEntry:
    """Restore an entry value after a JSON-only graph checkpoint round trip."""

    return ParsedEntry.model_validate(payload["entry"])
