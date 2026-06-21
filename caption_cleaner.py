"""
Caption Watermark Remover — caption_cleaner.py
───────────────────────────────────────────────
Strips watermarks from captions before forwarding.

Built-in patterns (always active):
  @mentions, t.me/ links, http URLs, [tag] blocks,
  "Powered by / Source: / Join:" lines, separator lines

Custom patterns (editable at runtime via /strippatterns):
  Loaded from strip_patterns.json — full Python regex supported
"""
import os
import re

_ENABLED = os.environ.get("CLEAN_CAPTIONS", "true").lower() not in ("false", "0", "no")

_BUILTIN = [
    re.compile(r'https?://\S+', re.I),
    re.compile(r't\.me/\S+', re.I),
    re.compile(r'@[A-Za-z0-9_]{3,}'),
    re.compile(r'\[https?://[^\]]+\]'),
    re.compile(r'\[www\.[^\]]+\]', re.I),
    re.compile(r'\[[A-Za-z0-9._\-\s]{3,40}\]'),
    re.compile(r'^(Powered|Source|Join|Follow|Provided|Shared|Posted|Download)\s*(by|:)[^\n]*', re.I | re.M),
    re.compile(r'^(For\s+more|More\s+movies|Visit\s+us)[^\n]*', re.I | re.M),
    re.compile(r'^\s*[-—•|]+\s*$', re.M),
]


def _custom_patterns() -> list:
    """Load user-defined patterns from strip_patterns.json (runtime-editable)."""
    try:
        from strip_patterns import load as _load
        raw = _load()
        compiled = []
        for p in raw:
            try:
                compiled.append(re.compile(p, re.I | re.M))
            except re.error:
                pass
        return compiled
    except ImportError:
        return []


def clean(caption: str | None) -> str | None:
    """
    Strip watermarks from caption.
    Returns None if result is empty (forwarded file will have no caption).
    Returns caption unchanged if CLEAN_CAPTIONS=false.
    """
    if not caption or not _ENABLED:
        return caption or None

    result = caption
    for p in _BUILTIN:
        result = p.sub("", result)
    for p in _custom_patterns():
        result = p.sub("", result)

    result = re.sub(r'\n{3,}', '\n\n', result)
    result = result.strip()
    return result if result else None


def is_enabled() -> bool:
    return _ENABLED
