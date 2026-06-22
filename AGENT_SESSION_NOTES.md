# Agent Session Notes — Auto-Filter Bot Project
_Last updated: Session 7 | Date: 2026-06-21_

---

## 🗂 Repos

| Repo | URL | Purpose |
|---|---|---|
| **Main Bot** | https://github.com/azizthekiller123/Auto-filter-bot-4 | Telegram auto-filter movie bot (production, Railway) |
| **File Forwarder** | https://github.com/azizthekiller123/tg-file-forwarder | Userbot: feeds files into the main bot's index channel |
| **Thumb Cleaner** | https://github.com/azizthekiller123/tg-thumb-cleaner | NEW: removes thumbnail watermarks from @ClipmateEmpirer channel |
| **Forwarder v2** | https://github.com/bbaziz4155/Telegram-Forwarder-3 | Older Telethon-based bulk copier — mined for feature ideas |

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
- **DB**: MongoDB Atlas (motor async) — `MULTIPLE_DB=True`, DB1 threshold 407MB → auto-switches DB2
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
A userbot that joins source groups, watches for new file messages, and copies them to the main bot's private index channel.

### Complete File Structure (Session 7)
```
tg-file-forwarder/
├── forwarder.py        # Real-time watcher (primary single-account mode)
├── multi_forwarder.py  # Multi-account version (pool of 2–3 accounts)
├── bot_capture.py      # Captures files from a specific bot per group
├── bulk_dump.py        # One-time historical dump with resume
├── account_pool.py     # Rotates accounts on FloodWait
├── chats_db.py         # Dynamic chat list (chats.json)
├── router.py           # Routes by filename → DEST_MOVIES/SERIES/SOUTH
├── seen_db.py          # Dedup via file_unique_id (seen.json)
├── stats_db.py         # Per-source forwarding stats (stats.json)
├── failed_db.py        # ← NEW: Failed forwards store (failed.json)
├── strip_patterns.py   # Runtime-editable watermark patterns (strip_patterns.json)
├── caption_suffix.py   # Persistent caption suffix (caption_suffix.json)
├── caption_cleaner.py  # Strip watermarks — loads from strip_patterns.json
├── utils.py            # safe_forward() — applies cleaning + suffix + saves to failed_db
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

### Complete Command Reference (Session 7)
| Command | What it does |
|---|---|
| `/addchat <chat>` | Add source group — instant |
| `/removechat <chat>` | Remove source group |
| `/listchats` | List active sources |
| `/fwrstatus` | Full stats (session, routing, suffix, patterns) |
| `/poolstatus` | Pool account status (multi_forwarder.py only) |
| `/route <src> <dest>` | Override destination for a source group |
| `/routes` | Show all routing rules |
| `/dupstats` | Duplicate detection stats |
| `/srcstats` | Files forwarded per source group |
| `/resetdups` | Two-step confirm → clear seen.json |
| `/pausefwd` | Pause all forwarding |
| `/resumefwd` | Resume forwarding, shows count dropped |
| `/setcaption <text>` | Append custom line to every caption |
| `/setcaption off` | Remove the suffix |
| `/setcaption` | Show current suffix |
| `/strippatterns list` | Show custom watermark patterns |
| `/strippatterns add <regex>` | Add pattern — validated, instant effect |
| `/strippatterns remove <n>` | Remove pattern by number |
| `/cleancaptions [channel_id]` | Edit captions in index channel in-place |
| `/stopcleaning` | Cancel running /cleancaptions job |
| **`/failedstats`** | **← NEW: Show failed.json — count + by-source breakdown** |
| **`/retry`** | **← NEW: Re-attempt all failed forwards from failed.json** |
| **`/clearfailed`** | **← NEW: Two-step confirm → wipe failed.json** |
| `/discover` | Scan joined groups for movie sources |
| `/suggest <keyword>` | Search Telegram for public groups |

### How /retry Works (NEW — Session 7)
- `safe_forward()` in `utils.py` now calls `failed_db.save(chat_id, message_id)` after all retries fail
- `failed.json` is a persistent list of `{chat_id, message_id, ts}` entries
- `/retry` fetches each message by ID from the source group, re-attempts forward
- Successful retries removed from `failed.json`; deleted messages silently skipped
- `/failedstats` shows count + per-source breakdown + oldest entry timestamp
- `/clearfailed` requires "confirm" argument — shows count first as warning

### Why /retry Matters for Single-Account Setup
- With 1 account (no pool), FloodWait can last 5–47 minutes
- Files posted during FloodWait → silently lost without retry system
- `/retry` recovers those files once FloodWait clears

### Session Watchdog
Background task pings `get_me()` every 5 minutes. On SessionRevoked/AuthKeyUnregistered/UserDeactivated:
- Sends alert to LOG_CHANNEL and all ADMINS
- Calls `os._exit(1)` so Railway auto-restarts

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

## 🖼️ Thumbnail Cleaner — `tg-thumb-cleaner` (NEW Repo — Session 7)

### Purpose
User found channel `@ClipmateEmpirer` / group `@ClipmateZone_New` that posts movies/series with:
- Thumbnail: `@ClipmateEmpirer` text burned into the image
- Caption: `[@ClipmateEmpirer] Movie title...`

Both types of watermarks are handled by the new bot.

### How Thumbnail Removal Works
```
Detect: EasyOCR → finds text bounding boxes in thumbnail JPEG
Mask:   np.zeros mask painted over detected regions (+ dilation for AA edges)
Inpaint: cv2.inpaint TELEA fills masked area from surrounding pixels
Resize: PIL ensures result is ≤ 200KB and ≤ 320×320 (Telegram limits)
Upload: send_video(video=file_id, thumb=cleaned_path) — no full video download
```

### 5 Bugs Fixed in Session 7
| Bug | Fix |
|---|---|
| `_is_source()` built `"-100{username}"` — wrong for numeric IDs | Simplified to direct string compare + username match |
| `filters.channel` only — misses supergroups/groups | Changed to `(filters.channel \| filters.group)` |
| Document thumbs never cleaned | Added `_has_thumbs()` + `_get_thumb_file_id()` helpers |
| `tempfile.mktemp()` deprecated | Changed to `tempfile.mkstemp()` |
| `admin_only` missing `functools.wraps` | Added `@functools.wraps(func)` |

### Commands (tg-thumb-cleaner)
| Command | What |
|---|---|
| `/status` | Session stats — forwarded, cleaned, failed |
| `/preview` | Reply to any image → get watermark-removed version |
| `/help` | Help text |

### See Full Context
→ `AGENT_CONTEXT.md` in the `tg-thumb-cleaner` repo for complete technical details.

---

## 🐛 All Bugs Fixed — Sessions 1–5 (7 bugs)

| File | Bug | Fix |
|---|---|---|
| `dashboard.py` | `chats_cfg.keys()` wrong data | `chats_cfg.get("chats", [])` |
| `utils.py` | `caption=None` kept watermarks | Fixed to empty string fallback |
| `config.py` | SOURCE_CHATS mandatory — crash | Made optional |
| `multi_forwarder.py` | `/route` ValueError crash | try/except ValueError |
| `account_pool.py` | ChatForwardsRestricted no fallback | Added copy_message fallback |
| `router.py` | `-100` prefix doubled | Fixed prefix logic |
| `seen_db.py` + `router.py` | Unclosed file handles | Changed to `with open()` |

---

## ✨ Features Added — Sessions 5–7

| Feature | Session | File(s) |
|---|---|---|
| `/resetdups` | 6 | forwarder.py, multi_forwarder.py |
| `/pausefwd` / `/resumefwd` | 6 | forwarder.py, multi_forwarder.py |
| `/srcstats` + stats_db.py | 6 | both + stats_db.py |
| Session watchdog | 6 | both forwarder files |
| `/cleancaptions` + `/stopcleaning` | 6 | both forwarder files |
| `/setcaption` + caption_suffix.py | 6 | both + caption_suffix.py |
| `/strippatterns` + strip_patterns.py | 6 | both + strip_patterns.py |
| **`/retry` + `/failedstats` + `/clearfailed`** | **7** | **both + failed_db.py** |
| **failed_db.py** | **7** | **new file** |
| **utils.py auto-saves failures** | **7** | **utils.py** |
| **tg-thumb-cleaner new repo** | **7** | **separate repo** |
| **Thumbnail watermark removal** | **7** | **thumb_cleaner.py** |
| **/preview test command** | **7** | **main.py (thumb-cleaner)** |
| **/bulk + /stopbulk** | **7** | **main.py (thumb-cleaner)** |

---

## 🔄 Workflow — Pushing to GitHub

Always use GitHub API **sequentially** (never parallel within same repo — SHA conflicts):
```bash
SHA=$(curl -s -H "Authorization: Bearer $GITHUB_PERSONAL_ACCESS_TOKEN" \
  -H "User-Agent: replit-agent" \
  "https://api.github.com/repos/azizthekiller123/REPO/contents/FILE" \
  | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{const j=JSON.parse(d);console.log(j.sha||'');});")

curl -s -X PUT \
  -H "Authorization: Bearer $GITHUB_PERSONAL_ACCESS_TOKEN" \
  -H "Content-Type: application/json" -H "User-Agent: replit-agent" \
  "https://api.github.com/repos/azizthekiller123/REPO/contents/FILE" \
  -d "{\"message\":\"feat: description\",\"content\":\"$(base64 -w0 file.py)\",\"sha\":\"$SHA\"}"
```
- `python3` NOT available in Replit sandbox — use `node -e` for JSON parsing
- Railway auto-deploys on push (~2 min)
- Health check: `curl https://web-production-ef06e.up.railway.app/ping`

---

## ⚠️ Critical Rules

1. **NEVER** change `from pyrogram import` — library is pyrofork, imported as pyrogram
2. **NEVER** push files in parallel within same repo — SHA conflicts
3. **NEVER** edit Railway directly — push to GitHub only
4. **ALWAYS** wrap `temp.BANNED_*` `.remove()` in `try/except ValueError: pass` (main bot)
5. **ALWAYS** use `group=-1` for new callbacks in main bot (pmfilter.py catch-all at group=0)
6. `SOURCE_CHATS` is optional in config.py (fixed session 5)
7. `python3` not available in Replit sandbox — use `node -e`

---

## 📋 What to Work on Next

1. `/bulk` for tg-thumb-cleaner — scan historical messages in source channel, clean+forward all
2. Add `/addpattern` to tg-thumb-cleaner (like `/strippatterns` in forwarder)
3. Add deduplication (seen.json) to tg-thumb-cleaner
4. Add per-source stats to web dashboard (dashboard.py reads stats.json)
5. Add `/ignorechat <chat>` to forwarder — skip a source without removing it
6. Add `/health` route to main bot's route.py with real DB status check
