import asyncio
import logging
from pyrogram.errors import FloodWait
from config import DELAY, FLOOD_EXTRA, MAX_RETRIES

logger = logging.getLogger(__name__)

async def safe_forward(message, dest: int) -> bool:
    """Forward a single message with automatic FloodWait handling and retries."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            await message.forward(dest)
            await asyncio.sleep(DELAY)
            return True
        except FloodWait as e:
            wait = e.value + FLOOD_EXTRA
            logger.warning(f"⏳ FloodWait {e.value}s — sleeping {wait}s (attempt {attempt}/{MAX_RETRIES})")
            await asyncio.sleep(wait)
        except Exception as e:
            logger.error(f"❌ Forward error (attempt {attempt}/{MAX_RETRIES}): {type(e).__name__}: {e}")
            await asyncio.sleep(5 * attempt)
    return False

def is_allowed_file(message, allowed_types: list) -> bool:
    """Return True if the message contains a file type we want to forward."""
    checks = {
        "document": bool(message.document),
        "video":    bool(message.video),
        "audio":    bool(message.audio),
        "photo":    bool(message.photo),
    }
    return any(checks.get(t, False) for t in allowed_types)

def get_file_name(message) -> str:
    """Extract a human-readable filename from a message."""
    for attr in ("document", "video", "audio"):
        obj = getattr(message, attr, None)
        if obj and getattr(obj, "file_name", None):
            return obj.file_name
    return "unnamed"

def get_file_size(message) -> int:
    """Return file size in bytes, or 0 if unavailable."""
    for attr in ("document", "video", "audio"):
        obj = getattr(message, attr, None)
        if obj:
            return getattr(obj, "file_size", 0) or 0
    return 0

def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
