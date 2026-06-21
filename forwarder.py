"""
Real-time Forwarder — forwarder.py
────────────────────────────────────────────────────────────────
Watches SOURCE_CHATS for new file messages and instantly forwards
them to the correct destination channel.

New features vs v1:
  • Multi-destination routing  (DEST_MOVIES / DEST_SERIES / DEST_SOUTH)
  • Duplicate detection        (file_unique_id via seen_db)
  • Caption watermark removal  (caption_cleaner)
  • Web dashboard              (aiohttp at PORT)
  • /route /routes /dupstats /discover /suggest commands

Commands (ADMINS only) — DM the userbot:
  /addchat <username or id>   — add a source chat at runtime
  /removechat <username or id>— remove a source chat at runtime
  /listchats                  — show all current source chats
  /fwrstatus                  — session stats + routing info
  /route <source> <channel>   — set per-source destination override
  /routes                     — show all routing rules
  /dupstats                   — duplicate detection stats
  /discover                   — scan joined groups for movie sources
  /suggest <keyword>          — search Telegram for public movie groups
  /help                       — all commands

Deploy on Railway — set env vars from .env.example in Variables tab.
Run locally: python forwarder.py
"""
import asyncio
import logging
import os

from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import ChannelPrivate, UserNotParticipant

from config import (
    API_ID, API_HASH, SESSION_STRING,
    SOURCE_CHATS, DEST_CHANNEL, ALLOWED_TYPES, LOG_CHANNEL,
)
from utils import safe_forward, is_allowed_file, get_file_name, get_file_size, human_size
from chats_db import get_all_chats, add_chat, remove_chat, list_chats
from router import get_destination, set_route, remove_route, list_routes, format_type_label
from seen_db import count as seen_count
from caption_cleaner import is_enabled as captions_enabled
from discovery import find_joined_sources, search_public_sources, format_results

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("forwarder")

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
_stats = {"forwarded": 0, "skipped_dup": 0, "failed": 0}


def admin_only(func):
    async def wrapper(client, message: Message):
        if ADMINS and message.from_user and message.from_user.id not in ADMINS:
            await message.reply("⛔ You are not authorized.")
            return
        await func(client, message)
    wrapper.__name__ = func.__name__
    return wrapper


# ── File handler ──────────────────────────────────────────────────────────
@app.on_message(filters.document | filters.video | filters.audio)
async def on_new_file(client: Client, message: Message):
    current_chats = get_all_chats(SOURCE_CHATS)
    chat_id       = message.chat.id
    chat_username = getattr(message.chat, "username", None)

    in_source = (
        chat_id in current_chats or
        (chat_username and chat_username in current_chats) or
        (chat_username and f"@{chat_username}" in current_chats)
    )
    if not in_source:
        return
    if not is_allowed_file(message, ALLOWED_TYPES):
        return

    name  = get_file_name(message)
    size  = human_size(get_file_size(message))
    dest  = get_destination(name, chat_id)
    label = format_type_label(name)
    title = getattr(message.chat, "title", str(chat_id))

    logger.info(f"📥 [{title}] {label} {name} ({size}) → {dest}")

    # safe_forward handles dedup + caption cleaning internally
    success = await safe_forward(message, dest)

    if success:
        _stats["forwarded"] += 1
        logger.info(f"✅ Forwarded → {dest}  |  total: {_stats['forwarded']}")
    else:
        # False = duplicate skipped OR max retries
        from utils import get_unique_id
        from seen_db import is_seen
        uid = get_unique_id(message)
        if uid and is_seen(uid):
            _stats["skipped_dup"] += 1
            logger.debug(f"⏭️  Dup: {name}")
        else:
            _stats["failed"] += 1
            logger.error(f"❌ Failed: {name}")


# ── /addchat ───────────────────────────────────────────────────────────────
@app.on_message(filters.command("addchat") & filters.private)
@admin_only
async def cmd_addchat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply(
            "**Usage:** `/addchat <username or group ID>`\n\n"
            "Examples:\n• `/addchat CineAlliance`\n• `/addchat -100987654321`",
            parse_mode="markdown"
        )
        return
    raw = args[1].strip()
    ok, msg = add_chat(raw)
    if ok:
        try:
            chat_obj = await client.get_chat(raw)
            extra = f"\n👥 **{chat_obj.title}** ({getattr(chat_obj, 'members_count', '?')} members)"
        except Exception:
            extra = "\n⚠️ Could not verify — make sure the userbot is a member!"
        await message.reply(f"{msg}{extra}", parse_mode="markdown")
        if LOG_CHANNEL:
            try:
                await client.send_message(LOG_CHANNEL, f"➕ Source added: `{raw}`")
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
        await message.reply("**Usage:** `/removechat <username or group ID>`", parse_mode="markdown")
        return
    ok, msg = remove_chat(args[1].strip())
    await message.reply(msg, parse_mode="markdown")


# ── /listchats ─────────────────────────────────────────────────────────────
@app.on_message(filters.command("listchats") & filters.private)
@admin_only
async def cmd_listchats(client: Client, message: Message):
    await message.reply(list_chats(SOURCE_CHATS), parse_mode="markdown")


# ── /fwrstatus ─────────────────────────────────────────────────────────────
@app.on_message(filters.command("fwrstatus") & filters.private)
@admin_only
async def cmd_status(client: Client, message: Message):
    current = get_all_chats(SOURCE_CHATS)
    me      = await client.get_me()
    total   = _stats["forwarded"] + _stats["skipped_dup"] + _stats["failed"]
    dup_pct = f"{_stats['skipped_dup']/total*100:.0f}%" if total else "0%"
    text = (
        f"**Forwarder Status**\n\n"
        f"👤 Running as: `{me.first_name}` (@{me.username})\n"
        f"👀 Watching: `{len(current)}` source chats\n\n"
        f"**Session stats:**\n"
        f"✅ Forwarded: `{_stats['forwarded']}`\n"
        f"⏭️ Duplicates skipped: `{_stats['skipped_dup']}` ({dup_pct})\n"
        f"❌ Failed: `{_stats['failed']}`\n"
        f"🗂️ Seen DB total: `{seen_count():,}` unique files\n\n"
        f"**Routing:**\n" + list_routes() + "\n\n"
        f"**Caption cleaning:** {'✅ on' if captions_enabled() else '⛔ off'}"
    )
    await message.reply(text, parse_mode="markdown")


# ── /route ─────────────────────────────────────────────────────────────────
@app.on_message(filters.command("route") & filters.private)
@admin_only
async def cmd_route(client: Client, message: Message):
    args = message.text.split(None, 2)
    if len(args) < 3:
        await message.reply(
            "**Usage:** `/route <source_chat> <dest_channel>`\n\n"
            "**Example:** `/route CineAlliance -1001111111111`\n\n"
            "Set env vars `DEST_MOVIES`, `DEST_SERIES`, `DEST_SOUTH` for auto-routing.\n\n"
            + list_routes(),
            parse_mode="markdown"
        )
        return
    try:
        dest_int = int(args[2].strip())
    except ValueError:
        await message.reply("❌ Destination must be a channel ID (negative integer).", parse_mode="markdown")
        return
    msg = set_route(args[1].strip(), dest_int)
    await message.reply(msg, parse_mode="markdown")


# ── /routes ────────────────────────────────────────────────────────────────
@app.on_message(filters.command("routes") & filters.private)
@admin_only
async def cmd_routes(client: Client, message: Message):
    await message.reply(list_routes(), parse_mode="markdown")


# ── /dupstats ──────────────────────────────────────────────────────────────
@app.on_message(filters.command("dupstats") & filters.private)
@admin_only
async def cmd_dupstats(client: Client, message: Message):
    total = _stats["forwarded"] + _stats["skipped_dup"]
    pct   = f"{_stats['skipped_dup']/total*100:.1f}%" if total > 0 else "0%"
    await message.reply(
        f"**Duplicate Detection Stats**\n\n"
        f"🗂️ Seen DB (all-time): `{seen_count():,}` unique file IDs\n\n"
        f"**This session:**\n"
        f"✅ Forwarded: `{_stats['forwarded']}`\n"
        f"⏭️ Skipped (duplicate): `{_stats['skipped_dup']}` ({pct} of traffic)\n\n"
        f"_Duplicate detection uses Telegram's `file_unique_id` —_\n"
        f"_the same file in two groups is always the same ID._",
        parse_mode="markdown"
    )


# ── /discover ──────────────────────────────────────────────────────────────
@app.on_message(filters.command("discover") & filters.private)
@admin_only
async def cmd_discover(client: Client, message: Message):
    msg = await message.reply("🔍 Scanning your joined groups for movie sources...")
    results = await find_joined_sources(client, limit=20)
    text = format_results(results, "Movie groups you've joined")
    await msg.edit(text, parse_mode="markdown")


# ── /suggest ───────────────────────────────────────────────────────────────
@app.on_message(filters.command("suggest") & filters.private)
@admin_only
async def cmd_suggest(client: Client, message: Message):
    args = message.text.split(None, 1)
    query = args[1].strip() if len(args) > 1 else "movies 1080p"
    msg = await message.reply(f"🔍 Searching Telegram for: `{query}`...", parse_mode="markdown")
    results = await search_public_sources(client, query, limit=10)
    text = format_results(results, f"Public groups matching '{query}'")
    await msg.edit(text, parse_mode="markdown")


# ── /help ──────────────────────────────────────────────────────────────────
@app.on_message(filters.command(["start", "help"]) & filters.private)
async def cmd_help(client: Client, message: Message):
    me = await client.get_me()
    await message.reply(
        f"**TG File Forwarder** — `{me.first_name}`\n\n"
        "Silently forwards files from source groups to your index channel.\n"
        "Includes routing, dedup detection, caption cleaning, and dashboard.\n\n"
        "**Source commands:**\n"
        "• `/addchat <chat>` — add a source chat\n"
        "• `/removechat <chat>` — remove a source chat\n"
        "• `/listchats` — list all sources\n\n"
        "**Routing:**\n"
        "• `/route <src> <channel>` — override destination for a source\n"
        "• `/routes` — show all routing rules\n\n"
        "**Stats:**\n"
        "• `/fwrstatus` — full session stats + routing\n"
        "• `/dupstats` — duplicate detection stats\n\n"
        "**Discovery:**\n"
        "• `/discover` — scan joined groups for movie sources\n"
        "• `/suggest <keyword>` — search Telegram for public groups\n",
        parse_mode="markdown"
    )


# ── Startup ────────────────────────────────────────────────────────────────
async def main():
    await app.start()
    me = await app.get_me()
    logger.info(f"🚀 Forwarder started as: {me.first_name} (@{me.username})")

    current = get_all_chats(SOURCE_CHATS)
    logger.info(f"👀 Watching {len(current)} source chat(s)")
    logger.info(f"📤 Routing enabled — seen DB: {seen_count():,} unique IDs loaded")
    logger.info(f"✏️  Caption cleaning: {'on' if captions_enabled() else 'off'}")

    # Verify source chats
    for src in current:
        try:
            chat = await app.get_chat(src)
            logger.info(f"  ✅ {chat.title}")
        except (ChannelPrivate, UserNotParticipant):
            logger.warning(f"  ⚠️  Not a member of: {src} — join first!")
        except Exception as e:
            logger.warning(f"  ⚠️  Cannot verify {src}: {e}")

    # Start web dashboard as background task
    port = int(os.environ.get("PORT", 8080))
    try:
        from dashboard import start_dashboard
        asyncio.create_task(start_dashboard(stats_getter=lambda: _stats, port=port))
        logger.info(f"🌐 Dashboard starting on port {port}")
    except ImportError:
        logger.warning("dashboard.py not found — web dashboard disabled")

    if LOG_CHANNEL:
        try:
            dest_info = os.environ.get("DEST_MOVIES") and "multi-channel routing" or f"→ {DEST_CHANNEL}"
            await app.send_message(
                LOG_CHANNEL,
                f"✅ **Forwarder started**\n"
                f"As: `{me.first_name}`\n"
                f"Watching `{len(current)}` chats | {dest_info}\n"
                f"Dedup: {seen_count():,} IDs | Caption clean: {'on' if captions_enabled() else 'off'}"
            )
        except Exception:
            pass

    logger.info("⏳ Listening for new files...")
    await idle()

    logger.info(
        f"📊 Final — Forwarded: {_stats['forwarded']} | "
        f"Dup-skipped: {_stats['skipped_dup']} | Failed: {_stats['failed']}"
    )
    await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
