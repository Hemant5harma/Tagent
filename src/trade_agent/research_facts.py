"""Code-owned numerical evidence; model prose cannot invent measurements."""

import re
from decimal import Decimal


class ResearchQualityError(ValueError):
    """A report failed a deterministic factual guard."""


def research_facts(snapshot: dict) -> dict:
    bars = snapshot["bars"]
    lows = [Decimal(str(b["low"])) for b in bars]
    highs = [Decimal(str(b["high"])) for b in bars]
    ranges = [hi - lo for hi, lo in zip(highs, lows, strict=True)]
    troughs = [
        {"time": bars[i]["time"], "low": str(lows[i])}
        for i in range(1, len(bars) - 1)
        if lows[i] < lows[i - 1] and lows[i] <= lows[i + 1]
    ][-4:]
    rising = len(troughs) >= 3 and all(
        Decimal(b["low"]) > Decimal(a["low"]) for a, b in zip(troughs, troughs[1:], strict=False)
    )
    falling = len(troughs) >= 3 and all(
        Decimal(b["low"]) < Decimal(a["low"]) for a, b in zip(troughs, troughs[1:], strict=False)
    )
    unit = Decimal("0.0001") if snapshot["symbol"] == "EURUSD" else None
    close = Decimal(str(bars[-1]["close"]))
    fast = Decimal(str(snapshot["indicators"]["ema_fast"]))
    slow = Decimal(str(snapshot["indicators"]["ema_slow"]))
    result = {
        "id": "verified-facts",
        "bar_count": len(bars),
        "timeframes_supplied": [snapshot["timeframe"]]
        + [
            tf
            for tf, data in snapshot.get("trade_plans", {}).get("timeframes", {}).items()
            if tf in {"H1", "H4"} and data.get("status") == "available"
        ],
        "window_low": str(min(lows)),
        "window_high": str(max(highs)),
        "last_close": str(bars[-1]["close"]),
        "max_single_bar_range_price": str(max(ranges)),
        "recent_pivot_lows": troughs,
        "strictly_rising_recent_pivot_lows": rising,
        "strictly_falling_recent_pivot_lows": falling,
        "indicators": snapshot["indicators"],
        "close_vs_ema20": "above" if close > fast else "below" if close < fast else "equal",
        "close_vs_ema50": "above" if close > slow else "below" if close < slow else "equal",
        "macro_available": snapshot.get("macro_available", False),
        "news_scope": "Headlines only; no complete calendar, forecasts or surprise values",
    }
    if unit:
        result["pip_size"] = str(unit)
        result["max_single_bar_range_pips"] = str(max(ranges) / unit)
        result["atr_pips"] = str(Decimal(str(snapshot["indicators"]["atr"])) / unit)
    return result


def facts_summary(snapshot: dict, facts: dict) -> str:
    ind = facts["indicators"]

    def fmt(value: object) -> str:
        return format(Decimal(str(value)), ".5f")

    summary = (
        f"Verified by code: {facts['bar_count']} closed {snapshot['timeframe']} candles; "
        f"window low {facts['window_low']}, high {facts['window_high']}; "
        f"last close {facts['last_close']}; "
        f"EMA20 {fmt(ind['ema_fast'])}, EMA50 {fmt(ind['ema_slow'])}, "
        f"RSI14 {Decimal(str(ind['rsi'])):.2f}, ATR14 {fmt(ind['atr'])}. "
        f"Last close is {facts['close_vs_ema20']} EMA20 and {facts['close_vs_ema50']} EMA50. "
    )
    if "max_single_bar_range_pips" in facts:
        summary += (
            f"Largest single candle {Decimal(facts['max_single_bar_range_pips']):.2f} pips; "
            f"ATR {Decimal(facts['atr_pips']):.2f} pips. "
        )
    summary += (
        "Recent pivot lows: "
        + (
            "strictly rising"
            if facts["strictly_rising_recent_pivot_lows"]
            else "strictly falling"
            if facts["strictly_falling_recent_pivot_lows"]
            else "mixed or insufficient"
        )
        + ". Verified timeframes: "
        + ", ".join(facts["timeframes_supplied"])
        + ". News is headline-only; macro surprises unavailable."
    )
    return summary


def validate_prose(output: dict, facts: dict, ids: set[str]) -> None:
    text = " ".join(
        [str(output.get("summary", output.get("reason", ""))), *output.get("limitations", [])]
    )
    for identifier in sorted(ids, key=len, reverse=True):
        text = text.replace(identifier, "evidence")
    # Standard indicator/timeframe labels are identifiers, not measurements.
    text = re.sub(r"\b(?:EMA|RSI|ATR|M|H|D)\d+\b", "indicator", text, flags=re.I)
    if re.search(r"\d", text):
        raise ResearchQualityError("Numerical claims must come from verified facts")
    if "D1" not in facts["timeframes_supplied"] and re.search(
        r"\bdaily\s+(?:trend|structure|bias)", text, re.I
    ):
        raise ResearchQualityError("Unsupported daily timeframe claim")
    if len(facts["timeframes_supplied"]) == 1 and re.search(
        r"\b(?:higher[- ]timeframe|daily|hourly)\s+(?:trend|structure|bias)", text, re.I
    ):
        raise ResearchQualityError("Unsupported timeframe claim")
    # Advocates may discuss uncertainty, but cannot assert a pattern which the
    # explicitly defined local-pivot calculation does not confirm.
    if not facts["strictly_rising_recent_pivot_lows"] and re.search(
        r"\b(?:progressively higher lows|sequence of higher lows|higher[- ]low structure)\b",
        text,
        re.I,
    ):
        raise ResearchQualityError("Unverified higher-low pattern")
