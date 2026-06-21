"""
Multi-Account Pool — account_pool.py
──────────────────────────────────────
Manages 2–3 Pyrogram userbot clients as a rotating pool.
When one account hits FloodWait, the pool automatically switches to the
next available account — keeping forwarding at full speed.

Setup:
  SESSION_STRING   → Account 1 (required)
  SESSION_STRING_2 → Account 2 (optional)
  SESSION_STRING_3 → Account 3 (optional)

Usage:
  pool  = await AccountPool.create()
  ok    = await pool.forward(message, dest)   # auto-rotates on FloodWait
"""
import asyncio
import logging
import os
import time

from pyrogram import Client
from pyrogram.errors import FloodWait, ChatForwardsRestricted

from config import API_ID, API_HASH, DELAY, FLOOD_EXTRA, MAX_RETRIES

logger = logging.getLogger(__name__)


class AccountPool:
    def __init__(self, clients: list[Client]):
        self._clients    : list[Client]     = clients
        self._flood_until: dict[int, float] = {}   # index → epoch when available
        self._fwd_counts : dict[int, int]   = {}   # index → forwards this session
        self._flood_counts: dict[int, int]  = {}   # index → times hit FloodWait

    # ── Factory ──────────────────────────────────────────────────────────────
    @classmethod
    async def create(cls) -> "AccountPool":
        """Load all SESSION_STRINGs from env and start each client."""
        session_keys = ["SESSION_STRING", "SESSION_STRING_2", "SESSION_STRING_3"]
        clients = []
        for i, key in enumerate(session_keys, 1):
            val = os.environ.get(key, "").strip()
            if not val:
                continue
            name = f"account_{i}"
            c = Client(name, api_id=API_ID, api_hash=API_HASH, session_string=val)
            try:
                await c.start()
                me = await c.get_me()
                logger.info(f"✅ Account {i}: {me.first_name} (@{me.username}) — id {me.id}")
                clients.append(c)
            except Exception as e:
                logger.error(f"❌ Account {i} ({key}) failed to start: {e}")
        if not clients:
            raise RuntimeError("No valid SESSION_STRING found in environment.")
        logger.info(f"🏊 Account pool ready: {len(clients)} account(s)")
        return cls(clients)

    # ── Forwarding ────────────────────────────────────────────────────────────
    async def forward(self, message, dest: int) -> bool:
        """
        Forward a message using the best available account.
        Auto-rotates on FloodWait. Returns True on success.

        FIX: falls back to copy_message when the source chat has content
        protection enabled (ChatForwardsRestricted), so protected-content
        channels no longer silently fail every attempt.
        """
        tried: set[int] = set()
        for _attempt in range(MAX_RETRIES * len(self._clients)):
            idx, client = self._pick()
            if idx is None:
                # All accounts on FloodWait — wait for the soonest
                wait = self._soonest_wait()
                logger.warning(f"⏳ All accounts on FloodWait — waiting {wait:.0f}s")
                await asyncio.sleep(wait)
                continue

            try:
                await client.forward_messages(
                    chat_id=dest,
                    from_chat_id=message.chat.id,
                    message_ids=message.id,
                )
                await asyncio.sleep(DELAY)
                self._fwd_counts[idx] = self._fwd_counts.get(idx, 0) + 1
                return True

            except ChatForwardsRestricted:
                # Source chat has content protection — forward_messages is blocked.
                # Fall back to copy_message which re-uploads and bypasses the restriction.
                logger.warning(
                    f"🔒 {self._label(idx)}: source chat has content protection "
                    f"— falling back to copy_message"
                )
                try:
                    await client.copy_message(
                        chat_id=dest,
                        from_chat_id=message.chat.id,
                        message_id=message.id,
                    )
                    await asyncio.sleep(DELAY)
                    self._fwd_counts[idx] = self._fwd_counts.get(idx, 0) + 1
                    return True
                except FloodWait as e:
                    wait = e.value + FLOOD_EXTRA
                    logger.warning(f"⏳ {self._label(idx)} FloodWait {e.value}s (copy) — switching")
                    self._flood_until[idx]  = time.time() + wait
                    self._flood_counts[idx] = self._flood_counts.get(idx, 0) + 1
                    tried.add(idx)
                except Exception as ce:
                    logger.error(f"❌ {self._label(idx)} copy_message failed: {type(ce).__name__}: {ce}")
                    tried.add(idx)

            except FloodWait as e:
                wait = e.value + FLOOD_EXTRA
                logger.warning(f"⏳ {self._label(idx)} FloodWait {e.value}s — switching account")
                self._flood_until[idx]  = time.time() + wait
                self._flood_counts[idx] = self._flood_counts.get(idx, 0) + 1
                tried.add(idx)

            except Exception as e:
                logger.error(f"❌ {self._label(idx)} forward error: {type(e).__name__}: {e}")
                await asyncio.sleep(5)
                tried.add(idx)

            if len(tried) >= len(self._clients):
                # Every account has failed at least once in this round
                break

        return False

    # ── Status ────────────────────────────────────────────────────────────────
    async def status(self) -> str:
        lines = [f"**Multi-Account Pool** — {len(self._clients)} account(s)\n"]
        now = time.time()
        for i, c in enumerate(self._clients):
            try:
                me   = await c.get_me()
                name = f"{me.first_name} (@{me.username})"
            except Exception:
                name = f"Account {i+1} (error)"
            flood  = self._flood_until.get(i, 0)
            status = f"⏳ FloodWait {flood - now:.0f}s" if flood > now else "✅ Available"
            fwds   = self._fwd_counts.get(i, 0)
            floods = self._flood_counts.get(i, 0)
            lines.append(f"{i+1}. **{name}**")
            lines.append(f"   {status} | Forwarded: {fwds} | FloodWaits: {floods}")
        return "\n".join(lines)

    async def stop_all(self):
        for c in self._clients:
            try:
                await c.stop()
            except Exception:
                pass

    def account_count(self) -> int:
        return len(self._clients)

    # ── Internals ─────────────────────────────────────────────────────────────
    def _pick(self) -> tuple[int | None, Client | None]:
        """Return (index, client) for the next available account, or (None, None)."""
        now = time.time()
        for i, c in enumerate(self._clients):
            if self._flood_until.get(i, 0) <= now:
                return i, c
        return None, None

    def _soonest_wait(self) -> float:
        now = time.time()
        if not self._flood_until:
            return DELAY
        return max(0.0, min(self._flood_until.values()) - now)

    def _label(self, idx: int) -> str:
        return f"Account {idx + 1}"
