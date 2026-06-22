"""
failed_db.py — Stores message IDs that failed all forwarding retries.
Persists to failed.json so /retry can recover files lost during FloodWait.

Entry format: {"chat_id": int, "message_id": int, "ts": float}
"""
import json
import os
import threading
import time

_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "failed.json")
_lock = threading.Lock()


def _load_raw() -> list:
    try:
        with open(_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_raw(data: list):
    with open(_FILE, "w") as f:
        json.dump(data, f, indent=2)


def save(chat_id: int, message_id: int):
    """Record a failed forward."""
    with _lock:
        data = _load_raw()
        entry = {"chat_id": chat_id, "message_id": message_id, "ts": time.time()}
        # Deduplicate
        for e in data:
            if e["chat_id"] == chat_id and e["message_id"] == message_id:
                return
        data.append(entry)
        _save_raw(data)


def load() -> list:
    """Return all pending failed entries."""
    with _lock:
        return _load_raw()


def remove(chat_id: int, message_id: int):
    """Remove a successfully retried entry."""
    with _lock:
        data = _load_raw()
        data = [e for e in data if not (e["chat_id"] == chat_id and e["message_id"] == message_id)]
        _save_raw(data)


def clear():
    """Wipe all failed entries."""
    with _lock:
        _save_raw([])


def count() -> int:
    return len(load())


def by_chat() -> dict:
    """Return {chat_id: count} breakdown."""
    out: dict = {}
    for e in load():
        cid = e["chat_id"]
        out[cid] = out.get(cid, 0) + 1
    return out
