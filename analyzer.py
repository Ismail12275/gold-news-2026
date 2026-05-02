"""
analyzer.py — Free AI-powered news impact analyzer
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Priority chain (first success wins):

  1. Google Gemini Flash  — free tier, 15 req/min, no card required
                            Set GEMINI_API_KEY in .env
                            Get key → https://aistudio.google.com/app/apikey

  2. Ollama (local LLM)  — fully free, offline, no API key
                            Install → https://ollama.com
                            Run     → ollama pull gemma2   (or mistral / llama3.2)
                            Set OLLAMA_MODEL in .env to override default

  3. Rule-based fallback — always available, no network, no keys

Public interface is UNCHANGED — analyze_news(title, summary) still returns:
  {usd_impact, gold_impact, strength, tradable, explanation}
"""

import asyncio
import json
import logging
import os
import re
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

# ── Free API config ───────────────────────────────────────────────────────────

# Option A — Google Gemini (free tier)
# Free quota: 15 RPM / 1,500 req per day  (gemini-1.5-flash)
# No billing required for free tier.
# Get your key: https://aistudio.google.com/app/apikey
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_URL     = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    + GEMINI_MODEL + ":generateContent"
)

# Option B — Ollama (local, fully offline)
# Default host = same machine; on Railway/VPS set OLLAMA_HOST=http://<host>:11434
OLLAMA_HOST  = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma2")
OLLAMA_URL   = OLLAMA_HOST + "/api/generate"

# Timeouts
GEMINI_TIMEOUT = 20
OLLAMA_TIMEOUT = 45

# ── Shared prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are a professional macro-economic analyst specialising in "
    "Gold (XAUUSD) and the US Dollar (USD). You receive financial news headlines and "
    "summaries and return a strict JSON impact assessment. You are concise and precise."
)


def build_user_prompt(title: str, summary: str) -> str:
    return (
        "Analyse the following financial news and return ONLY a valid JSON object "
        "with these exact keys:\n\n"
        "{\n"
        '  "usd_impact":   "Bullish" | "Bearish" | "Neutral",\n'
        '  "gold_impact":  "Bullish" | "Bearish" | "Neutral",\n'
        '  "strength":     "Low" | "Medium" | "High",\n'
        '  "tradable":     "Yes" | "No",\n'
        '  "explanation":  "<one sentence, max 25 words, plain English>"\n'
        "}\n\n"
        "Rules:\n"
        "- USD Bullish = strong USD, higher rates, hawkish Fed\n"
        "- USD Bearish = weak USD, rate cuts, dovish Fed, geopolitical risk\n"
        "- Gold Bullish = safe-haven demand, USD weakness, geopolitical fear, inflation\n"
        "- Gold Bearish = USD strength, hawkish Fed, falling inflation\n"
        "- Strength = High if market-moving data (NFP, CPI, rate decision, war escalation)\n"
        "- Tradable = Yes if the news is likely to cause a tradeable XAUUSD move TODAY\n"
        "- Return ONLY the JSON object, no markdown, no preamble\n\n"
        f"News Title:   {title}\n"
        f"News Summary: {summary}"
    )


# ── JSON extractor / validator (unchanged) ────────────────────────────────────
def _extract_json(raw: str) -> dict[str, Any]:
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in response: {raw[:200]}")

    data = json.loads(match.group())

    allowed_usd_gold = {"bullish", "bearish", "neutral"}
    allowed_strength = {"low", "medium", "high"}
    allowed_tradable = {"yes", "no"}

    usd      = str(data.get("usd_impact",  "Neutral")).strip().capitalize()
    gold     = str(data.get("gold_impact", "Neutral")).strip().capitalize()
    strength = str(data.get("strength",    "Low")).strip().capitalize()
    tradable = str(data.get("tradable",    "No")).strip().capitalize()
    explain  = str(data.get("explanation", "")).strip()

    if usd.lower()      not in allowed_usd_gold: usd      = "Neutral"
    if gold.lower()     not in allowed_usd_gold: gold     = "Neutral"
    if strength.lower() not in allowed_strength: strength = "Low"
    if tradable.lower() not in allowed_tradable: tradable = "No"

    return {
        "usd_impact":  usd,
        "gold_impact": gold,
        "strength":    strength,
        "tradable":    tradable,
        "explanation": explain[:200],
    }


# ── Option A — Google Gemini (free tier) ─────────────────────────────────────
async def _analyse_with_gemini(
    session: aiohttp.ClientSession,
    title: str,
    summary: str,
) -> dict[str, Any]:
    full_prompt = SYSTEM_PROMPT + "\n\n" + build_user_prompt(title, summary)

    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 300,
            "topP": 0.9,
        },
        "safetySettings": [
            {
                "category":  "HARM_CATEGORY_DANGEROUS_CONTENT",
                "threshold": "BLOCK_ONLY_HIGH",
            }
        ],
    }

    async with session.post(
        GEMINI_URL,
        json=payload,
        params={"key": GEMINI_API_KEY},
        timeout=aiohttp.ClientTimeout(total=GEMINI_TIMEOUT),
    ) as resp:
        if resp.status == 429:
            raise RuntimeError("Gemini rate-limit (429)")
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"Gemini API HTTP {resp.status}: {body[:300]}")
        data = await resp.json()

    try:
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise ValueError(f"Unexpected Gemini response: {exc}") from exc

    return _extract_json(raw)


# ── Option B — Ollama (local, fully free) ────────────────────────────────────
async def _analyse_with_ollama(
    session: aiohttp.ClientSession,
    title: str,
    summary: str,
) -> dict[str, Any]:
    full_prompt = (
        "<|system|>\n" + SYSTEM_PROMPT + "\n<|end|>\n"
        "<|user|>\n" + build_user_prompt(title, summary) + "\n<|end|>\n"
        "<|assistant|>\n"
    )

    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": full_prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 300, "top_p": 0.9},
    }

    try:
        async with session.post(
            OLLAMA_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=OLLAMA_TIMEOUT),
        ) as resp:
            if resp.status == 404:
                raise RuntimeError(
                    f"Model '{OLLAMA_MODEL}' not found — run: ollama pull {OLLAMA_MODEL}"
                )
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Ollama HTTP {resp.status}: {body[:300]}")
            data = await resp.json()
    except aiohttp.ClientConnectorError:
        raise RuntimeError(
            f"Cannot reach Ollama at {OLLAMA_HOST} — run: ollama serve"
        )

    raw = data.get("response", "").strip()
    if not raw:
        raise ValueError("Ollama returned an empty response")

    return _extract_json(raw)


# ── Fallback — enhanced rule-based scoring (zero dependencies) ────────────────
def _rule_based_fallback(title: str, summary: str) -> dict[str, Any]:
    text = (title + " " + summary).lower()

    USD_BULLISH_KW = [
        "rate hike", "hawkish", "strong dollar", "tightening",
        "beats expectations", "better than expected", "hotter than expected",
        "above forecast", "fed hikes", "fomc hikes", "rate increase",
        "dollar surges", "dollar strengthens",
    ]
    USD_BEARISH_KW = [
        "rate cut", "dovish", "weak dollar", "recession", "war",
        "conflict", "crisis", "geopolit", "misses expectations",
        "below forecast", "worse than expected", "fed cuts", "fomc cuts",
        "rate decrease", "dollar falls", "dollar weakens", "slowdown",
    ]
    bull_usd = sum(1 for k in USD_BULLISH_KW if k in text)
    bear_usd = sum(1 for k in USD_BEARISH_KW if k in text)
    usd = "Bullish" if bull_usd > bear_usd else "Bearish" if bear_usd > bull_usd else "Neutral"

    GOLD_BULLISH_KW = [
        "gold rises", "gold rally", "gold surges", "gold climbs",
        "safe haven", "safe-haven", "geopolit", "nuclear",
        "inflation surges", "inflation rises", "xauusd up",
    ]
    GOLD_BEARISH_KW = [
        "gold falls", "gold drops", "gold slumps", "gold declines",
        "rate hike", "fed hikes", "xauusd down",
    ]
    bull_gold = sum(1 for k in GOLD_BULLISH_KW if k in text)
    bear_gold = sum(1 for k in GOLD_BEARISH_KW if k in text)

    if bull_gold > bear_gold:
        gold = "Bullish"
    elif bear_gold > bull_gold:
        gold = "Bearish"
    else:
        gold = {"Bearish": "Bullish", "Bullish": "Bearish"}.get(usd, "Neutral")

    HIGH_KW = [
        "fomc", "rate decision", "nfp", "non-farm", "nonfarm", "cpi report",
        "war escalat", "nuclear", "fed hikes", "fed cuts", "inflation report",
    ]
    MED_KW = [
        "inflation", "payroll", "ecb", "powell", "lagarde", "ppi",
        "unemployment", "jobs report", "gdp", "retail sales", "pmi",
    ]
    strength = (
        "High"   if any(k in text for k in HIGH_KW) else
        "Medium" if any(k in text for k in MED_KW)  else
        "Low"
    )

    EXPLANATIONS = {
        ("Bullish", "Bearish"): "Hawkish USD signals put downward pressure on gold.",
        ("Bullish", "Neutral"): "USD strength noted; gold direction unclear.",
        ("Bullish", "Bullish"): "Mixed signals — confirm with price action.",
        ("Bearish", "Bullish"): "USD weakness supports gold safe-haven demand.",
        ("Bearish", "Neutral"): "USD under pressure; gold lacks clear catalyst.",
        ("Bearish", "Bearish"): "Risk-off selling hitting both USD and gold.",
        ("Neutral", "Bullish"): "Gold demand rising on non-USD catalyst.",
        ("Neutral", "Bearish"): "Gold faces headwinds despite stable USD.",
        ("Neutral", "Neutral"): "Limited directional signal from this news.",
    }

    return {
        "usd_impact":  usd,
        "gold_impact": gold,
        "strength":    strength,
        "tradable":    "Yes" if strength == "High" else "No",
        "explanation": EXPLANATIONS.get((usd, gold), "Rule-based fallback — AI unavailable."),
    }


# ── Public API — SIGNATURE UNCHANGED ─────────────────────────────────────────
async def analyze_news(title: str, summary: str) -> dict[str, Any]:
    """
    Analyse macro news impact on USD and Gold using free AI only.

    Priority chain:
      1. Google Gemini Flash  — fastest free cloud AI (set GEMINI_API_KEY)
      2. Ollama local LLM     — fully offline, no cost  (ollama serve)
      3. Rule-based fallback  — always works, no dependencies

    Returns:
      { usd_impact, gold_impact, strength, tradable, explanation }
    """
    async with aiohttp.ClientSession() as session:

        # Tier 1 — Gemini (free cloud)
        if GEMINI_API_KEY:
            try:
                result = await _analyse_with_gemini(session, title, summary)
                log.debug("Gemini analysis OK: %s", result)
                return result
            except RuntimeError as exc:
                if "429" in str(exc):
                    log.warning("Gemini rate-limited — trying Ollama…")
                    await asyncio.sleep(4)
                else:
                    log.warning("Gemini failed (%s) — trying Ollama…", exc)
            except Exception as exc:
                log.warning("Gemini error (%s) — trying Ollama…", exc)

        # Tier 2 — Ollama (local)
        try:
            result = await _analyse_with_ollama(session, title, summary)
            log.debug("Ollama analysis OK: %s", result)
            return result
        except RuntimeError as exc:
            log.info("Ollama unavailable (%s) — using rule-based fallback.", exc)
        except Exception as exc:
            log.warning("Ollama error (%s) — using rule-based fallback.", exc)

    # Tier 3 — rule-based (always works)
    log.info("Using enhanced rule-based analysis.")
    return _rule_based_fallback(title, summary)
