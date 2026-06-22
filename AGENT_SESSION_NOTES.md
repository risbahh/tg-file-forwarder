# AGENT_SESSION_NOTES — tg-file-forwarder
_Last updated: 2026-06-22 | Session 8 (dry-run pass)_

## Repo
`azizthekiller123/tg-file-forwarder`
Railway auto-deploys on push to main (~2 min). Never edit Railway directly.

## What this bot does
Pyrogram userbot (pyrofork==2.3.45, imported as `pyrogram` — **NEVER change imports**).
Watches multiple Telegram source chats for new video/document/audio and forwards
them to index channels used with `azizthekiller123/Auto-filter-bot-4`.
Supports: per-type routing, deduplication, caption cleaning, stats, keyword filter,
ignore-chat, auto-retry on startup, web dashboard.

## User context
- Multiple Telegram accounts available (not single-account constrained)
- Uses `account_pool.py` (AccountPool) in `multi_forwarder.py` for pool management
- Railway deployment; no Docker knowledge required

## File map
| File | Purpose |
|------|---------|
| `forwarder.py` | Primary entry — all commands + file handler + startup |
| `multi_forwarder.py` | Multi-account entry — uses AccountPool to spread load |
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
| `account_pool.py` | Multi-account pool (pre-existing) |
| `dashboard.py` | aiohttp web dashboard at `/` [S8 enhanced] |
| `utils.py` | safe_forward, get_file_name, get_file_size, human_size |
| `discovery.py` | Source chat discovery helpers |

## Required env vars (Railway)
```
API_ID, API_HASH, SESSION_STRING   — primary account
SOURCE_CHATS                        — comma-separated chat IDs/@usernames
DEST_CHANNEL                        — default forwarding destination
ADMINS                              — comma-separated Telegram user IDs
```
Optional: `DEST_MOVIES`, `DEST_SERIES`, `DEST_SOUTH`, `LOG_CHANNEL`, `DELAY`

## All commands
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
- Auto-retry: runs 30s after startup if `failed.json` non-empty [S8]

### Pause / misc
- `/pausefwd` / `/resumefwd`
- `/resetdups confirm`
- `/discover` / `/suggest <kw>`
- `/poolstatus` (multi_forwarder.py only)
- `/help`

## Key API contracts
```python
# failed_db
failed_db.load()                           # → list of {"chat_id":int, "message_id":int, "ts":float}
failed_db.save(chat_id, message_id)
failed_db.remove(chat_id, message_id)      # both are ints
failed_db.count()
failed_db.clear()
failed_db.by_chat()                        # → {chat_id: count}

# stats_db
stats_db.record(chat_id, title)
stats_db.get_all()                         # → sorted list of dicts
stats_db.all_stats()                       # → raw dict {chat_id_str: {...}}  [added S8]
stats_db.total()

# ignore_db
ignore_db.ignore(chat_id: int, title: str)
ignore_db.unignore(chat_id: int)
ignore_db.is_ignored(chat_id: int) → bool
ignore_db.list_ignored() → dict

# keyword_filter
keyword_filter.passes(text: str) → bool   # text = f"{filename} {caption}"
keyword_filter.get_state() → dict
keyword_filter.set_mode("allow"|"block"|"off")
keyword_filter.add_keyword(word) → bool
keyword_filter.remove_keyword(index: int) → str

# router
get_destination(filename: str, source_chat_id: int) → str  # TWO args required
```

## Bugs fixed across all sessions
| Session | Bug | Fix |
|---------|-----|-----|
| 1–6 | Duplicate detection, FloodWait, caption, stats, session revoke | Various |
| 7 | auto-retry save/load | failed_db.py |
| 8 | failed_db.all() → .load(); item["msg_id"] → ["message_id"] | forwarder.py |
| 8 | bulk_dest inside try/except block (syntax error) | thumb main.py |
| 8 (dry-run) | multi_forwarder.py missing ignore_db + keyword_filter | multi_forwarder.py |
| 8 (dry-run) | multi_forwarder.py missing auto-retry on startup | multi_forwarder.py |

## What to build next
- Account rotation / failover UI (multi-account setup confirmed by user)
- `/schedule off HH:MM HH:MM` — quiet-hours auto-pause
- Per-source keyword filters (not global)
- Quality routing in thumb-cleaner (480p/720p/1080p → different channels)
- Milestone notifications (every 100/500 files forwarded)
