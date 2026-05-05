"""
main.py — Orchestration loop: Fetch → Dedup → Analyze → Filter → Route → Send
=============================================================================
Alert flow:
  CRITICAL  → Send immediately, bypass hourly cap
  HIGH      → Send (subject to hourly cap)
  MEDIUM    → Send (subject to hourly cap)
  LOW       → Log only, never sent
  SUPPRESS  → Silently dropped
"""

import os
import sys
import time
import signal
import traceback
from datetime import datetime, timezone

from news_fetcher   import fetch_all_feeds, fetch_finnhub_news
from deduplicator   import filter_new, mark_seen, stats as dedup_stats
from analyzer       import analyze_and_format, validate_env
from signal_filter  import (
    evaluate_alert, record_sent, inject_priority_header,
    log_suppressed, log_cleared, get_hourly_stats,
    Priority,
)
from telegram_sender import send_alert, send_startup_message

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

POLL_INTERVAL  = int(os.environ.get("POLL_INTERVAL",  "300"))
MIN_PRIORITY   = int(os.environ.get("MIN_PRIORITY",   "1"))    # pre-analysis news filter
MAX_PER_CYCLE  = int(os.environ.get("MAX_PER_CYCLE",  "20"))   # items to analyze per cycle
INTER_SEND_DELAY = float(os.environ.get("INTER_SEND_DELAY", "2.0"))  # seconds between Telegram sends

# Tiers that get sent to Telegram (CRITICAL always included)
_SENDABLE_TIERS = {Priority.CRITICAL, Priority.HIGH, Priority.MEDIUM}

# ─────────────────────────────────────────────────────────────────────────────
# GRACEFUL SHUTDOWN
# ─────────────────────────────────────────────────────────────────────────────

_running = True

def _on_signal(sig, frame):
    global _running
    print(f"\n[MAIN] Signal {sig} received — finishing current cycle then stopping")
    _running = False

signal.signal(signal.SIGTERM, _on_signal)
signal.signal(signal.SIGINT,  _on_signal)


# ─────────────────────────────────────────────────────────────────────────────
# CYCLE
# ─────────────────────────────────────────────────────────────────────────────

def run_cycle() -> dict:
    """
    One full fetch → analyze → filter → send cycle.
    Returns stats dict for logging.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*56}")
    print(f"[MAIN] ⏱  Cycle start: {ts}")
    print(f"{'='*56}")

    stats = {
        "fetched": 0, "new": 0, "analyzed": 0,
        "critical": 0, "high": 0, "medium": 0,
        "low": 0, "suppressed": 0, "sent": 0, "failed": 0,
    }

    # ── 1. FETCH ──────────────────────────────────────────────────────────────
    items      = fetch_all_feeds(min_priority=MIN_PRIORITY)
    fin_items  = fetch_finnhub_news()
    all_items  = items + fin_items
    stats["fetched"] = len(all_items)
    print(f"[MAIN] 📡 Fetched {len(all_items)} candidates "
          f"({len(items)} RSS + {len(fin_items)} Finnhub)")

    # ── 2. DEDUPLICATE ────────────────────────────────────────────────────────
    new_items = filter_new(all_items)
    stats["new"] = len(new_items)
    print(f"[MAIN] 🔍 {len(new_items)} new after dedup "
          f"(dropped {len(all_items) - len(new_items)})")

    if not new_items:
        print("[MAIN] Nothing new this cycle.")
        return stats

    # Cap items sent to analyzer (most relevant first — already sorted by fetcher)
    to_analyze = new_items[:MAX_PER_CYCLE]
    if len(new_items) > MAX_PER_CYCLE:
        print(f"[MAIN] ✂️  Capped analysis at {MAX_PER_CYCLE}/{len(new_items)} items")

    # ── 3. ANALYZE + FILTER + SEND ───────────────────────────────────────────
    for item in to_analyze:
        if not _running:
            print("[MAIN] Shutdown flag — stopping mid-cycle")
            break

        title = item.get("title", "")[:72]
        print(f"\n[MAIN] 🔬 Analyzing: {title}…")

        # 3a. AI Analysis
        analysis, message = analyze_and_format(item)
        stats["analyzed"] += 1

        if not analysis or not message:
            print(f"[MAIN] ⚠️  Analysis failed — marking seen, skipping")
            mark_seen(item)
            continue

        score       = analysis.get("macro_score", 1)
        tradability = analysis.get("tradability", "?")
        tone        = analysis.get("tone", "?")
        category    = analysis.get("category", "?")
        provider    = analysis.get("_provider", "AI")
        print(f"[MAIN] 📊 Score={score}/5 | {tone} | {tradability} | {category} | via {provider}")

        # 3b. Signal filter evaluation
        result = evaluate_alert(analysis, item)
        tier   = result.priority

        # Count by tier
        tier_key = tier.value.lower()
        if tier_key in stats:
            stats[tier_key] += 1
        else:
            stats["suppressed"] += 1

        if not result.should_send:
            log_suppressed(item, result)
            mark_seen(item)
            continue

        log_cleared(item, result)

        # 3c. Inject priority badge into message
        final_message = inject_priority_header(message, tier)

        # 3d. Send to Telegram
        ok = send_alert(final_message)

        if ok:
            record_sent(analysis, item, result)
            mark_seen(item)
            stats["sent"] += 1
            print(f"[MAIN] ✅ Sent [{tier.value}] — total this cycle: {stats['sent']}")
            time.sleep(INTER_SEND_DELAY)
        else:
            stats["failed"] += 1
            print(f"[MAIN] ❌ Telegram send failed — will retry next cycle")
            # Don't mark_seen so it retries

    # ── 4. CYCLE SUMMARY ──────────────────────────────────────────────────────
    hourly = get_hourly_stats()
    print(f"\n[MAIN] ── Cycle Summary ───────────────────────────────")
    print(f"[MAIN]   Fetched={stats['fetched']} | New={stats['new']} | "
          f"Analyzed={stats['analyzed']}")
    print(f"[MAIN]   CRITICAL={stats['critical']} | HIGH={stats['high']} | "
          f"MEDIUM={stats['medium']} | LOW={stats['low']} | "
          f"Suppressed={stats['suppressed']}")
    print(f"[MAIN]   Sent={stats['sent']} | Failed={stats['failed']}")
    print(f"[MAIN]   Hourly: {hourly['alerts_this_hour']}/{hourly['max_per_hour']} | "
          f"Active clusters: {hourly['active_clusters']}")
    print(f"[MAIN] ──────────────────────────────────────────────────")

    return stats


# ─────────────────────────────────────────────────────────────────────────────
# STARTUP BANNER
# ─────────────────────────────────────────────────────────────────────────────

def _print_banner() -> None:
    ds = dedup_stats()
    hs = get_hourly_stats()
    print("=" * 56)
    print("🤖  Gold News Bot — Institutional Signal System")
    print("=" * 56)
    print(f"   Poll interval     : {POLL_INTERVAL}s ({POLL_INTERVAL//60}min)")
    print(f"   Max per cycle     : {MAX_PER_CYCLE} items analyzed")
    print(f"   Max per hour      : {hs['max_per_hour']} Telegram alerts")
    print(f"   Topic cluster TTL : {hs['cluster_window_h']}h")
    print(f"   Dedup cache       : {ds['cached_items']} items (TTL={ds['ttl_hours']}h)")
    print(f"   Send tiers        : CRITICAL, HIGH, MEDIUM")
    print(f"   Suppress tiers    : LOW (log), SUPPRESS (silent)")
    print("=" * 56)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    _print_banner()

    # Validate API keys — exit hard if none available
    if not validate_env():
        print("\n[MAIN] ❌ No AI provider keys configured.")
        print("   Set GEMINI_API_KEY in Railway Variables.")
        print("   Get a free key at: https://aistudio.google.com/apikey")
        sys.exit(1)

    send_startup_message()

    cycle_num = 0
    while _running:
        cycle_num += 1
        try:
            run_cycle()
        except Exception as e:
            print(f"[MAIN] ❌ Unhandled error in cycle {cycle_num}: {e}")
            traceback.print_exc()

        if not _running:
            break

        print(f"[MAIN] 💤 Sleeping {POLL_INTERVAL}s until next cycle…")
        for _ in range(POLL_INTERVAL):
            if not _running:
                break
            time.sleep(1)

    print("[MAIN] 🛑 Bot stopped cleanly.")
    sys.exit(0)


if __name__ == "__main__":
    main()
