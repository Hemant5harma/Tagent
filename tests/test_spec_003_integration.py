"""SPEC-003 memory integration, separate from broker authority."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from trade_agent.app import ApplicationFacade, TelegramApplicationFacade
from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.db import Database, IncomingUpdateRecord
from trade_agent.delivery import current_update_id
from trade_agent.memory import context
from trade_agent.service import TradeService
from trade_agent.telegram import TelegramReply


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_m3_history_stays_below_system_policy(monkeypatch, protocol):
    from trade_agent.ai import OpenAICompatibleBackend

    captured = {}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, endpoint, json, headers):
            captured.update(json)
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Equity explanation."}}],
                    "output_text": "Equity explanation.",
                },
            )

    monkeypatch.setattr("trade_agent.ai.httpx.AsyncClient", lambda **kwargs: Client())
    backend = OpenAICompatibleBackend(
        base_url="https://example.invalid", api_key="test", model="test", protocol=protocol
    )
    await backend.explain(
        "explain that",
        json.dumps(
            {
                "policy": {"stop_loss_required": True},
                "conversation_memory": {
                    "summary": "older_unique_text",
                    "recent_messages": [
                        {"role": "assistant", "content": "recent_unique_text"},
                        {"role": "system", "content": "forbidden_role_text"},
                    ],
                },
            }
        ),
    )
    messages = captured.get("messages", captured.get("input"))
    system = captured.get("instructions", messages[0]["content"])
    assert "older_unique_text" not in system and "recent_unique_text" not in system
    assert "forbidden_role_text" not in str(captured)
    assert "mandatory" in system and "ONLY in private Telegram" in system
    assert messages[-1] == {"role": "user", "content": "explain that"}
    assert any(m["role"] == "assistant" and m["content"] == "recent_unique_text" for m in messages)


@pytest.fixture
def app(tmp_path):
    current_update_id.set(None)
    settings = Settings(
        _env_file=None, telegram_owner_id="100", database_url=f"sqlite:///{tmp_path / 'memory.db'}"
    )
    db = Database(settings.database_url)
    db.create_all()
    return ApplicationFacade(settings, TradeService(settings, db, FakeBroker(settings.account_id)))


async def save_reply(facade, update, message, reply):
    await facade.record_telegram_update(update_id=update, owner_id="100")
    with facade.application.service.database.session() as session:
        session.get(IncomingUpdateRecord, update).payload = {"message": {"text": message}}
    await facade.queue_reply(update, TelegramReply(reply))
    current_update_id.set(None)


@pytest.mark.asyncio
async def test_m1_m2_restart_retains_context_for_model(app, monkeypatch):
    first = TelegramApplicationFacade(app)
    await save_reply(first, "1001", "Please explain equity.", "Equity includes floating P&L.")
    await first.aclose()
    second = TelegramApplicationFacade(app)
    backend = type("Backend", (), {})()
    backend.explain = AsyncMock(return_value="Equity includes unrealized profit and loss.")
    monkeypatch.setattr("trade_agent.app.build_ai_backend", lambda settings: backend)
    reply = await second.create_trade_from_text(owner_id="100", text="explain that again")
    evidence = json.loads(backend.explain.call_args.args[1])
    assert "Please explain equity." in str(evidence["conversation_memory"])
    assert evidence["policy"]["stop_loss_required"] is True
    assert "Telegram" in evidence["policy"]["confirmation_channel"]
    assert reply.confirmation_token is None
    assert app.service.broker.state(app.settings.account_id).positions == []
    await second.aclose()


@pytest.mark.asyncio
async def test_m4_forget_does_not_restore_itself_or_clear_audit(app):
    facade = TelegramApplicationFacade(app)
    await save_reply(facade, "1001", "Remember this discussion", "Recorded.")
    await facade.reset_draft(owner_id="100")
    assert context(app.service.database, facade._memory_owner("100"))["recent_messages"]
    response = await facade.forget(owner_id="100")
    await save_reply(facade, "1002", "/forget", response.text)
    assert context(app.service.database, facade._memory_owner("100"))["recent_messages"] == []
    with app.service.database.session() as session:
        assert session.get(IncomingUpdateRecord, "1001") is not None
    await facade.aclose()


@pytest.mark.asyncio
async def test_m5_dashboard_codes_and_memory_dump_are_not_remembered(app):
    facade = TelegramApplicationFacade(app)
    await save_reply(facade, "1001", "/dashboard", "One-use login code: ABCDEFG23456")
    await save_reply(facade, "1002", "/memory", "Sensitive old recap shown to owner")
    assert context(app.service.database, facade._memory_owner("100"))["recent_messages"] == []
    await facade.aclose()


@pytest.mark.asyncio
async def test_m6_screenshot_followup_preserves_details_and_reports_limits(app):
    facade = TelegramApplicationFacade(app)
    app.service.set_paused(True)
    await facade.create_trade_from_text(
        owner_id="100", text="i want you to open a trade for sell side lot 0.05 and take 10$ profit"
    )
    response = await facade.create_trade_from_text(
        owner_id="100",
        text="you can set tp according to given price . and symbol is XAUUSD this . don't need to add stop_loss for now.",
    )
    draft = await facade._conversation.current("100")
    assert draft["parsed"]["side"] == "sell" and draft["parsed"]["volume"] == "0.05"
    assert draft["parsed"]["profit_target_usd"] == "10" and draft["parsed"]["symbol"] == "XAUUSD"
    assert draft["parsed"]["entry"]["kind"] == "missing" and draft["parsed"]["stop_loss"] is None
    assert (
        "EURUSD" in response.text and "mandatory" in response.text and "never on" in response.text
    )
    assert response.confirmation_token is None and app.service._paused()
    await facade.aclose()
