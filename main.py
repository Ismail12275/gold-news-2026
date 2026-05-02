"""
XAUUSD / USD Macro News Bot — Main Orchestrator
Runs every 10-15 minutes, enforces all filtering logic.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from news_fetcher import fetch_news
from analyzer import analyze_news
from deduplicator import Deduplicator
from telegram_sender import TelegramSender

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", 600))   # 10 min default
MAX_MESSAGES_PER_HOUR = int(os.getenv("MAX_MSG_PER_HOUR", 5))

# ── Scoring ─────────────────────────────────────────────────────────────────
KEYWORD_SCORES: dict[str, int] = {
    # Fed / interest rates → +3
    "federal reserve": 3,
    "fed rate": 3,
    "interest rate": 3,
    "rate decision": 3,
    "fomc": 3,
    "ecb rate": 3,
    "rate hike": 3,
    "rate cut": 3,
    "powell": 2,
    "lagarde": 2,
    "central bank": 2,
    # Inflation → +2
    "cpi": 2,
    "ppi": 2,
    "inflation": 2,
    "consumer price": 2,
    "producer price": 2,
    # NFP → +3
    "non-farm": 3,
    "nonfarm": 3,
    "nfp": 3,
    "payroll": 3,
    "jobs report": 3,
    "unemployment": 2,
    # Geopolitics → +2
    "war": 2,
    "conflict": 2,
    "sanctions": 2,
    "geopolitical": 2,
    "crisis": 2,
    "invasion": 2,
    "nuclear": 3,
    # Oil → +2
    "oil shock": 2,
    "opec": 2,
    "crude oil": 2,
    "petroleum": 1,
}

MIN_SCORE = 3  # articles below this are discarded before AI analysis


def score_article(title: str, summary: str) -> int:
    """Return keyword-based importance score."""
    text = (title + " " + summary).lower()
    total = 0
    matched = []
    for kw, pts in KEYWORD_SCORES.items():
        if kw in text:
            total += pts
            matched.append(kw)
    if matched:
        log.debug("Score %d — keywords: %s", total, matched)
    return total


class HourlyRateLimiter:
    """Tracks how many messages were sent in the current clock-hour."""

    def __init__(self, max_per_hour: int):
        self.max = max_per_hour
        self._bucket: list[float] = []

    def can_send(self) -> bool:
        now = time.time()
        self._bucket = [t for t in self._bucket if now - t < 3600]
        return len(self._bucket) < self.max

    def record(self):
        self._bucket.append(time.time())


async def run_cycle(
    dedup: Deduplicator,
    sender: TelegramSender,
    limiter: HourlyRateLimiter,
) -> int:
    """One fetch-analyse-send cycle. Returns number of messages sent."""
    log.info("── Starting news cycle at %s", datetime.now(timezone.utc).isoformat())
    articles = await fetch_news()
    log.info("Fetched %d raw articles", len(articles))

    sent = 0
    for article in articles:
        if not limiter.can_send():
            log.warning("Hourly cap reached (%d msgs). Skipping remainder.", limiter.max)
            break

        title   = article.get("title", "").strip()
        summary = article.get("summary", "").strip()
        url     = article.get("url", "")

        if not title:
            continue

        # ── 1. Pre-filter by keyword score ──────────────────────────────
        score = score_article(title, summary)
        if score < MIN_SCORE:
            log.debug("SKIP (score %d < %d): %s", score, MIN_SCORE, title[:80])
            continue

        # ── 2. Deduplication ────────────────────────────────────────────
        if dedup.is_duplicate(title, summary):
            log.debug("SKIP (duplicate): %s", title[:80])
            continue

        # ── 3. AI Analysis ──────────────────────────────────────────────
        log.info("Analysing [score=%d]: %s", score, title[:80])
        try:
            analysis = await analyze_news(title, summary)
        except Exception as exc:
            log.error("AI analysis failed for '%s': %s", title[:60], exc)
            continue

        # ── 4. Final gate: Strength=High OR Tradable=Yes ─────────────────
        strength  = analysis.get("strength", "").lower()
        tradable  = analysis.get("tradable", "").lower()

        if strength != "high" and tradable != "yes":
            log.info(
                "SKIP (strength=%s, tradable=%s): %s",
                strength, tradable, title[:60],
            )
            continue

        # ── 5. Send ──────────────────────────────────────────────────────
        try:
            await sender.send(article, analysis, score)
            dedup.mark_sent(title, summary)
            limiter.record()
            sent += 1
            log.info("✅ Sent: %s", title[:80])
        except Exception as exc:
            log.error("Failed to send message: %s", exc)

    log.info("── Cycle complete. %d message(s) sent.", sent)
    return sent


ONE_SHOT = os.getenv("ONE_SHOT", "0") == "1"   # GitHub Actions mode


async def main():
    log.info("🤖 XAUUSD/USD Macro Bot starting… (one_shot=%s)", ONE_SHOT)
    dedup   = Deduplicator()
    sender  = TelegramSender()
    limiter = HourlyRateLimiter(MAX_MESSAGES_PER_HOUR)

    # Send startup ping only on first ever run (storage is empty)
    if dedup.count() == 0 and not ONE_SHOT:
        await sender.send_startup_message()

    if ONE_SHOT:
        # GitHub Actions: one cycle then exit
        try:
            await run_cycle(dedup, sender, limiter)
        except Exception as exc:
            log.error("Unhandled error: %s", exc, exc_info=True)
        return

    # Railway / server: infinite loop
    while True:
        try:
            await run_cycle(dedup, sender, limiter)
        except Exception as exc:
            log.error("Unhandled error in main cycle: %s", exc, exc_info=True)

        next_run = datetime.now(timezone.utc).strftime("%H:%M:%S")
        log.info("💤 Sleeping %ds … (next run after %s)", POLL_INTERVAL_SECONDS, next_run)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
