"""
Multi-Account Forwarder — multi_forwarder.py
Commands (ADMINS only — DM the userbot):
  Source:    /addchat /removechat /listchats /joinchat
  Stats:     /srcstats /resetdups /export
  Routing:   /route /routes
  Pause:     /pausefwd /resumefwd
  Captions:  /setcaption /strippatterns /cleancaptions /stopcleaning
  Recovery:  /failedstats /retry /clearfailed
  Filter:    /keywords /ignorechat /unignorechat /listignored
  Discovery: /discover /suggest
  Pool:      /poolstatus /assignsource /unassignsource /assignments
  Misc:      /help
"""
import asyncio
import logging
import os

from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import (
    ChannelPrivate, UserNotParticipant,
    SessionRevoked, AuthKeyUnregistered, UserDeactivated,
)

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
import strip_patterns as sp_db
import caption_suffix as cs_db
import failed_db
import ignore_db
import keyword_filter
from dashboard import start_dashboard
from caption_cleaner import is_enabled as captions_enabled
from discovery import find_joined_sources, search_public_sources, format_results

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

PORT = int(os.environ.get("PORT", "8080"))

_listener = Client(
    "multi_fwd_listener",
    api_id=API_ID, api_hash=API_HASH,
    session_string=SESSION_STRING,
)
_pool: AccountPool | None = None
_stats  = {"forwarded": 0, "skipped_dup": 0, "failed": 0, "skipped_paused": 0}
_paused = False
_cleaning_task: asyncio.Task | None = None
_stop_cleaning  = False


def admin_only(func):
    async def wrapper(client, message: Message):
        if ADMINS and message.from_user and message.from_user.id not in ADMINS:
            await message.reply("⛔ Not authorized.")
            return
        await func(client, message)
    wrapper.__name__ = func.__name__
    return wrapper


# ── Session watchdog ───────────────────────────────────────────────────────
async def _session_watchdog():
    await asyncio.sleep(60)
    while True:
        await asyncio.sleep(300)
        try:
            await _listener.get_me()
        except (SessionRevoked, AuthKeyUnregistered, UserDeactivated) as e:
            alert = (
                f"⚠️ **Session Revoked!**\n\nError: `{type(e).__name__}`\n\n"
                f"The forwarder has stopped. Regenerate SESSION_STRING and redeploy."
            )
            logger.critical(f"🔴 SESSION REVOKED: {e}")
            if LOG_CHANNEL:
                try: await _listener.send_message(LOG_CHANNEL, alert)
                except Exception: pass
            for uid in ADMINS:
                try: await _listener.send_message(uid, alert)
                except Exception: pass
            await asyncio.sleep(5)
            os._exit(1)
        except Exception:
            pass


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

    # Ignore-chat check
    if ignore_db.is_ignored(chat_id):
        logger.debug(f"U0001f6ab Ignored chat skipped: {chat_id}")
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

    # Keyword filter check
    caption_text = message.caption or ""
    if not keyword_filter.passes(f"{name} {caption_text}"):
        logger.info(f"🔍 Keyword filter blocked: {name}")
        _stats["skipped_paused"] += 1
        return

    ok = await _pool.forward_from_source(message, dest, str(chat_id)) if _pool else False

    if ok:
        for attr in ("document", "video", "audio"):
            obj = getattr(message, attr, None)
            if obj:
                uid = getattr(obj, "file_unique_id", None)
                if uid: mark_seen(uid)
                break
        _stats["forwarded"] += 1
        stats_db.record(chat_id, title)
        logger.info(f"✅ Forwarded → {dest}  |  total: {_stats['forwarded']}")
    else:
        _stats["failed"] += 1
        logger.error(f"❌ All accounts failed for: {name}")



# ── /poolstatus ────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("poolstatus") & filters.private)
@admin_only
async def cmd_poolstatus(client: Client, message: Message):
    if not _pool:
        await message.reply("⚠️ Pool not initialized yet.")
        return
    text = await _pool.status()
    # Append global session stats
    text += (
        f"\n\n**Session totals:**\n"
        f"✅ Forwarded: `{_stats['forwarded']:,}`\n"
        f"⏭️ Dedup skipped: `{_stats['skipped_dup']:,}`\n"
        f"❌ Failed: `{_stats['failed']:,}`\n"
        f"⏸️ Paused/filtered: `{_stats['skipped_paused']:,}`"
    )
    await message.reply(text, parse_mode="markdown")


# ── /assignsource ──────────────────────────────────────────────────────────
@_listener.on_message(filters.command("assignsource") & filters.private)
@admin_only
async def cmd_assignsource(client: Client, message: Message):
    """
    /assignsource <chat_id or @username> <account_number>
    
    Pin a source chat to a specific account for forwarding.
    The listener always watches all sources; this controls which
    account is USED to forward files from that source.
    FloodWait failover still applies — if the assigned account is
    unavailable, the next free account takes over automatically.
    
    Example: /assignsource @MoviesSource 2
    """
    if not _pool:
        await message.reply("⚠️ Pool not initialized yet.")
        return
    args = message.text.split()
    if len(args) < 3 or not args[2].isdigit():
        await message.reply(
            "**Usage:** `/assignsource <chat_id or @username> <account_number>`\n\n"
            "Example: `/assignsource @MoviesSource 2`\n"
            f"Available accounts: 1 to {_pool.account_count()}",
            parse_mode="markdown"
        )
        return
    source   = args[1].strip()
    acc_num  = int(args[2]) - 1   # convert to 0-based
    err = _pool.assign(source, acc_num)
    if err:
        await message.reply(f"❌ {err}", parse_mode="markdown")
        return
    await message.reply(
        f"📌 **Source assigned**\n\n"
        f"Source: `{source}`\n"
        f"→ Account {acc_num + 1}\n\n"
        f"Failover is automatic if that account hits FloodWait.",
        parse_mode="markdown"
    )


# ── /unassignsource ────────────────────────────────────────────────────────
@_listener.on_message(filters.command("unassignsource") & filters.private)
@admin_only
async def cmd_unassignsource(client: Client, message: Message):
    if not _pool:
        await message.reply("⚠️ Pool not initialized yet.")
        return
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply("**Usage:** `/unassignsource <chat_id or @username>`", parse_mode="markdown")
        return
    source = args[1].strip()
    removed = _pool.unassign(source)
    if removed:
        await message.reply(
            f"✅ Assignment removed for `{source}`\n"
            f"It will now use round-robin across all accounts.",
            parse_mode="markdown"
        )
    else:
        await message.reply(f"ℹ️ `{source}` had no specific assignment.", parse_mode="markdown")


# ── /assignments ───────────────────────────────────────────────────────────
@_listener.on_message(filters.command("assignments") & filters.private)
@admin_only
async def cmd_assignments(client: Client, message: Message):
    if not _pool:
        await message.reply("⚠️ Pool not initialized yet.")
        return
    data = _pool.get_assignments()
    if not data:
        await message.reply(
            "📋 **No source assignments** — all sources use round-robin.\n\n"
            "Use `/assignsource <chat> <account_num>` to pin a source to an account.",
            parse_mode="markdown"
        )
        return
    # Group by account
    by_acc: dict[int, list[str]] = {}
    for src, idx in data.items():
        by_acc.setdefault(idx, []).append(src)
    lines = []
    for idx in sorted(by_acc):
        srcs = ", ".join(f"`{s}`" for s in by_acc[idx])
        lines.append(f"**Account {idx+1}:** {srcs}")
    await message.reply(
        f"📌 **Source assignments** ({len(data)} assigned):\n\n" + "\n".join(lines) +
        "\n\nUnassigned sources use round-robin across all accounts.",
        parse_mode="markdown"
    )




# ── /addchat ───────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("addchat") & filters.private)
@admin_only
async def cmd_addchat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply("**Usage:** `/addchat <username or group ID>`", parse_mode="markdown")
        return
    raw = args[1].strip()
    ok, msg = add_chat(raw)
    if ok:
        try:
            chat_obj = await client.get_chat(raw)
            extra = f"\n👥 **{chat_obj.title}**"
        except Exception:
            extra = "\n⚠️ Could not verify — make sure the userbot is a member!"
        await message.reply(f"{msg}{extra}", parse_mode="markdown")
        if LOG_CHANNEL:
            try: await client.send_message(LOG_CHANNEL, f"➕ Source added: `{raw}`")
            except Exception: pass
    else:
        await message.reply(msg, parse_mode="markdown")


# ── /removechat ────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("removechat") & filters.private)
@admin_only
async def cmd_removechat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply("**Usage:** `/removechat <username or group ID>`", parse_mode="markdown")
        return
    ok, msg = remove_chat(args[1].strip())
    await message.reply(msg, parse_mode="markdown")


# ── /listchats ─────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("listchats") & filters.private)
@admin_only
async def cmd_listchats(client: Client, message: Message):
    from config import SOURCE_CHATS as _SC
    await message.reply(list_chats(_SC), parse_mode="markdown")


# ── /route /routes ─────────────────────────────────────────────────────────
@_listener.on_message(filters.command("route") & filters.private)
@admin_only
async def cmd_route(client: Client, message: Message):
    args = message.text.split(None, 2)
    if len(args) < 3:
        await message.reply("**Usage:** `/route <source_chat> <dest_channel>`\n\n" + list_routes(), parse_mode="markdown")
        return
    try:
        dest_int = int(args[2].strip())
    except ValueError:
        await message.reply("❌ Destination must be a channel ID (negative integer).", parse_mode="markdown")
        return
    await message.reply(set_route(args[1].strip(), dest_int), parse_mode="markdown")


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
    n = seen_count()
    if not confirmed:
        await message.reply(
            f"⚠️ **Reset Duplicate Memory?**\n\nWill erase `{n:,}` tracked file IDs.\n"
            f"No files deleted from Telegram — only memory cleared.\n\n"
            f"**To confirm:** `/resetdups confirm`", parse_mode="markdown"
        )
        return
    seen_reset()
    _stats["skipped_dup"] = 0
    await message.reply(f"✅ Cleared `{n:,}` file IDs from seen.json.", parse_mode="markdown")


# ── /srcstats ──────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("srcstats") & filters.private)
@admin_only
async def cmd_srcstats(client: Client, message: Message):
    rows = stats_db.get_all()
    grand = stats_db.total()
    if not rows:
        await message.reply("📊 No forwarding stats yet.", parse_mode="markdown")
        return
    lines = ["**📊 Per-Source Forwarding Stats**\n"]
    for i, r in enumerate(rows[:20], 1):
        pct  = f"{r['count']/grand*100:.1f}%" if grand else "0%"
        last = r.get("last_seen", "")[:10]
        lines.append(f"{i}. **{r['title']}**\n   `{r['count']:,}` files ({pct}) — last: {last}")
    lines.append(f"\n**Total:** `{grand:,}` files")
    if len(rows) > 20:
        lines.append(f"_...and {len(rows)-20} more sources_")
    await message.reply("\n".join(lines), parse_mode="markdown")


# ── /pausefwd /resumefwd ───────────────────────────────────────────────────
@_listener.on_message(filters.command("pausefwd") & filters.private)
@admin_only
async def cmd_pausefwd(client: Client, message: Message):
    global _paused
    if _paused:
        await message.reply(f"⏸️ Already paused. Dropped: `{_stats['skipped_paused']}`\nSend `/resumefwd` to resume.", parse_mode="markdown")
        return
    _paused = True
    await message.reply("⏸️ **Forwarding paused.**\nSend `/resumefwd` to resume.", parse_mode="markdown")
    if LOG_CHANNEL:
        try:
            me = await client.get_me()
            await client.send_message(LOG_CHANNEL, f"⏸️ Forwarding paused by `{me.first_name}`")
        except Exception: pass


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
    await message.reply(f"▶️ **Forwarding resumed.**\nDropped while paused: `{dropped}` files", parse_mode="markdown")
    if LOG_CHANNEL:
        try:
            me = await client.get_me()
            await client.send_message(LOG_CHANNEL, f"▶️ Forwarding resumed by `{me.first_name}` — dropped `{dropped}` files")
        except Exception: pass


# ── /setcaption ────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("setcaption") & filters.private)
@admin_only
async def cmd_setcaption(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) == 1:
        current = cs_db.get()
        if current:
            await message.reply(f"**Current caption suffix:**\n`{current}`\n\nTo change: `/setcaption <new text>`\nTo remove: `/setcaption off`", parse_mode="markdown")
        else:
            await message.reply("**No caption suffix set.**\n\nUse `/setcaption <text>` to add a line to every forwarded file's caption.\nExample: `/setcaption 🎬 @MyChannel`", parse_mode="markdown")
        return
    text = args[1].strip()
    if text.lower() == "off":
        cs_db.clear()
        await message.reply("✅ Caption suffix removed.", parse_mode="markdown")
    else:
        cs_db.set(text)
        await message.reply(f"✅ **Caption suffix set:**\n`{text}`\n\nThis will be appended to every forwarded file's caption from now on.", parse_mode="markdown")


# ── /strippatterns ─────────────────────────────────────────────────────────
@_listener.on_message(filters.command("strippatterns") & filters.private)
@admin_only
async def cmd_strippatterns(client: Client, message: Message):
    args = message.text.split(None, 2)
    sub  = args[1].strip().lower() if len(args) > 1 else "list"
    if sub == "list":
        patterns = sp_db.load()
        if not patterns:
            await message.reply("**Custom strip patterns:** _none_\n\nAdd one with `/strippatterns add <regex>`", parse_mode="markdown")
        else:
            lines = ["**Custom strip patterns:**\n"]
            for i, p in enumerate(patterns, 1):
                lines.append(f"`{i}.` `{p}`")
            lines.append(f"\nTo remove: `/strippatterns remove <number>`")
            await message.reply("\n".join(lines), parse_mode="markdown")
    elif sub == "add":
        if len(args) < 3 or not args[2].strip():
            await message.reply("**Usage:** `/strippatterns add <regex pattern>`", parse_mode="markdown")
            return
        pattern = args[2].strip()
        try:
            added = sp_db.add(pattern)
        except ValueError as e:
            await message.reply(f"❌ Invalid regex: `{e}`", parse_mode="markdown")
            return
        if added:
            await message.reply(f"✅ **Pattern added:**\n`{pattern}`\n\nTotal: `{sp_db.count()}`", parse_mode="markdown")
        else:
            await message.reply(f"⚠️ Pattern already exists: `{pattern}`", parse_mode="markdown")
    elif sub == "remove":
        if len(args) < 3 or not args[2].strip().isdigit():
            await message.reply("**Usage:** `/strippatterns remove <number>`", parse_mode="markdown")
            return
        removed = sp_db.remove(int(args[2].strip()))
        if removed:
            await message.reply(f"✅ Removed pattern:\n`{removed}`", parse_mode="markdown")
        else:
            await message.reply("❌ Number out of range.", parse_mode="markdown")
    else:
        await message.reply("**Usage:**\n• `/strippatterns list`\n• `/strippatterns add <regex>`\n• `/strippatterns remove <number>`", parse_mode="markdown")


# ── /cleancaptions /stopcleaning ───────────────────────────────────────────
@_listener.on_message(filters.command("cleancaptions") & filters.private)
@admin_only
async def cmd_cleancaptions(client: Client, message: Message):
    global _cleaning_task, _stop_cleaning
    if _cleaning_task and not _cleaning_task.done():
        await message.reply("⚠️ A caption cleaning job is already running.\nSend `/stopcleaning` to cancel it.", parse_mode="markdown")
        return
    args   = message.text.split(None, 1)
    target = int(args[1].strip()) if len(args) > 1 and args[1].strip().lstrip("-").isdigit() else DEST_CHANNEL
    if not target:
        await message.reply("❌ No destination channel configured.", parse_mode="markdown")
        return
    _stop_cleaning = False
    status_msg = await message.reply(f"🧹 **Caption cleaning started**\n\nChannel: `{target}`\nSend `/stopcleaning` to stop.", parse_mode="markdown")

    async def _clean_loop():
        global _stop_cleaning
        from caption_cleaner import clean as strip_watermarks
        scanned = edited = skipped = errors = 0
        last_update = asyncio.get_event_loop().time()
        try:
            async for msg in client.get_chat_history(target):
                if _stop_cleaning:
                    break
                scanned += 1
                now = asyncio.get_event_loop().time()
                if now - last_update >= 5:
                    try:
                        await status_msg.edit(f"🧹 **Caption cleaning in progress...**\n\n📂 Scanned: `{scanned:,}`\n✏️ Edited: `{edited:,}`\n⏭️ Skipped: `{skipped:,}`\n❌ Errors: `{errors}`\n\nSend `/stopcleaning` to stop.", parse_mode="markdown")
                    except Exception: pass
                    last_update = now
                if not msg.caption:
                    skipped += 1
                    continue
                cleaned = strip_watermarks(msg.caption)
                if cleaned == msg.caption.strip():
                    skipped += 1
                    continue
                try:
                    await msg.edit_caption(caption=cleaned or "")
                    edited += 1
                    await asyncio.sleep(0.5)
                except Exception as e:
                    errors += 1
                    await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"/cleancaptions loop error: {e}")
        status = "✅ Complete" if not _stop_cleaning else "⛔ Stopped"
        try:
            await status_msg.edit(f"🧹 **Caption cleaning {status}**\n\n📂 Scanned: `{scanned:,}`\n✏️ Edited: `{edited:,}`\n⏭️ Already clean: `{skipped:,}`\n❌ Errors: `{errors}`", parse_mode="markdown")
        except Exception: pass

    _cleaning_task = asyncio.create_task(_clean_loop())


@_listener.on_message(filters.command("stopcleaning") & filters.private)
@admin_only
async def cmd_stopcleaning(client: Client, message: Message):
    global _stop_cleaning, _cleaning_task
    if not _cleaning_task or _cleaning_task.done():
        await message.reply("No caption cleaning job is currently running.", parse_mode="markdown")
        return
    _stop_cleaning = True
    await message.reply("⛔ Stopping caption cleaning after the current message...", parse_mode="markdown")


# ── /ignorechat /unignorechat /listignored ─────────────────────────────────
@_listener.on_message(filters.command("ignorechat") & filters.private)
@admin_only
async def cmd_ignorechat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply("Usage: `/ignorechat <@username or chat_id>`", parse_mode="markdown")
        return
    try:
        chat = await client.get_chat(args[1].strip())
        ignore_db.ignore(chat.id, chat.title or str(chat.id))
        await message.reply(f"🚫 Ignoring: **{chat.title}** (`{chat.id}`)\nUse `/unignorechat` to re-enable.", parse_mode="markdown")
    except Exception as e:
        await message.reply(f"❌ Cannot find chat: `{e}`", parse_mode="markdown")


@_listener.on_message(filters.command("unignorechat") & filters.private)
@admin_only
async def cmd_unignorechat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply("Usage: `/unignorechat <@username or chat_id>`", parse_mode="markdown")
        return
    try:
        chat = await client.get_chat(args[1].strip())
        ignore_db.unignore(chat.id)
        await message.reply(f"✅ Re-enabled: **{chat.title}**", parse_mode="markdown")
    except Exception as e:
        await message.reply(f"❌ Cannot find chat: `{e}`", parse_mode="markdown")


@_listener.on_message(filters.command("listignored") & filters.private)
@admin_only
async def cmd_listignored(client: Client, message: Message):
    data = ignore_db.list_ignored()
    if not data:
        await message.reply("✅ No chats ignored — all sources active.")
        return
    import datetime
    lines = [
        f"• `{info.get('title', cid)}` — since "
        f"{datetime.datetime.fromtimestamp(info.get('since', 0)).strftime('%Y-%m-%d %H:%M')}"
        for cid, info in data.items()
    ]
    await message.reply(f"**🚫 Ignored ({len(data)}):**\n\n" + "\n".join(lines), parse_mode="markdown")


# ── /joinchat ──────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("joinchat") & filters.private)
@admin_only
async def cmd_joinchat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply("Usage: `/joinchat <invite_link or @username>`\n\nJoins the chat and automatically adds it to sources.", parse_mode="markdown")
        return
    link = args[1].strip()
    prog = await message.reply(f"⏳ Joining `{link}`...", parse_mode="markdown")
    try:
        chat = await client.join_chat(link)
        add_chat(str(chat.id))
        await prog.edit(f"✅ Joined and added to sources!\n\n**Chat:** {chat.title}\n**ID:** `{chat.id}`\n\nNow forwarding files from this chat.", parse_mode="markdown")
    except Exception as e:
        await prog.edit(f"❌ Failed to join: `{type(e).__name__}: {e}`", parse_mode="markdown")


# ── /keywords ──────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("keywords") & filters.private)
@admin_only
async def cmd_keywords(client: Client, message: Message):
    args  = message.text.split(None, 2)
    sub   = args[1].strip().lower() if len(args) > 1 else "list"
    value = args[2].strip()         if len(args) > 2 else ""
    if sub == "list":
        state = keyword_filter.get_state()
        kws   = state.get("keywords", [])
        mode  = state.get("mode", "off")
        if not kws:
            await message.reply(f"**🔍 Keyword filter** — mode: `{mode}`\n\nNo keywords set.\n\n• `/keywords allow <word>`\n• `/keywords block <word>`\n• `/keywords off`", parse_mode="markdown")
            return
        lines = "\n".join(f"`{i+1}.` {k}" for i, k in enumerate(kws))
        await message.reply(f"**🔍 Keyword filter** — mode: `{mode}`\n\n{lines}\n\n• `/keywords allow/block/remove/off`", parse_mode="markdown")
    elif sub in ("allow", "block"):
        if not value:
            await message.reply(f"Usage: `/keywords {sub} <keyword>`", parse_mode="markdown")
            return
        keyword_filter.set_mode(sub)
        added = keyword_filter.add_keyword(value)
        state = keyword_filter.get_state()
        if added:
            await message.reply(f"✅ Added `{value}` to **{sub}** list.\nMode: `{sub}` — {len(state['keywords'])} keyword(s) active.", parse_mode="markdown")
        else:
            await message.reply(f"ℹ️ `{value}` already in list.", parse_mode="markdown")
    elif sub in ("remove", "del"):
        if not value.isdigit():
            await message.reply("Usage: `/keywords remove <number>`", parse_mode="markdown")
            return
        result = keyword_filter.remove_keyword(int(value))
        if result in ("No keywords set.",) or result.startswith("Index"):
            await message.reply(f"❌ {result}", parse_mode="markdown")
        else:
            await message.reply(f"🗑️ Removed: `{result}`", parse_mode="markdown")
    elif sub == "off":
        keyword_filter.set_mode("off")
        await message.reply("✅ Keyword filter disabled — all files forwarded.", parse_mode="markdown")
    else:
        await message.reply("**Usage:**\n• `/keywords list`\n• `/keywords allow <word>`\n• `/keywords block <word>`\n• `/keywords remove <n>`\n• `/keywords off`", parse_mode="markdown")


# ── /failedstats /retry /clearfailed ──────────────────────────────────────
@_listener.on_message(filters.command("failedstats") & filters.private)
@admin_only
async def cmd_failedstats(client: Client, message: Message):
    entries = failed_db.load()
    if not entries:
        await message.reply("✅ No failed forwards — failed.json is empty.")
        return
    breakdown = failed_db.by_chat()
    lines = []
    for chat_id, cnt in sorted(breakdown.items(), key=lambda x: -x[1]):
        try:
            chat = await client.get_chat(int(chat_id))
            name = chat.title or str(chat_id)
        except Exception:
            name = str(chat_id)
        lines.append(f"  • {name}: {cnt}")
    import datetime
    oldest = min(e.get("ts", 0) for e in entries)
    oldest_str = datetime.datetime.fromtimestamp(oldest).strftime("%Y-%m-%d %H:%M") if oldest else "?"
    await message.reply(
        f"**⚠️ Failed Forwards: {len(entries)} pending**\n\n**By source:**\n" + "\n".join(lines) +
        f"\n\nOldest entry: {oldest_str}\nRun /retry to attempt recovery.",
        parse_mode="markdown"
    )


@_listener.on_message(filters.command("retry") & filters.private)
@admin_only
async def cmd_retry(client: Client, message: Message):
    entries = failed_db.load()
    if not entries:
        await message.reply("✅ Nothing to retry — failed.json is empty.")
        return
    from utils import safe_forward as _sf, get_file_name as _gfn
    prog = await message.reply(f"🔄 Retrying {len(entries)} failed forward(s)...", parse_mode="markdown")
    ok = fail = skip = 0
    for entry in list(entries):
        chat_id    = entry["chat_id"]
        message_id = entry["message_id"]
        try:
            msgs = await client.get_messages(chat_id, message_id)
            msg = msgs if not isinstance(msgs, list) else msgs[0]
            if not msg or not msg.id:
                skip += 1
                failed_db.remove(chat_id, message_id)
                continue
            stored_dest = entry.get("dest", 0)
            dest = stored_dest or get_destination(_gfn(msg), int(chat_id)) or DEST_CHANNEL
            success = await _sf(msg, dest, skip_duplicates=True)
            if success:
                ok += 1
                failed_db.remove(chat_id, message_id)
                stats_db.record(str(chat_id), getattr(msg.chat, "title", str(chat_id)))
            else:
                fail += 1
        except Exception as e:
            logger.warning(f"Retry error chat={chat_id} msg={message_id}: {e}")
            fail += 1
        await asyncio.sleep(2)
    await prog.edit(
        f"**✅ Retry complete**\n\n✅ Succeeded: {ok}\n❌ Still failed: {fail}\n⏭️ Skipped (deleted): {skip}\n📋 Remaining: {failed_db.count()}",
        parse_mode="markdown"
    )


@_listener.on_message(filters.command("clearfailed") & filters.private)
@admin_only
async def cmd_clearfailed(client: Client, message: Message):
    args = message.text.split(None, 1)
    n = failed_db.count()
    if len(args) < 2 or args[1].strip().lower() != "confirm":
        await message.reply(f"⚠️ This will clear {n} failed entry(s).\nSend `/clearfailed confirm` to proceed.", parse_mode="markdown")
        return
    failed_db.clear()
    await message.reply(f"🗑️ Cleared {n} failed entries from failed.json.")


# ── /discover /suggest ─────────────────────────────────────────────────────
@_listener.on_message(filters.command("discover") & filters.private)
@admin_only
async def cmd_discover(client: Client, message: Message):
    msg = await message.reply("🔍 Scanning your joined groups for movie sources...")
    results = await find_joined_sources(client, limit=20)
    await msg.edit(format_results(results, "Movie groups you've joined"), parse_mode="markdown")


@_listener.on_message(filters.command("suggest") & filters.private)
@admin_only
async def cmd_suggest(client: Client, message: Message):
    args  = message.text.split(None, 1)
    query = args[1].strip() if len(args) > 1 else "movies 1080p"
    msg   = await message.reply(f"🔍 Searching Telegram for: `{query}`...", parse_mode="markdown")
    results = await search_public_sources(client, query, limit=10)
    await msg.edit(format_results(results, f"Public groups matching '{query}'"), parse_mode="markdown")


# ── /export ────────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("export") & filters.private)
@admin_only
async def cmd_export(client: Client, message: Message):
    import csv, io, datetime, tempfile, os as _os
    data = stats_db.all_stats() if hasattr(stats_db, "all_stats") else {}
    if not data:
        await message.reply("📊 No stats to export yet.")
        return
    fd, tmp = tempfile.mkstemp(suffix=".csv")
    _os.close(fd)
    try:
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Chat ID", "Title", "Count", "First Seen", "Last Seen"])
            for cid, info in sorted(data.items(), key=lambda x: -x[1].get("count", 0)):
                writer.writerow([cid, info.get("title", ""), info.get("count", 0), info.get("first_seen", ""), info.get("last_seen", "")])
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        await client.send_document(message.chat.id, document=tmp, file_name=f"forwarder_stats_{ts}.csv", caption=f"📊 Stats — {len(data)} sources | {ts}")
    finally:
        try: _os.remove(tmp)
        except Exception: pass


# ── /help ─────────────────────────────────────────────────────────────────
@_listener.on_message(filters.command(["start", "help"]) & filters.private)
async def cmd_help(client: Client, message: Message):
    me = await client.get_me()
    await message.reply(
        f"**Multi-Account Forwarder** — `{me.first_name}`\n\n"
        "**Sources:**\n"
        "• `/addchat` / `/removechat` / `/listchats` / `/joinchat`\n"
        "• `/ignorechat` / `/unignorechat` / `/listignored`\n\n"
        "**Stats:**\n"
        "• `/srcstats` / `/resetdups` / `/export`\n\n"
        "**Routing:**\n"
        "• `/route <src> <dest>` / `/routes`\n\n"
        "**Pause:**\n"
        "• `/pausefwd` / `/resumefwd`\n\n"
        "**Captions:**\n"
        "• `/setcaption <text|off>` / `/strippatterns add/remove/list`\n"
        "• `/cleancaptions` / `/stopcleaning`\n\n"
        "**Recovery:**\n"
        "• `/failedstats` / `/retry` / `/clearfailed confirm`\n\n"
        "**Filters:**\n"
        "• `/keywords list/allow/block/remove/off`\n\n"
        "**Discovery:**\n"
        "• `/discover` / `/suggest <keyword>`\n\n"
        "**Pool:**\n"
        "• `/poolstatus` / `/assignsource` / `/unassignsource` / `/assignments`\n",
        parse_mode="markdown"
    )


# ── Startup ───────────────────────────────────────────────────────────────
async def main():
    global _pool
    await _listener.start()
    me = await _listener.get_me()
    logger.info(f"🎧 Listener started as: {me.first_name} (@{me.username})")

    _pool = await AccountPool.create()
    logger.info(f"🏊 Pool: {_pool.account_count()} account(s)")

    current = get_all_chats(SOURCE_CHATS)
    logger.info(f"👀 Watching {len(current)} sources | all-time: {stats_db.total():,} files")

    # Auto-retry failed forwards from last session
    failed_count = failed_db.count()
    if failed_count > 0:
        logger.info(f"⚠️  {failed_count} failed forward(s) from previous session — scheduling auto-retry in 30s")
        async def _auto_retry():
            await asyncio.sleep(30)
            logger.info("🔄 Auto-retry: starting…")
            items = failed_db.load()
            ok_count = failed_count2 = 0
            for item in items:
                try:
                    msg = await _listener.get_messages(int(item["chat_id"]), int(item["message_id"]))
                    dest_r = item.get("dest", DEST_CHANNEL)
                    if _pool and await _pool.forward_from_source(msg, dest_r, item["chat_id"]):
                        failed_db.remove(item["chat_id"], item["message_id"])
                        ok_count += 1
                    else:
                        failed_count2 += 1
                except Exception as exc:
                    logger.warning(f"Auto-retry error: {exc}")
                    failed_count2 += 1
            logger.info(f"✅ Auto-retry done: {ok_count} recovered, {failed_count2} still failed")
            if LOG_CHANNEL and ok_count > 0:
                try:
                    await _listener.send_message(LOG_CHANNEL, f"🔄 Auto-retry: {ok_count} recovered, {failed_count2} still failed")
                except Exception:
                    pass
        asyncio.create_task(_auto_retry())

    asyncio.create_task(_session_watchdog())
    logger.info("🛡️ Session watchdog started")

    asyncio.create_task(start_dashboard(
        stats_getter=lambda: _stats,
        pool_getter=lambda: _pool,
        port=PORT,
    ))
    logger.info(f"📊 Dashboard starting on port {PORT}")

    if LOG_CHANNEL:
        try:
            suffix = cs_db.get()
            await _listener.send_message(LOG_CHANNEL,
                f"🏊 **Multi-account forwarder started**\n"
                f"Listener: `{me.first_name}` | Pool: {_pool.account_count()} accounts\n"
                f"Sources: {len(current)} | All-time: {stats_db.total():,} files\n"
                f"Patterns: {sp_db.count()} | Suffix: " + (f"`{suffix}`" if suffix else "_none_"))
        except Exception:
            pass

    await idle()
    await _pool.stop_all()
    await _listener.stop()

if __name__ == "__main__":
    asyncio.run(main())
