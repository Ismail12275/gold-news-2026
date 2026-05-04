"""
deduplicator.py — News deduplication with fuzzy title matching
Prevents re-sending the same story even if headline wording changes slightly
"""

import os
import json
import time
import hashlib
import re
from pathlib import Path
from difflib import SequenceMatcher

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
SEEN_FILE = os.environ.get("DEDUP_FILE", "seen_news.json")
TTL_HOURS = int(os.environ.get("DEDUP_TTL_HOURS", "24"))
SIMILARITY_THRESHOLD = 0.82  # 82% title similarity = duplicate


# ─────────────────────────────────────────────
# SEEN STORE
# ─────────────────────────────────────────────
def _load_seen() -> dict:
    """Load the seen-news store from disk."""
    try:
        if Path(SEEN_FILE).exists():
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_seen(seen: dict) -> None:
    """Persist seen-news store to disk."""
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[DEDUP] Save error: {e}")


def _evict_expired(seen: dict) -> dict:
    """Remove entries older than TTL_HOURS."""
    cutoff = time.time() - (TTL_HOURS * 3600)
    return {k: v for k, v in seen.items() if v.get("ts", 0) > cutoff}


# ─────────────────────────────────────────────
# DEDUP LOGIC
# ─────────────────────────────────────────────
def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation for fuzzy comparison."""
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


def _title_hash(title: str) -> str:
    return hashlib.sha256(_normalize_title(title).encode()).hexdigest()[:16]


def _is_similar(title_a: str, title_b: str) -> bool:
    """Check if two titles are semantically similar enough to be duplicates."""
    ratio = SequenceMatcher(
        None,
        _normalize_title(title_a),
        _normalize_title(title_b),
    ).ratio()
    return ratio >= SIMILARITY_THRESHOLD


def is_duplicate(news_item: dict) -> bool:
    """
    Check if a news item has already been sent.
    Uses exact hash + fuzzy title matching against recent history.

    Returns True if duplicate (should skip), False if new (should send).
    """
    title = news_item.get("title", "")
    if not title:
        return False

    seen = _load_seen()
    seen = _evict_expired(seen)

    exact_key = _title_hash(title)
    if exact_key in seen:
        return True

    # Fuzzy check against recent titles
    for entry in seen.values():
        if _is_similar(title, entry.get("title", "")):
            return True

    return False


def mark_sent(news_item: dict) -> None:
    """Record a news item as sent."""
    title = news_item.get("title", "")
    if not title:
        return

    seen = _load_seen()
    seen = _evict_expired(seen)

    key = _title_hash(title)
    seen[key] = {
        "title": title,
        "source": news_item.get("source", ""),
        "ts": time.time(),
    }

    _save_seen(seen)


def filter_new(news_items: list[dict]) -> list[dict]:
    """
    Filter a list of news items to only new (unseen) ones.
    Returns the filtered list.
    """
    new_items = []
    for item in news_items:
        if not is_duplicate(item):
            new_items.append(item)
        else:
            print(f"[DEDUP] Skipping duplicate: {item.get('title', '')[:60]}")
    return new_items


def clear_cache() -> None:
    """Clear the deduplication cache (for testing)."""
    try:
        Path(SEEN_FILE).unlink(missing_ok=True)
        print("[DEDUP] Cache cleared")
    except Exception as e:
        print(f"[DEDUP] Clear error: {e}")
