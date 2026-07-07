@echo off
echo ==============================================================
echo   Updating Tor GeoIP Databases (Windows)
echo ==============================================================
echo.

if not exist data mkdir data
if exist temp_tor rmdir /s /q temp_tor
mkdir temp_tor

echo Step 1: Downloading latest Tor Expert Bundle to extract GeoIP...
powershell -Command "Invoke-WebRequest -Uri 'https://dist.torproject.org/torbrowser/15.0.17/tor-expert-bundle-linux-x86_64-15.0.17.tar.gz'"

echo Step 2: Extracting GeoIP databases...
tar -xzf temp_tor.tar.gz -C temp_tor
copy /y "temp_tor\data\tor\geoip" "data\geoip" >nul
copy /y "temp_tor\data\tor\geoip6" "data\geoip6" >nul

echo Step 3: Cleaning up...
del temp_tor.tar.gz
rmdir /s /q temp_tor

echo.
echo ==============================================================
echo Update complete! GeoIP databases are now the latest version.
echo ==============================================================
pause
