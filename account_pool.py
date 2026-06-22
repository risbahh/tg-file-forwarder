"""
Multi-Account Pool — account_pool.py
──────────────────────────────────────
Manages up to 5 Pyrogram userbot clients as a rotating pool.

Setup (Railway env vars):
  SESSION_STRING    → Account 1 (required — also used as listener)
  SESSION_STRING_2  → Account 2 (optional)
  SESSION_STRING_3  → Account 3 (optional)
  SESSION_STRING_4  → Account 4 (optional)
  SESSION_STRING_5  → Account 5 (optional)

Per-account source assignment (optional static env override):
  SOURCE_CHATS_2    → comma-separated source chats forwarded via Account 2 only
  SOURCE_CHATS_3    → ... Account 3 only
  (sources not in any SOURCE_CHATS_N use round-robin across all accounts)

Dynamic assignment: /assignsource <chat> <account_num> in Telegram.
Stored in source_assignments.json.

FloodWait failover: if the assigned account is on FloodWait, the pool
automatically falls over to the next available account.
"""
import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field

from pyrogram import Client
from pyrogram.errors import FloodWait, ChatForwardsRestricted

from config import API_ID, API_HASH, DELAY, FLOOD_EXTRA, MAX_RETRIES

logger = logging.getLogger(__name__)

_ASSIGN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "source_assignments.json")
_lock = threading.Lock()


@dataclass
class AccountInfo:
    idx:          int
    client:       Client
    name:         str
    username:     str
    flood_until:  float = 0.0   # epoch when FloodWait clears
    fwd_count:    int   = 0     # forwards this session
    flood_count:  int   = 0     # FloodWaits hit this session
    error_count:  int   = 0     # non-FloodWait errors

    def is_available(self) -> bool:
        return time.time() >= self.flood_until

    def flood_remaining(self) -> float:
        return max(0.0, self.flood_until - time.time())

    def label(self) -> str:
        return f"Account {self.idx + 1} ({self.name})"


class AccountPool:
    def __init__(self, accounts: list[AccountInfo]):
        self._accounts = accounts
        self._rr_idx   = 0          # round-robin pointer
        self._assignments: dict[str, int] = {}   # chat_id_str → account idx
        self._load_assignments()

    # ── Factory ───────────────────────────────────────────────────────────────
    @classmethod
    async def create(cls) -> "AccountPool":
        """Load all SESSION_STRINGs from env, start each client, return pool."""
        session_keys = [
            "SESSION_STRING",
            "SESSION_STRING_2",
            "SESSION_STRING_3",
            "SESSION_STRING_4",
            "SESSION_STRING_5",
        ]
        accounts: list[AccountInfo] = []
        for i, key in enumerate(session_keys):
            val = os.environ.get(key, "").strip()
            if not val:
                continue
            c = Client(f"account_{i+1}", api_id=API_ID, api_hash=API_HASH, session_string=val)
            try:
                await c.start()
                me = await c.get_me()
                acc = AccountInfo(
                    idx=i, client=c,
                    name=me.first_name or f"Account {i+1}",
                    username=me.username or "?",
                )
                # Apply static SOURCE_CHATS_N env assignments
                if i > 0:
                    env_key = f"SOURCE_CHATS_{i+1}"
                    sources = [s.strip() for s in os.environ.get(env_key, "").split(",") if s.strip()]
                    for src in sources:
                        # Will be finalised after pool is created
                        pass
                accounts.append(acc)
                logger.info(f"✅ Account {i+1}: {me.first_name} (@{me.username}) id={me.id}")
            except Exception as e:
                logger.error(f"❌ Account {i+1} ({key}) failed: {e}")

        if not accounts:
            raise RuntimeError("No valid SESSION_STRING found in environment.")
        logger.info(f"🏊 Pool ready: {len(accounts)} account(s)")

        pool = cls(accounts)

        # Apply static env-based assignments (SOURCE_CHATS_2, SOURCE_CHATS_3, …)
        for i in range(1, len(accounts)):
            env_key = f"SOURCE_CHATS_{i+1}"
            sources = [s.strip() for s in os.environ.get(env_key, "").split(",") if s.strip()]
            for src in sources:
                if src not in pool._assignments:   # don't overwrite dynamic assignments
                    pool._assignments[str(src)] = i
                    logger.info(f"  📌 {src} → Account {i+1} (env)")

        return pool

    # ── Assignment management ─────────────────────────────────────────────────
    def _load_assignments(self):
        try:
            with _lock, open(_ASSIGN_FILE) as f:
                self._assignments = json.load(f)
        except Exception:
            self._assignments = {}

    def _save_assignments(self):
        with _lock, open(_ASSIGN_FILE, "w") as f:
            json.dump(self._assignments, f, indent=2)

    def assign(self, source_id: str, account_idx: int) -> str:
        """Assign a source chat to a specific account (0-based idx).
        Returns error string or empty string on success."""
        if account_idx < 0 or account_idx >= len(self._accounts):
            return f"Account {account_idx+1} doesn't exist (pool has {len(self._accounts)} accounts)."
        self._assignments[str(source_id)] = account_idx
        self._save_assignments()
        return ""

    def unassign(self, source_id: str) -> bool:
        """Remove assignment for a source. Returns True if it existed."""
        existed = str(source_id) in self._assignments
        self._assignments.pop(str(source_id), None)
        self._save_assignments()
        return existed

    def get_assignments(self) -> dict[str, int]:
        return dict(self._assignments)

    # ── Forwarding ────────────────────────────────────────────────────────────
    async def forward(self, message, dest) -> bool:
        """Forward using round-robin (no source-specific routing)."""
        return await self._forward(message, dest, source_id=None)

    async def forward_from_source(self, message, dest, source_chat_id: str | int) -> bool:
        """Forward preferring the account assigned to this source chat.
        Falls over to any available account if assigned one is on FloodWait."""
        return await self._forward(message, dest, source_id=str(source_chat_id))

    async def _forward(self, message, dest, source_id: str | None) -> bool:
        tried: set[int] = set()
        max_attempts = MAX_RETRIES * max(len(self._accounts), 1)

        for _ in range(max_attempts):
            idx, acc = self._pick_for(source_id, tried)
            if idx is None:
                wait = self._soonest_wait()
                logger.warning(f"⏳ All accounts on FloodWait — waiting {wait:.0f}s")
                await asyncio.sleep(wait)
                tried.clear()    # reset so we retry after wait
                continue

            try:
                await acc.client.forward_messages(
                    chat_id=dest,
                    from_chat_id=message.chat.id,
                    message_ids=message.id,
                )
                await asyncio.sleep(DELAY)
                acc.fwd_count += 1
                return True

            except ChatForwardsRestricted:
                logger.warning(f"🔒 {acc.label()}: content protection — falling back to copy_message")
                try:
                    await acc.client.copy_message(
                        chat_id=dest,
                        from_chat_id=message.chat.id,
                        message_id=message.id,
                    )
                    await asyncio.sleep(DELAY)
                    acc.fwd_count += 1
                    return True
                except FloodWait as e:
                    self._set_flood(acc, e.value)
                    tried.add(idx)
                except Exception as ce:
                    logger.error(f"❌ {acc.label()} copy_message failed: {ce}")
                    acc.error_count += 1
                    tried.add(idx)

            except FloodWait as e:
                self._set_flood(acc, e.value)
                tried.add(idx)

            except Exception as e:
                logger.error(f"❌ {acc.label()} forward error: {type(e).__name__}: {e}")
                acc.error_count += 1
                await asyncio.sleep(5)
                tried.add(idx)

            if len(tried) >= len(self._accounts):
                break

        return False

    # ── Status ────────────────────────────────────────────────────────────────
    async def status(self) -> str:
        """Formatted text for /poolstatus command."""
        lines = [f"**🏊 Account Pool — {len(self._accounts)} account(s)**\n"]
        now = time.time()
        for acc in self._accounts:
            flood_rem = acc.flood_remaining()
            st = f"⏳ FloodWait {flood_rem:.0f}s" if flood_rem > 0 else "✅ Available"
            assigned = [k for k, v in self._assignments.items() if v == acc.idx]
            src_str = f" | Sources: {', '.join(assigned)}" if assigned else " | Sources: (round-robin)"
            lines.append(
                f"**{acc.idx+1}. {acc.name}** (@{acc.username})\n"
                f"   {st} | Forwarded: {acc.fwd_count:,} | "
                f"FloodWaits: {acc.flood_count} | Errors: {acc.error_count}"
                f"{src_str}"
            )
        return "\n\n".join(lines)

    def get_status_list(self) -> list[dict]:
        """Structured status for dashboard."""
        return [
            {
                "idx":            acc.idx,
                "name":           acc.name,
                "username":       acc.username,
                "available":      acc.is_available(),
                "flood_remaining": acc.flood_remaining(),
                "fwd_count":      acc.fwd_count,
                "flood_count":    acc.flood_count,
                "error_count":    acc.error_count,
                "assigned_sources": [k for k, v in self._assignments.items() if v == acc.idx],
            }
            for acc in self._accounts
        ]

    async def stop_all(self):
        for acc in self._accounts:
            try:
                await acc.client.stop()
            except Exception:
                pass

    def account_count(self) -> int:
        return len(self._accounts)

    # ── Internals ─────────────────────────────────────────────────────────────
    def _pick_for(self, source_id: str | None, skip: set[int]) -> tuple[int | None, AccountInfo | None]:
        """
        Pick the best account for this forward:
        1. If source_id has an assignment, try that account first.
        2. Fall back to round-robin across all available accounts.
        Skip accounts in the `skip` set.
        """
        now = time.time()

        # Try assigned account first
        if source_id and source_id in self._assignments:
            idx = self._assignments[source_id]
            if idx not in skip and idx < len(self._accounts):
                acc = self._accounts[idx]
                if acc.is_available():
                    return idx, acc
                # Assigned account on FloodWait — log failover
                if idx not in skip:
                    logger.info(
                        f"⚡ {acc.label()} on FloodWait ({acc.flood_remaining():.0f}s) "
                        f"— failing over to next available account"
                    )

        # Round-robin over remaining accounts
        n = len(self._accounts)
        for _ in range(n):
            idx = self._rr_idx % n
            self._rr_idx = (self._rr_idx + 1) % n
            if idx in skip:
                continue
            acc = self._accounts[idx]
            if acc.is_available():
                return idx, acc

        return None, None

    def _set_flood(self, acc: AccountInfo, seconds: int):
        wait = seconds + FLOOD_EXTRA
        acc.flood_until  = time.time() + wait
        acc.flood_count += 1
        logger.warning(f"⏳ {acc.label()} FloodWait {seconds}s → switching account")

    def _soonest_wait(self) -> float:
        now = time.time()
        waits = [a.flood_until - now for a in self._accounts if a.flood_until > now]
        return max(0.5, min(waits)) if waits else DELAY
