"""Natural Telegram questions must not depend on the structured trade parser."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from trade_agent.ai import AIBackendResponseError, ParsedEntry, TradeProposalParse
from trade_agent.app import ApplicationFacade, TelegramApplicationFacade
from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.db import Database, ProposalRecord
from trade_agent.delivery import current_update_id
from trade_agent.domain import PositionSnapshot, Side
from trade_agent.service import TradeService


class ConversationBackend:
    def __init__(self):
        self.parsed_messages = []
        self.explained_messages = []
        self.results = []

    async def parse_trade(self, text):
        self.parsed_messages.append(text)
        if self.results:
            return self.results.pop(0)
        raise AIBackendResponseError("provider returned an invalid structured trade parse")

    async def explain(self, text, evidence):
        self.explained_messages.append((text, evidence))
        return "You describe a trade, review its details, and confirm once."


@pytest.fixture
def chat(tmp_path, monkeypatch):
    current_update_id.set(None)
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'chat.db'}",
        telegram_owner_id="100",
        ai_provider="openai_compatible",
    )
    database = Database(settings.database_url)
    database.create_all()
    broker = FakeBroker(settings.account_id)
    service = TradeService(settings, database, broker)
    backend = ConversationBackend()
    monkeypatch.setattr("trade_agent.app.build_ai_backend", lambda _: backend)

    def no_mutation(*args, **kwargs):
        pytest.fail("Conversation/query attempted a broker mutation")

    for method in ("submit", "modify", "cancel", "close"):
        monkeypatch.setattr(broker, method, no_mutation)
    return TelegramApplicationFacade(ApplicationFacade(settings, service)), backend


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("do we have any open trades ?", "No open positions."),
        ("Do we have any open trades?", "No open positions."),
        ("any open positions?", "No open positions."),
        ("show my positions", "No open positions."),
        ("are there any pending orders", "No pending orders."),
        ("show my pending orders?", "No pending orders."),
        ("what is my balance?", "Balance: 10000 USD"),
        ("show my account", "Account demo-mt5 (demo)"),
        ("balance", "Equity: 10000"),
        ("hi", "Confirm trade"),
    ],
)
async def test_broker_questions_work_when_structured_provider_is_broken(chat, message, expected):
    app, backend = chat
    try:
        reply = await app.create_trade_from_text(owner_id="100", text=message)
        assert expected in reply.text
        assert reply.confirmation_token is None
        assert backend.parsed_messages == []
        assert backend.explained_messages == []
        with app.application.service.database.session() as session:
            assert session.scalar(select(ProposalRecord)) is None
    finally:
        await app.aclose()


async def test_open_trades_question_reports_actual_position_not_model_guess(chat):
    app, backend = chat
    app.application.service.broker._positions["4321"] = PositionSnapshot(
        position_id="4321",
        symbol="XAUUSD",
        side=Side.SELL,
        volume=Decimal("0.01"),
        entry_price=Decimal("3800"),
        stop_loss=None,
        take_profit=None,
        profit=Decimal("-3.25"),
        opened_at=datetime.now(UTC),
    )
    try:
        reply = await app.create_trade_from_text(
            owner_id="100", text="do we have any open trades ?"
        )
        assert "4321" in reply.text and "XAUUSD" in reply.text and "-3.25" in reply.text
        assert backend.parsed_messages == [] and backend.explained_messages == []
        assert len(app.application.service.broker._positions) == 1
    finally:
        await app.aclose()


@pytest.mark.parametrize(
    "question", ["explain how this works", "what is equity?", "how can I buy EURUSD?"]
)
async def test_explanations_bypass_structured_parser(chat, question):
    app, backend = chat
    try:
        reply = await app.create_trade_from_text(owner_id="100", text=question)
        assert "confirm once" in reply.text
        assert reply.confirmation_token is None
        assert backend.parsed_messages == []
        assert len(backend.explained_messages) == 1
    finally:
        await app.aclose()


async def test_trade_question_is_not_misclassified_as_read_only(chat):
    app, backend = chat
    backend.results.append(
        TradeProposalParse(
            intent="trade",
            symbol="EURUSD",
            side="buy",
            entry=ParsedEntry(kind="missing"),
            missing_fields=["entry"],
        )
    )
    try:
        reply = await app.create_trade_from_text(owner_id="100", text="can you buy EURUSD")
        assert "Please provide:" in reply.text
        for field in ("volume", "entry", "stop_loss", "take_profit"):
            assert field in reply.text
        assert reply.confirmation_token is None
        assert len(backend.parsed_messages) == 1
        assert backend.explained_messages == []
    finally:
        await app.aclose()


async def test_account_question_preserves_draft_and_grouped_clarification(chat):
    app, backend = chat
    backend.results.extend(
        [
            TradeProposalParse(
                intent="trade",
                symbol="EURUSD",
                side="buy",
                entry=ParsedEntry(kind="missing"),
                missing_fields=["entry"],
            ),
            TradeProposalParse(
                intent="trade", volume=Decimal("0.10"), entry=ParsedEntry(kind="market")
            ),
        ]
    )
    try:
        initial = await app.create_trade_from_text(owner_id="100", text="buy EURUSD")
        assert all(
            field in initial.text for field in ("entry", "volume", "stop_loss", "take_profit")
        )
        account = await app.create_trade_from_text(owner_id="100", text="what is my balance?")
        assert "10000" in account.text
        followup = await app.create_trade_from_text(owner_id="100", text="0.10 lots entry market")
        assert "stop_loss" in followup.text and "take_profit" in followup.text
        assert "symbol" not in followup.text and "side" not in followup.text
        assert "entry" not in followup.text and "volume" not in followup.text
        assert len(backend.parsed_messages) == 2
        assert "Existing draft:" in backend.parsed_messages[1]
        assert "EURUSD" in backend.parsed_messages[1]
        assert followup.confirmation_token is None
    finally:
        await app.aclose()


async def test_conversational_yes_does_not_confirm_or_execute(chat):
    app, backend = chat
    try:
        reply = await app.create_trade_from_text(owner_id="100", text="yes")
        assert reply.confirmation_token is None
        with app.application.service.database.session() as session:
            assert session.scalar(select(ProposalRecord)) is None
        assert backend.parsed_messages == []
    finally:
        await app.aclose()
