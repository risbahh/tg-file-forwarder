"""
Duplicate Detection — seen_db.py
─────────────────────────────────
Tracks every file ever forwarded by its Telegram file_unique_id.
Same file posted in two different groups → forwarded once, skipped once.

file_unique_id is Telegram's global unique key — identical across all chats.
Storage: seen.json (flat JSON array of unique ID strings)
"""
import json
import logging
import os

logger   = logging.getLogger(__name__)
_DB_FILE = os.environ.get("SEEN_DB_FILE", "seen.json")
_cache: set | None = None          # in-memory cache — avoid disk read every message


def _load() -> set:
    global _cache
    if _cache is not None:
        return _cache
    if os.path.exists(_DB_FILE):
        try:
            # FIX: use context manager so the file handle is always closed
            with open(_DB_FILE) as f:
                _cache = set(json.load(f))
            logger.info(f"seen_db: loaded {len(_cache):,} previously-forwarded IDs")
            return _cache
        except Exception:
            logger.warning("seen.json corrupt — starting fresh")
    _cache = set()
    return _cache


def _save(ids: set):
    with open(_DB_FILE, "w") as f:
        json.dump(list(ids), f)


def is_seen(unique_id: str) -> bool:
    """Return True if this file_unique_id has been forwarded before."""
    return unique_id in _load()


def mark_seen(unique_id: str) -> int:
    """Record a file as forwarded. Returns new total count."""
    ids = _load()
    ids.add(unique_id)
    _save(ids)
    return len(ids)


def count() -> int:
    """Total distinct files ever forwarded."""
    return len(_load())


def reset():
    """Clear seen history (use with caution — will re-forward duplicates)."""
    global _cache
    _cache = set()
    _save(_cache)
