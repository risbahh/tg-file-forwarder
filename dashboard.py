"""
Web Dashboard — dashboard.py
──────────────────────────────
A live status page served at your Railway URL.
Run as background task inside forwarder.py (recommended) or standalone.

GET /         → HTML dashboard
GET /api/stats → JSON stats
GET /health    → {"status": "ok"} health check

Integrated into forwarder.py:
  asyncio.create_task(start_dashboard(stats_getter=lambda: _stats))

Standalone (if preferred):
  python dashboard.py   ← reads JSON files for stats
"""
import asyncio
import json
import logging
import os
import time
from pathlib import Path

import aiohttp
from aiohttp import web

logger    = logging.getLogger(__name__)
_start_ts = time.time()

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>TG File Forwarder — Dashboard</title>
  <meta http-equiv="refresh" content="30">
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
          background:#0f0f1a;color:#e0e0e0;min-height:100vh;padding:24px}}
    h1{{font-size:1.5rem;color:#fff;margin-bottom:4px}}
    .sub{{color:#888;font-size:.85rem;margin-bottom:28px}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:28px}}
    .card{{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:12px;padding:20px}}
    .card .val{{font-size:2rem;font-weight:700;color:#7c6aff;line-height:1}}
    .card .lbl{{font-size:.8rem;color:#888;margin-top:6px;text-transform:uppercase;letter-spacing:.04em}}
    .section{{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:12px;padding:20px;margin-bottom:16px}}
    .section h2{{font-size:1rem;color:#aaa;margin-bottom:12px;text-transform:uppercase;letter-spacing:.05em}}
    table{{width:100%;border-collapse:collapse}}
    td,th{{text-align:left;padding:8px 10px;border-bottom:1px solid #2a2a4a;font-size:.875rem}}
    th{{color:#888;font-weight:500}}
    .ok{{color:#4ade80}} .warn{{color:#facc15}} .err{{color:#f87171}}
    .badge{{display:inline-block;padding:2px 8px;border-radius:99px;font-size:.75rem;font-weight:600}}
    .badge-ok{{background:#14532d;color:#4ade80}}
    .badge-warn{{background:#422006;color:#facc15}}
    footer{{color:#555;font-size:.75rem;text-align:center;margin-top:24px}}
  </style>
</head>
<body>
  <h1>📡 TG File Forwarder</h1>
  <p class="sub">Auto-refreshes every 30 s &nbsp;|&nbsp; Last updated: {updated}</p>
  <div class="grid">
    <div class="card"><div class="val">{forwarded}</div><div class="lbl">Forwarded this session</div></div>
    <div class="card"><div class="val">{seen_total}</div><div class="lbl">Total unique files (seen DB)</div></div>
    <div class="card"><div class="val">{dup_skipped}</div><div class="lbl">Duplicates skipped</div></div>
    <div class="card"><div class="val">{failed}</div><div class="lbl">Failed</div></div>
    <div class="card"><div class="val">{sources}</div><div class="lbl">Active source chats</div></div>
    <div class="card"><div class="val">{uptime}</div><div class="lbl">Uptime</div></div>
  </div>
  <div class="section">
    <h2>Routing</h2>
    <table><tr><th>Type</th><th>Destination Channel</th></tr>{routing_rows}</table>
  </div>
  <div class="section">
    <h2>Source Chats</h2>
    <table><tr><th>Chat</th><th>Status</th></tr>{source_rows}</table>
  </div>
  <footer>TG File Forwarder — <a href="/api/stats" style="color:#7c6aff">JSON API</a></footer>
</body>
</html>"""


def _uptime_str(seconds: float) -> str:
    h, r = divmod(int(seconds), 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _read_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _gather_stats(stats_getter=None) -> dict:
    forwarded   = 0
    dup_skipped = 0
    failed      = 0

    if stats_getter:
        s = stats_getter()
        forwarded   = s.get("forwarded", 0)
        dup_skipped = s.get("skipped_dup", 0)
        failed      = s.get("failed", 0)

    seen_ids   = _read_json(os.environ.get("SEEN_DB_FILE",  "seen.json"),  [])
    # FIX: chats.json format is {"chats": [...]} — must read the list, not .keys()
    # Previously: chats_cfg.keys() returned ["chats"] instead of actual chat IDs
    chats_cfg  = _read_json(os.environ.get("CHATS_DB_FILE", "chats.json"), {})
    routes_cfg = _read_json(os.environ.get("ROUTING_FILE",  "routing.json"), {})

    seed_chats = [c.strip() for c in os.environ.get("SOURCE_CHATS", "").split(",") if c.strip()]
    # FIX: use chats_cfg.get("chats", []) to get the actual list of chat IDs
    dynamic_chats = [str(c) for c in chats_cfg.get("chats", [])]
    all_chats  = list({*seed_chats, *dynamic_chats})

    dest_default = os.environ.get("DEST_CHANNEL", "?")
    routing_info = [
        ("Default", dest_default),
        ("Movies",  os.environ.get("DEST_MOVIES",  "—")),
        ("Series",  os.environ.get("DEST_SERIES",  "—")),
        ("South",   os.environ.get("DEST_SOUTH",   "—")),
    ]

    return {
        "forwarded":    forwarded,
        "seen_total":   len(seen_ids),
        "dup_skipped":  dup_skipped,
        "failed":       failed,
        "sources":      len(all_chats),
        "all_chats":    all_chats,
        "routing":      routing_info,
        "per_source":   routes_cfg,
        "uptime_sec":   time.time() - _start_ts,
    }


async def start_dashboard(stats_getter=None, port: int | None = None):
    """
    Start the aiohttp dashboard server as a background coroutine.
    Call from forwarder.py: asyncio.create_task(start_dashboard(lambda: _stats))
    """
    _port = port or int(os.environ.get("PORT", 8080))

    async def handle_html(request):
        s = _gather_stats(stats_getter)
        routing_rows = "\n".join(
            f"<tr><td>{rtype}</td><td><code>{dest}</code></td></tr>"
            for rtype, dest in s["routing"]
        )
        source_rows = "\n".join(
            f'<tr><td><code>{chat}</code></td><td><span class="badge badge-ok">active</span></td></tr>'
            for chat in s["all_chats"]
        ) or "<tr><td colspan=2><em>No sources configured</em></td></tr>"

        html = _HTML.format(
            updated      = __import__("datetime").datetime.utcnow().strftime("%H:%M:%S UTC"),
            forwarded    = f"{s['forwarded']:,}",
            seen_total   = f"{s['seen_total']:,}",
            dup_skipped  = f"{s['dup_skipped']:,}",
            failed       = f"{s['failed']:,}",
            sources      = s["sources"],
            uptime       = _uptime_str(s["uptime_sec"]),
            routing_rows = routing_rows,
            source_rows  = source_rows,
        )
        return web.Response(text=html, content_type="text/html")

    async def handle_json(request):
        s = _gather_stats(stats_getter)
        return web.json_response(s)

    async def handle_health(request):
        return web.json_response({"status": "ok", "uptime": _uptime_str(time.time() - _start_ts)})

    app = web.Application()
    app.router.add_get("/",          handle_html)
    app.router.add_get("/api/stats", handle_json)
    app.router.add_get("/health",    handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", _port)
    await site.start()
    logger.info(f"🌐 Dashboard running at http://0.0.0.0:{_port}/")


if __name__ == "__main__":
    # Standalone mode — just serve the dashboard (reads from JSON files)
    async def _run():
        await start_dashboard()
        while True:
            await asyncio.sleep(3600)
    asyncio.run(_run())
