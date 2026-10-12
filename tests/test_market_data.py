from datetime import UTC, datetime, timedelta

import pytest

from trade_agent.broker import FakeBroker, MT5HTTPBroker
from trade_agent.free_context import FreeContextCollector
from trade_agent.market_data import MarketDataCollector, MarketDataError


def test_fake_snapshot_is_explicitly_synthetic():
    snapshot = MarketDataCollector(FakeBroker()).collect("EURUSD")
    assert snapshot["data_mode"] == "synthetic"
    assert len(snapshot["bars"]) == 200
    assert float(snapshot["indicators"]["atr"]) > 0
    assert 0 <= float(snapshot["indicators"]["rsi"]) <= 100


@pytest.mark.parametrize("seconds", [-60, 60])
def test_bad_quote_timestamp_abstains(monkeypatch, seconds):
    broker = FakeBroker()
    quote = broker.quote("EURUSD").model_copy(
        update={"observed_at": datetime.now(UTC) + timedelta(seconds=seconds)}
    )
    monkeypatch.setattr(broker, "quote", lambda symbol: quote)
    with pytest.raises(MarketDataError, match="quote timestamp"):
        MarketDataCollector(broker).collect("EURUSD")


def _real_collector(monkeypatch):
    fake = FakeBroker()
    snapshot = MarketDataCollector(fake).collect("EURUSD")
    broker = MT5HTTPBroker("http://localhost", None)
    monkeypatch.setattr(broker, "quote", fake.quote)
    monkeypatch.setattr(broker, "bars", lambda *args: snapshot["bars"])
    return MarketDataCollector(broker), snapshot


@pytest.mark.parametrize("failure", ["duplicate", "future", "stale", "nan", "ohlc", "short"])
def test_invalid_history_abstains(monkeypatch, failure):
    collector, snapshot = _real_collector(monkeypatch)
    bars = snapshot["bars"]
    if failure == "duplicate":
        bars[-1] = dict(bars[-2])
    elif failure == "future":
        bars[-1]["time"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    elif failure == "stale":
        for bar in bars:
            for key in ("time", "closed_at"):
                bar[key] = (datetime.fromisoformat(bar[key]) - timedelta(days=2)).isoformat()
    elif failure == "nan":
        bars[-1]["close"] = "NaN"
    elif failure == "ohlc":
        bars[-1]["high"] = "0.1"
    else:
        del bars[40:]
    with pytest.raises(MarketDataError):
        collector.collect("EURUSD")


@pytest.mark.asyncio
async def test_free_context_disabled_never_fetches(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("unexpected external request")
    monkeypatch.setattr("httpx.AsyncClient.get", forbidden)
    result = await FreeContextCollector().collect()
    assert result["sources"]["fed"]["status"] == "disabled"
    assert result["event_calendar_complete"] is False


@pytest.mark.asyncio
async def test_context_failures_are_explicit_and_do_not_expose_key(monkeypatch):
    import httpx

    async def unavailable(*args, **kwargs):
        raise httpx.ConnectError("secret-key-sensitive-URL")

    monkeypatch.setattr("httpx.AsyncClient.get", unavailable)
    result = await FreeContextCollector(enabled=True, fred_api_key="secret-key").collect()
    assert all(source["status"] == "unavailable" for source in result["sources"].values())
    assert result["evidence"] == []
    assert "secret-key" not in str(result)


@pytest.mark.asyncio
async def test_official_feeds_skip_future_publications(monkeypatch):
    from email.utils import format_datetime

    import httpx

    async def response(*args, **kwargs):
        future = format_datetime(datetime.now(UTC) + timedelta(days=1))
        past = format_datetime(datetime.now(UTC) - timedelta(hours=1))
        xml = f"<rss><channel><item><title>Future</title><pubDate>{future}</pubDate></item><item><title>Current</title><pubDate>{past}</pubDate></item></channel></rss>"
        return httpx.Response(200, text=xml, request=httpx.Request("GET", "https://example.org"))

    monkeypatch.setattr("httpx.AsyncClient.get", response)
    result = await FreeContextCollector(enabled=True).collect()
    assert len(result["evidence"]) == 4
    assert all(item["text"] == "Current" for item in result["evidence"])
    assert result["historical_replay_safe"] is False
