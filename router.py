"""
Multi-Destination Router — router.py
──────────────────────────────────────
Routes each forwarded file to the correct index channel based on:
  1. Per-source override  (/route <chat> <channel>  → routing.json)
  2. Filename content-type detection (series, south, movies)
  3. DEST_CHANNEL fallback

Environment variables:
  DEST_CHANNEL    — default / fallback destination (required)
  DEST_MOVIES     — channel for standalone movies  (optional)
  DEST_SERIES     — channel for TV series          (optional)
  DEST_SOUTH      — channel for South Indian films (optional)

Usage:
  from router import get_destination, set_route, list_routes, remove_route, detect_type
  dest = get_destination(filename, source_chat_id)
  await safe_forward(message, dest)
"""
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# ── Destination channels from env ──────────────────────────────────────────
DEST_DEFAULT = int(os.environ.get("DEST_CHANNEL", "0"))
DEST_MOVIES  = int(os.environ.get("DEST_MOVIES",  "0")) or None
DEST_SERIES  = int(os.environ.get("DEST_SERIES",  "0")) or None
DEST_SOUTH   = int(os.environ.get("DEST_SOUTH",   "0")) or None

_DB_FILE = os.environ.get("ROUTING_FILE", "routing.json")

# ── Detection patterns ─────────────────────────────────────────────────────
_SERIES_RE = re.compile(
    r'[Ss]\d{1,2}[Ee]\d{1,2}'       # S01E01
    r'|Season\s*\d+'                  # Season 1
    r'|Episode\s*\d+'                 # Episode 3
    r'|\bComplete\b'                  # Complete Series
    r'|\bS\d{1,2}\b',                 # S01 (season pack)
    re.I
)
_SOUTH_RE = re.compile(
    r'Tamil|Telugu|Malayalam|Kannada|Hindi[\s\-]Dubbed|South\s+Indian',
    re.I
)


def detect_type(filename: str) -> str:
    """Return 'series', 'south', or 'movie' based on filename patterns."""
    if _SERIES_RE.search(filename):
        return "series"
    if _SOUTH_RE.search(filename):
        return "south"
    return "movie"


def get_destination(filename: str, source_chat=None) -> int:
    """
    Determine the correct destination channel for a file.
    Priority: per-source override → filename detection → DEST_CHANNEL fallback.
    """
    # 1. Per-source routing override
    routes = _load()
    if source_chat:
        key = str(source_chat).lstrip("-")
        for k in (str(source_chat), key, f"-{key}", f"-100{key}"):
            if k in routes:
                return int(routes[k])

    # 2. Auto-detect by filename
    kind = detect_type(filename)
    if kind == "series" and DEST_SERIES:
        return DEST_SERIES
    if kind == "south" and DEST_SOUTH:
        return DEST_SOUTH
    if kind == "movie" and DEST_MOVIES:
        return DEST_MOVIES

    # 3. Fallback
    return DEST_DEFAULT


def set_route(source_chat, dest_channel: int) -> str:
    """Set a per-source routing rule. Returns confirmation message."""
    routes = _load()
    key = str(source_chat).strip()
    routes[key] = str(dest_channel)
    _save(routes)
    logger.info(f"Route set: {source_chat} → {dest_channel}")
    return f"✅ `{source_chat}` → `{dest_channel}`\nFiles from this source will now go to that channel."


def remove_route(source_chat) -> tuple[bool, str]:
    """Remove a per-source routing rule."""
    routes = _load()
    key = str(source_chat).strip()
    if key not in routes:
        return False, f"No custom route set for `{source_chat}`."
    routes.pop(key)
    _save(routes)
    return True, f"✅ Removed route for `{source_chat}`. Will now use auto-detection."


def list_routes() -> str:
    """Human-readable list of all per-source routes + auto-routing config."""
    routes = _load()
    lines = ["**Routing configuration:**\n"]

    lines.append("**Auto-detect channels:**")
    lines.append(f"• Movies  → `{DEST_MOVIES or 'same as DEST_CHANNEL'}`")
    lines.append(f"• Series  → `{DEST_SERIES or 'same as DEST_CHANNEL'}`")
    lines.append(f"• South   → `{DEST_SOUTH or 'same as DEST_CHANNEL'}`")
    lines.append(f"• Default → `{DEST_DEFAULT}`\n")

    if routes:
        lines.append("**Per-source overrides:**")
        for src, dst in routes.items():
            lines.append(f"• `{src}` → `{dst}`")
    else:
        lines.append("_No per-source overrides set._\nUse `/route <source> <channel>` to add one.")

    return "\n".join(lines)


def format_type_label(filename: str) -> str:
    t = detect_type(filename)
    return {"series": "📺 Series", "south": "🎬 South", "movie": "🎥 Movie"}[t]


def _load() -> dict:
    if os.path.exists(_DB_FILE):
        try:
            return json.load(open(_DB_FILE))
        except Exception:
            pass
    return {}


def _save(data: dict):
    with open(_DB_FILE, "w") as f:
        json.dump(data, f, indent=2)
