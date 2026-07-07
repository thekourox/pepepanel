@echo off
echo ==============================================================
echo   Tor VPN Backend Web Panel (Windows)
echo ==============================================================
echo.

if not exist tor\tor.exe if not exist Tor\tor.exe (
    echo Step 0: Downloading and extracting Tor Expert Bundle...
    curl -sSL "https://dist.torproject.org/torbrowser/15.0.17/tor-expert-bundle-windows-x86_64-15.0.17.tar.gz" -o tor.tar.gz
    tar -xzf tor.tar.gz
    del tor.tar.gz
    echo [OK] Tor downloaded and extracted.
    echo.
)

echo Step 1: Installing Python requirements...
pip install -r requirements.txt >nul 2>&1
echo [OK] Requirements installed.
echo.

echo Step 2: Cleaning up any hanging Tor processes...
taskkill /F /IM tor.exe >nul 2>&1
echo.

echo Step 3: Launching Web Panel (FastAPI)...
echo The panel will be available at http://127.0.0.1:54322
python api.py

pause
