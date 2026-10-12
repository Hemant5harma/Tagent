"""Optional public-source context; absence never masquerades as a clean event calendar."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

FEEDS = {
    "fed": "https://www.federalreserve.gov/feeds/press_all.xml",
    "ecb": "https://www.ecb.europa.eu/rss/press.html",
    "bls": "https://www.bls.gov/feed/bls_latest.rss",
    "bea": "https://apps.bea.gov/rss/rss.xml",
}


class FreeContextCollector:
    def __init__(self, enabled: bool = False, fred_api_key: str | None = None) -> None:
        self.enabled = enabled
        self.fred_api_key = fred_api_key

    async def collect(self) -> dict:
        now = datetime.now(UTC)
        result: dict = {"as_of": now.isoformat(), "historical_replay_safe": False,
                        "event_calendar_complete": False, "sources": {}, "evidence": []}
        if not self.enabled:
            result["sources"] = {name: {"status": "disabled"} for name in FEEDS}
            result["sources"]["fred"] = {"status": "disabled"}
            return result
        async with httpx.AsyncClient(timeout=8, follow_redirects=True,
                                     headers={"User-Agent": "TradeAgent/0.1 public-context"}) as client:
            async def feed(name: str, url: str) -> tuple[str, dict, list]:
                evidence = []
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    if len(response.content) > 2_000_000:
                        raise ValueError("feed exceeds size limit")
                    root = ElementTree.fromstring(response.content)
                    for index, item in enumerate(root.findall(".//item")[:10]):
                        published = item.findtext("pubDate")
                        if not published:
                            continue
                        stamp = parsedate_to_datetime(published)
                        if stamp.utcoffset() is None or stamp > now:
                            continue
                        evidence.append({"id": f"{name}-{index}", "source": url,
                                         "observed_at": stamp.isoformat(),
                                         "retrieved_at": now.isoformat(),
                                         "text": (item.findtext("title") or "")[:1000],
                                         "url": item.findtext("link")})
                    return name, {"status": "available", "items": len(evidence)}, evidence
                except (httpx.HTTPError, ElementTree.ParseError, ValueError, TypeError):
                    return name, {"status": "unavailable", "reason": "feed fetch or parsing failed"}, []
            fetched = await asyncio.gather(*(feed(name, url) for name, url in FEEDS.items()))
            for name, state, evidence in fetched:
                result["sources"][name] = state
                result["evidence"].extend(evidence)
            result["sources"]["fred"] = {"status": "unavailable", "reason": "API key not configured"}
            if self.fred_api_key:
                try:
                    response = await client.get("https://api.stlouisfed.org/fred/series/observations",
                                                params={"api_key": self.fred_api_key,
                                                        "series_id": "DFF", "file_type": "json",
                                                        "sort_order": "desc", "limit": 5})
                    response.raise_for_status()
                    observations = response.json()["observations"]
                    result["sources"]["fred"] = {"status": "available", "series": "DFF"}
                    result["evidence"].append({"id": "fred-DFF", "source": "FRED DFF",
                                               "observed_at": now.isoformat(),
                                               "text": f"Current-vintage effective federal funds rate observations: {observations}. Not point-in-time historical evidence."})
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    # Never include exception URLs: they may contain the API key.
                    result["sources"]["fred"] = {"status": "unavailable", "reason": "FRED fetch failed"}
        return result
