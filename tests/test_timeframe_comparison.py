from datetime import UTC, datetime, timedelta

import pytest

from trade_agent.broker import FakeBroker
from trade_agent.config import Settings
from trade_agent.market_data import MarketDataCollector
from trade_agent.timeframe_comparison import COMPARISON_COUNTS, compare_timeframes


def evidence():
    broker = FakeBroker("demo-mt5")
    collector = MarketDataCollector(broker)
    frames = {tf: collector.collect("EURUSD", tf, count) for tf, count in COMPARISON_COUNTS.items()}
    higher = {"H1": collector.collect("EURUSD", "H1", 200)}
    return (
        frames,
        higher,
        broker.symbol("EURUSD"),
        Settings(_env_file=None),
        datetime.now(UTC).isoformat(),
    )


def test_three_entry_frames_with_equal_candle_hours_and_h1_context():
    result = compare_timeframes(*evidence())
    assert [r["timeframe"] for r in result["frames"]] == ["M5", "M15", "M30"]
    assert all(r["status"] == "evaluated" and len(r["plans"]) == 2 for r in result["frames"])
    assert [r["bar_count"] for r in result["frames"]] == [600, 200, 100]
    assert all(set(r["context"]) == {"H1"} for r in result["frames"])


@pytest.mark.parametrize("bad", ["missing", "future", "insufficient"])
def test_no_current_candidate_from_missing_or_future_candles(bad):
    frames, higher, spec, cfg, cutoff = evidence()
    if bad == "missing":
        frames["M30"] = None
    elif bad == "future":
        frames["M30"]["closed_bar_at"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    else:
        frames["M30"]["bars"].pop()
    result = compare_timeframes(frames, higher, spec, cfg, cutoff)
    assert result["frames"][2]["status"] == "unavailable"
    assert result["frames"][2]["plans"] == []


async def test_comparison_cannot_enable_demo_orders(tmp_path):
    from trade_agent.autonomy import AutonomyController
    from trade_agent.main import build_runtime

    cfg = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'comparison.db'}",
        auto_create_schema=True,
        telegram_owner_id="100",
        autonomy_mode="demo",
        autonomy_comparison_enabled=True,
    )
    f, _ = build_runtime(cfg)
    with pytest.raises(ValueError, match="shadow-only"):
        await AutonomyController(f.service).run("100", "EURUSD", mode="demo")
    assert not f.service.broker.state(cfg.account_id).positions


async def test_shadow_pipeline_archives_comparison_without_orders(tmp_path):
    import json

    from trade_agent.autonomy import AutonomyController
    from trade_agent.main import build_runtime

    calls = []

    class Research:
        async def analyze(self, snapshot):
            calls.append(snapshot["symbol"])
            assert any(e["id"] == "timeframe-comparison" for e in snapshot["evidence"])
            json.dumps(snapshot)
            return {
                "direction": "wait",
                "confidence": 0,
                "reason": "comparison test",
                "reports": [],
            }

    cfg = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'pipeline.db'}",
        auto_create_schema=True,
        telegram_owner_id="100",
        autonomy_mode="shadow",
        autonomy_trade_plans_enabled=True,
        autonomy_comparison_enabled=True,
    )
    f, _ = build_runtime(cfg)
    controller = AutonomyController(f.service, researcher=Research())
    result = await controller.run("100", "EURUSD")
    assert result["status"] == "wait"
    assert result["decision"]["execution_gate"] == "No ready trade plan; model not invoked"
    assert calls == []
    assert len(result["decision"]["timeframe_comparison"]["frames"]) == 3
    saved = controller.runs()[0]["snapshot"]
    assert set(saved["comparison_bars"]) == {"M5", "M30"}
    assert not f.service.broker.state(cfg.account_id).positions


@pytest.mark.parametrize("planning_state", ["unavailable", "ready"])
async def test_shadow_prefilter_preserves_failure_and_ready_model_paths(
    tmp_path, monkeypatch, planning_state
):
    from trade_agent.autonomy import AutonomyController
    from trade_agent.main import build_runtime

    calls = []

    class Research:
        async def analyze(self, snapshot):
            calls.append(snapshot["symbol"])
            return {"direction": "wait", "confidence": 0, "reason": "abstain", "reports": []}

    if planning_state == "unavailable":

        def plans_unavailable(*args, **kwargs):
            raise ValueError("incomplete broker data")

        monkeypatch.setattr("trade_agent.trade_plans.build_plans", plans_unavailable)
    else:
        monkeypatch.setattr(
            "trade_agent.trade_plans.build_plans",
            lambda *args, **kwargs: {"plans": [{"side": "buy", "status": "ready"}]},
        )
    cfg = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / f'{planning_state}.db'}",
        auto_create_schema=True,
        telegram_owner_id="100",
        autonomy_mode="shadow",
        autonomy_trade_plans_enabled=True,
    )
    facade, _ = build_runtime(cfg)
    controller = AutonomyController(facade.service, researcher=Research())
    result = await controller.run("100", "EURUSD")
    assert not facade.service.broker.state(cfg.account_id).positions
    if planning_state == "unavailable":
        assert result["status"] == "research_failed"
        assert (
            result["decision"]["execution_gate"] == "Trade planning unavailable; model not invoked"
        )
        assert calls == []
    else:
        assert result["status"] == "wait"
        assert calls == ["EURUSD"]
