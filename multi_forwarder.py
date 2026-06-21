"""
Multi-Account Forwarder — multi_forwarder.py
Rotates 2–3 userbot accounts to avoid FloodWait.

Commands: /addchat /removechat /listchats /route /routes
          /resetdups /pausefwd /resumefwd /srcstats /poolstatus /help
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
from seen_db import is_seen, mark_seen, count as seen_count, reset as seen_reset
from account_pool import AccountPool
import stats_db

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

_listener = Client(
    "multi_fwd_listener",
    api_id=API_ID, api_hash=API_HASH,
    session_string=SESSION_STRING,
)
_pool: AccountPool | None = None

_stats  = {"forwarded": 0, "skipped_dup": 0, "failed": 0, "skipped_paused": 0}
_paused = False


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
    global _paused

    if _paused:
        _stats["skipped_paused"] += 1
        return

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

    ok = await _pool.forward(message, dest) if _pool else False

    if ok:
        for attr in ("document", "video", "audio"):
            obj = getattr(message, attr, None)
            if obj:
                uid = getattr(obj, "file_unique_id", None)
                if uid:
                    mark_seen(uid)
                break
        _stats["forwarded"] += 1
        stats_db.record(chat_id, title)   # ← per-source stat
        logger.info(f"✅ Forwarded → {dest}  |  total: {_stats['forwarded']}")
    else:
        _stats["failed"] += 1
        logger.error(f"❌ All accounts failed for: {name}")


# ── /poolstatus ────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("poolstatus") & filters.private)
@admin_only
async def cmd_poolstatus(client: Client, message: Message):
    if not _pool:
        await message.reply("Pool not initialized yet.")
        return
    text  = await _pool.status()
    pause_line = f"\n⏸️ **PAUSED** — {_stats['skipped_paused']} dropped\n" if _paused else ""
    text += (
        f"\n{pause_line}"
        f"**Session:**\n"
        f"✅ Forwarded: `{_stats['forwarded']}`\n"
        f"⏭️ Dup-skipped: `{_stats['skipped_dup']}`\n"
        f"⏸️ Paused-dropped: `{_stats['skipped_paused']}`\n"
        f"❌ Failed: `{_stats['failed']}`\n"
        f"🗂️ Seen DB: `{seen_count():,}` unique files\n"
        f"📊 All-time: `{stats_db.total():,}` files"
    )
    await message.reply(text, parse_mode="markdown")


# ── /addchat ───────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("addchat") & filters.private)
@admin_only
async def cmd_addchat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply("Usage: `/addchat <username or id>`", parse_mode="markdown")
        return
    ok, msg = add_chat(args[1].strip())
    await message.reply(msg, parse_mode="markdown")


# ── /removechat ────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("removechat") & filters.private)
@admin_only
async def cmd_removechat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply("Usage: `/removechat <username or id>`", parse_mode="markdown")
        return
    ok, msg = remove_chat(args[1].strip())
    await message.reply(msg, parse_mode="markdown")


# ── /listchats ─────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("listchats") & filters.private)
@admin_only
async def cmd_listchats(client: Client, message: Message):
    await message.reply(list_chats(SOURCE_CHATS), parse_mode="markdown")


# ── /route ─────────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("route") & filters.private)
@admin_only
async def cmd_route(client: Client, message: Message):
    args = message.text.split(None, 2)
    if len(args) < 3:
        await message.reply(
            "**Usage:** `/route <source_chat> <dest_channel>`\n\n" + list_routes(),
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
@_listener.on_message(filters.command("routes") & filters.private)
@admin_only
async def cmd_routes(client: Client, message: Message):
    await message.reply(list_routes(), parse_mode="markdown")


# ── /resetdups ─────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("resetdups") & filters.private)
@admin_only
async def cmd_resetdups(client: Client, message: Message):
    args = message.text.split(None, 1)
    confirmed = len(args) > 1 and args[1].strip().lower() == "confirm"
    current_count = seen_count()
    if not confirmed:
        await message.reply(
            f"⚠️ **Reset Duplicate Memory?**\n\n"
            f"Will erase `{current_count:,}` tracked IDs from `seen.json`.\n\n"
            f"**To confirm:** `/resetdups confirm`",
            parse_mode="markdown"
        )
        return
    seen_reset()
    _stats["skipped_dup"] = 0
    logger.warning(f"🗑️ seen.json cleared — was {current_count:,} IDs")
    await message.reply(
        f"✅ **Duplicate memory cleared.**\nErased `{current_count:,}` file IDs.",
        parse_mode="markdown"
    )
    if LOG_CHANNEL:
        try:
            me = await client.get_me()
            await client.send_message(LOG_CHANNEL,
                f"🗑️ **seen.json reset** by `{me.first_name}`\nCleared `{current_count:,}` IDs.")
        except Exception:
            pass


# ── /pausefwd ──────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("pausefwd") & filters.private)
@admin_only
async def cmd_pausefwd(client: Client, message: Message):
    global _paused
    if _paused:
        await message.reply(
            f"⏸️ Already paused. Dropped: `{_stats['skipped_paused']}`\n\nSend `/resumefwd` to resume.",
            parse_mode="markdown"
        )
        return
    _paused = True
    logger.warning("⏸️ Forwarding PAUSED")
    await message.reply("⏸️ **Forwarding paused.**\nSend `/resumefwd` to resume.", parse_mode="markdown")
    if LOG_CHANNEL:
        try:
            me = await client.get_me()
            await client.send_message(LOG_CHANNEL, f"⏸️ Forwarding **paused** by `{me.first_name}`")
        except Exception:
            pass


# ── /resumefwd ─────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("resumefwd") & filters.private)
@admin_only
async def cmd_resumefwd(client: Client, message: Message):
    global _paused
    if not _paused:
        await message.reply("▶️ Already running — nothing to resume.", parse_mode="markdown")
        return
    dropped = _stats["skipped_paused"]
    _paused = False
    _stats["skipped_paused"] = 0
    logger.info(f"▶️ Forwarding RESUMED — dropped {dropped} while paused")
    await message.reply(
        f"▶️ **Forwarding resumed.**\nDropped while paused: `{dropped}` files",
        parse_mode="markdown"
    )
    if LOG_CHANNEL:
        try:
            me = await client.get_me()
            await client.send_message(LOG_CHANNEL,
                f"▶️ Forwarding **resumed** by `{me.first_name}`\nDropped: `{dropped}` files")
        except Exception:
            pass


# ── /srcstats ──────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("srcstats") & filters.private)
@admin_only
async def cmd_srcstats(client: Client, message: Message):
    rows = stats_db.get_all()
    grand_total = stats_db.total()

    if not rows:
        await message.reply(
            "📊 No forwarding stats yet.\n_Stats recorded per successful forward._",
            parse_mode="markdown"
        )
        return

    lines = ["**📊 Per-Source Forwarding Stats**\n"]
    for i, row in enumerate(rows[:20], 1):
        pct  = f"{row['count']/grand_total*100:.1f}%" if grand_total else "0%"
        last = row.get("last_seen", "")[:10]
        lines.append(
            f"{i}. **{row['title']}**\n"
            f"   `{row['count']:,}` files ({pct}) — last: {last}"
        )

    lines.append(f"\n**Total (all-time):** `{grand_total:,}` files")
    if len(rows) > 20:
        lines.append(f"_...and {len(rows)-20} more sources_")

    await message.reply("\n".join(lines), parse_mode="markdown")


# ── /help ─────────────────────────────────────────────────────────────────
@_listener.on_message(filters.command(["start", "help"]) & filters.private)
async def cmd_help(client: Client, message: Message):
    me = await client.get_me()
    await message.reply(
        f"**Multi-Account Forwarder** — `{me.first_name}`\n\n"
        "**Commands:**\n"
        "• `/addchat` / `/removechat` / `/listchats`\n"
        "• `/route <src> <dest>` / `/routes`\n"
        "• `/resetdups` — clear duplicate memory\n"
        "• `/pausefwd` — pause all forwarding\n"
        "• `/resumefwd` — resume forwarding\n"
        "• `/srcstats` — files forwarded per source group\n"
        "• `/poolstatus` — account pool + session stats\n",
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
    logger.info(f"👀 Watching {len(current)} sources | all-time: {stats_db.total():,} files")

    if LOG_CHANNEL:
        try:
            await _listener.send_message(LOG_CHANNEL,
                f"🏊 **Multi-account forwarder started**\n"
                f"Listener: `{me.first_name}` | Pool: {_pool.account_count()} accounts\n"
                f"Sources: {len(current)} | All-time: {stats_db.total():,} files forwarded")
        except Exception:
            pass

    await idle()
    await _pool.stop_all()
    await _listener.stop()

if __name__ == "__main__":
    asyncio.run(main())
