"""
deduplicator.py — News deduplication with fuzzy matching + topic memory
=======================================================================
Two-layer deduplication:
  1. Exact SHA-256 title hash (always checked)
  2. SequenceMatcher fuzzy title similarity (catches rephrased duplicates)

Separate from signal_filter.py topic clustering:
  - deduplicator.py = "have we SEEN this specific story?"
  - signal_filter.py = "have we SENT an alert on this TOPIC recently?"
"""

import os
import json
import time
import hashlib
import re
from pathlib import Path
from difflib import SequenceMatcher

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

SEEN_FILE           = os.environ.get("DEDUP_FILE",        "seen_news.json")
TTL_HOURS           = int(os.environ.get("DEDUP_TTL_HOURS",   "24"))
SIMILARITY_THRESHOLD = float(os.environ.get("DEDUP_SIMILARITY", "0.84"))


# ─────────────────────────────────────────────────────────────────────────────
# PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        p = Path(SEEN_FILE)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[DEDUP] Load warning: {e}")
    return {}


def _save(seen: dict) -> None:
    try:
        Path(SEEN_FILE).write_text(
            json.dumps(seen, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[DEDUP] Save error: {e}")


def _evict(seen: dict) -> dict:
    """Drop entries older than TTL_HOURS."""
    cutoff = time.time() - (TTL_HOURS * 3600)
    return {k: v for k, v in seen.items() if v.get("ts", 0) > cutoff}


# ─────────────────────────────────────────────────────────────────────────────
# NORMALIZATION & HASHING
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(title: str) -> str:
    """Strip punctuation, lower-case, collapse whitespace."""
    t = re.sub(r"[^\w\s]", "", title.lower())
    return re.sub(r"\s+", " ", t).strip()


def _hash(title: str) -> str:
    return hashlib.sha256(_normalize(title).encode("utf-8")).hexdigest()[:20]


def _similar(a: str, b: str) -> bool:
    return (
        SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()
        >= SIMILARITY_THRESHOLD
    )


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def is_duplicate(news_item: dict) -> bool:
    """
    Returns True (skip this item) if the title has been seen before,
    either by exact hash or by fuzzy similarity within the TTL window.
    """
    title = (news_item.get("title") or "").strip()
    if not title:
        return False

    seen = _evict(_load())

    # 1. Exact match
    if _hash(title) in seen:
        return True

    # 2. Fuzzy match against recent titles
    for entry in seen.values():
        if _similar(title, entry.get("title", "")):
            return True

    return False


def mark_seen(news_item: dict) -> None:
    """
    Record that we have processed this news item (seen, not necessarily sent).
    Call this for every item we analyze — sent or suppressed.
    """
    title = (news_item.get("title") or "").strip()
    if not title:
        return

    seen = _evict(_load())
    seen[_hash(title)] = {
        "title":  title,
        "source": news_item.get("source", ""),
        "ts":     time.time(),
    }
    _save(seen)


def mark_sent(news_item: dict) -> None:
    """Alias kept for backward compatibility with main.py."""
    mark_seen(news_item)


def filter_new(news_items: list[dict]) -> list[dict]:
    """
    Return only items not yet seen. Logs duplicates at DEBUG level.
    """
    # Load once, filter in memory
    seen  = _evict(_load())
    fresh = []

    for item in news_items:
        title = (item.get("title") or "").strip()
        if not title:
            continue

        if _hash(title) in seen:
            print(f"[DEDUP] ♻️  exact   | {title[:65]}")
            continue

        if any(_similar(title, e.get("title", "")) for e in seen.values()):
            print(f"[DEDUP] ♻️  fuzzy   | {title[:65]}")
            continue

        fresh.append(item)

    return fresh


def stats() -> dict:
    seen = _evict(_load())
    return {
        "cached_items": len(seen),
        "ttl_hours":    TTL_HOURS,
        "threshold":    SIMILARITY_THRESHOLD,
    }


def clear_cache() -> None:
    try:
        Path(SEEN_FILE).unlink(missing_ok=True)
        print("[DEDUP] Cache cleared")
    except Exception as e:
        print(f"[DEDUP] Clear error: {e}")
