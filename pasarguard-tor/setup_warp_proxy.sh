#!/bin/bash
echo "==================================================="
echo "Setting up Cloudflare WARP Proxy for Tor..."
echo "==================================================="

# Download wgcf (WARP Config Generator)
wget -O wgcf https://github.com/ViRb3/wgcf/releases/download/v2.2.22/wgcf_2.2.22_linux_amd64
chmod +x wgcf

# Register free account and generate config
./wgcf register --accept-tos
./wgcf generate

# Modify config to add a SOCKS5 proxy on port 40000
mv wgcf-profile.conf warp_tor.conf
echo "" >> warp_tor.conf
echo "[Socks5]" >> warp_tor.conf
echo "BindAddress = 127.0.0.1:40000" >> warp_tor.conf

# Create a systemd service to keep it running
cat <<EOF > /etc/systemd/system/warp-proxy.service
[Unit]
Description=Cloudflare WARP Proxy for Tor Bypass
After=network.target

[Service]
ExecStart=/usr/local/bin/wireproxy -c $(pwd)/warp_tor.conf
Restart=always
User=root
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

# Start the service
systemctl daemon-reload
systemctl enable warp-proxy
systemctl restart warp-proxy

echo "==================================================="
echo "Done! Cloudflare WARP is now running securely on Port 40000."
echo "Your DNS is completely safe and Xray is untouched."
echo "==================================================="
