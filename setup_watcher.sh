#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# setup_watcher.sh — One-time setup for SOP drop-folder automation
# Run once on the server:
#   bash /opt/SOP_Chatbot/setup_watcher.sh
# ─────────────────────────────────────────────────────────────────

set -e

BASE="/opt/SOP_Chatbot"
INBOX="$BASE/sop_inbox"
PYTHON="/usr/bin/python3"
CRON_JOB="*/2 * * * * $PYTHON $BASE/sop_watcher.py >> $INBOX/watcher.log 2>&1"
SERVICE_USER="${SUDO_USER:-$USER}"

echo "=== SOP Watcher Setup ==="

# ── 1. Create inbox folder structure ─────────────────────────────
echo "[1/4] Creating inbox folders..."
mkdir -p "$INBOX/everhealth"
mkdir -p "$INBOX/other_vertical"
mkdir -p "$INBOX/processed"
mkdir -p "$INBOX/failed"
touch "$INBOX/watcher.log"

# Set permissions so the service user can write (SCP target)
chmod -R 755 "$INBOX"
echo "    Folders created at $INBOX"

# ── 2. Allow docker-compose-v2 restart without password prompt ───
# The watcher calls: sudo docker-compose-v2 restart sop-chatbot
# We add a targeted sudoers rule so no password is needed from cron.
echo "[2/4] Configuring passwordless sudo for docker-compose-v2 restart..."
SUDOERS_LINE="$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/local/bin/docker-compose-v2 restart sop-chatbot"
SUDOERS_FILE="/etc/sudoers.d/sop_watcher"

if sudo grep -qF "sop_watcher" /etc/sudoers.d/sop_watcher 2>/dev/null; then
    echo "    Sudoers rule already exists — skipping"
else
    echo "$SUDOERS_LINE" | sudo tee "$SUDOERS_FILE" > /dev/null
    sudo chmod 440 "$SUDOERS_FILE"
    echo "    Sudoers rule added: $SUDOERS_FILE"
fi

# ── 3. Install cron job ──────────────────────────────────────────
echo "[3/4] Installing cron job (every 2 minutes)..."
# Remove any existing watcher cron entry then add fresh
( crontab -l 2>/dev/null | grep -v "sop_watcher.py" ; echo "$CRON_JOB" ) | crontab -
echo "    Cron job installed:"
echo "    $CRON_JOB"

# ── 4. Smoke test — run watcher once manually ────────────────────
echo "[4/4] Running watcher once to verify setup..."
$PYTHON "$BASE/sop_watcher.py"
echo ""
echo "=== Setup complete ==="
echo ""
echo "HOW TO ADD A NEW SOP (example, from a local machine):"
echo ""
echo "  Everhealth:"
echo '  scp -i "path/to/your-key.pem" "MyPractice.xlsx" your-user@your-server-ip:/opt/SOP_Chatbot/sop_inbox/everhealth/'
echo ""
echo "  Other Vertical (KMB/DIM):"
echo '  scp -i "path/to/your-key.pem" "KMB_SOP.docx" your-user@your-server-ip:/opt/SOP_Chatbot/sop_inbox/other_vertical/'
echo ""
echo "  The watcher runs every 2 minutes. Check progress:"
echo "  tail -f $INBOX/watcher.log"
echo ""
echo "  Processed files land in:  $INBOX/processed/"
echo "  Failed files land in:     $INBOX/failed/"
