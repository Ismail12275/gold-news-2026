"""
telegram_sender.py — Professional Telegram message delivery
Handles Unicode Arabic, Markdown formatting, chunking, and retry logic
"""

import os
import time
import requests
from typing import Optional

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Telegram message char limit
TELEGRAM_MAX_CHARS = 4096

# Retry config
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


# ─────────────────────────────────────────────
# CORE SEND FUNCTION
# ─────────────────────────────────────────────
def send_message(
    message: str,
    chat_id: Optional[str] = None,
    parse_mode: str = "Markdown",
    disable_web_preview: bool = True,
) -> bool:
    """
    Send a Telegram message with retry logic and chunking for long messages.

    Args:
        message: The message text (supports Markdown and Unicode/Arabic)
        chat_id: Override the default chat ID
        parse_mode: "Markdown" or "HTML"
        disable_web_preview: Disable link previews

    Returns:
        True if all chunks sent successfully, False otherwise
    """
    if not TELEGRAM_BOT_TOKEN or not (chat_id or TELEGRAM_CHAT_ID):
        print("[TELEGRAM] Missing BOT_TOKEN or CHAT_ID — skipping send")
        return False

    target_chat = chat_id or TELEGRAM_CHAT_ID

    # Split long messages into chunks at natural line breaks
    chunks = _split_message(message, TELEGRAM_MAX_CHARS)

    all_ok = True
    for i, chunk in enumerate(chunks):
        ok = _send_with_retry(
            chunk,
            target_chat,
            parse_mode,
            disable_web_preview,
            attempt_label=f"chunk {i+1}/{len(chunks)}",
        )
        if not ok:
            all_ok = False
        if len(chunks) > 1:
            time.sleep(0.5)  # Avoid rate limiting between chunks

    return all_ok


def _send_with_retry(
    text: str,
    chat_id: str,
    parse_mode: str,
    disable_web_preview: bool,
    attempt_label: str = "",
) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_preview,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            data = resp.json()

            if data.get("ok"):
                print(f"[TELEGRAM] ✅ Sent {attempt_label}")
                return True

            error_code = data.get("error_code", 0)
            description = data.get("description", "Unknown error")

            # If Markdown parsing failed, retry as plain text
            if error_code == 400 and "parse" in description.lower() and parse_mode != "":
                print(f"[TELEGRAM] ⚠️ Markdown parse error — retrying as plain text")
                payload["parse_mode"] = ""
                continue

            # Rate limited — back off
            if error_code == 429:
                retry_after = data.get("parameters", {}).get("retry_after", 5)
                print(f"[TELEGRAM] Rate limited — waiting {retry_after}s")
                time.sleep(retry_after)
                continue

            print(f"[TELEGRAM] ❌ API error {error_code}: {description}")
            return False

        except requests.exceptions.Timeout:
            print(f"[TELEGRAM] ⏱ Timeout on attempt {attempt}/{MAX_RETRIES}")
        except requests.exceptions.RequestException as e:
            print(f"[TELEGRAM] 🔌 Network error on attempt {attempt}/{MAX_RETRIES}: {e}")

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY * attempt)

    print(f"[TELEGRAM] ❌ All {MAX_RETRIES} attempts failed for {attempt_label}")
    return False


def _split_message(message: str, max_chars: int) -> list[str]:
    """
    Split a message into chunks no larger than max_chars,
    splitting at newlines to preserve formatting.
    """
    if len(message) <= max_chars:
        return [message]

    chunks = []
    lines = message.split("\n")
    current = ""

    for line in lines:
        test = current + "\n" + line if current else line
        if len(test) > max_chars:
            if current:
                chunks.append(current)
            current = line
        else:
            current = test

    if current:
        chunks.append(current)

    return chunks


# ─────────────────────────────────────────────
# ALERT SENDER (high-level interface)
# ─────────────────────────────────────────────
def send_alert(formatted_message: str, chat_id: Optional[str] = None) -> bool:
    """
    Send a formatted news alert to Telegram.
    Handles Arabic Unicode and Markdown gracefully.
    """
    # Ensure proper Unicode — Python handles this natively,
    # but we explicitly encode/decode to avoid any transit corruption
    if isinstance(formatted_message, bytes):
        formatted_message = formatted_message.decode("utf-8")

    return send_message(formatted_message, chat_id=chat_id)


def send_startup_message() -> None:
    """Send a startup notification to the channel."""
    msg = (
        "🤖 *Gold News Bot — Active*\n"
        "Monitoring macro news for XAUUSD & USD\n"
        "─────────────────────\n"
        "_Institutional-grade bilingual alerts enabled_"
    )
    send_message(msg)
