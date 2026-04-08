#!/bin/bash
# ============================================
#  YouTube View Bot - VPS One-Click Deploy
#  Tested on: Ubuntu 22.04+ (Oracle Cloud Free Tier)
# ============================================
set -e

echo "=========================================="
echo "  YouTube View Bot - VPS Deploy"
echo "=========================================="

# 1. Install Docker if not present
if ! command -v docker &> /dev/null; then
    echo "[1/4] Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo "  Docker installed. You may need to re-login for group changes."
else
    echo "[1/4] Docker already installed"
fi

# 2. Install Docker Compose plugin if not present
if ! docker compose version &> /dev/null 2>&1; then
    echo "[2/4] Installing Docker Compose..."
    sudo apt-get update && sudo apt-get install -y docker-compose-plugin
else
    echo "[2/4] Docker Compose already installed"
fi

# 3. Clone repo if not already cloned
REPO_DIR="$HOME/yt-view-web"
if [ ! -d "$REPO_DIR" ]; then
    echo "[3/4] Cloning repository..."
    git clone https://github.com/BudongJW/yt-view-web.git "$REPO_DIR"
else
    echo "[3/4] Updating repository..."
    cd "$REPO_DIR" && git pull
fi

# 4. Build and start
echo "[4/4] Building and starting..."
cd "$REPO_DIR"
docker compose up -d --build

# Get server IP
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')

echo ""
echo "=========================================="
echo "  Deploy complete!"
echo "  Dashboard: http://${SERVER_IP}:5000"
echo ""
echo "  Useful commands:"
echo "    docker compose logs -f     # View logs"
echo "    docker compose restart     # Restart"
echo "    docker compose down        # Stop"
echo "=========================================="
