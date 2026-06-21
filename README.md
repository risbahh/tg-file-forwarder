# 🚀 TG File Forwarder

A production-ready Telegram **userbot** that automatically forwards movie/series files from source groups or channels into your **auto-filter bot's index channel** — building your database on autopilot.

---

## Features

- ✅ **Real-time forwarding** — new files forwarded the instant they're posted
- ✅ **Bulk history dump** — pull all historical files from any group you're a member of
- ✅ **Resume support** — bulk dump picks up exactly where it left off after a crash
- ✅ **Flood-wait handling** — auto-sleeps on Telegram rate limits, never crashes
- ✅ **Multi-source** — watch unlimited source groups/channels simultaneously
- ✅ **File type filter** — forward only documents, videos, audio, or photos
- ✅ **Progress tracking** — live logs + optional Telegram log channel summary
- ✅ **Railway deploy** — one-click deploy, runs 24/7 for free

---

## How It Works

```
[Cine Alliance Group]  ──┐
[Movies HD Hub]         ──┼──▶  [Your Private Index Channel]  ──▶  [Your Auto-Filter Bot]
[Any Source Channel]   ──┘
```

1. Userbot joins source groups as a normal member
2. Every new file posted → instantly forwarded to your index channel
3. Your auto-filter bot indexes the file → users can search it immediately

---

## Quick Setup

### Step 1 — Get Telegram API Credentials
1. Go to [my.telegram.org](https://my.telegram.org) → **API Development Tools**
2. Copy your `API_ID` and `API_HASH`

### Step 2 — Generate Session String (run locally once)
```bash
pip install pyrofork tgcrypto-pyrofork python-dotenv
python session_gen.py
```
Copy the printed string — this is your `SESSION_STRING`.

> ⚠️ **Keep SESSION_STRING secret** — it gives full access to your Telegram account.

### Step 3 — Get Your Destination Channel ID
Your index channel ID is a negative number like `-1001234567890`.
To find it: forward any message from the channel to [@userinfobot](https://t.me/userinfobot).

### Step 4 — Deploy to Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template)

1. Fork this repo → Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**
2. Select your fork
3. Go to **Variables** tab → add all variables from `.env.example`:

| Variable | Required | Description |
|---|---|---|
| `API_ID` | ✅ | From my.telegram.org |
| `API_HASH` | ✅ | From my.telegram.org |
| `SESSION_STRING` | ✅ | Output from `session_gen.py` |
| `DEST_CHANNEL` | ✅ | Your index channel ID (negative) |
| `SOURCE_CHATS` | ✅ | Comma-separated usernames/IDs to watch |
| `DELAY` | ⚡ | Seconds between forwards (default: 3) |
| `ALLOWED_TYPES` | ⚡ | `document,video` (default) |
| `LOG_CHANNEL` | ⚡ | Get summary messages here |
| `MAX_RETRIES` | ⚡ | Retries before skip (default: 5) |

4. Railway auto-deploys — check **Deployments** tab for ✅ Active

---

## Usage

### Real-time Forwarding (default — runs 24/7)
```bash
python forwarder.py
```
Watches `SOURCE_CHATS` for new files. Runs forever. Perfect for Railway.

### Bulk History Dump (one-time import)
```bash
# Dump all SOURCE_CHATS from config
python bulk_dump.py

# Dump a specific chat
python bulk_dump.py CineAlliance
python bulk_dump.py -100987654321
```
Pulls every historical file. Resumes automatically if interrupted.
Can take hours for large groups — run overnight.

---

## Tips from Pro Operators

| Tip | Details |
|---|---|
| **Use a secondary account** | Never run userbots on your main Telegram account |
| **Increase DELAY** | If you get repeated FloodWait errors, set `DELAY=5` or higher |
| **Multi-source simultaneously** | Add 20+ sources to `SOURCE_CHATS` — the bot handles all at once |
| **Run bulk dump first** | Import history before enabling real-time to fill the database fast |
| **Check LOG_CHANNEL** | Set a private channel as `LOG_CHANNEL` to monitor progress from Telegram |
| **Restart to resume** | bulk_dump.py saves progress to `forwarded.json` — safe to stop/start |

---

## Project Structure

```
tg-file-forwarder/
├── forwarder.py      # Real-time watcher (Railway entry point)
├── bulk_dump.py      # One-time historical dump with resume
├── session_gen.py    # Run once locally to generate SESSION_STRING
├── config.py         # All settings from environment variables
├── utils.py          # Flood-wait safe forward + file helpers
├── tracker.py        # Progress tracking for bulk dump
├── requirements.txt  # pyrofork + tgcrypto
├── Procfile          # Railway process definition
├── railway.toml      # Railway deploy config
└── .env.example      # All variables with descriptions
```

---

## ⚠️ Important Notes

- This tool uses a **user account**, not a bot — it can access any group you're a member of
- Forwarding copyrighted content may violate Telegram ToS — use responsibly
- Always add delays (`DELAY=3` minimum) to avoid hitting Telegram rate limits
- The `SESSION_STRING` grants full account access — never share it or commit it to git

---

## Related

- [Auto-Filter Bot](https://github.com/azizthekiller123/Auto-filter-bot-4) — the bot that indexes and serves your files
