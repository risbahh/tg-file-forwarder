"""
PM Bot Forwarder — pm_bot_forwarder.py

Watches source groups for auto-filter bot result messages (e.g. NarutoXMoviesBot).
When the bot posts deep-link results in the group, this userbot:
  1. Extracts every `start=files_XXXXX` deep link from the result message
  2. Sends /start <param> to the bot in PM via a rate-limited queue
  3. Captures every file/video the bot sends back in PM
  4. Forwards each file immediately to DEST_CHANNEL — permanently skipping duplicates

Deduplication (both layers survive restarts):
  • file_unique_id  → seen_db (seen.json)         never re-forward same file
  • start param     → pm_processed.json            never re-send same deep link

Commands (DM the userbot, admin only):
  /dumpbot <group> [limit]  scan group history, queue all past deep links
  /pmstatus                 queue size, forwarded/skipped counts
  /pmclear confirm          reset processed-links cache (keeps file dedup intact)
  /help                     this message

Environment variables:
  SESSION_STRING      Pyrogram session string
  API_ID              Telegram API ID
  API_HASH            Telegram API hash
  SOURCE_BOT          Bot username to watch (default: NarutoXMoviesBot)
  SOURCE_GROUPS       Comma-separated group IDs/usernames to watch
  DEST_CHANNEL        Channel ID to forward files into
  ADMIN_IDS           Comma-separated admin Telegram user IDs
  PM_DELAY            Seconds between PM start commands (default: 4)
  LOG_CHANNEL         (optional) channel to log activity
  PM_PROCESSED_FILE   Path to processed-links JSON (default: pm_processed.json)
"""

import asyncio
import json
import logging
import os
import re

from dotenv import load_dotenv
from pyrogram import Client, filters, idle
from pyrogram.types import Message

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("pm_bot_fwd")

# ── Config ────────────────────────────────────────────────────────────────────
API_ID        = int(os.environ["API_ID"])
API_HASH      = os.environ["API_HASH"]
SESSION_STR   = os.environ["SESSION_STRING"]
SOURCE_BOT    = os.environ.get("SOURCE_BOT", "NarutoXMoviesBot").lstrip("@")
DEST_CHANNEL  = int(os.environ["DEST_CHANNEL"])
LOG_CHANNEL   = int(os.environ["LOG_CHANNEL"]) if os.environ.get("LOG_CHANNEL") else None
PM_DELAY      = float(os.environ.get("PM_DELAY", "4"))
ADMIN_IDS     = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().lstrip("-").isdigit()]
_PROCESSED_FILE = os.environ.get("PM_PROCESSED_FILE", "pm_processed.json")

# Parse SOURCE_GROUPS into a mixed list of int IDs and str usernames
_raw_groups   = [g.strip() for g in os.environ.get("SOURCE_GROUPS", "").split(",") if g.strip()]
SOURCE_GROUPS: list = []
for _g in _raw_groups:
    try:
        SOURCE_GROUPS.append(int(_g))
    except ValueError:
        SOURCE_GROUPS.append(_g.lstrip("@").lower())

# ── Persistent: processed start params ───────────────────────────────────────
_proc_lock = asyncio.Lock()          # async-safe; held only for in-memory ops
_SAVE_BATCH = 50                     # write pm_processed.json every N new items
_unsaved_count = 0                   # items added since last save


def _load_processed() -> set:
    if os.path.exists(_PROCESSED_FILE):
        try:
            with open(_PROCESSED_FILE) as f:
                data = json.load(f)
            return set(data) if isinstance(data, list) else set()
        except Exception:
            pass
    return set()


def _flush_processed(processed: set):
    """Write processed set to disk (called outside the async lock)."""
    try:
        with open(_PROCESSED_FILE, "w") as f:
            json.dump(sorted(processed), f)
    except Exception as e:
        logger.warning(f"pm_processed save error: {e}")


_processed: set = _load_processed()
logger.info(f"Loaded {len(_processed):,} already-processed start params from {_PROCESSED_FILE}")

# ── Persistent: seen file_unique_ids via seen_db ──────────────────────────────
try:
    from seen_db import is_seen as _is_seen_db, mark_seen as _mark_seen_db
    _USE_SEEN_DB = True
    logger.info("Using seen_db for file deduplication (persistent ✅)")
except ImportError:
    _USE_SEEN_DB = False
    _seen_ids: set = set()
    logger.warning("seen_db not found — using in-memory dedup (resets on restart ⚠️)")


def _is_seen(uid: str) -> bool:
    if _USE_SEEN_DB:
        return _is_seen_db(uid)
    return uid in _seen_ids


def _mark_seen(uid: str):
    if _USE_SEEN_DB:
        _mark_seen_db(uid)
    else:
        _seen_ids.add(uid)


# ── Runtime state ─────────────────────────────────────────────────────────────
_queue: asyncio.Queue | None = None   # initialized in main() after event loop starts
_stats = {"queued": 0, "forwarded": 0, "skipped_dup": 0, "errors": 0}

DEEPLINK_RE = re.compile(
    r'(?:https?://)?t(?:elegram)?\.me/(\w+)\?start=([\w_-]+)',
    re.IGNORECASE
)

# ── Pyrogram client ───────────────────────────────────────────────────────────
app = Client(
    "pm_bot_fwd",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STR,
)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _admin_only(func):
    """Decorator: only allow ADMIN_IDS to run a command."""
    async def wrapper(client: Client, message: Message):
        user = message.from_user
        if not user:
            return                                # anonymous / channel post
        if ADMIN_IDS and user.id not in ADMIN_IDS:
            return                                # not an admin
        return await func(client, message)
    wrapper.__name__ = func.__name__
    return wrapper


def _is_source_group(message: Message) -> bool:
    """Return True if the message's chat is in our watched SOURCE_GROUPS."""
    if not SOURCE_GROUPS:
        return True   # no filter set → watch all groups
    cid = message.chat.id
    uname = (getattr(message.chat, "username", "") or "").lower()
    for g in SOURCE_GROUPS:
        if isinstance(g, int) and g == cid:
            return True
        if isinstance(g, str) and g == uname:
            return True
    return False


def extract_deeplinks(message: Message) -> list:
    """Return list of (bot_username, start_param) tuples from a message."""
    links = []
    # Inline keyboard buttons
    if message.reply_markup and hasattr(message.reply_markup, "inline_keyboard"):
        for row in message.reply_markup.inline_keyboard:
            for btn in row:
                url = getattr(btn, "url", None) or ""
                for m in DEEPLINK_RE.finditer(url):
                    links.append((m.group(1), m.group(2)))
    # Text / caption
    for text in [message.text or "", message.caption or ""]:
        for m in DEEPLINK_RE.finditer(text):
            links.append((m.group(1), m.group(2)))
    return links


async def _enqueue(bot_username: str, start_param: str, force_save: bool = False) -> bool:
    """
    Queue a start param for PM delivery.
    Returns True if newly queued, False if already processed.
    Batches disk writes every _SAVE_BATCH items to avoid blocking the event loop.
    """
    global _unsaved_count
    async with _proc_lock:
        if start_param in _processed:
            return False
        _processed.add(start_param)
        _unsaved_count += 1
        should_save = force_save or (_unsaved_count >= _SAVE_BATCH)
        if should_save:
            snapshot = set(_processed)
            _unsaved_count = 0

    if should_save:
        # Run disk write in a thread so we don't block the event loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _flush_processed, snapshot)

    if _queue is not None:
        await _queue.put((bot_username, start_param))
    _stats["queued"] += 1
    logger.info(f"Queued @{bot_username} start={start_param} (total={_stats['queued']})")
    return True


# ── Watch source groups for bot result messages ───────────────────────────────
@app.on_message(filters.group & filters.incoming)
async def on_group_message(client: Client, message: Message):
    """Fires only in group/supergroup chats — never in PMs."""
    if not _is_source_group(message):
        return

    sender = message.from_user or message.sender_chat
    username = (getattr(sender, "username", "") or "").lower()
    if username != SOURCE_BOT.lower():
        return

    links = extract_deeplinks(message)
    if not links:
        return

    new_count = 0
    for bot_username, start_param in links:
        if await _enqueue(bot_username, start_param):
            new_count += 1

    if new_count:
        logger.info(f"Group {message.chat.id}: {new_count} new deep link(s) queued")


# ── Watch PM for files from the source bot ────────────────────────────────────
@app.on_message(filters.private & filters.incoming)
async def on_pm_file(client: Client, message: Message):
    """Captures every file the source bot sends in PM and forwards it."""
    sender = message.from_user or message.sender_chat
    username = (getattr(sender, "username", "") or "").lower()
    if username != SOURCE_BOT.lower():
        return

    media = message.document or message.video or message.audio or message.photo
    if not media:
        return

    unique_id = getattr(media, "file_unique_id", None)
    if not unique_id:
        return

    # Permanent dedup check — survives restarts
    if _is_seen(unique_id):
        _stats["skipped_dup"] += 1
        logger.info(f"⏭ Duplicate skipped: {unique_id} (total={_stats['skipped_dup']})")
        return

    # Mark BEFORE forwarding to prevent race condition on concurrent PM messages
    _mark_seen(unique_id)

    try:
        await message.forward(DEST_CHANNEL)
        _stats["forwarded"] += 1
        logger.info(f"✅ Forwarded → {DEST_CHANNEL} (total={_stats['forwarded']})")
    except Exception as e:
        _stats["errors"] += 1
        logger.error(f"Forward error: {e}")


# ── Queue worker: sends /start <param> to bot in PM ──────────────────────────
async def _queue_worker():
    logger.info("Queue worker started")
    while True:
        bot_username, start_param = await _queue.get()
        try:
            await app.send_message(bot_username, f"/start {start_param}")
            logger.info(f"Sent /start {start_param} to @{bot_username}")
        except Exception as e:
            logger.error(f"PM send error (@{bot_username}): {e}")
            _stats["errors"] += 1
        finally:
            _queue.task_done()
        await asyncio.sleep(PM_DELAY)   # rate-limit between PM sends


# ── /dumpbot ──────────────────────────────────────────────────────────────────
@app.on_message(filters.command("dumpbot") & filters.private)
@_admin_only
async def cmd_dumpbot(client: Client, message: Message):
    args = message.text.split(None, 2)
    if len(args) < 2:
        await message.reply(
            "**Usage:** `/dumpbot <group_id or @username> [limit]`\n\n"
            f"Scans group history for @{SOURCE_BOT} result messages\n"
            "and queues every unseen deep link.\n\n"
            "Default limit: `10000` messages\n"
            "Example: `/dumpbot -1001234567890 50000`",
            parse_mode="markdown"
        )
        return

    group = args[1].strip()
    try:
        group_id = int(group)
    except ValueError:
        group_id = group

    limit = int(args[2]) if len(args) > 2 and args[2].strip().isdigit() else 10000

    prog = await message.reply(
        f"🔍 Scanning `{group}` for @{SOURCE_BOT} results...\n"
        f"Limit: `{limit:,}` messages — this may take a while.",
        parse_mode="markdown"
    )

    scanned = found_msgs = queued = 0
    try:
        async for msg in client.get_chat_history(group_id, limit=limit):
            scanned += 1
            sender = msg.from_user or msg.sender_chat
            username = (getattr(sender, "username", "") or "").lower()
            if username == SOURCE_BOT.lower():
                found_msgs += 1
                for bot_username, start_param in extract_deeplinks(msg):
                    if await _enqueue(bot_username, start_param):
                        queued += 1

            if scanned % 500 == 0:
                try:
                    await prog.edit(
                        f"🔍 Scanned: `{scanned:,}` messages\n"
                        f"📨 Bot results found: `{found_msgs}`\n"
                        f"🔗 New links queued: `{queued}`\n"
                        f"⏳ Queue size: `{_queue.qsize() if _queue else 0}`",
                        parse_mode="markdown"
                    )
                except Exception:
                    pass
            await asyncio.sleep(0.02)

    except Exception as e:
        await prog.edit(f"❌ Scan error: `{e}`", parse_mode="markdown")
        return
    finally:
        # Force-save any remaining unsaved processed params
        if _unsaved_count > 0:
            await asyncio.get_event_loop().run_in_executor(None, _flush_processed, set(_processed))

    q_size = _queue.qsize() if _queue else 0
    eta_min = (q_size * PM_DELAY) / 60
    await prog.edit(
        f"✅ **Dump scan complete**\n\n"
        f"📜 Messages scanned: `{scanned:,}`\n"
        f"📨 Bot results found: `{found_msgs}`\n"
        f"🔗 New links queued: `{queued}`\n"
        f"⏳ Queue size: `{q_size}` — ETA ~`{eta_min:.0f}` min\n\n"
        f"Files will forward to `{DEST_CHANNEL}` as the queue processes.\n"
        f"Already-seen files are skipped automatically.",
        parse_mode="markdown"
    )
    if LOG_CHANNEL:
        try:
            await client.send_message(LOG_CHANNEL, f"🗂 Dump: queued {queued} links from {group}")
        except Exception:
            pass


# ── /pmstatus ─────────────────────────────────────────────────────────────────
@app.on_message(filters.command("pmstatus") & filters.private)
@_admin_only
async def cmd_pmstatus(client: Client, message: Message):
    q_size = _queue.qsize() if _queue else 0
    eta_min = (q_size * PM_DELAY) / 60
    dedup_info = "seen_db (persistent ✅)" if _USE_SEEN_DB else "in-memory ⚠️"
    await message.reply(
        f"**📊 PM Bot Forwarder Status**\n\n"
        f"🤖 Source bot: @{SOURCE_BOT}\n"
        f"👁 Watching: `{len(SOURCE_GROUPS)} group(s)` (empty = all)\n"
        f"📺 Destination: `{DEST_CHANNEL}`\n"
        f"🔁 Dedup: {dedup_info}\n\n"
        f"🔗 Start params processed: `{len(_processed):,}`\n"
        f"✅ Files forwarded: `{_stats['forwarded']:,}`\n"
        f"⏭ Duplicates skipped: `{_stats['skipped_dup']:,}`\n"
        f"⏳ Queue size: `{q_size:,}` (~`{eta_min:.0f}` min remaining)\n"
        f"❌ Errors: `{_stats['errors']}`",
        parse_mode="markdown"
    )


# ── /pmclear ──────────────────────────────────────────────────────────────────
@app.on_message(filters.command("pmclear") & filters.private)
@_admin_only
async def cmd_pmclear(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2 or args[1].strip().lower() != "confirm":
        await message.reply(
            f"⚠️ This clears `{len(_processed):,}` processed-link records.\n"
            f"Deep links will be re-sent to @{SOURCE_BOT} on next encounter.\n"
            f"**File dedup (seen_db) is NOT cleared** — no duplicate files forwarded.\n\n"
            f"Send `/pmclear confirm` to proceed.",
            parse_mode="markdown"
        )
        return
    async with _proc_lock:
        n = len(_processed)
        _processed.clear()
    await asyncio.get_event_loop().run_in_executor(None, _flush_processed, set())
    await message.reply(
        f"🗑 Cleared `{n:,}` processed-link records.\n"
        f"File dedup (seen_db) intact — no duplicates will be forwarded.",
        parse_mode="markdown"
    )


# ── /help ─────────────────────────────────────────────────────────────────────
@app.on_message(filters.command(["start", "help"]) & filters.private)
async def cmd_help(client: Client, message: Message):
    me = await client.get_me()
    await message.reply(
        f"**PM Bot Forwarder** — `{me.first_name}`\n\n"
        f"Watches @{SOURCE_BOT} results and forwards all files to `{DEST_CHANNEL}`.\n"
        f"Duplicates are permanently skipped (survives restarts).\n\n"
        "**Commands:**\n"
        "• `/dumpbot <group> [limit]` — scan history, queue all past deep links\n"
        "• `/pmstatus` — queue, forwarded, skipped counts\n"
        "• `/pmclear confirm` — reset processed-links cache (keeps file dedup)\n",
        parse_mode="markdown"
    )


# ── Startup ───────────────────────────────────────────────────────────────────
async def main():
    global _queue
    _queue = asyncio.Queue()

    await app.start()
    me = await app.get_me()
    logger.info(f"🚀 PM Bot Forwarder started as: {me.first_name} (@{me.username})")
    logger.info(f"🤖 Source bot: @{SOURCE_BOT}")
    logger.info(f"📺 Destination: {DEST_CHANNEL}")
    logger.info(f"👁 Watching {len(SOURCE_GROUPS)} group(s): {SOURCE_GROUPS or 'ALL'}")
    logger.info(f"⏱ PM delay: {PM_DELAY}s | Dedup: {'seen_db' if _USE_SEEN_DB else 'in-memory'}")

    asyncio.create_task(_queue_worker())

    if LOG_CHANNEL:
        try:
            await app.send_message(
                LOG_CHANNEL,
                f"✅ **PM Bot Forwarder started**\n"
                f"As: `{me.first_name}` | Source: @{SOURCE_BOT}\n"
                f"Groups: {len(SOURCE_GROUPS)} | Dedup: {'seen_db ✅' if _USE_SEEN_DB else 'in-memory ⚠️'}"
            )
        except Exception:
            pass

    logger.info("⏳ Listening for bot results...")
    await idle()

    # Graceful shutdown: flush any unsaved processed params
    if _unsaved_count > 0:
        logger.info("Flushing processed params on shutdown...")
        _flush_processed(_processed)

    await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
