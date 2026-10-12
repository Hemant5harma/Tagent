"""Closed broker candles and deterministic technical evidence (no model arithmetic)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from trade_agent.broker import BrokerAdapter, FakeBroker

TIMEFRAME_SECONDS = {"M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


class MarketDataError(ValueError):
    """Required evidence is missing, stale, or inconsistent: abstain."""

    def __init__(self, message, *, code="invalid_market_data", quote_at=None):
        super().__init__(message)
        self.code, self.quote_at = code, quote_at


def _number(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MarketDataError("invalid candle number") from exc
    if not result.is_finite() or result <= 0:
        raise MarketDataError("candle prices must be finite and positive")
    return result


def _time(value: object) -> datetime:
    try:
        result = (
            datetime.fromtimestamp(value, UTC)
            if isinstance(value, (int, float))
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
        if result.utcoffset() is None:
            raise ValueError("naive time")
        return result.astimezone(UTC)
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        raise MarketDataError("candle timestamps must be valid UTC instants") from exc


def _ema(values: list[Decimal], period: int) -> Decimal:
    result = sum(values[:period], Decimal(0)) / period
    alpha = Decimal(2) / (period + 1)
    for value in values[period:]:
        result += alpha * (value - result)
    return result


def indicators(bars: list[dict]) -> dict[str, str]:
    closes = [Decimal(bar["close"]) for bar in bars]
    differences = [right - left for left, right in zip(closes, closes[1:], strict=False)]
    gain = sum((max(change, Decimal(0)) for change in differences[:14]), Decimal(0)) / 14
    loss = sum((max(-change, Decimal(0)) for change in differences[:14]), Decimal(0)) / 14
    for change in differences[14:]:
        gain = (gain * 13 + max(change, Decimal(0))) / 14
        loss = (loss * 13 + max(-change, Decimal(0))) / 14
    rsi = (
        Decimal(50)
        if gain == loss == 0
        else Decimal(100)
        if loss == 0
        else Decimal(100) - Decimal(100) / (1 + gain / loss)
    )
    ranges = []
    for index, bar in enumerate(bars):
        high, low = Decimal(bar["high"]), Decimal(bar["low"])
        prior = closes[max(0, index - 1)]
        ranges.append(max(high - low, abs(high - prior), abs(low - prior)))
    atr = sum(ranges[:14], Decimal(0)) / 14
    for value in ranges[14:]:
        atr = (atr * 13 + value) / 14
    if atr <= 0:
        raise MarketDataError("ATR must be positive")
    return {
        "ema_fast": str(_ema(closes, 20)),
        "ema_slow": str(_ema(closes, 50)),
        "rsi": str(rsi),
        "atr": str(atr),
    }


class MarketDataCollector:
    def __init__(self, broker: BrokerAdapter, max_quote_age_seconds: float = 15) -> None:
        self.broker = broker
        self.max_quote_age_seconds = max_quote_age_seconds

    def collect(self, symbol: str, timeframe: str = "M15", count: int = 200) -> dict:
        if timeframe not in TIMEFRAME_SECONDS or not 50 <= count <= 1000:
            raise MarketDataError("unsupported timeframe or candle count")
        now = datetime.now(UTC)
        interval = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
        quote = self.broker.quote(symbol)
        now = datetime.now(UTC)
        quoted_at = _time(quote.observed_at.isoformat())
        quote_age = (now - quoted_at).total_seconds()
        if quote_age < -5 or quote_age > self.max_quote_age_seconds:
            raise MarketDataError(
                "quote timestamp is stale or in the future",
                code="stale_quote" if quote_age > self.max_quote_age_seconds else "future_quote",
                quote_at=quoted_at.isoformat(),
            )
        synthetic = isinstance(self.broker, FakeBroker)
        if synthetic:
            # Explicit test-only fixture: never presented as actual market history.
            boundary = (
                int(now.timestamp()) // TIMEFRAME_SECONDS[timeframe] * TIMEFRAME_SECONDS[timeframe]
            )
            base = quote.bid
            step = base / Decimal(10000)
            raw: list[dict] = []
            for index in range(count):
                opened = datetime.fromtimestamp(boundary, UTC) - interval * (count - index)
                close = base + step * Decimal((index % 11) - 5)
                raw.append(
                    {
                        "time": opened.isoformat(),
                        "open": str(close),
                        "high": str(close + step),
                        "low": str(close - step),
                        "close": str(close),
                        "tick_volume": 100,
                    }
                )
        else:
            raw = self.broker.bars(symbol, timeframe, count)
        if not 50 <= len(raw) <= count:
            raise MarketDataError("insufficient or excessive closed candle coverage")
        normalized = []
        previous = None
        for bar in raw:
            if not isinstance(bar, dict):
                raise MarketDataError("candle record must be an object")
            opened = _time(bar.get("time"))
            closed = opened + interval
            if closed > now or (previous is not None and opened <= previous):
                raise MarketDataError("candles must be strictly chronological and closed")
            if bar.get("closed_at") is not None and _time(bar["closed_at"]) != closed:
                raise MarketDataError("candle close time does not match timeframe")
            values = {key: _number(bar.get(key)) for key in ("open", "high", "low", "close")}
            if not (
                values["low"]
                <= min(values["open"], values["close"])
                <= max(values["open"], values["close"])
                <= values["high"]
            ):
                raise MarketDataError("inconsistent OHLC candle")
            try:
                volume = int(bar.get("tick_volume", 0))
            except (TypeError, ValueError, OverflowError) as exc:
                raise MarketDataError("invalid candle tick volume") from exc
            if volume < 0:
                raise MarketDataError("negative candle tick volume")
            normalized.append(
                {
                    "time": opened.isoformat(),
                    "closed_at": closed.isoformat(),
                    **{key: str(value) for key, value in values.items()},
                    "tick_volume": volume,
                }
            )
            previous = opened
        latest = _time(normalized[-1]["closed_at"])
        if now - latest > interval * 2:
            raise MarketDataError("latest closed candle is stale")
        calculated = indicators(normalized)
        source = "synthetic-test-fixture" if synthetic else "MT5-broker-candles"
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "as_of": now.isoformat(),
            "closed_bar_at": latest.isoformat(),
            "bars": normalized,
            "indicators": calculated,
            "data_mode": "synthetic" if synthetic else "broker",
            "quote": {
                "bid": str(quote.bid),
                "ask": str(quote.ask),
                "observed_at": quote.observed_at.isoformat(),
            },
            "evidence": [
                {
                    "id": "price",
                    "source": "fake" if synthetic else "mt5",
                    "observed_at": quote.observed_at.isoformat(),
                    "text": f"Current bid {quote.bid}, ask {quote.ask}; synthetic={synthetic}.",
                },
                {
                    "id": "technical-1",
                    "source": source,
                    "observed_at": latest.isoformat(),
                    "text": f"{len(normalized)} closed {timeframe} candles; EMA20/50 and Wilder RSI14/ATR14: {calculated}. Tick volume is broker tick volume.",
                },
            ],
        }
