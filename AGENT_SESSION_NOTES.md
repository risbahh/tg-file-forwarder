# TG File Forwarder — Agent Session Notes

> **Last updated:** Session 10 (June 2026)
> Read this file FIRST before making any changes. It is the single source of truth for any agent working on this project.

---

## What This Bot Does

A Pyrogram **userbot** (not a bot token — a real user account) that:
1. Joins Telegram source groups as a member
2. Watches for video/document/audio files posted in those groups
3. Routes and forwards each file to the correct index channel (movies / series / south / default)
4. Deduplicates via `file_unique_id` so the same file is never forwarded twice

Two separate entry points exist:
- `forwarder.py` — single-account, all features, main deployment
- `multi_forwarder.py` — multi-account with FloodWait rotation across up to 5 accounts
- `pm_bot_forwarder.py` — NEW: captures files from auto-filter bots that deliver via PM deep links

---

## Critical Rules — Read Before Touching Anything

1. **Library is `pyrofork==2.3.45`** — imports as `from pyrogram import ...`. NEVER change imports to `pyrofork`. NEVER upgrade without testing.
2. **Never use the same SESSION_STRING in two simultaneous clients** — Telegram will kill one with `AUTH_KEY_DUPLICATED`.
3. **Never use `console.log` or `print` for logging** — use the `logger` singleton from `logging.getLogger()`.
4. **`seen_db.py` uses a module-level in-memory cache** (`_cache`) — changes write-through to `seen.json` but are lost if `_cache = None` is reset.
5. **`get_destination(filename, source_chat)`** — always pass BOTH args. Passing only one arg silently routes everything to the default channel.
6. **Railway filesystem is ephemeral** — mount a persistent volume at `/app` so JSON databases survive redeploys.

---

## Repository Structure

```
tg-file-forwarder/
├── forwarder.py          Main single-account entry point (992 lines)
├── multi_forwarder.py    Multi-account entry point (850+ lines after S10 fix)
├── pm_bot_forwarder.py   NEW S10: PM deep-link capture bot
├── bot_capture.py        Bot-only mode (watches specific bots in groups)
├── bulk_dump.py          Scan group history and forward all past files
├── session_gen.py        Helper to generate SESSION_STRING interactively
│
├── utils.py              safe_forward() — single forwarding entry point
├── router.py             get_destination() — multi-channel routing by type
├── seen_db.py            file_unique_id dedup (seen.json)
├── failed_db.py          failed-forward recovery (failed.json)
├── chats_db.py           source chat management (chats.json)
├── stats_db.py           per-source forwarding counters (stats.json)
├── caption_cleaner.py    watermark strip patterns
├── caption_suffix.py     per-file caption suffix (caption_suffix.json)
├── strip_patterns.py     regex strip patterns (strip_patterns.json)
├── ignore_db.py          per-source ignore list (ignored.json)
├── keyword_filter.py     allow/block keyword filter (keywords.json)
├── account_pool.py       multi-account FloodWait rotation
├── bots_db.py            bot-group mapping for bot_capture.py (bots.json)
├── discovery.py          /discover and /suggest — find new source groups
├── dashboard.py          aiohttp web dashboard (served on PORT)
│
├── config.py             reads all env vars, exports constants
├── railway.toml          startCommand = "python forwarder.py"
├── Dockerfile            python:3.11-slim, exposes 8080
├── requirements.txt      pyrofork==2.3.45, tgcrypto-pyrofork, etc.
└── AGENT_SESSION_NOTES.md  this file
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `API_ID` | ✅ | Telegram API ID from my.telegram.org |
| `API_HASH` | ✅ | Telegram API hash |
| `SESSION_STRING` | ✅ | Pyrogram session string for account 1 |
| `DEST_CHANNEL` | ✅ | Default destination channel ID (negative int) |
| `ADMIN_IDS` | ✅ | Comma-separated Telegram user IDs of admins |
| `SOURCE_CHATS` | ✅ | Comma-separated source group IDs/usernames |
| `LOG_CHANNEL` | optional | Channel for startup/error notifications |
| `DEST_MOVIES` | optional | Separate channel for standalone movies |
| `DEST_SERIES` | optional | Separate channel for TV series |
| `DEST_SOUTH` | optional | Separate channel for South Indian films |
| `SESSION_STRING_2..5` | optional | Extra accounts for multi_forwarder.py |
| `SOURCE_BOTS` | pm only | Comma-separated bot usernames to watch, e.g. `NarutoXMoviesBot,HDMoviesBot` |
| `SOURCE_BOT` | pm only | Legacy fallback if `SOURCE_BOTS` not set (default: NarutoXMoviesBot) |
| `SOURCE_GROUPS` | pm only | Groups to watch for PM bot results (empty = all groups) |
| `PM_DELAY` | pm only | Seconds between PM start commands (default: 4) |

---

## Deployment (Railway)

### Main forwarder
```
Repo: risbahh/tg-file-forwarder
Start command: python forwarder.py
```

### PM Bot Forwarder (separate Railway service, same repo)
```
Repo: risbahh/tg-file-forwarder
Start command: python pm_bot_forwarder.py
Extra env: SOURCE_BOTS (or SOURCE_BOT), SOURCE_GROUPS, PM_DELAY
```

**Important:** Mount a Railway Volume at `/app` so JSON databases (`seen.json`, `failed.json`, `pm_processed.json`, etc.) survive redeploys.

---

## Data Flow

### forwarder.py
```
New message in source group
  → is_allowed_file() check (document/video/audio)
  → ignore_db check (is this source ignored?)
  → keyword_filter check (allow/block by filename)
  → is_seen(file_unique_id) dedup check
  → get_destination(filename, chat_id) routing
  → safe_forward(message, dest)
      → caption strip + suffix append
      → forward with FloodWait retry
      → mark_seen(file_unique_id) on success
      → failed_db.save(chat_id, msg_id, dest) on total failure
```

### pm_bot_forwarder.py
```
Bot result message in source group (from @NarutoXMoviesBot)
  → extract_deeplinks() — parse t.me/Bot?start=files_XXXXX from buttons/text
  → _enqueue(bot_username, start_param)
      → check pm_processed.json (skip if already sent)
      → queue.put()
  → _queue_worker() (rate-limited, PM_DELAY between sends)
      → app.send_message(bot_username, "/start " + start_param)
  → Bot sends files back in PM
  → on_pm_file() fires
      → check seen_db (skip if already forwarded)
      → mark_seen() before forwarding (prevents race condition)
      → message.forward(DEST_CHANNEL)
```

---

## All Commands (forwarder.py + multi_forwarder.py)

Both entry points support the same commands. DM the userbot account to use them.

### Source Management
| Command | Description |
|---------|-------------|
| `/addchat <id>` | Add a source group |
| `/removechat <id>` | Remove a source group |
| `/listchats` | List all source groups |
| `/joinchat <link>` | Join a group and add it |
| `/ignorechat <id>` | Pause forwarding from a source |
| `/unignorechat <id>` | Resume a paused source |
| `/listignored` | Show all ignored sources |

### Stats
| Command | Description |
|---------|-------------|
| `/fwrstatus` | Full session stats |
| `/srcstats` | Files per source group |
| `/dupstats` | Duplicate detection stats |
| `/resetdups confirm` | Clear seen.json |
| `/failedstats` | Show failed.json entries |
| `/export` | Download stats as CSV |

### Routing
| Command | Description |
|---------|-------------|
| `/route <src> <dest>` | Set per-source destination override |
| `/routes` | List all routing overrides |

### Pause/Resume
| Command | Description |
|---------|-------------|
| `/pausefwd` | Stop forwarding new files |
| `/resumefwd` | Resume forwarding |

### Captions
| Command | Description |
|---------|-------------|
| `/setcaption <text\|off>` | Set/remove caption suffix |
| `/strippatterns add/remove/list` | Manage watermark strip regexes |
| `/cleancaptions [channel_id]` | Edit existing captions in bulk |
| `/stopcleaning` | Cancel the clean job |

### Recovery
| Command | Description |
|---------|-------------|
| `/retry` | Retry all entries in failed.json |
| `/clearfailed confirm` | Wipe failed.json |

### Filters
| Command | Description |
|---------|-------------|
| `/keywords list/allow/block/remove/off` | Keyword filter management |

### Discovery
| Command | Description |
|---------|-------------|
| `/discover` | Scan joined groups for movie sources |
| `/suggest <query>` | Search public groups |

### Multi-account only (multi_forwarder.py)
| Command | Description |
|---------|-------------|
| `/poolstatus` | Account pool health |
| `/assignsource <src> <account_n>` | Pin a source to a specific account |
| `/unassignsource <src>` | Remove account assignment |
| `/assignments` | List source→account assignments |

### PM Bot Forwarder only (pm_bot_forwarder.py)
| Command | Description |
|---------|-------------|
| `/dumpbot <group> [limit]` | Scan history, queue all past deep links from ALL watched bots |
| `/pmstatus` | Queue, forwarded, skipped counts + per-bot breakdown |
| `/listbots` | Show all currently watched bots |
| `/pmclear confirm` | Reset processed-links cache (file dedup untouched) |

---

## Bugs Fixed (All Sessions)

| Session | File | Bug | Fix |
|---------|------|-----|-----|
| 1–6 | Various | Duplicate detection, FloodWait, caption, stats, session revoke | Various |
| 7 | `failed_db.py` | auto-retry save/load broken | Rewrote save/load |
| 8 | `forwarder.py` | `failed_db.all()` → `.load()`; `item["msg_id"]` → `["message_id"]` | Fixed key names |
| 8 | `multi_forwarder.py` | Missing `ignore_db` + `keyword_filter` in new-file handler | Added checks |
| 8 | `multi_forwarder.py` | Missing auto-retry on startup | Added auto-retry block |
| 9 | `account_pool.py` | `_pick()` always picked idx 0 — not true round-robin | Fixed round-robin |
| 10 | `failed_db.py` | `save()` never stored `dest` field — retried files always went to DEST_CHANNEL | Added `dest: int = 0` param |
| 10 | `utils.py` | `failed_db.save()` called without `dest` arg | Passed `dest` |
| 10 | `forwarder.py` | `/retry` called `get_destination(str(chat_id))` — chat_id as filename | Fixed to `get_destination(get_file_name(msg), int(chat_id))` |
| 10 | `multi_forwarder.py` | 22 commands listed in docstring were completely absent from code | Added all handlers |
| 10 | `multi_forwarder.py` | Auto-retry used `_pool.forward()` (round-robin only) instead of `_pool.forward_from_source()` | Fixed |
| 10 | `pm_bot_forwarder.py` | `on_any_message` used bare `filters.incoming` — processed PMs unnecessarily | Changed to `filters.group & filters.incoming` |
| 10 | `pm_bot_forwarder.py` | `_save_processed` called on every enqueue — thousands of disk writes during dumpbot | Batched every 50 items + run_in_executor |
| 10 | `pm_bot_forwarder.py` | `_queue` type annotation only, no value — potential NameError | Initialized as `None`, set in `main()` |
| 10 | `pm_bot_forwarder.py` | `_admin_only` decorator didn't guard against `message.from_user` being None | Added None guard |
| 10 | `failed_db.py` | Docstring entry format missing `dest` field | Updated docstring |
| 10 | `pm_bot_forwarder.py` | `asyncio.get_event_loop()` deprecated since Python 3.10 — used in 3 places | Replaced with `asyncio.get_running_loop()` |
| 10 | `pm_bot_forwarder.py` | Only watched single `SOURCE_BOT` — missed all other auto-filter bots | Added `SOURCE_BOTS` multi-bot env var + `_SOURCE_BOT_SET` |
| 10 | `pm_bot_forwarder.py` | Dedup key was bare `start_param` — BotA:files_123 and BotB:files_123 treated as same | Key changed to `"botname:start_param"` |
| 10 | `pm_bot_forwarder.py` | `_admin_only` had no `functools.wraps` — stripped `__doc__`, `__module__` from handlers | Added `@wraps(func)` |
| 10 | `pm_bot_forwarder.py` | `/pmstatus`, `/pmclear`, `/dumpbot`, `/help` all referenced single SOURCE_BOT | All updated to show full bot list + per-bot stats |

---

## What to Build Next

1. ~~**Multi-source bot support in `pm_bot_forwarder.py`**~~ ✅ **DONE (Session 10)** — `SOURCE_BOTS` env var, multi-bot set lookup, per-bot stats.

2. **Quality routing in `pm_bot_forwarder.py`** — the PM bot sends multiple quality versions of each movie (480p, 720p, 1080p). Add `PREFERRED_QUALITY=1080p` env var to filter which versions get forwarded.

3. **`/schedule off HH:MM HH:MM`** — quiet-hours auto-pause. User declined for PM forwarder (wants 24/7) but may be useful for `forwarder.py` to reduce Railway compute costs during off-hours.

4. **Railway Volume reminder** — user must mount a volume at `/app` so JSON databases survive redeploys. Without it, `seen.json` resets on every deploy and files get re-forwarded.

5. **`/balancepool`** — redistribute source groups evenly across pool accounts in `multi_forwarder.py`.

6. **Per-source keyword filters** (not global) — current `/keywords` is global. Some sources post non-movie content; per-source filters would help.

7. **Session watchdog for `pm_bot_forwarder.py`** — `forwarder.py` has a session watchdog that pings Telegram every 5 min and alerts admins if the session is revoked. `pm_bot_forwarder.py` is missing this — add it so Railway knows to alert on session revoke.

---

## GitHub & Deployment Info

- **Main repo:** `azizthekiller123/tg-file-forwarder` (owner's account)
- **Deploy repo:** `risbahh/tg-file-forwarder` (fork used for Railway deployment)
- **GitHub token in Replit secret:** `GITHUB_PERSONAL_ACCESS_TOKEN` → currently set to `risbahh` account
- **Railway auto-deploys** from `risbahh/tg-file-forwarder` on every push to `main`
- **Push method:** Use Node.js GitHub API calls (no `git commit` in main agent)

### How to push a fix:
```javascript
// In bash via node:
node -e "
const fs = require('fs'), https = require('https');
const token = process.env.GITHUB_PERSONAL_ACCESS_TOKEN;
const repo = 'risbahh/tg-file-forwarder';
// ... apiCall() helper ... pushFile(remotePath, localPath, message)
"
```

---

## Session String Generation (No PC)

The user is in India where `my.telegram.org` may be blocked. Workarounds:
1. **API_ID/API_HASH are reusable** — one Telegram app covers all accounts. No need to create a new app per account.
2. **Generate session string on mobile** — use Replit Python repl at replit.com in mobile browser:
   ```python
   from pyrogram import Client
   import asyncio
   async def main():
       async with Client("s", api_id=YOUR_API_ID, api_hash="YOUR_API_HASH", in_memory=True) as app:
           print(await app.export_session_string())
   asyncio.run(main())
   ```
   Install: `pip install pyrofork tgcrypto-pyrofork`

---

## Known Limitations

- **Railway ephemeral filesystem** — all JSON databases (`seen.json`, `failed.json`, `pm_processed.json`, etc.) reset on redeploy unless a Volume is mounted at `/app`.
- **PM bot 10-minute deletion** — NarutoXMoviesBot deletes files from PM after 10 minutes. The PM forwarder must be running 24/7 to capture them in time. Do not pause it.
- **Same SESSION_STRING cannot run in two clients** — if running both `forwarder.py` and `pm_bot_forwarder.py`, they MUST use different session strings (different Telegram accounts).
- **FloodWait on bulk dump** — `/dumpbot` with large limits (50k+) may trigger Telegram FloodWait on the PM send queue. The `PM_DELAY` setting (default 4s) helps but very large dumps may still be slow.
