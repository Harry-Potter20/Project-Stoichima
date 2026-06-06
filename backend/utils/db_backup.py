"""
Atomic SQLite backup via the .backup() API — safe with WAL and live connections.

Usage:
    from utils.db_backup import backup_db

    path = backup_db("before_clear_wc_bets")
    # ... do destructive work ...

Backups go to backend/backups/football_<tag>_<UTC-timestamp>.db. Old backups
beyond MAX_BACKUPS are pruned automatically.
"""
from __future__ import annotations
import os
import re
import sqlite3
import logging
from datetime import datetime

log = logging.getLogger(__name__)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH     = os.path.join(BACKEND_DIR, "football.db")
BACKUP_DIR  = os.path.join(BACKEND_DIR, "backups")
MAX_BACKUPS = 20   # keep last N backups, prune older


def backup_db(tag: str = "manual") -> str:
    """
    Atomic backup of football.db. Returns the path to the backup file.
    Tag is sanitised — only [a-zA-Z0-9_-] survive, spaces become underscores.
    """
    safe_tag = re.sub(r"[^A-Za-z0-9_-]", "_", tag)[:40] or "manual"
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    out_path = os.path.join(BACKUP_DIR, f"football_{safe_tag}_{timestamp}.db")

    src = sqlite3.connect(DB_PATH)
    try:
        dst = sqlite3.connect(out_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    log.info("DB backup created: %s", out_path)
    _prune_old_backups()
    return out_path


def _prune_old_backups() -> None:
    """Delete oldest backups beyond MAX_BACKUPS."""
    try:
        files = [
            os.path.join(BACKUP_DIR, f)
            for f in os.listdir(BACKUP_DIR)
            if f.startswith("football_") and f.endswith(".db")
        ]
        files.sort(key=os.path.getmtime, reverse=True)
        for stale in files[MAX_BACKUPS:]:
            os.remove(stale)
    except Exception as exc:
        log.warning("Backup prune failed: %s", exc)


def list_backups() -> list[dict]:
    """List existing backups, newest first."""
    if not os.path.isdir(BACKUP_DIR):
        return []
    rows = []
    for f in os.listdir(BACKUP_DIR):
        if not (f.startswith("football_") and f.endswith(".db")):
            continue
        path = os.path.join(BACKUP_DIR, f)
        stat = os.stat(path)
        rows.append({
            "file":      f,
            "path":      path,
            "size_mb":   round(stat.st_size / (1024 * 1024), 2),
            "created":   datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
        })
    rows.sort(key=lambda r: r["created"], reverse=True)
    return rows
