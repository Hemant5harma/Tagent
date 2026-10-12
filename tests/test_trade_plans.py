from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_agent.config import Settings
from trade_agent.domain import SymbolSpec
from trade_agent.trade_plans import build_plans


def fixture(side="buy"):
    bars = [
        {"time": str(i), "open": "1.1032", "high": "1.104", "low": "1.103", "close": "1.1035"}
        for i in range(60)
    ]
    bars[-1].update(open="1.104", high="1.1052", low="1.1034", close="1.105")
    snapshot = {
        "symbol": "EURUSD",
        "timeframe": "M15",
        "as_of": "2026-10-02T17:00:01+00:00",
        "closed_bar_at": "2026-10-02T17:00:00+00:00",
        "bars": bars,
        "quote": {"bid": "1.105", "ask": "1.1051"},
        "indicators": {"ema_fast": "1.1045", "ema_slow": "1.104", "rsi": "60", "atr": "0.001"},
    }
    if side == "sell":
        for bar in bars:
            before = dict(bar)
            for key, other in (
                ("open", "open"),
                ("close", "close"),
                ("low", "high"),
                ("high", "low"),
            ):
                bar[key] = str(Decimal("2.2") - Decimal(before[other]))
        snapshot["quote"] = {"bid": "1.095", "ask": "1.0951"}
        snapshot["indicators"].update(ema_fast="1.0955", ema_slow="1.096", rsi="40")
    higher = {
        tf: {**deepcopy(snapshot), "timeframe": tf, "closed_bar_at": closed}
        for tf, closed in (("H1", "2026-10-02T17:00:00+00:00"), ("H4", "2026-10-02T16:00:00+00:00"))
    }
    spec = SymbolSpec(
        symbol="EURUSD",
        point="0.00001",
        digits=5,
        volume_min="0.01",
        volume_max="10",
        volume_step="0.01",
        min_stop_distance_points="10",
        trade_tick_size="0.00001",
        trade_tick_value="1",
    )
    return snapshot, higher, spec, Settings(_env_file=None)


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_aligned_breakout_plan_has_correct_sides_grid_and_budget(side):
    snapshot, higher, spec, settings = fixture(side)
    plan = next(
        p for p in build_plans(snapshot, higher, spec, settings)["plans"] if p["side"] == side
    )
    assert plan["status"] == "ready", plan["blockers"]
    entry, stop, target = (Decimal(plan[k]) for k in ("entry", "stop_loss", "take_profit"))
    assert stop < entry < target if side == "buy" else target < entry < stop
    assert stop % spec.trade_tick_size == target % spec.trade_tick_size == 0
    assert Decimal(plan["reward_risk"]) >= settings.autonomy_reward_risk
    assert Decimal(plan["estimated_risk"]) <= settings.autonomy_risk_amount
    assert plan["trigger"] == "closed-bar breakout"


@pytest.mark.parametrize("problem", ["missing", "future", "stale", "mixed"])
def test_missing_future_or_conflicting_context_cannot_be_ready(problem):
    s, higher, spec, cfg = fixture()
    if problem == "missing":
        higher["H1"] = None
    elif problem == "future":
        higher["H1"]["closed_bar_at"] = "2026-10-02T18:00:00+00:00"
    elif problem == "stale":
        higher["H1"]["closed_bar_at"] = "2026-10-01T12:00:00+00:00"
    else:
        higher["H1"]["indicators"]["ema_fast"] = "1.2"
    plan = build_plans(s, higher, spec, cfg)["plans"][0]
    assert plan["status"] == "wait"
    assert any("H1/H4" in b for b in plan["blockers"])


def test_nearby_resistance_rejects_unrealistic_reward():
    s, higher, spec, cfg = fixture()
    s["bars"][-8]["high"] = "1.106"
    plan = build_plans(s, higher, spec, cfg)["plans"][0]
    assert plan["status"] == "wait"
    assert Decimal(plan["reward_risk"]) < cfg.autonomy_reward_risk
    assert "opposing pivot" in plan["target_basis"]


def test_expensive_spread_and_minimum_lot_are_explained():
    s, higher, spec, cfg = fixture()
    s["quote"]["ask"] = "1.1055"
    cfg.autonomy_risk_amount = Decimal("0.01")
    plan = build_plans(s, higher, spec, cfg)["plans"][0]
    assert plan["status"] == "wait" and plan["volume"] == "0"
    assert any("Spread" in b for b in plan["blockers"])
    assert any("minimum volume" in b for b in plan["blockers"])


async def test_enabled_planning_cannot_fall_back_to_legacy_execution(tmp_path):
    from trade_agent.autonomy import AutonomyController
    from trade_agent.main import build_runtime

    cfg = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'plans.db'}",
        auto_create_schema=True,
        telegram_owner_id="100",
        allowed_symbols=["EURUSD"],
        autonomy_trade_plans_enabled=True,
    )
    facade, _ = build_runtime(cfg)
    controller = AutonomyController(facade.service)
    snapshot = {"indicators": {"atr": "0.001"}, "closed_bar_at": datetime.now(UTC).isoformat()}
    with pytest.raises(ValueError, match="unexpired ready"):
        controller._instruction("100", "EURUSD", "buy", snapshot, None)
    snapshot["trade_plans"] = {
        "plans": [
            {
                "side": "buy",
                "status": "ready",
                "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            }
        ]
    }
    with pytest.raises(ValueError, match="unexpired ready"):
        controller._instruction("100", "EURUSD", "buy", snapshot, None)
