"""
signal_filter.py — Institutional Signal Filtering & Alert Routing Engine
=========================================================================
Transforms raw AI analysis into tiered, suppressed, deduplicated alerts.

PRIORITY TIERS:
  CRITICAL  — FOMC decisions, CPI/NFP shocks, geopolitical escalation
  HIGH      — Fed speakers with policy shift, PCE, Treasury yield spikes
  MEDIUM    — Moderate macro relevance, actionable but not urgent
  LOW       — Log only. Never sent to Telegram.
  SUPPRESS  — Silently dropped. Not even logged to file.

SUPPRESSION RULES:
  - CEO / executive commentary
  - Regional PMIs (non-Fed)
  - Generic market wraps
  - Minor FX moves
  - Duplicate topic within configurable window (default 5h)
  - Hourly alert cap enforcement
"""

import os
import re
import time
import json
import hashlib
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum

# ─────────────────────────────────────────────────────────────────────────────
# ENUMS & DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

class Priority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"
    SUPPRESS = "SUPPRESS"


@dataclass
class FilterResult:
    priority:         Priority
    should_send:      bool
    suppress_reason:  str        = ""
    topic_cluster:    str        = ""
    priority_reason:  str        = ""


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION  (all overridable via env vars)
# ─────────────────────────────────────────────────────────────────────────────

# Spam control
MAX_ALERTS_PER_HOUR    = int(os.environ.get("MAX_ALERTS_PER_HOUR",    "6"))
TOPIC_CLUSTER_HOURS    = float(os.environ.get("TOPIC_CLUSTER_HOURS",  "5"))
TOPIC_SIM_THRESHOLD    = float(os.environ.get("TOPIC_SIM_THRESHOLD",  "0.72"))

# Score / tradability minimums per tier
MIN_SCORE_CRITICAL     = 5
MIN_SCORE_HIGH         = 4
MIN_SCORE_MEDIUM       = 3
MIN_SCORE_LOW          = 2       # 1 is always SUPPRESS

# State file for hourly cap + topic clusters
FILTER_STATE_FILE = os.environ.get("FILTER_STATE_FILE", "filter_state.json")


# ─────────────────────────────────────────────────────────────────────────────
# HARD SUPPRESS PATTERNS
# These patterns cause instant SUPPRESS regardless of AI score.
# ─────────────────────────────────────────────────────────────────────────────

# Title / summary fragments that are always noise
_SUPPRESS_PATTERNS = [
    # Executive commentary without policy signal
    r"\bceo\b",
    r"\bchief executive\b",
    r"\bchairman says\b",
    r"\bpresident of\b.*\bsays\b",
    r"\bspeaking at\b.*\bconference\b",
    r"\bkeynote\b",
    r"\bawards\b",

    # Generic market wraps
    r"markets? wrap",
    r"morning brief",
    r"evening brief",
    r"week ahead",
    r"market update",
    r"daily roundup",
    r"what to watch",
    r"five things",
    r"things to know",

    # Minor / regional data with no Fed relevance
    r"\bregional pmi\b",
    r"\bchicago pmi\b",
    r"\bphiladelphia fed\b.*\bindex\b",
    r"\brichmond fed\b.*\bindex\b",
    r"\bdallas fed\b.*\bindex\b",
    r"\bkansas city fed\b.*\bindex\b",
    r"\bhouston pmi\b",

    # FX minor moves
    r"eur/usd (rises|falls|slips|gains) \d+",
    r"gbp/usd (rises|falls|slips|gains) \d+",
    r"minor (gains|losses) in",
    r"little changed",
    r"edges (higher|lower)",
    r"tepid (gains|losses)",

    # Corporate / equity noise
    r"\bearnings (beat|miss|report)\b",
    r"\bdividend\b",
    r"\bstock split\b",
    r"\bshare (buyback|repurchase)\b",
    r"\bm&a\b",
    r"\bacquisition\b",
    r"\bmerger\b",
    r"\bipo\b",
    r"\bcrypto\b",
    r"\bbitcoin\b",
    r"\bethereum\b",
    r"\bnft\b",

    # Completely off-topic
    r"\bweather\b",
    r"\bsports\b",
    r"\bcelebrit",
    r"\blifestyle\b",
    r"\brecipe\b",
    r"\bhoroscope\b",
]

_SUPPRESS_RE = re.compile("|".join(_SUPPRESS_PATTERNS), re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
# CRITICAL / HIGH CATALYST PATTERNS
# These patterns can ELEVATE score or override weak AI scoring.
# ─────────────────────────────────────────────────────────────────────────────

_CRITICAL_PATTERNS = [
    r"\bfomc\b",
    r"\bfed(eral reserve)? (decision|rate decision|cuts|hikes|holds|raises|lowers)\b",
    r"\brate (decision|cut|hike|hold)\b",
    r"\bemergency (rate|meeting|cut)\b",
    r"\bcpi (report|data|print|release|surprise)\b",
    r"\bnfp\b",
    r"\bnon.?farm payroll",
    r"\bjobs report\b",
    r"\bpayroll (surprise|miss|beat|shock)\b",
    r"\bpce (inflation|data|print)\b",
    r"\bpivot\b.*\b(fed|federal reserve|powell)\b",
    r"\bgeopolit.*\bescalat",
    r"\bnuclear\b",
    r"\bwar\b.*\b(escalat|expand|spread|major)\b",
    r"\bstrike\b.*\b(iran|israel|russia|china|nato)\b",
    r"\bsanction\b.*\b(oil|russia|iran|china)\b",
    r"\bdefault\b.*\b(sovereign|government|treasury)\b",
]

_HIGH_PATTERNS = [
    r"\bpowell\b",
    r"\bfed (speaker|official|governor|president)\b",
    r"\b(waller|goolsbee|jefferson|williams|daly|kashkari|barkin|bostic|cook)\b",
    r"\byield (spike|surge|jump|crash|collapse)\b",
    r"\b10.?year (yield|treasury)\b.*(spike|surge|high|low|hit)\b",
    r"\btreasury (selloff|rally|yield)\b",
    r"\bpce\b",
    r"\bcore inflation\b",
    r"\bpmi\b.*(shock|surprise|miss|beat|lowest|highest)\b",
    r"\bism\b.*(shock|surprise|miss|beat)\b",
    r"\bgold\b.*(record|all.?time|surge|crash|spike|plunge)\b",
    r"\bxauusd\b",
    r"\bdxy\b.*(spike|surge|crash|plunge)\b",
    r"\boil\b.*(spike|surge|embargo|cut)\b",
    r"\bopec\b",
    r"\becb\b.*(decision|rate|hike|cut)\b",
    r"\bboe\b.*(decision|rate|hike|cut)\b",
    r"\bboj\b.*(decision|rate|intervention|yen)\b",
]

_CRITICAL_RE = re.compile("|".join(_CRITICAL_PATTERNS), re.IGNORECASE)
_HIGH_RE     = re.compile("|".join(_HIGH_PATTERNS),    re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
# TOPIC CLUSTER KEYS
# Groups semantically related news into a single topic window.
# ─────────────────────────────────────────────────────────────────────────────

_TOPIC_CLUSTERS = {
    "fomc_decision":     [r"\bfomc\b", r"\brate decision\b", r"\bfed decides\b"],
    "fed_powell":        [r"\bpowell\b"],
    "fed_speakers":      [r"\b(waller|goolsbee|jefferson|williams|daly|kashkari|barkin|bostic|cook)\b"],
    "cpi_data":          [r"\bcpi\b", r"\bconsumer price\b"],
    "nfp_jobs":          [r"\bnfp\b", r"\bnon.?farm\b", r"\bjobs report\b", r"\bpayroll\b"],
    "pce_data":          [r"\bpce\b"],
    "treasury_yields":   [r"\b(10.?year|2.?year|30.?year)\b.*\byield\b", r"\btreasury yield\b"],
    "gold_move":         [r"\bgold\b.*(surge|plunge|spike|record|crash)", r"\bxauusd\b"],
    "oil_opec":          [r"\bopec\b", r"\boil\b.*(cut|embargo|spike)"],
    "ecb_policy":        [r"\becb\b.*(rate|decision|lagarde|hike|cut)"],
    "boj_policy":        [r"\bboj\b", r"\bjapan.*rate\b", r"\byen\b.*(intervention|boj)"],
    "geopolitical":      [r"\b(iran|israel|russia|ukraine|china|taiwan)\b.*(war|attack|strike|escalat|sanction)"],
}

_TOPIC_RES = {
    topic: re.compile("|".join(patterns), re.IGNORECASE)
    for topic, patterns in _TOPIC_CLUSTERS.items()
}


def _infer_topic_cluster(text: str) -> str:
    """Return the most specific matching topic cluster key, or '' if none."""
    for topic, pattern in _TOPIC_RES.items():
        if pattern.search(text):
            return topic
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# STATE MANAGEMENT  (hourly cap + topic cluster memory)
# ─────────────────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        p = Path(FILTER_STATE_FILE)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"hourly": {}, "clusters": {}}


def _save_state(state: dict) -> None:
    try:
        Path(FILTER_STATE_FILE).write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        print(f"[FILTER] State save error: {e}")


def _current_hour_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")


def _get_hourly_count(state: dict) -> int:
    hour = _current_hour_key()
    return state.get("hourly", {}).get(hour, 0)


def _increment_hourly(state: dict) -> None:
    hour = _current_hourly_key() if False else _current_hour_key()
    state.setdefault("hourly", {})[hour] = state["hourly"].get(hour, 0) + 1
    # Prune hours older than 48h
    cutoff = datetime.now(timezone.utc).timestamp() - 172800
    state["hourly"] = {
        k: v for k, v in state["hourly"].items()
        if _hour_key_to_ts(k) > cutoff
    }


def _hour_key_to_ts(key: str) -> float:
    try:
        return datetime.strptime(key, "%Y-%m-%dT%H").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except Exception:
        return 0.0


def _cluster_recently_sent(state: dict, cluster: str) -> bool:
    """Return True if this topic cluster was sent within TOPIC_CLUSTER_HOURS."""
    if not cluster:
        return False
    sent_ts = state.get("clusters", {}).get(cluster, 0)
    return (time.time() - sent_ts) < (TOPIC_CLUSTER_HOURS * 3600)


def _record_cluster_sent(state: dict, cluster: str) -> None:
    if cluster:
        state.setdefault("clusters", {})[cluster] = time.time()


# ─────────────────────────────────────────────────────────────────────────────
# TITLE SIMILARITY  (catches rephrased duplicate themes)
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _titles_similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio() >= TOPIC_SIM_THRESHOLD


# ─────────────────────────────────────────────────────────────────────────────
# PRIORITY SCORING
# ─────────────────────────────────────────────────────────────────────────────

def _compute_priority(analysis: dict, text: str) -> tuple[Priority, str]:
    """
    Combine AI macro_score + keyword pattern matching to assign final priority.
    Pattern matches can ELEVATE but not lower AI-assigned priority.
    """
    score       = int(analysis.get("macro_score", 1))
    tradability = analysis.get("tradability", "Non-Tradable")
    category    = analysis.get("category", "")

    # Pattern-based elevation
    if _CRITICAL_RE.search(text):
        if score >= 4 or tradability in ("High Conviction", "Moderate"):
            return Priority.CRITICAL, "Critical catalyst pattern matched + AI score confirms"
        if score == 3:
            return Priority.HIGH, "Critical catalyst pattern + moderate AI score"

    if _HIGH_RE.search(text):
        if score >= 4:
            return Priority.HIGH, "High-impact pattern + strong AI score"
        if score == 3 and tradability in ("High Conviction", "Moderate"):
            return Priority.HIGH, "High-impact pattern + tradable AI classification"

    # Pure AI score-based
    if score == 5:
        return Priority.CRITICAL, f"AI macro_score=5 ({tradability})"
    if score == 4:
        if tradability in ("High Conviction", "Moderate"):
            return Priority.HIGH, f"AI score=4 + {tradability}"
        return Priority.MEDIUM, f"AI score=4 but {tradability} — downgraded"
    if score == 3:
        if tradability in ("High Conviction", "Moderate"):
            return Priority.MEDIUM, f"AI score=3 + {tradability}"
        return Priority.LOW, f"AI score=3 but {tradability}"
    if score == 2:
        return Priority.LOW, f"AI score=2 — informational only"

    return Priority.SUPPRESS, f"AI score={score} — below threshold"


# ─────────────────────────────────────────────────────────────────────────────
# MAIN FILTER FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_alert(analysis: dict, news_item: dict) -> FilterResult:
    """
    Full evaluation pipeline for a single analyzed news item.

    Args:
        analysis:  dict returned by analyzer.analyze_news()
        news_item: original raw news item dict

    Returns:
        FilterResult with priority, send decision, and reasoning.
    """
    title   = news_item.get("title", "")
    summary = news_item.get("summary", "")
    text    = f"{title} {summary}"

    # ── STAGE 1: Hard suppress check ─────────────────────────────────────────
    if _SUPPRESS_RE.search(text):
        return FilterResult(
            priority=Priority.SUPPRESS,
            should_send=False,
            suppress_reason=f"Matched hard-suppress pattern",
        )

    # ── STAGE 2: Priority assignment ─────────────────────────────────────────
    priority, priority_reason = _compute_priority(analysis, text)

    if priority == Priority.SUPPRESS:
        return FilterResult(
            priority=Priority.SUPPRESS,
            should_send=False,
            suppress_reason=priority_reason,
        )

    if priority == Priority.LOW:
        return FilterResult(
            priority=Priority.LOW,
            should_send=False,
            suppress_reason="LOW priority — logged only, not sent to Telegram",
            priority_reason=priority_reason,
        )

    # ── STAGE 3: Topic cluster dedup ─────────────────────────────────────────
    cluster = _infer_topic_cluster(text)
    state   = _load_state()

    if cluster and _cluster_recently_sent(state, cluster):
        # Only suppress if it's not a CRITICAL escalation
        if priority != Priority.CRITICAL:
            return FilterResult(
                priority=priority,
                should_send=False,
                suppress_reason=(
                    f"Topic cluster '{cluster}' already sent within "
                    f"{TOPIC_CLUSTER_HOURS}h window"
                ),
                topic_cluster=cluster,
                priority_reason=priority_reason,
            )

    # ── STAGE 4: Hourly cap ───────────────────────────────────────────────────
    hourly_count = _get_hourly_count(state)
    if hourly_count >= MAX_ALERTS_PER_HOUR:
        if priority not in (Priority.CRITICAL,):
            return FilterResult(
                priority=priority,
                should_send=False,
                suppress_reason=(
                    f"Hourly cap reached ({hourly_count}/{MAX_ALERTS_PER_HOUR}) "
                    f"— CRITICAL alerts bypass this limit"
                ),
                topic_cluster=cluster,
                priority_reason=priority_reason,
            )

    # ── STAGE 5: PASS — alert cleared for sending ─────────────────────────────
    return FilterResult(
        priority=priority,
        should_send=True,
        topic_cluster=cluster,
        priority_reason=priority_reason,
    )


def record_sent(analysis: dict, news_item: dict, filter_result: FilterResult) -> None:
    """
    Record that an alert was sent — update hourly counter and cluster timestamp.
    Call this ONLY after a successful Telegram send.
    """
    state = _load_state()
    _increment_hourly(state)
    if filter_result.topic_cluster:
        _record_cluster_sent(state, filter_result.topic_cluster)
    _save_state(state)


# ─────────────────────────────────────────────────────────────────────────────
# PRIORITY HEADER FORMATTER
# Injects tier badge into Telegram message header
# ─────────────────────────────────────────────────────────────────────────────

_PRIORITY_HEADERS = {
    Priority.CRITICAL: "🚨🚨🚨 *CRITICAL MACRO ALERT*",
    Priority.HIGH:     "🔴 *HIGH PRIORITY ALERT*",
    Priority.MEDIUM:   "🟡 *MACRO ALERT*",
    Priority.LOW:      "⚪ *LOW SIGNAL* _(log only)_",
}

_PRIORITY_AR_HEADERS = {
    Priority.CRITICAL: "🚨🚨🚨 *تنبيه ماكرو حرج*",
    Priority.HIGH:     "🔴 *تنبيه أولوية عالية*",
    Priority.MEDIUM:   "🟡 *تنبيه ماكرو*",
    Priority.LOW:      "⚪ *إشارة منخفضة*",
}


def inject_priority_header(message: str, priority: Priority) -> str:
    """
    Replace the generic first line of a Telegram message with a tier-stamped header.
    """
    en_header = _PRIORITY_HEADERS.get(priority, "📊 *MACRO ALERT*")
    ar_header = _PRIORITY_AR_HEADERS.get(priority, "📊 *تنبيه ماكرو*")

    lines = message.split("\n")

    # Replace first line (EN alert header)
    if lines:
        lines[0] = en_header

    # Find and replace Arabic header line
    for i, line in enumerate(lines):
        if "تنبيه إخباري" in line or "تنبيه ماكرو" in line or "HIGH IMPACT" in line:
            lines[i] = ar_header
            break

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def log_suppressed(news_item: dict, result: FilterResult) -> None:
    """Log suppressed items cleanly without cluttering output."""
    title = news_item.get("title", "")[:70]
    if result.priority == Priority.SUPPRESS:
        print(f"[FILTER] ⛔ SUPPRESS | {title}")
        print(f"         Reason: {result.suppress_reason}")
    elif result.priority == Priority.LOW:
        print(f"[FILTER] 🔅 LOW     | {title}")
        print(f"         Reason: {result.suppress_reason}")
    else:
        print(f"[FILTER] 🚫 BLOCKED | [{result.priority.value}] {title}")
        print(f"         Reason: {result.suppress_reason}")


def log_cleared(news_item: dict, result: FilterResult) -> None:
    """Log alerts that passed all filters."""
    title   = news_item.get("title", "")[:70]
    cluster = f" [cluster: {result.topic_cluster}]" if result.topic_cluster else ""
    print(f"[FILTER] ✅ {result.priority.value:<8} | {title}{cluster}")
    print(f"         Reason: {result.priority_reason}")


def get_hourly_stats() -> dict:
    """Return current filter state stats for startup/status messages."""
    state = _load_state()
    hour  = _current_hour_key()
    return {
        "alerts_this_hour": state.get("hourly", {}).get(hour, 0),
        "max_per_hour":     MAX_ALERTS_PER_HOUR,
        "active_clusters":  [
            k for k, ts in state.get("clusters", {}).items()
            if (time.time() - ts) < (TOPIC_CLUSTER_HOURS * 3600)
        ],
        "cluster_window_h": TOPIC_CLUSTER_HOURS,
    }
