# 📡 TG File Forwarder

A production-ready Telegram **userbot** that silently captures movie/series files from source groups and feeds them into your auto-filter bot's private index channel — building your database on autopilot, 24/7.

> **For agents:** Read this entire file before making changes. See [Agent Reference](#-agent-reference) at the bottom for rules, known bugs, and what to build next.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Why Userbot Not Bot](#why-userbot-not-bot)
- [File Structure](#file-structure)
- [Quick Setup](#quick-setup)
- [Environment Variables](#environment-variables)
- [Telegram Commands](#telegram-commands)
- [Bulk History Dump](#bulk-history-dump)
- [Capturing From Other Bots](#capturing-from-other-bots)
- [Bugs Fixed](#bugs-fixed)
- [Feature Roadmap](#feature-roadmap)
- [Agent Reference](#-agent-reference)

---

## How It Works

```
[Cine Alliance Group]  ──┐
[Movies HD Hub]         ──┼──▶  forwarder.py (userbot)  ──▶  [Your Private Index Channel]  ──▶  [Auto-Filter Bot]
[Any Source Chat]      ──┘          ↑
                                /addchat adds
                               sources live here
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
| Visible to group admins | ✅ Appears in member list as a bot | ✅ Appears as normal user |
| Requires `SESSION_STRING` | ❌ | ✅ Generate with `session_gen.py` |

---

## File Structure

```
tg-file-forwarder/
│
├── forwarder.py        # ★ MAIN ENTRY POINT
│                       #   Real-time watcher: listens for new files in all source chats
│                       #   Also handles Telegram commands (/addchat etc.)
│                       #   Deploy this on Railway as: python forwarder.py
│
├── bulk_dump.py        # One-time historical dump
│                       #   Iterates entire chat history, forwards all files found
│                       #   Saves progress to TRACKER_FILE (forwarded.json) — safe to interrupt
│                       #   Run: python bulk_dump.py CineAlliance
│
├── chats_db.py         # Dynamic source chat storage
│                       #   Reads/writes chats.json — no redeploy needed when adding chats
│                       #   get_all_chats(seed) merges Railway config + dynamically added chats
│                       #   add_chat() / remove_chat() / list_chats() called by forwarder commands
│
├── config.py           # All settings loaded from environment variables
│                       #   Required: API_ID, API_HASH, SESSION_STRING, DEST_CHANNEL, SOURCE_CHATS
│                       #   Will SystemExit with clear error message if required vars are missing
│
├── utils.py            # Shared helpers
│                       #   safe_forward(message, dest) — forwards with FloodWait handling + retries
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
│                       #   Logs into your Telegram account, prints SESSION_STRING
│                       #   Paste SESSION_STRING into Railway Variables tab
│                       #   NEVER commit SESSION_STRING to git
│
├── requirements.txt    # pyrofork==2.3.45 + tgcrypto-pyrofork
│                       #   Uses pyrofork (NOT pyrogram) — same API, maintained fork
│
├── Procfile            # Railway: worker: python forwarder.py
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
- Telegram sends a verification code to your app
- Copy the printed `SESSION_STRING` — it's a long string starting with `BQA...`

> ⚠️ **Keep SESSION_STRING secret.** It grants full account access. Never commit it to git.

### Step 3 — Get Your Destination Channel ID
Your index channel ID is a negative number like `-1001234567890`.
- Forward any message from the channel to [@userinfobot](https://t.me/userinfobot)
- Copy the `id` field — use exactly as-is (with the minus sign)

### Step 4 — Find Your Telegram User ID (for ADMINS)
- Message [@userinfobot](https://t.me/userinfobot) directly
- Copy your `id` — this is your `ADMINS` value

### Step 5 — Join Source Groups
Log into Telegram with your secondary account and join every group you want to capture from (e.g. Cine Alliance). The userbot will see files posted in groups it's a member of.

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

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_ID` | ✅ | — | From my.telegram.org |
| `API_HASH` | ✅ | — | From my.telegram.org |
| `SESSION_STRING` | ✅ | — | Run session_gen.py once locally |
| `DEST_CHANNEL` | ✅ | — | Index channel ID (negative int, e.g. -1001234567890) |
| `SOURCE_CHATS` | ⚡ | — | Comma-separated usernames/IDs. Can also add via /addchat |
| `ADMINS` | ⚡ | — | Your Telegram user ID. Comma-separated for multiple |
| `DELAY` | ⚡ | `3` | Seconds between each forward (increase if FloodWait) |
| `FLOOD_EXTRA` | ⚡ | `5` | Extra seconds added on top of Telegram's FloodWait |
| `MAX_RETRIES` | ⚡ | `5` | Retries before skipping a failed forward |
| `BATCH_SIZE` | ⚡ | `200` | Messages fetched per API call in bulk_dump.py |
| `ALLOWED_TYPES` | ⚡ | `document,video` | File types to forward (document, video, audio, photo) |
| `LOG_CHANNEL` | ⚡ | — | Get notifications + command confirmations here |
| `TRACKER_FILE` | ⚡ | `forwarded.json` | Progress file for bulk_dump.py resume |
| `CHATS_DB_FILE` | ⚡ | `chats.json` | Dynamic chat storage for /addchat |

---

## Telegram Commands

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

> Note: commands added via `/addchat` are stored in `chats.json` and survive bot restarts. Config-seeded chats (from `SOURCE_CHATS` env var) cannot be removed via `/removechat` — edit Railway vars to change those.

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

If a group uses an auto-filter bot (like Cine Alliance™), users request movies and the **bot** sends the files. Here's how to capture those:

**The userbot joins the group as a normal member.** When the group's bot responds to any user's request with a file, the userbot sees it and forwards it to your index channel — silently.

This works because:
1. The group bot **forwards** files from its private index channel into the group chat
2. The forwarded file appears as a normal group message
3. Your userbot reads that message and forwards it to YOUR index channel

**No special setup needed** — just `/addchat` the group. Every file the group bot sends to any member will be captured.

**To target ONLY that group's bot responses** (skip user uploads/spam):
- Find the bot's Telegram user ID (forward any message from the bot to @userinfobot)
- Future feature: `bot_capture.py` (see roadmap below — not yet built)

---

## Bugs Fixed

| Date | File | Bug | Fix |
|---|---|---|---|
| 2026-06-21 | `forwarder.py` | `filters.all` does not exist in pyrofork → `AttributeError` crash at startup | Removed `filters.all &` — file type filters `(filters.document \| filters.video \| filters.audio)` are sufficient; dynamic chat check happens inside the handler |

---

## Feature Roadmap

Features are listed in recommended build order. Tier 1 should be built first.

### 🔴 Tier 1 — Build Next (High Impact, Low Effort)

#### 1. Duplicate Detection _(not built)_
- Compare `file_unique_id` against a local `seen.json` before forwarding
- Skip if already forwarded — prevents index bloat
- `/duplicates` command shows session skip count
- **Implementation:** add `seen_ids: set` to utils.py, check before `safe_forward()`

#### 2. `bot_capture.py` — Target Bot Responses Only _(not built)_
- Filter by `message.from_user.id == TARGET_BOT_ID` instead of all file messages
- Captures only verified movie files — skips user spam and junk
- `/setbot <group> <bot_username>` registers which bot to watch per group
- **Implementation:** new file `bot_capture.py`, store `{group_id: bot_id}` in `bots.json`

#### 3. Filename Cleaner _(not built)_
- Strip watermarks: `[TamilMV]`, `www.1337x.to`, `@channel`, `Powered by:` etc.
- Normalize: `Movie.Name.2024.1080p.BluRay` → `Movie Name 2024 1080p BluRay`
- `/cleanmode on/off` toggle
- **Implementation:** `cleaner.py` with regex patterns, call before forwarding

#### 4. Quality & Language Filter _(not built)_
- `ALLOWED_QUALITIES=1080p,4K,720p` env var — skip 360p/480p files
- `BLOCKED_LANGUAGES=Telugu` — exclude specific dubs
- Checks filename string before forwarding
- **Implementation:** extend `is_allowed_file()` in `utils.py`

#### 5. File Size Filter _(not built)_
- `MIN_SIZE_MB=10` — skip corrupt tiny files
- `MAX_SIZE_MB=4000` — skip files over 4GB
- **Implementation:** add to `is_allowed_file()` in `utils.py` (2 lines)

---

### 🟡 Tier 2 — Growth Features _(not built)_

#### 6. Auto-Retry Failed Forwards
- Store failed message IDs in `failed.json`
- `/retry` command re-attempts all failed
- Auto-retry on startup
- **Implementation:** extend `tracker.py` with a `failed` section

#### 7. Multi-Destination Routing
- Route different content to different index channels:
  - Series (`S01E01` pattern) → `DEST_SERIES`
  - Movies → `DEST_MOVIES`
  - Regional → `DEST_SOUTH`
- `/route <chat> <destination>` for per-source routing
- **Implementation:** `router.py` with regex pattern matching on filename

#### 8. Caption Watermark Remover
- Groups add their own ads/promo to captions when forwarding
- Strip `@ChannelName`, `t.me/xxxxx`, `Powered by:`, `Join:` patterns
- Send file with clean empty caption to your index channel
- **Implementation:** `caption_cleaner.py`, call `clean_caption()` before `safe_forward()`

#### 9. `/stats` Daily Report
- Auto-post daily summary to `LOG_CHANNEL` at midnight:
  ```
  📊 Daily Report — 21 June 2026
  Files forwarded today: 847
  Duplicates skipped: 124
  Failed: 3
  Top source: CineAlliance (412 files)
  ```
- **Implementation:** use `apscheduler` (already in main bot requirements)

---

### 🟢 Tier 3 — Power Features _(not built)_

#### 10. Multi-Account Parallel Forwarding
- 2–3 userbot accounts in rotation
- When Account A hits FloodWait, Account B takes over
- `SESSION_STRING_2`, `SESSION_STRING_3` env vars
- Effectively 3× the forwarding speed

#### 11. Source Auto-Discovery
- Search Telegram for new movie groups matching keywords
- `/suggest_sources` returns 10 found groups with member counts
- `/autojoin` joins and adds them automatically

#### 12. Webhook Notifications (Discord/Slack)
- `DISCORD_WEBHOOK=https://discord.com/api/webhooks/...`
- Post forwarding updates to a Discord channel in real-time

#### 13. `/search <query>` — Query Main Bot's Database
- DM the userbot and search what's already indexed
- Uses the same `DATABASE_URL` as the main auto-filter bot
- Returns top 5 matches — useful before running a bulk dump

#### 14. Scheduled Bulk Dump
- `BULK_SCHEDULE=02:00` — auto-run bulk dump at 2 AM daily
- Avoids hitting FloodWait during peak hours
- **Implementation:** `apscheduler` + run `dump_chat()` from `bulk_dump.py`

#### 15. Web Dashboard
- Status page at your Railway URL showing:
  - Total files forwarded, active sources, last forward time
  - FloodWait count, error rate
- **Implementation:** `aiohttp` web server (same pattern as main bot's `route.py`)

---

### Priority Summary

| # | Feature | Impact | Effort |
|---|---|---|---|
| 1 | Duplicate detection | ⭐⭐⭐ | Very Low |
| 2 | bot_capture.py | ⭐⭐⭐ | Low |
| 3 | Filename cleaner | ⭐⭐⭐ | Medium |
| 4 | Quality/language filter | ⭐⭐⭐ | Low |
| 5 | File size filter | ⭐⭐ | Very Low |
| 6 | Auto-retry failed | ⭐⭐ | Low |
| 7 | Multi-destination routing | ⭐⭐⭐ | Medium |
| 8 | Caption cleaner | ⭐⭐ | Medium |
| 9 | Daily stats report | ⭐⭐ | Low |
| 10 | Multi-account forwarding | ⭐⭐⭐ | High |
| 11 | Auto-discovery | ⭐⭐⭐ | High |
| 12 | Discord webhook | ⭐ | Low |
| 13 | /search DB | ⭐⭐ | Medium |
| 14 | Scheduled bulk dump | ⭐⭐ | Medium |
| 15 | Web dashboard | ⭐ | High |

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
2. **`filters.all` does not exist** in pyrofork — use specific filters or check inside the handler.
3. **Push files sequentially** — parallel GitHub API pushes cause SHA conflicts and fail silently.
4. **Never commit `SESSION_STRING`** — it's a Railway env var only. It grants full Telegram account access.
5. **`SOURCE_CHATS` in Railway** are seed-only — dynamic additions go to `chats.json` via `/addchat`.
6. **`safe_forward()` in `utils.py`** must always be used for forwarding — never call `message.forward()` directly (no FloodWait protection).
7. **New features** should add env vars to `.env.example` and document them in the Variables table in this README.

### Testing Without Deploying
```bash
# Syntax check all Python files
for f in *.py; do python3 -m py_compile $f && echo "✓ $f" || echo "✗ $f"; done

# AST check (catches logic issues)
python3 -c "import ast; ast.parse(open('forwarder.py').read()); print('OK')"
```

### What to Build Next (Recommended Order)
1. **Duplicate detection** — `seen.json` + check `file_unique_id` in `utils.py` before `safe_forward()`
2. **`bot_capture.py`** — new file, filter `message.from_user.id == TARGET_BOT_ID`, store `bots.json`
3. **Quality/language filter** — extend `is_allowed_file()` in `utils.py` with filename regex
4. **File size filter** — 2 lines added to `is_allowed_file()` in `utils.py`
5. **Filename cleaner** — new `cleaner.py`, regex strip watermarks, call before forwarding

### Known Limitations
- `chats.json` is stored on Railway's ephemeral filesystem — resets on full redeploy. For production, connect a MongoDB or Postgres to persist dynamic chat list.
- `forwarded.json` (bulk dump tracker) has the same limitation — attach persistent storage for long dumps.
- The userbot account can be temporarily restricted by Telegram if forwarding too fast — keep `DELAY >= 3`.
