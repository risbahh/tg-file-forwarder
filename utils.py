"""
Shared utilities — utils.py
────────────────────────────
safe_forward() is the single forwarding entry point:
  • Duplicate detection  (seen_db file_unique_id)
  • Caption watermark stripping  (caption_cleaner)
  • Caption suffix appending     (caption_suffix)
  • FloodWait retry with backoff
"""
import asyncio
import logging
from pyrogram.errors import FloodWait
from pyrogram.types  import Message
import failed_db
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
    Forward a single message.
    Returns True on success, False on duplicate-skip or max-retries.
    """
    # ── 1. Duplicate check ─────────────────────────────────────────────────
    if skip_duplicates:
        uid = get_unique_id(message)
        if uid:
            from seen_db import is_seen
            if is_seen(uid):
                logger.debug(f"⏭️  Duplicate skipped: {uid[:12]}…")
                return False

    # ── 2. Build cleaned caption ───────────────────────────────────────────
    from caption_cleaner import clean as strip_watermarks, is_enabled as captions_on

    use_copy = clean_captions and captions_on()

    def _build_caption(original: str | None) -> str:
        """Apply watermark stripping + custom suffix."""
        cleaned = strip_watermarks(original) if (clean_captions and captions_on()) else original
        # Append suffix (if set)
        try:
            from caption_suffix import get as get_suffix
            suffix = get_suffix()
        except ImportError:
            suffix = ""
        if suffix:
            if cleaned:
                return cleaned + "\n\n" + suffix
            return suffix
        return cleaned if cleaned is not None else ""

    # ── 3. Forward with retry ──────────────────────────────────────────────
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if use_copy:
                caption_out = _build_caption(message.caption)
                await message.copy(dest, caption=caption_out)
            else:
                await message.forward(dest)
            await asyncio.sleep(DELAY)

            # Mark as seen after success
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

    # Save to failed.json so /retry can recover files lost during FloodWait
    try:
        failed_db.save(message.chat.id, message.id)
        logger.info(f"💾 Saved to failed.json: chat={message.chat.id} msg={message.id}")
    except Exception as _e:
        logger.debug(f"failed_db.save error: {_e}")
    return False


def get_unique_id(message: Message) -> str | None:
    for attr in ("document", "video", "audio", "photo"):
        obj = getattr(message, attr, None)
        if obj:
            uid = getattr(obj, "file_unique_id", None)
            if uid:
                return uid
    return None


def is_allowed_file(message: Message, allowed_types: list) -> bool:
    checks = {
        "document": bool(message.document),
        "video":    bool(message.video),
        "audio":    bool(message.audio),
        "photo":    bool(message.photo),
    }
    return any(checks.get(t, False) for t in allowed_types)


def get_file_name(message: Message) -> str:
    for attr in ("document", "video", "audio"):
        obj = getattr(message, attr, None)
        if obj and getattr(obj, "file_name", None):
            return obj.file_name
    return "unnamed"


def get_file_size(message: Message) -> int:
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
