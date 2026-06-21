"""
Bot-Group mapping database.
Stores which bot to watch in each source group.
Persists to bots.json — survives restarts, updated via /setbot command.

Schema: { "group_id_or_username": { "bot_id": int, "bot_username": str, "label": str } }
"""
import json
import os
import logging

logger = logging.getLogger(__name__)
_DB_FILE = os.environ.get("BOTS_DB_FILE", "bots.json")


def _load() -> dict:
    if os.path.exists(_DB_FILE):
        try:
            with open(_DB_FILE) as f:
                return json.load(f)
        except Exception:
            logger.warning("bots.json corrupt — starting fresh")
    return {}


def _save(data: dict):
    with open(_DB_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _key(group) -> str:
    return str(group).strip().lstrip("@")


def set_bot(group, bot_id: int, bot_username: str, label: str = "") -> str:
    """Register a bot to watch in a group. Returns confirmation message."""
    data = _load()
    key  = _key(group)
    data[key] = {
        "bot_id":       bot_id,
        "bot_username": bot_username.lstrip("@"),
        "label":        label or bot_username,
    }
    _save(data)
    logger.info(f"Registered bot @{bot_username} ({bot_id}) for group {group}")
    return f"✅ Now watching **@{bot_username}** in `{group}`\nOnly files sent by that bot will be forwarded."


def remove_bot(group) -> tuple[bool, str]:
    """Remove bot registration for a group."""
    data = _load()
    key  = _key(group)
    if key not in data:
        return False, f"No bot registered for `{group}`."
    entry = data.pop(key)
    _save(data)
    logger.info(f"Removed bot registration for group {group}")
    return True, f"✅ Removed **@{entry['bot_username']}** from `{group}`.\nWill now forward ALL files from that group."


def get_bot(group) -> dict | None:
    """Return bot entry for a group, or None if not registered."""
    data = _load()
    return data.get(_key(group))


def get_bot_by_chat_id(chat_id: int) -> dict | None:
    """Look up by numeric chat ID (checks both string key and int key)."""
    data = _load()
    for key, val in data.items():
        if key == str(chat_id) or key == str(chat_id).lstrip("-"):
            return val
    return None


def list_all() -> dict:
    """Return all registered bot-group pairs."""
    return _load()


def format_list() -> str:
    """Return a human-readable list of all registered bots."""
    data = _load()
    if not data:
        return "_No bots registered yet._\n\nUse `/setbot <group> <bot_username>` to register one."
    lines = ["**Registered bots (target-only capture):**\n"]
    for group, entry in data.items():
        lines.append(f"• `{group}` → **@{entry['bot_username']}** (ID: `{entry['bot_id']}`)")
    lines.append(f"\n**Total: {len(data)} registered**")
    return "\n".join(lines)
