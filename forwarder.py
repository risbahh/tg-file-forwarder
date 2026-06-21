"""
Real-time Forwarder  +  /addchat  /removechat  /listchats  commands
────────────────────────────────────────────────────────────────────
Watches SOURCE_CHATS for new file messages and instantly
forwards them to DEST_CHANNEL (your auto-filter index channel).

Commands (ADMINS only):
  /addchat <username or -100id>   — add a new source chat at runtime
  /removechat <username or -100id>— remove a source chat at runtime
  /listchats                      — show all current source chats
  /fwrstatus                      — show forwarder stats

Deploy on Railway — set env vars from .env.example in Variables tab.
Run locally:  python forwarder.py
"""

import asyncio
import logging
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import ChannelPrivate, UserNotParticipant

from config import (
    API_ID, API_HASH, SESSION_STRING,
    SOURCE_CHATS, DEST_CHANNEL, ALLOWED_TYPES, LOG_CHANNEL
)
from utils import safe_forward, is_allowed_file, get_file_name, get_file_size, human_size
from chats_db import get_all_chats, add_chat, remove_chat, list_chats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("forwarder")

# ── Admin list from config ─────────────────────────────────────────────────
import os
ADMINS = [
    int(x.strip()) for x in os.environ.get("ADMINS", "").split(",")
    if x.strip().isdigit()
]

app = Client(
    "forwarder_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)

# ── Stats ──────────────────────────────────────────────────────────────────
_stats = {"forwarded": 0, "skipped": 0, "failed": 0}

# ── Admin guard decorator ──────────────────────────────────────────────────
def admin_only(func):
    async def wrapper(client, message: Message):
        if ADMINS and message.from_user and message.from_user.id not in ADMINS:
            await message.reply("⛔ You are not authorized to use this command.")
            return
        await func(client, message)
    wrapper.__name__ = func.__name__
    return wrapper

# ── File handler (catches ALL chats dynamically) ───────────────────────────
@app.on_message(filters.document | filters.video | filters.audio)
async def on_new_file(client: Client, message: Message):
    # Dynamically check against current chat list (picks up /addchat additions instantly)
    current_chats = get_all_chats(SOURCE_CHATS)

    chat_id       = message.chat.id
    chat_username = getattr(message.chat, "username", None)

    in_source = (
        chat_id in current_chats or
        (chat_username and chat_username in current_chats) or
        (chat_username and f"@{chat_username}" in current_chats)
    )
    if not in_source:
        return  # Not a monitored chat — ignore

    if not is_allowed_file(message, ALLOWED_TYPES):
        return

    name  = get_file_name(message)
    size  = human_size(get_file_size(message))
    title = getattr(message.chat, "title", str(chat_id))

    logger.info(f"📥 [{title}] {name} ({size})")

    success = await safe_forward(message, DEST_CHANNEL)
    if success:
        _stats["forwarded"] += 1
        logger.info(f"✅ Forwarded → {DEST_CHANNEL}  |  total: {_stats['forwarded']}")
    else:
        _stats["failed"] += 1
        logger.error(f"❌ Failed to forward: {name}")

# ── /addchat ───────────────────────────────────────────────────────────────
@app.on_message(filters.command("addchat") & filters.private)
@admin_only
async def cmd_addchat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply(
            "**Usage:** `/addchat <username or group ID>`\n\n"
            "Examples:\n"
            "• `/addchat CineAlliance`\n"
            "• `/addchat -100987654321`",
            parse_mode="markdown"
        )
        return

    raw = args[1].strip()
    ok, msg = add_chat(raw)

    if ok:
        # Verify we can actually access the chat
        try:
            chat_obj = await client.get_chat(raw)
            extra = f"\n👥 Chat: **{chat_obj.title}** ({getattr(chat_obj, 'members_count', '?')} members)"
        except Exception:
            extra = "\n⚠️ Could not verify chat — make sure the userbot is a member!"

        await message.reply(f"{msg}{extra}\n\nFiles from this chat will now be forwarded to your index channel.", parse_mode="markdown")
        if LOG_CHANNEL:
            try:
                me = await client.get_me()
                await client.send_message(LOG_CHANNEL, f"➕ **Source chat added**\nChat: `{raw}`\nBy: {me.first_name}")
            except Exception:
                pass
    else:
        await message.reply(msg, parse_mode="markdown")

# ── /removechat ────────────────────────────────────────────────────────────
@app.on_message(filters.command("removechat") & filters.private)
@admin_only
async def cmd_removechat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply(
            "**Usage:** `/removechat <username or group ID>`\n\n"
            "Examples:\n"
            "• `/removechat CineAlliance`\n"
            "• `/removechat -100987654321`",
            parse_mode="markdown"
        )
        return

    raw = args[1].strip()
    ok, msg = remove_chat(raw)
    await message.reply(msg, parse_mode="markdown")

    if ok and LOG_CHANNEL:
        try:
            await client.send_message(LOG_CHANNEL, f"➖ **Source chat removed**\nChat: `{raw}`")
        except Exception:
            pass

# ── /listchats ─────────────────────────────────────────────────────────────
@app.on_message(filters.command("listchats") & filters.private)
@admin_only
async def cmd_listchats(client: Client, message: Message):
    text = list_chats(SOURCE_CHATS)
    await message.reply(text, parse_mode="markdown")

# ── /fwrstatus ─────────────────────────────────────────────────────────────
@app.on_message(filters.command("fwrstatus") & filters.private)
@admin_only
async def cmd_status(client: Client, message: Message):
    current = get_all_chats(SOURCE_CHATS)
    me      = await client.get_me()
    text = (
        f"**Forwarder Status**\n\n"
        f"👤 Running as: `{me.first_name}` (@{me.username})\n"
        f"📤 Destination: `{DEST_CHANNEL}`\n"
        f"👀 Watching: `{len(current)}` source chats\n\n"
        f"**Session stats:**\n"
        f"✅ Forwarded: `{_stats['forwarded']}`\n"
        f"❌ Failed: `{_stats['failed']}`\n"
    )
    await message.reply(text, parse_mode="markdown")

# ── /help ──────────────────────────────────────────────────────────────────
@app.on_message(filters.command(["start", "help"]) & filters.private)
async def cmd_help(client: Client, message: Message):
    me = await client.get_me()
    await message.reply(
        f"**TG File Forwarder** — `{me.first_name}`\n\n"
        "Silently forwards files from source groups to your index channel.\n\n"
        "**Commands** _(admins only)_:\n"
        "• `/addchat <username>` — add a source chat\n"
        "• `/removechat <username>` — remove a source chat\n"
        "• `/listchats` — show all source chats\n"
        "• `/fwrstatus` — show forwarder stats\n\n"
        "**How to add Cine Alliance:**\n"
        "1. Join the group with this account\n"
        "2. Send: `/addchat CineAlliance`\n"
        "3. Done — files forwarded instantly 🚀",
        parse_mode="markdown"
    )

# ── Startup ────────────────────────────────────────────────────────────────
async def main():
    await app.start()
    me = await app.get_me()
    logger.info(f"🚀 Forwarder started as: {me.first_name} (@{me.username})")

    current = get_all_chats(SOURCE_CHATS)
    logger.info(f"👀 Watching {len(current)} source chat(s)")
    logger.info(f"📤 Destination: {DEST_CHANNEL}")

    # Verify source chats
    for src in current:
        try:
            chat = await app.get_chat(src)
            logger.info(f"  ✅ {chat.title}")
        except (ChannelPrivate, UserNotParticipant):
            logger.warning(f"  ⚠️  Not a member of: {src} — join first!")
        except Exception as e:
            logger.warning(f"  ⚠️  Cannot verify {src}: {e}")

    if LOG_CHANNEL:
        try:
            await app.send_message(
                LOG_CHANNEL,
                f"✅ **Forwarder started**\n"
                f"As: `{me.first_name}`\n"
                f"Watching `{len(current)}` chats → `{DEST_CHANNEL}`\n\n"
                f"Send `/addchat <group>` to add sources on the fly."
            )
        except Exception:
            pass

    logger.info("⏳ Listening for new files... (send /addchat to add sources)")
    await idle()

    logger.info(f"📊 Summary — Forwarded: {_stats['forwarded']} | Failed: {_stats['failed']}")
    await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
