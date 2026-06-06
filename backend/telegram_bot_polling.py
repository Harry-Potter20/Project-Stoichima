"""
Telegram bot long-polling worker — alternative to webhook for local development.

Why this exists
---------------
The webhook handler in api/routes/telegram.py needs a public HTTPS URL
(ngrok/deploy) to receive Telegram updates. For local development without a
tunnel, this worker uses Telegram's long-poll API (getUpdates) to fetch new
messages every few seconds and dispatch them to the same command handlers.

Run alongside the FastAPI server:
    cd backend && python telegram_bot_polling.py

The worker maintains its own offset cursor in saved_models/telegram_offset.txt
so it doesn't redeliver messages on restart.

Both modes (webhook AND polling) can be safe to run simultaneously — Telegram
auto-disables webhook delivery for any update read via getUpdates.
"""
from __future__ import annotations
import json
import logging
import os
import time
import urllib.request
from pathlib import Path

from app.config import get_settings

# Reuse the same command handlers used by the webhook route
from api.routes.telegram import (
    _send,
    _cmd_predictions, _cmd_today, _cmd_match, _cmd_bankroll,
    _cmd_slip, _cmd_best, _cmd_accuracy, _cmd_clv, _cmd_live, _cmd_props,
    _cmd_results,
    HELP_TEXT,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("telegram-poller")

OFFSET_FILE = Path(__file__).parent / "saved_models" / "telegram_offset.txt"
POLL_TIMEOUT_SEC = 30   # long-poll timeout — Telegram holds the connection open
POLL_RETRY_DELAY = 5    # backoff on errors


def _load_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text().strip())
    except Exception:
        return 0


def _save_offset(offset: int) -> None:
    OFFSET_FILE.parent.mkdir(exist_ok=True)
    OFFSET_FILE.write_text(str(offset))


def _get_updates(token: str, offset: int) -> list[dict]:
    url = (
        f"https://api.telegram.org/bot{token}/getUpdates"
        f"?offset={offset}&timeout={POLL_TIMEOUT_SEC}"
    )
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=POLL_TIMEOUT_SEC + 10) as r:
        data = json.loads(r.read())
    if not data.get("ok"):
        raise RuntimeError(f"getUpdates error: {data}")
    return data.get("result", [])


def _delete_webhook(token: str) -> None:
    """
    Delete any registered webhook so long-poll mode can receive updates.
    Telegram disallows getUpdates while a webhook is active.
    """
    try:
        url = f"https://api.telegram.org/bot{token}/deleteWebhook"
        with urllib.request.urlopen(url, timeout=5) as r:
            res = json.loads(r.read())
        log.info("deleteWebhook: %s", res)
    except Exception as exc:
        log.warning("deleteWebhook failed: %s", exc)


def _dispatch(text: str) -> str:
    """Same dispatcher logic as the webhook handler — kept in sync by hand."""
    parts = text.split()
    cmd = parts[0].lower().split("@")[0]

    if cmd == "/predictions":
        return _cmd_predictions(parts[1] if len(parts) > 1 else "WC")
    if cmd == "/live":
        return _cmd_live()
    if cmd == "/today":
        return _cmd_today()
    if cmd == "/best":
        try:    n = int(parts[1]) if len(parts) > 1 else 5
        except: n = 5
        return _cmd_best(min(max(n, 1), 10))
    if cmd == "/accuracy":
        return _cmd_accuracy(parts[1] if len(parts) > 1 else None)
    if cmd == "/clv":
        return _cmd_clv()
    if cmd == "/match":
        if len(parts) < 3:
            return "Usage: `/match <home_team> <away_team>` (e.g. /match brazil morocco)"
        home = " ".join(parts[1:-1])
        away = parts[-1]
        return _cmd_match(home, away)
    if cmd == "/results":
        try:    hours = int(parts[1]) if len(parts) > 1 else 36
        except: hours = 36
        return _cmd_results(min(max(hours, 1), 168))
    if cmd == "/props":
        if len(parts) < 3:
            return "Usage: `/props <home_team> <away_team>` (e.g. /props brazil morocco)"
        home = " ".join(parts[1:-1])
        away = parts[-1]
        return _cmd_props(home, away)
    if cmd == "/bankroll":
        return _cmd_bankroll()
    if cmd == "/slip":
        return _cmd_slip()
    if cmd in ("/help", "/start"):
        return HELP_TEXT
    return f"Unknown command: `{cmd}`\n\n" + HELP_TEXT


def run() -> None:
    s = get_settings()
    token = s.telegram_bot_token
    if not token:
        log.error("TELEGRAM_BOT_TOKEN not set — exiting")
        return

    # Long-poll and webhook are mutually exclusive. Delete any registered
    # webhook so this worker can read updates.
    _delete_webhook(token)

    offset = _load_offset()
    log.info("Telegram poller starting from offset=%d", offset)

    while True:
        try:
            updates = _get_updates(token, offset)
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                chat_id = msg.get("chat", {}).get("id")
                text    = (msg.get("text") or "").strip()
                if not chat_id or not text.startswith("/"):
                    continue
                log.info("← %s from chat %s", text, chat_id)
                try:
                    reply = _dispatch(text)
                    _send(chat_id, reply)
                    log.info("→ replied to chat %s (%d chars)", chat_id, len(reply))
                except Exception as exc:
                    log.exception("dispatch error: %s", exc)
                    _send(chat_id, f"❌ Error: {exc}")

            if updates:
                _save_offset(offset)
        except KeyboardInterrupt:
            log.info("Stopped by user")
            break
        except Exception as exc:
            log.warning("poll error: %s", exc)
            time.sleep(POLL_RETRY_DELAY)


if __name__ == "__main__":
    run()
