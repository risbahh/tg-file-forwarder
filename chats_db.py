"""
Dynamic chat list manager.
Stores SOURCE_CHATS in chats.json so they survive restarts
and can be updated via Telegram commands without touching Railway.
"""
import json
import os
import logging

logger = logging.getLogger(__name__)

_DB_FILE = os.environ.get("CHATS_DB_FILE", "chats.json")

def _load() -> dict:
    if os.path.exists(_DB_FILE):
        try:
            with open(_DB_FILE) as f:
                return json.load(f)
        except Exception:
            logger.warning("chats.json corrupt — starting fresh")
    return {"chats": []}

def _save(data: dict):
    with open(_DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

def _parse(raw: str):
    """Return int if numeric, else string username."""
    r = raw.strip().lstrip("@")
    return int(r) if r.lstrip("-").isdigit() else r

def get_all_chats(seed: list) -> list:
    """Merge config-seeded chats with dynamically added ones (deduped)."""
    data   = _load()
    stored = data.get("chats", [])
    merged = list(seed)
    for c in stored:
        if c not in merged:
            merged.append(c)
    return merged

def add_chat(raw: str) -> tuple[bool, str]:
    """Add a chat. Returns (success, message)."""
    chat = _parse(raw)
    data = _load()
    if chat in data["chats"]:
        return False, f"Already in list: `{chat}`"
    data["chats"].append(chat)
    _save(data)
    logger.info(f"Added chat: {chat}")
    return True, f"✅ Added `{chat}` to source chats."

def remove_chat(raw: str) -> tuple[bool, str]:
    """Remove a chat. Returns (success, message)."""
    chat = _parse(raw)
    data = _load()
    if chat not in data["chats"]:
        # Try matching by str/int variant
        alt = str(chat) if isinstance(chat, int) else int(chat) if str(chat).lstrip("-").isdigit() else None
        if alt and alt in data["chats"]:
            chat = alt
        else:
            return False, f"Not found in dynamic list: `{chat}`\n_(Config-seeded chats can't be removed here — edit Railway vars)_"
    data["chats"].remove(chat)
    _save(data)
    logger.info(f"Removed chat: {chat}")
    return True, f"✅ Removed `{chat}` from source chats."

def list_chats(seed: list) -> str:
    """Return a formatted list of all current source chats."""
    data    = _load()
    dynamic = data.get("chats", [])
    lines   = ["**Current source chats:**\n"]
    if seed:
        lines.append("_From Railway config (edit vars to change):_")
        for c in seed:
            lines.append(f"  • `{c}`")
    if dynamic:
        lines.append("\n_Dynamically added (via /addchat):_")
        for c in dynamic:
            lines.append(f"  • `{c}`")
    if not seed and not dynamic:
        lines.append("_No source chats configured yet._")
    lines.append(f"\n**Total: {len(seed) + len(dynamic)} chats**")
    return "\n".join(lines)
