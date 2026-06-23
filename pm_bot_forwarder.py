"""
PM Bot Forwarder — pm_bot_forwarder.py

Watches source groups for auto-filter bot result messages (e.g. NarutoXMoviesBot).
When the bot posts deep-link results in the group, this userbot:
  1. Extracts the `start=files_XXXXX` deep links from every result message
  2. Sends /start <param> to the bot in PM (via a rate-limited queue)
  3. Captures every file/video the bot sends back in PM
  4. Forwards each file immediately to DEST_CHANNEL

Commands (DM the userbot):
  /dumpbot <group> [limit]  — scan group history and queue all past deep links
  /pmstatus                 — show queue size, processed/forwarded counts
  /pmclear                  — clear the processed-links cache (re-process all)
  /help                     — this message

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
"""

import asyncio
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

_raw_groups   = [g.strip() for g in os.environ.get("SOURCE_GROUPS", "").split(",") if g.strip()]
SOURCE_GROUPS = []
for g in _raw_groups:
    try:
        SOURCE_GROUPS.append(int(g))
    except ValueError:
        SOURCE_GROUPS.append(g)

# ── State ─────────────────────────────────────────────────────────────────────
_processed: set[str] = set()   # start params already sent to bot
_forwarded: set[str] = set()   # file_unique_ids already forwarded
_queue: asyncio.Queue          # populated at runtime
_stats = {"queued": 0, "forwarded": 0, "errors": 0}

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


async def _enqueue(bot_username: str, start_param: str):
    if start_param in _processed:
        return
    _processed.add(start_param)
    await _queue.put((bot_username, start_param))
    _stats["queued"] += 1
    logger.info(f"Queued: @{bot_username} start={start_param} (total={_stats['queued']})")


# ── Watch source groups for bot result messages ───────────────────────────────
@app.on_message(filters.incoming)
async def on_any_message(client: Client, message: Message):
    # Only care about messages in our watched groups
    if SOURCE_GROUPS and message.chat.id not in SOURCE_GROUPS:
        if not any(
            getattr(message.chat, "username", "").lower() == str(g).lstrip("@").lower()
            for g in SOURCE_GROUPS if not str(g).lstrip("-").isdigit()
        ):
            return

    # Only process messages from the target bot
    sender = message.from_user or message.sender_chat
    username = getattr(sender, "username", "") or ""
    if username.lower() != SOURCE_BOT.lower():
        return

    links = extract_deeplinks(message)
    if not links:
        return

    logger.info(f"Bot result in {message.chat.id}: {len(links)} deep link(s) found")
    for bot_username, start_param in links:
        await _enqueue(bot_username, start_param)


# ── Watch PM for files from the source bot ────────────────────────────────────
@app.on_message(filters.private & filters.incoming)
async def on_pm_file(client: Client, message: Message):
    sender = message.from_user or message.sender_chat
    username = getattr(sender, "username", "") or ""
    if username.lower() != SOURCE_BOT.lower():
        return

    media = message.document or message.video or message.audio or message.photo
    if not media:
        return

    unique_id = getattr(media, "file_unique_id", None)
    if unique_id and unique_id in _forwarded:
        return
    if unique_id:
        _forwarded.add(unique_id)

    try:
        await message.forward(DEST_CHANNEL)
        _stats["forwarded"] += 1
        logger.info(f"✅ Forwarded file from @{SOURCE_BOT} PM → {DEST_CHANNEL} (total={_stats['forwarded']})")
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
        await asyncio.sleep(PM_DELAY)


# ── /dumpbot ──────────────────────────────────────────────────────────────────
@app.on_message(filters.command("dumpbot") & filters.private)
@_admin_only
async def cmd_dumpbot(client: Client, message: Message):
    args = message.text.split(None, 2)
    if len(args) < 2:
        await message.reply(
            "**Usage:** `/dumpbot <group_id or @username> [limit]`\n\n"
            "Scans the group's entire history for @" + SOURCE_BOT + " result messages\n"
            "and queues every deep link found.\n\n"
            "Default limit: `10000` messages",
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
            username = getattr(sender, "username", "") or ""
            if username.lower() == SOURCE_BOT.lower():
                found_msgs += 1
                for bot_username, start_param in extract_deeplinks(msg):
                    if start_param not in _processed:
                        await _enqueue(bot_username, start_param)
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
        f"Files will forward to `{DEST_CHANNEL}` as the queue processes.",
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
    await message.reply(
        f"**📊 PM Bot Forwarder Status**\n\n"
        f"🤖 Source bot: @{SOURCE_BOT}\n"
        f"👁 Watching groups: `{len(SOURCE_GROUPS)}`\n"
        f"📺 Destination: `{DEST_CHANNEL}`\n\n"
        f"🔗 Links processed: `{len(_processed):,}`\n"
        f"✅ Files forwarded: `{_stats['forwarded']:,}`\n"
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
            f"⚠️ This clears `{len(_processed):,}` processed-link records.\n"
            f"Any previously seen deep links will be re-processed.\n\n"
            f"Send `/pmclear confirm` to proceed.",
            parse_mode="markdown"
        )
        return
    n = len(_processed)
    _processed.clear()
    await message.reply(f"🗑 Cleared `{n:,}` processed-link records. Cache is fresh.", parse_mode="markdown")


# ── /help ─────────────────────────────────────────────────────────────────────
@app.on_message(filters.command(["start", "help"]) & filters.private)
async def cmd_help(client: Client, message: Message):
    me = await client.get_me()
    await message.reply(
        f"**PM Bot Forwarder** — `{me.first_name}`\n\n"
        f"Watches groups for @{SOURCE_BOT} results and forwards all files to `{DEST_CHANNEL}`.\n\n"
        "**Commands:**\n"
        "• `/dumpbot <group> [limit]` — scan history and queue all past deep links\n"
        "• `/pmstatus` — show queue, processed, forwarded counts\n"
        "• `/pmclear confirm` — reset the processed-links cache\n",
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

    asyncio.create_task(_queue_worker())

    if LOG_CHANNEL:
        try:
            await app.send_message(
                LOG_CHANNEL,
                f"✅ **PM Bot Forwarder started**\nAs: `{me.first_name}`\n"
                f"Watching @{SOURCE_BOT} in {len(SOURCE_GROUPS)} group(s)"
            )
        except Exception:
            pass

    logger.info("⏳ Listening...")
    await idle()
    await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
