"""
Prediction and simulation result cache with per-key TTL.

TTL is kick-off-aware: predictions for matches kicking off soon are cached
for a shorter window to ensure late injury/lineup news is picked up quickly.
"""
import time
import threading

DEFAULT_TTL = 1800  # 30 minutes — default for matches > 24h away

_lock  = threading.Lock()
_store: dict = {}   # key -> (value, expiry_monotonic)


def get(key):
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.monotonic() > expiry:
            del _store[key]
            return None
        return value


def set(key, value, ttl: int = DEFAULT_TTL):
    with _lock:
        _store[key] = (value, time.monotonic() + ttl)


def invalidate(competition_id: str | None = None):
    """Invalidate all entries, or only those matching a competition_id prefix."""
    with _lock:
        if competition_id is None:
            _store.clear()
        else:
            stale = [k for k in list(_store.keys()) if str(k).startswith(competition_id)]
            for k in stale:
                _store.pop(k, None)
