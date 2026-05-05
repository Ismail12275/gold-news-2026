"""
analyzer.py — Institutional-grade macro analysis engine
Primary  : Google Gemini 2.0 Flash  (fast, cheap, JSON mode)
Fallback 1: Groq / llama-3.3-70b     (free tier, very fast)
Fallback 2: OpenRouter / mistral-7b  (last resort)

Produces bilingual (EN/AR) Telegram alerts for XAUUSD & USD traders.
"""

import os
import json
import re
import time
import requests
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────────────────────
# PROVIDER ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

GEMINI_API_URL  = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)
GROQ_API_URL       = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

GROQ_MODEL       = "llama-3.3-70b-versatile"
OPENROUTER_MODEL = "mistralai/mistral-7b-instruct"

REQUEST_TIMEOUT = 30  # seconds per provider attempt


# ─────────────────────────────────────────────────────────────────────────────
# ENV VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def validate_env() -> bool:
    """
    Check all provider keys at startup.
    At least GEMINI_API_KEY must be present.
    Returns True if at least one provider key is available.
    """
    providers = [
        ("GEMINI_API_KEY",     "Gemini Flash   [PRIMARY]"),
        ("GROQ_API_KEY",       "Groq           [FALLBACK 1]"),
        ("OPENROUTER_API_KEY", "OpenRouter     [FALLBACK 2]"),
    ]
    print("[ANALYZER] ── Provider Key Check ──────────────────────")
    available = 0
    for env_var, label in providers:
        key = os.environ.get(env_var, "").strip()
        if key:
            masked = key[:8] + "..." + key[-4:]
            print(f"[ANALYZER] ✅ {label} → {masked}")
            available += 1
        else:
            print(f"[ANALYZER] ⚠️  {label} → NOT SET")
    print("[ANALYZER] ───────────────────────────────────────────")
    if available == 0:
        print(
            "[ANALYZER] ❌ No API keys found.\n"
            "   → Add GEMINI_API_KEY to Railway: Settings → Variables\n"
            "   → Get a free key at: https://aistudio.google.com/apikey"
        )
        return False
    return True


def _get_key(env_var: str) -> str:
    """Read env var at call time (never at import). Raises KeyError if missing."""
    val = os.environ.get(env_var, "").strip()
    if not val:
        raise KeyError(f"{env_var} is not set")
    return val


# ─────────────────────────────────────────────────────────────────────────────
# SHARED SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior macro analyst at a tier-1 institutional trading desk specializing in USD and Gold (XAUUSD).

Your job is to analyze financial news and produce structured JSON output for professional discretionary traders.

════════════════════════════════════
ANALYSIS FRAMEWORK
════════════════════════════════════

TONE CLASSIFICATION:
- Hawkish: rate-hike bias, tightening language, inflation concern, balance sheet reduction
- Dovish: rate-cut bias, easing language, growth concern, accommodation signal
- Neutral: data-dependent, balanced, no directional signal

MACRO SCORING (integer 1-5):
5 = FOMC rate decision, CPI beat/miss, NFP shock — major cross-asset repricing
4 = Fed speaker with explicit policy shift, PCE print, Treasury yield spike, geopolitical escalation
3 = Regional Fed commentary with new angle, ISM/PMI surprise, housing data deviation
2 = Secondary data, in-line prints, vague commentary
1 = Pre-announced, ceremonial, purely informational — zero surprise factor

TRADABILITY:
High Conviction = FOMC/CPI/NFP, explicit pivot signal, confirmed data surprise
Moderate = Fed conditional speech, mixed data, geopolitical risk development
Low Conviction = Speech without new info, in-line data, restatement of prior guidance
Non-Tradable = Pre-announced, fully priced-in, ceremonial

USD TRANSMISSION MECHANISM:
Hawkish → higher real rates → capital inflows → USD Bullish
Dovish → lower rate expectations → capital outflows → USD Bearish
Risk-off → safe haven demand → USD Bullish
Strong data → Fed tightening pressure → USD Bullish
Weak labor → Fed easing pressure → USD Bearish

GOLD TRANSMISSION MECHANISM:
USD strength → inverse correlation → Gold Bearish
Real yields rising → opportunity cost → Gold Bearish
Dovish pivot → lower real rates → Gold Bullish
Risk-off / geopolitical → safe haven flow → Gold Bullish
Inflation surprise → inflation hedge → Gold Bullish
Fed pause or cut → Gold Bullish

════════════════════════════════════
ARABIC OUTPUT RULES — MANDATORY
════════════════════════════════════

REQUIRED: All Arabic fields must use professional financial Arabic only.
FORBIDDEN: Machine-literal translation, mixed scripts, broken Unicode, English words in Arabic fields.

MANDATORY ARABIC TERMINOLOGY — use EXACTLY as written:
  Hawkish          → تشدد نقدي
  Dovish           → تيسير نقدي
  Neutral          → محايد
  Bullish (Gold)   → إيجابي للذهب
  Bearish (Gold)   → سلبي للذهب
  Neutral (Gold)   → محايد للذهب
  Bullish (USD)    → داعم للدولار
  Bearish (USD)    → ضاغط على الدولار
  Neutral (USD)    → محايد للدولار
  Federal Reserve  → الاحتياطي الفيدرالي
  Interest rates   → أسعار الفائدة
  Inflation        → التضخم
  Monetary policy  → السياسة النقدية
  Treasury yields  → عوائد السندات الأمريكية
  Safe haven       → الملاذ الآمن
  Market repricing → إعادة تسعير السوق
  Risk sentiment   → معنويات المخاطرة
  Rate hike        → رفع أسعار الفائدة
  Rate cut         → خفض أسعار الفائدة
  Gold             → الذهب
  Dollar / USD     → الدولار الأمريكي
  FOMC             → لجنة السوق المفتوحة الفيدرالية
  CPI              → مؤشر أسعار المستهلك
  NFP              → بيانات الوظائف خارج القطاع الزراعي
  PCE              → مؤشر نفقات الاستهلاك الشخصي
  PMI              → مؤشر مديري المشتريات
  GDP              → الناتج المحلي الإجمالي
  ECB              → البنك المركزي الأوروبي
  BOJ              → بنك اليابان

arabic_analysis field rules:
- 2-4 natural sentences of professional financial Arabic
- Explain macro direction and WHY (transmission mechanism in Arabic)
- Must be CONSISTENT with English classification (same tone, same direction)
- Never contradict the english usd_impact or gold_impact fields
- Natural financial Arabic — not word-for-word literal translation

CONSISTENCY ENFORCEMENT:
If tone=Hawkish then arabic_tone MUST be "تشدد نقدي"
If usd_impact=Bullish then arabic_usd_impact MUST be "داعم للدولار"
If usd_impact=Bearish then arabic_usd_impact MUST be "ضاغط على الدولار"
If gold_impact=Bullish then arabic_gold_impact MUST be "إيجابي للذهب"
If gold_impact=Bearish then arabic_gold_impact MUST be "سلبي للذهب"

════════════════════════════════════
OUTPUT RULES
════════════════════════════════════

NEVER use: "limited directional signal", "unclear impact", "it depends", "mixed signals"
ALWAYS explain the macro transmission mechanism in every reasoning field
ALWAYS ensure Arabic fields exactly mirror the English classification
Return ONLY a raw JSON object — no markdown, no code fences, no preamble, no explanation

Required JSON fields (all mandatory):
  category           — Federal Reserve | Economic Data | Geopolitical | Central Bank | Treasury & Yields | Gold & Commodities | Market News
  title              — concise English headline
  tone               — Hawkish | Dovish | Neutral
  usd_impact         — Bullish | Bearish | Neutral
  usd_reasoning      — 1-2 sentence macro transmission explanation
  gold_impact        — Bullish | Bearish | Neutral
  gold_reasoning     — 1-2 sentence macro transmission explanation
  macro_score        — integer 1 to 5
  macro_score_reason — one sentence justification
  tradability        — High Conviction | Moderate | Low Conviction | Non-Tradable
  tradability_reason — one sentence justification
  professional_analysis — 2-4 institutional-quality sentences, no generic phrases
  arabic_title       — professional Arabic headline
  arabic_tone        — exact Arabic from mandatory terminology list
  arabic_usd_impact  — exact Arabic from mandatory terminology list
  arabic_gold_impact — exact Arabic from mandatory terminology list
  arabic_analysis    — 2-4 sentences of natural professional financial Arabic"""


def _build_prompt(news_item: dict) -> str:
    return (
        f"Analyze this financial news for macro impact on USD and Gold (XAUUSD).\n\n"
        f"HEADLINE: {news_item.get('title', '')}\n"
        f"SUMMARY: {news_item.get('summary', '')}\n"
        f"SOURCE: {news_item.get('source', 'Unknown')}\n"
        f"PUBLISHED: {news_item.get('published_at', '')}\n"
        f"CATEGORY: {news_item.get('category', '')}\n\n"
        f"Return ONLY the JSON object. No explanation, no markdown fences."
    )


# ─────────────────────────────────────────────────────────────────────────────
# JSON CLEANER
# ─────────────────────────────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict:
    """Strip markdown fences and parse JSON. Raises ValueError on failure."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    # Grab outermost JSON object in case there's surrounding prose
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON parse failed: {e} | raw snippet: {text[:200]}")


# ─────────────────────────────────────────────────────────────────────────────
# PROVIDER IMPLEMENTATIONS
# ─────────────────────────────────────────────────────────────────────────────

def _call_gemini(prompt: str) -> tuple[dict, str]:
    """
    Gemini 2.0 Flash via REST.
    Uses responseMimeType=application/json for clean structured output.
    ~$0.00015/1K input tokens. Typical latency: 1-2s.
    """
    api_key = _get_key("GEMINI_API_KEY")
    url = f"{GEMINI_API_URL}?key={api_key}"

    payload = {
        "system_instruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": [
            {"parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1200,
            "responseMimeType": "application/json",
        },
    }

    t0 = time.time()
    resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    elapsed = round(time.time() - t0, 2)

    if resp.status_code != 200:
        err = resp.text[:400]
        raise RuntimeError(f"Gemini HTTP {resp.status_code}: {err}")

    data = resp.json()

    # Check for safety/content blocks
    candidate = data.get("candidates", [{}])[0]
    finish_reason = candidate.get("finishReason", "")
    if finish_reason in ("SAFETY", "RECITATION", "OTHER"):
        raise RuntimeError(f"Gemini blocked response: finishReason={finish_reason}")

    try:
        raw = candidate["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Gemini response structure: {data}")

    result = _parse_json(raw)
    print(f"[ANALYZER] ✅ Gemini Flash — {elapsed}s")
    return result, "Gemini Flash"


def _call_groq(prompt: str) -> tuple[dict, str]:
    """
    Groq / llama-3.3-70b via OpenAI-compatible API.
    Free tier available. Typical latency: 0.5-1.5s.
    """
    api_key = _get_key("GROQ_API_KEY")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
    }

    t0 = time.time()
    resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    elapsed = round(time.time() - t0, 2)

    if resp.status_code != 200:
        raise RuntimeError(f"Groq HTTP {resp.status_code}: {resp.text[:400]}")

    raw = resp.json()["choices"][0]["message"]["content"]
    result = _parse_json(raw)
    print(f"[ANALYZER] ✅ Groq ({GROQ_MODEL}) — {elapsed}s")
    return result, f"Groq/{GROQ_MODEL}"


def _call_openrouter(prompt: str) -> tuple[dict, str]:
    """
    OpenRouter / mistral-7b-instruct as last resort.
    """
    api_key = _get_key("OPENROUTER_API_KEY")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/Ismail12275/gold-news-2026",
        "X-Title": "Gold News Bot",
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
    }

    t0 = time.time()
    resp = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    elapsed = round(time.time() - t0, 2)

    if resp.status_code != 200:
        raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {resp.text[:400]}")

    raw = resp.json()["choices"][0]["message"]["content"]
    result = _parse_json(raw)
    print(f"[ANALYZER] ✅ OpenRouter ({OPENROUTER_MODEL}) — {elapsed}s")
    return result, f"OpenRouter/{OPENROUTER_MODEL}"


# ─────────────────────────────────────────────────────────────────────────────
# PROVIDER WATERFALL
# ─────────────────────────────────────────────────────────────────────────────

_PROVIDERS = [
    ("Gemini Flash",  "GEMINI_API_KEY",     _call_gemini),
    ("Groq",          "GROQ_API_KEY",       _call_groq),
    ("OpenRouter",    "OPENROUTER_API_KEY", _call_openrouter),
]


def _call_with_fallback(prompt: str) -> tuple[dict, str]:
    """
    Try each provider in priority order.
    Skips providers whose key is not set.
    Returns (result_dict, provider_name).
    Raises RuntimeError only if all available providers fail.
    """
    errors = []
    attempted = 0

    for name, env_var, fn in _PROVIDERS:
        if not os.environ.get(env_var, "").strip():
            continue  # key not configured — skip silently

        attempted += 1
        print(f"[ANALYZER] 🔄 Trying {name}...")
        try:
            return fn(prompt)
        except KeyError:
            print(f"[ANALYZER] ⏭  {name} key missing at call time — skipping")
            continue
        except ValueError as e:
            errors.append(f"{name}: {e}")
            print(f"[ANALYZER] ⚠️  {name} bad JSON: {e}")
        except RuntimeError as e:
            errors.append(f"{name}: {e}")
            print(f"[ANALYZER] ⚠️  {name} error: {e}")
        except requests.exceptions.Timeout:
            msg = f"{name}: timed out after {REQUEST_TIMEOUT}s"
            errors.append(msg)
            print(f"[ANALYZER] ⏱  {msg}")
        except requests.exceptions.RequestException as e:
            msg = f"{name}: network error — {e}"
            errors.append(msg)
            print(f"[ANALYZER] 🔌 {msg}")

    if attempted == 0:
        raise RuntimeError(
            "No API keys configured. Set at least GEMINI_API_KEY in Railway Variables.\n"
            "Get a free key at: https://aistudio.google.com/apikey"
        )

    summary = " | ".join(errors)
    raise RuntimeError(f"All {attempted} provider(s) exhausted. Errors: {summary}")


# ─────────────────────────────────────────────────────────────────────────────
# DEFAULTS FOR INCOMPLETE RESPONSES
# ─────────────────────────────────────────────────────────────────────────────

def _apply_defaults(analysis: dict) -> None:
    analysis.setdefault("category",             "Market News")
    analysis.setdefault("title",                analysis.get("_raw_title", ""))
    analysis.setdefault("tone",                 "Neutral")
    analysis.setdefault("usd_impact",           "Neutral")
    analysis.setdefault("usd_reasoning",        "Macro context insufficient for directional assessment.")
    analysis.setdefault("gold_impact",          "Neutral")
    analysis.setdefault("gold_reasoning",       "Macro context insufficient for directional assessment.")
    analysis.setdefault("macro_score",          1)
    analysis.setdefault("macro_score_reason",   "Default score — response incomplete.")
    analysis.setdefault("tradability",          "Low Conviction")
    analysis.setdefault("tradability_reason",   "Tradability uncertain due to incomplete analysis.")
    analysis.setdefault("professional_analysis","Analysis incomplete — review source directly.")
    analysis.setdefault("arabic_title",         analysis.get("_raw_title", ""))
    analysis.setdefault("arabic_tone",          "محايد")
    analysis.setdefault("arabic_usd_impact",    "محايد للدولار")
    analysis.setdefault("arabic_gold_impact",   "محايد للذهب")
    analysis.setdefault("arabic_analysis",      "التحليل غير مكتمل — يرجى مراجعة المصدر مباشرة.")


# ─────────────────────────────────────────────────────────────────────────────
# CORE PUBLIC FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def analyze_news(news_item: dict) -> dict | None:
    """
    Analyze a news item and return structured institutional analysis.

    Args:
        news_item: dict with keys: title, summary, source, published_at, category

    Returns:
        Full analysis dict, or None on unrecoverable failure.
    """
    title        = news_item.get("title", "").strip()
    source       = news_item.get("source", "Unknown")
    published_at = news_item.get("published_at", datetime.now(timezone.utc).isoformat())

    if not title:
        print("[ANALYZER] ⚠️  Skipping item with empty title")
        return None

    try:
        analysis, provider = _call_with_fallback(_build_prompt(news_item))
    except RuntimeError as e:
        print(f"[ANALYZER] ❌ {e}")
        return None
    except Exception as e:
        print(f"[ANALYZER] ❌ Unexpected: {type(e).__name__}: {e}")
        return None

    # Attach metadata
    analysis["_source"]       = source
    analysis["_published_at"] = published_at
    analysis["_raw_title"]    = title
    analysis["_raw_summary"]  = news_item.get("summary", "")
    analysis["_provider"]     = provider

    # Ensure macro_score is an int
    try:
        analysis["macro_score"] = int(analysis.get("macro_score", 1))
    except (ValueError, TypeError):
        analysis["macro_score"] = 1

    # Fill any missing fields
    required = ["tone", "usd_impact", "gold_impact", "macro_score",
                "tradability", "professional_analysis", "arabic_analysis"]
    if any(k not in analysis for k in required):
        missing = [k for k in required if k not in analysis]
        print(f"[ANALYZER] ⚠️  Missing fields from {provider}: {missing} — applying defaults")
        _apply_defaults(analysis)

    return analysis


# ─────────────────────────────────────────────────────────────────────────────
# EMOJI / FORMATTING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _tone_emoji(tone: str) -> str:
    return {"Hawkish": "🦅", "Dovish": "🕊", "Neutral": "⚖️"}.get(tone, "📊")


def _impact_emoji(impact: str) -> str:
    return {"Bullish": "🟢", "Bearish": "🔴", "Neutral": "🟡"}.get(impact, "⚪")


def _tradability_emoji(t: str) -> str:
    return {
        "High Conviction": "🔥",
        "Moderate":        "📈",
        "Low Conviction":  "🔅",
        "Non-Tradable":    "⛔",
    }.get(t, "📊")


def _score_bar(score: int) -> str:
    score = max(1, min(5, int(score)))
    return f"{'█' * score}{'░' * (5 - score)} {score}/5"


def _arabic_impact_fallback(impact: str, asset: str) -> str:
    if asset == "usd":
        return {
            "Bullish": "📈 داعم للدولار",
            "Bearish": "📉 ضاغط على الدولار",
            "Neutral": "➡️ محايد للدولار",
        }.get(impact, impact)
    return {
        "Bullish": "📈 إيجابي للذهب",
        "Bearish": "📉 سلبي للذهب",
        "Neutral": "➡️ محايد للذهب",
    }.get(impact, impact)


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM MESSAGE FORMATTER
# ─────────────────────────────────────────────────────────────────────────────

def format_telegram_message(analysis: dict) -> str:
    """
    Format analysis into a professional bilingual Telegram alert.
    Returns Markdown-compatible string safe for Telegram sendMessage.
    """
    tone        = analysis.get("tone",        "Neutral")
    usd         = analysis.get("usd_impact",  "Neutral")
    gold        = analysis.get("gold_impact", "Neutral")
    score       = int(analysis.get("macro_score", 1))
    tradability = analysis.get("tradability", "Low Conviction")

    # ── ENGLISH SECTION ───────────────────────────────────────────────────────
    en = [
        f"{'⚡' * min(score, 3)} *NEWS ALERT — MACRO IMPACT*",
        "",
        f"📌 *{analysis.get('category', 'Market News').upper()}*",
        f"📰 *{analysis.get('title', analysis.get('_raw_title', 'N/A'))}*",
        "",
        f"{_tone_emoji(tone)} *Tone:* `{tone}`",
        "",
        f"{_impact_emoji(usd)} *USD Impact:* `{usd}`",
        f"_{analysis.get('usd_reasoning', '')}_",
        "",
        f"{_impact_emoji(gold)} *Gold Impact:* `{gold}`",
        f"_{analysis.get('gold_reasoning', '')}_",
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

    # ── ARABIC SECTION ────────────────────────────────────────────────────────
    ar_tone = analysis.get("arabic_tone", tone)
    ar_usd  = analysis.get("arabic_usd_impact")  or _arabic_impact_fallback(usd,  "usd")
    ar_gold = analysis.get("arabic_gold_impact") or _arabic_impact_fallback(gold, "gold")

    ar = [
        "",
        "─────────────────────",
        "🔔 *تنبيه إخباري — تأثير السوق*",
        "",
        f"📌 *{analysis.get('category', 'أخبار السوق').upper()}*",
        f"📰 *{analysis.get('arabic_title', analysis.get('title', ''))}*",
        "",
        f"{_tone_emoji(tone)} *النبرة النقدية:* `{ar_tone}`",
        "",
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

    # ── FOOTER ────────────────────────────────────────────────────────────────
    ts = analysis.get("_published_at", datetime.now(timezone.utc).isoformat())
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        ts_fmt = dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        ts_fmt = str(ts)

    provider_tag = analysis.get("_provider", "AI")
    footer = [
        "",
        "─────────────────────",
        f"🕐 `{ts_fmt}`  |  📡 {analysis.get('_source', 'Unknown')}  |  🤖 {provider_tag}",
    ]

    return "\n".join(en + ar + footer)


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def analyze_and_format(news_item: dict) -> tuple[dict | None, str | None]:
    """
    Full pipeline: analyze then format.
    Returns (analysis_dict, telegram_message) or (None, None) on failure.
    """
    analysis = analyze_news(news_item)
    if not analysis:
        return None, None
    return analysis, format_telegram_message(analysis)
