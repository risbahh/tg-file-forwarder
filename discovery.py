"""
Source Auto-Discovery — discovery.py
──────────────────────────────────────
Helps you find new movie groups to add as sources.

Two discovery methods:
  1. search_joined()  — scan groups the userbot already joined, suggest ones
                        that look like movie sources (by title/description)
  2. search_public()  — search Telegram for public groups matching a keyword
                        (requires the userbot to call search_public_chats)

Usage (called from forwarder.py commands):
  from discovery import find_joined_sources, search_public_sources
"""
import logging
import re

from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.errors import FloodWait
import asyncio

logger = logging.getLogger(__name__)

_MOVIE_KEYWORDS = re.compile(
    r'movie|film|cinema|cine|4k|1080p|hd|series|web[\s\-]?dl|bluray|'
    r'bollywood|hollywood|south|tamil|telugu|dubbed|ott|stream|torrent',
    re.I
)

async def find_joined_sources(client: Client, limit: int = 50) -> list[dict]:
    """
    Scan all groups the userbot is already a member of.
    Returns a sorted list of groups that look like movie sources.
    """
    results = []
    try:
        async for dialog in client.get_dialogs(limit=limit * 5):
            chat = dialog.chat
            if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
                continue
            title = chat.title or ""
            desc  = getattr(chat, "description", "") or ""
            if _MOVIE_KEYWORDS.search(title) or _MOVIE_KEYWORDS.search(desc):
                results.append({
                    "id":       chat.id,
                    "title":    title,
                    "username": getattr(chat, "username", None),
                    "members":  getattr(chat, "members_count", 0) or 0,
                    "type":     chat.type.value,
                })
            if len(results) >= limit:
                break
    except FloodWait as e:
        logger.warning(f"FloodWait {e.value}s during discovery")
        await asyncio.sleep(e.value)
    except Exception as e:
        logger.error(f"find_joined_sources error: {e}")

    results.sort(key=lambda x: x["members"], reverse=True)
    return results


async def search_public_sources(client: Client, query: str, limit: int = 10) -> list[dict]:
    """
    Search Telegram for public groups/channels matching a query.
    Uses search_public_chats — may return limited results depending on account age.
    """
    results = []
    try:
        chats = await client.search_public_chats(query)
        for chat in chats[:limit]:
            if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
                continue
            results.append({
                "id":       chat.id,
                "title":    chat.title or "",
                "username": getattr(chat, "username", None),
                "members":  getattr(chat, "members_count", 0) or 0,
                "type":     chat.type.value,
            })
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except Exception as e:
        logger.error(f"search_public_sources error: {e}")

    results.sort(key=lambda x: x["members"], reverse=True)
    return results


def format_results(results: list[dict], title: str = "Discovered sources") -> str:
    if not results:
        return "_No movie-related groups found._"
    lines = [f"**{title}** ({len(results)} found):\n"]
    for i, r in enumerate(results, 1):
        uname    = f"@{r['username']}" if r["username"] else f"`{r['id']}`"
        members  = f"{r['members']:,}" if r["members"] else "?"
        lines.append(f"{i}. **{r['title']}** — {uname} ({members} members)")
    lines.append("\nUse `/addchat <username or id>` to add any of these.")
    return "\n".join(lines)
