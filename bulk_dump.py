"""
Bulk History Dumper
───────────────────
One-time script that pulls ALL historical files from one or more
source chats and forwards them to DEST_CHANNEL.

Safely resumes from where it left off after a crash (uses tracker.py).
Run with:  python bulk_dump.py [chat_username_or_id]
           If no argument given, dumps ALL SOURCE_CHATS from config.
"""

import asyncio
import logging
import sys
import time
from pyrogram import Client
from pyrogram.errors import FloodWait, ChannelPrivate, UserNotParticipant

from config import (
    API_ID, API_HASH, SESSION_STRING,
    SOURCE_CHATS, DEST_CHANNEL, ALLOWED_TYPES,
    BATCH_SIZE, LOG_CHANNEL
)
from utils import safe_forward, is_allowed_file, get_file_name, get_file_size, human_size
from tracker import is_done, mark_done, get_stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("bulk_dump")

app = Client(
    "bulk_dump_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)

async def dump_chat(chat_id) -> dict:
    """Dump all files from a single chat. Returns stats dict."""
    stats = {
        "total_scanned":  0,
        "total_files":    0,
        "forwarded":      0,
        "skipped":        0,
        "failed":         0,
        "total_bytes":    0,
        "start_time":     time.time(),
    }

    try:
        chat = await app.get_chat(chat_id)
        chat_title = chat.title
    except (ChannelPrivate, UserNotParticipant):
        logger.error(f"❌ Not a member of {chat_id} — join first and re-run")
        return stats
    except Exception as e:
        logger.error(f"❌ Cannot access {chat_id}: {e}")
        return stats

    already_done = get_stats(chat_id)
    logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"📂 Dumping: {chat_title}  (already forwarded: {already_done})")
    logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    async for message in app.get_chat_history(chat_id, limit=0):
        stats["total_scanned"] += 1

        # Progress log every 500 messages
        if stats["total_scanned"] % 500 == 0:
            elapsed = time.time() - stats["start_time"]
            rate = stats["forwarded"] / elapsed * 60 if elapsed else 0
            logger.info(
                f"📊 Scanned {stats['total_scanned']} | "
                f"Files found {stats['total_files']} | "
                f"Forwarded {stats['forwarded']} | "
                f"Rate {rate:.0f}/min"
            )

        if not is_allowed_file(message, ALLOWED_TYPES):
            continue

        stats["total_files"] += 1

        # Skip if already forwarded (resume support)
        if is_done(chat_id, message.id):
            stats["skipped"] += 1
            continue

        name = get_file_name(message)
        size = get_file_size(message)
        logger.info(f"📥  [{message.id}] {name} ({human_size(size)})")

        success = await safe_forward(message, DEST_CHANNEL)
        if success:
            stats["forwarded"] += 1
            stats["total_bytes"] += size
            mark_done(chat_id, message.id)
            logger.info(f"✅  Forwarded  ({stats['forwarded']} total)")
        else:
            stats["failed"] += 1
            logger.error(f"❌  Failed: {name}")

    elapsed = time.time() - stats["start_time"]
    logger.info(f"")
    logger.info(f"🏁 Done with: {chat_title}")
    logger.info(f"   Scanned   : {stats['total_scanned']:,}")
    logger.info(f"   Files     : {stats['total_files']:,}")
    logger.info(f"   Forwarded : {stats['forwarded']:,}  ({human_size(stats['total_bytes'])})")
    logger.info(f"   Skipped   : {stats['skipped']:,}  (already done)")
    logger.info(f"   Failed    : {stats['failed']:,}")
    logger.info(f"   Time      : {elapsed/60:.1f} min")
    return stats

async def main():
    # Determine which chats to dump
    targets = SOURCE_CHATS
    if len(sys.argv) > 1:
        raw = sys.argv[1]
        targets = [int(raw) if raw.lstrip("-").isdigit() else raw]
        logger.info(f"🎯 Overriding source — dumping: {targets}")

    await app.start()
    me = await app.get_me()
    logger.info(f"🚀 Bulk dump started as: {me.first_name} (@{me.username})")

    grand_total = {"forwarded": 0, "failed": 0, "bytes": 0}
    start = time.time()

    for chat in targets:
        stats = await dump_chat(chat)
        grand_total["forwarded"] += stats["forwarded"]
        grand_total["failed"]    += stats["failed"]
        grand_total["bytes"]     += stats["total_bytes"]

    elapsed = time.time() - start
    summary = (
        f"🏆 **Bulk Dump Complete**\n"
        f"Chats dumped   : `{len(targets)}`\n"
        f"Total forwarded: `{grand_total['forwarded']:,}`\n"
        f"Total failed   : `{grand_total['failed']:,}`\n"
        f"Total size     : `{human_size(grand_total['bytes'])}`\n"
        f"Total time     : `{elapsed/60:.1f} min`"
    )
    logger.info(summary.replace("**","").replace("`",""))

    if LOG_CHANNEL:
        try:
            await app.send_message(LOG_CHANNEL, summary)
        except Exception as e:
            logger.warning(f"Could not send log: {e}")

    await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
