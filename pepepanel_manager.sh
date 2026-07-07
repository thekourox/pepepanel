#!/bin/bash

# Ensure script is run as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (use sudo)"
  exit
fi

APP_DIR="$(pwd)"
GATEWAY_SERVICE_FILE="/etc/systemd/system/pepepanel-gateway.service"

install_services() {
    echo "====================================="
    echo " Installing Pasarguard-Tor..."
    echo "====================================="
    if [ -f "$APP_DIR/pasarguard-tor/install_service.sh" ]; then
        chmod +x "$APP_DIR/pasarguard-tor/install_service.sh"
        cd "$APP_DIR/pasarguard-tor" && ./install_service.sh
        cd "$APP_DIR"
    else
        echo "Error: pasarguard-tor/install_service.sh not found."
    fi

    echo "====================================="
    echo " Installing Pepeshark..."
    echo "====================================="
    if [ -f "$APP_DIR/pepeshark/install.sh" ]; then
        chmod +x "$APP_DIR/pepeshark/install.sh"
        cd "$APP_DIR/pepeshark" && ./install.sh
        cd "$APP_DIR"
    else
        echo "Error: pepeshark/install.sh not found."
    fi

    echo "====================================="
    echo " Installing Unified Gateway Service..."
    echo "====================================="
    
    # Check if python3 is available
    if ! command -v python3 &> /dev/null; then
        echo "Installing Python3 and required packages..."
        apt-get update && apt-get install -y python3 python3-pip
    fi

    # Install Python requirements for Gateway
    pip3 install flask requests werkzeug --break-system-packages --ignore-installed

    # Create systemd service for gateway
    cat > "$GATEWAY_SERVICE_FILE" <<EOF
[Unit]
Description=PepePanel Unified Gateway
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/python3 $APP_DIR/gateway.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable pepepanel-gateway.service
    systemctl start pepepanel-gateway.service

    echo "====================================="
    echo "Unified Gateway has been installed!"
    echo "Gateway is running on Port 5000."
    echo "====================================="
}

manage_admins() {
    echo "====================================="
    if [ -f "$APP_DIR/pepeshark-cli.py" ]; then
        python3 "$APP_DIR/pepeshark-cli.py"
    else
        echo "Error: pepeshark-cli.py not found."
    fi
}

view_logs() {
    echo "====================================="
    echo "Viewing Gateway Logs (Press Ctrl+C to exit)..."
    journalctl -u pepepanel-gateway.service -f
}

while true; do
    echo ""
    echo "====================================="
    echo "     PepePanel Unified Manager       "
    echo "====================================="
    echo "1. Install All Services (Tor, Surfshark, Gateway)"
    echo "2. Manage Admins (Add/View users)"
    echo "3. View System Logs (Gateway)"
    echo "4. Restart Gateway Service"
    echo "5. Exit"
    echo "====================================="
    read -p "Select an option [1-5]: " option

    case $option in
        1) install_services ;;
        2) manage_admins ;;
        3) view_logs ;;
        4) systemctl restart pepepanel-gateway.service; echo "Gateway Restarted." ;;
        5) echo "Exiting..."; exit 0 ;;
        *) echo "Invalid option." ;;
    esac
done
