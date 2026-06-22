# AGENT_SESSION_NOTES — tg-file-forwarder
_Last updated: 2026-06-22 | Session 9 — multi-account pool_

## Repo
`azizthekiller123/tg-file-forwarder`
Railway auto-deploys on push to main (~2 min). Never edit Railway directly.

## What this bot does
Pyrogram userbot (pyrofork==2.3.45, imported as `pyrogram` — **NEVER change imports**).
Watches multiple Telegram source chats for new video/document/audio and forwards
them to index channels. Supports multi-account pool with FloodWait failover,
per-source account assignment, round-robin rotation, and a live web dashboard.

## User context
- **Multiple Telegram accounts confirmed** — pool supports up to 5 accounts
- Uses `account_pool.py` (AccountPool) in both `forwarder.py` and `multi_forwarder.py`
- Railway deployment; no Docker knowledge required

## File map
| File | Purpose |
|------|---------|
| `forwarder.py` | Primary entry — all commands + file handler + startup |
| `multi_forwarder.py` | Multi-account entry — AccountPool + dashboard [S9] |
| `account_pool.py` | Multi-account pool: round-robin, per-source assignment, FloodWait failover [full rewrite S9] |
| `config.py` | Env var loader |
| `chats_db.py` | Dynamic source chats → `chats.json` |
| `router.py` | Per-type routing → `routing.json` |
| `seen_db.py` | Deduplication → `seen.json` |
| `stats_db.py` | Per-source forwarding counts → `stats.json` |
| `failed_db.py` | Failed forwards queue → `failed.json` |
| `caption_cleaner.py` | Strips @mentions, URLs, promo lines |
| `caption_suffix.py` | Appends user suffix → `caption_suffix.json` |
| `strip_patterns.py` | Runtime regex strip patterns → `strip_patterns.json` |
| `ignore_db.py` | Ignored chat IDs → `ignored.json` [S8] |
| `keyword_filter.py` | Allow/block keyword filter → `keywords.json` [S8] |
| `source_assignments.json` | Per-source account assignments (auto-created by pool) [S9] |
| `dashboard.py` | aiohttp web dashboard — includes account health table [S9] |
| `utils.py` | safe_forward, get_file_name, get_file_size, human_size |

## Required env vars (Railway)
```
API_ID, API_HASH
SESSION_STRING      → Account 1 (required — also used as listener)
SESSION_STRING_2    → Account 2 (optional)
SESSION_STRING_3    → Account 3 (optional)
SESSION_STRING_4    → Account 4 (optional)
SESSION_STRING_5    → Account 5 (optional)
SOURCE_CHATS        → comma-separated chat IDs/@usernames
DEST_CHANNEL        → default forwarding destination
ADMINS              → comma-separated Telegram user IDs
```
Optional static source assignment (overridden by /assignsource):
```
SOURCE_CHATS_2      → sources forwarded only via Account 2
SOURCE_CHATS_3      → sources forwarded only via Account 3
```
Optional: `DEST_MOVIES`, `DEST_SERIES`, `DEST_SOUTH`, `LOG_CHANNEL`, `DELAY`

## Multi-account system (implemented S9)
### How it works
1. **One listener** — `SESSION_STRING` (Account 1) watches all source chats.
   The listener only *sees* messages; accounts do the *forwarding*.
2. **Round-robin** — each forward request cycles through accounts in sequence.
   `_rr_idx` advances after each pick; never resets to 0.
3. **Per-source assignment** — a source can be pinned to a specific account
   via `/assignsource`. That account is tried first; on FloodWait → failover.
4. **FloodWait failover** — if the preferred account is on FloodWait, the pool
   immediately tries the next available account (round-robin fallback).
5. **All accounts on FloodWait** — pool waits for the soonest one to clear
   and logs a warning.

### New env vars for multi-account
| Var | Purpose |
|-----|---------|
| `SESSION_STRING_2` | Account 2 session string |
| `SESSION_STRING_3` | Account 3 session string |
| `SESSION_STRING_4` | Account 4 session string |
| `SESSION_STRING_5` | Account 5 session string |
| `SOURCE_CHATS_2` | Static sources for Account 2 (optional) |
| `SOURCE_CHATS_3` | Static sources for Account 3 (optional) |

### Account pool API (account_pool.py)
```python
pool = await AccountPool.create()         # starts all clients from env
pool.account_count() → int

# Forwarding
await pool.forward(message, dest) → bool                       # round-robin
await pool.forward_from_source(message, dest, chat_id) → bool  # source-aware + failover

# Assignment management
pool.assign(source_id: str, account_idx: int) → str  # "" on success, error on fail; idx is 0-based
pool.unassign(source_id: str) → bool                  # True if it existed
pool.get_assignments() → dict[str, int]               # {chat_id_str: account_idx}

# Status
await pool.status() → str                 # formatted text for /poolstatus
pool.get_status_list() → list[dict]       # structured data for dashboard
# each dict: {idx, name, username, available, flood_remaining, fwd_count, flood_count, error_count, assigned_sources}

await pool.stop_all()
```

### Dashboard integration
- `start_dashboard(stats_getter, pool_getter, port)` — pass `pool_getter=lambda: _pool`
- Dashboard renders "Account Pool" table: #, Name, Status (✅/⏳Ns), Forwarded, FloodWaits, Pinned Sources

## All commands
### Pool / account management (NEW in S9)
- `/poolstatus` — full table: each account's name, FloodWait status, forwarded count, flood count, pinned sources
- `/assignsource <chat> <account_num>` — pin source chat to specific account (1-based)
- `/unassignsource <chat>` — remove pin, revert to round-robin
- `/assignments` — list all pinned assignments grouped by account

### Source management
- `/addchat <chat>` — add source chat
- `/removechat <chat>` — remove source chat
- `/listchats` — list all sources
- `/ignorechat <chat>` — pause a source silently [S8]
- `/unignorechat <chat>` — re-enable [S8]
- `/listignored` — show all ignored chats [S8]
- `/joinchat <link>` — join invite link + auto-add [S8]

### Stats & export
- `/fwrstatus` — session stats
- `/dupstats` — dedup DB size
- `/srcstats` — per-source forwarding counts
- `/export` — download stats.json as CSV [S8]

### Routing & filtering
- `/route <type> <channel>` — set per-type destination
- `/routes` — list routing rules
- `/keywords list/allow/block/remove/off` — keyword filter [S8]

### Caption management
- `/setcaption <text|off>` — caption suffix
- `/strippatterns add/remove/list/test` — runtime strip patterns
- `/cleancaptions` — bulk edit existing captions
- `/stopcleaning` — cancel clean job

### Recovery
- `/failedstats` — show failed queue
- `/retry` — retry all failed manually
- `/clearfailed confirm` — wipe failed queue
- Auto-retry on startup: 30s after start if `failed.json` non-empty

### Pause / misc
- `/pausefwd` / `/resumefwd`
- `/resetdups confirm`
- `/discover` / `/suggest <kw>`
- `/help`

## Key API contracts
```python
# failed_db keys
{"chat_id": int, "message_id": int, "ts": float}
failed_db.load() → list; .save(cid, mid); .remove(cid, mid); .count(); .clear(); .by_chat()

# stats_db
stats_db.record(chat_id, title); .get_all() → sorted list; .all_stats() → raw dict; .total()

# ignore_db
ignore_db.is_ignored(int) → bool; .ignore(int, str); .unignore(int); .list_ignored() → dict

# keyword_filter
keyword_filter.passes(text) → bool; .get_state() → dict; .set_mode(str); .add_keyword(str); .remove_keyword(int)

# router
get_destination(filename: str, source_chat_id: int) → str   # TWO args required
```

## Bugs fixed across all sessions
| Session | Bug | Fix |
|---------|-----|-----|
| 1–6 | Duplicate detection, FloodWait, caption, stats, session revoke | Various |
| 7 | auto-retry save/load | failed_db.py |
| 8 | failed_db.all() → .load(); item["msg_id"] → ["message_id"] | forwarder.py |
| 8 (dry-run) | multi_forwarder.py missing ignore_db + keyword_filter | multi_forwarder.py |
| 8 (dry-run) | multi_forwarder.py missing auto-retry on startup | multi_forwarder.py |
| 9 | account_pool._pick() always picked idx 0 — not true round-robin | account_pool.py |

## What to build next
- `/schedule off HH:MM HH:MM` — quiet-hours auto-pause
- Per-source keyword filters (not global)
- Quality routing in thumb-cleaner (480p/720p/1080p → different channels)
- Milestone notifications (every 100/500 files forwarded)
- `/balancepool` — redistribute sources evenly across accounts
