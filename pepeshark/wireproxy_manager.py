import os
import stat
import urllib.request
import subprocess
import tarfile
import shutil
import glob
import logging

logger = logging.getLogger("WireproxyManager")
logger.setLevel(logging.INFO)

BIN_DIR = os.path.join(os.path.dirname(__file__), "bin")
CONF_DIR = os.path.join(os.path.dirname(__file__), "wireproxy_configs")
WIREPROXY_BIN = os.path.join(BIN_DIR, "wireproxy")

os.makedirs(BIN_DIR, exist_ok=True)
os.makedirs(CONF_DIR, exist_ok=True)

def ensure_wireproxy():
    """Downloads wireproxy if it does not exist."""
    if os.path.exists(WIREPROXY_BIN):
        return

    logger.info("Wireproxy binary not found. Downloading...")
    url = "https://github.com/octeep/wireproxy/releases/download/v1.0.8/wireproxy_linux_amd64.tar.gz"
    tar_path = os.path.join(BIN_DIR, "wireproxy.tar.gz")
    
    urllib.request.urlretrieve(url, tar_path)
    
    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(path=BIN_DIR)
        
    os.remove(tar_path)
    
    # Ensure it's executable
    st = os.stat(WIREPROXY_BIN)
    os.chmod(WIREPROXY_BIN, st.st_mode | stat.S_IEXEC)
    logger.info("Wireproxy downloaded and ready.")

def start_wireproxy(tag: str, private_key: str, wg_address: str, endpoint: str, public_key: str, local_port: int):
    """Generates wireproxy config and starts the daemon."""
    ensure_wireproxy()
    
    conf_path = os.path.join(CONF_DIR, f"{tag}.conf")
    
    # Wireproxy config structure
    config_content = f"""[Interface]
Address = {wg_address}
PrivateKey = {private_key}
MTU = 1420

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
    # We use nohup or just Popen to let it run in the background
    logger.info(f"Starting wireproxy for {tag} on SOCKS5 127.0.0.1:{local_port}")
    subprocess.Popen(
        [WIREPROXY_BIN, "-c", conf_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )

def stop_surfshark_proxies(tags: list[str]):
    """Finds and kills specific wireproxy instances and deletes their configs."""
    logger.info(f"Stopping wireproxies for tags: {tags}")
    for tag in tags:
        try:
            # Kill process matching the config file
            subprocess.run(["pkill", "-f", f"wireproxy -c .*{tag}.conf"], check=False)
        except Exception as e:
            logger.error(f"Failed to kill processes for {tag}: {e}")
            
        # Delete configuration files
        conf_path = os.path.join(CONF_DIR, f"{tag}.conf")
        if os.path.exists(conf_path):
            try:
                os.remove(conf_path)
            except OSError:
                pass

def recover_all_proxies():
    """Kills any dangling wireproxy processes and restarts all configs."""
    logger.info("Recovering all Wireproxy instances...")
    try:
        subprocess.run(["pkill", "-f", "wireproxy -c"], check=False)
    except Exception:
        pass
        
    configs = glob.glob(os.path.join(CONF_DIR, "*.conf"))
    for conf_path in configs:
        logger.info(f"Restarting wireproxy for {conf_path}")
        subprocess.Popen(
            [WIREPROXY_BIN, "-c", conf_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
