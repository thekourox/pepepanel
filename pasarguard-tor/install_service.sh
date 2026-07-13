#!/bin/bash

# Ensure script is run as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run this script as root (sudo bash install_service.sh)"
  exit
fi

# Get the absolute path to the directory where this script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SERVICE_PATH="/etc/systemd/system/tor-checker.service"

echo "Installing Tor Checker Systemd Service..."
echo "Project Directory: $DIR"

# Generate the systemd service file dynamically
cat <<EOF > $SERVICE_PATH
[Unit]
Description=Tor Proxy Network Checker
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$DIR
ExecStart=/bin/bash setup_and_run.sh
Restart=always
RestartSec=5
RuntimeMaxSec=6h
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd and enable/start the service
systemctl daemon-reload
systemctl enable tor-checker
systemctl start tor-checker

echo ""
echo "============================================================"
echo "✅ Tor Checker is now running permanently in the background!"
echo "============================================================"
echo ""
echo "Helpful Commands:"
echo "  - Check Status: systemctl status tor-checker"
echo "  - Stop Service: systemctl stop tor-checker"
echo "  - Start Service: systemctl start tor-checker"
echo "  - View Live Logs: journalctl -u tor-checker -f"
echo "============================================================"
