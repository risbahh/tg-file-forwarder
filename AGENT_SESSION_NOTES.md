# AGENT_SESSION_NOTES — tg-file-forwarder
_Last updated: 2026-06-22 | Session 8_

## Repo
`azizthekiller123/tg-file-forwarder`

## What this bot does
Pyrogram userbot (pyrofork==2.3.45, imported as pyrogram — **NEVER change imports**).
Watches multiple Telegram source chats for new video/document/audio files and
forwards them to index channels (used with azizthekiller123/Auto-filter-bot-4).
Supports per-type routing (Movies/Series/South), deduplication, caption cleaning,
stats tracking, and now keyword filtering and ignore-chat.

## Architecture
- `forwarder.py` — main: all commands + file handler + startup
- `multi_forwarder.py` — secondary if present (same commands, separate session)
- `config.py` — env var loader
- `chats_db.py` — dynamic source chats (chats.json)
- `router.py` — per-type routing (routing.json)
- `seen_db.py` — deduplication (seen.json)
- `stats_db.py` — per-source forwarding counts (stats.json)
- `failed_db.py` — failed forwards queue (failed.json)
- `caption_cleaner.py` — strips @mentions, URLs, promo lines
- `caption_suffix.py` — appends user-defined suffix
- `strip_patterns.py` — runtime regex patterns (strip_patterns.json)
- `ignore_db.py` — ignored chat IDs (ignored.json) [NEW Session 8]
- `keyword_filter.py` — allow/block keyword filter (keywords.json) [NEW Session 8]
- `dashboard.py` — aiohttp web dashboard at /

## Railway Deployment
- Auto-deploys on push to main (~2 min)
- Never edit Railway dashboard directly
- Required env vars: API_ID, API_HASH, SESSION_STRING, SOURCE_CHATS, DEST_CHANNEL, ADMINS
- Optional: DEST_MOVIES, DEST_SERIES, DEST_SOUTH, LOG_CHANNEL, DELAY

## All Commands (current)
### Source management
- `/addchat <chat>` — add source chat
- `/removechat <chat>` — remove source chat
- `/listchats` — list all sources
- `/ignorechat <chat>` — pause a source without removing it [NEW]
- `/unignorechat <chat>` — re-enable ignored chat [NEW]
- `/listignored` — show all ignored chats [NEW]
- `/joinchat <link>` — join invite link + auto-add to sources [NEW]

### Stats & Status
- `/fwrstatus` — session stats
- `/dupstats` — dedup DB stats
- `/srcstats` — per-source forwarding counts
- `/export` — download stats.json as CSV [NEW]

### Routing & Filtering
- `/route <type> <channel>` — set per-type destination
- `/routes` — list routing rules
- `/keywords list/allow/block/remove/off` — keyword filter [NEW]

### Caption management
- `/setcaption <text>` — set caption suffix
- `/strippatterns add/remove/list/test` — runtime strip patterns
- `/cleancaptions` — bulk edit existing captions
- `/stopcleaning` — cancel clean job

### Recovery
- `/failedstats` — show failed forwards count
- `/retry` — retry all failed manually
- `/clearfailed` — wipe failed queue
- (auto-retry runs 30s after startup if failed.json is non-empty)

### Pause / Misc
- `/pausefwd` / `/resumefwd` — pause all forwarding
- `/resetdups` — clear seen.json
- `/discover` / `/suggest <kw>` — find new source chats
- `/help` — command list

## Key Technical Notes
- `failed_db.load()` returns list of `{"chat_id": int, "message_id": int, "ts": float}`
  - Key is `message_id` NOT `msg_id`
  - `remove(chat_id, message_id)` — both ints
- `stats_db.all_stats()` returns raw dict (added Session 8) — use for CSV export
- `stats_db.get_all()` returns sorted list of dicts
- `keyword_filter.passes(text)` — text = filename + " " + caption combined
- `ignore_db.is_ignored(chat_id: int)` — check before forwarding
- Dashboard at /: shows failed_pending, top sources, ignored count, keyword mode
- Auto-retry: `asyncio.create_task(_auto_retry())` runs 30s after startup

## Bugs fixed in sessions 1–8
1. Duplicate detection not working (missing seen_db mark)
2. FloodWait not being awaited correctly
3. Caption cleaner not applied on bulk re-runs
4. Stats not per-source (only total)
5. Session revoke not caught → zombie process
6. Dashboard chats.json format — must use .get("chats", [])
7. failed_db auto-retry used wrong function name (all→load) and wrong key (msg_id→message_id)
8. bulk_dest was placed inside try/except block (syntax error) — fixed

## What to do next (suggestions)
- `/schedule off 02:00 06:00` — quiet hours for auto-pause
- Milestone notifications (every 100/500 files)
- Per-source keyword filtering (not global)
- Auto-join all sources on startup (verify membership)
