"""
Progress tracker for bulk_dump.py
Persists forwarded message IDs to a JSON file so bulk dumps
can be resumed safely after a crash or restart.
"""
import json
import os
import logging
from config import TRACKER_FILE

logger = logging.getLogger(__name__)

def _load() -> dict:
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE) as f:
                return json.load(f)
        except Exception:
            logger.warning("Tracker file corrupt — starting fresh")
    return {}

def _save(data: dict):
    with open(TRACKER_FILE, "w") as f:
        json.dump(data, f, indent=2)

def _key(chat_id) -> str:
    return str(chat_id)

def is_done(chat_id, message_id: int) -> bool:
    data = _load()
    return message_id in data.get(_key(chat_id), [])

def mark_done(chat_id, message_id: int):
    data = _load()
    key  = _key(chat_id)
    if key not in data:
        data[key] = []
    if message_id not in data[key]:
        data[key].append(message_id)
    _save(data)

def get_stats(chat_id) -> int:
    data = _load()
    return len(data.get(_key(chat_id), []))

def clear(chat_id):
    data = _load()
    data.pop(_key(chat_id), None)
    _save(data)
    logger.info(f"Tracker cleared for chat {chat_id}")
