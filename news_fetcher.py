"""
news_fetcher.py — Professional multi-source financial news aggregator
Optimized for Railway / Cloud deployment
Free sources only + optional NewsAPI
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

# ── Stable RSS Sources for Cloud Deployment ────────────────────────────────
RSS_FEEDS: list[dict[str, str]] = [
    {
        "name": "Yahoo Finance",
        "url": "https://finance.yahoo.com/news/rssindex",
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
        "name": "Federal Reserve",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
    },
    {
        "name": "ECB Press",
        "url": "https://www.ecb.europa.eu/rss/press.html",
    },
    {
        "name": "IMF News",
        "url": "https://www.imf.org/en/News/rss",
    },
    {
        "name": "ForexLive",
        "url": "https://www.forexlive.com/feed/news",
    },
    {
        "name": "Investing.com - Commodities",
        "url": "https://www.investing.com/rss/news_14.rss",
    },
    {
        "name": "Investing.com - Forex",
        "url": "https://www.investing.com/rss/news_1.rss",
    },
]

# ── NewsAPI Optional ───────────────────────────────────────────────────────
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
NEWSAPI_URL = "https://newsapi.org/v2/everything"

NEWSAPI_QUERY = (
    "gold OR XAUUSD OR USD OR dollar OR "
    "\"federal reserve\" OR FOMC OR ECB OR inflation OR CPI OR PPI OR "
    "NFP OR payrolls OR \"interest rates\" OR recession OR oil OR geopolitics"
)

# ── Filters ────────────────────────────────────────────────────────────────
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
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "source": self.source,
            "published": self.published,
        }


# ── Helpers ────────────────────────────────────────────────────────────────
def _clean(text: str) -> str:
    """Remove HTML and normalize whitespace."""
    import re

    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1500]


def _parse_rss_feed(raw: str, source_name: str) -> list[Article]:
    """Parse RSS/Atom feed into structured articles."""
    feed = feedparser.parse(raw)
    articles: list[Article] = []
    now = time.time()

    for entry in feed.entries:
        title = _clean(entry.get("title", ""))
        summary = _clean(
            entry.get("summary", entry.get("description", ""))
        )
        url = entry.get("link", "")

        pub_parsed = entry.get("published_parsed") or entry.get(
            "updated_parsed"
        )

        if pub_parsed:
            import calendar

            pub_ts = float(calendar.timegm(pub_parsed))
        else:
            pub_ts = now

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


# ── RSS Fetch ──────────────────────────────────────────────────────────────
async def _fetch_rss(
    session: aiohttp.ClientSession,
    feed: dict[str, str],
) -> list[Article]:
    name = feed["name"]
    url = feed["url"]

    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=25),
        ) as resp:
            if resp.status != 200:
                log.warning("RSS %s returned HTTP %d", name, resp.status)
                return []

            raw = await resp.text(errors="replace")
            articles = _parse_rss_feed(raw, name)

            log.info("RSS %-25s → %d articles", name, len(articles))
            return articles

    except asyncio.TimeoutError:
        log.warning("Timeout fetching RSS: %s", name)

    except Exception as exc:
        log.warning("Error fetching RSS %s: %s", name, exc)

    return []


# ── NewsAPI Fetch ──────────────────────────────────────────────────────────
async def _fetch_newsapi(
    session: aiohttp.ClientSession,
) -> list[Article]:
    if not NEWSAPI_KEY:
        return []

    params = {
        "q": NEWSAPI_QUERY,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": "30",
        "apiKey": NEWSAPI_KEY,
    }

    try:
        async with session.get(
            NEWSAPI_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=25),
        ) as resp:
            if resp.status != 200:
                log.warning("NewsAPI returned HTTP %d", resp.status)
                return []

            data = await resp.json()
            articles: list[Article] = []
            now = time.time()

            for item in data.get("articles", []):
                title = _clean(item.get("title", ""))
                summary = _clean(item.get("description", ""))
                url = item.get("url", "")
                source = item.get("source", {}).get("name", "NewsAPI")
                pub_str = item.get("publishedAt", "")

                pub_ts = now
                if pub_str:
                    try:
                        from datetime import datetime

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

            log.info("NewsAPI → %d articles", len(articles))
            return articles

    except Exception as exc:
        log.warning("NewsAPI error: %s", exc)

    return []


# ── Deduplication ──────────────────────────────────────────────────────────
def _deduplicate_by_hash(
    articles: list[Article],
) -> list[Article]:
    seen: set[str] = set()
    unique: list[Article] = []

    for article in articles:
        fingerprint = hashlib.md5(
            article.title.lower().encode()
        ).hexdigest()

        if fingerprint not in seen:
            seen.add(fingerprint)
            unique.append(article)

    return unique


# ── Main Public Function ───────────────────────────────────────────────────
async def fetch_news() -> list[dict[str, Any]]:
    """
    Fetch from all sources concurrently.
    Returns sorted list of unique article dictionaries.
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }

    connector = aiohttp.TCPConnector(
        limit=15,
        ssl=False,
    )

    async with aiohttp.ClientSession(
        headers=headers,
        connector=connector,
    ) as session:
        tasks = [_fetch_rss(session, feed) for feed in RSS_FEEDS]

        if NEWSAPI_KEY:
            tasks.append(_fetch_newsapi(session))

        results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

    all_articles: list[Article] = []

    for result in results:
        if isinstance(result, list):
            all_articles.extend(result)

        elif isinstance(result, Exception):
            log.error("Fetch task error: %s", result)

    unique_articles = _deduplicate_by_hash(all_articles)

    unique_articles.sort(
        key=lambda x: x.published,
        reverse=True,
    )

    log.info(
        "Total unique articles fetched: %d",
        len(unique_articles),
    )

    return [article.to_dict() for article in unique_articles]
