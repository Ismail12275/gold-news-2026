"""
deduplicator.py — Persistent deduplication via hashed storage
Stores SHA-256 fingerprints of sent articles in storage.json.
Automatically prunes entries older than RETENTION_DAYS.
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STORAGE_FILE   = os.getenv("STORAGE_PATH", "storage.json")
RETENTION_DAYS = int(os.getenv("DEDUP_RETENTION_DAYS", 7))
RETENTION_SECS = RETENTION_DAYS * 86400


def _fingerprint(title: str, summary: str) -> str:
    """
    Compute a stable fingerprint from title + first 120 chars of summary.
    Using the title's first 120 chars makes partial-duplicate detection robust.
    """
    # Normalise: lower, strip punctuation, collapse whitespace
    import re
    clean_title   = re.sub(r"\W+", " ", (title or "").lower()).strip()[:120]
    clean_summary = re.sub(r"\W+", " ", (summary or "").lower()).strip()[:120]
    payload = f"{clean_title}||{clean_summary}"
    return hashlib.sha256(payload.encode()).hexdigest()


class Deduplicator:
    """Thread-safe (single-process) deduplication store backed by JSON."""

    def __init__(self, storage_path: str = STORAGE_FILE):
        self._path = Path(storage_path)
        self._store: dict[str, Any] = {}  # fingerprint → {"ts": float}
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            log.info("Storage file not found — starting fresh: %s", self._path)
            self._store = {}
            return
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
            # Support both legacy list format and current dict format
            if isinstance(raw, list):
                self._store = {h: {"ts": 0.0} for h in raw}
            elif isinstance(raw, dict):
                self._store = raw
            else:
                self._store = {}
            log.info("Loaded %d fingerprints from %s", len(self._store), self._path)
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Failed to load storage (%s) — starting fresh.", exc)
            self._store = {}

    def _save(self) -> None:
        try:
            tmp = self._path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(self._store, fh, indent=2)
            tmp.replace(self._path)
        except OSError as exc:
            log.error("Failed to save storage: %s", exc)

    # ── Pruning ───────────────────────────────────────────────────────────────

    def _prune(self) -> None:
        """Remove fingerprints older than RETENTION_DAYS."""
        cutoff = time.time() - RETENTION_SECS
        before = len(self._store)
        self._store = {
            fp: meta
            for fp, meta in self._store.items()
            if meta.get("ts", 0) >= cutoff
        }
        pruned = before - len(self._store)
        if pruned:
            log.info("Pruned %d old fingerprints from storage.", pruned)

    # ── Public API ────────────────────────────────────────────────────────────

    def is_duplicate(self, title: str, summary: str) -> bool:
        """Return True if this article was already sent."""
        fp = _fingerprint(title, summary)
        return fp in self._store

    def mark_sent(self, title: str, summary: str) -> None:
        """Record that this article has been sent."""
        fp = _fingerprint(title, summary)
        self._store[fp] = {
            "ts":    time.time(),
            "title": (title or "")[:100],
        }
        self._prune()
        self._save()
        log.debug("Marked as sent (fp=%s…): %s", fp[:12], title[:60])

    def count(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        """Wipe all stored fingerprints (admin use)."""
        self._store = {}
        self._save()
        log.warning("Storage cleared.")
