"""
main.py — Orchestration loop for Gold/Forex News Alert Bot
Fetches → Deduplicates → Analyzes → Sends bilingual alerts
"""

import os
import time
import signal
import sys
from datetime import datetime, timezone

from news_fetcher import fetch_all_feeds, fetch_finnhub_news
from deduplicator import filter_new, mark_sent
from analyzer import analyze_and_format, validate_env
from telegram_sender import send_alert, send_startup_message

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL", "300"))   # 5 min default
MIN_MACRO_SCORE = int(os.environ.get("MIN_MACRO_SCORE", "2"))          # Skip score 1 (info only)
MIN_PRIORITY = int(os.environ.get("MIN_PRIORITY", "1"))                # 1=moderate, 2=high only
SEND_NON_TRADABLE = os.environ.get("SEND_NON_TRADABLE", "false").lower() == "true"
MAX_ALERTS_PER_CYCLE = int(os.environ.get("MAX_ALERTS_PER_CYCLE", "5"))  # Prevent spam

# ─────────────────────────────────────────────
# GRACEFUL SHUTDOWN
# ─────────────────────────────────────────────
_running = True

def _handle_signal(sig, frame):
    global _running
    print("\n[MAIN] Shutdown signal received — stopping after current cycle")
    _running = False

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ─────────────────────────────────────────────
# FILTER LOGIC
# ─────────────────────────────────────────────
def should_send(analysis: dict) -> bool:
    """
    Decide whether to send this alert based on quality thresholds.
    """
    score = analysis.get("macro_score", 1)
    tradability = analysis.get("tradability", "Non-Tradable")

    # Always skip pure noise
    if score < MIN_MACRO_SCORE:
        return False

    # Optionally suppress non-tradable
    if not SEND_NON_TRADABLE and tradability == "Non-Tradable":
        return False

    return True


# ─────────────────────────────────────────────
# SINGLE PROCESSING CYCLE
# ─────────────────────────────────────────────
def run_cycle() -> int:
    """
    Fetch → filter → analyze → send one full cycle.
    Returns number of alerts sent.
    """
    print(f"\n[MAIN] ⏱ Cycle start: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    # 1. Fetch news
    items = fetch_all_feeds(min_priority=MIN_PRIORITY)
    finnhub_items = fetch_finnhub_news()
    items = items + finnhub_items

    print(f"[MAIN] Fetched {len(items)} candidate items")

    # 2. Deduplicate
    new_items = filter_new(items)
    print(f"[MAIN] {len(new_items)} new items after deduplication")

    # 3. Cap per cycle (prioritize high score items)
    # We process in priority order; analyzer assigns macro_score
    new_items = new_items[:MAX_ALERTS_PER_CYCLE * 3]  # Allow headroom for filtering

    sent_count = 0

    for item in new_items:
        if sent_count >= MAX_ALERTS_PER_CYCLE:
            print(f"[MAIN] Reached cycle cap ({MAX_ALERTS_PER_CYCLE}) — stopping")
            break

        title = item.get("title", "")[:80]
        print(f"[MAIN] Analyzing: {title}...")

        # 4. Analyze
        analysis, message = analyze_and_format(item)

        if not analysis or not message:
            print(f"[MAIN] ⚠️ Analysis failed — skipping")
            continue

        score = analysis.get("macro_score", 1)
        tradability = analysis.get("tradability", "?")
        tone = analysis.get("tone", "?")
        print(f"[MAIN] → Score: {score}/5 | {tone} | {tradability}")

        # 5. Quality gate
        if not should_send(analysis):
            print(f"[MAIN] 🚫 Filtered (score={score}, tradability={tradability})")
            # Still mark as seen to avoid re-analyzing
            mark_sent(item)
            continue

        # 6. Send
        ok = send_alert(message)

        if ok:
            mark_sent(item)
            sent_count += 1
            print(f"[MAIN] ✅ Alert sent ({sent_count}/{MAX_ALERTS_PER_CYCLE})")
            time.sleep(1.5)  # Brief pause between sends
        else:
            print(f"[MAIN] ❌ Send failed — item will retry next cycle")

    print(f"[MAIN] Cycle complete: {sent_count} alerts sent")
    return sent_count


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────
def main():
    print("=" * 50)
    print("🤖 Gold News Alert Bot — Starting")
    print(f"   Poll interval  : {POLL_INTERVAL_SECONDS}s")
    print(f"   Min macro score: {MIN_MACRO_SCORE}/5")
    print(f"   Min priority   : {MIN_PRIORITY}")
    print(f"   Max per cycle  : {MAX_ALERTS_PER_CYCLE}")
    print("=" * 50)

    # ── Critical env check before doing anything ──
    if not validate_env():
        print("\n💡 HOW TO FIX ON RAILWAY:")
        print("   1. Go to your Railway project")
        print("   2. Click your service → Variables tab")
        print("   3. Add: ANTHROPIC_API_KEY = sk-ant-...")
        print("   4. Railway will auto-redeploy\n")
        sys.exit(1)

    send_startup_message()

    while _running:
        try:
            run_cycle()
        except Exception as e:
            print(f"[MAIN] ❌ Unhandled cycle error: {e}")
            import traceback
            traceback.print_exc()

        if not _running:
            break

        print(f"[MAIN] 💤 Sleeping {POLL_INTERVAL_SECONDS}s...")
        # Sleep in small increments to allow clean shutdown
        for _ in range(POLL_INTERVAL_SECONDS):
            if not _running:
                break
            time.sleep(1)

    print("[MAIN] Stopped cleanly")
    sys.exit(0)


if __name__ == "__main__":
    main()
