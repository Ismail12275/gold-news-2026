"""
telegram_sender.py — Professional Telegram alert sender
Updated for Gemini / Ollama / Rule-based bot architecture
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ── Emoji Maps ─────────────────────────────────────────────────────────────
USD_EMOJI = {
    "Bullish": "🟢",
    "Bearish": "🔴",
    "Neutral": "🟡",
}

GOLD_EMOJI = {
    "Bullish": "⬆️",
    "Bearish": "⬇️",
    "Neutral": "➡️",
}

STR_EMOJI = {
    "High": "🔥",
    "Medium": "⚡",
    "Low": "📉",
}

TRD_EMOJI = {
    "Yes": "✅",
    "No": "❌",
}

# ── Category Detection ─────────────────────────────────────────────────────
CATEGORY_MAP: list[tuple[list[str], str]] = [
    (
        [
            "fomc",
            "federal reserve",
            "fed rate",
            "rate decision",
            "rate hike",
            "rate cut",
            "ecb",
        ],
        "🏦 Rate Decision",
    ),
    (
        [
            "cpi",
            "inflation",
            "ppi",
            "consumer price",
            "producer price",
        ],
        "📊 Inflation Data",
    ),
    (
        [
            "non-farm",
            "nonfarm",
            "nfp",
            "payroll",
            "jobs report",
            "unemployment",
        ],
        "💼 Jobs / NFP",
    ),
    (
        [
            "powell",
            "lagarde",
            "central bank",
            "speech",
            "testimony",
        ],
        "🎙️ Central Bank Speech",
    ),
    (
        [
            "war",
            "conflict",
            "invasion",
            "nuclear",
            "sanction",
            "geopolitical",
            "crisis",
        ],
        "⚔️ Geopolitics",
    ),
    (
        [
            "oil",
            "opec",
            "crude",
            "petroleum",
        ],
        "🛢️ Oil Shock",
    ),
]


def _detect_category(title: str, summary: str) -> str:
    text = (title + " " + summary).lower()

    for keywords, label in CATEGORY_MAP:
        if any(keyword in text for keyword in keywords):
            return label

    return "📰 Macro Event"


# ── Message Builder ────────────────────────────────────────────────────────
def _build_message(
    article: dict[str, Any],
    analysis: dict[str, Any],
    score: int,
) -> str:
    title = article.get("title", "No title")
    url = article.get("url", "")
    source = article.get("source", "Unknown")

    usd = analysis.get("usd_impact", "Neutral")
    gold = analysis.get("gold_impact", "Neutral")
    strength = analysis.get("strength", "Low")
    tradable = analysis.get("tradable", "No")
    explanation = analysis.get("explanation", "")

    category = _detect_category(
        title,
        article.get("summary", ""),
    )

    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")

    score_bar = (
        "●" * min(score, 5)
        + "○" * max(0, 5 - score)
    )

    lines = [
        "🚨 <b>HIGH IMPACT NEWS ALERT</b>",
        "",
        f"{category}",
        "",
        f"📌 <b>Title:</b> {title}",
        "",
        f"💵 <b>USD Impact:</b> {USD_EMOJI.get(usd, '🟡')} {usd}",
        f"🥇 <b>Gold Impact:</b> {GOLD_EMOJI.get(gold, '➡️')} {gold}",
        "",
        f"{STR_EMOJI.get(strength, '📉')} <b>Strength:</b> {strength}",
        f"{TRD_EMOJI.get(tradable, '❌')} <b>Tradable:</b> {tradable}",
        "",
        f"📝 <b>Analysis:</b> <i>{explanation}</i>",
        "",
        f"⚡ <b>Macro Score:</b> [{score_bar}] {score}/5+",
        f"🕐 {ts} | 📡 {source}",
    ]

    if url:
        lines.extend(
            [
                "",
                f'🔗 <a href="{url}">Read Full Article</a>',
            ]
        )

    return "\n".join(lines)


# ── Telegram Sending ───────────────────────────────────────────────────────
async def _send_single(
    session: aiohttp.ClientSession,
    chat_id: str,
    text: str,
    retries: int = 3,
) -> bool:
    endpoint = f"{TELEGRAM_API}/sendMessage"

    payload = {
        "chat_id": chat_id.strip(),
        "text": text,
        "parse_mode": "HTML",
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

                err = data.get(
                    "description",
                    "unknown error",
                )

                log.warning(
                    "Telegram rejected (attempt %d/%d) chat=%s: %s",
                    attempt,
                    retries,
                    chat_id,
                    err,
                )

                if resp.status == 429:
                    retry_after = int(
                        data.get(
                            "parameters",
                            {},
                        ).get(
                            "retry_after",
                            5,
                        )
                    )

                    log.info(
                        "Rate limited — sleeping %ds",
                        retry_after,
                    )

                    await asyncio.sleep(retry_after)

        except aiohttp.ClientError as exc:
            log.warning(
                "Network error sending to %s (attempt %d): %s",
                chat_id,
                attempt,
                exc,
            )

            await asyncio.sleep(2**attempt)

    return False


# ── Main Sender Class ──────────────────────────────────────────────────────
class TelegramSender:
    def __init__(self):
        if not TELEGRAM_TOKEN:
            log.error(
                "TELEGRAM_BOT_TOKEN is missing!"
            )

        if not TELEGRAM_CHAT_ID:
            log.error(
                "TELEGRAM_CHAT_ID is missing!"
            )

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
        text = _build_message(
            article,
            analysis,
            score,
        )

        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(
                *[
                    _send_single(
                        session,
                        cid,
                        text,
                    )
                    for cid in self._chat_ids
                ],
                return_exceptions=True,
            )

        success = all(
            result is True
            for result in results
        )

        if not success:
            failures = [
                result
                for result in results
                if result is not True
            ]

            log.warning(
                "%d chat(s) failed: %s",
                len(failures),
                failures,
            )

        return success

    async def send_startup_message(self) -> None:
        ts = datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M UTC"
        )

        text = (
            "🤖 <b>XAUUSD/USD Macro Bot Online</b>\n\n"
            f"✅ Bot started at {ts}\n"
            "📡 Monitoring: Yahoo Finance, Federal Reserve, ECB, ForexLive, NewsAPI\n"
            "🧠 AI: Gemini + Ollama + Rule-based fallback\n"
            "⏱️ Polling every 10 minutes\n"
            "🔕 Only HIGH IMPACT events will be reported."
        )

        async with aiohttp.ClientSession() as session:
            for cid in self._chat_ids:
                await _send_single(
                    session,
                    cid,
                    text,
                )
