#!/bin/bash

echo "=============================================================="
echo "   Updating Tor GeoIP Databases (Linux)"
echo "=============================================================="
echo ""

mkdir -p data
rm -rf temp_tor
mkdir -p temp_tor

echo "Step 1: Downloading latest Tor Expert Bundle to extract GeoIP..."
wget -qO temp_tor.tar.gz https://www.torproject.org/dist/torbrowser/13.5.3/tor-expert-bundle-linux-x86_64-13.5.3.tar.gz

echo "Step 2: Extracting GeoIP databases..."
tar -xzf temp_tor.tar.gz -C temp_tor
cp temp_tor/data/tor/geoip data/geoip
cp temp_tor/data/tor/geoip6 data/geoip6

echo "Step 3: Cleaning up..."
rm temp_tor.tar.gz
rm -rf temp_tor

echo ""
echo "=============================================================="
echo "Update complete! GeoIP databases are now the latest version."
echo "=============================================================="
