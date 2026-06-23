"""
PM Bot Forwarder — pm_bot_forwarder.py

Watches source groups for auto-filter bot result messages (e.g. NarutoXMoviesBot).
When the bot posts deep-link results in the group, this userbot:
  1. Extracts the `start=files_XXXXX` deep links from every result message
  2. Sends /start <param> to the bot in PM (via a rate-limited queue)
  3. Captures every file/video the bot sends back in PM
  4. Forwards each file immediately to DEST_CHANNEL — skipping duplicates permanently

Deduplication:
  - file_unique_id stored in seen_db (seen.json) — survives restarts
  - processed start params stored in pm_processed.json — survives restarts

Commands (DM the userbot):
  /dumpbot <group> [limit]  — scan group history, queue all past deep links
  /pmstatus                 — show queue, processed, forwarded counts
  /pmclear confirm          — reset the processed-links cache (re-process all)
  /help                     — command list

Environment variables:
  SESSION_STRING    Pyrogram session string
  API_ID            Telegram API ID
  API_HASH          Telegram API hash
  SOURCE_BOT        Bot username to watch (default: NarutoXMoviesBot)
  SOURCE_GROUPS     Comma-separated group IDs/usernames to watch for results
  DEST_CHANNEL      Channel ID to forward files into
  ADMIN_IDS         Comma-separated admin Telegram user IDs
  PM_DELAY          Seconds between PM start commands (default: 4)
  LOG_CHANNEL       (optional) channel to log activity
  PM_PROCESSED_FILE Path to processed-links JSON (default: pm_processed.json)
"""

import asyncio
import json
import logging
import os
import re
import threading
import time

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

_raw_groups   = [g.strip() for g in os.environ.get("SOURCE_GROUPS", "").split(",") if g.strip()]
SOURCE_GROUPS: list[int | str] = []
for _g in _raw_groups:
    try:
        SOURCE_GROUPS.append(int(_g))
    except ValueError:
        SOURCE_GROUPS.append(_g)

# ── Persistent: processed start params ───────────────────────────────────────
_proc_lock = threading.Lock()


def _load_processed() -> set[str]:
    if os.path.exists(_PROCESSED_FILE):
        try:
            with open(_PROCESSED_FILE) as f:
                data = json.load(f)
            return set(data) if isinstance(data, list) else set()
        except Exception:
            pass
    return set()


def _save_processed(processed: set[str]):
    with open(_PROCESSED_FILE, "w") as f:
        json.dump(sorted(processed), f)


_processed: set[str] = _load_processed()
logger.info(f"Loaded {len(_processed):,} already-processed start params from {_PROCESSED_FILE}")

# ── Persistent: seen file_unique_ids (uses seen_db) ──────────────────────────
try:
    from seen_db import is_seen, mark_seen
    _USE_SEEN_DB = True
    logger.info("Using seen_db for file deduplication (persistent)")
except ImportError:
    _USE_SEEN_DB = False
    _seen_ids: set[str] = set()
    logger.warning("seen_db not found — using in-memory dedup (resets on restart)")


def _is_seen(file_unique_id: str) -> bool:
    if _USE_SEEN_DB:
        return is_seen(file_unique_id)
    return file_unique_id in _seen_ids


def _mark_seen(file_unique_id: str):
    if _USE_SEEN_DB:
        mark_seen(file_unique_id)
    else:
        _seen_ids.add(file_unique_id)


# ── Runtime state ─────────────────────────────────────────────────────────────
_queue: asyncio.Queue
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
    async def wrapper(client, message):
        if not ADMIN_IDS or message.from_user.id in ADMIN_IDS:
            return await func(client, message)
    wrapper.__name__ = func.__name__
    return wrapper


def extract_deeplinks(message: Message) -> list[tuple[str, str]]:
    """Return list of (bot_username, start_param) from a message."""
    links: list[tuple[str, str]] = []
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


async def _enqueue(bot_username: str, start_param: str) -> bool:
    """Queue a start param. Returns True if newly queued, False if already processed."""
    with _proc_lock:
        if start_param in _processed:
            return False
        _processed.add(start_param)
        _save_processed(_processed)
    await _queue.put((bot_username, start_param))
    _stats["queued"] += 1
    logger.info(f"Queued: @{bot_username} start={start_param} (total={_stats['queued']})")
    return True


# ── Watch source groups for bot result messages ───────────────────────────────
@app.on_message(filters.incoming)
async def on_any_message(client: Client, message: Message):
    # Only care about watched groups
    if SOURCE_GROUPS:
        chat_id = message.chat.id
        chat_username = (getattr(message.chat, "username", "") or "").lower()
        matched = any(
            (isinstance(g, int) and g == chat_id) or
            (isinstance(g, str) and g.lstrip("@").lower() == chat_username)
            for g in SOURCE_GROUPS
        )
        if not matched:
            return

    # Only process messages from the target bot
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

    # Permanent deduplication check
    if _is_seen(unique_id):
        _stats["skipped_dup"] += 1
        logger.info(f"⏭ Skipped duplicate: {unique_id} (total skipped={_stats['skipped_dup']})")
        return

    # Mark as seen BEFORE forwarding to prevent race conditions
    _mark_seen(unique_id)

    try:
        await message.forward(DEST_CHANNEL)
        _stats["forwarded"] += 1
        logger.info(f"✅ Forwarded → {DEST_CHANNEL} (total={_stats['forwarded']})")
    except Exception as e:
        _stats["errors"] += 1
        logger.error(f"Forward error: {e}")


# ── Queue worker ──────────────────────────────────────────────────────────────
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
        # Rate limit: keep delay between PM requests
        await asyncio.sleep(PM_DELAY)


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
            "Default limit: `10000` messages",
            parse_mode="markdown"
        )
        return

    group = args[1].strip()
    try:
        group_id: int | str = int(group)
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
                        f"⏳ Queue size: `{_queue.qsize()}`",
                        parse_mode="markdown"
                    )
                except Exception:
                    pass
            await asyncio.sleep(0.02)

    except Exception as e:
        await prog.edit(f"❌ Scan error: `{e}`", parse_mode="markdown")
        return

    eta_min = (_queue.qsize() * PM_DELAY) / 60
    await prog.edit(
        f"✅ **Dump scan complete**\n\n"
        f"📜 Messages scanned: `{scanned:,}`\n"
        f"📨 Bot results found: `{found_msgs}`\n"
        f"🔗 New links queued: `{queued}`\n"
        f"⏳ Queue size: `{_queue.qsize()}` — ETA ~`{eta_min:.0f}` min\n\n"
        f"Files will forward to `{DEST_CHANNEL}` as the queue processes.\n"
        f"Already-seen files will be skipped automatically.",
        parse_mode="markdown"
    )
    if LOG_CHANNEL:
        try:
            await client.send_message(LOG_CHANNEL, f"🗂 Dump queued {queued} links from {group}")
        except Exception:
            pass


# ── /pmstatus ─────────────────────────────────────────────────────────────────
@app.on_message(filters.command("pmstatus") & filters.private)
@_admin_only
async def cmd_pmstatus(client: Client, message: Message):
    eta_min = (_queue.qsize() * PM_DELAY) / 60
    dedup_info = "seen_db (persistent ✅)" if _USE_SEEN_DB else "in-memory ⚠️"
    await message.reply(
        f"**📊 PM Bot Forwarder Status**\n\n"
        f"🤖 Source bot: @{SOURCE_BOT}\n"
        f"👁 Watching groups: `{len(SOURCE_GROUPS)}`\n"
        f"📺 Destination: `{DEST_CHANNEL}`\n"
        f"🔁 Deduplication: {dedup_info}\n\n"
        f"🔗 Start params processed: `{len(_processed):,}`\n"
        f"✅ Files forwarded: `{_stats['forwarded']:,}`\n"
        f"⏭ Duplicates skipped: `{_stats['skipped_dup']:,}`\n"
        f"⏳ Queue size: `{_queue.qsize():,}` (~`{eta_min:.0f}` min remaining)\n"
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
            f"⚠️ This clears `{len(_processed):,}` processed-link records from `{_PROCESSED_FILE}`.\n"
            f"Previously seen deep links will be re-sent to @{SOURCE_BOT}.\n"
            f"Note: file deduplication (seen_db) is NOT cleared — no duplicate files will be forwarded.\n\n"
            f"Send `/pmclear confirm` to proceed.",
            parse_mode="markdown"
        )
        return
    with _proc_lock:
        n = len(_processed)
        _processed.clear()
        _save_processed(_processed)
    await message.reply(
        f"🗑 Cleared `{n:,}` processed-link records.\n"
        f"File dedup (seen_db) kept intact — no duplicates will be forwarded.",
        parse_mode="markdown"
    )


# ── /help ─────────────────────────────────────────────────────────────────────
@app.on_message(filters.command(["start", "help"]) & filters.private)
async def cmd_help(client: Client, message: Message):
    me = await client.get_me()
    await message.reply(
        f"**PM Bot Forwarder** — `{me.first_name}`\n\n"
        f"Watches for @{SOURCE_BOT} results and forwards all files to `{DEST_CHANNEL}`.\n"
        f"Duplicate files are permanently skipped (survives restarts).\n\n"
        "**Commands:**\n"
        "• `/dumpbot <group> [limit]` — scan history and queue all past deep links\n"
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
    logger.info(f"📺 Destination channel: {DEST_CHANNEL}")
    logger.info(f"👁 Watching {len(SOURCE_GROUPS)} source group(s): {SOURCE_GROUPS}")
    logger.info(f"⏱ PM delay: {PM_DELAY}s between requests")
    logger.info(f"🔁 Dedup mode: {'seen_db (persistent)' if _USE_SEEN_DB else 'in-memory'}")

    asyncio.create_task(_queue_worker())

    if LOG_CHANNEL:
        try:
            await app.send_message(
                LOG_CHANNEL,
                f"✅ **PM Bot Forwarder started**\nAs: `{me.first_name}`\n"
                f"Source: @{SOURCE_BOT} | Groups: {len(SOURCE_GROUPS)}\n"
                f"Dedup: {'seen_db ✅' if _USE_SEEN_DB else 'in-memory ⚠️'}"
            )
        except Exception:
            pass

    logger.info("⏳ Listening...")
    await idle()
    await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
