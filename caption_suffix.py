"""
caption_suffix.py — Persistent caption suffix for forwarded files.

Stores a single suffix string in caption_suffix.json.
utils.safe_forward() reads this and appends it to every caption.

  get()      → current suffix string, or "" if not set
  set(text)  → save new suffix
  clear()    → remove suffix
"""
import json
import os

_FILE = os.path.join(os.path.dirname(__file__), "caption_suffix.json")


def get() -> str:
    try:
        with open(_FILE) as f:
            data = json.load(f)
        return str(data.get("suffix", "")).strip()
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def set(text: str) -> None:
    with open(_FILE, "w") as f:
        json.dump({"suffix": text.strip()}, f)


def clear() -> None:
    with open(_FILE, "w") as f:
        json.dump({"suffix": ""}, f)
