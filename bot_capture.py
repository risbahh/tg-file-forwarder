"""
Bot Capture Mode
────────────────
Watches specific groups and captures ONLY files sent by a registered bot
(e.g. the Cine Alliance auto-filter bot). Skips all user messages, spam,
random uploads — only clean, verified movie files get forwarded.

Difference from forwarder.py:
  forwarder.py   → captures ALL files from watched groups (anyone can send)
  bot_capture.py → captures ONLY files from a specific bot in each group

How it works:
  1. You join the group (e.g. Cine Alliance) as a member
  2. You register: /setbot CineAlliance CineAllianceBotUsername
  3. A user in that group types a movie name → their bot sends the file
  4. bot_capture.py sees the file is FROM the registered bot → forwards it
  5. Your index channel gets the clean file → your auto-filter bot indexes it

Run:    python bot_capture.py
Deploy: Add to Procfile as a second worker, or run alongside forwarder.py

Commands (DM the userbot — admins only):
  /setbot <group> <bot_username>    Register which bot to watch in a group
  /removebot <group>                Stop targeted capture for a group
  /listbots                         Show all registered bot-group pairs
  /capturestatus                    Live stats for this session
  /help                             Show all commands
"""

import asyncio
import logging
import os
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import ChannelPrivate, UserNotParticipant, PeerIdInvalid

from config import (
    API_ID, API_HASH, SESSION_STRING,
    DEST_CHANNEL, ALLOWED_TYPES, LOG_CHANNEL
)
from utils import safe_forward, is_allowed_file, get_file_name, get_file_size, human_size
from bots_db import set_bot, remove_bot, get_bot_by_chat_id, list_all, format_list

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("bot_capture")

# ── Admin list ─────────────────────────────────────────────────────────────
ADMINS = [
    int(x.strip()) for x in os.environ.get("ADMINS", "").split(",")
    if x.strip().isdigit()
]

app = Client(
    "bot_capture_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
)

# ── Session stats ──────────────────────────────────────────────────────────
_stats = {
    "captured":   0,   # files forwarded (from registered bot)
    "skipped":    0,   # messages ignored (not from target bot)
    "failed":     0,   # forward failures
}

# ── Admin guard ────────────────────────────────────────────────────────────
def admin_only(func):
    async def wrapper(client: Client, message: Message):
        if ADMINS and message.from_user and message.from_user.id not in ADMINS:
            await message.reply("⛔ You are not authorized to use this command.")
            return
        await func(client, message)
    wrapper.__name__ = func.__name__
    return wrapper

# ── Core file capture handler ──────────────────────────────────────────────
@app.on_message(filters.document | filters.video | filters.audio)
async def on_file_message(client: Client, message: Message):
    """
    Fires on every file message across all groups the userbot is in.
    
    Decision logic:
    1. Is this group registered in bots.json?
       YES → only forward if sender is the registered bot
       NO  → skip (bot_capture.py ignores unregistered groups)
    """
    chat_id = message.chat.id

    # Check if this group has a registered target bot
    entry = get_bot_by_chat_id(chat_id)

    # Also check by username
    if entry is None:
        chat_username = getattr(message.chat, "username", None)
        if chat_username:
            from bots_db import get_bot
            entry = get_bot(chat_username)

    if entry is None:
        # Group not registered in bot_capture — ignore it
        # (forwarder.py handles unregistered groups if running in parallel)
        return

    # Group IS registered — check if the sender is the target bot
    sender = message.from_user
    if sender is None:
        # Anonymous channel post inside a group — not our target bot
        _stats["skipped"] += 1
        return

    target_bot_id = entry.get("bot_id")
    if sender.id != target_bot_id:
        # Not from the target bot — skip (user upload, admin post, etc.)
        _stats["skipped"] += 1
        return

    # ✅ This file is from the registered bot — capture it
    if not is_allowed_file(message, ALLOWED_TYPES):
        _stats["skipped"] += 1
        return

    name  = get_file_name(message)
    size  = human_size(get_file_size(message))
    chat  = getattr(message.chat, "title", str(chat_id))
    bot_u = entry.get("bot_username", "?")

    logger.info(f"🎯 [{chat}] Bot @{bot_u} sent: {name} ({size})")

    success = await safe_forward(message, DEST_CHANNEL)
    if success:
        _stats["captured"] += 1
        logger.info(f"✅ Captured → {DEST_CHANNEL}  |  total: {_stats['captured']}")
    else:
        _stats["failed"] += 1
        logger.error(f"❌ Failed to capture: {name}")

# ── /setbot ────────────────────────────────────────────────────────────────
@app.on_message(filters.command("setbot") & filters.private)
@admin_only
async def cmd_setbot(client: Client, message: Message):
    """
    /setbot <group_username_or_id> <bot_username>
    
    Finds the bot's Telegram user ID automatically.
    The bot must be a member of the group for this to work.
    """
    args = message.text.split(None, 2)
    if len(args) < 3:
        await message.reply(
            "**Usage:** `/setbot <group> <bot_username>`\n\n"
            "**Examples:**\n"
            "• `/setbot CineAlliance CineAllianceBot`\n"
            "• `/setbot -100987654321 MovieBotUsername`\n\n"
            "**How to find the bot username:**\n"
            "1. Go to the group\n"
            "2. Find any message sent by the bot\n"
            "3. Click the bot's name → copy username",
            parse_mode="markdown"
        )
        return

    raw_group  = args[1].strip()
    raw_bot    = args[2].strip().lstrip("@")
    status_msg = await message.reply(f"🔍 Looking up @{raw_bot}...")

    # Resolve bot user ID from username
    try:
        bot_user = await client.get_users(raw_bot)
        bot_id   = bot_user.id
        is_bot   = bot_user.is_bot
    except PeerIdInvalid:
        await status_msg.edit(f"❌ Cannot find user @{raw_bot}\n\nMake sure the username is correct (no @ needed).")
        return
    except Exception as e:
        await status_msg.edit(f"❌ Error resolving @{raw_bot}: `{e}`")
        return

    if not is_bot:
        await status_msg.edit(
            f"⚠️ @{raw_bot} (ID: `{bot_id}`) is **not a bot account**.\n\n"
            f"Are you sure this is the right username? Reply `/setbot {raw_group} {raw_bot}` again to confirm."
        )
        # Still register it — some auto-filter bots show as regular users
        # We don't force-block non-bot accounts

    # Verify the group is accessible
    try:
        chat_obj   = await client.get_chat(raw_group)
        group_name = chat_obj.title
        group_id   = chat_obj.id
    except Exception as e:
        await status_msg.edit(
            f"⚠️ Registered @{raw_bot} for `{raw_group}` but couldn't verify group: `{e}`\n"
            f"Make sure the userbot is a member of that group."
        )
        set_bot(raw_group, bot_id, raw_bot)
        return

    # Save the mapping
    confirm_msg = set_bot(group_id, bot_id, raw_bot, label=group_name)
    # Also save by username for easier lookup
    set_bot(raw_group, bot_id, raw_bot, label=group_name)

    await status_msg.edit(
        f"{confirm_msg}\n\n"
        f"**Group:** {group_name} (`{group_id}`)\n"
        f"**Bot:** @{raw_bot} (ID: `{bot_id}`)\n\n"
        f"Every file @{raw_bot} sends in {group_name} will now be captured. 🎯",
        parse_mode="markdown"
    )

    if LOG_CHANNEL:
        try:
            await client.send_message(
                LOG_CHANNEL,
                f"🎯 **Bot capture registered**\n"
                f"Group: {group_name} (`{group_id}`)\n"
                f"Target bot: @{raw_bot} (`{bot_id}`)"
            )
        except Exception:
            pass

# ── /removebot ─────────────────────────────────────────────────────────────
@app.on_message(filters.command("removebot") & filters.private)
@admin_only
async def cmd_removebot(client: Client, message: Message):
    args = message.text.split(None, 1)
    if len(args) < 2:
        await message.reply(
            "**Usage:** `/removebot <group_username_or_id>`\n\n"
            "**Example:** `/removebot CineAlliance`",
            parse_mode="markdown"
        )
        return

    raw = args[1].strip()
    ok, msg = remove_bot(raw)
    await message.reply(msg, parse_mode="markdown")

# ── /listbots ──────────────────────────────────────────────────────────────
@app.on_message(filters.command("listbots") & filters.private)
@admin_only
async def cmd_listbots(client: Client, message: Message):
    await message.reply(format_list(), parse_mode="markdown")

# ── /capturestatus ─────────────────────────────────────────────────────────
@app.on_message(filters.command("capturestatus") & filters.private)
@admin_only
async def cmd_status(client: Client, message: Message):
    all_bots  = list_all()
    me        = await client.get_me()
    skip_rate = 0
    total     = _stats["captured"] + _stats["skipped"]
    if total > 0:
        skip_rate = round(_stats["skipped"] / total * 100)

    text = (
        f"**Bot Capture Status**\n\n"
        f"👤 Running as: `{me.first_name}` (@{me.username})\n"
        f"📤 Destination: `{DEST_CHANNEL}`\n"
        f"🎯 Registered groups: `{len(all_bots)}`\n\n"
        f"**Session stats:**\n"
        f"✅ Captured (bot files): `{_stats['captured']}`\n"
        f"⏭️ Skipped (non-bot): `{_stats['skipped']}` ({skip_rate}% of traffic)\n"
        f"❌ Failed: `{_stats['failed']}`\n\n"
        f"_Use /listbots to see registered groups_"
    )
    await message.reply(text, parse_mode="markdown")

# ── /help ──────────────────────────────────────────────────────────────────
@app.on_message(filters.command(["start", "help"]) & filters.private)
async def cmd_help(client: Client, message: Message):
    me = await client.get_me()
    await message.reply(
        f"**Bot Capture Mode** — `{me.first_name}`\n\n"
        "Captures ONLY files from registered bots in watched groups.\n"
        "Skips user messages, spam, and unregistered groups.\n\n"
        "**Setup (one time):**\n"
        "1. Join the target group with this account\n"
        "2. `/setbot CineAlliance CineAllianceBotName`\n"
        "3. Done — every file that bot sends gets captured 🎯\n\n"
        "**Commands** _(admins only)_:\n"
        "• `/setbot <group> <bot>` — register bot to watch\n"
        "• `/removebot <group>` — stop targeting a group's bot\n"
        "• `/listbots` — show all registered bot-group pairs\n"
        "• `/capturestatus` — live capture stats\n\n"
        "**Tip:** Run alongside `forwarder.py` for full coverage:\n"
        "• `forwarder.py` = catches all files from groups\n"
        "• `bot_capture.py` = catches ONLY bot responses (cleaner)\n",
        parse_mode="markdown"
    )

# ── Startup ────────────────────────────────────────────────────────────────
async def main():
    await app.start()
    me = await app.get_me()
    logger.info(f"🎯 Bot capture started as: {me.first_name} (@{me.username})")

    all_bots = list_all()
    if not all_bots:
        logger.warning("⚠️  No bots registered yet — DM /setbot <group> <bot> to register one")
    else:
        logger.info(f"🎯 Watching {len(all_bots)} group(s) for specific bot responses:")
        for group, entry in all_bots.items():
            logger.info(f"  {group} → @{entry['bot_username']} (ID: {entry['bot_id']})")

    logger.info(f"📤 Destination: {DEST_CHANNEL}")

    if LOG_CHANNEL:
        try:
            registered = "\n".join(
                f"• `{g}` → @{e['bot_username']}"
                for g, e in all_bots.items()
            ) or "_none yet_"
            await app.send_message(
                LOG_CHANNEL,
                f"🎯 **Bot capture started**\n"
                f"As: `{me.first_name}`\n\n"
                f"Watching:\n{registered}\n\n"
                f"DM `/setbot <group> <bot>` to add more."
            )
        except Exception:
            pass

    logger.info("⏳ Listening... (only bot responses will be captured)")
    await idle()

    logger.info(
        f"📊 Session — Captured: {_stats['captured']} | "
        f"Skipped: {_stats['skipped']} | Failed: {_stats['failed']}"
    )
    await app.stop()

if __name__ == "__main__":
    asyncio.run(main())
