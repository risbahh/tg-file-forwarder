import os
from dotenv import load_dotenv
load_dotenv()

def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise SystemExit(f"\n❌ FATAL: {key} is not set! Add it to your Railway Variables tab.\n")
    return val

def _int_require(key: str) -> int:
    return int(_require(key))

# ── Required ──────────────────────────────────────────────
API_ID          = _int_require("API_ID")
API_HASH        = _require("API_HASH")
SESSION_STRING  = _require("SESSION_STRING")   # generate with: python session_gen.py
DEST_CHANNEL    = int(_require("DEST_CHANNEL"))  # your private index channel ID (negative int)

# Comma-separated list of source group/channel usernames or IDs
# e.g.  SOURCE_CHATS=CineAlliance,MoviesDump,-100123456789
# Optional — leave blank if you prefer to use /addchat commands only
SOURCE_CHATS_RAW = os.environ.get("SOURCE_CHATS", "")
SOURCE_CHATS = [
    int(c.strip()) if c.strip().lstrip("-").isdigit() else c.strip()
    for c in SOURCE_CHATS_RAW.split(",")
    if c.strip()
] if SOURCE_CHATS_RAW.strip() else []

# ── Optional tuning ───────────────────────────────────────
DELAY          = float(os.environ.get("DELAY", "3"))          # seconds between each forward
FLOOD_EXTRA    = int(os.environ.get("FLOOD_EXTRA", "5"))      # extra seconds added on top of FloodWait
MAX_RETRIES    = int(os.environ.get("MAX_RETRIES", "5"))      # retries before skipping a file
BATCH_SIZE     = int(os.environ.get("BATCH_SIZE", "200"))     # messages fetched per API call in bulk mode

# File types to forward (comma-separated: document, video, audio, photo)
ALLOWED_TYPES  = [t.strip() for t in os.environ.get("ALLOWED_TYPES", "document,video").split(",")]

# Optional log channel — get a summary message after bulk dump completes
LOG_CHANNEL    = int(os.environ.get("LOG_CHANNEL", "0")) or None

# Path to progress tracker file (bulk_dump.py skips already-forwarded message IDs)
TRACKER_FILE   = os.environ.get("TRACKER_FILE", "forwarded.json")
