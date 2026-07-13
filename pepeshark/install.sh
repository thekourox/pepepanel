#!/bin/bash
# ==============================================================================
# Pasargard VPN Automator (Pepeshark) - Installation & Execution Script
# ==============================================================================

set -e

# Ensure script is run as root or with sudo privileges
if [ "$EUID" -ne 0 ]; then
  echo -e "\033[1;31mPlease run this script with sudo or as root.\033[0m"
  exit 1
fi

APP_DIR=$(pwd)
VENV_DIR="$APP_DIR/venv"
PORT=8088

echo -e "\n\033[1;34m[1/4] Updating system and installing dependencies...\033[0m"
apt-get update -y
apt-get install -y python3 python3-pip python3-venv curl

echo -e "\n\033[1;34m[2/4] Setting up Python Virtual Environment...\033[0m"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "Virtual environment created at $VENV_DIR"
else
    echo "Virtual environment already exists."
fi

# Activate virtual environment and install packages
echo -e "\n\033[1;34m[3/4] Installing Python packages...\033[0m"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install fastapi uvicorn pydantic jinja2 httpx
deactivate

echo -e "\n\033[1;34m[4/4] Creating Systemd Service for Persistence...\033[0m"
SERVICE_FILE="/etc/systemd/system/pepeshark.service"

cat <<EOF > $SERVICE_FILE
[Unit]
Description=Pepeshark - PasarGuard VPN Automator
After=network.target

[Service]
User=root
WorkingDirectory=$APP_DIR
Environment="PATH=$VENV_DIR/bin"
ExecStart=$VENV_DIR/bin/python3 app.py
Restart=always
RestartSec=5
LimitNOFILE=1048576
LimitNPROC=1048576

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd and enable/start service
systemctl daemon-reload
systemctl enable pepeshark
systemctl restart pepeshark

echo -e "\n\033[1;32m====================================================================\033[0m"
echo -e "\033[1;32mSuccess! Pepeshark has been installed as a background service.\033[0m"
echo -e "Web Interface available at: http://<your-server-ip>:$PORT"
echo -e "To view live logs, run: \033[1;33mjournalctl -u pepeshark -f\033[0m"
echo -e "To stop the app, run: \033[1;33msystemctl stop pepeshark\033[0m"
echo -e "To start the app, run: \033[1;33msystemctl start pepeshark\033[0m"
echo -e "\033[1;32m====================================================================\033[0m"
