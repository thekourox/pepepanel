#!/bin/bash

# ===================================================================
# PepePanel Unified Gateway - Remote Installer Script
# ===================================================================

# ANSI Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[1;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}================================================================${NC}"
echo -e "${CYAN}             PepePanel Unified Gateway Installer                ${NC}"
echo -e "${CYAN}================================================================${NC}"

# 0. License / Security Check
# ⚠️ REPLACE THIS WITH THE EMPLOYER's SECRET PASSWORD
SECRET_LICENSE="Dz)3:)&”5:?trs&@@&@@64usGFgf"

if [ "$1" == "$SECRET_LICENSE" ]; then
    echo -e "${GREEN}[+] License verified via command-line argument.${NC}"
else
    echo -e "${YELLOW}[*] This installer is protected.${NC}"
    read -s -p "Enter Installation License Key: " INPUT_KEY
    echo ""
    if [ "$INPUT_KEY" != "$SECRET_LICENSE" ]; then
        echo -e "${RED}[!] Invalid License Key. Unauthorized access. Installation aborted.${NC}"
        exit 1
    fi
    echo -e "${GREEN}[+] License verified.${NC}"
fi

# 1. Check Root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}[!] Please run this script as root (use sudo).${NC}"
  exit 1
fi

# 2. Variables
INSTALL_DIR="/opt/pepepanel"
# ⚠️ REPLACE THIS URL WITH YOUR ACTUAL MIRROR SERVER URL
MIRROR_URL="http://31.77.147.87/pepepanel.tar.gz"

echo -e "\n${YELLOW}[*] Installing system dependencies (curl, wget, python3, pip)...${NC}"
apt-get update -y -q > /dev/null 2>&1
apt-get install -y -q curl wget tar python3 python3-pip sqlite3 > /dev/null 2>&1

# 3. Check for existing installation
if [ -d "$INSTALL_DIR" ]; then
    echo -e "${YELLOW}[!] PepePanel is already installed at $INSTALL_DIR.${NC}"
    read -p "Do you want to overwrite the existing installation? [y/N]: " overwrite
    if [[ "$overwrite" != "y" && "$overwrite" != "Y" ]]; then
        echo -e "${RED}[*] Installation aborted by user.${NC}"
        exit 0
    fi
    
    KEEP_DB="n"
    if [ -f "$INSTALL_DIR/auth.db" ]; then
        read -p "Do you want to KEEP existing admin accounts? (auth.db) [Y/n]: " keep_db_input
        if [[ "$keep_db_input" != "n" && "$keep_db_input" != "N" ]]; then
            KEEP_DB="y"
            cp "$INSTALL_DIR/auth.db" "/tmp/auth.db.bak"
            echo -e "${GREEN}[+] Backed up auth.db${NC}"
        fi
    fi
    
    echo -e "${YELLOW}[*] Proceeding to overwrite existing installation...${NC}"
fi

# 4. Download & Extract
echo -e "\n${YELLOW}[*] Downloading PepePanel from mirror server...${NC}"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cd /tmp

# Download the tar.gz and extract
if curl -sLf "$MIRROR_URL" -o pepepanel.tar.gz; then
    tar -xzf pepepanel.tar.gz -C "$INSTALL_DIR"
    rm pepepanel.tar.gz
    
    # Fix Windows CRLF line endings on all extracted scripts
    find "$INSTALL_DIR" -type f \( -name "*.sh" -o -name "*.py" \) -exec sed -i 's/\r$//' {} +
    
    echo -e "${GREEN}[+] Download and extraction successful.${NC}"
else
    echo -e "${RED}[!] Failed to download from the mirror server.${NC}"
    echo -e "${RED}[!] Please ensure the MIRROR_URL variable in this script is correctly set.${NC}"
    exit 1
fi

cd "$INSTALL_DIR"

if [ "$KEEP_DB" == "y" ]; then
    mv "/tmp/auth.db.bak" "$INSTALL_DIR/auth.db"
    echo -e "${GREEN}[+] Restored existing auth.db${NC}"
fi

# 4. Setup Python Environment
echo -e "\n${YELLOW}[*] Setting up Python environment...${NC}"
pip3 install flask requests werkzeug --break-system-packages > /dev/null 2>&1 || pip3 install flask requests werkzeug > /dev/null 2>&1

# 5. Execute Sub-Installers (Tor & Surfshark)
echo -e "${YELLOW}[*] Executing sub-installers for Tor and Surfshark...${NC}"
if [ -f "pasarguard-tor/install_service.sh" ]; then
    chmod +x pasarguard-tor/install_service.sh
    cd pasarguard-tor && ./install_service.sh
    cd ..
fi

if [ -f "pepeshark/install.sh" ]; then
    chmod +x pepeshark/install.sh
    cd pepeshark && ./install.sh
    cd ..
fi

# 6. Auto-Generate Secure Admin Credentials
if [ "$KEEP_DB" != "y" ]; then
    echo -e "${YELLOW}[*] Generating secure admin credentials...${NC}"
    USERNAME="admin_$((RANDOM % 900 + 100))"
    PASSWORD=$(tr -dc 'A-Za-z0-9!@#$%^&*' </dev/urandom | head -c 16)

    python3 -c "
import sqlite3
from werkzeug.security import generate_password_hash
conn = sqlite3.connect('auth.db')
conn.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT)')
hashed = generate_password_hash('$PASSWORD')
conn.execute('INSERT OR REPLACE INTO users (username, password_hash) VALUES (?, ?)', ('$USERNAME', hashed))
conn.commit()
conn.close()
"
else
    echo -e "${GREEN}[+] Existing admin credentials preserved.${NC}"
fi

# 7. Setup Systemd Service for Gateway
echo -e "${YELLOW}[*] Setting up Gateway systemd service...${NC}"
cat > "/etc/systemd/system/pepepanel-gateway.service" <<EOF
[Unit]
Description=PepePanel Unified Gateway
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/gateway.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable pepepanel-gateway.service > /dev/null 2>&1
systemctl restart pepepanel-gateway.service

# 8. Global Command Alias
echo -e "${YELLOW}[*] Creating global 'pepeshark' alias...${NC}"
chmod +x "$INSTALL_DIR/pepepanel_manager.sh"

cat > /usr/local/bin/pepeshark <<EOF
#!/bin/bash
cd $INSTALL_DIR
./pepepanel_manager.sh
EOF
chmod +x /usr/local/bin/pepeshark

SERVER_IP=$(curl -s http://checkip.amazonaws.com)

# 9. Final Output
echo -e "\n${GREEN}================================================================${NC}"
echo -e "${GREEN}                PepePanel Installed Successfully!               ${NC}"
echo -e "${GREEN}================================================================${NC}"
echo -e "🌐 Dashboard Login: ${CYAN}http://$SERVER_IP:5000${NC}"
echo -e ""
echo -e "${YELLOW}--- 🔐 ADMIN CREDENTIALS ---${NC}"
if [ "$KEEP_DB" != "y" ]; then
    echo -e "👤 Username : ${RED}$USERNAME${NC}"
    echo -e "🔑 Password : ${RED}$PASSWORD${NC}"
else
    echo -e "${GREEN}Preserved from previous installation.${NC}"
fi
echo -e "${YELLOW}----------------------------${NC}"
echo -e ""
echo -e "⚙️ To manage the panel in the future, simply type: ${CYAN}pepeshark${NC}"
echo -e "${CYAN}================================================================${NC}\n"
