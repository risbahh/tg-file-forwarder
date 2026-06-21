# ── TG File Forwarder — Dockerfile ────────────────────────────────────────
# Works on any Docker host: Railway, Render, Fly.io, Heroku, DigitalOcean,
# VPS, or locally with docker-compose.
#
# Build:   docker build -t tg-forwarder .
# Run:     docker run --env-file .env -p 8080:8080 -v fwd_data:/app/data tg-forwarder
# ─────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# Metadata
LABEL maintainer="tg-file-forwarder"
LABEL description="Telegram userbot that feeds files into an auto-filter bot index channel"

WORKDIR /app

# Install system deps + Python packages in one layer (keeps image small)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Persistent data directory — mount a volume here to survive restarts/redeploys
RUN mkdir -p /app/data

# Point all JSON storage files to the persistent data volume by default.
# Override any of these at runtime via environment variables if needed.
ENV TRACKER_FILE=/app/data/forwarded.json
ENV CHATS_DB_FILE=/app/data/chats.json
ENV BOTS_DB_FILE=/app/data/bots.json
ENV SEEN_DB_FILE=/app/data/seen.json
ENV ROUTING_FILE=/app/data/routing.json
ENV PORT=8080

# Expose dashboard port
EXPOSE 8080

# Default: run main forwarder.
# Override at runtime: docker run ... tg-forwarder python bot_capture.py
# Or for multi-account: docker run ... tg-forwarder python multi_forwarder.py
CMD ["python", "forwarder.py"]
