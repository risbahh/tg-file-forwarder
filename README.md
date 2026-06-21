# 📡 TG File Forwarder

A production-ready Telegram **userbot** that silently captures movie/series files from source groups and feeds them into your auto-filter bot's private index channel — building your database on autopilot, 24/7.

> **For agents:** Read this entire file before making changes. See [Agent Reference](#-agent-reference) at the bottom for rules, known bugs, and what to build next.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Why Userbot Not Bot](#why-userbot-not-bot)
- [Two Capture Modes](#two-capture-modes)
- [File Structure](#file-structure)
- [Quick Setup](#quick-setup)
- [Environment Variables](#environment-variables)
- [forwarder.py Commands](#forwarderpy-commands)
- [bot_capture.py Commands](#bot_capturepy-commands)
- [Bulk History Dump](#bulk-history-dump)
- [Capturing From Other Bots](#capturing-from-other-bots)
- [Bugs Fixed](#bugs-fixed)
- [Feature Roadmap](#feature-roadmap)
- [Agent Reference](#-agent-reference)

---

## How It Works

```
[Cine Alliance Group]  ──┐
[Movies HD Hub]         ──┼──▶  forwarder.py  ──▶  [Your Private Index Channel]  ──▶  [Auto-Filter Bot]
[Any Source Chat]      ──┘          OR
                               bot_capture.py ──▶  (same destination)
```

1. Userbot joins source groups as a **normal member** (no admin needed)
2. Every file posted in those groups → instantly forwarded to your index channel
3. Your auto-filter bot auto-indexes the file → users can search it immediately
4. Source groups never know their content is being captured

**Connected repo:** [Auto-filter-bot-4](https://github.com/azizthekiller123/Auto-filter-bot-4) — this forwarder feeds that bot's index channel.

---

## Why Userbot Not Bot

You **cannot** add a regular Telegram bot to someone else's group without admin permission (`CHAT_ADMIN_REQUIRED` error). A **userbot** is a regular Telegram account running as a script — it joins groups exactly like a human member, no permission needed.

| | Regular Bot | Userbot (this repo) |
|---|---|---|
| Join other groups | ❌ Needs admin to add | ✅ Join like any user |
| Read message history | ❌ Only if added first | ✅ Full access |
| Visible to group admins | ✅ Appears as a bot | ✅ Appears as normal user |
| Requires `SESSION_STRING` | ❌ | ✅ Generate with `session_gen.py` |

---

## Two Capture Modes

This repo has two capture scripts. Run one or both:

| Mode | Script | Captures | Use when |
|---|---|---|---|
| **All-file** | `forwarder.py` | Every file from watched groups | You want maximum coverage — user uploads + bot files |
| **Bot-only** | `bot_capture.py` | Only files sent by a specific registered bot | You want only clean, verified movie files from a group's auto-filter bot |

**Recommended:** Run both together for full coverage with quality filtering:
- `forwarder.py` → catches everything (all sources)
- `bot_capture.py` → catches only verified bot files from Cine Alliance-style groups (no spam)

In practice, add your high-trust sources (Cine Alliance) to `bot_capture.py` and lower-trust sources (general movie groups) to `forwarder.py`.

---

## File Structure

```
tg-file-forwarder/
│
├── forwarder.py        # ★ ALL-FILE CAPTURE — main entry point
│                       #   Real-time watcher: listens for new files in all source chats
│                       #   Captures ALL file types from everyone in watched groups
│                       #   Handles commands: /addchat /removechat /listchats /fwrstatus
│                       #   Deploy on Railway as: python forwarder.py
│
├── bot_capture.py      # ★ TARGETED CAPTURE — bot-response only
│                       #   Only forwards files sent by a SPECIFIC registered bot per group
│                       #   Example: in Cine Alliance, only capture files from CineAllianceBot
│                       #   Skips user uploads, admin messages, random files
│                       #   Handles commands: /setbot /removebot /listbots /capturestatus
│                       #   Run alongside forwarder.py or as standalone
│
├── bots_db.py          # Bot-group mapping storage for bot_capture.py
│                       #   Reads/writes bots.json — {group_id: {bot_id, bot_username, label}}
│                       #   set_bot() / remove_bot() / get_bot() / get_bot_by_chat_id()
│                       #   get_bot_by_chat_id() resolves both int and string keys
│
├── bulk_dump.py        # One-time historical dump
│                       #   Iterates entire chat history, forwards all files found
│                       #   Saves progress to TRACKER_FILE (forwarded.json) — safe to interrupt
│                       #   Run: python bulk_dump.py CineAlliance
│
├── chats_db.py         # Dynamic source chat storage for forwarder.py
│                       #   Reads/writes chats.json — no redeploy needed when adding chats
│                       #   get_all_chats(seed) merges Railway config + dynamically added chats
│                       #   add_chat() / remove_chat() / list_chats() called by /addchat
│
├── config.py           # All settings loaded from environment variables
│                       #   Required: API_ID, API_HASH, SESSION_STRING, DEST_CHANNEL
│                       #   Will SystemExit with clear error if required vars are missing
│
├── utils.py            # Shared helpers (used by forwarder.py AND bot_capture.py)
│                       #   safe_forward(message, dest) — forward with FloodWait + retries
│                       #   is_allowed_file() — checks ALLOWED_TYPES filter
│                       #   get_file_name() / get_file_size() / human_size()
│
├── tracker.py          # Progress tracker for bulk_dump.py
│                       #   Persists forwarded message IDs to TRACKER_FILE (forwarded.json)
│                       #   is_done() / mark_done() — used to skip already-forwarded messages
│                       #   Allows safe resume after crash/interruption
│
├── session_gen.py      # One-time session string generator
│                       #   Run locally ONCE: python session_gen.py
│                       #   Logs in, prints SESSION_STRING — paste to Railway Variables
│                       #   NEVER commit SESSION_STRING to git
│
├── requirements.txt    # pyrofork==2.3.45 + tgcrypto-pyrofork
│                       #   Imports as pyrogram — same API, actively maintained fork
│
├── Procfile            # Railway: worker: python forwarder.py
│                       #   To also run bot_capture.py add: worker2: python bot_capture.py
├── railway.toml        # Railway deploy config (restartPolicyType: always)
├── .env.example        # All variables documented with examples
└── AGENT_SESSION_NOTES.md  # Full project context for agents (architecture + bug ledger)
```

---

## Quick Setup

### Step 1 — Get Telegram API Credentials
Go to [my.telegram.org](https://my.telegram.org) → **API Development Tools** → copy `API_ID` and `API_HASH`.

> Use a **secondary Telegram account**, not your main. The account will silently sit in source groups 24/7.

### Step 2 — Generate Session String (run locally, one time only)
```bash
pip install pyrofork tgcrypto-pyrofork python-dotenv
python session_gen.py
```
- Enter your API_ID, API_HASH, and phone number when prompted
- Telegram sends a verification code to your Telegram app
- Copy the printed `SESSION_STRING` — starts with `BQA...`

> ⚠️ **Keep SESSION_STRING secret.** It grants full account access. Never commit it to git.

### Step 3 — Get Your Destination Channel ID
Your index channel ID is a negative number like `-1001234567890`.
- Forward any message from the channel to [@userinfobot](https://t.me/userinfobot)
- Copy the `id` field — use exactly as-is (with the minus sign)

### Step 4 — Find Your Telegram User ID (for ADMINS)
- Message [@userinfobot](https://t.me/userinfobot) directly
- Copy your `id` — this is your `ADMINS` value

### Step 5 — Join Source Groups
Log into Telegram with your secondary account and join every group you want to capture from. The userbot will see files posted in groups it's a member of.

### Step 6 — Deploy to Railway

1. Fork this repo
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub** → select your fork
3. In Railway → your service → **Variables** tab, add:

```
API_ID          = (from my.telegram.org)
API_HASH        = (from my.telegram.org)
SESSION_STRING  = (from session_gen.py)
DEST_CHANNEL    = -1001234567890
SOURCE_CHATS    = CineAlliance,MoviesHDHub
ADMINS          = 123456789
```

4. Railway auto-deploys. Check **Deployments** tab for ✅ Active (~2 min)
5. DM the userbot on Telegram: `/addchat CineAlliance` to add more sources
6. To enable bot-only capture: DM `/setbot CineAlliance CineAllianceBotUsername`

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_ID` | ✅ | — | From my.telegram.org |
| `API_HASH` | ✅ | — | From my.telegram.org |
| `SESSION_STRING` | ✅ | — | Run session_gen.py once locally |
| `DEST_CHANNEL` | ✅ | — | Index channel ID (negative int, e.g. -1001234567890) |
| `SOURCE_CHATS` | ⚡ | — | Comma-separated usernames/IDs. Also add via /addchat |
| `ADMINS` | ⚡ | — | Your Telegram user ID. Comma-separated for multiple |
| `DELAY` | ⚡ | `3` | Seconds between each forward (increase if FloodWait) |
| `FLOOD_EXTRA` | ⚡ | `5` | Extra seconds added on top of Telegram's FloodWait |
| `MAX_RETRIES` | ⚡ | `5` | Retries before skipping a failed forward |
| `BATCH_SIZE` | ⚡ | `200` | Messages fetched per API call in bulk_dump.py |
| `ALLOWED_TYPES` | ⚡ | `document,video` | File types to forward (document, video, audio, photo) |
| `LOG_CHANNEL` | ⚡ | — | Get startup notifications and command confirmations |
| `TRACKER_FILE` | ⚡ | `forwarded.json` | Progress file for bulk_dump.py resume |
| `CHATS_DB_FILE` | ⚡ | `chats.json` | Dynamic chat storage for /addchat (forwarder.py) |
| `BOTS_DB_FILE` | ⚡ | `bots.json` | Bot-group mapping for /setbot (bot_capture.py) |

---

## forwarder.py Commands

DM the userbot account directly on Telegram. All commands are admin-only (`ADMINS` env var).

| Command | Description |
|---|---|
| `/addchat <username or -100id>` | Add a source group — takes effect instantly, no redeploy |
| `/removechat <username or -100id>` | Stop forwarding from a group |
| `/listchats` | Show all active sources (config + dynamically added) |
| `/fwrstatus` | Live stats: forwarded count, active sources, destination |
| `/start` or `/help` | Show all commands |

**Examples:**
```
/addchat CineAlliance
/addchat -100987654321
/removechat MoviesHDHub
/listchats
/fwrstatus
```

---

## bot_capture.py Commands

DM the same userbot account. All commands are admin-only.

| Command | Description |
|---|---|
| `/setbot <group> <bot_username>` | Register which bot to watch in a group — auto-resolves bot ID |
| `/removebot <group>` | Stop targeted capture for a group (falls back to all-file if forwarder.py is also running) |
| `/listbots` | Show all registered bot-group pairs |
| `/capturestatus` | Live stats: captured count, skip rate, session totals |
| `/start` or `/help` | Show all commands |

**Examples:**
```
/setbot CineAlliance CineAllianceBot
/setbot -100987654321 MoviesBotUsername
/removebot CineAlliance
/listbots
/capturestatus
```

### How /setbot works

`/setbot` automatically resolves the bot's Telegram user ID from its username — you only need to know the username:

1. You run `/setbot CineAlliance CineAllianceBot`
2. bot_capture.py calls `client.get_users("CineAllianceBot")` → gets the numeric ID (e.g. `1234567890`)
3. Saves `{CineAlliance: {bot_id: 1234567890, bot_username: CineAllianceBot}}` to `bots.json`
4. From now on: every message in Cine Alliance is checked — if `sender.id == 1234567890` AND it's a file → forward it

**How to find the bot username:**
1. Go to the group in Telegram
2. Find any file the bot sent
3. Click/tap the bot's name → copy its @username

---

## Bulk History Dump

Pull ALL historical files from a group (runs once, can be resumed):

```bash
# Dump all SOURCE_CHATS from config
python bulk_dump.py

# Dump a specific group
python bulk_dump.py CineAlliance
python bulk_dump.py -100987654321
```

**Features:**
- Saves progress to `forwarded.json` after every forward
- Safe to interrupt with Ctrl+C — resumes exactly where it stopped
- Shows live progress: scanned / found / forwarded / rate per minute
- Sends summary to `LOG_CHANNEL` when complete
- Can take hours for large groups — run overnight

**Typical workflow for a new source:**
```bash
# 1. First: dump all history (run once)
python bulk_dump.py CineAlliance

# 2. Then: switch to real-time (runs 24/7 on Railway)
# forwarder.py handles this automatically after /addchat
```

---

## Capturing From Other Bots

If a group uses an auto-filter bot (like Cine Alliance™), users request movies and the **bot** sends the files. The userbot captures those by joining the group as a normal member:

1. The group bot responds to any user's request with a file message
2. Your userbot reads that message and forwards it to YOUR index channel — silently

**Two approaches:**

**Option A — Catch everything** (forwarder.py):
- `/addchat CineAlliance`
- Captures all files from all senders in the group
- Simple but captures user uploads and spam too

**Option B — Bot-only** (bot_capture.py):
- `/setbot CineAlliance CineAllianceBotUsername`
- Only files FROM that specific bot get forwarded
- Cleaner index — only verified movie files

**Recommended:** Use Option B (`bot_capture.py`) for high-traffic auto-filter groups. Use Option A (`forwarder.py`) for groups where users directly post movie files.

---

## Bugs Fixed

| Date | File | Bug | Fix |
|---|---|---|---|
| 2026-06-21 | `forwarder.py` | `filters.all` does not exist in pyrofork → `AttributeError` crash at startup | Removed `filters.all &` — file type filters `(filters.document \| filters.video \| filters.audio)` are sufficient |
| 2026-06-21 | `misc.py` (main bot) | `os.remove()` not wrapped in try/except → crash if file already deleted | Wrapped in `try/except OSError` |

---

## Feature Roadmap

Features are listed in recommended build order.

### ✅ Built

| Feature | File | Notes |
|---|---|---|
| Real-time all-file capture | `forwarder.py` | Watches multiple groups, /addchat at runtime |
| Bot-targeted capture | `bot_capture.py` | /setbot auto-resolves bot ID, bots.json persists |
| Bulk history dump (resume-safe) | `bulk_dump.py` | Saves progress to forwarded.json |
| Dynamic source management | `chats_db.py` | Add/remove groups at runtime |
| Bot-group mapping storage | `bots_db.py` | Persists /setbot registrations |
| FloodWait handling | `utils.py` | Retries with backoff |
| Admin command guards | both scripts | ADMINS env var |

---

### 🔴 Tier 1 — Build Next (High Impact, Low Effort)

#### 1. Duplicate Detection _(not built)_
- Compare `file_unique_id` against a local `seen.json` before forwarding
- Skip if already forwarded — prevents index bloat from multiple sources posting the same file
- `/duplicates` command shows session skip count
- **Implementation:** add `seen_ids: set` to `utils.py`, check before `safe_forward()` in both scripts

#### 2. Filename Cleaner _(not built)_
- Strip watermarks: `[TamilMV]`, `www.1337x.to`, `@channel`, `Powered by:` etc.
- Normalize: `Movie.Name.2024.1080p.BluRay` → `Movie Name 2024 1080p BluRay`
- `/cleanmode on/off` toggle
- **Implementation:** new `cleaner.py` with regex patterns, call before `safe_forward()`

#### 3. Quality & Language Filter _(not built)_
- `ALLOWED_QUALITIES=1080p,4K,720p` env var — skip 360p/480p files
- `BLOCKED_LANGUAGES=Telugu` — exclude specific dubs
- Checks filename string before forwarding
- **Implementation:** extend `is_allowed_file()` in `utils.py`

#### 4. File Size Filter _(not built)_
- `MIN_SIZE_MB=10` — skip corrupt tiny files
- `MAX_SIZE_MB=4000` — skip files over 4GB
- **Implementation:** 4 lines added to `is_allowed_file()` in `utils.py`

---

### 🟡 Tier 2 — Growth Features _(not built)_

#### 5. Auto-Retry Failed Forwards
- Store failed message IDs in `failed.json`
- `/retry` command re-attempts all failed
- Auto-retry on startup
- **Implementation:** extend `tracker.py` with a `failed` section

#### 6. Multi-Destination Routing
- Route different content to different index channels:
  - Series (`S01E01` pattern) → `DEST_SERIES`
  - Movies → `DEST_MOVIES`
- `/route <chat> <destination>` for per-source routing
- **Implementation:** new `router.py` with regex matching on filename

#### 7. Caption Watermark Remover
- Strip `@ChannelName`, `t.me/xxxxx`, `Powered by:` from forwarded captions
- Send file with clean caption to index channel
- **Implementation:** `caption_cleaner.py`, call `clean_caption()` before `safe_forward()`

#### 8. `/stats` Daily Report
- Auto-post daily summary to `LOG_CHANNEL` at midnight
- **Implementation:** `apscheduler` (already in main bot requirements)

---

### 🟢 Tier 3 — Power Features _(not built)_

#### 9. Multi-Account Parallel Forwarding
- 2–3 userbot accounts in rotation
- When Account A hits FloodWait, Account B takes over
- `SESSION_STRING_2`, `SESSION_STRING_3` env vars

#### 10. Source Auto-Discovery
- Search Telegram for new movie groups matching keywords
- `/suggest_sources` returns 10 found groups with member counts

#### 11. Webhook Notifications (Discord/Slack)
- `DISCORD_WEBHOOK=https://discord.com/api/webhooks/...`
- Post forwarding updates to Discord in real-time

#### 12. `/search <query>` — Query Main Bot's Database
- DM the userbot and search what's already indexed
- Uses same `DATABASE_URL` as main auto-filter bot

#### 13. Scheduled Bulk Dump
- `BULK_SCHEDULE=02:00` — auto-run bulk dump at 2 AM daily

#### 14. Web Dashboard
- Status page at your Railway URL — total files, active sources, FloodWait count

---

### Priority Summary

| # | Feature | Impact | Effort | Status |
|---|---|---|---|---|
| — | Real-time all-file capture | ⭐⭐⭐ | — | ✅ Built |
| — | Bot-targeted capture | ⭐⭐⭐ | — | ✅ Built |
| — | Bulk dump (resume-safe) | ⭐⭐⭐ | — | ✅ Built |
| 1 | Duplicate detection | ⭐⭐⭐ | Very Low | 🔴 Next |
| 2 | Filename cleaner | ⭐⭐⭐ | Medium | 🔴 Next |
| 3 | Quality/language filter | ⭐⭐⭐ | Low | 🔴 Next |
| 4 | File size filter | ⭐⭐ | Very Low | 🔴 Next |
| 5 | Auto-retry failed | ⭐⭐ | Low | 🟡 |
| 6 | Multi-destination routing | ⭐⭐⭐ | Medium | 🟡 |
| 7 | Caption cleaner | ⭐⭐ | Medium | 🟡 |
| 8 | Daily stats report | ⭐⭐ | Low | 🟡 |
| 9 | Multi-account forwarding | ⭐⭐⭐ | High | 🟢 |
| 10 | Auto-discovery | ⭐⭐⭐ | High | 🟢 |
| 11 | Discord webhook | ⭐ | Low | 🟢 |
| 12 | /search DB | ⭐⭐ | Medium | 🟢 |
| 13 | Scheduled bulk dump | ⭐⭐ | Medium | 🟢 |
| 14 | Web dashboard | ⭐ | High | 🟢 |

---

## 🤖 Agent Reference

This section is for AI agents picking up this project. Read before making any changes.

### Connected Repos
| Repo | Purpose |
|---|---|
| [Auto-filter-bot-4](https://github.com/azizthekiller123/Auto-filter-bot-4) | Main Telegram movie bot — receives files from this forwarder's DEST_CHANNEL |
| [tg-file-forwarder](https://github.com/azizthekiller123/tg-file-forwarder) | This repo — feeds the main bot's index channel |

### How to Push to GitHub
Always use the GitHub API — push files **sequentially** (not parallel, causes SHA conflicts):
```bash
# Get current file SHA
SHA=$(curl -s -H "Authorization: token $GITHUB_PERSONAL_ACCESS_TOKEN" \
  "https://api.github.com/repos/azizthekiller123/tg-file-forwarder/contents/FILE" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['sha'])")

# Push update
CONTENT=$(base64 -w 0 local_file.py)
curl -s -X PUT \
  -H "Authorization: token $GITHUB_PERSONAL_ACCESS_TOKEN" \
  "https://api.github.com/repos/azizthekiller123/tg-file-forwarder/contents/FILE" \
  -d "{\"message\":\"fix: description\",\"content\":\"$CONTENT\",\"sha\":\"$SHA\"}"
```

### Critical Rules
1. **Library is `pyrofork==2.3.45`** — imports as `from pyrogram import ...`. Never change import names to pyrofork.
2. **`filters.all` does not exist** in pyrofork — use specific filters (filters.document | filters.video | filters.audio) or check inside the handler.
3. **Push files sequentially** — parallel GitHub API pushes cause SHA conflicts and fail silently.
4. **Never commit `SESSION_STRING`** — it's a Railway env var only. It grants full Telegram account access.
5. **`SOURCE_CHATS` in Railway** are seed-only — dynamic additions go to `chats.json` via `/addchat`.
6. **`safe_forward()` in `utils.py`** must always be used — never call `message.forward()` directly (no FloodWait protection).
7. **`bots.json` and `chats.json`** are stored on Railway's ephemeral filesystem — they reset on full redeploy. Use persistent storage (MongoDB/Postgres) for production.
8. **New features** must add env vars to `.env.example` and document them in the Variables table above.
9. **`get_bot_by_chat_id()`** in `bots_db.py` checks both string and int keys — always use this (not `get_bot()`) when resolving from a numeric chat ID inside a handler.
10. **bot_capture.py handlers must NOT use `filters.all`** — the handler already uses specific type filters at decoration level.

### Testing Without Deploying
```bash
# Syntax + AST check all Python files
for f in *.py; do python3 -m py_compile $f && echo "✓ $f" || echo "✗ $f"; done

# Full AST check (catches logic issues)
python3 -c "
import ast, glob
for p in glob.glob('*.py'):
    src = open(p).read()
    try:
        ast.parse(src)
        # Critical: check for filters.all bug
        if 'filters.all' in src: print(f'CRITICAL {p}: filters.all found')
        else: print(f'OK  {p}')
    except SyntaxError as e:
        print(f'ERR {p} line {e.lineno}: {e.msg}')
"
```

### What to Build Next (Recommended Order)
1. **Duplicate detection** — `seen.json` + check `file_unique_id` before `safe_forward()` in both forwarder.py and bot_capture.py
2. **Quality/language filter** — extend `is_allowed_file()` in `utils.py` with filename regex for `ALLOWED_QUALITIES` / `BLOCKED_LANGUAGES` env vars
3. **File size filter** — 4 lines added to `is_allowed_file()` for `MIN_SIZE_MB` / `MAX_SIZE_MB`
4. **Filename cleaner** — new `cleaner.py`, regex strip watermarks, call result as caption in `safe_forward()`
5. **Auto-retry failed** — extend `tracker.py` with `failed` tracking, `/retry` command in both scripts

### Known Limitations
- `chats.json` and `bots.json` are on Railway's ephemeral filesystem — reset on full redeploy. Connect MongoDB or Postgres to persist dynamic registrations.
- `forwarded.json` (bulk dump tracker) has the same limitation — use persistent storage for long dumps.
- The userbot account can be temporarily restricted by Telegram if forwarding too fast — keep `DELAY >= 3`.
- bot_capture.py and forwarder.py both run as separate processes — if a group is in BOTH chats.json and bots.json, the file may be forwarded twice to DEST_CHANNEL. Keep sources exclusive between the two scripts to avoid duplicates (another reason to build duplicate detection first).
