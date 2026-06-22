"""
Real-time Forwarder — forwarder.py
Commands (ADMINS only — DM the userbot):
  Source:    /addchat /removechat /listchats
  Stats:     /fwrstatus /dupstats /srcstats /resetdups
  Routing:   /route /routes
  Pause:     /pausefwd /resumefwd
  Captions:  /setcaption /strippatterns /cleancaptions /stopcleaning
  Recovery:  /failedstats /retry /clearfailed
  Discovery: /discover /suggest /help
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
from utils import safe_forward, is_allowed_file, get_file_name, get_file_size, human_size
from chats_db import get_all_chats, add_chat, remove_chat, list_chats
from router import get_destination, set_route, remove_route, list_routes, format_type_label
from seen_db import count as seen_count, reset as seen_reset
from caption_cleaner import is_enabled as captions_enabled
from discovery import find_joined_sources, search_public_sources, format_results
import stats_db
import strip_patterns as sp_db
import caption_suffix as cs_db
import failed_db
import ignore_db
import keyword_filter

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

_stats  = {"forwarded": 0, "skipped_dup": 0, "failed": 0, "skipped_paused": 0}
_paused = False
_cleaning_task: asyncio.Task | None = None
_stop_cleaning = False


def admin_only(func):
    async def wrapper(client, message: Message):
        if ADMINS and message.from_user and message.from_user.id not in ADMINS:
            await message.reply("⛔ You are not authorized.")
            return
        await func(client, message)
    wrapper.__name__ = func.__name__
    return wrapper


# ── Session watchdog ───────────────────────────────────────────────────────
async def _session_watchdog():
    """Ping Telegram every 5 min. Alert admins & exit if session is revoked."""
    await asyncio.sleep(60)   # give the bot a moment to fully start
    while True:
        await asyncio.sleep(300)
        try:
            await app.get_me()
        except (SessionRevoked, AuthKeyUnregistered, UserDeactivated) as e:
            alert = (
                f"⚠️ **Session Revoked!**\n\n"
                f"Error: `{type(e).__name__}`\n\n"
                f"The forwarder has stopped. Run `/gensession` (locally) to generate "
                f"a new SESSION_STRING, update it in Railway Variables, and redeploy."
            )
            logger.critical(f"🔴 SESSION REVOKED: {e}")
            if LOG_CHANNEL:
                try:
                    await app.send_message(LOG_CHANNEL, alert)
                except Exception:
                    pass
            for uid in ADMINS:
                try:
                    await app.send_message(uid, alert)
                except Exception:
                    pass
            await asyncio.sleep(5)
            os._exit(1)
        except Exception:
            pass   # network blip — ignore, retry next cycle


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

    # Ignore-chat check
    if ignore_db.is_ignored(chat_id):
        logger.debug(f"🚫 Ignored chat skipped: {chat_id}")
        return

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

    success = await safe_forward(message, dest)

    if success:
        _stats["forwarded"] += 1
        stats_db.record(chat_id, title)
        logger.info(f"✅ Forwarded → {dest}  |  total: {_stats['forwarded']}")
    else:
        from utils import get_unique_id
        from seen_db import is_seen
        uid = get_unique_id(message)
        if uid and is_seen(uid):
            _stats["skipped_dup"] += 1
        else:
            _stats["failed"] += 1


# ── /addchat ───────────────────────────────────────────────────────────────
@app.on_message(filters.command("addchat") & filters.private)
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
    suffix  = cs_db.get()
    pause_line = f"\n⏸️ **FORWARDING PAUSED** — {_stats['skipped_paused']} dropped\n" if _paused else ""
    await message.reply(
        f"**Forwarder Status**\n{pause_line}\n"
        f"👤 `{me.first_name}` (@{me.username})\n"
        f"👀 Watching: `{len(current)}` source chats\n\n"
        f"**Session:**\n"
        f"✅ Forwarded: `{_stats['forwarded']}`\n"
        f"⏭️ Dup-skipped: `{_stats['skipped_dup']}` ({dup_pct})\n"
        f"⏸️ Paused-dropped: `{_stats['skipped_paused']}`\n"
        f"❌ Failed: `{_stats['failed']}`\n"
        f"🗂️ Seen DB: `{seen_count():,}` unique files\n"
        f"📊 All-time: `{stats_db.total():,}` files\n\n"
        f"**Routing:**\n{list_routes()}\n\n"
        f"**Caption cleaning:** {'✅ on' if captions_enabled() else '⛔ off'}\n"
        f"**Caption suffix:** `{suffix}` " + ("✅" if suffix else "_not set_") + "\n"
        f"**Custom strip patterns:** `{sp_db.count()}`",
        parse_mode="markdown"
    )


# ── /route /routes ─────────────────────────────────────────────────────────
@app.on_message(filters.command("route") & filters.private)
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


@app.on_message(filters.command("routes") & filters.private)
@admin_only
async def cmd_routes(client: Client, message: Message):
    await message.reply(list_routes(), parse_mode="markdown")


# ── /dupstats ──────────────────────────────────────────────────────────────
@app.on_message(filters.command("dupstats") & filters.private)
@admin_only
async def cmd_dupstats(client: Client, message: Message):
    total = _stats["forwarded"] + _stats["skipped_dup"]
    pct   = f"{_stats['skipped_dup']/total*100:.1f}%" if total else "0%"
    await message.reply(
        f"**Duplicate Detection Stats**\n\n"
        f"🗂️ Seen DB: `{seen_count():,}` unique file IDs\n\n"
        f"✅ Forwarded: `{_stats['forwarded']}`\n"
        f"⏭️ Skipped (dup): `{_stats['skipped_dup']}` ({pct})",
        parse_mode="markdown"
    )


# ── /resetdups ─────────────────────────────────────────────────────────────
@app.on_message(filters.command("resetdups") & filters.private)
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
    if LOG_CHANNEL:
        try:
            me = await client.get_me()
            await client.send_message(LOG_CHANNEL, f"🗑️ seen.json reset by `{me.first_name}` — cleared `{n:,}` IDs")
        except Exception: pass


# ── /srcstats ──────────────────────────────────────────────────────────────
@app.on_message(filters.command("srcstats") & filters.private)
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
@app.on_message(filters.command("pausefwd") & filters.private)
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


@app.on_message(filters.command("resumefwd") & filters.private)
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
@app.on_message(filters.command("setcaption") & filters.private)
@admin_only
async def cmd_setcaption(client: Client, message: Message):
    args = message.text.split(None, 1)

    if len(args) == 1:
        # No argument — show current
        current = cs_db.get()
        if current:
            await message.reply(
                f"**Current caption suffix:**\n`{current}`\n\n"
                f"To change: `/setcaption <new text>`\n"
                f"To remove: `/setcaption off`",
                parse_mode="markdown"
            )
        else:
            await message.reply(
                "**No caption suffix set.**\n\n"
                "Use `/setcaption <text>` to add a line to every forwarded file's caption.\n"
                "Example: `/setcaption 🎬 @MyChannel`",
                parse_mode="markdown"
            )
        return

    text = args[1].strip()
    if text.lower() == "off":
        cs_db.clear()
        await message.reply("✅ Caption suffix removed.", parse_mode="markdown")
    else:
        cs_db.set(text)
        await message.reply(
            f"✅ **Caption suffix set:**\n`{text}`\n\n"
            f"This will be appended to every forwarded file's caption from now on.",
            parse_mode="markdown"
        )


# ── /strippatterns ─────────────────────────────────────────────────────────
@app.on_message(filters.command("strippatterns") & filters.private)
@admin_only
async def cmd_strippatterns(client: Client, message: Message):
    """
    Manage runtime watermark-strip patterns.
    Usage:
      /strippatterns list
      /strippatterns add <regex>
      /strippatterns remove <number>
    """
    args = message.text.split(None, 2)
    sub  = args[1].strip().lower() if len(args) > 1 else "list"

    if sub == "list":
        patterns = sp_db.load()
        if not patterns:
            await message.reply(
                "**Custom strip patterns:** _none_\n\n"
                "Add one with `/strippatterns add <regex>`\n"
                "Example: `/strippatterns add FILE ADDED BY GOUTHAM`",
                parse_mode="markdown"
            )
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
            await message.reply(
                f"✅ **Pattern added:**\n`{pattern}`\n\n"
                f"Total custom patterns: `{sp_db.count()}`\n"
                f"Takes effect immediately on the next forward.",
                parse_mode="markdown"
            )
        else:
            await message.reply(f"⚠️ Pattern already exists: `{pattern}`", parse_mode="markdown")

    elif sub == "remove":
        if len(args) < 3 or not args[2].strip().isdigit():
            await message.reply("**Usage:** `/strippatterns remove <number>`\nGet the number from `/strippatterns list`", parse_mode="markdown")
            return
        removed = sp_db.remove(int(args[2].strip()))
        if removed:
            await message.reply(f"✅ Removed pattern:\n`{removed}`", parse_mode="markdown")
        else:
            await message.reply("❌ Number out of range. Use `/strippatterns list` to see valid numbers.", parse_mode="markdown")

    else:
        await message.reply(
            "**Usage:**\n"
            "• `/strippatterns list` — show all custom patterns\n"
            "• `/strippatterns add <regex>` — add a new pattern\n"
            "• `/strippatterns remove <number>` — remove a pattern",
            parse_mode="markdown"
        )


# ── /cleancaptions ─────────────────────────────────────────────────────────
@app.on_message(filters.command("cleancaptions") & filters.private)
@admin_only
async def cmd_cleancaptions(client: Client, message: Message):
    global _cleaning_task, _stop_cleaning

    if _cleaning_task and not _cleaning_task.done():
        await message.reply("⚠️ A caption cleaning job is already running.\nSend `/stopcleaning` to cancel it.", parse_mode="markdown")
        return

    # Allow optional custom channel argument, default to DEST_CHANNEL
    args = message.text.split(None, 1)
    target = int(args[1].strip()) if len(args) > 1 and args[1].strip().lstrip("-").isdigit() else DEST_CHANNEL

    if not target:
        await message.reply("❌ No destination channel configured. Set `DEST_CHANNEL` in Railway Variables.", parse_mode="markdown")
        return

    _stop_cleaning = False
    status_msg = await message.reply(
        f"🧹 **Caption cleaning started**\n\n"
        f"Channel: `{target}`\n"
        f"Scanning messages... this may take a while.\n\n"
        f"Send `/stopcleaning` to stop.",
        parse_mode="markdown"
    )

    async def _clean_loop():
        global _stop_cleaning
        from caption_cleaner import clean as strip_watermarks, is_enabled as captions_on

        scanned = 0
        edited  = 0
        skipped = 0
        errors  = 0
        last_update = asyncio.get_event_loop().time()

        try:
            async for msg in client.get_chat_history(target):
                if _stop_cleaning:
                    break

                scanned += 1

                # Live progress update every 5 seconds
                now = asyncio.get_event_loop().time()
                if now - last_update >= 5:
                    try:
                        await status_msg.edit(
                            f"🧹 **Caption cleaning in progress...**\n\n"
                            f"📂 Scanned: `{scanned:,}`\n"
                            f"✏️ Edited: `{edited:,}`\n"
                            f"⏭️ Skipped (clean): `{skipped:,}`\n"
                            f"❌ Errors: `{errors}`\n\n"
                            f"Send `/stopcleaning` to stop.",
                            parse_mode="markdown"
                        )
                    except Exception:
                        pass
                    last_update = now

                if not msg.caption:
                    skipped += 1
                    continue

                cleaned = strip_watermarks(msg.caption)
                # If cleaned == original, nothing to do
                if cleaned == msg.caption.strip():
                    skipped += 1
                    continue

                try:
                    await msg.edit_caption(caption=cleaned or "")
                    edited += 1
                    await asyncio.sleep(0.5)   # stay under edit rate limit
                except Exception as e:
                    errors += 1
                    logger.warning(f"Caption edit failed on msg {msg.id}: {e}")
                    await asyncio.sleep(2)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"/cleancaptions loop error: {e}")

        # Final summary
        status = "✅ Complete" if not _stop_cleaning else "⛔ Stopped"
        try:
            await status_msg.edit(
                f"🧹 **Caption cleaning {status}**\n\n"
                f"📂 Scanned: `{scanned:,}`\n"
                f"✏️ Edited: `{edited:,}`\n"
                f"⏭️ Already clean: `{skipped:,}`\n"
                f"❌ Errors: `{errors}`",
                parse_mode="markdown"
            )
        except Exception:
            pass

    _cleaning_task = asyncio.create_task(_clean_loop())


# ── /stopcleaning ──────────────────────────────────────────────────────────
@app.on_message(filters.command("stopcleaning") & filters.private)
@admin_only
async def cmd_stopcleaning(client: Client, message: Message):
    global _stop_cleaning, _cleaning_task
    if not _cleaning_task or _cleaning_task.done():
        await message.reply("No caption cleaning job is currently running.", parse_mode="markdown")
        return
    _stop_cleaning = True
    await message.reply("⛔ Stopping caption cleaning after the current message...", parse_mode="markdown")


# ── /discover /suggest ─────────────────────────────────────────────────────
@app.on_message(filters.command("discover") & filters.private)
@admin_only
async def cmd_discover(client: Client, message: Message):
    msg = await message.reply("🔍 Scanning your joined groups for movie sources...")
    results = await find_joined_sources(client, limit=20)
    await msg.edit(format_results(results, "Movie groups you've joined"), parse_mode="markdown")


@app.on_message(filters.command("suggest") & filters.private)
@admin_only
async def cmd_suggest(client: Client, message: Message):
    args  = message.text.split(None, 1)
    query = args[1].strip() if len(args) > 1 else "movies 1080p"
    msg   = await message.reply(f"🔍 Searching Telegram for: `{query}`...", parse_mode="markdown")
    results = await search_public_sources(client, query, limit=10)
    await msg.edit(format_results(results, f"Public groups matching '{query}'"), parse_mode="markdown")



# ── /failedstats ──────────────────────────────────────────────────────────────
@app.on_message(filters.command("failedstats") & filters.private)
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
    oldest = min(e.get('ts', 0) for e in entries)
    oldest_str = datetime.datetime.fromtimestamp(oldest).strftime('%Y-%m-%d %H:%M') if oldest else '?'
    text = (
        f"**⚠️ Failed Forwards: {len(entries)} pending**\n\n"
        f"**By source:**\n" + "\n".join(lines) +
        f"\n\nOldest entry: {oldest_str}\n"
        f"Run /retry to attempt recovery."
    )
    await message.reply(text, parse_mode="markdown")


# ── /retry ────────────────────────────────────────────────────────────────────
@app.on_message(filters.command("retry") & filters.private)
@admin_only
async def cmd_retry(client: Client, message: Message):
    entries = failed_db.load()
    if not entries:
        await message.reply("✅ Nothing to retry — failed.json is empty.")
        return
    prog = await message.reply(
        f"🔄 Retrying {len(entries)} failed forward(s)... This may take a while.",
        parse_mode="markdown"
    )
    ok = fail = skip = 0
    for entry in list(entries):
        chat_id    = entry["chat_id"]
        message_id = entry["message_id"]
        dest = get_destination(str(chat_id)) or DEST_CHANNEL
        try:
            msgs = await client.get_messages(chat_id, message_id)
            msg = msgs if not isinstance(msgs, list) else msgs[0]
            if not msg or not msg.id:
                skip += 1
                failed_db.remove(chat_id, message_id)
                continue
            success = await safe_forward(msg, dest, skip_duplicates=True)
            if success:
                ok += 1
                failed_db.remove(chat_id, message_id)
                stats_db.record(str(chat_id), getattr(msg.chat, 'title', str(chat_id)))
            else:
                fail += 1
        except Exception as e:
            logger.warning(f"Retry error chat={chat_id} msg={message_id}: {e}")
            fail += 1
        await asyncio.sleep(2)
    await prog.edit(
        f"**✅ Retry complete**\n\n"
        f"✅ Succeeded: {ok}\n"
        f"❌ Still failed: {fail}\n"
        f"⏭️ Skipped (deleted): {skip}\n"
        f"📋 Remaining in failed.json: {failed_db.count()}",
        parse_mode="markdown"
    )


# ── /clearfailed ──────────────────────────────────────────────────────────────
@app.on_message(filters.command("clearfailed") & filters.private)
@admin_only
async def cmd_clearfailed(client: Client, message: Message):
    args = message.text.split(None, 1)
    n = failed_db.count()
    if len(args) < 2 or args[1].strip().lower() != "confirm":
        await message.reply(
            f"⚠️ This will clear {n} failed entry(s) from failed.json.\n"
            f"Send /clearfailed confirm to proceed.",
            parse_mode="markdown"
        )
        return
    failed_db.clear()
    await message.reply(f"🗑️ Cleared {n} failed entries from failed.json.")



# ── /ignorechat  /unignorechat  /listignored ──────────────────────────────────
@app.on_message(filters.command("ignorechat") & filters.private)
@admin_only
async def cmd_ignorechat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply("Usage: `/ignorechat <@username or chat_id>`", parse_mode="markdown")
        return
    target = args[1].strip()
    try:
        chat = await client.get_chat(target)
        ignore_db.ignore(chat.id, chat.title or str(chat.id))
        await message.reply(
            f"🚫 Ignoring: **{chat.title}** (`{chat.id}`)\n"
            f"Messages skipped silently. Use `/unignorechat` to re-enable.",
            parse_mode="markdown"
        )
    except Exception as e:
        await message.reply(f"❌ Cannot find chat: `{e}`", parse_mode="markdown")


@app.on_message(filters.command("unignorechat") & filters.private)
@admin_only
async def cmd_unignorechat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply("Usage: `/unignorechat <@username or chat_id>`", parse_mode="markdown")
        return
    target = args[1].strip()
    try:
        chat = await client.get_chat(target)
        ignore_db.unignore(chat.id)
        await message.reply(f"✅ Re-enabled: **{chat.title}**", parse_mode="markdown")
    except Exception as e:
        await message.reply(f"❌ Cannot find chat: `{e}`", parse_mode="markdown")


@app.on_message(filters.command("listignored") & filters.private)
@admin_only
async def cmd_listignored(client: Client, message: Message):
    data = ignore_db.list_ignored()
    if not data:
        await message.reply("✅ No chats ignored — all sources active.")
        return
    import datetime
    lines = [
        f"• `{info.get('title', cid)}` — since "
        f"{datetime.datetime.fromtimestamp(info.get('since',0)).strftime('%Y-%m-%d %H:%M')}"
        for cid, info in data.items()
    ]
    await message.reply(
        f"**🚫 Ignored ({len(data)}):**\n\n" + "\n".join(lines),
        parse_mode="markdown"
    )


# ── /joinchat ─────────────────────────────────────────────────────────────────
@app.on_message(filters.command("joinchat") & filters.private)
@admin_only
async def cmd_joinchat(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply(
            "Usage: `/joinchat <invite_link or @username>`\n\n"
            "Joins the chat and automatically adds it to sources.",
            parse_mode="markdown"
        )
        return
    link = args[1].strip()
    prog = await message.reply(f"⏳ Joining `{link}`...", parse_mode="markdown")
    try:
        chat = await client.join_chat(link)
        add_chat(str(chat.id))
        await prog.edit(
            f"✅ Joined and added to sources!\n\n"
            f"**Chat:** {chat.title}\n"
            f"**ID:** `{chat.id}`\n\n"
            f"Now forwarding files from this chat.",
            parse_mode="markdown"
        )
        logger.info(f"Joined and added source: {chat.title} ({chat.id})")
    except Exception as e:
        await prog.edit(f"❌ Failed to join: `{type(e).__name__}: {e}`", parse_mode="markdown")


# ── /export ───────────────────────────────────────────────────────────────────
@app.on_message(filters.command("export") & filters.private)
@admin_only
async def cmd_export(client: Client, message: Message):
    import csv, io, datetime, tempfile, os
    data = stats_db.all_stats() if hasattr(stats_db, 'all_stats') else {}
    if not data:
        await message.reply("📊 No stats to export yet.")
        return
    fd, tmp = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Chat ID", "Title", "Count", "First Seen", "Last Seen"])
            for cid, info in sorted(data.items(), key=lambda x: -x[1].get("count", 0)):
                writer.writerow([
                    cid,
                    info.get("title", ""),
                    info.get("count", 0),
                    info.get("first_seen", ""),
                    info.get("last_seen", ""),
                ])
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        await client.send_document(
            message.chat.id,
            document=tmp,
            file_name=f"forwarder_stats_{ts}.csv",
            caption=f"📊 Forwarding stats — {len(data)} sources | exported {ts}",
        )
    finally:
        try: os.remove(tmp)
        except Exception: pass


# ── /keywords ─────────────────────────────────────────────────────────────────
@app.on_message(filters.command("keywords") & filters.private)
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
            await message.reply(
                f"**🔍 Keyword filter** — mode: `{mode}`\n\nNo keywords set.\n\n"
                "• `/keywords allow <word>` — only forward matching\n"
                "• `/keywords block <word>` — skip matching\n"
                "• `/keywords off` — disable filter", parse_mode="markdown"
            )
            return
        lines = "\n".join(f"`{i+1}.` {k}" for i, k in enumerate(kws))
        await message.reply(
            f"**🔍 Keyword filter** — mode: `{mode}`\n\n{lines}\n\n"
            "• `/keywords allow <word>` — add allow keyword\n"
            "• `/keywords block <word>` — add block keyword\n"
            "• `/keywords remove <n>` — remove by number\n"
            "• `/keywords off` — disable filter", parse_mode="markdown"
        )
    elif sub in ("allow", "block"):
        if not value:
            await message.reply(f"Usage: `/keywords {sub} <keyword>`", parse_mode="markdown")
            return
        keyword_filter.set_mode(sub)
        added = keyword_filter.add_keyword(value)
        state = keyword_filter.get_state()
        if added:
            await message.reply(
                f"✅ Added `{value}` to **{sub}** list.\n"
                f"Mode: `{sub}` — {len(state['keywords'])} keyword(s) active.",
                parse_mode="markdown"
            )
        else:
            await message.reply(f"ℹ️ `{value}` already in list.", parse_mode="markdown")
    elif sub in ("remove", "del"):
        if not value.isdigit():
            await message.reply("Usage: `/keywords remove <number>`", parse_mode="markdown")
            return
        result = keyword_filter.remove_keyword(int(value))
        if result in ("No keywords set.", ) or result.startswith("Index"):
            await message.reply(f"❌ {result}", parse_mode="markdown")
        else:
            await message.reply(f"🗑️ Removed: `{result}`", parse_mode="markdown")
    elif sub == "off":
        keyword_filter.set_mode("off")
        await message.reply("✅ Keyword filter disabled — all files forwarded.", parse_mode="markdown")
    else:
        await message.reply(
            "**Usage:**\n"
            "• `/keywords list`\n"
            "• `/keywords allow <word>`\n"
            "• `/keywords block <word>`\n"
            "• `/keywords remove <n>`\n"
            "• `/keywords off`", parse_mode="markdown"
        )



# ── /help ──────────────────────────────────────────────────────────────────
@app.on_message(filters.command(["start", "help"]) & filters.private)
async def cmd_help(client: Client, message: Message):
    me = await client.get_me()
    await message.reply(
        f"**TG File Forwarder** — `{me.first_name}`\n\n"
        "**Source commands:**\n"
        "• `/addchat` / `/removechat` / `/listchats`\n\n"
        "**Stats:**\n"
        "• `/fwrstatus` — full session stats\n"
        "• `/dupstats` — duplicate detection\n"
        "• `/srcstats` — files per source group\n"
        "• `/resetdups` — clear dup memory\n\n"
        "**Routing:**\n"
        "• `/route <src> <dest>` / `/routes`\n\n"
        "**Pause / Resume:**\n"
        "• `/pausefwd` / `/resumefwd`\n\n"
        "**Captions:**\n"
        "• `/setcaption <text>` — add suffix to every file caption\n"
        "• `/setcaption off` — remove suffix\n"
        "• `/strippatterns add/remove/list` — manage watermark strip patterns\n"
        "• `/cleancaptions` — edit existing captions in index channel\n"
        "• `/stopcleaning` — cancel the clean job\n\n"
        "**Recovery:**\n"
        "• `/failedstats` — show failed forwards\n"
        "• `/retry` — retry all failed\n"
        "• `/clearfailed` — wipe failed list\n\n"
        "**Sources:**\n"
        "• `/ignorechat <chat>` — pause a source\n"
        "• `/unignorechat <chat>` — re-enable\n"
        "• `/listignored` — show ignored\n"
        "• `/joinchat <link>` — join + add to sources\n\n"
        "**Filters:**\n"
        "• `/keywords list/allow/block/remove/off`\n\n"
        "**Export:**\n"
        "• `/export` — download stats as CSV\n\n"
        "**Discovery:**\n"
        "• `/discover` / `/suggest <keyword>`\n",
        parse_mode="markdown"
    )


# ── Startup ────────────────────────────────────────────────────────────────
async def main():
    await app.start()
    me = await app.get_me()
    logger.info(f"🚀 Forwarder started as: {me.first_name} (@{me.username})")

    current = get_all_chats(SOURCE_CHATS)
    logger.info(f"👀 Watching {len(current)} source chat(s)")
    logger.info(f"📊 Seen DB: {seen_count():,} | All-time: {stats_db.total():,} | Patterns: {sp_db.count()}")

    for src in current:
        try:
            chat = await app.get_chat(src)
            logger.info(f"  ✅ {chat.title}")
        except (ChannelPrivate, UserNotParticipant):
            logger.warning(f"  ⚠️  Not a member of: {src}")
        except Exception as e:
            logger.warning(f"  ⚠️  Cannot verify {src}: {e}")

    # Auto-retry failed forwards from last session
    failed_count = failed_db.count()
    if failed_count > 0:
        logger.info(f"⚠️  {failed_count} failed forward(s) from previous session — scheduling auto-retry in 30s")
        async def _auto_retry():
            await asyncio.sleep(30)
            logger.info("🔄 Auto-retry: starting…")
            from utils import safe_forward as _sf
            items = failed_db.load()
            ok = failed = 0
            for item in items:
                try:
                    msg = await app.get_messages(int(item["chat_id"]), int(item["message_id"]))
                    dest = item.get("dest", DEST_CHANNEL)
                    if await _sf(msg, dest):
                        failed_db.remove(item["chat_id"], item["message_id"])
                        ok += 1
                    else:
                        failed += 1
                except Exception as exc:
                    logger.warning(f"Auto-retry error: {exc}")
                    failed += 1
            logger.info(f"✅ Auto-retry done: {ok} recovered, {failed} still failed")
            if LOG_CHANNEL and ok > 0:
                try:
                    await app.send_message(LOG_CHANNEL, f"🔄 Auto-retry: {ok} recovered, {failed} still failed")
                except Exception:
                    pass
        asyncio.create_task(_auto_retry())

    # Start watchdog
    asyncio.create_task(_session_watchdog())
    logger.info("🛡️ Session watchdog started (checks every 5 min)")

    port = int(os.environ.get("PORT", 8080))
    try:
        from dashboard import start_dashboard
        asyncio.create_task(start_dashboard(stats_getter=lambda: _stats, port=port))
    except ImportError:
        pass

    if LOG_CHANNEL:
        try:
            suffix_info = f"Suffix: `{cs_db.get()}`" if cs_db.get() else "No suffix"
            await app.send_message(LOG_CHANNEL,
                f"✅ **Forwarder started**\nAs: `{me.first_name}`\n"
                f"Watching `{len(current)}` chats | All-time: {stats_db.total():,} files\n"
                f"Custom patterns: {sp_db.count()} | {suffix_info}")
        except Exception:
            pass

    logger.info("⏳ Listening for new files...")
    await idle()
    await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
