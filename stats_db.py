"""
stats_db.py — Per-source forwarding stats, stored in stats.json.

  record(chat_id, chat_title) — call after each successful forward
  get_all()                   — list of source dicts, sorted by count desc
  total()                     — grand total across all sources
  reset_source(chat_id)       — zero out one source's count
  reset_all()                 — clear everything
"""
import json
import os
import threading
from datetime import datetime, timezone

STATS_FILE = os.path.join(os.path.dirname(__file__), "stats.json")
_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    with open(STATS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record(chat_id: int, chat_title: str) -> None:
    """Increment forwarded count for a source chat. Thread-safe."""
    key = str(chat_id)
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        data = _load()
        if key not in data:
            data[key] = {
                "title":      chat_title,
                "count":      0,
                "first_seen": now,
            }
        data[key]["count"]     += 1
        data[key]["title"]      = chat_title   # keep name updated
        data[key]["last_seen"]  = now
        _save(data)


def get_all() -> list:
    """Return list of source dicts sorted by count descending."""
    data = _load()
    rows = [{"chat_id": int(k), **v} for k, v in data.items()]
    return sorted(rows, key=lambda r: r["count"], reverse=True)


def total() -> int:
    """Grand total files forwarded across all sources."""
    return sum(v["count"] for v in _load().values())


def reset_source(chat_id: int) -> bool:
    """Zero out one source. Returns True if it existed."""
    key = str(chat_id)
    with _lock:
        data = _load()
        if key not in data:
            return False
        data[key]["count"] = 0
        _save(data)
        return True


def reset_all() -> int:
    """Clear everything. Returns how many sources were cleared."""
    with _lock:
        data = _load()
        count = len(data)
        _save({})
        return count


def all_stats() -> dict:
    """Return raw stats dict {chat_id_str: {title, count, first_seen, last_seen}}"""
    with _lock:
        return _load()
