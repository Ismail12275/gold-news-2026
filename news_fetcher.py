"""
news_fetcher.py — News ingestion with macro relevance pre-filtering
Fetches, scores relevance, and normalizes news items before analysis
"""

import os
import re
import feedparser
import requests
from datetime import datetime, timezone
from typing import Optional

# ─────────────────────────────────────────────
# NEWS SOURCES
# ─────────────────────────────────────────────
RSS_FEEDS = {
    "ForexFactory": "https://www.forexfactory.com/news?rss",
    "Reuters_Markets": "https://feeds.reuters.com/reuters/businessNews",
    "Bloomberg_Economics": "https://feeds.bloomberg.com/markets/news.rss",
    "FXStreet": "https://www.fxstreet.com/rss/news",
    "Investing_com": "https://www.investing.com/rss/news.rss",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
}

# ─────────────────────────────────────────────
# RELEVANCE KEYWORD TIERS
# ─────────────────────────────────────────────

# Tier 1: Always fetch — highest macro impact
HIGH_PRIORITY_KEYWORDS = [
    "fed", "federal reserve", "fomc", "powell", "cpi", "nfp", "non-farm",
    "inflation", "interest rate", "rate decision", "rate hike", "rate cut",
    "treasury yield", "jobs report", "unemployment", "pce", "payroll",
    "gdp", "monetary policy", "balance sheet", "quantitative",
    "gold", "xauusd", "safe haven", "central bank",
]

# Tier 2: Fetch if recent / high source weight
MODERATE_KEYWORDS = [
    "dollar", "usd", "dxy", "ism", "pmi", "retail sales", "housing",
    "consumer confidence", "jolts", "adp", "waller", "goolsbee", "jefferson",
    "williams", "daly", "kashkari", "mester", "barkin", "bostic",
    "geopolit", "ukraine", "middle east", "oil", "recession", "debt",
    "yield curve", "spread", "bond", "risk off", "risk on",
]

# Tier 0: Never send — noise
BLACKLIST_KEYWORDS = [
    "crypto", "bitcoin", "ethereum", "nft", "stock split",
    "earnings", "dividend", "acquisition", "merger", "ipo",
    "celebrity", "sports", "weather", "lifestyle",
]

# Category inference map
CATEGORY_MAP = {
    "Federal Reserve": ["fed", "federal reserve", "fomc", "powell", "waller", "goolsbee",
                        "jefferson", "williams", "daly", "kashkari", "mester", "barkin", "bostic"],
    "Economic Data": ["cpi", "nfp", "pce", "gdp", "ism", "pmi", "retail sales", "housing",
                      "jobs", "unemployment", "payroll", "adp", "jolts", "consumer confidence",
                      "inflation", "non-farm"],
    "Treasury & Yields": ["treasury", "yield", "bond", "10-year", "2-year", "yield curve",
                           "t-note", "t-bill", "spread"],
    "Geopolitical": ["geopolit", "ukraine", "russia", "middle east", "china", "taiwan",
                     "war", "conflict", "sanction"],
    "Gold & Commodities": ["gold", "xauusd", "silver", "oil", "commodity", "safe haven"],
    "Central Bank": ["ecb", "boe", "boj", "rba", "snb", "rbnz", "central bank",
                     "lagarde", "bailey", "ueda", "kuroda"],
}


# ─────────────────────────────────────────────
# NORMALIZER
# ─────────────────────────────────────────────
def _normalize_item(entry: dict, source_name: str) -> dict:
    """Convert a feedparser entry to a standard news_item dict."""

    title = entry.get("title", "").strip()
    summary = entry.get("summary", entry.get("description", "")).strip()
    summary = re.sub(r"<[^>]+>", "", summary)  # strip HTML tags

    # Parse published date
    published_at = datetime.now(timezone.utc).isoformat()
    if entry.get("published_parsed"):
        try:
            import calendar
            ts = calendar.timegm(entry.published_parsed)
            published_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            pass

    link = entry.get("link", "")

    return {
        "title": title,
        "summary": summary[:1000],  # Cap summary length
        "source": source_name,
        "published_at": published_at,
        "link": link,
        "category": _infer_category(title + " " + summary),
        "_priority": _score_priority(title + " " + summary),
    }


def _infer_category(text: str) -> str:
    text_lower = text.lower()
    for category, keywords in CATEGORY_MAP.items():
        if any(kw in text_lower for kw in keywords):
            return category
    return "Market News"


def _score_priority(text: str) -> int:
    """Return 2 (high), 1 (moderate), 0 (skip)."""
    text_lower = text.lower()

    if any(kw in text_lower for kw in BLACKLIST_KEYWORDS):
        return 0

    if any(kw in text_lower for kw in HIGH_PRIORITY_KEYWORDS):
        return 2

    if any(kw in text_lower for kw in MODERATE_KEYWORDS):
        return 1

    return 0


# ─────────────────────────────────────────────
# FETCHERS
# ─────────────────────────────────────────────
def fetch_rss_feed(url: str, source_name: str, min_priority: int = 1) -> list[dict]:
    """Fetch and filter a single RSS feed."""
    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries:
            item = _normalize_item(entry, source_name)
            if item["_priority"] >= min_priority:
                items.append(item)
        return items
    except Exception as e:
        print(f"[FETCHER] Error fetching {source_name}: {e}")
        return []


def fetch_all_feeds(min_priority: int = 1) -> list[dict]:
    """
    Fetch all configured RSS feeds and return merged, priority-filtered list.
    Sorted by priority then published time (newest first).
    """
    all_items = []
    for name, url in RSS_FEEDS.items():
        items = fetch_rss_feed(url, name, min_priority)
        all_items.extend(items)
        print(f"[FETCHER] {name}: {len(items)} relevant items")

    # Sort: high priority first, then newest
    all_items.sort(key=lambda x: (x["_priority"], x["published_at"]), reverse=True)
    return all_items


def fetch_finnhub_news(api_key: Optional[str] = None) -> list[dict]:
    """
    Fetch forex/macro news from Finnhub as alternative source.
    Requires FINNHUB_API_KEY env var.
    """
    key = api_key or os.environ.get("FINNHUB_API_KEY", "")
    if not key:
        return []

    try:
        url = "https://finnhub.io/api/v1/news"
        params = {"category": "forex", "token": key}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        items = []
        for entry in data:
            title = entry.get("headline", "")
            summary = entry.get("summary", "")
            ts = entry.get("datetime", 0)
            published_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else datetime.now(timezone.utc).isoformat()

            item = {
                "title": title,
                "summary": summary[:1000],
                "source": entry.get("source", "Finnhub"),
                "published_at": published_at,
                "link": entry.get("url", ""),
                "category": _infer_category(title + " " + summary),
                "_priority": _score_priority(title + " " + summary),
            }

            if item["_priority"] >= 1:
                items.append(item)

        return items
    except Exception as e:
        print(f"[FETCHER] Finnhub error: {e}")
        return []
