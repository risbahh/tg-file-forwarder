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
import ignore_db
import keyword_filter
from dashboard import start_dashboard

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
                    if _pool and await _pool.forward(msg, dest_r):
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
