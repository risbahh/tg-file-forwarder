#!/bin/bash
# ── TG File Forwarder — VPS Quick Setup ──────────────────────────────────
# Tested on Ubuntu 22.04 / Debian 11 / 12
# Runs the forwarder as a background screen session.
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
#
# After running:
#   screen -r forwarder    ← view live logs
#   Ctrl+A then D          ← detach (leave running in background)
#
# To update after a git pull:
#   screen -S forwarder -X quit   ← stop old session
#   ./setup.sh                    ← restart
# ─────────────────────────────────────────────────────────────────────────

set -e

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
RESET="\033[0m"

info()    { echo -e "${GREEN}▶ $*${RESET}"; }
warn()    { echo -e "${YELLOW}⚠ $*${RESET}"; }
success() { echo -e "${GREEN}✓ $*${RESET}"; }
die()     { echo -e "${RED}✗ $*${RESET}"; exit 1; }

echo -e "${BOLD}📡 TG File Forwarder — VPS Setup${RESET}"
echo "──────────────────────────────────────────"

# ── 1. Check Python ───────────────────────────────────────────────────────
info "Checking Python..."
if ! command -v python3 &>/dev/null; then
    info "Installing Python 3..."
    sudo apt update -qq && sudo apt install -y python3 python3-pip python3-venv
fi
PYTHON_VER=$(python3 --version 2>&1)
success "Found: $PYTHON_VER"

# ── 2. Check/install screen ───────────────────────────────────────────────
if ! command -v screen &>/dev/null; then
    info "Installing screen..."
    sudo apt install -y screen
fi
success "screen available"

# ── 3. Install Python deps ────────────────────────────────────────────────
info "Installing Python dependencies..."
pip3 install --quiet -r requirements.txt
success "Dependencies installed"

# ── 4. Check .env file ────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        warn ".env not found — copying .env.example as .env"
        cp .env.example .env
        echo ""
        warn "IMPORTANT: Edit .env before continuing!"
        warn "  nano .env"
        warn ""
        warn "Required values to fill in:"
        warn "  API_ID, API_HASH, SESSION_STRING, DEST_CHANNEL, ADMINS"
        echo ""
        read -p "Press Enter after editing .env to continue..." _
    else
        die ".env file not found. Create it from .env.example"
    fi
fi
success ".env found"

# ── 5. Check required env vars ────────────────────────────────────────────
info "Checking required environment variables..."
source .env 2>/dev/null || true
MISSING=()
[ -z "$API_ID" ]          && MISSING+=("API_ID")
[ -z "$API_HASH" ]        && MISSING+=("API_HASH")
[ -z "$SESSION_STRING" ]  && MISSING+=("SESSION_STRING")
[ -z "$DEST_CHANNEL" ]    && MISSING+=("DEST_CHANNEL")

if [ ${#MISSING[@]} -gt 0 ]; then
    die "Missing required vars in .env: ${MISSING[*]}"
fi
success "All required vars present"

# ── 6. Create data directory ──────────────────────────────────────────────
mkdir -p data
success "Data directory ready at ./data/"

# Export data paths unless already set
export TRACKER_FILE="${TRACKER_FILE:-./data/forwarded.json}"
export CHATS_DB_FILE="${CHATS_DB_FILE:-./data/chats.json}"
export BOTS_DB_FILE="${BOTS_DB_FILE:-./data/bots.json}"
export SEEN_DB_FILE="${SEEN_DB_FILE:-./data/seen.json}"
export ROUTING_FILE="${ROUTING_FILE:-./data/routing.json}"

# ── 7. Choose script to run ───────────────────────────────────────────────
echo ""
echo -e "${BOLD}Which forwarder to run?${RESET}"
echo "  1) forwarder.py       — all-file capture (recommended)"
echo "  2) bot_capture.py     — bot-response only capture"
echo "  3) multi_forwarder.py — multi-account (needs SESSION_STRING_2)"
echo ""
read -p "Enter 1, 2, or 3 [default: 1]: " CHOICE
CHOICE=${CHOICE:-1}

case $CHOICE in
    1) SCRIPT="forwarder.py" ;;
    2) SCRIPT="bot_capture.py" ;;
    3) SCRIPT="multi_forwarder.py" ;;
    *) SCRIPT="forwarder.py" ;;
esac

# ── 8. Kill any running session ───────────────────────────────────────────
if screen -list | grep -q "forwarder"; then
    warn "Stopping existing forwarder session..."
    screen -S forwarder -X quit 2>/dev/null || true
    sleep 1
fi

# ── 9. Start in background screen session ─────────────────────────────────
info "Starting $SCRIPT in background screen session..."
screen -dmS forwarder bash -c "
    set -a; source .env 2>/dev/null; set +a
    export TRACKER_FILE='${TRACKER_FILE}'
    export CHATS_DB_FILE='${CHATS_DB_FILE}'
    export BOTS_DB_FILE='${BOTS_DB_FILE}'
    export SEEN_DB_FILE='${SEEN_DB_FILE}'
    export ROUTING_FILE='${ROUTING_FILE}'
    exec python3 $SCRIPT
"
sleep 2

# ── 10. Verify it started ────────────────────────────────────────────────
if screen -list | grep -q "forwarder"; then
    success "$SCRIPT is running in background!"
    echo ""
    echo -e "${BOLD}Next steps:${RESET}"
    echo "  screen -r forwarder      ← view live logs (Ctrl+A+D to detach)"
    echo "  screen -S forwarder -X quit  ← stop the forwarder"
    echo "  ./setup.sh               ← restart after code changes"
    echo ""
    PORT=${PORT:-8080}
    echo -e "  Dashboard: ${GREEN}http://$(hostname -I | awk '{print $1}'):${PORT}/${RESET}"
else
    die "Forwarder failed to start — check for errors: screen -r forwarder"
fi
