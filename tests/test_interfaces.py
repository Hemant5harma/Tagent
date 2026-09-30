from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from trade_agent.ai import (
    AIBackendResponseError,
    OpenAICompatibleBackend,
    OpenCodeBackend,
    TradeProposalParse,
    _codex_environment,
)
from trade_agent.telegram import _confirmation_markup
from trade_agent.workflow import ConversationRunner


def _trade_parse() -> dict[str, object]:
    return {
        "intent": "trade",
        "symbol": "eurusd",
        "side": "buy",
        "volume": "0.10",
        "entry": {"kind": "market"},
        "stop_loss": "1.0750",
        "take_profit": "1.0900",
        "missing_fields": [],
    }


def test_openai_chat_adapter_requests_json_schema_and_returns_parse() -> None:
    async def run() -> None:
        seen: dict[str, object] = {}

        def json_handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(_trade_parse())}}]},
            )

        async with httpx.AsyncClient(
            base_url="https://provider.example/v1", transport=httpx.MockTransport(json_handler)
        ) as client:
            backend = OpenAICompatibleBackend(
                base_url="https://provider.example/v1",
                api_key="test-key",
                model="test-model",
                protocol="chat_completions",
                client=client,
            )
            result = await backend.parse_trade("Buy EURUSD 0.10 lots entry market SL 1.075 TP 1.09")

        assert result.symbol == "EURUSD"
        assert seen["path"] == "/v1/chat/completions"
        body = seen["body"]
        assert isinstance(body, dict)
        assert body["response_format"]["type"] == "json_schema"

    asyncio.run(run())


def test_openai_chat_json_object_mode_validates_result_locally() -> None:
    async def run() -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(_trade_parse())}}]},
            )

        async with httpx.AsyncClient(
            base_url="https://provider.example/v1", transport=httpx.MockTransport(handler)
        ) as client:
            backend = OpenAICompatibleBackend(
                base_url="https://provider.example/v1",
                api_key="test-key",
                model="test-model",
                protocol="chat_completions",
                response_format="json_object",
                reasoning_effort="low",
                max_completion_tokens=3000,
                client=client,
            )
            result = await backend.parse_trade("Buy EURUSD 0.10 at market SL 1.075 TP 1.09")

        assert result.symbol == "EURUSD"
        body = seen["body"]
        assert isinstance(body, dict)
        assert body["response_format"] == {"type": "json_object"}
        assert body["reasoning_effort"] == "low"
        assert body["max_completion_tokens"] == 3000
        assert "Copy all explicitly stated values" in body["messages"][0]["content"]

    asyncio.run(run())


def test_opencode_adapter_uses_validated_structured_output() -> None:
    async def run() -> None:
        requests: list[tuple[str, dict[str, object]]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.url.path, json.loads(request.content or b"{}")))
            if request.url.path == "/session":
                return httpx.Response(200, json={"id": "session-1"})
            return httpx.Response(200, json={"info": {"structured_output": _trade_parse()}})

        async with httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)
        ) as client:
            backend = OpenCodeBackend(
                base_url="http://127.0.0.1",
                model_provider="openai",
                model="gpt-test",
                client=client,
            )
            result = await backend.parse_trade("Buy EURUSD 0.10 lots entry market SL 1.075 TP 1.09")

        assert result.volume is not None
        assert [path for path, _ in requests] == ["/session", "/session/session-1/message"]
        assert requests[1][1]["format"]["type"] == "json_schema"
        assert requests[1][1]["model"] == {"providerID": "openai", "modelID": "gpt-test"}

    asyncio.run(run())


def test_provider_parse_rejects_extra_fields() -> None:
    invalid = _trade_parse() | {"execute": True}
    with pytest.raises(AIBackendResponseError):
        from trade_agent.ai import _parse_json

        _parse_json(invalid)


def test_confirmation_callback_is_compact_and_token_only() -> None:
    markup = _confirmation_markup("short-token_123", "trade")
    assert markup is not None
    assert markup.inline_keyboard[0][0].callback_data == "confirm:trade:short-token_123"
    assert _confirmation_markup("x" * 57, "trade") is None


def test_parse_normalizes_symbols_without_creating_an_instruction() -> None:
    parsed = TradeProposalParse.model_validate(_trade_parse())
    assert parsed.symbol == "EURUSD"
    assert not hasattr(parsed, "owner_id")


def test_conversation_graph_persists_parse_state_without_executing() -> None:
    async def parser(_: str) -> TradeProposalParse:
        return TradeProposalParse.model_validate(_trade_parse())

    async def run() -> None:
        runner = ConversationRunner(parser, "sqlite:///unused-for-memory.db")
        outcome = await runner.run("100", "Buy EURUSD 0.10 lots entry market SL 1.075 TP 1.09")
        assert outcome["stage"] == "ready_for_confirmation"
        assert outcome["parsed"] is not None
        assert outcome["parsed"]["symbol"] == "EURUSD"
        await runner.aclose()

    asyncio.run(run())


def test_codex_subprocess_environment_excludes_application_secrets(monkeypatch) -> None:
    monkeypatch.setenv("TRADE_AGENT_DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("MT5_CONNECTOR_PASSWORD", "broker-secret")
    monkeypatch.setenv("PATH", "runtime-path")

    environment = _codex_environment()

    assert environment["PATH"] == "runtime-path"
    assert "TRADE_AGENT_DATABASE_URL" not in environment
    assert "MT5_CONNECTOR_PASSWORD" not in environment
