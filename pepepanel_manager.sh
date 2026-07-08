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

fetch_pg_token() {
    echo ""
    echo "====================================="
    echo "     Pasarguard Token Fetcher        "
    echo "====================================="
    read -p "Enter Pasarguard Port (Default 54321): " pg_port
    pg_port=${pg_port:-54321}
    read -p "Enter Pasarguard Username: " pg_user
    read -sp "Enter Pasarguard Password: " pg_pass
    echo ""

    # Python script to fetch the token
    python3 -c "
import urllib.request
import urllib.parse
import json
import ssl

def fetch(proto):
    url = f'{proto}://127.0.0.1:$pg_port/api/admin/token'
    data = urllib.parse.urlencode({'username': '$pg_user', 'password': '$pg_pass'}).encode('utf-8')
    req = urllib.request.Request(url, data=data)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
        if response.status == 200:
            resp_data = json.loads(response.read().decode())
            return resp_data.get('access_token')
    return None

try:
    token = fetch('https')
    if token: print(f'\n\033[1;32m[+] SUCCESS! Your Admin API Token:\033[0m\n\n{token}\n')
except Exception:
    try:
        token = fetch('http')
        if token: print(f'\n\033[1;32m[+] SUCCESS! Your Admin API Token:\033[0m\n\n{token}\n')
        else: print('\n[!] Logged in, but token not found in response.')
    except urllib.error.HTTPError as e:
        print(f'\n\033[1;31m[!] Login failed. Check your username/password. (HTTP {e.code})\033[0m')
    except Exception as e:
        print(f'\n\033[1;31m[!] Connection error: {e}. Is Pasarguard running on port $pg_port?\033[0m')
"
}

uninstall_services() {
    echo ""
    echo "====================================="
    echo "       UNINSTALLING PEPEPANEL        "
    echo "====================================="
    read -p "Are you sure you want to completely remove PepePanel and all its services? (y/n): " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "Uninstallation cancelled."
        return
    fi

    echo "Stopping and disabling services..."
    systemctl stop pepepanel-gateway.service tor-checker.service pepeshark.service 2>/dev/null
    systemctl disable pepepanel-gateway.service tor-checker.service pepeshark.service 2>/dev/null

    echo "Removing systemd service files..."
    rm -f /etc/systemd/system/pepepanel-gateway.service
    rm -f /etc/systemd/system/tor-checker.service
    rm -f /etc/systemd/system/pepeshark.service
    systemctl daemon-reload

    echo "Removing global 'pepeshark' alias..."
    rm -f /usr/local/bin/pepeshark

    read -p "Do you also want to DELETE all application files in $APP_DIR? (y/n): " confirm_files
    if [[ "$confirm_files" == "y" || "$confirm_files" == "Y" ]]; then
        echo "Deleting application files..."
        rm -rf "$APP_DIR"
        echo "====================================="
        echo "PepePanel has been completely uninstalled."
        echo "====================================="
        exit 0
    else
        echo "====================================="
        echo "Services uninstalled. Files were kept in $APP_DIR."
        echo "====================================="
    fi
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
    echo "5. Uninstall PepePanel"
    echo "6. Fetch Pasarguard Token"
    echo "7. Exit"
    echo "====================================="
    read -p "Select an option [1-7]: " option

    case $option in
        1) install_services ;;
        2) manage_admins ;;
        3) view_logs ;;
        4) systemctl restart pepepanel-gateway.service; echo "Gateway Restarted." ;;
        5) uninstall_services ;;
        6) fetch_pg_token ;;
        7) echo "Exiting..."; exit 0 ;;
        *) echo "Invalid option." ;;
    esac
done
