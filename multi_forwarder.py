"""
Multi-Account Forwarder — multi_forwarder.py
Commands: /addchat /removechat /listchats /route /routes
          /resetdups /pausefwd /resumefwd /srcstats
          /setcaption /strippatterns /cleancaptions /stopcleaning
          /poolstatus /help
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
        await message.reply("Pool not initialized yet.")
        return
    text = await _pool.status()
    pause_line = f"\n⏸️ **PAUSED** — {_stats['skipped_paused']} dropped\n" if _paused else ""
    suffix = cs_db.get()
    text += (
        f"\n{pause_line}"
        f"✅ Forwarded: `{_stats['forwarded']}`\n"
        f"⏭️ Dup-skipped: `{_stats['skipped_dup']}`\n"
        f"⏸️ Paused-dropped: `{_stats['skipped_paused']}`\n"
        f"❌ Failed: `{_stats['failed']}`\n"
        f"🗂️ Seen DB: `{seen_count():,}`\n"
        f"📊 All-time: `{stats_db.total():,}` files\n"
        f"**Suffix:** " + (f"`{suffix}`" if suffix else "_not set_") + "\n"
        f"**Patterns:** `{sp_db.count()}`"
    )
    await message.reply(text, parse_mode="markdown")


# ── Source commands ────────────────────────────────────────────────────────
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


# ── Routing ────────────────────────────────────────────────────────────────
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


# ── /resetdups /srcstats ───────────────────────────────────────────────────
@_listener.on_message(filters.command("resetdups") & filters.private)
@admin_only
async def cmd_resetdups(client: Client, message: Message):
    args = message.text.split(None, 1)
    confirmed = len(args) > 1 and args[1].strip().lower() == "confirm"
    n = seen_count()
    if not confirmed:
        await message.reply(f"⚠️ Will erase `{n:,}` tracked IDs.\nNo files deleted — only memory.\n\n**To confirm:** `/resetdups confirm`", parse_mode="markdown")
        return
    seen_reset()
    _stats["skipped_dup"] = 0
    await message.reply(f"✅ Cleared `{n:,}` file IDs.", parse_mode="markdown")


@_listener.on_message(filters.command("srcstats") & filters.private)
@admin_only
async def cmd_srcstats(client: Client, message: Message):
    rows  = stats_db.get_all()
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


@_listener.on_message(filters.command("resumefwd") & filters.private)
@admin_only
async def cmd_resumefwd(client: Client, message: Message):
    global _paused
    if not _paused:
        await message.reply("▶️ Already running.", parse_mode="markdown")
        return
    dropped = _stats["skipped_paused"]
    _paused = False
    _stats["skipped_paused"] = 0
    await message.reply(f"▶️ **Forwarding resumed.**\nDropped: `{dropped}` files", parse_mode="markdown")


# ── /setcaption ────────────────────────────────────────────────────────────
@_listener.on_message(filters.command("setcaption") & filters.private)
@admin_only
async def cmd_setcaption(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) == 1:
        current = cs_db.get()
        if current:
            await message.reply(f"**Current suffix:** `{current}`\n\nChange: `/setcaption <text>`\nRemove: `/setcaption off`", parse_mode="markdown")
        else:
            await message.reply("**No suffix set.**\n\nUse `/setcaption <text>` to add one.\nExample: `/setcaption 🎬 @MyChannel`", parse_mode="markdown")
        return
    text = args[1].strip()
    if text.lower() == "off":
        cs_db.clear()
        await message.reply("✅ Caption suffix removed.", parse_mode="markdown")
    else:
        cs_db.set(text)
        await message.reply(f"✅ **Caption suffix set:**\n`{text}`", parse_mode="markdown")


# ── /strippatterns ─────────────────────────────────────────────────────────
@_listener.on_message(filters.command("strippatterns") & filters.private)
@admin_only
async def cmd_strippatterns(client: Client, message: Message):
    args = message.text.split(None, 2)
    sub  = args[1].strip().lower() if len(args) > 1 else "list"

    if sub == "list":
        patterns = sp_db.load()
        if not patterns:
            await message.reply("**Custom strip patterns:** _none_\n\nAdd: `/strippatterns add <regex>`", parse_mode="markdown")
        else:
            lines = ["**Custom strip patterns:**\n"] + [f"`{i}.` `{p}`" for i, p in enumerate(patterns, 1)]
            lines.append("\nRemove: `/strippatterns remove <number>`")
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
            await message.reply(f"✅ Pattern added: `{pattern}`\nTotal: `{sp_db.count()}`", parse_mode="markdown")
        else:
            await message.reply(f"⚠️ Already exists: `{pattern}`", parse_mode="markdown")

    elif sub == "remove":
        if len(args) < 3 or not args[2].strip().isdigit():
            await message.reply("**Usage:** `/strippatterns remove <number>`", parse_mode="markdown")
            return
        removed = sp_db.remove(int(args[2].strip()))
        if removed:
            await message.reply(f"✅ Removed: `{removed}`", parse_mode="markdown")
        else:
            await message.reply("❌ Number out of range.", parse_mode="markdown")
    else:
        await message.reply(
            "• `/strippatterns list`\n• `/strippatterns add <regex>`\n• `/strippatterns remove <number>`",
            parse_mode="markdown"
        )


# ── /cleancaptions /stopcleaning ───────────────────────────────────────────
@_listener.on_message(filters.command("cleancaptions") & filters.private)
@admin_only
async def cmd_cleancaptions(client: Client, message: Message):
    global _cleaning_task, _stop_cleaning

    if _cleaning_task and not _cleaning_task.done():
        await message.reply("⚠️ Already running. Send `/stopcleaning` to cancel.", parse_mode="markdown")
        return

    args   = message.text.split(None, 1)
    target = int(args[1].strip()) if len(args) > 1 and args[1].strip().lstrip("-").isdigit() else DEST_CHANNEL
    if not target:
        await message.reply("❌ No destination channel configured.", parse_mode="markdown")
        return

    _stop_cleaning = False
    status_msg = await message.reply(
        f"🧹 **Caption cleaning started**\n\nChannel: `{target}`\nSend `/stopcleaning` to stop.",
        parse_mode="markdown"
    )

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
                        await status_msg.edit(
                            f"🧹 **Cleaning in progress...**\n\n"
                            f"📂 Scanned: `{scanned:,}` | ✏️ Edited: `{edited:,}` | ❌ Errors: `{errors}`",
                            parse_mode="markdown"
                        )
                    except Exception:
                        pass
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
            logger.error(f"/cleancaptions error: {e}")

        status = "✅ Complete" if not _stop_cleaning else "⛔ Stopped"
        try:
            await status_msg.edit(
                f"🧹 **Caption cleaning {status}**\n\n"
                f"📂 Scanned: `{scanned:,}` | ✏️ Edited: `{edited:,}` | ❌ Errors: `{errors}`",
                parse_mode="markdown"
            )
        except Exception:
            pass

    _cleaning_task = asyncio.create_task(_clean_loop())


@_listener.on_message(filters.command("stopcleaning") & filters.private)
@admin_only
async def cmd_stopcleaning(client: Client, message: Message):
    global _stop_cleaning, _cleaning_task
    if not _cleaning_task or _cleaning_task.done():
        await message.reply("No cleaning job is running.", parse_mode="markdown")
        return
    _stop_cleaning = True
    await message.reply("⛔ Stopping after current message...", parse_mode="markdown")



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
            msgs = await pool.get_client().get_messages(chat_id, message_id)
            msg = msgs if not isinstance(msgs, list) else msgs[0]
            if not msg or not msg.id:
                skip += 1
                failed_db.remove(chat_id, message_id)
                continue
            client_to_use = await pool.get_available()
            success = await safe_forward_pool(msg, dest, pool, skip_duplicates=True)
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


# ── /help ─────────────────────────────────────────────────────────────────
@_listener.on_message(filters.command(["start", "help"]) & filters.private)
async def cmd_help(client: Client, message: Message):
    me = await client.get_me()
    await message.reply(
        f"**Multi-Account Forwarder** — `{me.first_name}`\n\n"
        "• `/addchat` / `/removechat` / `/listchats`\n"
        "• `/route <src> <dest>` / `/routes`\n"
        "• `/resetdups` / `/srcstats`\n"
        "• `/pausefwd` / `/resumefwd`\n"
        "• `/setcaption <text|off>` — caption suffix\n"
        "• `/strippatterns add/remove/list`\n"
        "• `/cleancaptions` / `/stopcleaning`\n"
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
    logger.info(f"🏊 Pool: {_pool.account_count()} account(s)")

    current = get_all_chats(SOURCE_CHATS)
    logger.info(f"👀 Watching {len(current)} sources | all-time: {stats_db.total():,} files")

    asyncio.create_task(_session_watchdog())
    logger.info("🛡️ Session watchdog started")

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
