"""
PM Bot Forwarder — pm_bot_forwarder.py

Watches source groups for auto-filter bot result messages.
Supports watching multiple bots simultaneously (e.g. NarutoXMoviesBot, HDMoviesBot).

When any watched bot posts deep-link results in a source group, this userbot:
  1. Extracts every `start=files_XXXXX` deep link from the result message
  2. Sends /start <param> to the correct bot in PM via a rate-limited queue
  3. Captures every file/video the bot sends back in PM
  4. Forwards each file immediately to DEST_CHANNEL — permanently skipping duplicates

Deduplication (both layers survive restarts):
  • file_unique_id  → seen_db (seen.json)           never re-forward the same file
  • bot:start_param → pm_processed.json             never re-send the same deep link

Commands (DM the userbot, admin only):
  /dumpbot <group> [limit]  scan group history, queue all past deep links from all watched bots
  /pmstatus                 queue size, forwarded/skipped counts, per-bot breakdown
  /pmclear confirm          reset processed-links cache (file dedup untouched)
  /listbots                 list all currently watched bots
  /help                     this message

Environment variables:
  SESSION_STRING      Pyrogram session string (required)
  API_ID              Telegram API ID (required)
  API_HASH            Telegram API hash (required)
  SOURCE_BOTS         Comma-separated bot usernames to watch, e.g. NarutoXMoviesBot,HDMoviesBot
                      Falls back to SOURCE_BOT if not set (backward compatible)
  SOURCE_BOT          Single bot username — used if SOURCE_BOTS is not set
  SOURCE_GROUPS       Comma-separated group IDs/usernames to watch (empty = all groups)
  DEST_CHANNEL        Channel ID to forward files into (required)
  ADMIN_IDS           Comma-separated admin Telegram user IDs
  PM_DELAY            Seconds between PM start commands (default: 4)
  LOG_CHANNEL         Channel to log startup/activity (optional)
  PM_PROCESSED_FILE   Path to processed-links JSON (default: pm_processed.json)

  Caption watermark replacement (optional):
  MY_USERNAME         Your username WITHOUT @ — every @mention in captions is replaced
                      e.g. MY_USERNAME=Moviebot123  →  @NarutoXMoviesBot → @Moviebot123
  MY_CHANNEL_URL      Your channel link — every t.me/... URL in captions is replaced
                      e.g. MY_CHANNEL_URL=https://t.me/backupchannek
"""

import asyncio
import json
import logging
import os
import re
from functools import wraps

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
API_ID       = int(os.environ["API_ID"])
API_HASH     = os.environ["API_HASH"]
SESSION_STR  = os.environ["SESSION_STRING"]
DEST_CHANNEL = int(os.environ["DEST_CHANNEL"])
LOG_CHANNEL  = int(os.environ["LOG_CHANNEL"]) if os.environ.get("LOG_CHANNEL") else None
PM_DELAY     = float(os.environ.get("PM_DELAY", "4"))
ADMIN_IDS    = [
    int(x) for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
]
_PROCESSED_FILE = os.environ.get("PM_PROCESSED_FILE", "pm_processed.json")

# ── Caption watermark replacement ─────────────────────────────────────────────
# Set MY_USERNAME and/or MY_CHANNEL_URL to replace bot watermarks with your own.
# If neither is set, files are forwarded as-is (original caption preserved).
MY_USERNAME    = os.environ.get("MY_USERNAME", "").lstrip("@").strip()
MY_CHANNEL_URL = os.environ.get("MY_CHANNEL_URL", "").strip()

# Matches any @username (Telegram allows a-z, 0-9, underscore, min 5 chars,
# but we match all @word tokens to be safe — bots often use short names too)
_RE_USERNAME = re.compile(r'@[\w]{1,}')
# Full https://t.me/... links (must come BEFORE bare t.me to avoid double-replace)
_RE_TG_HTTPS = re.compile(r'https?://t(?:elegram)?\.me/[\w@/?=&]+', re.IGNORECASE)
# Bare t.me/... links not preceded by // (so we don't double-match the above)
_RE_TG_BARE  = re.compile(r'(?<![/])t\.me/[\w@/?=&]+', re.IGNORECASE)


def _clean_caption(caption: str | None) -> str | None:
    """
    Replace every @username and every t.me/... link in a caption with the
    owner's details (MY_USERNAME / MY_CHANNEL_URL).

    Returns the cleaned string, or the original if nothing needs changing.
    Returns None if caption was None.
    """
    if not caption:
        return caption

    result = caption

    # 1. Replace full https://t.me/... links first (most specific pattern)
    if MY_CHANNEL_URL:
        result = _RE_TG_HTTPS.sub(MY_CHANNEL_URL, result)
        result = _RE_TG_BARE.sub(MY_CHANNEL_URL, result)

    # 2. Replace all @mentions — skip if replacement equals the mention itself
    if MY_USERNAME:
        result = _RE_USERNAME.sub(f"@{MY_USERNAME}", result)

    return result


# ── Multi-bot config (SOURCE_BOTS, falls back to SOURCE_BOT) ─────────────────
# SOURCE_BOTS = "NarutoXMoviesBot,HDMoviesBot,CineAllianceBot"
# or SOURCE_BOT = "NarutoXMoviesBot"  (legacy single-bot mode)
_bots_env = (
    os.environ.get("SOURCE_BOTS")          # new multi-bot env var
    or os.environ.get("SOURCE_BOT", "NarutoXMoviesBot")   # legacy fallback
)
SOURCE_BOTS_LIST: list[str] = [
    b.strip().lstrip("@") for b in _bots_env.split(",") if b.strip()
]
_SOURCE_BOT_SET: set[str] = {b.lower() for b in SOURCE_BOTS_LIST}   # O(1) lookup

# ── Source group config ───────────────────────────────────────────────────────
# Parse SOURCE_GROUPS into a mixed list of int IDs and str usernames
_raw_groups   = [g.strip() for g in os.environ.get("SOURCE_GROUPS", "").split(",") if g.strip()]
SOURCE_GROUPS: list = []
for _g in _raw_groups:
    try:
        SOURCE_GROUPS.append(int(_g))
    except ValueError:
        SOURCE_GROUPS.append(_g.lstrip("@").lower())

# ── Dedup key helpers ─────────────────────────────────────────────────────────
# Key format: "botname_lower:start_param"
# Namespaces each bot separately — prevents false dedup when different bots
# happen to share the same start_param value (unlikely but possible).
def _proc_key(bot_username: str, start_param: str) -> str:
    return f"{bot_username.lower()}:{start_param}"


# ── Persistent: processed start params ───────────────────────────────────────
_proc_lock    = asyncio.Lock()  # async-safe; held only briefly for in-memory ops
_SAVE_BATCH   = 50              # flush pm_processed.json every N new items
_unsaved_count = 0              # items added since last flush


def _load_processed() -> set:
    if os.path.exists(_PROCESSED_FILE):
        try:
            with open(_PROCESSED_FILE) as f:
                data = json.load(f)
            return set(data) if isinstance(data, list) else set()
        except Exception:
            pass
    return set()


def _flush_processed(snapshot: set):
    """Write the processed set to disk. Called in a thread executor — never blocks event loop."""
    try:
        with open(_PROCESSED_FILE, "w") as f:
            json.dump(sorted(snapshot), f)
    except Exception as e:
        logger.warning(f"pm_processed flush error: {e}")


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
_queue: asyncio.Queue | None = None   # set in main() after event loop starts

# Per-bot counters for /pmstatus breakdown
_stats: dict = {
    "queued":      0,
    "forwarded":   0,
    "skipped_dup": 0,
    "errors":      0,
    "by_bot":      {b: {"queued": 0, "forwarded": 0} for b in SOURCE_BOTS_LIST},
}

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
    """Decorator: only allow ADMIN_IDS to use a command. Uses functools.wraps."""
    @wraps(func)
    async def wrapper(client: Client, message: Message):
        user = message.from_user
        if not user:
            return                              # anonymous group admin / channel post
        if ADMIN_IDS and user.id not in ADMIN_IDS:
            return                              # not in admin list
        return await func(client, message)
    return wrapper


def _is_source_group(message: Message) -> bool:
    """Return True if this message's chat is in the watched SOURCE_GROUPS list.
    If SOURCE_GROUPS is empty, watch all groups."""
    if not SOURCE_GROUPS:
        return True
    cid   = message.chat.id
    uname = (getattr(message.chat, "username", "") or "").lower()
    for g in SOURCE_GROUPS:
        if isinstance(g, int)  and g == cid:   return True
        if isinstance(g, str)  and g == uname: return True
    return False


def extract_deeplinks(message: Message) -> list[tuple[str, str]]:
    """Return list of (bot_username, start_param) tuples found in a message.
    Searches inline keyboard button URLs, message text, and caption."""
    links: list[tuple[str, str]] = []

    # Inline keyboard buttons
    if message.reply_markup and hasattr(message.reply_markup, "inline_keyboard"):
        for row in message.reply_markup.inline_keyboard:
            for btn in row:
                url = getattr(btn, "url", None) or ""
                for m in DEEPLINK_RE.finditer(url):
                    links.append((m.group(1), m.group(2)))

    # Text and caption
    for text in (message.text or "", message.caption or ""):
        for m in DEEPLINK_RE.finditer(text):
            links.append((m.group(1), m.group(2)))

    return links


async def _enqueue(bot_username: str, start_param: str, force_save: bool = False) -> bool:
    """
    Queue one (bot, start_param) pair for PM delivery.
    Returns True if newly queued, False if already processed.

    Dedup key = "botname:start_param" so different bots never collide.
    Disk writes are batched every _SAVE_BATCH items to avoid blocking the event loop.
    """
    global _unsaved_count
    key = _proc_key(bot_username, start_param)

    should_save = False
    snapshot: set = set()

    async with _proc_lock:
        if key in _processed:
            return False
        _processed.add(key)
        _unsaved_count += 1
        should_save = force_save or (_unsaved_count >= _SAVE_BATCH)
        if should_save:
            snapshot = set(_processed)   # consistent snapshot inside the lock
            _unsaved_count = 0

    # Disk I/O runs in a thread executor — never blocks the event loop
    if should_save:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _flush_processed, snapshot)

    if _queue is not None:
        await _queue.put((bot_username, start_param))

    _stats["queued"] += 1
    bot_key = bot_username.lower()
    if bot_key not in _stats["by_bot"]:
        _stats["by_bot"][bot_key] = {"queued": 0, "forwarded": 0}
    _stats["by_bot"][bot_key]["queued"] += 1

    logger.info(f"Queued @{bot_username} start={start_param} (total={_stats['queued']})")
    return True


# ── Watch source groups for bot result messages ───────────────────────────────
@app.on_message(filters.group & filters.incoming)
async def on_group_message(client: Client, message: Message):
    """Fires only in group/supergroup chats — never in private chats."""
    if not _is_source_group(message):
        return

    sender   = message.from_user or message.sender_chat
    username = (getattr(sender, "username", "") or "").lower()

    # Check against all watched bots (O(1) set lookup)
    if username not in _SOURCE_BOT_SET:
        return

    links = extract_deeplinks(message)
    if not links:
        return

    new_count = 0
    for bot_username, start_param in links:
        if await _enqueue(bot_username, start_param):
            new_count += 1

    if new_count:
        logger.info(
            f"Group {message.chat.id}: {new_count} new link(s) from @{username} queued"
        )


# ── Watch PM for files from any watched bot ───────────────────────────────────
@app.on_message(filters.private & filters.incoming)
async def on_pm_file(client: Client, message: Message):
    """Captures every file any watched bot sends in PM and forwards it to DEST_CHANNEL."""
    sender   = message.from_user or message.sender_chat
    username = (getattr(sender, "username", "") or "").lower()

    # Only process messages from bots we actually sent /start to
    if username not in _SOURCE_BOT_SET:
        return

    media = message.document or message.video or message.audio or message.photo
    if not media:
        return

    unique_id = getattr(media, "file_unique_id", None)
    if not unique_id:
        return

    # Permanent dedup — survives restarts
    if _is_seen(unique_id):
        _stats["skipped_dup"] += 1
        logger.info(f"⏭ Duplicate skipped: {unique_id} (total={_stats['skipped_dup']})")
        return

    # Mark BEFORE forwarding — prevents re-forward if another /start arrives
    # for the same file while the forward call is in-flight
    _mark_seen(unique_id)

    try:
        cleaned = _clean_caption(message.caption)
        caption_changed = cleaned != message.caption

        if caption_changed or MY_USERNAME or MY_CHANNEL_URL:
            # Use copy() so we can inject the cleaned caption.
            # copy() sends the file fresh without a "Forwarded from" header.
            await message.copy(DEST_CHANNEL, caption=cleaned)
            if caption_changed:
                logger.debug(f"Caption cleaned: {message.caption!r} → {cleaned!r}")
        else:
            # No replacement configured — plain forward (preserves original formatting)
            await message.forward(DEST_CHANNEL)

        _stats["forwarded"] += 1
        bot_key = username
        if bot_key not in _stats["by_bot"]:
            _stats["by_bot"][bot_key] = {"queued": 0, "forwarded": 0}
        _stats["by_bot"][bot_key]["forwarded"] += 1
        logger.info(f"✅ Forwarded → {DEST_CHANNEL} from @{username} (total={_stats['forwarded']})")
    except Exception as e:
        _stats["errors"] += 1
        logger.error(f"Forward error from @{username}: {e}")


# ── Queue worker: sends /start <param> to the correct bot in PM ───────────────
async def _queue_worker():
    logger.info("Queue worker started")
    while True:
        bot_username, start_param = await _queue.get()
        try:
            await app.send_message(bot_username, f"/start {start_param}")
            logger.info(f"Sent /start {start_param} to @{bot_username}")
        except Exception as e:
            logger.error(f"PM send error (@{bot_username} start={start_param}): {e}")
            _stats["errors"] += 1
        finally:
            _queue.task_done()
        await asyncio.sleep(PM_DELAY)    # rate-limit between /start sends


# ── /dumpbot ──────────────────────────────────────────────────────────────────
@app.on_message(filters.command("dumpbot") & filters.private)
@_admin_only
async def cmd_dumpbot(client: Client, message: Message):
    args = message.text.split(None, 2)
    bots_display = " + ".join(f"@{b}" for b in SOURCE_BOTS_LIST)

    if len(args) < 2:
        await message.reply(
            "**Usage:** `/dumpbot <group_id or @username> [limit]`\n\n"
            f"Scans group history for result messages from:\n{bots_display}\n"
            "and queues every unseen deep link.\n\n"
            "Default limit: `10000` messages\n"
            "Example: `/dumpbot -1001234567890 50000`",
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
        f"🔍 Scanning `{group}` for results from:\n{bots_display}\n"
        f"Limit: `{limit:,}` messages — this may take a while.",
        parse_mode="markdown"
    )

    scanned = found_msgs = queued = 0
    try:
        async for msg in client.get_chat_history(group_id, limit=limit):
            scanned += 1
            sender   = msg.from_user or msg.sender_chat
            username = (getattr(sender, "username", "") or "").lower()

            # Match any of the watched bots
            if username in _SOURCE_BOT_SET:
                found_msgs += 1
                for bot_username, start_param in extract_deeplinks(msg):
                    if await _enqueue(bot_username, start_param):
                        queued += 1

            # Progress update every 500 messages
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

            await asyncio.sleep(0.02)   # gentle rate limit on history fetch

    except Exception as e:
        await prog.edit(f"❌ Scan error: `{e}`", parse_mode="markdown")
        return
    finally:
        # Force-flush any remaining unsaved params regardless of success/error
        if _unsaved_count > 0:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _flush_processed, set(_processed))

    q_size  = _queue.qsize() if _queue else 0
    eta_min = (q_size * PM_DELAY) / 60
    await prog.edit(
        f"✅ **Dump scan complete**\n\n"
        f"📜 Messages scanned: `{scanned:,}`\n"
        f"📨 Bot results found: `{found_msgs}`\n"
        f"🔗 New links queued: `{queued}`\n"
        f"⏳ Queue size: `{q_size}` — ETA ~`{eta_min:.0f}` min\n\n"
        f"Files will forward to `{DEST_CHANNEL}` as the queue processes.\n"
        f"Already-seen files are skipped permanently.",
        parse_mode="markdown"
    )
    if LOG_CHANNEL:
        try:
            await client.send_message(
                LOG_CHANNEL,
                f"🗂 Dump: queued {queued} links from {group} (scanned {scanned:,})"
            )
        except Exception:
            pass


# ── /pmstatus ─────────────────────────────────────────────────────────────────
@app.on_message(filters.command("pmstatus") & filters.private)
@_admin_only
async def cmd_pmstatus(client: Client, message: Message):
    q_size   = _queue.qsize() if _queue else 0
    eta_min  = (q_size * PM_DELAY) / 60
    dedup    = "seen_db (persistent ✅)" if _USE_SEEN_DB else "in-memory ⚠️"
    bots_str = "\n".join(
        f"  • @{b}: {_stats['by_bot'].get(b.lower(), {}).get('queued', 0)} queued, "
        f"{_stats['by_bot'].get(b.lower(), {}).get('forwarded', 0)} forwarded"
        for b in SOURCE_BOTS_LIST
    )
    caption_parts = []
    if MY_USERNAME:
        caption_parts.append(f"@mentions → @{MY_USERNAME}")
    if MY_CHANNEL_URL:
        caption_parts.append(f"links → {MY_CHANNEL_URL}")
    caption_status = " | ".join(caption_parts) if caption_parts else "off (forwarding original captions)"
    await message.reply(
        f"**📊 PM Bot Forwarder Status**\n\n"
        f"🤖 Bots watched ({len(SOURCE_BOTS_LIST)}):\n{bots_str}\n\n"
        f"👁 Groups: `{len(SOURCE_GROUPS)}` configured (0 = watch all)\n"
        f"📺 Destination: `{DEST_CHANNEL}`\n"
        f"🔁 File dedup: {dedup}\n"
        f"✏️ Caption replace: {caption_status}\n\n"
        f"🔗 Start params processed: `{len(_processed):,}`\n"
        f"✅ Files forwarded: `{_stats['forwarded']:,}`\n"
        f"⏭ Duplicates skipped: `{_stats['skipped_dup']:,}`\n"
        f"⏳ Queue size: `{q_size:,}` (~`{eta_min:.0f}` min remaining)\n"
        f"❌ Errors: `{_stats['errors']}`",
        parse_mode="markdown"
    )


# ── /listbots ─────────────────────────────────────────────────────────────────
@app.on_message(filters.command("listbots") & filters.private)
@_admin_only
async def cmd_listbots(client: Client, message: Message):
    lines = [f"{i+1}. @{b}" for i, b in enumerate(SOURCE_BOTS_LIST)]
    await message.reply(
        f"**🤖 Watched Bots ({len(SOURCE_BOTS_LIST)})**\n\n"
        + "\n".join(lines)
        + "\n\nTo change, update the `SOURCE_BOTS` env var and redeploy.",
        parse_mode="markdown"
    )


# ── /pmclear ──────────────────────────────────────────────────────────────────
@app.on_message(filters.command("pmclear") & filters.private)
@_admin_only
async def cmd_pmclear(client: Client, message: Message):
    args = message.text.split(None, 1)
    bots_display = ", ".join(f"@{b}" for b in SOURCE_BOTS_LIST)
    if len(args) < 2 or args[1].strip().lower() != "confirm":
        await message.reply(
            f"⚠️ This clears `{len(_processed):,}` processed-link records.\n"
            f"Deep links will be re-sent to: {bots_display}\n"
            f"**File dedup (seen_db) is NOT cleared** — no duplicate files forwarded.\n\n"
            f"Send `/pmclear confirm` to proceed.",
            parse_mode="markdown"
        )
        return
    async with _proc_lock:
        n = len(_processed)
        _processed.clear()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _flush_processed, set())
    await message.reply(
        f"🗑 Cleared `{n:,}` processed-link records.\n"
        f"File dedup (seen_db) intact — no duplicate files will be forwarded.",
        parse_mode="markdown"
    )


# ── /help ─────────────────────────────────────────────────────────────────────
@app.on_message(filters.command(["start", "help"]) & filters.private)
async def cmd_help(client: Client, message: Message):
    me = await client.get_me()
    bots_display = ", ".join(f"@{b}" for b in SOURCE_BOTS_LIST)
    await message.reply(
        f"**PM Bot Forwarder** — `{me.first_name}`\n\n"
        f"Watching: {bots_display}\n"
        f"Forwarding to: `{DEST_CHANNEL}`\n"
        f"Duplicates: permanently skipped (survives restarts)\n\n"
        "**Commands (admin only):**\n"
        "• `/dumpbot <group> [limit]` — scan history, queue all past deep links\n"
        "• `/pmstatus` — queue, forwarded, skipped, per-bot breakdown\n"
        "• `/listbots` — show all watched bots\n"
        "• `/pmclear confirm` — reset processed-links cache (keeps file dedup)\n",
        parse_mode="markdown"
    )


# ── Startup ───────────────────────────────────────────────────────────────────
async def main():
    global _queue
    _queue = asyncio.Queue()

    await app.start()
    me = await app.get_me()

    bots_display = ", ".join(f"@{b}" for b in SOURCE_BOTS_LIST)
    logger.info(f"🚀 PM Bot Forwarder started as: {me.first_name} (@{me.username})")
    logger.info(f"🤖 Watching bots: {bots_display}")
    logger.info(f"📺 Destination: {DEST_CHANNEL}")
    logger.info(f"👁 Source groups: {SOURCE_GROUPS or 'ALL'}")
    logger.info(f"⏱ PM delay: {PM_DELAY}s | Dedup: {'seen_db' if _USE_SEEN_DB else 'in-memory'}")

    asyncio.create_task(_queue_worker())

    if LOG_CHANNEL:
        try:
            await app.send_message(
                LOG_CHANNEL,
                f"✅ **PM Bot Forwarder started**\n"
                f"As: `{me.first_name}` | Bots: {bots_display}\n"
                f"Groups: {len(SOURCE_GROUPS)} | "
                f"Dedup: {'seen_db ✅' if _USE_SEEN_DB else 'in-memory ⚠️'}"
            )
        except Exception:
            pass

    logger.info("⏳ Listening for bot results...")
    await idle()

    # Graceful shutdown — flush remaining unsaved params
    if _unsaved_count > 0:
        logger.info("Flushing processed params on shutdown...")
        _flush_processed(_processed)

    await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
