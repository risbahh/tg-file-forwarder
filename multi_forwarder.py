"""
Multi-Account Forwarder — multi_forwarder.py
─────────────────────────────────────────────
Runs forwarder.py logic using a rotating POOL of 2–3 userbot accounts.
When Account 1 hits FloodWait, Account 2 takes over instantly.
Result: near-zero downtime, 2–3× effective forwarding capacity.

Setup (add to Railway Variables):
  SESSION_STRING    = BQA...  ← Account 1 (required, already set)
  SESSION_STRING_2  = BQA...  ← Account 2 (optional)
  SESSION_STRING_3  = BQA...  ← Account 3 (optional)

Deploy:
  Option A — Replace forwarder.py in Procfile:
    worker: python multi_forwarder.py
  
  Option B — Run alongside forwarder.py (if forwarder.py uses Account 1 only):
    worker:  python forwarder.py
    worker2: python multi_forwarder.py   ← uses accounts 2 + 3

Commands are identical to forwarder.py — DM the userbot account.
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
from utils import is_allowed_file, get_file_name, get_file_size, human_size
from chats_db import get_all_chats, add_chat, remove_chat, list_chats
from router import get_destination, set_route, remove_route, list_routes, format_type_label
from seen_db import is_seen, mark_seen, count as seen_count
from account_pool import AccountPool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("multi_forwarder")

ADMINS = [
    int(x.strip()) for x in os.environ.get("ADMINS", "").split(",")
    if x.strip().isdigit()
]

# ── Main (listener) client — Account 1 always, used for commands + listening ─
_listener = Client(
    "multi_fwd_listener",
    api_id=API_ID, api_hash=API_HASH,
    session_string=SESSION_STRING,
)
_pool: AccountPool | None = None

# ── Stats ─────────────────────────────────────────────────────────────────
_stats = {"forwarded": 0, "skipped_dup": 0, "failed": 0}


def admin_only(func):
    async def wrapper(client, message: Message):
        if ADMINS and message.from_user and message.from_user.id not in ADMINS:
            await message.reply("⛔ Not authorized.")
            return
        await func(client, message)
    wrapper.__name__ = func.__name__
    return wrapper


# ── File handler ──────────────────────────────────────────────────────────
@_listener.on_message(filters.document | filters.video | filters.audio)
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

    # Duplicate check
    for attr in ("document", "video", "audio"):
        obj = getattr(message, attr, None)
        if obj:
            uid = getattr(obj, "file_unique_id", None)
            if uid and is_seen(uid):
                _stats["skipped_dup"] += 1
                return
            break

    name  = get_file_name(message)
    size  = human_size(get_file_size(message))
    dest  = get_destination(name, chat_id)
    label = format_type_label(name)
    title = getattr(message.chat, "title", str(chat_id))

    logger.info(f"📥 [{title}] {label} {name} ({size}) → {dest}")

    if _pool:
        ok = await _pool.forward(message, dest)
    else:
        ok = False

    if ok:
        for attr in ("document", "video", "audio"):
            obj = getattr(message, attr, None)
            if obj:
                uid = getattr(obj, "file_unique_id", None)
                if uid:
                    mark_seen(uid)
                break
        _stats["forwarded"] += 1
        logger.info(f"✅ Forwarded → {dest}  |  total: {_stats['forwarded']}")
    else:
        _stats["failed"] += 1
        logger.error(f"❌ All accounts failed for: {name}")


# ── Commands ──────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("poolstatus") & filters.private)
@admin_only
async def cmd_poolstatus(client: Client, message: Message):
    if not _pool:
        await message.reply("Pool not initialized yet.")
        return
    text  = await _pool.status()
    text += (
        f"\n\n**Session:**\n"
        f"✅ Forwarded: `{_stats['forwarded']}`\n"
        f"⏭️ Dup-skipped: `{_stats['skipped_dup']}`\n"
        f"❌ Failed: `{_stats['failed']}`\n"
        f"🗂️ Seen DB: `{seen_count():,}` unique files"
    )
    await message.reply(text, parse_mode="markdown")


@_listener.on_message(filters.command("addchat") & filters.private)
@admin_only
async def cmd_addchat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply("Usage: `/addchat <username or id>`", parse_mode="markdown")
        return
    ok, msg = add_chat(args[1].strip())
    await message.reply(msg, parse_mode="markdown")


@_listener.on_message(filters.command("removechat") & filters.private)
@admin_only
async def cmd_removechat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply("Usage: `/removechat <username or id>`", parse_mode="markdown")
        return
    ok, msg = remove_chat(args[1].strip())
    await message.reply(msg, parse_mode="markdown")


@_listener.on_message(filters.command("listchats") & filters.private)
@admin_only
async def cmd_listchats(client: Client, message: Message):
    await message.reply(list_chats(SOURCE_CHATS), parse_mode="markdown")


@_listener.on_message(filters.command("route") & filters.private)
@admin_only
async def cmd_route(client: Client, message: Message):
    args = message.text.split(None, 2)
    if len(args) < 3:
        await message.reply(
            "**Usage:** `/route <source_chat> <dest_channel>`\n\n"
            "**Example:** `/route CineAlliance -1001111111111`\n\n"
            + list_routes(),
            parse_mode="markdown"
        )
        return
    # FIX: wrap int() conversion in try/except — crashes without it on bad input
    try:
        dest_int = int(args[2].strip())
    except ValueError:
        await message.reply("❌ Destination must be a channel ID (negative integer).", parse_mode="markdown")
        return
    msg = set_route(args[1].strip(), dest_int)
    await message.reply(msg, parse_mode="markdown")


@_listener.on_message(filters.command("routes") & filters.private)
@admin_only
async def cmd_routes(client: Client, message: Message):
    await message.reply(list_routes(), parse_mode="markdown")


@_listener.on_message(filters.command(["start", "help"]) & filters.private)
async def cmd_help(client: Client, message: Message):
    me = await client.get_me()
    await message.reply(
        f"**Multi-Account Forwarder** — `{me.first_name}`\n\n"
        "Same as forwarder.py but uses a pool of accounts to avoid FloodWait.\n\n"
        "**Commands:**\n"
        "• `/addchat <chat>` — add source\n"
        "• `/removechat <chat>` — remove source\n"
        "• `/listchats` — list sources\n"
        "• `/route <src> <dest>` — set routing rule\n"
        "• `/routes` — show all routing rules\n"
        "• `/poolstatus` — account pool stats\n",
        parse_mode="markdown"
    )


# ── Startup ───────────────────────────────────────────────────────────────
async def main():
    global _pool

    await _listener.start()
    me = await _listener.get_me()
    logger.info(f"🎧 Listener started as: {me.first_name} (@{me.username})")

    _pool = await AccountPool.create()
    logger.info(f"🏊 Pool: {_pool.account_count()} account(s) loaded")

    current = get_all_chats(SOURCE_CHATS)
    logger.info(f"👀 Watching {len(current)} source chat(s) → routing enabled")

    if LOG_CHANNEL:
        try:
            await _listener.send_message(
                LOG_CHANNEL,
                f"🏊 **Multi-account forwarder started**\n"
                f"Listener: `{me.first_name}`\n"
                f"Pool: {_pool.account_count()} account(s)\n"
                f"Sources: {len(current)}\n"
                f"Routing: {'DEST_MOVIES/SERIES/SOUTH' if os.environ.get('DEST_MOVIES') else 'single channel'}"
            )
        except Exception:
            pass

    await idle()
    await _pool.stop_all()
    await _listener.stop()

if __name__ == "__main__":
    asyncio.run(main())
