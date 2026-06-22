"""
keyword_filter.py — Keyword-based forwarding filter.

Two modes stored in keywords.json:
  "allow":  only forward files whose filename/caption matches at least one allow keyword
  "block":  skip files whose filename/caption matches any block keyword
  "off":    disabled (default)

Keywords are case-insensitive substring matches (not regex).

Schema: {"mode": "off"|"allow"|"block", "keywords": [...]}
"""
import json
import os
import threading

_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keywords.json")
_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(_FILE) as f:
            return json.load(f)
    except Exception:
        return {"mode": "off", "keywords": []}


def _save(data: dict):
    with open(_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_state() -> dict:
    with _lock:
        return _load()


def set_mode(mode: str):
    """mode: 'allow', 'block', or 'off'"""
    with _lock:
        data = _load()
        data["mode"] = mode
        _save(data)


def add_keyword(word: str) -> bool:
    """Returns False if already present."""
    with _lock:
        data = _load()
        if word.lower() in [k.lower() for k in data.get("keywords", [])]:
            return False
        data.setdefault("keywords", []).append(word)
        _save(data)
        return True


def remove_keyword(index: int) -> str:
    """Remove by 1-based index. Returns removed word or error string."""
    with _lock:
        data = _load()
        kws = data.get("keywords", [])
        if not kws:
            return "No keywords set."
        if index < 1 or index > len(kws):
            return f"Index out of range (1–{len(kws)})."
        removed = kws.pop(index - 1)
        data["keywords"] = kws
        _save(data)
        return removed


def passes(text: str) -> bool:
    """
    Returns True if the message should be forwarded.
    text = filename + " " + caption (combined for matching).
    """
    data = _load()
    mode = data.get("mode", "off")
    if mode == "off":
        return True
    kws = [k.lower() for k in data.get("keywords", [])]
    if not kws:
        return True
    t = text.lower()
    if mode == "allow":
        return any(k in t for k in kws)
    if mode == "block":
        return not any(k in t for k in kws)
    return True
