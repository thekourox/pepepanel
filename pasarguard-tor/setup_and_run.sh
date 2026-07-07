#!/bin/bash

echo "=============================================================="
echo "   Tor VPN Backend Web Panel (Linux)"
echo "=============================================================="
echo ""

if [ ! -f "tor/tor" ] && [ ! -f "Tor/tor" ]; then
    echo "Step 0: Downloading and extracting Tor Expert Bundle..."
    curl -sSL "https://dist.torproject.org/torbrowser/15.0.17/tor-expert-bundle-linux-x86_64-15.0.17.tar.gz" -o tor.tar.gz
    tar -xzf tor.tar.gz
    rm tor.tar.gz
    echo "[OK] Tor downloaded and extracted."
    echo ""
fi

# Check for python3
if ! command -v python3 >/dev/null 2>&1; then
    echo "[Error] python3 is not installed! Please install python3."
    exit 1
fi

# Check for pip3
if ! command -v pip3 >/dev/null 2>&1; then
    echo "[!] pip3 is not installed. Attempting to install python3-pip..."
    if [ "$EUID" -ne 0 ]; then
        sudo apt-get update && sudo apt-get install -y python3-pip
    else
        apt-get update && apt-get install -y python3-pip
    fi
    if ! command -v pip3 >/dev/null 2>&1; then
        echo "[Error] Failed to install pip3. Please manually run: apt-get install python3-pip"
        exit 1
    fi
fi

echo "Step 1: Installing Python requirements..."
pip3 install -r requirements.txt --break-system-packages --ignore-installed || { echo "[Error] Failed to install requirements!"; exit 1; }
echo "[OK] Requirements installed successfully."
echo ""

if [ -f "./Tor/tor" ]; then
    chmod +x ./Tor/tor
    TOR_BIN="./Tor/tor"
fi
if [ -f "./tor/tor" ]; then
    chmod +x ./tor/tor
    TOR_BIN="./tor/tor"
fi

if [ -n "$TOR_BIN" ]; then
    if ! $TOR_BIN --version > /dev/null 2>&1; then
        echo "[!] Missing Tor system dependencies (e.g. libevent). Installing..."
        if [ "$EUID" -ne 0 ]; then
            sudo apt-get update && sudo apt-get install -y libevent-dev
        else
            apt-get update && apt-get install -y libevent-dev
        fi
        if ! $TOR_BIN --version > /dev/null 2>&1; then
            echo "[Error] Still missing dependencies. Please manually run: apt-get install libevent-dev"
            exit 1
        fi
    fi
fi

echo "Step 2: Cleaning up any hanging background processes..."
systemctl stop tor > /dev/null 2>&1
pkill -9 -f "python api.py" > /dev/null 2>&1
pkill -9 -f "python3 api.py" > /dev/null 2>&1
pkill -9 -f "python tor_manager.py" > /dev/null 2>&1
pkill -9 -f "python3 tor_manager.py" > /dev/null 2>&1
pkill -9 -x "tor" > /dev/null 2>&1
fuser -k 54322/tcp > /dev/null 2>&1

echo "Cleaning up old cache and state files (preventing OS conflicts)..."
rm -rf ./tor_data > /dev/null 2>&1
rm -f tor_fingerprints_cache.json > /dev/null 2>&1
sleep 3
echo ""

echo "Step 3: Launching Web Panel (FastAPI)..."
echo "The panel will be available at http://<YOUR_SERVER_IP>:54322"
ulimit -n 65535 > /dev/null 2>&1
exec python3 api.py
