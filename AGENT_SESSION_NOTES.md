# Agent Session Notes — Auto-Filter Bot Project
_Last updated: Session 6 | Date: 2026-06-21_

---

## 🗂 Repos

| Repo | URL | Purpose |
|---|---|---|
| **Main Bot** | https://github.com/azizthekiller123/Auto-filter-bot-4 | Telegram auto-filter movie bot (production, on Railway) |
| **File Forwarder** | https://github.com/azizthekiller123/tg-file-forwarder | Userbot that feeds files into the main bot's index channel |
| **Forwarder v2** | https://github.com/bbaziz4155/Telegram-Forwarder-3 | Older bulk-copy bot (Telethon-based) — mined for feature ideas |

---

## 🤖 Main Bot — `Auto-filter-bot-4`

### Architecture
```
User types movie name in a group
        ↓
Auto-filter bot searches MongoDB (indexed from private channels)
        ↓
Bot sends file buttons → user clicks → file forwarded from index channel
        ↓
File auto-deletes after N minutes (anti-leech)
```

### Stack
- **Library**: `pyrofork==2.3.45` — imported as `pyrogram` (NEVER change import names)
- **DB**: MongoDB Atlas (motor async driver) — `MULTIPLE_DB=True`, DB1 threshold 407MB → auto-switches to DB2
- **Deploy**: Railway (auto-deploy on GitHub push to main, ~2 min lag)
- **Web server**: aiohttp on `PORT` env var (default 8080)
- **Health URL**: `https://web-production-ef06e.up.railway.app/ping`

### Critical Rules
- `plugins/pmfilter.py` line 848 has catch-all `group=0` handler → **new callbacks MUST use `group=-1`**
- `/ban` and `/unban` canonical handlers are in `admin_features.py` ONLY
- `temp.BANNED_USERS` / `temp.BANNED_CHATS` `.remove()` calls MUST be in `try/except ValueError: pass`
- Push to GitHub → Railway auto-deploys → never edit Railway directly

---

## 📡 File Forwarder — `tg-file-forwarder`

### What It Does
A **userbot** that joins source groups as a normal member, watches for new file messages, and copies them to the main bot's private index channel. Main bot auto-indexes → files become searchable instantly.

### Complete File Structure (Session 6)
```
tg-file-forwarder/
├── forwarder.py        # Real-time watcher (primary)
├── multi_forwarder.py  # Multi-account version (pool of 2–3 accounts)
├── bot_capture.py      # Captures files from a specific bot per group
├── bulk_dump.py        # One-time historical dump with resume
├── account_pool.py     # Rotates accounts on FloodWait
├── chats_db.py         # Dynamic chat list (chats.json)
├── router.py           # Routes by filename pattern → DEST_MOVIES/SERIES/SOUTH
├── seen_db.py          # Dedup via file_unique_id (seen.json)
├── stats_db.py         # Per-source forwarding stats (stats.json)   ← NEW
├── strip_patterns.py   # Runtime-editable watermark patterns         ← NEW
├── caption_suffix.py   # Persistent caption suffix                   ← NEW
├── caption_cleaner.py  # Strip watermarks — loads from strip_patterns.json
├── utils.py            # safe_forward() — applies cleaning + suffix
├── discovery.py        # Scan/search for movie groups
├── dashboard.py        # Web dashboard at PORT
├── bots_db.py          # Bot-group mapping for bot_capture.py
├── tracker.py          # Resume progress for bulk_dump.py
├── session_gen.py      # Generate SESSION_STRING once locally
├── config.py           # All settings from env vars
├── requirements.txt    # pyrofork==2.3.45 + tgcrypto-pyrofork + aiohttp
├── Procfile            # worker: python forwarder.py
└── .env.example        # All variables documented
```

### Complete Command Reference
| Command | What it does | File |
|---|---|---|
| `/addchat <chat>` | Add source group — instant, no redeploy | both |
| `/removechat <chat>` | Remove source group | both |
| `/listchats` | List all active sources | both |
| `/fwrstatus` | Full stats: session, routing, suffix, patterns | forwarder.py |
| `/poolstatus` | Pool account status + full stats | multi_forwarder.py |
| `/route <src> <dest>` | Override destination for a source group | both |
| `/routes` | Show all routing rules | both |
| `/dupstats` | Duplicate detection stats | forwarder.py |
| `/srcstats` | Files forwarded per source group | both |
| `/resetdups` | Two-step: clear seen.json (shows warning first) | both |
| `/pausefwd` | Pause all forwarding instantly | both |
| `/resumefwd` | Resume forwarding, shows count dropped | both |
| `/setcaption <text>` | Append custom line to every forwarded caption | both |
| `/setcaption off` | Remove the suffix | both |
| `/setcaption` | Show current suffix | both |
| `/strippatterns list` | Show custom watermark strip patterns | both |
| `/strippatterns add <regex>` | Add pattern — validated regex, instant effect | both |
| `/strippatterns remove <n>` | Remove pattern by list number | both |
| `/cleancaptions [channel_id]` | Scan & edit captions in index channel in-place | both |
| `/stopcleaning` | Cancel a running /cleancaptions job | both |
| `/discover` | Scan joined groups for movie sources | forwarder.py |
| `/suggest <keyword>` | Search Telegram for public groups | forwarder.py |

### Session Watchdog
Both forwarder.py and multi_forwarder.py now have a background task (`_session_watchdog`) that:
- Pings `get_me()` every 5 minutes
- On `SessionRevoked` / `AuthKeyUnregistered` / `UserDeactivated`:
  - Sends alert to LOG_CHANNEL and all ADMINS
  - Message tells user to regenerate SESSION_STRING and redeploy
  - Calls `os._exit(1)` so Railway auto-restarts

### How /cleancaptions Works
- Uses `client.get_chat_history(DEST_CHANNEL)` to iterate all messages
- For each message with a caption: applies `clean()` from caption_cleaner.py
- If cleaned != original: calls `msg.edit_caption(caption=cleaned)` with 0.5s rate-limit delay
- Live progress updates every 5 seconds (scanned / edited / errors)
- `/stopcleaning` sets a flag that exits the loop after the current message
- Requires the userbot to have "Edit Messages" permission in the destination channel

### How /strippatterns Works
- Patterns stored in `strip_patterns.json` (auto-created)
- `caption_cleaner.py` calls `strip_patterns.load()` on every caption clean (dynamic, no restart needed)
- Each pattern is a Python regex validated before saving
- Built-in patterns (in caption_cleaner.py) always run too — these are additive

### How /setcaption Works
- Suffix stored in `caption_suffix.json`
- `utils.safe_forward()` reads it via `caption_suffix.get()` for every forward
- If cleaned caption exists: appends `\n\n<suffix>`
- If caption was fully stripped: suffix alone becomes the caption
- Takes effect immediately on next forward, no restart needed

### How /srcstats Works
- `stats_db.py` writes to `stats.json` after every successful forward
- Keyed by `chat_id` — tracks title, count, first_seen, last_seen
- Sorted by count descending — shows top 20 sources
- Grand total shown at bottom
- Thread-safe (file lock)

### /resetdups Two-Step Confirmation
- Step 1: `/resetdups` → shows current ID count + warning (no changes made)
- Step 2: `/resetdups confirm` → clears seen.json + notifies LOG_CHANNEL
- Does NOT delete any files from Telegram

### How seen_db.py Works
- In-memory set + seen.json on disk
- `file_unique_id` = Telegram's globally unique file fingerprint
- Same file in two groups = same ID → forwarded once, skipped on repeat

### How account_pool.py Works
- Loads SESSION_STRING, SESSION_STRING_2, SESSION_STRING_3
- On FloodWait: marks account unavailable, switches to next
- On ChatForwardsRestricted: falls back to copy_message()

### Railway Environment Variables
| Var | Required | Description |
|---|---|---|
| `API_ID` | ✅ | From my.telegram.org |
| `API_HASH` | ✅ | From my.telegram.org |
| `SESSION_STRING` | ✅ | Run session_gen.py locally once |
| `DEST_CHANNEL` | ✅ | Main bot's index channel ID |
| `SOURCE_CHATS` | ⚡ | Optional — can use /addchat only |
| `ADMINS` | ⚡ | Your Telegram user ID |
| `LOG_CHANNEL` | ⚡ | Get startup/alert notifications |
| `DEST_MOVIES` / `DEST_SERIES` / `DEST_SOUTH` | ⚡ | Multi-channel routing |
| `CLEAN_CAPTIONS` | ⚡ | true/false (default: true) |
| `SESSION_STRING_2` / `SESSION_STRING_3` | ⚡ | Extra accounts for pool |

---

## 🐛 All Bugs Fixed — Sessions 1–5 (7 bugs)

| File | Bug | Fix |
|---|---|---|
| `dashboard.py` | `chats_cfg.keys()` showed wrong data | `chats_cfg.get("chats", [])` |
| `utils.py` | `caption=None` kept watermarks | `caption=cleaned if cleaned is not None else ""` |
| `config.py` | SOURCE_CHATS mandatory — startup crash | Made optional |
| `multi_forwarder.py` | `/route` ValueError crash | try/except ValueError |
| `account_pool.py` | ChatForwardsRestricted no fallback | Added copy_message fallback |
| `router.py` | `-100` prefix doubled in key | Fixed prefix logic |
| `seen_db.py` + `router.py` | Unclosed file handles | Changed to `with open()` |

---

## ✨ Features Added — Session 5–6

| Feature | File(s) | What it does |
|---|---|---|
| `/resetdups` | forwarder.py, multi_forwarder.py | Two-step confirm to clear seen.json |
| `/pausefwd` / `/resumefwd` | forwarder.py, multi_forwarder.py | Pause/resume forwarding without restart |
| `/srcstats` | forwarder.py, multi_forwarder.py + stats_db.py | Per-source forwarding count + all-time total |
| Session watchdog | forwarder.py, multi_forwarder.py | Alert + exit on session revocation (checks every 5 min) |
| `/cleancaptions` / `/stopcleaning` | forwarder.py, multi_forwarder.py | Edit existing captions in index channel in-place |
| `/setcaption` | forwarder.py, multi_forwarder.py + caption_suffix.py | Append custom suffix to every forwarded caption |
| `/strippatterns` | forwarder.py, multi_forwarder.py + strip_patterns.py | Runtime-editable watermark patterns |
| Dynamic strip patterns | caption_cleaner.py | Loads strip_patterns.json on every clean (no restart) |
| Caption suffix in forward | utils.py + caption_suffix.py | Appended after watermark stripping |

---

## 🔄 Workflow — Pushing to GitHub

Always use GitHub API sequentially (never parallel — SHA conflicts):
```bash
SHA=$(curl -s -H "Authorization: Bearer $GITHUB_PERSONAL_ACCESS_TOKEN" \
  -H "User-Agent: replit-agent" \
  "https://api.github.com/repos/azizthekiller123/REPO/contents/FILE" \
  | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{const j=JSON.parse(d);console.log(j.sha);});")

curl -s -X PUT \
  -H "Authorization: Bearer $GITHUB_PERSONAL_ACCESS_TOKEN" \
  -H "Content-Type: application/json" -H "User-Agent: replit-agent" \
  "https://api.github.com/repos/azizthekiller123/REPO/contents/FILE" \
  -d "{\"message\":\"feat: description\",\"content\":\"$(base64 -w0 file.py)\",\"sha\":\"$SHA\"}"
```
- `python3` not available in Replit sandbox — use `node -e` for JSON parsing
- Railway auto-deploys on every push (~2 min)
- Health check: `curl https://web-production-ef06e.up.railway.app/ping`

---

## ⚠️ Critical Rules

1. **NEVER** change `from pyrogram import` — library is pyrofork, imported as pyrogram
2. **NEVER** push files in parallel — SHA conflicts
3. **NEVER** edit Railway directly — push to GitHub only
4. **ALWAYS** wrap `temp.BANNED_*` `.remove()` in `try/except ValueError: pass` (main bot)
5. **ALWAYS** use `group=-1` for new callbacks in main bot
6. `SOURCE_CHATS` is optional in config.py (fixed session 5)
7. `python3` not available in Replit sandbox — use `node -e`

---

## 📋 What to Work on Next

1. Add `/health` route to main bot's `route.py` with real DB status check
2. Add `/addchat` `/removechat` to main bot for managing index channels dynamically
3. Add per-source forwarding stats to web dashboard (dashboard.py reads stats.json)
4. Add `/ignorechat <chat>` — skip files from specific groups even if in source list
5. Referral/invite tracking system for main bot
