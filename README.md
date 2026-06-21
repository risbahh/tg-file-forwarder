# 📡 TG File Forwarder

A production-ready Telegram **userbot** that silently captures movie/series files from source groups and feeds them into your auto-filter bot's private index channel — building your database on autopilot, 24/7.

**Deploy anywhere in minutes** — Railway, Render, Fly.io, Heroku, Docker VPS, or bare Python on any Linux server.

> **For agents:** Read this entire file before making changes. See [Agent Reference](#-agent-reference) at the bottom for rules, known bugs, and what to build next.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Before You Deploy](#before-you-deploy)
- [🚀 Deploy — Pick Your Platform](#-deploy--pick-your-platform)
  - [Railway](#-railway-recommended--easiest)
  - [Render](#-render-free-tier-available)
  - [Fly.io](#-flyio-free-tier-available)
  - [Docker (VPS or local)](#-docker-vps-or-local)
  - [Bare VPS (no Docker)](#-bare-vps-no-docker)
  - [Heroku](#-heroku)
- [Capture Modes](#capture-modes)
- [File Structure](#file-structure)
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
6. Dashboard at `https://your-app.domain/` shows live stats

**Connected repo:** [Auto-filter-bot-4](https://github.com/azizthekiller123/Auto-filter-bot-4) — this forwarder feeds that bot's index channel(s).

---

## Before You Deploy

You need these 3 things regardless of which platform you choose:

### 1. Telegram API Credentials
Go to [my.telegram.org](https://my.telegram.org) → **API Development Tools** → copy `API_ID` and `API_HASH`.
> Use a **secondary Telegram account**, not your main.

### 2. Session String (one-time, run locally)
```bash
pip install pyrofork tgcrypto-pyrofork python-dotenv
python session_gen.py
```
Enter phone number + verification code → copy the printed `SESSION_STRING` (starts with `BQA...`).
> ⚠️ Never commit SESSION_STRING to git. Never share it. It grants full account access.

### 3. Your Destination Channel ID
Forward any message from your index channel to [@userinfobot](https://t.me/userinfobot) → copy the `id` (a negative number like `-1001234567890`).

---

## 🚀 Deploy — Pick Your Platform

---

### ⭐ Railway (recommended — easiest)

**Cost:** Free tier available (500h/month) | Paid from $5/mo for 24/7

1. Fork this repo on GitHub
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub** → select your fork
3. In Railway → your service → **Variables** tab, add:

| Variable | Value |
|---|---|
| `API_ID` | from my.telegram.org |
| `API_HASH` | from my.telegram.org |
| `SESSION_STRING` | from `python session_gen.py` |
| `DEST_CHANNEL` | your index channel ID (e.g. `-1001234567890`) |
| `SOURCE_CHATS` | comma-separated groups (e.g. `CineAlliance,MoviesHub`) |
| `ADMINS` | your Telegram user ID |

4. Railway auto-deploys. Check **Deployments** tab for ✅ Active (~2 min)
5. Visit your Railway URL → live dashboard ✓

**Persistent storage:** Railway provides ephemeral storage by default. Add a Railway Volume in your service settings for persistence (chats, seen DB, routing survive redeploys).

---

### 🎨 Render (free tier available)

**Cost:** Free tier (spins down after inactivity) | Starter $7/mo for always-on

**Option A — Blueprint (one click):**
1. Fork this repo
2. Go to [render.com](https://render.com) → **New** → **Blueprint** → connect your fork
3. Render reads `render.yaml` automatically — creates the worker + 1GB persistent disk
4. Add secrets in the Render dashboard: `API_ID`, `API_HASH`, `SESSION_STRING`, `DEST_CHANNEL`, `ADMINS`
5. Click **Apply** → deploys automatically

**Option B — Manual:**
1. Render → **New Worker** → connect your GitHub fork
2. Build command: `pip install -r requirements.txt`
3. Start command: `python forwarder.py`
4. Add a **Disk** → mount path `/app/data`, size 1GB
5. Add environment variables (same as above)

**Disk env vars** (pre-set in `render.yaml`, set manually if doing Option B):
```
TRACKER_FILE=/app/data/forwarded.json
CHATS_DB_FILE=/app/data/chats.json
BOTS_DB_FILE=/app/data/bots.json
SEEN_DB_FILE=/app/data/seen.json
ROUTING_FILE=/app/data/routing.json
```

---

### ✈️ Fly.io (free tier available)

**Cost:** Free allowance (3 shared-cpu VMs) | ~$2/mo for persistent 1GB volume

```bash
# Install flyctl
curl -L https://fly.io/install.sh | sh

# Clone your fork
git clone https://github.com/YOUR_USERNAME/tg-file-forwarder
cd tg-file-forwarder

# Launch (reads fly.toml automatically)
fly launch --name tg-file-forwarder --no-deploy

# Create persistent volume for data files
fly volumes create forwarder_data --size 1 --region sin

# Set secrets (never visible in logs)
fly secrets set \
  API_ID=12345678 \
  API_HASH=abcdef1234 \
  SESSION_STRING="BQA..." \
  DEST_CHANNEL=-1001234567890 \
  SOURCE_CHATS=CineAlliance,MoviesHub \
  ADMINS=123456789

# Deploy
fly deploy

# View logs
fly logs
```

**Change region** in `fly.toml` → `primary_region`: `sin` (Singapore), `nrt` (Tokyo), `fra` (Frankfurt), `lax` (Los Angeles).

---

### 🐳 Docker (VPS or local)

Works on any server or computer with Docker installed — DigitalOcean, Linode, Vultr, Hetzner, your home server, etc.

**Setup:**
```bash
# 1. Clone your fork
git clone https://github.com/YOUR_USERNAME/tg-file-forwarder
cd tg-file-forwarder

# 2. Create your .env file
cp .env.example .env
nano .env   # fill in API_ID, API_HASH, SESSION_STRING, DEST_CHANNEL, ADMINS

# 3. Start with docker compose
docker compose up -d

# 4. View live logs
docker compose logs -f

# 5. Visit dashboard
open http://localhost:8080
```

**To run bot_capture.py instead of forwarder.py:**
Edit `docker-compose.yml` → change `command: python forwarder.py` → `command: python bot_capture.py`

**To run BOTH at the same time:**
Uncomment the `bot_capture` service in `docker-compose.yml` (already written, just uncomment).

**Update after a git pull:**
```bash
git pull
docker compose down && docker compose up -d --build
```

**Data persists** in a named Docker volume (`forwarder_data`) — survives container restarts and `docker compose down/up`.
To see where data is stored: `docker volume inspect tg-file-forwarder_forwarder_data`

---

### 🖥️ Bare VPS (no Docker)

For Ubuntu 22.04 / Debian 11–12. Runs the forwarder in a background `screen` session.

```bash
# 1. Clone your fork
git clone https://github.com/YOUR_USERNAME/tg-file-forwarder
cd tg-file-forwarder

# 2. Fill in your environment variables
cp .env.example .env
nano .env

# 3. Run the setup script — installs deps, starts in background
chmod +x setup.sh
./setup.sh
```

The script will:
- Install Python 3 and `screen` if not present
- Install Python dependencies
- Check your `.env` for required variables
- Ask which script to run (forwarder / bot_capture / multi_forwarder)
- Start it in a named `screen` session

**Manage the running forwarder:**
```bash
screen -r forwarder         # attach to see live logs
# Ctrl+A then D              # detach (leave running in background)
screen -S forwarder -X quit # stop the forwarder
./setup.sh                  # restart after code changes or git pull
```

**Auto-start on reboot** (add to crontab):
```bash
crontab -e
# Add this line:
@reboot cd /path/to/tg-file-forwarder && ./setup.sh
```

---

### 🟣 Heroku

**Cost:** No free tier (Eco dynos from $5/mo)

```bash
# Install Heroku CLI, then:
heroku login
heroku create tg-file-forwarder

# Set config vars
heroku config:set \
  API_ID=12345678 \
  API_HASH=abcdef1234 \
  SESSION_STRING="BQA..." \
  DEST_CHANNEL=-1001234567890 \
  SOURCE_CHATS=CineAlliance \
  ADMINS=123456789

# Heroku uses Procfile automatically:
# worker: python forwarder.py

# Deploy
git push heroku main

# View logs
heroku logs --tail
```

> ⚠️ Heroku has no persistent filesystem. `chats.json`, `seen.json` etc. will reset on every redeploy. For production use on Heroku, use Railway or Render instead (both have persistent disk support).

---

### Platform Comparison

| Platform | Free Tier | Persistent Disk | Ease | Best For |
|---|---|---|---|---|
| **Railway** | 500h/mo | Add-on volume | ⭐⭐⭐ | Best all-round |
| **Render** | Spins down | ✅ 1GB included | ⭐⭐⭐ | Free 24/7 with paid |
| **Fly.io** | 3 VMs free | ✅ 1GB volume | ⭐⭐ | Lowest cost |
| **Docker VPS** | Pay VPS | ✅ Named volume | ⭐⭐ | Full control |
| **Bare VPS** | Pay VPS | ✅ Local disk | ⭐⭐⭐ | Simplest on VPS |
| **Heroku** | ❌ | ❌ Ephemeral | ⭐⭐ | Not recommended |

---

## Capture Modes

| Mode | Script | Captures | Use when |
|---|---|---|---|
| **All-file** | `forwarder.py` | Every file from watched groups | Maximum coverage |
| **Bot-only** | `bot_capture.py` | Only files from a specific bot per group | Only verified bot files |
| **Multi-account** | `multi_forwarder.py` | Same as forwarder.py with account rotation | FloodWait is frequent |

---

## File Structure

```
tg-file-forwarder/
│
├── Dockerfile           # Docker image — works on any Docker host
├── docker-compose.yml   # Local dev + self-hosted VPS Docker setup
├── .dockerignore        # Keeps image lean (no .env, no .session files)
├── fly.toml             # Fly.io deploy config (fly deploy)
├── render.yaml          # Render blueprint (auto-detected by Render)
├── setup.sh             # Bare VPS quick-start script (Ubuntu/Debian)
├── Procfile             # Railway + Heroku: worker: python forwarder.py
├── railway.toml         # Railway: restartPolicyType: always
│
├── forwarder.py         # ★ ALL-FILE CAPTURE — main entry point
│                        #   Commands: /addchat /removechat /route /dupstats /discover /suggest
│
├── bot_capture.py       # ★ BOT-ONLY CAPTURE
│                        #   Commands: /setbot /removebot /listbots /capturestatus
│
├── multi_forwarder.py   # ★ MULTI-ACCOUNT CAPTURE (FloodWait rotation)
│                        #   Commands: /addchat /route /routes /poolstatus
│
├── account_pool.py      # Multi-account FloodWait rotation engine
├── router.py            # Multi-destination routing (movies/series/south)
├── seen_db.py           # Duplicate detection (file_unique_id → seen.json)
├── caption_cleaner.py   # Caption watermark remover
├── discovery.py         # Source auto-discovery (/discover /suggest)
├── dashboard.py         # aiohttp live status page at PORT
│
├── bots_db.py           # Bot-group mapping (bot_capture.py)
├── bulk_dump.py         # One-time history dump (resume-safe)
├── chats_db.py          # Dynamic source list (/addchat)
├── config.py            # All settings from env vars
├── tracker.py           # Bulk dump progress
├── utils.py             # safe_forward() — FloodWait + dedup + caption clean
├── session_gen.py       # One-time session string generator
│
├── requirements.txt     # pyrofork==2.3.45, tgcrypto-pyrofork, aiohttp
├── .env.example         # All env vars documented with examples
└── AGENT_SESSION_NOTES.md  # Full project context for agents
```

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
| `PORT` | ⚡ | `8080` | Dashboard port (set automatically by Railway/Render/Fly) |
| `TRACKER_FILE` | ⚡ | `forwarded.json` | Bulk dump progress |
| `CHATS_DB_FILE` | ⚡ | `chats.json` | Dynamic source list |
| `BOTS_DB_FILE` | ⚡ | `bots.json` | Bot-group mapping (bot_capture.py) |
| `SEEN_DB_FILE` | ⚡ | `seen.json` | Duplicate detection store |
| `ROUTING_FILE` | ⚡ | `routing.json` | Per-source routing overrides |

**For persistent storage** on Docker/Fly/Render, set the `*_FILE` paths to your mounted volume:
```
TRACKER_FILE=/app/data/forwarded.json
CHATS_DB_FILE=/app/data/chats.json
BOTS_DB_FILE=/app/data/bots.json
SEEN_DB_FILE=/app/data/seen.json
ROUTING_FILE=/app/data/routing.json
```
The `Dockerfile`, `fly.toml`, `render.yaml`, and `docker-compose.yml` already set these defaults.

---

## forwarder.py Commands

DM the userbot. All commands are admin-only (`ADMINS` env var).

| Command | Description |
|---|---|
| `/addchat <username or id>` | Add a source group — takes effect instantly |
| `/removechat <username or id>` | Stop forwarding from a group |
| `/listchats` | Show all active sources |
| `/fwrstatus` | Full stats: forwarded, dedup, routing, caption clean |
| `/route <source> <channel>` | Override destination for a specific source |
| `/routes` | Show all routing rules and auto-detect config |
| `/dupstats` | Duplicate detection stats |
| `/discover` | Scan joined groups for movie sources |
| `/suggest <keyword>` | Search Telegram for public groups |
| `/help` | Show all commands |

---

## bot_capture.py Commands

| Command | Description |
|---|---|
| `/setbot <group> <bot_username>` | Register which bot to watch — auto-resolves bot ID |
| `/removebot <group>` | Stop targeted capture for a group |
| `/listbots` | Show all registered bot-group pairs |
| `/capturestatus` | Live capture stats |
| `/help` | Show all commands |

---

## Multi-Destination Routing

Route different content types to different index channels automatically:

```
Movie file       → DEST_MOVIES   (e.g. "Inception 2010 1080p.mkv")
Series episode   → DEST_SERIES   (e.g. "Breaking Bad S05E14.mkv")
South Indian     → DEST_SOUTH    (e.g. "KGF Chapter 2 Hindi Dubbed.mkv")
Fallback         → DEST_CHANNEL
```

Set env vars `DEST_MOVIES`, `DEST_SERIES`, `DEST_SOUTH`. Use `/route <source> <channel>` for per-source overrides. View all rules with `/routes`.

---

## Duplicate Detection

Every forwarded file is tracked by `file_unique_id` in `seen.json`. Same movie posted in two groups → forwarded once, skipped once. Works across sessions. Built into `safe_forward()` automatically — no extra setup.

---

## Caption Watermark Removal

`caption_cleaner.py` strips before forwarding: `@username`, `t.me/` links, `[TamilMV]` tags, `Powered by:`, `Join:`, any URL. Toggle with `CLEAN_CAPTIONS=false`.

---

## Multi-Account Forwarding

When Account 1 hits FloodWait, switches to Account 2 instantly. Set `SESSION_STRING_2` and `SESSION_STRING_3`. Change Procfile to `python multi_forwarder.py`. Use `/poolstatus` to see per-account stats.

---

## Source Auto-Discovery

- `/discover` — scan groups the userbot already joined, find movie-related ones
- `/suggest <keyword>` — search Telegram for public groups (`/suggest 4k movies hindi dubbed`)

Both return group names, usernames, member counts. Use `/addchat <username>` to add any result.

---

## Web Dashboard

Visit your app URL after deploying:
- `https://your-app.domain/` → HTML dashboard (auto-refreshes every 30s)
- `https://your-app.domain/api/stats` → JSON stats
- `https://your-app.domain/health` → `{"status": "ok"}`

Shows: forwarded count, seen DB size, duplicates skipped, failed, source list, routing config, uptime.

---

## Bulk History Dump

Pull ALL historical files from a group before switching to real-time:

```bash
python bulk_dump.py CineAlliance    # dump specific group
python bulk_dump.py                  # dump all SOURCE_CHATS
```

Resume-safe — saves progress to `forwarded.json` after every file. Safe to interrupt with Ctrl+C.

---

## Bugs Fixed

| Date | File | Bug | Fix |
|---|---|---|---|
| 2026-06-21 | `forwarder.py` | `filters.all` crash at startup — does not exist in pyrofork | Removed `filters.all &` — specific type filters are sufficient |
| 2026-06-21 | `misc.py` (main bot) | `os.remove()` not in try/except → crash if file already deleted | Wrapped in `try/except OSError` |

---

## Feature Roadmap

### ✅ Built

| Feature | File(s) |
|---|---|
| Real-time all-file capture | `forwarder.py` |
| Bot-targeted capture | `bot_capture.py` |
| Multi-account FloodWait rotation | `multi_forwarder.py`, `account_pool.py` |
| Multi-destination routing | `router.py` |
| Duplicate detection | `seen_db.py`, `utils.py` |
| Caption watermark removal | `caption_cleaner.py`, `utils.py` |
| Source auto-discovery | `discovery.py` |
| Web dashboard | `dashboard.py` |
| Docker support | `Dockerfile`, `docker-compose.yml` |
| Fly.io support | `fly.toml` |
| Render support | `render.yaml` |
| VPS quick-setup | `setup.sh` |
| Bulk history dump (resume-safe) | `bulk_dump.py` |

### 🔴 Remaining — Build Next

1. **Auto-retry failed forwards** — `failed.json` + `/retry` command in forwarder.py
2. **File size filter** — `MIN_SIZE_MB`/`MAX_SIZE_MB` in `is_allowed_file()` (4 lines)
3. **Quality/language filter** — `ALLOWED_QUALITIES`/`BLOCKED_LANGUAGES` regex in `is_allowed_file()`
4. **Daily stats report** — `apscheduler` midnight post to `LOG_CHANNEL`
5. **Scheduled bulk dump** — `apscheduler` nightly run

---

## 🤖 Agent Reference

### Connected Repos
| Repo | Purpose |
|---|---|
| [Auto-filter-bot-4](https://github.com/azizthekiller123/Auto-filter-bot-4) | Main Telegram movie search bot |
| [tg-file-forwarder](https://github.com/azizthekiller123/tg-file-forwarder) | This repo — feeds the main bot's index channel(s) |

### How to Push to GitHub
Sequential for updates (each has its own SHA), parallel for new files:
```bash
SHA=$(curl -s -H "Authorization: token $GITHUB_PERSONAL_ACCESS_TOKEN" \
  "https://api.github.com/repos/azizthekiller123/tg-file-forwarder/contents/FILE" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['sha'])")
CONTENT=$(base64 -w 0 local_file.py)
curl -s -X PUT -H "Authorization: token $GITHUB_PERSONAL_ACCESS_TOKEN" \
  "https://api.github.com/repos/azizthekiller123/tg-file-forwarder/contents/FILE" \
  -d "{\"message\":\"fix: description\",\"content\":\"$CONTENT\",\"sha\":\"$SHA\"}"
```

### Critical Rules
1. **Library is `pyrofork==2.3.45`** — imports as `from pyrogram import ...`. Never rename to pyrofork.
2. **`filters.all` does not exist** in pyrofork — use `filters.document | filters.video | filters.audio`.
3. **`safe_forward()` in `utils.py`** must always be used — handles FloodWait, dedup, and caption cleaning.
4. **Never commit `SESSION_STRING`** — env var only.
5. **`get_bot_by_chat_id()`** in `bots_db.py` — use inside handlers, not `get_bot()`.
6. **`forwarder.py` and `bot_capture.py` can overlap** — `seen_db.py` dedup catches the second attempt.
7. **`dashboard.py` runs as `asyncio.create_task()`** inside `forwarder.py` — not a separate process.
8. **Dockerfile sets data paths to `/app/data`** — always mount a volume there for persistence.
9. **New features** must add env vars to `.env.example`, `Dockerfile` ENV section, `fly.toml` [env], and `render.yaml` envVars.

### What to Build Next
1. **Auto-retry failed** — `tracker.py` + `/retry` command
2. **File size filter** — 4 lines in `is_allowed_file()` in `utils.py`
3. **Quality/language filter** — extend `is_allowed_file()` in `utils.py`
4. **Daily stats report** — `apscheduler` midnight task
5. **Scheduled bulk dump** — nightly `apscheduler` run

### Known Limitations
- JSON files on Railway/Heroku ephemeral disk reset on full redeploy. Use volumes (Fly/Render/Docker) for production persistence.
- `seen.json` is memory-cached on first load (`_cache` in `seen_db.py`) — reloads from disk on restart automatically.
- `multi_forwarder.py` Account 1 is also the command listener — DM the same number as with `forwarder.py`.
- `discovery.py` `search_public_chats()` returns limited results for newer Telegram accounts. `find_joined_sources()` (scans existing dialogs) is more reliable.
- `setup.sh` uses `screen` for background processes. `tmux` or `systemd` are more robust for production VPS — see comments in setup.sh.
