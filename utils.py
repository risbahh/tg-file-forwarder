"""
Shared utilities — utils.py
────────────────────────────
Used by forwarder.py, bot_capture.py, and multi_forwarder.py.

safe_forward() is the single point of entry for all forwarding:
  • FloodWait retry with exponential backoff
  • Duplicate detection via seen_db (file_unique_id)
  • Caption watermark stripping via caption_cleaner
"""
import asyncio
import logging
from pyrogram.errors import FloodWait
from pyrogram.types  import Message
from config import DELAY, FLOOD_EXTRA, MAX_RETRIES

logger = logging.getLogger(__name__)


async def safe_forward(
    message: Message,
    dest: int,
    *,
    skip_duplicates: bool = True,
    clean_captions: bool  = True,
) -> bool:
    """
    Forward a single message with:
      • FloodWait handling + retries
      • Duplicate detection  (skip_duplicates=True by default)
      • Caption watermark removal  (clean_captions=True by default)

    Returns True on success, False on duplicate-skip or max-retries.
    """
    # ── 1. Duplicate check ─────────────────────────────────────────────────
    if skip_duplicates:
        uid = get_unique_id(message)
        if uid:
            from seen_db import is_seen, mark_seen
            if is_seen(uid):
                logger.debug(f"⏭️  Duplicate skipped: {uid[:12]}…")
                return False

    # ── 2. Forward (with optional caption cleaning) ─────────────────────────
    from caption_cleaner import clean as strip_watermarks, is_enabled as captions_on

    use_copy = clean_captions and captions_on() and bool(
        message.caption or (
            getattr(message.document, "file_name", None) and message.caption
        )
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if use_copy:
                cleaned = strip_watermarks(message.caption)
                # FIX: pass "" (not None) so pyrofork actually clears the caption
                # when the cleaner strips everything. Passing None keeps the original.
                await message.copy(dest, caption=cleaned if cleaned is not None else "")
            else:
                await message.forward(dest)
            await asyncio.sleep(DELAY)

            # ── 3. Mark as seen after successful forward ──────────────────
            if skip_duplicates:
                uid = get_unique_id(message)
                if uid:
                    from seen_db import mark_seen
                    mark_seen(uid)

            return True

        except FloodWait as e:
            wait = e.value + FLOOD_EXTRA
            logger.warning(f"⏳ FloodWait {e.value}s — sleeping {wait}s (attempt {attempt}/{MAX_RETRIES})")
            await asyncio.sleep(wait)
        except Exception as e:
            logger.error(f"❌ Forward error (attempt {attempt}/{MAX_RETRIES}): {type(e).__name__}: {e}")
            await asyncio.sleep(5 * attempt)

    return False


def get_unique_id(message: Message) -> str | None:
    """Extract file_unique_id from any supported message type."""
    for attr in ("document", "video", "audio", "photo"):
        obj = getattr(message, attr, None)
        if obj:
            uid = getattr(obj, "file_unique_id", None)
            if uid:
                return uid
    return None


def is_allowed_file(message: Message, allowed_types: list) -> bool:
    """Return True if the message contains a file type we want to forward."""
    checks = {
        "document": bool(message.document),
        "video":    bool(message.video),
        "audio":    bool(message.audio),
        "photo":    bool(message.photo),
    }
    return any(checks.get(t, False) for t in allowed_types)


def get_file_name(message: Message) -> str:
    """Extract a human-readable filename from a message."""
    for attr in ("document", "video", "audio"):
        obj = getattr(message, attr, None)
        if obj and getattr(obj, "file_name", None):
            return obj.file_name
    return "unnamed"


def get_file_size(message: Message) -> int:
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
