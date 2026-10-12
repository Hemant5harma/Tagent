from datetime import UTC, datetime, timedelta

from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.db import AutonomousRunRecord, Database
from trade_agent.reporting import Reporter, dashboard_data
from trade_agent.research_diagnostics import collect, summarize
from trade_agent.service import TradeService


def test_summary_counts_runs_not_duplicate_direction_blockers():
    stamp = datetime(2026, 10, 11, tzinfo=UTC)
    rows = [
        (
            "wait",
            "XAUUSD",
            {
                "execution_gate": "No ready trade plan; model not invoked",
                "trade_plans": {
                    "plans": [
                        {
                            "side": "buy",
                            "status": "wait",
                            "blockers": ["Target after costs", "Spread too high"],
                        },
                        {"side": "sell", "status": "wait", "blockers": ["Target after costs"]},
                    ]
                },
            },
            stamp,
        ),
        (
            "research_failed",
            "EURUSD",
            {
                "trade_plans": {"plans": [{"side": "buy", "status": "ready", "blockers": []}]},
                "research_health": {"judge": {"status": "failed", "error_type": "timeout"}},
            },
            stamp - timedelta(minutes=15),
        ),
    ]
    result = summarize(rows, as_of=stamp, truncated=False)
    assert result["observed_runs"] == 2
    assert result["status_counts"] == {"wait": 1, "research_failed": 1}
    assert result["plan_ready_runs"] == 1
    assert result["top_blockers"][0] == {"reason": "Target after costs", "runs": 1}
    assert result["model_failures"] == [{"reason": "judge: timeout", "occurrences": 1}]


def test_tied_opposite_side_rules_do_not_look_universal():
    stamp = datetime(2026, 10, 11, tzinfo=UTC)
    rows = [
        (
            "wait",
            "XAUUSD",
            {
                "trade_plans": {
                    "plans": [
                        {"side": "buy", "status": "wait", "blockers": ["Buy-only", "Shared"]},
                        {"side": "sell", "status": "wait", "blockers": ["Sell-only", "Shared"]},
                    ]
                }
            },
            stamp,
        )
    ]
    result = summarize(rows, as_of=stamp, truncated=False)
    assert result["tied_closest_runs"] == 1
    assert result["top_blockers"] == [{"reason": "Shared", "runs": 1}]


def test_reporter_publishes_read_only_diagnostics(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'research-diagnostics.db'}")
    db.create_all()
    settings = Settings(_env_file=None, account_id="demo-mt5")
    service = TradeService(settings, db, FakeBroker(settings.account_id))
    stamp = datetime.now(UTC)
    with db.session() as session:
        session.add(
            AutonomousRunRecord(
                id="research-run",
                account_id=settings.account_id,
                owner_id="owner",
                symbol="XAUUSD",
                timeframe="M15",
                bar_at=stamp,
                strategy_version="test-v1",
                mode="shadow",
                status="wait",
                snapshot={"trade_plans": {"plans": []}},
                reports=[],
                decision={"execution_gate": "No ready trade plan; model not invoked"},
                created_at=stamp,
                updated_at=stamp,
            )
        )
    assert collect(db, settings.account_id)["observed_runs"] == 1
    Reporter(service).collect()
    dashboard = dashboard_data(db)
    assert dashboard["research_diagnostics"]["observed_runs"] == 1
    assert dashboard["research_diagnostics"]["status_counts"]["wait"] == 1
