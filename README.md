# 📡 TG File Forwarder

A production-ready Telegram **userbot** that silently captures movie/series files from source groups and feeds them into your auto-filter bot's private index channel — building your database on autopilot, 24/7.

> **For agents:** Read this entire file before making changes. See [Agent Reference](#-agent-reference) at the bottom for rules, known bugs, and what to build next.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Why Userbot Not Bot](#why-userbot-not-bot)
- [Capture Modes](#capture-modes)
- [File Structure](#file-structure)
- [Quick Setup](#quick-setup)
- [Environment Variables](#environment-variables)
- [forwarder.py Commands](#forwarderpy-commands)
- [bot_capture.py Commands](#bot_capturepy-commands)
- [Multi-Destination Routing](#multi-destination-routing)
- [Duplicate Detection](#duplicate-detection)
- [Caption Watermark Removal](#caption-watermark-removal)
- [Multi-Account Forwarding](#multi-account-forwarding)
- [Source Auto-Discovery](#source-auto-discovery)
- [Web Dashboard](#web-dashboard)
- [Bulk History Dump](#bulk-history-dump)
- [Bugs Fixed](#bugs-fixed)
- [Feature Roadmap](#feature-roadmap)
- [Agent Reference](#-agent-reference)

---

## How It Works

```
[Cine Alliance Group]  ──┐
[Movies HD Hub]         ──┼──▶  forwarder.py  ──▶  router.py  ──▶  [DEST_MOVIES]
[Any Source Chat]      ──┘                     │               ──▶  [DEST_SERIES]
                                               └──▶  seen_db  ──▶  [DEST_SOUTH]
                                                    (dedup)
                               bot_capture.py ──▶  (same routing, only bot files)
```

1. Userbot joins source groups as a **normal member** (no admin needed)
2. Every file posted → `router.py` decides which index channel it belongs in
3. `seen_db.py` checks `file_unique_id` — skips if already forwarded (cross-group dedup)
4. `caption_cleaner.py` strips `@watermarks` and `t.me/` links before forwarding
5. Your auto-filter bot indexes the file → users can search it immediately

**Connected repo:** [Auto-filter-bot-4](https://github.com/azizthekiller123/Auto-filter-bot-4) — this forwarder feeds that bot's index channel(s).

---

## Why Userbot Not Bot

You **cannot** add a regular Telegram bot to someone else's group without admin permission (`CHAT_ADMIN_REQUIRED` error). A **userbot** is a regular Telegram account running as a script — it joins groups exactly like a human member, no permission needed.

| | Regular Bot | Userbot (this repo) |
|---|---|---|
| Join other groups | ❌ Needs admin | ✅ Join like any user |
| Read message history | ❌ Only if added first | ✅ Full access |
| Visible to group admins | ✅ Appears as a bot | ✅ Appears as normal user |
| Requires `SESSION_STRING` | ❌ | ✅ Generate with `session_gen.py` |

---

## Capture Modes

This repo has two capture scripts. Run one or both:

| Mode | Script | Captures | Use when |
|---|---|---|---|
| **All-file** | `forwarder.py` | Every file from watched groups | Maximum coverage — user uploads + bot files |
| **Bot-only** | `bot_capture.py` | Only files sent by a specific bot per group | Only clean verified bot-served movie files |
| **Multi-account** | `multi_forwarder.py` | Same as forwarder.py but uses account pool | FloodWait is a problem, need 2–3× throughput |

**Recommended for Cine Alliance-style groups:**
- Use `bot_capture.py` — captures only what the group's auto-filter bot sends, skips all user spam

**Recommended for general movie groups:**
- Use `forwarder.py` — captures everything, `seen_db` handles dedup

---

## File Structure

```
tg-file-forwarder/
│
├── forwarder.py         # ★ ALL-FILE CAPTURE — main entry point
│                        #   Real-time watcher: all files from watched groups
│                        #   Integrates: routing, dedup, caption cleaning, dashboard
│                        #   Commands: /addchat /removechat /route /dupstats /discover /suggest
│
├── bot_capture.py       # ★ BOT-ONLY CAPTURE
│                        #   Only forwards files sent by a registered target bot
│                        #   Commands: /setbot /removebot /listbots /capturestatus
│
├── multi_forwarder.py   # ★ MULTI-ACCOUNT CAPTURE
│                        #   Same as forwarder.py but uses AccountPool for FloodWait rotation
│                        #   Replace forwarder.py in Procfile or run as worker2
│                        #   Commands: /addchat /route /routes /poolstatus
│
├── account_pool.py      # Multi-account FloodWait rotation engine
│                        #   AccountPool.create() loads SESSION_STRING, _2, _3 from env
│                        #   pool.forward(message, dest) auto-rotates on FloodWait
│                        #   status() shows per-account stats and flood state
│
├── router.py            # Multi-destination routing
│                        #   get_destination(filename, source_chat) → correct channel ID
│                        #   detect_type() → "series" | "south" | "movie" via filename regex
│                        #   Per-source overrides stored in routing.json (/route command)
│                        #   Env vars: DEST_MOVIES, DEST_SERIES, DEST_SOUTH, DEST_CHANNEL
│
├── seen_db.py           # Duplicate detection
│                        #   Tracks file_unique_id in seen.json (in-memory cached set)
│                        #   is_seen() / mark_seen() used by safe_forward() in utils.py
│                        #   Same file in two groups → forwarded once, skipped once
│
├── caption_cleaner.py   # Caption watermark remover
│                        #   Strips @username, t.me/, [TamilMV], "Powered by" etc.
│                        #   clean(caption) → cleaned string or None
│                        #   Controlled by CLEAN_CAPTIONS env var (default: true)
│
├── discovery.py         # Source auto-discovery
│                        #   find_joined_sources() — scans joined groups for movie sources
│                        #   search_public_sources() — searches Telegram for public groups
│                        #   Called by /discover and /suggest commands in forwarder.py
│
├── dashboard.py         # Live web status page (aiohttp)
│                        #   GET /           → HTML dashboard (auto-refreshes every 30s)
│                        #   GET /api/stats  → JSON stats
│                        #   GET /health     → {"status":"ok"}
│                        #   start_dashboard() is an async task started by forwarder.py
│
├── bots_db.py           # Bot-group mapping for bot_capture.py
│                        #   set_bot() / remove_bot() / get_bot_by_chat_id()
│                        #   Persists to bots.json
│
├── bulk_dump.py         # One-time historical dump
│                        #   Iterates entire chat history, forwards all files
│                        #   Resume-safe: saves progress to forwarded.json
│
├── chats_db.py          # Dynamic source chat list
│                        #   add_chat() / remove_chat() — updated by /addchat
│                        #   Persists to chats.json
│
├── utils.py             # Shared helpers (used by all capture scripts)
│                        #   safe_forward(msg, dest, skip_duplicates=True, clean_captions=True)
│                        #   Handles FloodWait, dedup check, caption cleaning in one call
│                        #   get_unique_id() / is_allowed_file() / get_file_name() / human_size()
│
├── tracker.py           # Bulk dump progress tracker
│                        #   is_done(id) / mark_done(id) — skips already-forwarded messages
│
├── config.py            # All settings from environment variables
│                        #   SystemExit with clear message if required vars are missing
│
├── session_gen.py       # One-time session string generator
│                        #   Run locally once: python session_gen.py
│
├── requirements.txt     # pyrofork==2.3.45, tgcrypto-pyrofork, python-dotenv, aiohttp
├── Procfile             # Railway: worker: python forwarder.py
├── railway.toml         # Railway: restartPolicyType: always
├── .env.example         # All env vars documented with examples
└── AGENT_SESSION_NOTES.md  # Full project context for agents
```

---

## Quick Setup

### Step 1 — Get Telegram API Credentials
Go to [my.telegram.org](https://my.telegram.org) → **API Development Tools** → copy `API_ID` and `API_HASH`.

> Use a **secondary Telegram account**, not your main.

### Step 2 — Generate Session String (run locally, one time only)
```bash
pip install pyrofork tgcrypto-pyrofork python-dotenv
python session_gen.py
```
Enter phone number, paste the verification code → copy the printed `SESSION_STRING` (starts with `BQA...`).

> ⚠️ **Never commit SESSION_STRING to git.** It grants full account access.

### Step 3 — Get Your Destination Channel ID
Forward any message from the channel to [@userinfobot](https://t.me/userinfobot) → copy the `id` (negative number like `-1001234567890`).

### Step 4 — Find Your Telegram User ID
Message [@userinfobot](https://t.me/userinfobot) → copy your `id` → this is your `ADMINS` value.

### Step 5 — Join Source Groups
Log into Telegram with your secondary account and join every group you want to capture from.

### Step 6 — Deploy to Railway

1. Fork this repo → [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**
2. In Railway → **Variables** tab, add:

```
API_ID          = (from my.telegram.org)
API_HASH        = (from my.telegram.org)
SESSION_STRING  = (from session_gen.py)
DEST_CHANNEL    = -1001234567890
SOURCE_CHATS    = CineAlliance,MoviesHDHub
ADMINS          = 123456789
```

3. Optional — set up routing:
```
DEST_MOVIES = -1001111111111
DEST_SERIES = -1002222222222
DEST_SOUTH  = -1003333333333
```

4. Railway auto-deploys. Check **Deployments** tab for ✅ Active (~2 min)
5. Visit `https://your-app.railway.app/` → see the live dashboard
6. DM the userbot: `/addchat CineAlliance` to add more sources at runtime

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_ID` | ✅ | — | From my.telegram.org |
| `API_HASH` | ✅ | — | From my.telegram.org |
| `SESSION_STRING` | ✅ | — | Run session_gen.py once locally |
| `DEST_CHANNEL` | ✅ | — | Default/fallback index channel ID |
| `SOURCE_CHATS` | ⚡ | — | Comma-separated sources. Also add via /addchat |
| `ADMINS` | ⚡ | — | Your Telegram user ID. Comma-separated for multiple |
| `DEST_MOVIES` | ⚡ | DEST_CHANNEL | Standalone movies routed here |
| `DEST_SERIES` | ⚡ | DEST_CHANNEL | TV series (S01E01 pattern) routed here |
| `DEST_SOUTH` | ⚡ | DEST_CHANNEL | South Indian / dubbed films routed here |
| `SESSION_STRING_2` | ⚡ | — | Second account for multi_forwarder.py |
| `SESSION_STRING_3` | ⚡ | — | Third account for multi_forwarder.py |
| `CLEAN_CAPTIONS` | ⚡ | `true` | Strip @watermarks from captions |
| `DELAY` | ⚡ | `3` | Seconds between each forward |
| `FLOOD_EXTRA` | ⚡ | `5` | Extra seconds added on top of FloodWait |
| `MAX_RETRIES` | ⚡ | `5` | Retries before skipping a file |
| `BATCH_SIZE` | ⚡ | `200` | Messages per API call in bulk_dump.py |
| `ALLOWED_TYPES` | ⚡ | `document,video` | File types: document, video, audio, photo |
| `LOG_CHANNEL` | ⚡ | — | Startup/addchat notifications sent here |
| `TRACKER_FILE` | ⚡ | `forwarded.json` | Bulk dump progress |
| `CHATS_DB_FILE` | ⚡ | `chats.json` | Dynamic source list |
| `BOTS_DB_FILE` | ⚡ | `bots.json` | Bot-group mapping (bot_capture.py) |
| `SEEN_DB_FILE` | ⚡ | `seen.json` | Duplicate detection store |
| `ROUTING_FILE` | ⚡ | `routing.json` | Per-source routing overrides |

---

## forwarder.py Commands

DM the userbot account directly. All commands are admin-only (`ADMINS` env var).

| Command | Description |
|---|---|
| `/addchat <username or id>` | Add a source group — takes effect instantly |
| `/removechat <username or id>` | Stop forwarding from a group |
| `/listchats` | Show all active sources |
| `/fwrstatus` | Full stats: forwarded, dedup, routing, caption clean |
| `/route <source> <channel>` | Override destination for a specific source |
| `/routes` | Show all routing rules and auto-detect config |
| `/dupstats` | Duplicate detection stats (seen DB size, session skips) |
| `/discover` | Scan joined groups — find ones that look like movie sources |
| `/suggest <keyword>` | Search Telegram for public groups matching keyword |
| `/help` | Show all commands |

**Examples:**
```
/addchat CineAlliance
/route CineAlliance -1001111111111
/routes
/dupstats
/discover
/suggest 4k movies hindi dubbed
```

---

## bot_capture.py Commands

| Command | Description |
|---|---|
| `/setbot <group> <bot_username>` | Register which bot to watch — auto-resolves bot ID |
| `/removebot <group>` | Stop targeted capture for a group |
| `/listbots` | Show all registered bot-group pairs |
| `/capturestatus` | Live capture stats: captured, skip rate, session totals |
| `/help` | Show all commands |

**Example:**
```
/setbot CineAlliance CineAllianceBotUsername
/listbots
/capturestatus
```

**How /setbot works:** Calls `client.get_users("CineAllianceBotUsername")` → resolves numeric ID → saves `{group_id: {bot_id, bot_username}}` to `bots.json`. From then on every message in that group is checked — only forwards if `sender.id == registered_bot_id` AND message has a file.

---

## Multi-Destination Routing

Route different content types to different index channels automatically:

```
Movie file       → DEST_MOVIES   (e.g. "Inception 2010 1080p.mkv")
Series episode   → DEST_SERIES   (e.g. "Breaking Bad S05E14.mkv")
South Indian     → DEST_SOUTH    (e.g. "KGF Chapter 2 Hindi Dubbed.mkv")
Fallback         → DEST_CHANNEL  (everything else)
```

**Detection patterns (router.py):**
- **Series:** `S01E01`, `S01`, `Season 1`, `Episode 3`, `Complete`
- **South:** `Tamil`, `Telugu`, `Malayalam`, `Kannada`, `Hindi Dubbed`, `South Indian`
- **Movie:** anything that doesn't match the above

**Per-source override:** `/route CineAlliance -1001111111111` — all files from Cine Alliance go to that specific channel regardless of filename.

**View all rules:** `/routes`

---

## Duplicate Detection

Every forwarded file is tracked by its Telegram `file_unique_id` in `seen.json`.

- Same movie posted in both Cine Alliance and Movies Hub → forwarded once, skipped once
- Works across sessions — `seen.json` survives restarts (on Railway persistent disk)
- `safe_forward()` in `utils.py` runs the check automatically — no extra code needed in any script
- `/dupstats` shows session skip count and total seen DB size

**How it works:**
```python
uid = message.document.file_unique_id  # Telegram's global unique key
if uid in seen.json:
    skip()
else:
    forward() → add uid to seen.json
```

---

## Caption Watermark Removal

Before forwarding, `caption_cleaner.py` strips all promotional content from the file caption:

**Removed patterns:**
- `@channel_username` mentions
- `t.me/channel_link` links
- `https://...` and `http://...` URLs
- `[TamilMV]`, `[www.1337x.to]`, `[MoviesHub]` tags
- Lines starting with: `Powered by`, `Source:`, `Join:`, `For more movies`, `Visit us`
- Separator lines (`---`, `•••`)

**Toggle:** set `CLEAN_CAPTIONS=false` to disable (default: `true`).

This uses `message.copy(dest, caption=cleaned)` instead of `message.forward(dest)` when there's a caption to clean. The file itself is unchanged — only the caption text is cleaned.

---

## Multi-Account Forwarding

When Account 1 hits FloodWait, `account_pool.py` instantly switches to Account 2:

```
Account 1 → FloodWait 60s → Account 2 takes over
Account 2 → FloodWait 30s → Account 3 takes over
Account 3 → FloodWait 20s → Account 1 available again
```

**Setup:**
```
SESSION_STRING   = BQA...  ← Account 1 (already set)
SESSION_STRING_2 = BQA...  ← Account 2
SESSION_STRING_3 = BQA...  ← Account 3
```

**Deploy:**

Option A — Replace `forwarder.py` in `Procfile`:
```
worker: python multi_forwarder.py
```

Option B — Run as a second worker alongside `forwarder.py`:
```
worker:  python forwarder.py
worker2: python multi_forwarder.py
```

**Command:** `/poolstatus` — shows per-account forwarded count, FloodWait state, and availability.

---

## Source Auto-Discovery

### /discover — scan your joined groups
```
/discover
```
Scans all groups the userbot is already a member of. Returns groups with movie-related titles/descriptions, sorted by member count.

### /suggest — search Telegram
```
/suggest 4k movies
/suggest hindi dubbed 1080p
/suggest south indian movies
```
Calls `client.search_public_chats(query)` to find public groups. Returns results sorted by members.

Both commands show: group name, @username or ID, member count. Use `/addchat <username>` to add any result immediately.

---

## Web Dashboard

After deploying to Railway, visit your app URL to see the live status page:

```
https://your-app.railway.app/           → HTML dashboard
https://your-app.railway.app/api/stats  → JSON stats
https://your-app.railway.app/health     → health check
```

**Dashboard shows:**
- Files forwarded this session
- Total unique files in seen DB (all-time)
- Duplicates skipped
- Failed forwards
- Active source count
- Uptime
- Routing config (which channel gets which content type)
- All active source chats

Auto-refreshes every 30 seconds. The dashboard runs as an `asyncio.create_task()` inside `forwarder.py` on the same process — no extra worker needed.

---

## Bulk History Dump

Pull ALL historical files from a group (runs once, can be resumed):

```bash
python bulk_dump.py                  # dump all SOURCE_CHATS
python bulk_dump.py CineAlliance     # dump specific group
python bulk_dump.py -100987654321
```

- Saves progress to `forwarded.json` after every file — safe to interrupt with Ctrl+C
- Resumes exactly where it stopped on next run
- Can take hours for large groups — run overnight
- Sends summary to `LOG_CHANNEL` when complete

**Typical workflow for a new source:**
```bash
# 1. First: dump all history (run once)
python bulk_dump.py CineAlliance

# 2. Then: add to real-time forwarder
/addchat CineAlliance
```

---

## Bugs Fixed

| Date | File | Bug | Fix |
|---|---|---|---|
| 2026-06-21 | `forwarder.py` | `filters.all` crash at startup — does not exist in pyrofork | Removed `filters.all &` — specific type filters are sufficient |
| 2026-06-21 | `misc.py` (main bot) | `os.remove()` not in try/except → crash if file already deleted | Wrapped in `try/except OSError` |

---

## Feature Roadmap

### ✅ Built

| Feature | File(s) | Notes |
|---|---|---|
| Real-time all-file capture | `forwarder.py` | /addchat at runtime, no redeploy |
| Bot-targeted capture | `bot_capture.py` | /setbot auto-resolves bot ID |
| Multi-account FloodWait rotation | `multi_forwarder.py`, `account_pool.py` | SESSION_STRING_2/3 |
| Multi-destination routing | `router.py` | DEST_MOVIES/SERIES/SOUTH + /route override |
| Duplicate detection | `seen_db.py`, `utils.py` | file_unique_id, cross-group dedup |
| Caption watermark removal | `caption_cleaner.py`, `utils.py` | 8 regex patterns, CLEAN_CAPTIONS toggle |
| Source auto-discovery | `discovery.py` | /discover (joined) + /suggest (search) |
| Web dashboard | `dashboard.py` | aiohttp at PORT, auto-refresh, JSON API |
| Bulk history dump (resume-safe) | `bulk_dump.py` | forwarded.json progress tracking |
| Bot-group mapping storage | `bots_db.py` | bots.json persistence |
| Dynamic source management | `chats_db.py` | chats.json, /addchat at runtime |

---

### 🔴 Remaining — Build Next

#### 1. Auto-Retry Failed Forwards _(not built)_
- Store failed message IDs in `failed.json`
- `/retry` command re-attempts all failed forwards
- Auto-retry on startup
- **Implementation:** extend `tracker.py` with a `failed` dict `{chat_id: [msg_id, ...]}`

#### 2. Daily Stats Report _(not built)_
- Auto-post summary to `LOG_CHANNEL` at midnight:
  ```
  📊 Daily Report — 21 June 2026
  Files forwarded: 847 | Duplicates skipped: 124 | Failed: 3
  Top source: CineAlliance (412 files) | DEST_MOVIES: 601, DEST_SERIES: 246
  ```
- **Implementation:** `apscheduler` (already in main bot) + collect per-source counters in `_stats`

#### 3. File Size Filter _(not built)_
- `MIN_SIZE_MB=10` — skip corrupt tiny files under 10MB
- `MAX_SIZE_MB=4000` — skip files over 4GB
- **Implementation:** 4 lines in `is_allowed_file()` in `utils.py`

#### 4. Quality & Language Filter _(not built)_
- `ALLOWED_QUALITIES=1080p,4K,720p` — skip 360p/480p
- `BLOCKED_LANGUAGES=Telugu` — exclude specific dubs by filename
- **Implementation:** extend `is_allowed_file()` in `utils.py` with regex checks on filename

#### 5. Scheduled Bulk Dump _(not built)_
- `BULK_SCHEDULE=02:00` — run bulk dump nightly at 2 AM
- **Implementation:** `apscheduler` + call `dump_chat()` from `bulk_dump.py`

---

## 🤖 Agent Reference

This section is for AI agents picking up this project. Read before making any changes.

### Connected Repos
| Repo | Purpose |
|---|---|
| [Auto-filter-bot-4](https://github.com/azizthekiller123/Auto-filter-bot-4) | Main Telegram movie search bot |
| [tg-file-forwarder](https://github.com/azizthekiller123/tg-file-forwarder) | This repo — feeds the main bot's index channel(s) |

### How to Push to GitHub
Always **sequential** pushes — parallel pushes cause SHA conflicts:
```bash
SHA=$(curl -s -H "Authorization: token $GITHUB_PERSONAL_ACCESS_TOKEN" \
  "https://api.github.com/repos/azizthekiller123/tg-file-forwarder/contents/FILE" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['sha'])")

CONTENT=$(base64 -w 0 local_file.py)
curl -s -X PUT -H "Authorization: token $GITHUB_PERSONAL_ACCESS_TOKEN" \
  "https://api.github.com/repos/azizthekiller123/tg-file-forwarder/contents/FILE" \
  -d "{\"message\":\"fix: description\",\"content\":\"$CONTENT\",\"sha\":\"$SHA\"}"
```

**Exception:** new files (not yet in the repo) can be pushed in parallel since they have no SHA.

### Testing Before Pushing
```bash
python3 -c "
import ast, glob
for p in glob.glob('/tmp/fwdr/*.py'):
    src = open(p).read()
    try:
        ast.parse(src)
        if 'filters.all' in src: print(f'CRITICAL {p}: filters.all')
        else: print(f'OK  {p}')
    except SyntaxError as e:
        print(f'ERR {p} line {e.lineno}: {e.msg}')
"
```

### Critical Rules
1. **Library is `pyrofork==2.3.45`** — imports as `from pyrogram import ...`. Never change to `pyrofork`.
2. **`filters.all` does not exist** in pyrofork — use `filters.document | filters.video | filters.audio`.
3. **`safe_forward()` in `utils.py`** must always be used — it handles FloodWait, dedup, and caption cleaning in one call.
4. **Never commit `SESSION_STRING`** — Railway env var only.
5. **Push files sequentially** for updates; new files can be pushed in parallel.
6. **`get_bot_by_chat_id()`** in `bots_db.py` — always use this (not `get_bot()`) inside message handlers where you have a numeric chat ID.
7. **`forwarder.py` and `bot_capture.py` can overlap** — if a group is in both `chats.json` and `bots.json`, a file may forward twice. Keep sources exclusive between the two scripts. Dedup in `seen_db.py` will catch the second attempt via `file_unique_id`.
8. **`dashboard.py` runs as `asyncio.create_task()`** inside `forwarder.py` — NOT a separate process. It shares the same event loop.
9. **`routing.json`, `chats.json`, `bots.json`, `seen.json`** are on Railway's ephemeral filesystem. They reset on full redeploy. For production persistence, use Railway's persistent volume or attach a database.
10. **`CLEAN_CAPTIONS=true`** causes `message.copy()` instead of `message.forward()`. The auto-filter bot indexes by filename, not caption — this is safe. The file itself is unchanged.
11. **New features** must add env vars to `.env.example` and document them in the Variables table above.

### What to Build Next (Recommended Order)
1. **Auto-retry failed** — extend `tracker.py`, add `/retry` to `forwarder.py` and `bot_capture.py`
2. **File size filter** — 4 lines in `is_allowed_file()` in `utils.py` for `MIN_SIZE_MB`/`MAX_SIZE_MB`
3. **Quality/language filter** — extend `is_allowed_file()` with `ALLOWED_QUALITIES`/`BLOCKED_LANGUAGES` env vars
4. **Daily stats report** — `apscheduler` midnight task posting to `LOG_CHANNEL`
5. **Scheduled bulk dump** — `apscheduler` nightly run of `bulk_dump.py`

### Known Limitations
- JSON files (`seen.json`, `chats.json`, etc.) on Railway's ephemeral disk reset on full redeploy. Use Railway's persistent storage or MongoDB to survive redeploys.
- `seen.json` is loaded into memory on first use (`_cache` in `seen_db.py`) — if the process restarts, the cache reloads from disk automatically.
- `forwarder.py` and `bot_capture.py` both import `utils.safe_forward` — improvements to `utils.py` benefit both scripts automatically.
- `multi_forwarder.py` Account 1 is also the command listener (DM the same account for commands as with `forwarder.py`). Accounts 2 and 3 are forwarding-only.
- `discovery.py` `search_public_chats()` may return limited results for newer/unverified Telegram accounts. `find_joined_sources()` (scans existing dialogs) is more reliable.
