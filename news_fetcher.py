"""
news_fetcher.py — Multi-source financial news aggregator
Sources: Reuters RSS, MarketWatch RSS, Investing.com RSS, NewsAPI (optional)
All sources are free / no-auth by default; NewsAPI uses an env key.
"""

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import feedparser  # type: ignore

log = logging.getLogger(__name__)

# ── RSS Feed URLs ────────────────────────────────────────────────────────────
RSS_FEEDS: list[dict[str, str]] = [
    {
        "name": "Reuters - Top News",
        "url": "https://feeds.reuters.com/reuters/topNews",
    },
    {
        "name": "Reuters - Business",
        "url": "https://feeds.reuters.com/reuters/businessNews",
    },
    {
        "name": "MarketWatch - Economy",
        "url": "https://feeds.marketwatch.com/marketwatch/economy-politics/",
    },
    {
        "name": "Investing.com - Commodities",
        "url": "https://www.investing.com/rss/news_14.rss",
    },
    {
        "name": "Investing.com - Forex",
        "url": "https://www.investing.com/rss/news_1.rss",
    },
    {
        "name": "FXStreet",
        "url": "https://www.fxstreet.com/rss",
    },
    {
        "name": "ForexLive",
        "url": "https://www.forexlive.com/feed/news",
    },
    {
        "name": "CNBC - Economy",
        "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    },
    {
        "name": "CNBC - Markets",
        "url": "https://www.cnbc.com/id/15839069/device/rss/rss.html",
    },
    {
        "name": "Bloomberg - Economics (public)",
        "url": "https://feeds.bloomberg.com/economics/news.rss",
    },
]

# Optional: NewsAPI key from env
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
NEWSAPI_URL = "https://newsapi.org/v2/everything"
NEWSAPI_QUERY = (
    "gold OR XAUUSD OR \"federal reserve\" OR \"interest rate\" OR "
    "inflation OR CPI OR NFP OR \"non-farm\" OR FOMC OR ECB OR geopolitical"
)

# Max article age to consider (seconds)
MAX_AGE_SECONDS = 3 * 3600  # 3 hours


@dataclass
class Article:
    title: str
    summary: str
    url: str
    source: str
    published: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title":     self.title,
            "summary":   self.summary,
            "url":       self.url,
            "source":    self.source,
            "published": self.published,
        }


def _clean(text: str) -> str:
    """Strip HTML tags and excessive whitespace."""
    import re
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1000]


def _parse_rss_feed(raw: str, source_name: str) -> list[Article]:
    """Parse a raw RSS/Atom feed string into Article objects."""
    feed = feedparser.parse(raw)
    articles: list[Article] = []
    now = time.time()

    for entry in feed.entries:
        title   = _clean(entry.get("title", ""))
        summary = _clean(entry.get("summary", entry.get("description", "")))
        url     = entry.get("link", "")

        # Parse publish time
        pub_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if pub_parsed:
            import calendar
            pub_ts = float(calendar.timegm(pub_parsed))
        else:
            pub_ts = now  # assume fresh if no date

        # Drop very old articles
        if now - pub_ts > MAX_AGE_SECONDS:
            continue

        if title:
            articles.append(
                Article(
                    title=title,
                    summary=summary,
                    url=url,
                    source=source_name,
                    published=pub_ts,
                )
            )

    return articles


async def _fetch_rss(
    session: aiohttp.ClientSession,
    feed: dict[str, str],
) -> list[Article]:
    """Fetch and parse a single RSS feed."""
    name = feed["name"]
    url  = feed["url"]
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                log.warning("RSS %s returned HTTP %d", name, resp.status)
                return []
            raw = await resp.text(errors="replace")
            articles = _parse_rss_feed(raw, name)
            log.debug("RSS %-30s → %d articles", name, len(articles))
            return articles
    except asyncio.TimeoutError:
        log.warning("Timeout fetching RSS: %s", name)
    except Exception as exc:
        log.warning("Error fetching RSS %s: %s", name, exc)
    return []


async def _fetch_newsapi(session: aiohttp.ClientSession) -> list[Article]:
    """Fetch from NewsAPI if a key is configured."""
    if not NEWSAPI_KEY:
        return []

    params = {
        "q":        NEWSAPI_QUERY,
        "language": "en",
        "sortBy":   "publishedAt",
        "pageSize": "30",
        "apiKey":   NEWSAPI_KEY,
    }
    try:
        async with session.get(
            NEWSAPI_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                log.warning("NewsAPI returned HTTP %d", resp.status)
                return []
            data = await resp.json()
            articles = []
            now = time.time()
            for item in data.get("articles", []):
                title   = _clean(item.get("title", ""))
                summary = _clean(item.get("description", ""))
                url     = item.get("url", "")
                source  = item.get("source", {}).get("name", "NewsAPI")
                pub_str = item.get("publishedAt", "")

                # Parse ISO-8601
                pub_ts = now
                if pub_str:
                    try:
                        from datetime import datetime, timezone
                        pub_ts = datetime.fromisoformat(
                            pub_str.replace("Z", "+00:00")
                        ).timestamp()
                    except ValueError:
                        pass

                if now - pub_ts > MAX_AGE_SECONDS:
                    continue

                if title:
                    articles.append(
                        Article(
                            title=title,
                            summary=summary,
                            url=url,
                            source=source,
                            published=pub_ts,
                        )
                    )
            log.debug("NewsAPI → %d articles", len(articles))
            return articles
    except Exception as exc:
        log.warning("NewsAPI error: %s", exc)
    return []


def _deduplicate_by_hash(articles: list[Article]) -> list[Article]:
    """Remove within-batch duplicates by title fingerprint."""
    seen: set[str] = set()
    unique: list[Article] = []
    for a in articles:
        key = hashlib.md5(a.title.lower().encode()).hexdigest()
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique


async def fetch_news() -> list[dict[str, Any]]:
    """
    Fetch news from all configured sources concurrently.
    Returns a list of article dicts sorted newest-first.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; GoldNewsBot/1.0; +https://github.com/bot)"
        )
    }
    connector = aiohttp.TCPConnector(limit=10, ssl=False)

    async with aiohttp.ClientSession(
        headers=headers, connector=connector
    ) as session:
        tasks = [_fetch_rss(session, feed) for feed in RSS_FEEDS]
        tasks.append(_fetch_newsapi(session))

        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_articles: list[Article] = []
    for result in results:
        if isinstance(result, list):
            all_articles.extend(result)
        elif isinstance(result, Exception):
            log.error("Fetch task error: %s", result)

    # Deduplicate within this batch & sort newest-first
    unique = _deduplicate_by_hash(all_articles)
    unique.sort(key=lambda a: a.published, reverse=True)

    log.info("Total unique articles fetched: %d", len(unique))
    return [a.to_dict() for a in unique]
