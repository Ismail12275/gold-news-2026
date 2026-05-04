"""
analyzer.py — Institutional-grade macro analysis engine
Produces bilingual (EN/AR) Telegram alerts for XAUUSD & USD traders
"""

import os
import json
import re
import anthropic
from datetime import datetime

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ─────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """You are a senior macro analyst at a tier-1 institutional trading desk specializing in USD and Gold (XAUUSD).

Your job is to analyze financial news and produce structured JSON output used to generate real-time trading alerts for professional discretionary traders.

ANALYSIS FRAMEWORK:
- Classify monetary tone: Hawkish (rate-hike bias / tightening), Dovish (rate-cut bias / easing), or Neutral
- Assess USD directional impact: bullish, bearish, or neutral — with explicit macro reasoning
- Assess Gold directional impact: bullish, bearish, or neutral — with explicit macro reasoning
- Score macro importance 1–5 based on market-repricing potential
- Assess tradability: High Conviction / Moderate / Low Conviction / Non-Tradable
- Write a professional institutional summary (2–4 sentences) — no generic phrases like "limited directional signal"
- Write a full professional Arabic translation of the alert using proper financial Arabic terminology

MACRO SCORING RULES:
5 = FOMC decision, CPI beat/miss, NFP shock — major repricing potential across rates/FX/gold
4 = Fed speaker with clear policy shift signal, PCE data, strong PMI deviation — strong directional catalyst
3 = Regional Fed commentary, ISM data, housing data — moderate institutional relevance
2 = Secondary data or vague statement — weak but market-notable
1 = Purely informational, pre-known, zero surprise factor

TRADABILITY RULES:
- "High Conviction": Direct FOMC/CPI/NFP, clear policy pivot signal, major data surprise
- "Moderate": Fed speech with conditional language, mixed data, geopolitical developments
- "Low Conviction": Speech without new information, data in-line with expectations
- "Non-Tradable": Pre-announced, fully priced-in, ceremonial statements

USD IMPACT LOGIC:
- Hawkish signals → USD bullish (higher rates attract capital flows)
- Dovish signals → USD bearish (rate cuts reduce yield differential)
- Risk-off events → USD bullish (safe haven demand)
- Inflation above target → USD bullish (forces Fed action)
- Weak labor market → USD bearish (Fed easing pressure)

GOLD IMPACT LOGIC:
- USD strength → Gold bearish (inverse correlation)
- Real yields rising → Gold bearish (opportunity cost)
- Dovish pivot → Gold bullish (lower real rates)
- Risk-off / geopolitical → Gold bullish (safe haven)
- Inflation above expectations → Gold bullish (inflation hedge)
- Fed pause/cut → Gold bullish

ARABIC TERMINOLOGY STANDARDS:
- Hawkish = تشدد نقدي
- Dovish = تيسير نقدي
- Neutral = محايد
- Bullish Gold = إيجابي للذهب
- Bearish Gold = سلبي للذهب
- USD Supportive = داعم للدولار
- USD Negative = ضاغط على الدولار
- Federal Reserve = الاحتياطي الفيدرالي
- Interest rates = أسعار الفائدة
- Inflation = التضخم
- Monetary policy = السياسة النقدية
- Treasury yields = عوائد السندات الخزينة
- Safe haven = الملاذ الآمن
- Market repricing = إعادة تسعير السوق
- Risk sentiment = معنويات المخاطرة

NEVER use: "limited directional signal", "unclear impact", "mixed signals without elaboration", "it depends"
ALWAYS explain the macro transmission mechanism (why does this move gold/USD)

OUTPUT FORMAT (strict JSON, no markdown):
{
  "category": "string (e.g. Federal Reserve / Economic Data / Geopolitical / Central Bank)",
  "title": "string (concise headline)",
  "tone": "Hawkish | Dovish | Neutral",
  "usd_impact": "Bullish | Bearish | Neutral",
  "usd_reasoning": "string (1–2 sentences)",
  "gold_impact": "Bullish | Bearish | Neutral",
  "gold_reasoning": "string (1–2 sentences)",
  "macro_score": 1–5,
  "macro_score_reason": "string",
  "tradability": "High Conviction | Moderate | Low Conviction | Non-Tradable",
  "tradability_reason": "string",
  "professional_analysis": "string (2–4 institutional-quality sentences, no generic phrases)",
  "arabic_title": "string",
  "arabic_tone": "string",
  "arabic_usd_impact": "string",
  "arabic_gold_impact": "string",
  "arabic_analysis": "string (full professional Arabic summary, natural financial Arabic, not literal translation)"
}"""


# ─────────────────────────────────────────────
# CORE ANALYSIS FUNCTION
# ─────────────────────────────────────────────
def analyze_news(news_item: dict) -> dict | None:
    """
    Analyze a news item and return structured institutional analysis.

    Args:
        news_item: dict with keys: title, summary, source, published_at, category (optional)

    Returns:
        dict with full analysis or None on failure
    """
    title = news_item.get("title", "")
    summary = news_item.get("summary", "")
    source = news_item.get("source", "Unknown")
    published_at = news_item.get("published_at", datetime.utcnow().isoformat())
    category_hint = news_item.get("category", "")

    user_prompt = f"""Analyze the following financial news for its macro impact on USD and Gold (XAUUSD):

HEADLINE: {title}
SUMMARY: {summary}
SOURCE: {source}
PUBLISHED: {published_at}
CATEGORY HINT: {category_hint}

Provide your full institutional analysis in the exact JSON format specified. Be precise, actionable, and professional."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw = response.content[0].text.strip()

        # Strip any accidental markdown fences
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        analysis = json.loads(raw)

        # Attach original metadata
        analysis["_source"] = source
        analysis["_published_at"] = published_at
        analysis["_raw_title"] = title
        analysis["_raw_summary"] = summary

        return analysis

    except json.JSONDecodeError as e:
        print(f"[ANALYZER] JSON parse error for '{title}': {e}")
        return None
    except Exception as e:
        print(f"[ANALYZER] API error for '{title}': {e}")
        return None


# ─────────────────────────────────────────────
# IMPACT EMOJI HELPERS
# ─────────────────────────────────────────────
def _tone_emoji(tone: str) -> str:
    return {"Hawkish": "🦅", "Dovish": "🕊", "Neutral": "⚖️"}.get(tone, "📊")


def _impact_emoji(impact: str) -> str:
    return {"Bullish": "🟢", "Bearish": "🔴", "Neutral": "🟡"}.get(impact, "⚪")


def _tradability_emoji(t: str) -> str:
    return {
        "High Conviction": "🔥",
        "Moderate": "📈",
        "Low Conviction": "🔅",
        "Non-Tradable": "⛔",
    }.get(t, "📊")


def _score_bar(score: int) -> str:
    filled = "█" * score
    empty = "░" * (5 - score)
    return f"{filled}{empty} {score}/5"


def _arabic_impact(impact: str, asset: str) -> str:
    """Map English impact + asset to Arabic."""
    if asset == "usd":
        return {"Bullish": "📈 داعم للدولار", "Bearish": "📉 ضاغط على الدولار", "Neutral": "➡️ محايد للدولار"}.get(impact, impact)
    else:  # gold
        return {"Bullish": "📈 إيجابي للذهب", "Bearish": "📉 سلبي للذهب", "Neutral": "➡️ محايد للذهب"}.get(impact, impact)


# ─────────────────────────────────────────────
# TELEGRAM MESSAGE FORMATTER
# ─────────────────────────────────────────────
def format_telegram_message(analysis: dict) -> str:
    """
    Format analysis into a professional bilingual Telegram alert.
    Returns Markdown-compatible string for Telegram sendMessage.
    """

    tone = analysis.get("tone", "Neutral")
    usd = analysis.get("usd_impact", "Neutral")
    gold = analysis.get("gold_impact", "Neutral")
    score = analysis.get("macro_score", 1)
    tradability = analysis.get("tradability", "Low Conviction")

    # ── ENGLISH SECTION ────────────────────────────────────────────
    en_lines = [
        f"{'⚡' * min(score, 3)} *HIGH IMPACT NEWS ALERT*",
        "",
        f"📌 *{analysis.get('category', 'Market News').upper()}*",
        f"📰 *{analysis.get('title', analysis.get('_raw_title', 'N/A'))}*",
        "",
        f"{_tone_emoji(tone)} *Tone:* `{tone}`",
        f"{_impact_emoji(usd)} *USD:* `{usd}`  •  _{analysis.get('usd_reasoning', '')}_{' '}",
        f"{_impact_emoji(gold)} *Gold:* `{gold}`  •  _{analysis.get('gold_reasoning', '')}_{' '}",
        "",
        f"📊 *Macro Score:* `{_score_bar(score)}`",
        f"_{analysis.get('macro_score_reason', '')}_",
        "",
        f"{_tradability_emoji(tradability)} *Tradability:* `{tradability}`",
        f"_{analysis.get('tradability_reason', '')}_",
        "",
        "💼 *Institutional Analysis:*",
        f"_{analysis.get('professional_analysis', '')}_",
    ]

    # ── ARABIC SECTION ─────────────────────────────────────────────
    arabic_tone_raw = analysis.get("arabic_tone", tone)
    ar_usd = analysis.get("arabic_usd_impact") or _arabic_impact(usd, "usd")
    ar_gold = analysis.get("arabic_gold_impact") or _arabic_impact(gold, "gold")

    ar_lines = [
        "",
        "─────────────────────",
        f"🔔 *تنبيه إخباري — تأثير السوق*",
        "",
        f"📌 *{analysis.get('category', 'أخبار السوق').upper()}*",
        f"📰 *{analysis.get('arabic_title', analysis.get('title', ''))}*",
        "",
        f"{_tone_emoji(tone)} *النبرة النقدية:* `{arabic_tone_raw}`",
        f"{_impact_emoji(usd)} *الدولار:* `{ar_usd}`",
        f"{_impact_emoji(gold)} *الذهب:* `{ar_gold}`",
        "",
        f"📊 *الدرجة الكلية:* `{_score_bar(score)}`",
        "",
        f"{_tradability_emoji(tradability)} *قابلية التداول:* `{tradability}`",
        "",
        "💼 *التحليل المؤسسي:*",
        f"_{analysis.get('arabic_analysis', '')}_",
    ]

    # ── FOOTER ─────────────────────────────────────────────────────
    ts = analysis.get("_published_at", datetime.utcnow().isoformat())
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        ts_fmt = dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        ts_fmt = ts

    footer = [
        "",
        "─────────────────────",
        f"🕐 `{ts_fmt}`  |  📡 {analysis.get('_source', 'Unknown')}",
    ]

    return "\n".join(en_lines + ar_lines + footer)


# ─────────────────────────────────────────────
# BATCH ANALYZER
# ─────────────────────────────────────────────
def analyze_and_format(news_item: dict) -> tuple[dict | None, str | None]:
    """
    Full pipeline: analyze + format.
    Returns (analysis_dict, telegram_message_string) or (None, None) on failure.
    """
    analysis = analyze_news(news_item)
    if not analysis:
        return None, None

    message = format_telegram_message(analysis)
    return analysis, message
