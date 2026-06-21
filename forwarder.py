"""
Real-time Forwarder
───────────────────
Watches SOURCE_CHATS for new file messages and instantly
forwards them to DEST_CHANNEL (your auto-filter index channel).

Run with:  python forwarder.py
Deploy on Railway — set env vars from .env.example in Variables tab.
"""

import asyncio
import logging
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import ChannelPrivate, ChatAdminRequired, UserNotParticipant

from config import (
    API_ID, API_HASH, SESSION_STRING,
    SOURCE_CHATS, DEST_CHANNEL, ALLOWED_TYPES, LOG_CHANNEL
)
from utils import safe_forward, is_allowed_file, get_file_name, get_file_size, human_size

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("forwarder")

app = Client(
    "forwarder_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)

# ── Counters ───────────────────────────────────────────────────────────────
_stats = {"forwarded": 0, "skipped": 0, "failed": 0}

# ── Handler ────────────────────────────────────────────────────────────────
@app.on_message(filters.chat(SOURCE_CHATS))
async def on_new_message(client: Client, message: Message):
    if not is_allowed_file(message, ALLOWED_TYPES):
        return

    name  = get_file_name(message)
    size  = human_size(get_file_size(message))
    chat  = getattr(message.chat, "title", str(message.chat.id))

    logger.info(f"📥 [{chat}] {name} ({size})")

    success = await safe_forward(message, DEST_CHANNEL)
    if success:
        _stats["forwarded"] += 1
        logger.info(f"✅ Forwarded → {DEST_CHANNEL}  |  total: {_stats['forwarded']}")
    else:
        _stats["failed"] += 1
        logger.error(f"❌ Failed to forward: {name}")

# ── Startup ────────────────────────────────────────────────────────────────
async def main():
    await app.start()
    me = await app.get_me()
    logger.info(f"🚀 Userbot started as: {me.first_name} (@{me.username})")
    logger.info(f"👀 Watching {len(SOURCE_CHATS)} source(s): {SOURCE_CHATS}")
    logger.info(f"📤 Destination channel: {DEST_CHANNEL}")
    logger.info(f"📎 Allowed types: {ALLOWED_TYPES}")

    # Verify we can reach the destination channel
    try:
        dest = await app.get_chat(DEST_CHANNEL)
        logger.info(f"✅ Destination verified: {dest.title}")
    except Exception as e:
        logger.error(f"⚠️  Cannot reach DEST_CHANNEL {DEST_CHANNEL}: {e}")

    # Verify source chats
    for src in SOURCE_CHATS:
        try:
            chat = await app.get_chat(src)
            logger.info(f"✅ Source verified: {chat.title} ({chat.members_count} members)")
        except (ChannelPrivate, UserNotParticipant):
            logger.warning(f"⚠️  Not a member of source chat: {src}  — join first!")
        except Exception as e:
            logger.warning(f"⚠️  Cannot verify source {src}: {e}")

    if LOG_CHANNEL:
        try:
            await app.send_message(LOG_CHANNEL, "✅ **Real-time forwarder started**\n"
                f"Watching: `{SOURCE_CHATS}`\n"
                f"Destination: `{DEST_CHANNEL}`")
        except Exception:
            pass

    logger.info("⏳ Listening for new files...")
    await idle()

    # Shutdown summary
    logger.info(f"📊 Session summary — Forwarded: {_stats['forwarded']} | Failed: {_stats['failed']}")
    if LOG_CHANNEL:
        try:
            await app.send_message(LOG_CHANNEL,
                f"🛑 **Forwarder stopped**\n"
                f"Forwarded: `{_stats['forwarded']}`\n"
                f"Failed: `{_stats['failed']}`")
        except Exception:
            pass
    await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
