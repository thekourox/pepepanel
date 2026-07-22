import os
import stat
import urllib.request
import subprocess
import tarfile
import shutil
import glob
import logging
import time
import socket
import signal
import json

logger = logging.getLogger("WireproxyManager")
logger.setLevel(logging.INFO)

BIN_DIR = os.path.join(os.path.dirname(__file__), "bin")
CONF_DIR = os.path.join(os.path.dirname(__file__), "wireproxy_configs")
WIREPROXY_BIN = os.path.join(BIN_DIR, "wireproxy")
PID_FILE = os.path.join(os.path.dirname(__file__), "wireproxy_pids.json")

os.makedirs(BIN_DIR, exist_ok=True)
os.makedirs(CONF_DIR, exist_ok=True)

# ============ PID Tracking ============
def _load_pids() -> dict:
    """Load {tag: pid} mapping from disk."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_pids(pids: dict):
    """Save {tag: pid} mapping to disk."""
    try:
        with open(PID_FILE, "w") as f:
            json.dump(pids, f, indent=2)
    except Exception:
        pass

def _is_pid_alive(pid: int) -> bool:
    """Check if a process with given PID is still running."""
    try:
        os.kill(pid, 0)  # Signal 0 = just check, don't actually kill
        return True
    except (OSError, ProcessLookupError):
        return False

def _kill_pid(pid: int):
    """Force kill a process immediately."""
    try:
        os.kill(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass

def _kill_all_wireproxy():
    """Aggressively kill ALL wireproxy processes by scanning /proc and sending SIGKILL immediately."""
    killed = 0
    pids_to_kill = []
    try:
        for pid_dir in os.listdir('/proc'):
            if not pid_dir.isdigit():
                continue
            try:
                cmdline_path = f'/proc/{pid_dir}/cmdline'
                with open(cmdline_path, 'r') as f:
                    cmdline = f.read()
                if 'wireproxy' in cmdline and '-c' in cmdline:
                    pids_to_kill.append(int(pid_dir))
            except (IOError, OSError, PermissionError):
                continue
    except Exception as e:
        logger.error(f"Error scanning /proc: {e}")
    
    for pid in pids_to_kill:
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
        except:
            pass
            
    if killed > 0:
        logger.info(f"Killed {killed} wireproxy processes via /proc scan")
        time.sleep(1) # Give OS time to release sockets
    
    # Ultimate fallback using shell
    try:
        subprocess.run("killall -9 wireproxy 2>/dev/null", shell=True, check=False)
    except Exception:
        pass
    
    return killed

# ============ Core Functions ============

def ensure_wireproxy():
    """Downloads wireproxy if it does not exist."""
    log_file = os.path.join(os.path.dirname(__file__), "wireproxy_out.log")
    
    if os.path.exists(WIREPROXY_BIN):
        return

    logger.info("Wireproxy binary not found. Downloading...")
    with open(log_file, "a") as lf:
        lf.write("\\n--- Downloading Wireproxy from GitHub ---\\n")
        
    url_direct = "https://github.com/octeep/wireproxy/releases/download/v1.0.8/wireproxy_linux_amd64.tar.gz"
    url_proxy = "https://ghproxy.com/https://github.com/octeep/wireproxy/releases/download/v1.0.8/wireproxy_linux_amd64.tar.gz"
    tar_path = os.path.join(BIN_DIR, "wireproxy.tar.gz")
    
    try:
        try:
            urllib.request.urlretrieve(url_direct, tar_path)
        except Exception as e:
            with open(log_file, "a") as lf:
                lf.write(f"Direct download failed: {e}. Trying proxy...\\n")
            urllib.request.urlretrieve(url_proxy, tar_path)
            
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(path=BIN_DIR)
            
        os.remove(tar_path)
        
        # Ensure it's executable
        st = os.stat(WIREPROXY_BIN)
        os.chmod(WIREPROXY_BIN, st.st_mode | stat.S_IEXEC)
        logger.info("Wireproxy downloaded and ready.")
        with open(log_file, "a") as lf:
            lf.write("Wireproxy successfully downloaded and extracted.\\n")
            
    except Exception as e:
        with open(log_file, "a") as lf:
            lf.write(f"CRITICAL ERROR downloading wireproxy: {e}\\n")
        raise RuntimeError(f"Failed to download wireproxy: {e}")

def start_wireproxy(tag: str, private_key: str, wg_address: str, endpoint: str, public_key: str, local_port: int):
    """Generates wireproxy config and starts the daemon."""
    ensure_wireproxy()
    
    conf_path = os.path.join(CONF_DIR, f"{tag}.conf")
    
    # Wireproxy config structure
    # NOTE: ListenPort is intentionally OMITTED so the OS assigns a random
    # ephemeral UDP port (49152-65535) for each instance.  This prevents
    # Linux conntrack from confusing response packets between 142 sequential
    # UDP ports, which was the root cause of "Received packet with invalid mac1".
    config_content = f"""[Interface]
Address = {wg_address}
PrivateKey = {private_key}
MTU = 1280

[Peer]
PublicKey = {public_key}
Endpoint = {endpoint}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25

[Socks5]
BindAddress = 0.0.0.0:{local_port}
"""
    with open(conf_path, "w", encoding="utf-8") as f:
        f.write(config_content)
        
    # Start the process
    logger.info(f"Starting wireproxy for {tag} on SOCKS5 127.0.0.1:{local_port}")
    log_file = os.path.join(os.path.dirname(__file__), "wireproxy_out.log")
    lf = open(log_file, "a")
    lf.write(f"\n--- Starting wireproxy for {tag} at 127.0.0.1:{local_port} ---\n")
    lf.flush()
    proc = subprocess.Popen(
        [WIREPROXY_BIN, "-c", conf_path],
        stdout=lf,
        stderr=lf,
        start_new_session=True
    )
    
    # Track PID
    pids = _load_pids()
    pids[tag] = proc.pid
    _save_pids(pids)
    
    # Staggered start: prevent Hetzner DDoS detection on bulk UDP handshakes
    time.sleep(0.5)

def stop_surfshark_proxies(tags: list[str]):
    """Finds and kills specific wireproxy instances by PID and deletes their configs."""
    logger.info(f"Stopping wireproxies for tags: {tags}")
    pids = _load_pids()
    
    for tag in tags:
        # Kill by tracked PID first
        pid = pids.get(tag)
        if pid and _is_pid_alive(pid):
            logger.info(f"Killing wireproxy {tag} (PID {pid})")
            _kill_pid(pid)
            pids.pop(tag, None)
        
        # Delete configuration files
        conf_path = os.path.join(CONF_DIR, f"{tag}.conf")
        if os.path.exists(conf_path):
            try:
                os.remove(conf_path)
            except OSError:
                pass
    
    _save_pids(pids)

def recover_all_proxies():
    """Kills any dangling wireproxy processes and restarts all configs."""
    logger.info("Recovering all Wireproxy instances...")
    
    # Kill ALL wireproxy processes (pure Python, no pkill needed)
    _kill_all_wireproxy()
    
    time.sleep(2)  # Wait for ports to be released
        
    configs = glob.glob(os.path.join(CONF_DIR, "*.conf"))
    
    if not configs:
        logger.info("No wireproxy configs found. Nothing to recover.")
        return
    
    logger.info(f"Found {len(configs)} wireproxy configs to recover.")
    log_file = os.path.join(os.path.dirname(__file__), "wireproxy_out.log")
    lf = open(log_file, "a")
    
    new_pids = {}
    
    import re
    for conf_path in configs:
        tag = os.path.splitext(os.path.basename(conf_path))[0]
        
        # --- Strip ListenPort from old configs to let OS assign random ephemeral ports ---
        # This fixes "Received packet with invalid mac1" caused by conntrack confusion
        try:
            with open(conf_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if 'ListenPort' in content:
                content = re.sub(r'\nListenPort\s*=\s*\d+', '', content)
                with open(conf_path, 'w', encoding='utf-8') as f:
                    f.write(content)
        except Exception as e:
            logger.error(f"Failed to patch {conf_path}: {e}")
        # -------------------------------------------------------------------------
        
        logger.info(f"Restarting wireproxy for {tag}")
        lf.write(f"\n--- Restarting wireproxy from {conf_path} ---\n")
        lf.flush()
        proc = subprocess.Popen(
            [WIREPROXY_BIN, "-c", conf_path],
            stdout=lf,
            stderr=lf,
            start_new_session=True
        )
        new_pids[tag] = proc.pid
        time.sleep(1.5)  # Slower staggered start to prevent Surfshark/Hetzner ratelimits
    
    _save_pids(new_pids)
    logger.info(f"Recovery complete. {len(new_pids)} wireproxy instances started.")

def restart_single_proxy(tag: str):
    """Restarts a single wireproxy instance by tag without touching others."""
    conf_path = os.path.join(CONF_DIR, f"{tag}.conf")
    if not os.path.exists(conf_path):
        logger.warning(f"Config for {tag} not found, cannot restart.")
        return False
    
    # Kill only this specific process by PID
    pids = _load_pids()
    pid = pids.get(tag)
    if pid and _is_pid_alive(pid):
        _kill_pid(pid)
    
    time.sleep(1)  # Wait for port release
    
    log_file = os.path.join(os.path.dirname(__file__), "wireproxy_out.log")
    lf = open(log_file, "a")
    lf.write(f"\n--- Watchdog restarting wireproxy for {tag} ---\n")
    lf.flush()
    proc = subprocess.Popen(
        [WIREPROXY_BIN, "-c", conf_path],
        stdout=lf,
        stderr=lf,
        start_new_session=True
    )
    
    # Update PID
    pids[tag] = proc.pid
    _save_pids(pids)
    
    logger.info(f"Watchdog restarted {tag} (PID {proc.pid})")
    return True

def swap_proxy_endpoint(tag: str, new_endpoint: str, new_public_key: str) -> bool:
    """Kills a proxy, rewrites its config with a NEW server endpoint, and restarts it.
    This is used by the watchdog when a Surfshark server is permanently unreachable."""
    import re
    conf_path = os.path.join(CONF_DIR, f"{tag}.conf")
    if not os.path.exists(conf_path):
        logger.warning(f"Config for {tag} not found, cannot swap endpoint.")
        return False
    
    # Kill existing process
    pids = _load_pids()
    pid = pids.get(tag)
    if pid and _is_pid_alive(pid):
        _kill_pid(pid)
    
    time.sleep(0.5)
    
    # Rewrite config with new endpoint and public key
    try:
        with open(conf_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        content = re.sub(r'Endpoint\s*=\s*\S+', f'Endpoint = {new_endpoint}', content)
        content = re.sub(r'PublicKey\s*=\s*\S+', f'PublicKey = {new_public_key}', content)
        
        with open(conf_path, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        logger.error(f"Failed to rewrite config for {tag}: {e}")
        return False
    
    # Restart with new config
    log_file = os.path.join(os.path.dirname(__file__), "wireproxy_out.log")
    lf = open(log_file, "a")
    lf.write(f"\n--- Watchdog SWAPPING endpoint for {tag} to {new_endpoint} ---\n")
    lf.flush()
    proc = subprocess.Popen(
        [WIREPROXY_BIN, "-c", conf_path],
        stdout=lf,
        stderr=lf,
        start_new_session=True
    )
    
    pids[tag] = proc.pid
    _save_pids(pids)
    
    logger.info(f"Watchdog swapped {tag} to {new_endpoint} (PID {proc.pid})")
    return True

def health_check_proxy(socks_port: int, timeout: float = 5.0) -> bool:
    """Check if a SOCKS5 proxy on the given port is alive by attempting a TCP connect."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(('127.0.0.1', socks_port))
        # Send SOCKS5 greeting: version 5, 1 auth method (no auth)
        s.sendall(b'\x05\x01\x00')
        resp = s.recv(2)
        s.close()
        # Valid SOCKS5 response: version 5, method 0 (no auth)
        return len(resp) == 2 and resp[0] == 0x05
    except Exception:
        return False

def get_active_proxies() -> dict:
    """Returns a dict of {tag: socks_port} from existing config files."""
    result = {}
    configs = glob.glob(os.path.join(CONF_DIR, "*.conf"))
    for conf_path in configs:
        tag = os.path.splitext(os.path.basename(conf_path))[0]
        try:
            with open(conf_path, "r") as f:
                for line in f:
                    if line.strip().startswith("BindAddress"):
                        port_str = line.strip().split(":")[-1]
                        result[tag] = int(port_str)
                        break
        except Exception:
            pass
    return result
