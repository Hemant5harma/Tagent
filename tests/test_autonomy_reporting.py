from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.dashboard import create_dashboard, issue_code
from trade_agent.db import (
    AutonomousRunRecord,
    BrokerDealRecord,
    Database,
    ExecutionIntentRecord,
    ProposalRecord,
)
from trade_agent.reporting import Reporter, closed_trades, dashboard_data
from trade_agent.service import TradeService


def deal(ticket, position, entry, profit):
    stamp = int(datetime.now(UTC).timestamp()) - 100 + ticket
    return {
        "ticket": str(ticket),
        "position_id": position,
        "entry": str(entry),
        "type": "0" if entry == 0 else "1",
        "volume": "0.1",
        "price": "1.08",
        "profit": str(profit),
        "commission": "-1",
        "swap": "0",
        "fee": "0",
        "magic": "777",
        "symbol": "EURUSD",
        "time": str(stamp),
        "time_msc": str(stamp * 1000),
    }


def test_netting_mixed_origin_is_unknown_not_autonomous():
    deals = [deal(1, "42", 0, 0), deal(2, "42", 1, 10)]
    result = closed_trades(deals, {"42"}, set(), {"42": {"manual", "autonomous"}})
    assert result[0]["source"] == "agent"
    assert result[0]["origin"] == "unknown"
    assert closed_trades(deals, {"42"}, set(), {})[0]["origin"] == "unknown"
    assert closed_trades(deals, set(), set(), {})[0]["origin"] == "external"


def test_origin_scopes_and_read_only_evidence(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'origins.db'}")
    db.create_all()
    settings = Settings(_env_file=None, telegram_owner_id="100")
    service = TradeService(settings, db, FakeBroker(settings.account_id))
    Reporter(service).collect()
    stamp = datetime.now(UTC)
    with db.session() as session:
        for number, origin, pnl in [(1, "manual", 10), (2, "autonomous", -5)]:
            proposal_id, position = f"p{number}", str(number)
            session.add(
                ProposalRecord(
                    id=proposal_id,
                    owner_id="100",
                    account_id=settings.account_id,
                    status="executed",
                    instruction={"symbol": "EURUSD"},
                    validation={},
                    order_type="market",
                    confirmation_token=f"token{number}",
                    expires_at=stamp + timedelta(minutes=1),
                    created_at=stamp,
                    updated_at=stamp,
                    origin=origin,
                )
            )
            session.add(
                ExecutionIntentRecord(
                    id=f"i{number}",
                    proposal_id=proposal_id,
                    idempotency_key=f"key{number}",
                    status="closed",
                    broker_position_id=position,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
            for payload in [
                deal(number * 2, position, 0, 0),
                deal(number * 2 + 1, position, 1, pnl),
            ]:
                session.add(
                    BrokerDealRecord(
                        id=f"deal{payload['ticket']}",
                        account_id=settings.account_id,
                        position_id=position,
                        occurred_at=stamp,
                        payload=payload,
                    )
                )
        session.add(
            AutonomousRunRecord(
                id="run1",
                account_id=settings.account_id,
                owner_id="100",
                symbol="EURUSD",
                timeframe="M15",
                bar_at=stamp,
                strategy_version="technical-v1",
                mode="shadow",
                status="wait",
                snapshot={"coverage": "closed candles"},
                reports=[{"role": "technical", "summary": "No setup"}],
                decision={"action": "WAIT"},
                created_at=stamp,
                updated_at=stamp,
            )
        )
    manual = dashboard_data(db, scope="manual")
    autonomous = dashboard_data(db, scope="autonomous")
    combined = dashboard_data(db, scope="agent")
    assert manual["metrics"]["net_realized_pnl"] == "8"
    assert autonomous["metrics"]["net_realized_pnl"] == "-7"
    assert combined["metrics"]["net_realized_pnl"] == "1"
    assert autonomous["metrics"]["costs"] == "-2"
    assert autonomous["metrics"]["closed_trades"] == 1  # WAIT is not a trade
    assert autonomous["autonomy_runs"][0]["decision"]["action"] == "WAIT"
    assert autonomous["autonomy_runs"][0]["reports"][0]["role"] == "technical"
    assert autonomous["drawdown_scope"].startswith("Account-wide")
    with TestClient(create_dashboard(db, Path("frontend/dist")), base_url="https://test") as client:
        assert client.get("/dashboard-api/snapshot?scope=autonomous").status_code == 401
        assert client.post("/dashboard-api/login", json={"code": issue_code(db)}).status_code == 200
        assert client.get("/dashboard-api/snapshot?scope=manual").status_code == 200
        assert (
            client.get("/dashboard-api/snapshot?scope=autonomous").json()["metrics"][
                "closed_trades"
            ]
            == 1
        )
        assert client.post("/v1/autonomy/run", json={}).status_code == 404
