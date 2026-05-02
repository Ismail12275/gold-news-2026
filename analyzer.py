"""
analyzer.py — AI-powered news impact analyzer
Uses Claude (claude-sonnet-4-20250514) via the Anthropic API.
Falls back to OpenAI (gpt-4o-mini) if OPENAI_API_KEY is set and Claude fails.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

# ── API Config ───────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")

CLAUDE_MODEL  = "claude-sonnet-4-20250514"
OPENAI_MODEL  = "gpt-4o-mini"

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL    = "https://api.openai.com/v1/chat/completions"

# ── Prompt ───────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a professional macro-economic analyst specialising in 
Gold (XAUUSD) and the US Dollar (USD). You receive financial news headlines and 
summaries and return a strict JSON impact assessment. You are concise and precise."""

def build_user_prompt(title: str, summary: str) -> str:
    return f"""Analyse the following financial news and return ONLY a valid JSON object 
with these exact keys:

{{
  "usd_impact":   "Bullish" | "Bearish" | "Neutral",
  "gold_impact":  "Bullish" | "Bearish" | "Neutral",
  "strength":     "Low" | "Medium" | "High",
  "tradable":     "Yes" | "No",
  "explanation":  "<one sentence, max 25 words, plain English>"
}}

Rules:
- USD Bullish = strong USD, higher rates, hawkish Fed
- USD Bearish = weak USD, rate cuts, dovish Fed, geopolitical risk
- Gold Bullish = safe-haven demand, USD weakness, geopolitical fear, inflation
- Gold Bearish = USD strength, hawkish Fed, falling inflation
- Strength = High if market-moving data (NFP, CPI, rate decision, war escalation)
- Tradable = Yes if the news is likely to cause a tradeable XAUUSD move TODAY
- Return ONLY the JSON object, no markdown, no preamble

News Title:   {title}
News Summary: {summary}"""


# ── Parsers ──────────────────────────────────────────────────────────────────
def _extract_json(raw: str) -> dict[str, Any]:
    """Extract and validate JSON from model output."""
    # Strip markdown code fences if present
    raw = re.sub(r"```(?:json)?", "", raw).strip()

    # Find first JSON object
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in response: {raw[:200]}")

    data = json.loads(match.group())

    # Normalise keys
    allowed_usd_gold = {"bullish", "bearish", "neutral"}
    allowed_strength = {"low", "medium", "high"}
    allowed_tradable = {"yes", "no"}

    usd      = str(data.get("usd_impact", "Neutral")).strip().capitalize()
    gold     = str(data.get("gold_impact", "Neutral")).strip().capitalize()
    strength = str(data.get("strength", "Low")).strip().capitalize()
    tradable = str(data.get("tradable", "No")).strip().capitalize()
    explain  = str(data.get("explanation", "")).strip()

    if usd.lower() not in allowed_usd_gold:
        usd = "Neutral"
    if gold.lower() not in allowed_usd_gold:
        gold = "Neutral"
    if strength.lower() not in allowed_strength:
        strength = "Low"
    if tradable.lower() not in allowed_tradable:
        tradable = "No"

    return {
        "usd_impact":  usd,
        "gold_impact": gold,
        "strength":    strength,
        "tradable":    tradable,
        "explanation": explain[:200],
    }


# ── Claude ───────────────────────────────────────────────────────────────────
async def _analyse_with_claude(
    session: aiohttp.ClientSession,
    title: str,
    summary: str,
) -> dict[str, Any]:
    headers = {
        "x-api-key":         ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }
    payload = {
        "model":      CLAUDE_MODEL,
        "max_tokens": 300,
        "system":     SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": build_user_prompt(title, summary)}
        ],
    }

    async with session.post(
        ANTHROPIC_URL,
        json=payload,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"Claude API {resp.status}: {body[:300]}")
        data = await resp.json()

    raw = data["content"][0]["text"]
    return _extract_json(raw)


# ── OpenAI fallback ──────────────────────────────────────────────────────────
async def _analyse_with_openai(
    session: aiohttp.ClientSession,
    title: str,
    summary: str,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type":  "application/json",
    }
    payload = {
        "model":      OPENAI_MODEL,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": build_user_prompt(title, summary)},
        ],
    }

    async with session.post(
        OPENAI_URL,
        json=payload,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"OpenAI API {resp.status}: {body[:300]}")
        data = await resp.json()

    raw = data["choices"][0]["message"]["content"]
    return _extract_json(raw)


# ── Rule-based fallback ──────────────────────────────────────────────────────
def _rule_based_fallback(title: str, summary: str) -> dict[str, Any]:
    """Emergency fallback when all AI APIs fail — simple keyword heuristics."""
    text = (title + " " + summary).lower()

    # USD impact
    if any(k in text for k in ["rate hike", "hawkish", "strong dollar", "tightening"]):
        usd = "Bullish"
    elif any(k in text for k in ["rate cut", "dovish", "weak dollar", "recession", "war"]):
        usd = "Bearish"
    else:
        usd = "Neutral"

    # Gold impact (inverse USD for most cases)
    if any(k in text for k in ["gold rises", "gold rally", "safe haven", "geopolit", "nuclear"]):
        gold = "Bullish"
    elif any(k in text for k in ["gold falls", "gold drops", "rate hike"]):
        gold = "Bearish"
    elif usd == "Bearish":
        gold = "Bullish"
    elif usd == "Bullish":
        gold = "Bearish"
    else:
        gold = "Neutral"

    # Strength
    if any(k in text for k in ["fomc", "rate decision", "nfp", "non-farm", "cpi", "war", "nuclear"]):
        strength = "High"
    elif any(k in text for k in ["inflation", "payroll", "ecb", "powell", "lagarde"]):
        strength = "Medium"
    else:
        strength = "Low"

    return {
        "usd_impact":  usd,
        "gold_impact": gold,
        "strength":    strength,
        "tradable":    "Yes" if strength == "High" else "No",
        "explanation": "Rule-based fallback analysis (AI unavailable).",
    }


# ── Public API ────────────────────────────────────────────────────────────────
async def analyze_news(title: str, summary: str) -> dict[str, Any]:
    """
    Analyse news using AI.  Priority:
      1. Claude (Anthropic)  — if ANTHROPIC_API_KEY is set
      2. OpenAI              — if OPENAI_API_KEY is set
      3. Rule-based fallback — always available
    """
    async with aiohttp.ClientSession() as session:
        if ANTHROPIC_API_KEY:
            try:
                result = await _analyse_with_claude(session, title, summary)
                log.debug("Claude analysis OK: %s", result)
                return result
            except Exception as exc:
                log.warning("Claude analysis failed (%s), trying OpenAI…", exc)

        if OPENAI_API_KEY:
            try:
                result = await _analyse_with_openai(session, title, summary)
                log.debug("OpenAI analysis OK: %s", result)
                return result
            except Exception as exc:
                log.warning("OpenAI analysis failed (%s), using fallback…", exc)

    log.warning("All AI APIs unavailable — using rule-based fallback.")
    return _rule_based_fallback(title, summary)
