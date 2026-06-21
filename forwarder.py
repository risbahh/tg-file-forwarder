"""
Real-time Forwarder — forwarder.py
Commands (ADMINS only) — DM the userbot:
  /addchat /removechat /listchats /fwrstatus
  /route /routes /dupstats /resetdups
  /pausefwd /resumefwd
  /srcstats
  /discover /suggest /help
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
from seen_db import count as seen_count, reset as seen_reset
from caption_cleaner import is_enabled as captions_enabled
from discovery import find_joined_sources, search_public_sources, format_results
import stats_db

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
_stats = {"forwarded": 0, "skipped_dup": 0, "failed": 0, "skipped_paused": 0}
_paused = False


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

    name  = get_file_name(message)
    size  = human_size(get_file_size(message))
    dest  = get_destination(name, chat_id)
    label = format_type_label(name)
    title = getattr(message.chat, "title", str(chat_id))

    logger.info(f"📥 [{title}] {label} {name} ({size}) → {dest}")

    success = await safe_forward(message, dest)

    if success:
        _stats["forwarded"] += 1
        stats_db.record(chat_id, title)   # ← per-source stat
        logger.info(f"✅ Forwarded → {dest}  |  total: {_stats['forwarded']}")
    else:
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
    pause_line = f"\n⏸️ **FORWARDING PAUSED** — {_stats['skipped_paused']} files dropped\n" if _paused else ""
    text = (
        f"**Forwarder Status**\n"
        f"{pause_line}\n"
        f"👤 Running as: `{me.first_name}` (@{me.username})\n"
        f"👀 Watching: `{len(current)}` source chats\n\n"
        f"**Session stats:**\n"
        f"✅ Forwarded: `{_stats['forwarded']}`\n"
        f"⏭️ Duplicates skipped: `{_stats['skipped_dup']}` ({dup_pct})\n"
        f"⏸️ Skipped while paused: `{_stats['skipped_paused']}`\n"
        f"❌ Failed: `{_stats['failed']}`\n"
        f"🗂️ Seen DB: `{seen_count():,}` unique files\n"
        f"📊 All-time forwarded: `{stats_db.total():,}` files\n\n"
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
        f"_Same file in two groups = same ID → forwarded once, skipped on repeat._",
        parse_mode="markdown"
    )


# ── /resetdups ─────────────────────────────────────────────────────────────
@app.on_message(filters.command("resetdups") & filters.private)
@admin_only
async def cmd_resetdups(client: Client, message: Message):
    args = message.text.split(None, 1)
    confirmed = len(args) > 1 and args[1].strip().lower() == "confirm"
    current_count = seen_count()
    if not confirmed:
        await message.reply(
            f"⚠️ **Reset Duplicate Memory?**\n\n"
            f"This will erase `{current_count:,}` tracked file IDs from `seen.json`.\n\n"
            f"• No files deleted from Telegram — only memory cleared\n"
            f"• Re-posted files will be forwarded again after reset\n\n"
            f"**To confirm:** `/resetdups confirm`",
            parse_mode="markdown"
        )
        return
    seen_reset()
    _stats["skipped_dup"] = 0
    logger.warning(f"🗑️ seen.json cleared by admin — was {current_count:,} IDs")
    await message.reply(
        f"✅ **Duplicate memory cleared.**\n\n"
        f"Erased `{current_count:,}` file IDs from `seen.json`.\n"
        f"_Use `/dupstats` to confirm the DB is at 0._",
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
@app.on_message(filters.command("pausefwd") & filters.private)
@admin_only
async def cmd_pausefwd(client: Client, message: Message):
    global _paused
    if _paused:
        await message.reply(
            f"⏸️ Already paused.\nDropped so far: `{_stats['skipped_paused']}`\n\nSend `/resumefwd` to resume.",
            parse_mode="markdown"
        )
        return
    _paused = True
    logger.warning("⏸️ Forwarding PAUSED by admin")
    await message.reply(
        "⏸️ **Forwarding paused.**\n\nNew files will be ignored until you send `/resumefwd`.",
        parse_mode="markdown"
    )
    if LOG_CHANNEL:
        try:
            me = await client.get_me()
            await client.send_message(LOG_CHANNEL, f"⏸️ Forwarding **paused** by `{me.first_name}`")
        except Exception:
            pass


# ── /resumefwd ─────────────────────────────────────────────────────────────
@app.on_message(filters.command("resumefwd") & filters.private)
@admin_only
async def cmd_resumefwd(client: Client, message: Message):
    global _paused
    if not _paused:
        await message.reply("▶️ Forwarding is already running — nothing to resume.", parse_mode="markdown")
        return
    dropped = _stats["skipped_paused"]
    _paused = False
    _stats["skipped_paused"] = 0
    logger.info(f"▶️ Forwarding RESUMED — dropped {dropped} files while paused")
    await message.reply(
        f"▶️ **Forwarding resumed.**\n\nDropped while paused: `{dropped}` files",
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
@app.on_message(filters.command("srcstats") & filters.private)
@admin_only
async def cmd_srcstats(client: Client, message: Message):
    rows = stats_db.get_all()
    grand_total = stats_db.total()

    if not rows:
        await message.reply(
            "📊 No forwarding stats yet.\n_Stats are recorded per successful forward._",
            parse_mode="markdown"
        )
        return

    lines = ["**📊 Per-Source Forwarding Stats**\n"]
    for i, row in enumerate(rows[:20], 1):     # cap at 20 sources
        pct = f"{row['count']/grand_total*100:.1f}%" if grand_total else "0%"
        last = row.get("last_seen", "")[:10]   # YYYY-MM-DD
        lines.append(
            f"{i}. **{row['title']}**\n"
            f"   `{row['count']:,}` files forwarded ({pct}) — last: {last}"
        )

    lines.append(f"\n**Total (all-time):** `{grand_total:,}` files")
    if len(rows) > 20:
        lines.append(f"_...and {len(rows)-20} more sources_")

    await message.reply("\n".join(lines), parse_mode="markdown")


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
        "**Source commands:**\n"
        "• `/addchat <chat>` — add a source chat\n"
        "• `/removechat <chat>` — remove a source chat\n"
        "• `/listchats` — list all sources\n\n"
        "**Routing:**\n"
        "• `/route <src> <channel>` — override destination for a source\n"
        "• `/routes` — show all routing rules\n\n"
        "**Stats & dedup:**\n"
        "• `/fwrstatus` — full session stats\n"
        "• `/dupstats` — duplicate detection stats\n"
        "• `/srcstats` — files forwarded per source group\n"
        "• `/resetdups` — clear duplicate memory (with confirmation)\n\n"
        "**Pause / Resume:**\n"
        "• `/pausefwd` — pause all forwarding\n"
        "• `/resumefwd` — resume forwarding\n\n"
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
    logger.info(f"📤 Routing — seen DB: {seen_count():,} IDs | all-time: {stats_db.total():,} files")
    logger.info(f"✏️  Caption cleaning: {'on' if captions_enabled() else 'off'}")

    for src in current:
        try:
            chat = await app.get_chat(src)
            logger.info(f"  ✅ {chat.title}")
        except (ChannelPrivate, UserNotParticipant):
            logger.warning(f"  ⚠️  Not a member of: {src}")
        except Exception as e:
            logger.warning(f"  ⚠️  Cannot verify {src}: {e}")

    port = int(os.environ.get("PORT", 8080))
    try:
        from dashboard import start_dashboard
        asyncio.create_task(start_dashboard(stats_getter=lambda: _stats, port=port))
        logger.info(f"🌐 Dashboard on port {port}")
    except ImportError:
        logger.warning("dashboard.py not found — web dashboard disabled")

    if LOG_CHANNEL:
        try:
            dest_info = "multi-channel routing" if os.environ.get("DEST_MOVIES") else f"→ {DEST_CHANNEL}"
            await app.send_message(LOG_CHANNEL,
                f"✅ **Forwarder started**\nAs: `{me.first_name}`\n"
                f"Watching `{len(current)}` chats | {dest_info}\n"
                f"Dedup: {seen_count():,} IDs | All-time: {stats_db.total():,} files")
        except Exception:
            pass

    logger.info("⏳ Listening for new files...")
    await idle()
    logger.info(f"📊 Final — Forwarded: {_stats['forwarded']} | Dup-skipped: {_stats['skipped_dup']} | Failed: {_stats['failed']}")
    await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
