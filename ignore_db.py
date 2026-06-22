"""
ignore_db.py — Temporarily ignore source chats without removing them.

Ignored chats stay in chats.json (still "active") but all their messages
are silently skipped during forwarding. /unignorechat re-enables instantly.

Stored in ignored.json: {"chat_id_str": {"title": "...", "since": timestamp}}
"""
import json
import os
import threading
import time

_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ignored.json")
_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict):
    with open(_FILE, "w") as f:
        json.dump(data, f, indent=2)


def ignore(chat_id: int | str, title: str = ""):
    with _lock:
        data = _load()
        data[str(chat_id)] = {"title": title, "since": time.time()}
        _save(data)


def unignore(chat_id: int | str):
    with _lock:
        data = _load()
        data.pop(str(chat_id), None)
        _save(data)


def is_ignored(chat_id: int | str) -> bool:
    with _lock:
        return str(chat_id) in _load()


def list_ignored() -> dict:
    with _lock:
        return _load()


def count() -> int:
    return len(list_ignored())
