from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from trade_agent.ai import AIBackendUnavailable, ParsedEntry, TradeProposalParse
from trade_agent.app import ApplicationFacade, TelegramApplicationFacade
from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.dashboard import COOKIE, create_dashboard, issue_code
from trade_agent.db import DashboardAccessRecord, Database, IncomingUpdateRecord, ProposalRecord
from trade_agent.delivery import current_update_id
from trade_agent.domain import Side, TradeInstruction
from trade_agent.reporting import Reporter, closed_trades, dashboard_data, metrics, scrub
from trade_agent.risk import RiskRejected
from trade_agent.service import ProposalError, TradeService
from trade_agent.workflow import ConversationRunner


@pytest.fixture
def service(tmp_path):
    current_update_id.set(None)
    settings = Settings(
        _env_file=None, database_url=f"sqlite:///{tmp_path / 'release.db'}", telegram_owner_id="100"
    )
    db = Database(settings.database_url)
    db.create_all()
    return TradeService(settings, db, FakeBroker(settings.account_id))


def instruction():
    return TradeInstruction(
        owner_id="100",
        account_id="demo-mt5",
        symbol="EURUSD",
        side=Side.BUY,
        volume=Decimal("0.10"),
        entry_price=None,
        stop_loss=Decimal("1.07900"),
        take_profit=Decimal("1.08200"),
    )


def test_dashboard_session_is_single_use_and_read_only(service):
    app = create_dashboard(service.database, Path("frontend/dist"))
    with TestClient(app, base_url="https://dashboard.example") as client:
        assert client.get("/dashboard-api/snapshot").status_code == 401
        code = issue_code(service.database)
        with service.database.session() as session:
            assert all(
                code not in row.key for row in session.scalars(select(DashboardAccessRecord))
            )
        response = client.post("/dashboard-api/login", json={"code": code})
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "Secure" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        assert client.cookies.get(COOKIE)
        assert client.post("/dashboard-api/login", json={"code": code}).status_code == 401
        assert client.get("/dashboard-api/snapshot").status_code == 200
        assert client.post("/v1/proposals", json={}).status_code == 404
        assert client.post("/v1/positions/1/close", json={}).status_code == 404
        assert client.get("/docs").status_code == 404
        assert client.post("/dashboard-api/logout").status_code == 200
        assert client.get("/dashboard-api/snapshot").status_code == 401


def test_dashboard_expired_code_and_rate_limit(service):
    code = issue_code(service.database)
    with service.database.session() as session:
        row = session.scalar(
            select(DashboardAccessRecord).where(DashboardAccessRecord.kind == "code")
        )
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with TestClient(
        create_dashboard(service.database, Path("frontend/dist")),
        base_url="https://dashboard.example",
    ) as client:
        assert client.post("/dashboard-api/login", json={"code": code}).status_code == 401
        for _ in range(4):
            assert client.post("/dashboard-api/login", json={"code": "WRONG"}).status_code == 401
        assert client.post("/dashboard-api/login", json={"code": "WRONG"}).status_code == 429


def test_zero_data_metrics_are_not_invented(service):
    Reporter(service).collect()
    data = dashboard_data(service.database)
    assert data["metrics"]["win_rate"] is None
    assert data["metrics"]["profit_factor"] is None
    assert data["drawdown"] is None
    assert data["instruction_accuracy"] == "Not evaluated"
    assert len(data["equity"]) == 1
    assert data["prediction_accuracy"] == "Not available in assisted mode"


def deal(ticket, entry, volume, profit, **extra):
    return {
        "ticket": str(ticket),
        "order": str(ticket),
        "position_id": "42",
        "entry": str(entry),
        "type": "0" if entry == 0 else "1",
        "volume": str(volume),
        "price": "1.08",
        "profit": str(profit),
        "commission": "-1",
        "swap": "0",
        "fee": "0",
        "magic": "2026092201",
        "symbol": "EURUSD",
        "time": str(1700000000 + ticket),
        "time_msc": str((1700000000 + ticket) * 1000),
        **extra,
    }


def test_partial_closures_are_one_net_lifecycle():
    deals = [deal(1, 0, "0.2", 0), deal(2, 1, "0.1", 10), deal(3, 1, "0.1", -2, swap="-2")]
    assert closed_trades(deals[:2], {"42"}, set()) == []
    trades = closed_trades(deals, {"42"}, set())
    assert len(trades) == 1 and trades[0]["net_pnl"] == "3"
    assert metrics(trades)["win_rate"] == 100
    assert closed_trades(deals, {"42"}, {"42"}) == []
    assert closed_trades([deals[-1]], {"42"}, set()) == []
    assert closed_trades([*deals, deal(4, 2, "0.1", 0)], {"42"}, set()) == []


def test_cash_and_mixed_ownership_do_not_inflate_strategy_metrics():
    deposits = [deal(5, 0, "0", 10000, type="2", position_id="0")]
    assert closed_trades(deposits, set(), set()) == []
    mixed = [deal(1, 0, "0.1", 0), deal(2, 0, "0.1", 0, magic="0"), deal(3, 1, "0.2", 10)]
    assert closed_trades(mixed, {"42"}, set())[0]["source"] == "external"
    result = metrics([{"net_pnl": "10"}, {"net_pnl": "-5"}, {"net_pnl": "0"}])
    assert result["breakevens"] == 1 and result["profit_factor"] == 2
    assert result["win_rate"] == pytest.approx(100 / 3)


@pytest.mark.asyncio
async def test_greeting_and_durable_reply_do_not_create_trade(service):
    app = TelegramApplicationFacade(ApplicationFacade(service.settings, service))
    await app.record_telegram_update(update_id="123", owner_id="100")
    reply = await app.create_trade_from_text(owner_id="100", text="hi")
    assert "Confirm trade" in reply.text
    await app.queue_reply("123", reply)
    with service.database.session() as session:
        assert session.scalar(select(ProposalRecord)) is None
        row = session.get(IncomingUpdateRecord, "123")
        assert row.status == "completed" and row.reply["text"] == reply.text
    await app.aclose()
    current_update_id.set(None)


def test_proposal_recovery_by_update_id(service):
    token = current_update_id.set("555")
    try:
        a = service.create_proposal(instruction())
        b = service.create_proposal(instruction())
        assert a.proposal_id == b.proposal_id
    finally:
        current_update_id.reset(token)


@pytest.mark.asyncio
async def test_completed_draft_does_not_leak_and_parser_failure_stops():
    count = 0

    async def parser(message):
        nonlocal count
        count += 1
        if count == 1:
            return TradeProposalParse(
                intent="trade",
                symbol="EURUSD",
                side="buy",
                volume=Decimal("0.1"),
                entry=ParsedEntry(kind="market"),
                stop_loss=Decimal("1.07"),
                take_profit=Decimal("1.09"),
            )
        if count == 2:
            assert "Existing draft" not in message
            return TradeProposalParse(
                intent="trade", symbol="EURUSD", side="sell", entry=ParsedEntry(kind="missing")
            )
        raise AIBackendUnavailable("test outage")

    runner = ConversationRunner(parser, "sqlite://")
    assert (await runner.run("100", "buy EURUSD complete"))["stage"] == "ready_for_confirmation"
    partial = await runner.run("100", "sell EURUSD")
    assert (
        "volume" in partial["requires_trade_fields"]
        and "stop_loss" in partial["requires_trade_fields"]
    )
    failed = await runner.run("100", "more details")
    assert failed["stage"] == "failed" and not failed["parsed"]
    await runner.reset("100")
    await runner.aclose()


def test_wrong_account_stale_quote_and_quote_movement_block(service):
    with pytest.raises(RiskRejected, match="account"):
        service.create_proposal(instruction().model_copy(update={"account_id": "other"}))
    original_quote = service.broker.quote
    service.broker.quote = lambda symbol: original_quote(symbol).model_copy(
        update={"observed_at": datetime.now(UTC) - timedelta(minutes=1)}
    )
    with pytest.raises(RiskRejected, match="stale"):
        service.create_proposal(instruction())
    service.broker.quote = original_quote
    proposal = service.create_proposal(instruction())
    service.broker.quote = lambda symbol: original_quote(symbol).model_copy(
        update={"ask": Decimal("1.08100"), "bid": Decimal("1.08090")}
    )
    result = service.confirm(proposal.proposal_id, "100", 1, proposal.confirmation_token)
    assert result["status"] == "rejected"
    assert service.broker.state("demo-mt5").positions == []


def test_manual_close_requires_confirmation_and_replay_is_blocked(service):
    entry = service.create_proposal(instruction())
    service.confirm(entry.proposal_id, "100", 1, entry.confirmation_token)
    position = service.broker.state("demo-mt5").positions[0]
    exit_proposal = service.create_exit_proposal("100", position.position_id, "position")
    assert len(service.broker.state("demo-mt5").positions) == 1
    service.confirm_exit(exit_proposal.proposal_id, "100", 1, exit_proposal.confirmation_token)
    assert service.broker.state("demo-mt5").positions == []
    with pytest.raises(ProposalError):
        service.confirm_exit(exit_proposal.proposal_id, "100", 1, exit_proposal.confirmation_token)


def test_activity_redacts_labeled_secrets():
    assert "abcdef" not in scrub("password=abcdef")
    assert "xyz123" not in scrub("api_key: xyz123")
