"""
Caption Watermark Remover — caption_cleaner.py
───────────────────────────────────────────────
Strips group/channel watermarks and promotional text from file captions
before forwarding to the index channel. Keeps the index clean.

Patterns removed:
  • @channel_name mentions
  • t.me/... links
  • http(s):// URLs
  • [TamilMV], [www.1337x.to] style tags
  • "Powered by", "Source:", "Join:", "Provided by" lines
  • Leading/trailing whitespace and blank lines
"""
import os
import re

# Toggle: set CLEAN_CAPTIONS=false to disable (default: enabled)
_ENABLED = os.environ.get("CLEAN_CAPTIONS", "true").lower() not in ("false", "0", "no")

_PATTERNS = [
    re.compile(r'https?://\S+', re.I),               # http/https URLs
    re.compile(r't\.me/\S+', re.I),                  # t.me/ links without http
    re.compile(r'@[A-Za-z0-9_]{3,}'),                # @username mentions
    re.compile(r'\[https?://[^\]]+\]'),               # [http://...] bracketed links
    re.compile(r'\[www\.[^\]]+\]', re.I),             # [www.site.com] tags
    re.compile(r'\[[A-Za-z0-9._\-\s]{3,40}\]'),      # [TamilMV], [MoviesHub] tags
    re.compile(r'^(Powered|Source|Join|Follow|Provided|Shared|Posted|Download)\s*(by|:)[^\n]*', re.I | re.M),
    re.compile(r'^(For\s+more|More\s+movies|Visit\s+us)[^\n]*', re.I | re.M),
    re.compile(r'^\s*[-—•|]+\s*$', re.M),            # separator lines
]


def clean(caption: str | None) -> str | None:
    """
    Strip watermarks from a Telegram caption string.
    Returns None if the result is empty (so the forwarded file has no caption).
    Returns the original unmodified caption if CLEAN_CAPTIONS=false.
    """
    if not caption or not _ENABLED:
        return caption or None

    result = caption
    for pattern in _PATTERNS:
        result = pattern.sub("", result)

    # Collapse multiple blank lines → single blank line
    result = re.sub(r'\n{3,}', '\n\n', result)
    result = result.strip()

    return result if result else None


def is_enabled() -> bool:
    return _ENABLED
