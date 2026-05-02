"""
telegram_sender.py — Formats and sends high-impact news to Telegram
Supports single chat, multiple chat IDs, and Markdown V2 formatting.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")   # comma-separated for multiple

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Emoji maps
USD_EMOJI  = {"Bullish": "🟢", "Bearish": "🔴", "Neutral": "🟡"}
GOLD_EMOJI = {"Bullish": "⬆️", "Bearish": "⬇️", "Neutral": "➡️"}
STR_EMOJI  = {"High": "🔥", "Medium": "⚡", "Low": "📉"}
TRD_EMOJI  = {"Yes": "✅", "No": "❌"}

# Impact category → emoji tag
CATEGORY_MAP: list[tuple[list[str], str]] = [
    (["fomc", "federal reserve", "fed rate", "rate decision", "rate hike", "rate cut"], "🏦 Rate Decision"),
    (["cpi", "inflation", "ppi", "consumer price", "producer price"],                  "📊 Inflation Data"),
    (["non-farm", "nonfarm", "nfp", "payroll", "jobs report"],                         "💼 Jobs / NFP"),
    (["powell", "lagarde", "central bank", "speech", "testimony"],                     "🎙️ CB Speech"),
    (["war", "conflict", "invasion", "nuclear", "geopolit", "sanction", "crisis"],     "⚔️ Geopolitics"),
    (["oil", "opec", "crude", "petroleum"],                                             "🛢️ Oil Shock"),
]


def _detect_category(title: str, summary: str) -> str:
    text = (title + " " + summary).lower()
    for keywords, label in CATEGORY_MAP:
        if any(k in text for k in keywords):
            return label
    return "📰 Macro Event"


def _escape_md2(text: str) -> str:
    """Escape special chars for Telegram MarkdownV2."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


def _build_message(
    article: dict[str, Any],
    analysis: dict[str, Any],
    score: int,
) -> str:
    title    = article.get("title", "No title")
    url      = article.get("url", "")
    source   = article.get("source", "Unknown")

    usd      = analysis.get("usd_impact", "Neutral")
    gold     = analysis.get("gold_impact", "Neutral")
    strength = analysis.get("strength", "Low")
    tradable = analysis.get("tradable", "No")
    explain  = analysis.get("explanation", "")

    category = _detect_category(title, article.get("summary", ""))
    ts       = datetime.now(timezone.utc).strftime("%H:%M UTC")

    # ── Build plain-text version (HTML parse_mode) ──────────────────────────
    score_bar = "●" * min(score, 5) + "○" * max(0, 5 - score)

    lines = [
        f"🚨 <b>HIGH IMPACT NEWS</b>",
        f"",
        f"{category}",
        f"",
        f"📌 <b>Title:</b> {title}",
        f"",
        f"💵 <b>USD:</b> {USD_EMOJI.get(usd, '🟡')} {usd}",
        f"🥇 <b>Gold:</b> {GOLD_EMOJI.get(gold, '➡️')} {gold}",
        f"",
        f"{STR_EMOJI.get(strength, '📉')} <b>Strength:</b> {strength}",
        f"{TRD_EMOJI.get(tradable, '❌')} <b>Tradable:</b> {tradable}",
        f"",
        f"📝 <b>Summary:</b> <i>{explain}</i>",
        f"",
        f"⚡ <b>Score:</b> [{score_bar}] {score}/5+",
        f"🕐 {ts} | 📡 {source}",
    ]

    if url:
        lines.append(f"")
        lines.append(f'🔗 <a href="{url}">Read full article</a>')

    return "\n".join(lines)


async def _send_single(
    session: aiohttp.ClientSession,
    chat_id: str,
    text: str,
    retries: int = 3,
) -> bool:
    """Send a message to one chat_id with retry logic."""
    endpoint = f"{TELEGRAM_API}/sendMessage"
    payload  = {
        "chat_id":                  chat_id.strip(),
        "text":                     text,
        "parse_mode":               "HTML",
        "disable_web_page_preview": True,
    }

    for attempt in range(1, retries + 1):
        try:
            async with session.post(
                endpoint,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("ok"):
                    return True

                err = data.get("description", "unknown error")
                log.warning(
                    "Telegram rejected (attempt %d/%d) chat=%s: %s",
                    attempt, retries, chat_id, err,
                )

                # Rate-limit: retry after header
                if resp.status == 429:
                    retry_after = int(data.get("parameters", {}).get("retry_after", 5))
                    log.info("Rate-limited by Telegram — sleeping %ds", retry_after)
                    await asyncio.sleep(retry_after)

        except aiohttp.ClientError as exc:
            log.warning("Network error sending to %s (attempt %d): %s", chat_id, attempt, exc)
            await asyncio.sleep(2 ** attempt)

    return False


class TelegramSender:
    """Send formatted news alerts to one or more Telegram chats."""

    def __init__(self):
        if not TELEGRAM_TOKEN:
            log.error("TELEGRAM_BOT_TOKEN is not set!")
        if not TELEGRAM_CHAT_ID:
            log.error("TELEGRAM_CHAT_ID is not set!")
        self._chat_ids = [
            cid.strip()
            for cid in TELEGRAM_CHAT_ID.split(",")
            if cid.strip()
        ]

    async def send(
        self,
        article: dict[str, Any],
        analysis: dict[str, Any],
        score: int,
    ) -> bool:
        """Format and send the article to all configured chats."""
        text = _build_message(article, analysis, score)

        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(
                *[_send_single(session, cid, text) for cid in self._chat_ids],
                return_exceptions=True,
            )

        success = all(r is True for r in results)
        if not success:
            failures = [r for r in results if r is not True]
            log.warning("%d chat(s) failed to receive message: %s", len(failures), failures)
        return success

    async def send_startup_message(self) -> None:
        """Send a bot-started notification."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        text = (
            "🤖 <b>XAUUSD/USD Macro Bot Online</b>\n\n"
            f"✅ Bot started at {ts}\n"
            "📡 Monitoring: Reuters, MarketWatch, FXStreet, ForexLive, CNBC\n"
            "🧠 AI: Claude (Anthropic) analysis enabled\n"
            "⏱️ Polling every 10 minutes\n"
            "🔕 Only HIGH IMPACT events will be reported."
        )
        async with aiohttp.ClientSession() as session:
            for cid in self._chat_ids:
                await _send_single(session, cid, text)
