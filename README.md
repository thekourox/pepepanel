# PepePanel Unified Gateway

PepePanel Unified Gateway is a centralized authentication portal and reverse-proxy that brings together two independent microservices (**Pasarguard-Tor** and **Pepeshark**) under a single, secure interface.

Instead of manually navigating to different ports (e.g., `54322` and `8088`), this gateway exposes a unified dashboard on **Port 5000**. It handles user authentication via a local SQLite database and intelligently proxies HTTP traffic (including API calls and static assets) to the respective backend panels without requiring any backend modifications to the core applications.

## 🚀 Features

- **Centralized Authentication:** Protect both sub-panels behind a single, secure login page.
- **Smart Reverse Proxy:** Seamlessly routes `/tor/` and `/surfshark/` traffic to their respective backend ports.
- **Referer-based Asset Routing:** Automatically routes absolute paths (like `/static/...` or `/api/...`) to the correct microservice based on the browser's `Referer` header.
- **Unified Navigation:** Injected top-navigation bar in both panels for easy switching.
- **Cross-Platform Runners:** Includes `start_all.py` for Windows/macOS and a full `systemd` manager script for Linux servers.

---

## 📁 Directory Structure

```text
pepepanel/
├── pasarguard-tor/          # Core logic for the Tor proxy interface (Port 54322)
├── pepeshark/               # Core logic for the Surfshark interface (Port 8088)
├── gateway.py               # The main Flask Reverse-Proxy & Auth Gateway
├── pepeshark-cli.py         # CLI tool for generating and managing admin accounts
├── start_all.py             # Windows/Dev runner script to launch all 3 services
├── pepepanel_manager.sh     # Linux deployment & manager script (systemd)
└── auth.db                  # SQLite database (auto-generated) storing admin credentials
```

---

## 🐧 Linux Deployment (Production)

If you are deploying this on a Linux server (Ubuntu/Debian), a comprehensive bash manager is provided.

### 1. Make the script executable
```bash
chmod +x pepepanel_manager.sh
```

### 2. Run the Interactive Manager
You must run the manager as root:
```bash
sudo ./pepepanel_manager.sh
```

### 3. Manager Options
When the menu appears, you have the following options:
- **`1` (Install All Services):** This will execute the sub-installers for Tor and Surfshark, install Python dependencies, create a `systemd` service (`pepepanel-gateway.service`), and start the gateway in the background on Port 5000.
- **`2` (Manage Admins):** Opens the CLI to create a new admin username and password. You can choose to auto-generate a highly secure password or set your own.
- **`3` (View System Logs):** Tails the live `journalctl` logs of the unified gateway so you can monitor traffic and errors in real-time.
- **`4` (Restart Gateway):** Restarts the `systemd` service if you've made changes.

---

## 🪟 Windows / Desktop Usage (Development)

If you are running this locally on a Windows machine for development or local usage:

### 1. Install Requirements
Make sure you have Python 3 installed. Then install the gateway dependencies:
```cmd
pip install flask requests werkzeug
```

### 2. Create an Admin User
Run the CLI tool to generate your login credentials:
```cmd
python pepeshark-cli.py
```
Select option `1` to add an admin, and follow the prompts.

### 3. Start the Services
Run the unified python launcher. This script spawns the Gateway, Tor Panel, and Surfshark Panel simultaneously in the background of your terminal.
```cmd
python start_all.py
```

### 4. Access the Dashboard
Open your web browser and navigate to:
**http://127.0.0.1:5000**

To stop the services, simply press `Ctrl+C` in the terminal running `start_all.py`.

---

## ⚠️ Notes on Architecture
- **Zero-Touch Backend:** The backend logic of `pasarguard-tor` and `pepeshark` remains entirely black-boxed. The gateway only interfaces with them via HTTP proxying.
- **HTML Injection:** The only change made to the sub-projects is a tiny HTML snippet injected after the `<body>` tag in their respective `index.html` files. This snippet provides the "Unified Gateway" top navigation bar.
