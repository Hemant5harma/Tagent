import pytest

from trade_agent.config import Settings
from trade_agent.intelligence import ResearchWorkflow
from trade_agent.research_facts import ResearchQualityError, research_facts, validate_prose


def evidence():
    return {
        "symbol": "EURUSD",
        "timeframe": "M15",
        "as_of": "2026-10-02T10:00:01Z",
        "closed_bar_at": "2026-10-02T10:00:00Z",
        "data_mode": "mt5",
        "bars": [
            {"time": f"bar-{i}", "high": "1.12412", "low": str(low), "close": "1.12308"}
            for i, low in enumerate([1.123, 1.1217, 1.122, 1.12158, 1.123, 1.12145, 1.123])
        ],
        "indicators": {
            "ema_fast": "1.12522",
            "ema_slow": "1.12515",
            "rsi": "48.4",
            "atr": "0.0006",
        },
        "quote": {"bid": "1.12503", "ask": "1.12517"},
        "evidence": [{"id": "price", "source": "mt5", "observed_at": "now", "text": "Candles"}],
    }


def test_numeric_facts_preserve_actual_trough_and_pip_units():
    facts = research_facts(evidence())
    assert facts["window_low"] == "1.12145"
    assert float(facts["max_single_bar_range_pips"]) == pytest.approx(26.7)
    assert float(facts["atr_pips"]) == pytest.approx(6)
    assert facts["bar_count"] == 7
    assert not facts["strictly_rising_recent_pivot_lows"]
    assert facts["strictly_falling_recent_pivot_lows"]
    assert facts["close_vs_ema20"] == "below"
    assert facts["close_vs_ema50"] == "below"


@pytest.mark.parametrize(
    "claim",
    [
        "Candle ranges exceeded 200 pips",
        "Only 100 candles supplied",
        "A higher-timeframe trend is confirmed",
        "A higher-low structure supports buying",
        "The low is 1.1217",
    ],
)
def test_reject_observed_model_errors(claim):
    with pytest.raises(ResearchQualityError):
        validate_prose({"summary": claim}, research_facts(evidence()), {"price"})


class GuardedModel:
    def __init__(self, correct):
        self.correct = correct
        self.prompts = []

    async def complete(self, role, prompt, schema):
        self.prompts.append((role, prompt))
        if role == "judge":
            return {
                "direction": "wait",
                "confidence": 0.7,
                "reason": "Mixed momentum",
                "evidence_ids": ["verified-facts"],
            }
        summary = "Neutral RSI14 and mixed EMA20 alignment; see verified-facts"
        if role == "technical" and (not self.correct or "Correction required" not in prompt):
            summary = "Candle ranges exceed 200 pips"
        return {"summary": summary, "limitations": ["M15 only"], "evidence_ids": ["price"]}


async def test_bad_report_corrected_before_debate_and_code_numbers_rendered():
    model = GuardedModel(True)
    result = await ResearchWorkflow(Settings(_env_file=None), model).analyze(evidence())
    assert len(result["reports"]) == 5
    assert "window low 1.12145" in result["reports"][0]["output"]["summary"]
    assert all(
        "200 pips" not in prompt
        for role, prompt in model.prompts
        if role in {"bull", "bear", "judge"}
    )


async def test_persistent_inaccuracy_abstains_with_visible_validation_reason():
    result = await ResearchWorkflow(Settings(_env_file=None), GuardedModel(False)).analyze(
        evidence()
    )
    assert result["direction"] == "wait"
    assert "factual validation" in result["reason"]
    assert all(r["role"] != "technical" for r in result["reports"])
