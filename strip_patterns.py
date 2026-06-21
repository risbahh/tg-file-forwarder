"""
strip_patterns.py — Runtime-editable watermark strip patterns.

Patterns are stored in strip_patterns.json and loaded dynamically.
caption_cleaner.py calls load() each time it cleans a caption.

  load()            → list of raw regex strings
  add(pattern)      → True if added, False if already exists
  remove(index)     → removed pattern string, or None if out of range
  count()           → number of custom patterns
"""
import json
import os
import re
import threading

_FILE = os.path.join(os.path.dirname(__file__), "strip_patterns.json")
_lock = threading.Lock()


def load() -> list:
    try:
        with open(_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(patterns: list) -> None:
    with open(_FILE, "w") as f:
        json.dump(patterns, f, indent=2)


def add(pattern: str) -> bool:
    """Add a new regex pattern. Returns False if it already exists."""
    with _lock:
        patterns = load()
        if pattern in patterns:
            return False
        # Validate it's a legal regex before saving
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error:
            raise ValueError(f"Invalid regex: {pattern!r}")
        patterns.append(pattern)
        _save(patterns)
        return True


def remove(index: int) -> str | None:
    """Remove pattern at 1-based index. Returns removed string or None."""
    with _lock:
        patterns = load()
        if index < 1 or index > len(patterns):
            return None
        removed = patterns.pop(index - 1)
        _save(patterns)
        return removed


def count() -> int:
    return len(load())
